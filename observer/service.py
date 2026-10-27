import os
from collections import defaultdict
from . import blend
from .paths import canonical, fingerprint, within


class Service:
    def __init__(self, store):
        self.store = store

    def probe_targets(self, cancelled=lambda: False):
        # Filesystem probes run in worker, once per unique library (important on SMB).
        results = []
        for row in self.store.rows('SELECT DISTINCT target FROM links'):
            if cancelled():
                break
            results.append((row['target'], int(os.path.isfile(row['target']))))
        with self.store.connect() as db:
            db.executemany('INSERT OR REPLACE INTO target_status VALUES(?,?)', results)

    def scan(self, root, progress=lambda text: None, cancelled=lambda: False, force=False):
        root = canonical(root)
        if not os.path.isdir(root):
            raise FileNotFoundError(root)
        seen, directories, complete = set(), [root], True
        processed = skipped = errors = 0
        cached = {r['path']: r for r in self.store.rows('SELECT * FROM files')}
        while directories:
            if cancelled():
                complete = False
                break
            directory = directories.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if cancelled():
                            complete = False
                            break
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            directories.append(entry.path)
                        elif entry.name.lower().endswith('.blend') and entry.is_file():
                            path = canonical(entry.path)
                            seen.add(path)
                            try:
                                stamp = fingerprint(path)
                                old = cached.get(path)
                                if not force and old and not old['error'] and stamp == (old['mtime_ns'], old['size']):
                                    skipped += 1
                                    continue
                                libs = blend.read_libraries(path)
                                if fingerprint(path) != stamp:
                                    raise RuntimeError('Файл меняется во время чтения; будет повторён')
                                with self.store.connect() as db:
                                    db.execute('INSERT INTO files VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns,size=excluded.size,error=excluded.error', (path, *stamp, ''))
                                    db.execute('DELETE FROM links WHERE owner=?', (path,))
                                    db.executemany('INSERT OR IGNORE INTO links VALUES(?,?,?)', ((path, raw, target) for raw, target in libs))
                                processed += 1
                            except Exception as exc:
                                errors += 1
                                with self.store.connect() as db:
                                    db.execute('INSERT INTO files(path,error) VALUES(?,?) ON CONFLICT(path) DO UPDATE SET error=excluded.error', (path, str(exc)))
                                self.store.log('ERROR', f'{path}: {exc}')
                            progress(f'Прочитано: {processed}; без изменений: {skipped}; ошибок: {errors}')
            except OSError as exc:
                complete = False
                self.store.log('ERROR', f'Не удалось прочитать каталог {directory}: {exc}')
        if complete:
            with self.store.connect() as db:
                for path in cached:
                    if within(path, root) and path not in seen:
                        db.execute('DELETE FROM files WHERE path=?', (path,))
                # Repair rows survive disappearance: no silently lost failed jobs.
        self.probe_targets(cancelled)
        message = f'Индексация {"завершена" if complete else "неполная/остановлена"}: прочитано {processed}, пропущено {skipped}, ошибок {errors}'
        self.store.log('INFO', message)
        return message

    def moved(self, old, new, directory=False):
        old, new = canonical(old), canonical(new)
        if old == new:
            return
        def remap(path):
            if path == old or (directory and within(path, old)):
                return canonical(new + path[len(old):])
            return path
        with self.store.connect() as db:
            # A save may be an atomic replacement (.blend1/temp -> existing .blend).
            # Only indexed origins participate; never infer a move from matching basenames.
            files = list(db.execute('SELECT path FROM files'))
            links = list(db.execute('SELECT * FROM links'))
            jobs = {(r['owner'], r['raw']): dict(r) for r in db.execute('SELECT * FROM repairs')}
            mapped = [(r['path'], remap(r['path'])) for r in files if remap(r['path']) != r['path']]
            for src, dest in mapped:
                if db.execute('SELECT 1 FROM files WHERE path=?', (dest,)).fetchone():
                    raise ValueError(f'Конфликт перемещения: в индексе уже существует {dest}')
            # Preserve raw links of a moved owner: they now resolve from a different folder.
            for link in links:
                owner, raw, target = link['owner'], link['raw'], link['target']
                job = jobs.get((owner, raw))
                wanted = remap(job['new_target'] if job else target)
                moved_owner = remap(owner)
                if wanted != target or moved_owner != owner or job:
                    db.execute('INSERT INTO repairs(owner,raw,old_target,new_target) VALUES(?,?,?,?) ON CONFLICT(owner,raw) DO UPDATE SET new_target=excluded.new_target,error=\'\'',
                               (owner, raw, job['old_target'] if job else target, wanted))
            # Jobs can outlive a failed scan or deleted owner.
            for job in jobs.values():
                db.execute('UPDATE repairs SET new_target=?,error=\'\' WHERE id=?', (remap(job['new_target']), job['id']))
            for src, dest in mapped:
                db.execute('UPDATE files SET path=?,mtime_ns=NULL WHERE path=?', (dest, src))
            for row in list(db.execute('SELECT id,owner FROM repairs')):
                db.execute('UPDATE repairs SET owner=? WHERE id=?', (remap(row['owner']), row['id']))
        self.store.log('INFO', f'Перемещение: {old} → {new}')

    def queue(self, owner, raw, target):
        target = canonical(target)
        if not os.path.isfile(target) or not target.lower().endswith('.blend'):
            raise ValueError('Выберите существующий файл .blend')
        with self.store.connect() as db:
            row = db.execute('SELECT l.target,f.mtime_ns,f.error FROM links l JOIN files f ON f.path=l.owner WHERE l.owner=? AND l.raw=?', (owner, raw)).fetchone()
            if not row or row['mtime_ns'] is None or row['error']:
                raise ValueError('Сначала успешно проиндексируйте файл')
            db.execute('INSERT INTO repairs(owner,raw,old_target,new_target) VALUES(?,?,?,?) ON CONFLICT(owner,raw) DO UPDATE SET new_target=excluded.new_target,error=\'\'', (owner, raw, row['target'], target))

    def apply(self, ids, progress=lambda text: None, cancelled=lambda: False):
        selected = set(ids)
        groups = defaultdict(list)
        for row in self.store.rows('SELECT * FROM repairs ORDER BY id'):
            if row['id'] in selected:
                groups[row['owner']].append(row)
        success = failed = 0
        for owner, jobs in groups.items():
            if cancelled():
                break
            try:
                progress(f'Исправление: {owner}')
                backup = blend.repair(owner, {j['raw']: j['new_target'] for j in jobs})
                # Log disk success before updating index; if index update fails, backup remains.
                self.store.log('INFO', f'Файл записан: {owner}; резервная копия: {backup}')
                libs, stamp = blend.read_libraries(owner), fingerprint(owner)
                with self.store.connect() as db:
                    db.execute('INSERT INTO files VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns,size=excluded.size,error=excluded.error', (owner, *stamp, ''))
                    db.execute('DELETE FROM links WHERE owner=?', (owner,))
                    db.executemany('INSERT OR IGNORE INTO links VALUES(?,?,?)', ((owner, raw, target) for raw, target in libs))
                    db.executemany('DELETE FROM repairs WHERE id=?', ((j['id'],) for j in jobs))
                success += len(jobs)
            except Exception as exc:
                failed += len(jobs)
                with self.store.connect() as db:
                    db.executemany('UPDATE repairs SET error=? WHERE id=?', ((str(exc), j['id']) for j in jobs))
                self.store.log('ERROR', f'{owner}: {exc}')
        self.probe_targets(cancelled)
        return f'Исправлено ссылок: {success}; ошибок: {failed}. Необработанные задания сохранены.'
