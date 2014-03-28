# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

import unittest

from six import BytesIO, moves

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

class RateLimiterTestBase(unittest.TestCase):
    def setUp(self):
        self.t = 0

    def time(self):
        return self.t

    def sleep(self, wait):
        self.t += max(0, wait)

    def make_one(self, *args, **kwargs):
        limiter = self.limiter_class(*args, **kwargs)
        limiter.time = self.time
        limiter.sleep = self.sleep
        return limiter

class TestBucketRateLimiter(RateLimiterTestBase):
    @property
    def limiter_class(self):
        from puppyserv.util import BucketRateLimiter
        return BucketRateLimiter

    def test_default_bucket_size(self):
        limiter = self.make_one(10)
        self.assertEqual(limiter.max_rate, 10)
        self.assertEqual(limiter.bucket_size, 10)

    def test_bucket_size_one(self):
        limiter = self.make_one(2.0, 1)
        next(limiter)
        self.assertEqual(self.t, 0)
        next(limiter)
        self.assertEqual(self.t, 0.5)
        self.sleep(0.25)
        next(limiter)
        self.assertEqual(self.t, 1.0)
        self.sleep(0.75)
        next(limiter)
        self.assertEqual(self.t, 1.75)

    def test_bucket_size_two(self):
        limiter = self.make_one(0.25, 2)
        next(limiter)
        self.assertEqual(self.t, 0)
        next(limiter)
        self.assertEqual(self.t, 0)
        next(limiter)
        self.assertEqual(self.t, 4.0)
        self.sleep(3.5)
        next(limiter)
        self.assertEqual(self.t, 8.0)
        self.sleep(8.0)
        next(limiter)
        next(limiter)
        self.assertEqual(self.t, 16.0)

    def test_reset(self):
        limiter = self.make_one(2.0, 1)
        next(limiter)
        self.assertEqual(self.t, 0)
        limiter.reset()
        next(limiter)
        self.assertEqual(self.t, 0)

    def test_set_max_rate(self):
        limiter = self.make_one(2.0, 1)
        next(limiter)
        self.assertEqual(limiter.tokens, 0)
        self.sleep(0.25)                # get 0.5 tokens
        limiter.max_rate = 1.0
        self.sleep(0.25)                # get 0.25 tokens
        self.assertEqual(limiter.tokens, 0.75)
        next(limiter)
        self.assertEqual(self.t, 0.75)

    def test_iter(self):
        limiter = self.make_one(1.0, 1)
        for n, _ in moves.zip(range(10), limiter):
            self.assertEqual(self.t, n)

class TestBackofRateLimiter(RateLimiterTestBase):
    @property
    def limiter_class(self):
        from puppyserv.util import BackoffRateLimiter
        return BackoffRateLimiter

    def test_delays(self):
        limiter = self.make_one(initial_delay=1.5, backoff=2, max_delay=10)
        next(limiter)
        self.assertEqual(self.t, 0)
        next(limiter)
        self.assertEqual(self.t, 1.5)
        next(limiter)
        self.assertEqual(self.t, 4.5)   # delay = 3.0
        next(limiter)
        self.assertEqual(self.t, 10.5)  # delay = 6.0
        next(limiter)
        self.assertEqual(self.t, 20.5)  # delay = 10.0
        next(limiter)
        self.assertEqual(self.t, 30.5)  # delay = 10.0

    def test_credit_for_time_served(self):
        limiter = self.make_one(initial_delay=1, backoff=2, max_delay=10)
        next(limiter)
        self.assertEqual(self.t, 0)
        self.sleep(0.75)
        next(limiter)
        self.assertEqual(self.t, 1.0)
        self.sleep(3.0)
        next(limiter)
        self.assertEqual(self.t, 4.0)
        next(limiter)
        self.assertEqual(self.t, 8.0)

    def test_reset(self):
        limiter = self.make_one(initial_delay=1, backoff=2, max_delay=10)
        next(limiter)
        self.assertEqual(self.t, 0)
        next(limiter)
        self.assertEqual(self.t, 1)
        limiter.reset()
        next(limiter)
        self.assertEqual(self.t, 1)
        next(limiter)
        self.assertEqual(self.t, 2)


class TestReadlineAdapter(unittest.TestCase):
    def make_one(self, fp):
        from puppyserv.util import ReadlineAdapter
        return ReadlineAdapter(fp)

    def test_close(self):
        raw_fp = BytesIO()
        fp = self.make_one(raw_fp)
        fp.close()
        self.assertTrue(raw_fp.closed)

    def test_mixed_readlines_and_reads(self):
        fp = self.make_one(BytesIO(b'a\nbb\ncdefghi'))
        self.assertEqual(fp.readline(), b'a\n')
        self.assertEqual(fp.readline(), b'bb\n')
        self.assertEqual(fp.read(1), b'c')
        self.assertEqual(fp.read(2), b'de')
        self.assertEqual(fp.readline(), b'fghi')
        self.assertEqual(fp.read(), b'')

    def test_readline_long_lines(self):
        line = b'x' * 3570
        fp = self.make_one(BytesIO(b'\n'.join([line] * 3)))
        self.assertEqual(fp.readline(), line + b'\n')
        self.assertEqual(fp.readline(), line + b'\n')
        self.assertEqual(fp.readline(), line)
        self.assertEqual(fp.readline(), b'')

    def test_readline_with_limit(self):
        fp = self.make_one(BytesIO(b'abc\ndef\nghi\n'))
        self.assertEqual(fp.readline(2), b'ab')
        self.assertEqual(fp.readline(2), b'c\n')
        self.assertEqual(fp.readline(5), b'def\n')
        self.assertEqual(fp.readline(4), b'ghi\n')
        self.assertEqual(fp.readline(4), b'')

    def test_read(self):
        data = b'abc\ndef\nghi\n'
        fp = self.make_one(BytesIO(data))
        self.assertEqual(fp.read(), data)

    def test_read_after_readline(self):
        data = b'abc\ndef\nghi\n'
        fp = self.make_one(BytesIO(data))
        self.assertEqual(fp.readline(8), b'abc\n')
        self.assertEqual(fp.read(), data[4:])
        self.assertEqual(fp.read(), b'')

    def test_short_read(self):
        data = b'abc\ndef\nghi\n'
        fp = self.make_one(BytesIO(data))
        self.assertEqual(fp.read(6), data[:6])
        self.assertEqual(fp.read(6), data[6:])
        self.assertEqual(fp.read(6), b'')

    def test_short_read_after_readline(self):
        data = b'abc\ndef\nghi\n'
        fp = self.make_one(BytesIO(data))
        self.assertEqual(fp.readline(5), b'abc\n')
        self.assertEqual(fp.read(3), data[4:7])
        self.assertEqual(fp.read(3), data[7:10])
        self.assertEqual(fp.read(3), data[10:])
        self.assertEqual(fp.read(3), b'')

    def test_long_read(self):
        data = b'abc\ndef\nghi\n'
        fp = self.make_one(BytesIO(data))
        self.assertEqual(fp.read(123), data)
        self.assertEqual(fp.read(123), b'')

    def test_long_read_after_readline(self):
        data = b'abc\ndef\nghi\n'
        fp = self.make_one(BytesIO(data))
        self.assertEqual(fp.readline(6), b'abc\n')
        self.assertEqual(fp.read(123), data[4:])
        self.assertEqual(fp.read(123), b'')

class MockTime(object):
    def __init__(self):
        self.t = 0

    def time(self):
        return self.t

    def sleep(self, delay):
        self.t += max(0, delay)
