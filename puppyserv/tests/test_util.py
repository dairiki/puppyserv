# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

import unittest

from six import BytesIO

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

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
