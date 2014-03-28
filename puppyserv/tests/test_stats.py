# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

import unittest

import gevent

from mock import call, patch

from puppyserv.interfaces import VideoFrame

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

class Test_dummy_stream_stat_manager(unittest.TestCase):
    def call_it(self, stream, stream_name=None):
        from puppyserv.stats import dummy_stream_stat_manager
        return dummy_stream_stat_manager(stream, stream_name)

    def test(self):
        stream = iter(range(4))
        with self.call_it(stream) as wrapped:
            self.assertEqual(list(wrapped), [0,1,2,3])

class TestStreamStatManager(unittest.TestCase):
    def make_one(self, **kwargs):
        from puppyserv.stats import StreamStatManager
        return StreamStatManager(**kwargs)

    def test_contextmanager(self):
        stream = [1,2,3]
        manager = self.make_one()
        with manager(stream, 'NAME') as monitored:
            self.assertEqual(monitored.stream_name, 'NAME')
            self.assertTrue(callable(monitored.stats))
            self.assertIn(monitored, manager.streams)
        self.assertNotIn(monitored, manager.streams)

    def test_defaults_to_repr_for_stream_name(self):
        stream = (1,)
        manager = self.make_one()
        with manager(stream) as monitored:
            self.assertEqual(monitored.stream_name, repr((1,)))

    def test_log_stats(self):
        # not a real test yet, but here for coverage
        manager = self.make_one()
        manager.log_stats()             # no streams
        with manager([], 'NAME') as monitored:
            manager.log_stats()        # with streams

    def test_logger(self):
        manager = self.make_one(log_interval=0.1)
        with patch.object(manager, 'log_stats') as log_stats:
            gevent.sleep(0.25)
        self.assertEqual(log_stats.mock_calls, [call(), call()])

    def test_logger_catches_exceptions(self):
        manager = self.make_one(log_interval=0.1)
        with patch.object(manager, 'log_stats', side_effect=Exception) \
                 as log_stats:
            gevent.sleep(0.25)
        self.assertEqual(log_stats.mock_calls, [call(), call()])

class TestStatMonitoredStream(unittest.TestCase):
    def setUp(self):
        self.t = 0

    def time(self):
        return self.t

    def sleep(self, wait):
        self.t += max(0, wait)

    def make_one(self, stream, stream_name):
        from puppyserv.stats import StatMonitoredStream
        patcher = patch.object(StatMonitoredStream, 'time', self.time)
        patcher.start()
        self.addCleanup(patcher.stop)
        return StatMonitoredStream(stream, stream_name)

    def test_iter(self):
        monitored = self.make_one([], 'NAME')
        self.assertIs(iter(monitored), monitored)

    def test_next(self):
        stream = [VideoFrame(b'data'), None, VideoFrame(b'more data')]
        monitored = self.make_one(stream, 'NAME')
        self.assertIs(next(monitored), stream[0])
        self.assertEqual(monitored.d_frames, 1)
        self.assertEqual(monitored.d_bytes, 4)

        self.assertIs(next(monitored), None)
        self.assertEqual(monitored.d_frames, 1)
        self.assertEqual(monitored.d_bytes, 4)

        self.assertIs(next(monitored), stream[2])
        self.assertEqual(monitored.d_frames, 2)
        self.assertEqual(monitored.d_bytes, 13)

    def test_stats(self):
        stream = [VideoFrame(b'data' * 4)]
        monitored = self.make_one(stream, 'NAME')
        self.sleep(1)
        monitored.reset()
        self.assertIs(next(monitored), stream[0])
        self.sleep(1)
        stats = monitored.stats(reset=False)

        expect = {
            't': 2,
            'time_connected': 2,
            'dt': 1,
            'stream_name': 'NAME',
            'frames_total': 1,
            'frames_avg_rate': 0.5,
            'frames_cur_rate': 1,
            'bytes_total_raw': 16,
            'bytes_avg_rate_raw': 8,
            'bytes_cur_rate_raw': 16,
            'bytes_total': '  16 B',
            'bytes_avg_rate': '   8 B',
            'bytes_cur_rate': '  16 B',
            }
        for key in list(stats):
            if key not in expect:
                del stats[key]
        self.assertEqual(stats, expect)

    def test_stats_format(self):
        stream = [VideoFrame(b'data' * 4)]
        monitored = self.make_one(stream, 'NAME')
        self.assertIs(next(monitored), stream[0])
        self.assertEqual(monitored.stats("{bytes_total}"), '  16 B')

    def test_reset(self):
        stream = [VideoFrame(b'data' * 4)]
        monitored = self.make_one(stream, 'NAME')
        self.assertIs(next(monitored), stream[0])
        monitored.reset(42)
        self.assertEqual(monitored.t, 42)
        self.assertEqual(monitored.n_frames, 1)
        self.assertEqual(monitored.n_bytes, 16)
        self.assertEqual(monitored.d_frames, 0)
        self.assertEqual(monitored.d_bytes, 0)

class Test_format_byte_size(unittest.TestCase):
    def call_it(self, nbytes):
        from puppyserv.stats import format_byte_size
        return format_byte_size(nbytes)

    def test_units(self):
        self.assertEqual(self.call_it(0), '   0 B')
        self.assertEqual(self.call_it(1018), '1018 B')

    def test_1k(self):
        self.assertEqual(self.call_it(1019), '1.00kB')
        self.assertEqual(self.call_it(10234), '9.99kB')
        self.assertEqual(self.call_it(10235), '10.0kB')
        self.assertEqual(self.call_it(102348), '99.9kB')
        self.assertEqual(self.call_it(102349), ' 100kB')
        self.assertEqual(self.call_it(1043333), '1019kB')

    def test_1M(self):
        self.assertEqual(self.call_it(1043334), '1.00MB')
        self.assertEqual(self.call_it(10480517), '9.99MB')
        self.assertEqual(self.call_it(10480518), '10.0MB')
        self.assertEqual(self.call_it(104805171), '99.9MB')
        self.assertEqual(self.call_it(104805172), ' 100MB')
        self.assertEqual(self.call_it(1068373114), '1019MB')

    def test_1G(self):
        self.assertEqual(self.call_it(1068373115), '1.00GB')

    def test_ones(self):
        self.assertEqual(self.call_it(1), '   1 B')
        self.assertEqual(self.call_it(1024), '1.00kB')
        self.assertEqual(self.call_it(1024 ** 2), '1.00MB')
        self.assertEqual(self.call_it(1024 ** 3), '1.00GB')
        self.assertEqual(self.call_it(1024 ** 4), '1.00TB')
        self.assertEqual(self.call_it(1024 ** 5), '1.00PB')
        self.assertEqual(self.call_it(1024 ** 6), '1024PB')
