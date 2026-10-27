"""Blender // paths are relative to the owning blend, not the working directory."""
import os


def canonical(path):
    return os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path))))


def resolve(raw, owner):
    if not raw or '\ufffd' in raw:
        raise ValueError('Пустой или некорректно декодированный путь библиотеки')
    if raw.startswith('//'):
        return canonical(os.path.join(os.path.dirname(owner), raw[2:].replace('\\', os.sep)))
    if not os.path.isabs(raw):
        raise ValueError(f'Неоднозначный путь библиотеки: {raw!r}')
    return canonical(raw)


def relative(target, owner):
    try:
        return '//' + os.path.relpath(target, os.path.dirname(owner)).replace('\\', '/')
    except ValueError:  # Different Windows drives.
        return canonical(target)


def within(path, directory):
    try:
        return os.path.commonpath([canonical(path), canonical(directory)]) == canonical(directory)
    except ValueError:
        return False


def fingerprint(path):
    s = os.stat(path)
    return s.st_mtime_ns, s.st_size
