"""The only module which reads/writes .blend content. Uses supplied Blendlib 1.4.4."""
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from blendlib import BlendFile
from .paths import fingerprint, relative, resolve


def read_libraries(path):
    # Avoid mmap: the supplied library can retain exported buffers on close.
    with BlendFile.open(path, strict_279=True, lazy=True) as bf:
        return [(lib.filepath, resolve(lib.filepath, path)) for lib in bf.libraries]


def repair(path, replacements):
    """replacements: raw stored path -> absolute destination. One atomic save per owner.

    Returns backup path. Refuses changed input, missing mappings or invalid output.
    The application must serialize its own writes; external Blender writes must be stopped.
    """
    source = Path(path)
    if source.is_symlink():
        raise ValueError('Запись через символическую ссылку не поддерживается')
    before = fingerprint(source)
    expected = {}
    for raw, target in replacements.items():
        if not Path(target).is_file():
            raise FileNotFoundError(f'Библиотека не найдена: {target}')
        expected[raw] = relative(target, path)
    fd, temporary = tempfile.mkstemp(prefix='.observer-', suffix='.tmp', dir=source.parent)
    os.close(fd)
    backup = str(source) + '.observer-' + uuid.uuid4().hex + '.bak'
    try:
        with BlendFile.open(source, strict_279=True) as bf:
            libs = bf.libraries
            found = {lib.filepath for lib in libs}
            absent = set(expected) - found
            if absent:
                raise ValueError(f'Ссылки изменились с момента индексации: {sorted(absent)}')
            for lib in libs:
                raw = lib.filepath
                if raw in expected:
                    lib.filepath = expected[raw]
                    lib.abs_filepath = replacements[raw]
            bf.save(temporary)
        actual = read_libraries(temporary)
        # Temporary file is in the same folder: relative paths have the same meaning.
        for raw, target in replacements.items():
            if (expected[raw], target) not in actual:
                raise ValueError('Проверка записанного пути не пройдена')
        with open(temporary, 'rb') as fh:
            os.fsync(fh.fileno())
        if fingerprint(source) != before:
            raise RuntimeError('Файл изменился во время обработки; повторите индексацию')
        shutil.copy2(source, backup)
        if fingerprint(source) != before:
            raise RuntimeError('Файл изменился во время резервного копирования')
        shutil.copymode(source, temporary)
        os.replace(temporary, source)
        return backup
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
