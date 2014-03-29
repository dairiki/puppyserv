# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

from collections import deque
import glob
import logging
import mimetypes
import time

import gevent
import gevent.lock
import gevent.monkey

Thread, Lock = gevent.monkey.get_original('threading', ['Thread', 'Lock'])

from puppyserv.interfaces import VideoBuffer, VideoFrame
from puppyserv.stats import dummy_stream_stat_manager
from puppyserv.util import asbool

log = logging.getLogger(__name__)

def video_frame_from_file(filename):
    content_type, encoding = mimetypes.guess_type(filename)
    if not content_type:
        raise ValueError("Can not guess content type")
    with open(filename, 'rb') as fp:
        return VideoFrame(fp.read(), content_type)

class StaticVideoStreamBuffer(VideoBuffer):
    """ A video stream from static images.  For testing.
    """
    time = staticmethod(time.time)
    sleep = staticmethod(gevent.sleep)

    def __init__(self, frames, loop=True, frame_rate=4.0):
        self.frames = frames
        self.loop = loop
        self.start = self.time()
        self.frame_rate = frame_rate
        self.closed = False

    @classmethod
    def from_settings(cls, settings, prefix='static.'):
        images = settings[prefix + 'images']
        loop = asbool(settings.get(prefix + 'loop', True))
        frame_rate = float(settings.get(prefix + 'frame_rate', 4.0))

        image_filenames = sorted(glob.glob(images))
        frames = map(VideoFrame.from_file, image_filenames)
        return cls(frames, loop, frame_rate)

    def close(self):
        self.closed = True

    def stream(self):
        last_frame = None
        while not self.closed:
            pos = max(0, self.frame_rate * (self.time() - self.start))
            frame = int(pos)
            if frame == last_frame:
                wait = (frame + 1 - pos) / self.frame_rate
                self.sleep(wait)
                frame = last_frame + 1
            last_frame = frame
            if self.loop:
                frame = frame % max(len(self.frames), 1)
            if self.closed or frame >= len(self.frames):
                break
            yield self.frames[frame]

class ThreadedStreamBuffer(VideoBuffer):
    """ Stream video in a separate thread.

    """
    def __init__(self, source, timeout=None, buffer_size=10,
                 stream_stat_manager=dummy_stream_stat_manager,
                 stream_name=None):
        self.source = source
        self.timeout = timeout

        self.stream_stat_manager = stream_stat_manager
        self.stream_name = stream_name or repr(source)

        self.framebuf = deque(maxlen=buffer_size)
        self.length = 0
        self.condition = _GeventCondition()

        self.closed = False

        self.runner = Thread(target=self.run)
        self.runner.daemon = True
        self.runner.start()

    def __repr__(self):
        return (
            "<{self.__class__.__name__} [{self.runner.name}] {self.source!r}>"
            .format(**locals()))

    def close(self):
        self.closed = True

    def is_alive(self):
        return self.runner.is_alive()

    def run(self):
        condition = self.condition
        framebuf = self.framebuf
        log.debug("Capture thread starting: %r", self.source)
        with self.stream_stat_manager(self.source, self.stream_name) as frames:
            try:
                while not self.closed:
                    frame = next(frames)
                    with condition:
                        framebuf.append(frame)
                        self.length += 1
                        condition.notifyAll()
            except StopIteration:
                self.closed = True
        log.debug("Capture thread terminating: %r", self.source)
        self.source.close()

    def stream(self):
        condition = self.condition
        framebuf = self.framebuf
        pos = max(0, self.length - 1)
        while not self.closed:
            with condition:
                if pos == self.length:
                    self.condition.wait(self.timeout)
                    if self.closed:
                        break
                if pos < self.length:
                    bufstart = self.length - len(framebuf)
                    if bufstart > pos:
                        log.debug("Dropped %d frames", bufstart - pos)
                        pos = bufstart
                    frame = framebuf[pos - self.length]
                    pos += 1
                else:
                    assert pos == self.length
                    frame = None        # timed out
            yield frame

class _GeventCondition(object):
    """ A gevent-aware version of threading.Condition

    The ``wait`` method may be called only from a single (the gevent)
    thread.

    The ``notifyAll`` may safely be called from any thread.

    """
    def __init__(self, lock=None):
        if lock is None:
            # RLock?
            lock = Lock()               # a real threading.Lock
        self.lock = lock
        self.waiters = []

        self.acquire = lock.acquire
        self.release = lock.release

        # How we communicate from other threads to the gevent thread
        async = gevent.get_hub().loop.async()
        async.start(self._notify_all)
        self.async = async

    def __enter__(self):
        return self.lock.__enter__()

    def __exit__(self, exc_type, exc_value, tb):
        return self.lock.__exit__(exc_type, exc_value, tb)

    def wait(self, timeout=None):
        # FIXME: check that we own and have locked the lock?
        waiter = gevent.lock.Semaphore(0)
        self.waiters.append(waiter)
        self.release()
        try:
            if not waiter.wait(timeout):
                self.waiters.remove(waiter)
        finally:
            self.acquire()

    def notifyAll(self):
        # FIXME: check that we own and have locked the lock?
        self.async.send()

    def _notify_all(self):
        for waiter in self.waiters:
            waiter.release()
        self.waiters = []

class FailsafeStreamBuffer(VideoBuffer):
    """ A stream bufferwhich falls back to a backup stream buffer if
    the primary stream buffer times out.

    """
    def __init__(self, primary_buffer, backup_buffer_factory):
        self.primary_buffer = primary_buffer
        self.backup_buffer_factory = backup_buffer_factory
        self.backup_buffer = None
        self.closed = False
        self._monitor = gevent.spawn(lambda : None)
        self._monitor.join()

    def close(self):
        self.closed = True
        self.primary_buffer.close()
        backup_buffer = self.backup_buffer
        if backup_buffer is not None:
            backup_buffer.close()
            self.backup_buffer = None
        self._monitor.kill(block=True)

    def switch_to_backup(self):
        if self.backup_buffer is None:
            self.backup_buffer = self.backup_buffer_factory()
            log.info("Switching to backup stream")
            assert self._monitor.successful()
            self._monitor = gevent.spawn(self._monitor_primary)

    def _monitor_primary(self):
        # Wait for consecutive non-timeouts
        primary_stream = self.primary_buffer.stream()
        okay = deque([False], 3)
        try:
            while not all(okay):
                okay.append(next(primary_stream) is not None)
        except StopIteration:
            # primary stream terminated, switch back to primary (and quit)
            pass

        log.info("Switching to primary stream")
        backup_buffer = self.backup_buffer
        self.backup_buffer = None
        backup_buffer.close()

    def stream(self):
        while True:
            stream = self.primary_buffer.stream()
            while self.backup_buffer is None:
                frame = next(stream)
                if frame is None:
                    # primary stream timeout
                    self.switch_to_backup()
                yield frame

            stream = self.backup_buffer.stream()
            while self.backup_buffer is not None:
                yield next(stream)
