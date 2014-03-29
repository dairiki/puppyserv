# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

import tempfile
import time
import unittest

from pkg_resources import resource_filename
from six.moves import queue
import gevent
import gevent.event
from mock import patch

from puppyserv.interfaces import VideoBuffer, VideoStream

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

    def test_unguessable_type(self):
        with self.assertRaises(ValueError):
            frame = self.call_it('foobar')

class TestStaticVideoStreamBuffer(unittest.TestCase):
    def setUp(self):
        self.t = 0

    def time(self):
        return self.t

    def sleep(self, wait):
        self.t += max(0, wait)

    def make_one(self, frames, **kwargs):
        from puppyserv.stream import StaticVideoStreamBuffer
        patcher = patch.multiple(StaticVideoStreamBuffer,
                                 time=self.time, sleep=self.sleep)
        patcher.start()
        self.addCleanup(patcher.stop)

        stream_buffer = StaticVideoStreamBuffer(frames, **kwargs)
        return stream_buffer

    def test_from_settings(self):
        from puppyserv.stream import StaticVideoStreamBuffer
        settings = {
            'static.images': resource_filename('puppyserv', 'timeout.jpg'),
            'static.loop': '1',
            'static.frame_rate': '42',
            }
        buf = StaticVideoStreamBuffer.from_settings(settings)
        self.assertEqual(len(buf.frames), 1)
        self.assertEqual(buf.frames[0].content_type, 'image/jpeg')
        self.assertIs(buf.loop, True)
        self.assertAlmostEqual(buf.frame_rate, 42.0)

    def test_close(self):
        buf = self.make_one(['frame1'], loop=True)
        stream = buf.stream()
        buf.close()
        with self.assertRaises(StopIteration):
            next(stream)

    def test_loop(self):
        buf = self.make_one(['frame1', 'frame2'], loop=True)
        stream = buf.stream()
        self.assertEqual(next(stream), 'frame1')
        self.assertEqual(self.t, 0)
        self.assertEqual(next(stream), 'frame2')
        self.assertEqual(self.t, 0.25)
        self.assertEqual(next(stream), 'frame1')
        self.assertEqual(self.t, 0.5)
        self.assertEqual(next(stream), 'frame2')
        self.assertEqual(self.t, 0.75)

    def test_noloop(self):
        buf = self.make_one(['frame1', 'frame2'], loop=False)
        stream = buf.stream()
        self.assertEqual(next(stream), 'frame1')
        self.assertEqual(self.t, 0)
        self.assertEqual(next(stream), 'frame2')
        self.assertEqual(self.t, 0.25)
        with self.assertRaises(StopIteration):
            next(stream)

class TestThreadedStreamBuffer(unittest.TestCase):
    def make_one(self, stream, **kwargs):
        from puppyserv.stream import ThreadedStreamBuffer
        kwargs.setdefault('timeout', 0.1)
        stream_buffer = ThreadedStreamBuffer(stream, **kwargs)
        self.addCleanup(stream_buffer.close)
        return stream_buffer

    def test_repr(self):
        source = DummyVideoStream()
        stream_buffer = self.make_one(source)
        self.assertRegexpMatches(
            repr(stream_buffer),
            r'<ThreadedStreamBuffer \[Thread-\d+\] <.*DummyVideoStream.*>>')

    def test_close(self):
        source = DummyVideoStream()
        stream_buffer = self.make_one(source)
        self.assertTrue(stream_buffer.is_alive())
        stream_buffer.close()
        for n in range(10):
            source.put('frame')
            if not stream_buffer.is_alive():
                break
            time.sleep(0.1)
        self.assertFalse(stream_buffer.is_alive())

    def test_source_closed(self):
        source = DummyVideoStream(timeout=0.01)
        stream_buffer = self.make_one(source, timeout=0.05, buffer_size=2)
        stream = stream_buffer.stream()

        source.close()
        with self.assertRaises(StopIteration):
            next(stream)
        self.assertFalse(stream_buffer.is_alive())

    def test_stream(self):
        source = DummyVideoStream(timeout=0.1)
        stream_buffer = self.make_one(source, timeout=0.05, buffer_size=2)
        stream = stream_buffer.stream()

        self.assertIs(next(stream), None) # timeout

        source.put('frame1')
        source.put('frame2')
        self.assertIs(next(stream), 'frame1')
        self.assertIs(next(stream), 'frame2')

    def test_skipped_frames(self):
        source = DummyVideoStream()
        stream_buffer = self.make_one(source, timeout=0.1, buffer_size=2)
        stream = stream_buffer.stream()

        # Need to start the iterator
        source.put('frame0')
        self.assertIs(next(stream), 'frame0')

        source.put('frame1')
        source.put('frame2')
        source.put('frame3')
        gevent.sleep(0.05)
        self.assertIs(next(stream), 'frame2')
        self.assertIs(next(stream), 'frame3')
        self.assertIs(next(stream), None) # timeout

    def test_wait_for_frame(self):
        source = DummyVideoStream(timeout=0.5)
        stream_buffer = self.make_one(source, timeout=0.5, buffer_size=1)
        stream = stream_buffer.stream()

        gevent.spawn_later(0.2, source.put, 'frame1')
        self.assertIs(next(stream), 'frame1')

class TestFailsafeStreamBuffer(unittest.TestCase):
    def make_one(self, primary_buffer, backup_buffer_factory):
        from puppyserv.stream import FailsafeStreamBuffer
        buffer = FailsafeStreamBuffer(primary_buffer, backup_buffer_factory)
        self.addCleanup(buffer.close)
        return buffer

    def test_stops_iteration_if_closed(self):
        primary_buffer = DummyBuffer()
        backup_buffer_factory = DummyBuffer
        failsafe = self.make_one(primary_buffer, backup_buffer_factory)
        failsafe.close()
        self.assertTrue(primary_buffer.closed)
        with self.assertRaises(StopIteration):
            next(failsafe.stream())

    def test_stops_iteration_if_closed_on_backup(self):
        primary_buffer = DummyBuffer()
        backup_buffer_factory = DummyBuffer
        failsafe = self.make_one(primary_buffer, backup_buffer_factory)
        stream = failsafe.stream()

        primary_buffer.put(None)
        self.assertEqual(next(stream), None)

        backup_buffer = failsafe.backup_buffer
        self.assertIsNot(backup_buffer, None)

        failsafe.close()
        self.assertTrue(primary_buffer.closed)
        self.assertTrue(backup_buffer.closed)
        self.assertIs(failsafe.backup_buffer, None)
        with self.assertRaises(StopIteration):
            next(failsafe.stream())

    def test_failover_and_recovery(self):
        primary_buffer = DummyBuffer()
        backup_buffer_factory = DummyBuffer()
        failsafe = self.make_one(primary_buffer, backup_buffer_factory)
        stream = failsafe.stream()

        primary_buffer.put('frame1')
        self.assertEqual(next(stream), 'frame1')

        primary_buffer.put(None)
        self.assertIs(failsafe.backup_buffer, None)
        self.assertEqual(next(stream), None)
        self.assertIsNot(failsafe.backup_buffer, None)

        backup_buffer_factory.put('backup1')
        self.assertEqual(next(stream), 'backup1')
        gevent.sleep()

        primary_buffer.put('frame2')
        primary_buffer.put('frame3')
        self.assertIsNot(failsafe.backup_buffer, None)
        primary_buffer.put('frame4')
        self.assertIs(failsafe.backup_buffer, None)

        primary_buffer.put('frame5')
        self.assertEqual(next(stream), 'frame5')

    def test_primary_stream_terminates_while_on_backup(self):
        primary_buffer = DummyBuffer()
        backup_buffer_factory = DummyBuffer
        failsafe = self.make_one(primary_buffer, backup_buffer_factory)
        stream = failsafe.stream()

        primary_buffer.put(None)
        self.assertEqual(next(stream), None)
        self.assertIsNot(failsafe.backup_buffer, None)

        primary_buffer.close()
        gevent.sleep()
        self.assertIs(failsafe.backup_buffer, None)

        with self.assertRaises(StopIteration):
            next(stream)


class DummyFrame(object):
    pass

class DummyVideoStream(VideoStream):
    def __init__(self, timeout=None):
        self.timeout = timeout
        self.frame_queue = queue.Queue()
        self.closed = False

    def close(self):
        self.closed = True

    def put(self, frame):
        self.frame_queue.put(frame)

    def next(self):
        if not self.closed:
            try:
                frame = self.frame_queue.get(timeout=self.timeout)
            except queue.Empty:
                frame = None                # timeout
        if self.closed:
            raise StopIteration()
        return frame

class DummyBuffer(VideoBuffer):
    def __init__(self):
        self.buf = []
        self.event = gevent.event.Event()
        self.closed = False

    # hokism - serve as own factory
    def __call__(self):
        return self

    def close(self):
        self.closed = True
        self.event.set()

    def put(self, frame):
        self.buf.append(frame)
        event = self.event
        self.event = gevent.event.Event()
        event.set()
        gevent.sleep(0)

    def stream(self):
        return self.Iterator(self)

    class Iterator(object):
        def __init__(self, parent):
            self.parent = parent
            self.i = max(0, len(parent.buf) - 1)

        def next(self):
            parent = self.parent
            if self.i == len(parent.buf) and not parent.closed:
                parent.event.wait()
            if parent.closed:
                raise StopIteration()
            frame = parent.buf[self.i]
            self.i += 1
            return frame
