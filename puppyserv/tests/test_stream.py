# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from itertools import repeat
import tempfile
import time
import unittest

import gevent
from mock import call, patch, sentinel, Mock

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

class TestFailsafeStreamBuffer(unittest.TestCase):
    def make_one(self, primary_buffer, backup_buffer_factory):
        from puppyserv.stream import FailsafeStreamBuffer
        buffer = FailsafeStreamBuffer(primary_buffer, backup_buffer_factory)
        self.addCleanup(buffer.close)
        return buffer

    def test_returns_none_if_closed(self):
        primary_buffer = Mock(name='primary_buffer')
        backup_buffer_factory = Mock(name='backup_buffer_factory', spec=())
        failsafe = self.make_one(primary_buffer, backup_buffer_factory)
        failsafe.close()
        frame = failsafe.get_frame(None, 1)
        self.assertIs(frame, None)

    def test_long_timeout(self):
        failsafe = self.make_one(Mock(), Mock())

        with patch.object(failsafe, '_get_frame') as get_frame:
            frame = failsafe.get_frame(None, 200)
        self.assertIs(frame, get_frame.return_value)
        self.assertEqual(get_frame.mock_calls, [call(None, 10)])

        with patch.object(failsafe, '_get_frame') as get_frame:
            get_frame.side_effect = StreamTimeout
            with self.assertRaises(StreamTimeout):
                failsafe.get_frame(None, 15)
        self.assertEqual(get_frame.mock_calls, [
            call(None, 10),
            call(None, 5),
            ])

    def test_failover_and_recovery(self):
        primary_buffer = Mock(name='primary_buffer')
        backup_buffer_factory = Mock(name='backup_buffer_factory', spec=())
        backup_buffer = backup_buffer_factory.return_value
        failsafe = self.make_one(primary_buffer, backup_buffer_factory)

        frame1 = failsafe.get_frame(None, 1)
        self.assertIs(frame1, primary_buffer.get_frame.return_value)

        primary_buffer.get_frame.side_effect = StreamTimeout
        with self.assertRaises(StreamTimeout):
            failsafe.get_frame(frame1, 1)
        self.assertEqual(backup_buffer_factory.mock_calls, [call()])

        frame2 = failsafe.get_frame(frame1, 1)
        self.assertIs(frame2, backup_buffer.get_frame.return_value)

        primary_buffer.get_frame.side_effect = None
        frame3 = failsafe.get_frame(frame1, 1)
        self.assertIs(frame3, primary_buffer.get_frame.return_value)
        self.assertEqual(backup_buffer_factory.mock_calls, [
            call(),
            call().get_frame(frame1, 1),
            call().close(),
            ])

    def test_concurrent_primary_timeouts(self):
        # Check that if two clients both hit primary timeouts simultaneously
        # only one manages to create a backup buffer
        def primary_get_frame(current_frame, timeout):
            gevent.sleep(timeout)
            raise StreamTimeout()
        primary_buffer = Mock(get_frame=primary_get_frame)
        backup_buffer_factory = Mock(spec=())

        failsafe = self.make_one(primary_buffer, backup_buffer_factory)
        def client():
            with self.assertRaises(StreamTimeout):
                failsafe.get_frame(None, 0.1)
        client2 = gevent.spawn(client)
        client()
        client2.join()
        self.assertEqual(backup_buffer_factory.mock_calls, [call()])

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
        frame = buf.get_frame(sentinel.current_frame, sentinel.timeout)
        self.assertIsInstance(frame, _TimeoutFrame)
        self.assertEqual(frame.current_frame, sentinel.current_frame)

        self.assertEqual(stream_buffer.mock_calls, [
            call.get_frame(sentinel.current_frame, sentinel.timeout),
            ])

class DummyFrame(object):
    pass

class DummyVideoStream(object):
    def __init__(self, frames=(), max_rate=100):
        from puppyserv.util import BucketRateLimiter
        self.frames = iter(frames)
        self.closed = False
        self.rate_limiter = BucketRateLimiter(max_rate, 1)
        next(self.rate_limiter)

    def close(self):
        self.closed = True

    def next_frame(self):
        if self.closed:
            return None
        next(self.rate_limiter)
        return next(self.frames, None)
