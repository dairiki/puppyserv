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

from puppyserv.interfaces import StreamTimeout, VideoBuffer, VideoFrame

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
    def __init__(self, image_filenames, loop=True, frame_rate=4.0):
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
            index = index % max(len(self.frames), 1)
        elif index >= len(self.frames):
            return None                 # stream done
        return self.frames[index]

def synchronized(method):
    @wraps(method)
    def wrapper(self, *args):
        with self.mutex:
            return method(self, *args)
    return wrapper

class ThreadedStreamBuffer(VideoBuffer):
    """ Stream video in a separate thread.

    This has good support of the timeout argument to get_frame.

    """
    def __init__(self, stream, buffer_size=4):
        super(ThreadedStreamBuffer, self).__init__()

        self.stream = stream
        self.framebuf = deque(maxlen=buffer_size)
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
        rep = super(ThreadedStreamBuffer, self).__repr__()
        return rep[:-1] + ': %s>' % repr(self.stream)

    def close(self):
        self.closed = True

    def is_alive(self):
        return self.runner.is_alive()

    def run(self):
        log.debug("Capture thread starting: %r", self.stream)
        while not self.closed:
            try:
                frame = self.stream.next_frame()
            except StreamTimeout:
                pass
            else:
                if frame:
                    self._buffer_frame(frame)
                else:
                    self.closed = True
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

    def get_frame(self, current_frame=None, timeout=None):
        frame = self._get_frame(current_frame)
        if isinstance(frame, gevent.event.Event):
            if self.closed:
                return None
            if not frame.wait(timeout):
                raise StreamTimeout()
            frame = self._get_frame(current_frame)
            assert not isinstance(frame, gevent.event.Event)
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

class FailsafeStreamBuffer(VideoBuffer):
    """ A stream bufferwhich falls back to a backup stream buffer if
    the primary stream buffer times out.

    """
    def __init__(self, primary_buffer, backup_buffer_factory):
        self.primary_buffer = primary_buffer
        self.backup_buffer_factory = backup_buffer_factory
        self.backup_buffer = None
        self.closed = False

    def close(self):
        self.closed = True
        self.primary_buffer.close()
        if self.backup_buffer:
            self.backup_buffer.close()
            self.backup_buffer = None

    # FIXME: delete
    # def __iter__(self):
    #     frame = self.get_frame()
    #     while frame:
    #         yield frame
    #         frame = self.get_frame(frame)

    def get_frame(self, current_frame=None, timeout=None):
        if self.closed:
            return None

        # FIXME: make this timeout configurable
        check_timeout = 10

        while timeout is None or timeout > check_timeout:
            try:
                return self._get_frame(current_frame, check_timeout)
            except StreamTimeout:
                if timeout is not None:
                    timeout -= check_timeout

        return self._get_frame(current_frame, timeout)

    def _get_frame(self, current_frame, timeout):
        assert not self.closed
        assert timeout is not None

        primary_frame = getattr(current_frame, 'primary_frame', current_frame)

        primary = self.primary_buffer
        backup = self.backup_buffer

        if backup:
            # We're current streaming from the backup buffer
            try:
                # Check to see if primary buffer is back up
                frame = primary.get_frame(primary_frame, 0)
            except StreamTimeout:
                # It's not...
                frame = backup.get_frame(current_frame, timeout)
                frame.primary_frame = primary_frame
                return frame
            else:
                # Primary buffer working again, close backup buffer
                log.info("Switching to primary stream")
                backup.close()
                self.backup_buffer = None
                return frame
        else:
            try:
                return primary.get_frame(primary_frame, timeout)
            except StreamTimeout:
                log.info("Switching to backup stream")
                self.backup_buffer = self.backup_buffer_factory()
                raise

class TimeoutStreamBuffer(VideoBuffer):
    """ A stream wrapper which substitutes a frame with a 'timed out' message
    when the wrapped stream buffer times out.

    """
    def __init__(self, stream_buffer):
        self.stream_buffer = stream_buffer

        # FIXME: make timeout image configurable
        timeout_image = resource_filename('puppyserv', 'timeout.jpg')
        self.timeout_frame = VideoFrame.from_file(timeout_image)

    def close(self):
        self.stream_buffer.close()

    @staticmethod
    def is_timeout(frame):
        return isinstance(frame, _TimeoutFrame)

    def get_frame(self, current_frame=None, timeout=None):
        if isinstance(current_frame, _TimeoutFrame):
            current_frame = current_frame.current_frame
        try:
            return self.stream_buffer.get_frame(current_frame, timeout)
        except StreamTimeout:
            return _TimeoutFrame(self.timeout_frame,
                                 current_frame=current_frame)

class _TimeoutFrame(VideoFrame):
    def __init__(self, frame, current_frame):
        super(_TimeoutFrame, self).__init__(frame.image_data,
                                            frame.content_type)
        self.current_frame = current_frame
