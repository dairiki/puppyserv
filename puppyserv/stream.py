# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from collections import deque
from functools import wraps
import logging
import mimetypes
import time

try:
    import gevent
    import gevent.event
except ImportError:
    gevent = None

if gevent:
    from gevent.monkey import get_original
    Thread, Lock = get_original('threading', ['Thread', 'Lock'])
    sleep = gevent.sleep
else:
    from threading import Thread, Lock
    from time import sleep

log = logging.getLogger(__name__)

class StreamTimeout(Exception):
    pass

class VideoFrame(object):
    def __init__(self, image_data, content_type):
        self.image_data = image_data
        self.content_type = content_type

    @classmethod
    def from_file(cls, filename):
        content_type, encoding = mimetypes.guess_type(filename)
        if not content_type:
            raise ValueError("Can not guess content type")
        with open(filename, 'rb') as fp:
            return cls(fp.read(), content_type)

class StaticVideoStream(object):
    """ A video stream from static images.  For testing.
    """
    def __init__(self, image_filenames, loop=True):
        if not image_filenames:
            raise ValueError("No images given")
        self.frames = map(VideoFrame.from_file, image_filenames)
        self.loop = loop
        self.start = time.time()
        self.frame_rate = 0.2
        self.closed = False

    def close(self):
        self.closed = True

    def __iter__(self):
        frame = self.get_frame()
        while frame:
            yield frame
            frame = self.get_frame(frame)

    def get_frame(self, current_frame=None, timeout=None):
        index = self.frame_rate * (time.time() - self.start)
        frame = self._get_frame(index)
        if current_frame is None or frame is not current_frame:
            return frame

        wait = (int(index) + 1 - index) / self.frame_rate
        if timeout and timeout < wait:
            sleep(timeout)
            raise StreamTimeout()
        sleep(wait)
        return self._get_frame(index + 1)

    def _get_frame(self, index):
        index = int(index)
        if self.closed:
            return None
        if self.loop:
            index = index % len(self.frames)
        elif index >= len(self.frames):
            return None                 # stream done
        return self.frames[index]

def synchronized(method):
    @wraps(method)
    def wrapper(self, *args):
        with self.mutex:
            return method(self, *args)
    return wrapper

class VideoStreamer(Thread):
    """ Stream video in a separate thread.

    This has good support of the timeout argument to get_frame.

    """
    def __init__(self, stream, buffer_size=4):
        super(VideoStreamer, self).__init__()

        self.stream = stream
        self.framebuf = deque(maxlen=buffer_size)
        self.mutex = Lock()

        self.new_frame_event = gevent.event.Event()

        # gevent magic:
        # Hook to to set the new_frame event in the main thread
        async = gevent.get_hub().loop.async()
        async.start(self._signal_new_frame)
        self.signal_new_frame = async.send

        self.daemon = True
        self.closed = False
        self.start()

    def close(self):
        self.closed = True
        self.stream.close()

    def run(self):
        log.info("Capture thread starting: %r", self.stream)
        frame = None
        while not self.closed:
            try:
                frame = self.stream.get_frame(frame, timeout=1.0)
            except StreamTimeout:
                pass
            else:
                self._buffer_frame(frame)
        log.info("Capture thread terminating: %r", self.stream)

    @synchronized
    def _buffer_frame(self, frame):
        self.framebuf.appendleft(frame)
        self.signal_new_frame()

    @synchronized
    def _signal_new_frame(self):
        new_frame_event = self.new_frame_event
        self.new_frame_event = gevent.event.Event()
        new_frame_event.set()

    def __iter__(self):
        frame = self.get_frame()
        while frame:
            yield frame
            frame = self.get_frame(frame)

    def get_frame(self, current_frame=None, timeout=None):
        if self.closed:
            return None
        frame = self._get_frame(current_frame)
        while isinstance(frame, gevent.event.Event):
            if not frame.wait(timeout):
                raise StreamTimeout()
            frame = self._get_frame(current_frame)
        return frame

    @synchronized
    def _get_frame(self, current_frame=None):
        framebuf = self.framebuf

        if len(framebuf) == 0:
            return self.new_frame_event

        if current_frame is None:
            # Start with the most recent frame
            return framebuf[0]

        frames = iter(framebuf)
        next_frame = next(frames)
        if next_frame == current_frame:
            return self.new_frame_event

        for n, frame in enumerate(frames):
            if frame is current_frame:
                if n:
                    log.debug("Behind %d frames", n)
                return next_frame
            next_frame = frame
        # Current frame is no longer in buffer.
        # Skip ahead to oldest buffered frame.
        log.debug("Skipping frames")
        return framebuf[-1]
