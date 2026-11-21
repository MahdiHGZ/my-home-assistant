"""Bounded serial device work with observable progress and deadline-safe shutdown."""

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

from action_errors import ActionQueueFullError

log = logging.getLogger(__name__)


@dataclass
class Work:
    created: float
    run: Callable
    cancel: Callable


class Worker:
    def __init__(self, name, capacity):
        self.queue = queue.Queue(maxsize=capacity)
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.active_since = None
        self.last_progress = time.monotonic()
        self.thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self.thread.start()

    def submit(self, run, cancel=lambda: None):
        with self.lock:
            if self.stopping.is_set():
                raise ActionQueueFullError("Controller is stopping.")
            try:
                self.queue.put_nowait(Work(time.monotonic(), run, cancel))
            except queue.Full as exc:
                raise ActionQueueFullError("Too many pending actions.") from exc

    def _loop(self):
        while not self.stopping.is_set():
            try:
                work = self.queue.get(timeout=0.25)
            except queue.Empty:
                continue
            with self.lock:
                cancel = self.stopping.is_set()
                self.active_since = time.monotonic()
            try:
                work.cancel() if cancel else work.run()
            except Exception:
                log.exception("Device worker failed")
            finally:
                with self.lock:
                    self.active_since = None
                    self.last_progress = time.monotonic()
                self.queue.task_done()

    def diagnostics(self):
        with self.lock, self.queue.mutex:
            now = time.monotonic()
            age = now - self.active_since if self.active_since is not None else 0
            return {
                "healthy": self.thread.is_alive() and not self.stopping.is_set() and age < 120,
                "queue_depth": len(self.queue.queue),
                "capacity": self.queue.maxsize,
                "oldest_queued_s": now - self.queue.queue[0].created if self.queue.queue else 0,
                "active_s": age,
                "last_progress_s": now - self.last_progress,
            }

    def stop(self):
        with self.lock:
            self.stopping.set()
            while True:
                try:
                    work = self.queue.get_nowait()
                except queue.Empty:
                    break
                work.cancel()
                self.queue.task_done()
