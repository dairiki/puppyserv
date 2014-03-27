# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from itertools import count
import time
import unittest

from gevent import sleep
from mock import call, Mock

from puppyserv.interfaces import StreamTimeout, VideoFrame
from puppyserv.testing import StopableWSGIServer

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

class Test_BufferManager(unittest.TestCase):
    def make_one(self, buffer_factory, **kwargs):
        from puppyserv import BufferManager
        return BufferManager(buffer_factory, **kwargs)

    def test_n_clients(self):
        buffer_factory = Mock(name='buffer_factory', spec=())
        manager = self.make_one(buffer_factory, stop_stream_holdoff=0)

        self.assertEqual(manager.n_clients, 0)
        with manager:
            self.assertEqual(manager.n_clients, 1)
        self.assertEqual(manager.n_clients, 0)
        with manager:
            self.assertEqual(manager.n_clients, 1)
            with manager:
                self.assertEqual(manager.n_clients, 2)
            self.assertEqual(manager.n_clients, 1)
        self.assertEqual(manager.n_clients, 0)

    def test_concurrent_clients_get_same_buffer(self):
        buffer_factory = Mock(name='buffer_factory', spec=())
        manager = self.make_one(buffer_factory, stop_stream_holdoff=0)

        with manager as buf1:
            self.assertIs(buf1, buffer_factory.return_value)
            self.assertEqual(buffer_factory.mock_calls, [call()])
            with manager as buf2:
                self.assertIs(buf2, buf1)
                self.assertEqual(buffer_factory.mock_calls, [call()])
            self.assertEqual(buffer_factory.mock_calls, [call()])
        self.assertEqual(buffer_factory.mock_calls, [call(), call().close()])

    def test_nonconcurrent_clients_get_differnt_buffer(self):
        buffer_factory = Mock(name='buffer_factory', spec=())
        manager = self.make_one(buffer_factory, stop_stream_holdoff=0)

        with manager as buf1:
            self.assertIs(buf1, buffer_factory.return_value)
            self.assertEqual(buffer_factory.mock_calls, [call()])
        self.assertEqual(buffer_factory.mock_calls, [call(), call().close()])

        new_buffer = Mock(name='new streambuffer')
        buffer_factory.return_value = new_buffer
        with manager as buf2:
            self.assertIs(buf2, new_buffer)
            self.assertEqual(new_buffer.mock_calls, [])
        self.assertEqual(new_buffer.mock_calls, [call.close()])

    def test_stop_stream_holdoff(self):
        buffer_factory = Mock(name='buffer_factory', spec=())
        manager = self.make_one(buffer_factory, stop_stream_holdoff=0.1)
        with manager as buf1:
            pass
        self.assertEqual(buffer_factory.mock_calls, [call()])
        # before holdoff, this should reuse the first buffer
        with manager as buf2:
            pass
        self.assertEqual(buffer_factory.mock_calls, [call()])
        sleep(0.2)
        self.assertEqual(buffer_factory.mock_calls, [call(), call().close()])
