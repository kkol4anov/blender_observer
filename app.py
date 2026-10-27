"""Run: python app.py [--db PATH]. No work is performed on import."""
import argparse
import os
import sys
from pathlib import Path


def main():
    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QApplication, QMessageBox
    from observer.gui import Window
    parser = argparse.ArgumentParser(description='Blender Observer')
    default_dir = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'BlenderObserver'
    parser.add_argument('--db', type=Path, default=default_dir / 'observer.sqlite3')
    args = parser.parse_args()
    args.db = args.db.resolve()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    app.setApplicationName('Blender Observer')
    lock = QLockFile(str(args.db) + '.lock')
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.warning(None, 'Blender Observer', 'Эта база уже открыта другим экземпляром программы или каталог недоступен.')
        return 1
    try:
        window = Window(args.db)
        window.show()
        return app.exec()
    finally:
        lock.unlock()


if __name__ == '__main__':
    raise SystemExit(main())
