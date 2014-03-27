# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from collections import deque
from functools import wraps
import logging
import mimetypes
import time

from pkg_resources import resource_filename

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
    def __init__(self, image_filenames, loop=True, frame_rate=4.0):
        if not image_filenames:
            raise ValueError("No images given")
        self.frames = map(VideoFrame.from_file, image_filenames)
        self.loop = loop
        self.start = time.time()
        self.frame_rate = frame_rate
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

    def __repr__(self):
        rep = super(VideoStreamer, self).__repr__()
        return rep[:-1] + ': %s>' % repr(self.stream)

    def close(self):
        self.closed = True

    def run(self):
        log.debug("Capture thread starting: %r", self.stream)
        frame = None
        while not self.closed:
            try:
                frame = self.stream.get_frame(frame, timeout=1.0)
            except StreamTimeout:
                pass
            else:
                if frame is None:
                    self.closed = True
                else:
                    self._buffer_frame(frame)
        log.debug("Capture thread terminating: %r", self.stream)
        self.stream.close()

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

class FailsafeStream(object):
    """ A stream which falls back to a backup stream if the primary
    stream times out.

    """
    def __init__(self, primary_stream, backup_stream_factory):
        self.primary_stream = primary_stream
        self.backup_stream_factory = backup_stream_factory
        self.backup_stream = None
        self.closed = False

    def close(self):
        self.closed = True
        self.primary_stream.close()
        if self.backup_stream:
            self.backup_stream.close()
            self.backup_stream = None

    def __iter__(self):
        frame = self.get_frame()
        while frame:
            yield frame
            frame = self.get_frame(frame)

    def get_frame(self, current_frame=None, timeout=None):
        if self.closed:
            return None

        # FIXME: make this timeout configurable
        check_timeout = 10

        while timeout is None or timeout > check_timeout:
            try:
                return self._get_frame(current_frame, timeout=check_timeout)
            except StreamTimeout:
                if timeout is not None:
                    timeout -= check_timeout

        return self._get_frame(current_frame, timeout)

    def _get_frame(self, current_frame, timeout):
        assert not self.closed
        assert timeout is not None

        primary_frame = getattr(current_frame, 'primary_frame', current_frame)

        primary = self.primary_stream
        backup = self.backup_stream

        if backup:
            # We're current streaming from the backup stream
            try:
                # Check to see if primary stream is back up
                frame = primary.get_frame(primary_frame, timeout=0)
            except StreamTimeout:
                # It's not...
                frame = backup.get_frame(current_frame, timeout=timeout)
                frame.primary_frame = primary_frame
                return frame
            else:
                # Primary stream working again, close backup stream
                log.info("Switching to primary stream")
                backup.close()
                self.backup_stream = None
                return frame
        else:
            try:
                return primary.get_frame(primary_frame, timeout=timeout)
            except StreamTimeout:
                log.info("Switching to backup stream")
                self.backup_stream = self.backup_stream_factory()
                raise

class TimeoutStream(object):
    """ A stream wrapper which substitutes a frame with a 'timed out' message
    when the wrapped stream times out.

    """
    def __init__(self, stream, frame_timeout=10):
        self.stream = stream
        self.frame_timeout = frame_timeout

        # FIXME: make timeout image configurable
        timeout_image = resource_filename('puppyserv', 'timeout.jpg')
        self.timeout_frame = VideoFrame.from_file(timeout_image)

    def close(self):
        self.stream.close()

    def __iter__(self):
        current_frame = None
        n_timeouts = 0
        while True:
            timeout = self.frame_timeout if n_timeouts < 2 else None
            try:
                frame = self.stream.get_frame(current_frame, timeout=timeout)
            except StreamTimeout:
                n_timeouts += 1
                yield self.timeout_frame
            else:
                n_timeouts = 0
                current_frame = frame
                if frame is None:
                    break
                yield frame

    def get_frame(self, current_frame=None, timeout=None):
        # XXX: not sure how this should behave
        return self.stream.get_frame(current_frame, timeout)
