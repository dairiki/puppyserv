# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

import socket
import sys
import time
import unittest

from mock import patch

from six import StringIO
from six.moves.http_client import HTTPConnection
from six.moves.queue import Queue

from webob.dec import wsgify
from webob import Response

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

class Test_check_server(unittest.TestCase):
    def call_it(self, host, port, *args, **kwargs):
        from puppyserv.testing import check_server
        return check_server(host, port, *args, **kwargs)

    @patch('puppyserv.testing.HTTPConnection')
    def test_fails(self, HTTPConnection):
        # coverage
        conn = HTTPConnection()
        conn.request.side_effect = socket.error
        rv = self.call_it('127.0.0.1', 8080, timeout=0.01, retries=2)
        self.assertFalse(rv)

class TestStopableWSGIServer(unittest.TestCase):
    def make_one(self, app, **kwargs):
        from puppyserv.testing import StopableWSGIServer
        server = StopableWSGIServer.create(app, **kwargs)
        self.addCleanup(server.shutdown)
        return server

    def make_connection(self, server):
        host, port = server.server_address
        connection = HTTPConnection(host, port)
        self.addCleanup(connection.close)
        return connection

    def test_chunks_not_buffered(self):
        queue = Queue()

        @wsgify
        def app(req):
            def app_iter():
                yield b''
                while True:
                    yield queue.get()
            return Response(
                content_type='text/x-test',
                app_iter=app_iter())

        server = self.make_one(app)
        server.wait()
        conn = self.make_connection(server)

        conn.request("GET", '/')
        data = recv_data(conn.sock, 0.5)
        self.assertRegexpMatches(data, r'Content-Type: text/x-test')

        queue.put('1')
        data = recv_data(conn.sock, 0.5)
        self.assertEqual(data, '1')

    def test_no_traceback_if_client_closes_connection(self):
        with patch.object(sys, 'stderr', new_callable=StringIO) as stderr:
            server = self.make_one(dummy_app)
            server.wait()
            conn = self.make_connection(server)
            conn.request("GET", '/')
            conn.close()
            time.sleep(.1)
        self.assertEqual(stderr.getvalue(), '')

    def test_wait_failure(self):
        # this is mainly for coverage
        server = self.make_one(dummy_app, socket_timeout=1)
        self.assertFalse(server.wait(-1))

@wsgify
def dummy_app(req):
    return Response('Hello world!')

def recv_data(sock, timeout):
    t_end = time.time() + timeout
    buf = []
    while timeout > 0:
        sock.settimeout(timeout)
        try:
            buf.append(sock.recv(2048))
        except socket.timeout:
            pass
        timeout = t_end - time.time()
    return b''.join(buf)
