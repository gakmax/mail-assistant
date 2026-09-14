"""The worker, owned by the process instead of by a window.

A browser tab closes, reopens and reloads. The poller must not die with it and two
must never run at once, so the thread, the stop/wake events and the run log live
here and whoever is watching subscribes for messages.

No tkinter, no nicegui, no win32: `run` is injected, which is what lets the tests
drive the whole thing on any platform.
"""
import queue
import threading
from collections import deque

from .core import LogStore, local_text
from .report import report as send_report

LIVE_LINES = 400        # what a screen that just opened prints
KEEP_LINES = 2000       # what the database keeps


def line_text(at, text):
    """One log line as a screen shows it: Korean local time, then the message."""
    return f"{local_text(at, '%m-%d %H:%M:%S')}  {text}"


class Hub:
    """One worker, one log, and a queue per watcher.

    `publish` is the worker's `notify`. Subscribers get every message, so a second
    screen does not change what the first one sees.
    """

    def __init__(self, directory, config, run, report=None, keep=KEEP_LINES):
        self.directory = directory
        self.config = config
        self.run = run
        self.report = report or send_report
        self.keep = keep
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.waking = threading.Event()
        self.thread = None
        self.watchers = []
        self.lines = deque(maxlen=LIVE_LINES)
        self.last_message = ''
        self.revision = 0
        self.local = threading.local()

    # --------------------------------------------------------------- revision

    def touch(self):
        """Something a screen shows has changed. Screens compare this, not sqlite.

        A page cannot be told by another page: each is its own client with its own
        elements, and the only thing they share is this process. An int they can read
        on their own timer is what turns '언젠가 5초 안에' into 'now', for the price
        of one comparison per tick.
        """
        with self.lock:
            self.revision += 1
            return self.revision

    # ------------------------------------------------------------------ log

    def store(self):
        """One LogStore per thread: the worker writes, the screen reads, neither shares."""
        if getattr(self.local, 'store', None) is None:
            self.local.store = LogStore(self.directory / 'mail.db')
        return self.local.store

    def release(self):
        """Close this thread's connection. Windows keeps the file locked until we do."""
        store = getattr(self.local, 'store', None)
        if store is None:
            return
        try:
            store.close()
        except Exception as exc:
            self.report('실행 기록 닫기 실패', exc)
        finally:
            self.local.store = None

    def log(self, text):
        """Persist and fan out. Survives a restart, which the old Text widget did not."""
        text = str(text)
        try:
            self.store().add(text, keep=self.keep)
        except Exception as exc:
            # A log that cannot be written must not take the worker with it.
            self.report('실행 기록 저장 실패', exc)
        with self.lock:
            self.last_message = text
            self.lines.append(text)
        self.send(text)

    def publish(self, message):
        """The worker's notify. Strings are log lines; the rest is only fanned out."""
        if isinstance(message, str):
            self.log(message)
        elif isinstance(message, dict):
            self.log(message.get('body') or message.get('title', ''))
        else:
            self.send(message)

    def recent(self, limit=LIVE_LINES):
        """[(at, text)] oldest first. Falls back to memory if the table cannot be read."""
        try:
            return self.store().recent(limit)
        except Exception as exc:
            self.report('실행 기록 읽기 실패', exc)
            with self.lock:
                return [('', text) for text in self.lines]

    # ------------------------------------------------------------------ watchers

    def subscribe(self):
        channel = queue.Queue()
        with self.lock:
            self.watchers.append(channel)
        return channel

    def unsubscribe(self, channel):
        with self.lock:
            if channel in self.watchers:
                self.watchers.remove(channel)

    def send(self, value):
        with self.lock:
            watchers = list(self.watchers)
        for channel in watchers:
            channel.put(value)

    # ------------------------------------------------------------------ worker

    def running(self):
        with self.lock:
            return bool(self.thread and self.thread.is_alive())

    def start(self, config=None):
        """False when it was already running, so a second screen cannot start a second one."""
        with self.lock:
            if self.running():
                return False
            if config is not None:
                self.config = config
            self.stopping.clear()
            self.waking.clear()
            settings = dict(self.config)
            # Not a daemon: a half-written export or a mail mid-download has to finish.
            self.thread = threading.Thread(target=self.work, args=(settings,), daemon=False,
                                           name='mail-worker')
            self.thread.start()
        return True

    def work(self, settings):
        try:
            self.run(settings, self.directory, self.stopping, self.publish, self.waking)
        except Exception as exc:
            self.report('작업 스레드 중단', exc)
            self.log('실행 오류: 데이터 폴더 접근 권한과 설치 상태를 확인하세요.')
        finally:
            with self.lock:
                self.thread = None
            self.release()      # the worker's own connection, on the worker's own thread
            # The sentinel every screen watches for, after `running()` is already False.
            self.send(None)

    def halt(self):
        if not self.running():
            return False
        self.stopping.set()
        self.waking.set()      # so a sleeping cycle stops now instead of after the interval
        return True

    def wake(self):
        """'지금 확인'. Harmless when stopped: the next start clears it."""
        if not self.running():
            return False
        self.waking.set()
        return True

    def state(self):
        with self.lock:
            return {'running': self.running(), 'stopping': self.stopping.is_set(),
                    'message': self.last_message}

    def close(self):
        """Stop, and release this thread's connection. The worker closes its own."""
        self.halt()
        self.release()
