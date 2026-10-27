"""SQLite owns the index, repair queue, preferences and operation journal."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .paths import canonical, resolve

SCHEMA = '''
CREATE TABLE IF NOT EXISTS files(
 path TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER, error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS links(
 owner TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE ON UPDATE CASCADE,
 raw TEXT NOT NULL, target TEXT NOT NULL, PRIMARY KEY(owner,raw));
CREATE INDEX IF NOT EXISTS links_target ON links(target);
CREATE TABLE IF NOT EXISTS target_status(target TEXT PRIMARY KEY, available INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS repairs(
 id INTEGER PRIMARY KEY, owner TEXT NOT NULL, raw TEXT NOT NULL,
 old_target TEXT NOT NULL, new_target TEXT NOT NULL,
 error TEXT NOT NULL DEFAULT '', UNIQUE(owner,raw));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journal(
 id INTEGER PRIMARY KEY, time TEXT DEFAULT (datetime('now','localtime')),
 level TEXT NOT NULL, message TEXT NOT NULL);
PRAGMA user_version=1;
'''


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError(f'Неподдерживаемая версия базы: {version}')
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self, sql, params=()):
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, params)]

    def setting(self, key, default=''):
        rows = self.rows('SELECT value FROM settings WHERE key=?', (key,))
        return rows[0]['value'] if rows else default

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, str(value)))

    def log(self, level, message):
        with self.connect() as db:
            db.execute('INSERT INTO journal(level,message) VALUES(?,?)', (level, message))

    def import_legacy(self, path):
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        all_files = data.get('all_files')
        if not isinstance(all_files, dict):
            raise ValueError('Отсутствует словарь all_files')
        # Legacy paths are platform specific. Import on the original Windows workstation.
        with self.connect() as db:
            if db.execute('SELECT 1 FROM files LIMIT 1').fetchone():
                raise ValueError('Импорт разрешён только в пустую базу')
            for owner, info in all_files.items():
                owner = canonical(owner)
                db.execute('INSERT OR IGNORE INTO files(path) VALUES(?)', (owner,))
                for target in info.get('libraries', []):
                    target = resolve(target, owner)
                    db.execute('INSERT OR IGNORE INTO links VALUES(?,?,?)', (owner, target, target))
            # Fingerprints intentionally NULL: first scan must read real raw library paths.
        self.log('INFO', f'Импортирован JSON: {path}. Требуется индексация перед исправлением.')
