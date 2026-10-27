"""Watchdog only queues events; never touches Qt widgets or SQLite."""
import queue
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver


class Handler(FileSystemEventHandler):
    def __init__(self, events):
        self.events = events

    def on_any_event(self, event):
        if event.event_type not in {'created', 'deleted', 'modified', 'moved'}:
            return
        if event.is_directory and event.event_type == 'modified':
            return
        src, dest = event.src_path, getattr(event, 'dest_path', '')
        if event.is_directory or src.lower().endswith('.blend') or dest.lower().endswith('.blend'):
            self.events.put((event.event_type, src, dest, event.is_directory))


class Monitor:
    def __init__(self):
        self.events = queue.Queue()
        self.observer = None

    def start(self, root, polling=False):
        self.stop()
        observer = (PollingObserver if polling else Observer)(timeout=2)
        observer.schedule(Handler(self.events), root, recursive=True)
        try:
            observer.start()
        except Exception:
            observer.stop()
            raise
        self.observer = observer

    def stop(self):
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None

    def drain(self):
        result = []
        while True:
            try:
                result.append(self.events.get_nowait())
            except queue.Empty:
                return result
