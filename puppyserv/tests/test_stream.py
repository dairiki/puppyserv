# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from itertools import repeat
import tempfile
import time
import unittest

from mock import call, sentinel, Mock

from puppyserv.interfaces import StreamTimeout

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

class Test_video_frame_from_file(unittest.TestCase):
    def call_it(self, filename):
        from puppyserv.stream import video_frame_from_file
        return video_frame_from_file(filename)

    def test(self):
        tmpfile = tempfile.NamedTemporaryFile(suffix='.jpg')
        tmpfile.write('IMAGE DATA')
        tmpfile.flush()
        frame = self.call_it(tmpfile.name)
        self.assertEqual(frame.content_type, 'image/jpeg')
        self.assertEqual(frame.image_data, 'IMAGE DATA')

class TestThreadedStreamBuffer(unittest.TestCase):
    def make_one(self, stream, *args, **kw):
        from puppyserv.stream import ThreadedStreamBuffer
        stream_buffer = ThreadedStreamBuffer(stream, *args, **kw)
        self.addCleanup(stream_buffer.close)
        return stream_buffer

    def test_close(self):
        stream = DummyVideoStream(repeat(DummyFrame()))
        stream_buffer = self.make_one(stream)
        self.assertTrue(stream_buffer.is_alive())
        stream_buffer.close()
        for n in range(10):
            if not stream_buffer.is_alive():
                break
            time.sleep(0.1)
        self.assertFalse(stream_buffer.is_alive())

    def test_get_frame(self):
        frame1 = DummyFrame()
        frame2 = DummyFrame()
        stream = DummyVideoStream([frame1, frame2], max_rate=10)
        stream_buffer = self.make_one(stream)

        with self.assertRaises(StreamTimeout):
            frame = stream_buffer.get_frame(None, timeout=0.05)
        frame = stream_buffer.get_frame(None, timeout=0.2)
        self.assertIs(frame, frame1)

        frame = stream_buffer.get_frame(None, timeout=0)
        self.assertIs(frame, frame1)

        frame = stream_buffer.get_frame(frame, timeout=0.2)
        self.assertIs(frame, frame2)

        with self.assertRaises(StreamTimeout):
            stream_buffer.get_frame(frame, timeout=0.1)

class TestTimeoutStreamBuffer(unittest.TestCase):
    def make_one(self, stream_buffer):
        from puppyserv.stream import TimeoutStreamBuffer
        buffer = TimeoutStreamBuffer(stream_buffer)
        self.addCleanup(buffer.close)
        return buffer

    def test_close(self):
        stream_buffer = Mock()
        buf = self.make_one(stream_buffer)
        buf.close()
        stream_buffer.close.assert_called_once_with()

    def test_get_frame(self):
        stream_buffer = Mock()
        buf = self.make_one(stream_buffer)
        frame = buf.get_frame(sentinel.current_frame, sentinel.timeout)
        self.assertIs(frame, stream_buffer.get_frame.return_value)
        self.assertEqual(stream_buffer.mock_calls, [
            call.get_frame(sentinel.current_frame, sentinel.timeout),
            ])

    def test_get_frame_timeouts(self):
        from puppyserv.stream import _TimeoutFrame
        stream_buffer = Mock()
        stream_buffer.get_frame.side_effect = StreamTimeout
        buf = self.make_one(stream_buffer)
        frame = buf.get_frame(sentinel.current_frame, 12)
        self.assertIsInstance(frame, _TimeoutFrame)
        self.assertEqual(frame.n_timeouts, 1)
        self.assertEqual(frame.current_frame, sentinel.current_frame)
        frame = buf.get_frame(frame, 12)
        self.assertIsInstance(frame, _TimeoutFrame)
        self.assertEqual(frame.n_timeouts, 2)
        self.assertEqual(frame.current_frame, sentinel.current_frame)

        frame = buf.get_frame(frame, 12)
        self.assertIsInstance(frame, _TimeoutFrame)
        self.assertEqual(frame.n_timeouts, 3)
        self.assertEqual(frame.current_frame, sentinel.current_frame)

        self.assertEqual(stream_buffer.mock_calls, [
            call.get_frame(sentinel.current_frame, 12),
            call.get_frame(sentinel.current_frame, 12),
            call.get_frame(sentinel.current_frame, 60),
            ])

class DummyFrame(object):
    pass

class DummyVideoStream(object):
    def __init__(self, frames=(), max_rate=100):
        from puppyserv.util import RateLimiter
        self.frames = iter(frames)
        self.closed = False
        self.rate_limiter = RateLimiter(max_rate)
        self.rate_limiter()

    def close(self):
        self.closed = True

    def next_frame(self):
        if self.closed:
            return None
        self.rate_limiter()
        return next(self.frames, None)
