# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

from collections import deque
from functools import wraps
import glob
import logging
import mimetypes
import time

import gevent
import gevent.event
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

def synchronized(method):
    @wraps(method)
    def wrapper(self, *args):
        with self.mutex:
            return method(self, *args)
    return wrapper

class ThreadedStreamBuffer(VideoBuffer):
    """ Stream video in a separate thread.

    """
    def __init__(self, source, timeout=None, buffer_size=10,
                 stream_stat_manager=dummy_stream_stat_manager,
                 stream_name=None):
        self.source = source
        self.timeout = timeout
        self.framebuf = deque(maxlen=buffer_size)
        self.stream_stat_manager = stream_stat_manager
        self.stream_name = stream_name or repr(source)
        self.length = 0
        self.mutex = Lock()
        self.new_frame_event = gevent.event.Event()

        # gevent magic:
        # Hook to to set the new_frame event in the main thread
        async = gevent.get_hub().loop.async()
        async.start(self._signal_new_frame)
        self.signal_new_frame = async.send

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
        log.debug("Capture thread starting: %r", self.source)
        with self.stream_stat_manager(self.source, self.stream_name) as frames:
            try:
                while not self.closed:
                    self._put_frame(next(frames))
            except StopIteration:
                self.closed = True
        log.debug("Capture thread terminating: %r", self.source)
        self.source.close()

    @synchronized
    def _put_frame(self, frame):
        self.framebuf.append(frame)
        self.length += 1
        self.signal_new_frame()

    def _signal_new_frame(self):
        new_frame_event = self.new_frame_event
        self.new_frame_event = gevent.event.Event()
        new_frame_event.set()

    def stream(self):
        pos = max(0, self.length - 1)
        while not self.closed:
            pos, frame, wait_for = self._get_frame(pos)
            if wait_for is not None:
                if wait_for.wait(self.timeout):
                    pos, frame, wait_for = self._get_frame(pos)
                    assert wait_for is None
                else:
                    frame = None        # timeout
                if self.closed:
                    break
            yield frame

    @synchronized
    def _get_frame(self, pos):
        framebuf = self.framebuf
        length = self.length
        start = length - len(framebuf)
        if pos < start:
            log.debug("Dropped %d frames", start - pos)
            pos = start

        if pos == length:
            return pos, None, self.new_frame_event
        else:
            assert pos < length
            return pos + 1, framebuf[pos - length], None

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
