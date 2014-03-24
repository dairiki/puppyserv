# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

import tempfile
import time
import unittest

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

class TestVideoFrame(unittest.TestCase):
    def test_from_file(self):
        from puppyserv.stream import VideoFrame
        tmpfile = tempfile.NamedTemporaryFile(suffix='.jpg')
        tmpfile.write('IMAGE DATA')
        tmpfile.flush()
        frame = VideoFrame.from_file(tmpfile.name)
        self.assertEqual(frame.content_type, 'image/jpeg')
        self.assertEqual(frame.image_data, 'IMAGE DATA')

class TestVideoStreamer(unittest.TestCase):
    def make_one(self, stream, *args, **kw):
        from puppyserv.stream import VideoStreamer
        streamer = VideoStreamer(stream, *args, **kw)
        self.addCleanup(stream.close)
        return streamer

    def test_close(self):
        stream = DummyStream()
        streamer = self.make_one(stream)
        self.assertTrue(streamer.is_alive())
        streamer.close()
        for n in range(10):
            if not streamer.is_alive():
                break
            time.sleep(0.1)
        self.assertFalse(streamer.is_alive())

    def test_get_frame(self):
        from puppyserv.stream import StreamTimeout
        frame1 = DummyFrame()
        frame2 = DummyFrame()
        stream = DummyStream([frame1, frame2])
        streamer = self.make_one(stream)

        with self.assertRaises(StreamTimeout):
            frame = streamer.get_frame(None, timeout=0.1)
        frame = streamer.get_frame(None, timeout=0.5)
        self.assertIs(frame, frame1)

        with self.assertRaises(StreamTimeout):
            streamer.get_frame(frame, timeout=0.1)
        frame = streamer.get_frame(frame, timeout=0.5)
        self.assertIs(frame, frame2)

        streamer.close()
        frame = streamer.get_frame(frame, timeout=0.1)
        self.assertIs(frame, None)

class DummyFrame(object):
    pass

class DummyStream(object):
    def __init__(self, frames=()):
        from puppyserv.util import RateLimiter
        self.frames = iter(frames)
        self.closed = False
        self.rate_limiter = RateLimiter(2)
        self.rate_limiter()

    def close(self):
        self.closed = True

    def get_frame(self, current_frame, timeout=None):
        from puppyserv.stream import StreamTimeout
        if self.closed:
            return None
        frame = next(self.frames, None)
        if frame:
            self.rate_limiter()
            return frame
        time.sleep(timeout or 60)
        raise StreamTimeout()
