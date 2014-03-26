# -*- coding: utf-8 -*-
""" A re-implementation version of webtest.http.StopableWSGIServer.

That version uses waitress.  Waitress doesn't seem to handle streaming
output very well.  It chunks it with a 0.5 second timeout or something.
E.g. if the app is putting out small chunks, one hunk every 0.1 seconds,
they chunks won't get put on the wire individually.  Rather, they'll be
buffered for half-second chunks.

That doesn't work for us, since we're trying to simulate the
mutipart/x-mixed-replace MJPEG video.

"""
from __future__ import absolute_import

import httplib
import socket
import threading
import time
import os
from paste.httpserver import WSGIHandler, WSGIServer
from webob import Response
from webob.dec import wsgify

def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    ip, port = s.getsockname()
    s.close()
    ip = os.environ.get('WEBTEST_SERVER_BIND', '127.0.0.1')
    return ip, port

def check_server(host, port, path_info='/', timeout=3, retries=30):
    """Perform a request until the server reply"""
    if retries < 0:
        return 0
    conn = httplib.HTTPConnection(host, port, timeout=timeout)
    time.sleep(.3)
    for i in range(retries):
        try:
            conn.request('GET', path_info)
            res = conn.getresponse()
            return res.status
        except (socket.error, httplib.HTTPException):
            time.sleep(.3)
    return 0


class StopableWSGIServer(WSGIServer): # (sic)
    def __init__(self, app, host='127.0.0.1', port=8080,
                 handler=WSGIHandler,
                 ssl_context=None,
                 daemon_threads=False,
                 socket_timeout=None,
                 request_queue_size=5, **kwargs):
        server_address = host, port
        WSGIServer.__init__(self, self._wrapper(app), server_address,
                            handler, ssl_context,
                            request_queue_size=request_queue_size)

        if daemon_threads:
            self.daemon_threads = daemon_threads
        if socket_timeout:
            self.wsgi_socket_timeout = int(socket_timeout)

        self.application_url = 'http://%s:%s/' % self.server_address

    def run(self):
        self.serve_forever()

    def shutdown(self):
        WSGIServer.shutdown(self)
        if hasattr(self, 'runner'):
            self.runner.join()

    @staticmethod
    @wsgify.middleware
    def _wrapper(request, app):
        if '__application__' in request.path_info:
            return Response('server started')
        return request.get_response(app)

    @classmethod
    def create(cls, app, **kwargs):
        host, port = get_free_port()
        if 'port' not in kwargs:
            kwargs['port'] = port
        if 'host' not in kwargs:
            kwargs['host'] = host
        if 'daemon_threads' not in kwargs:
            kwargs['daemon_threads'] = True
        server = cls(app, **kwargs)
        server.runner = threading.Thread(target=server.run)
        server.runner.daemon = True
        server.runner.start()
        return server

    def wait(self, retries=30):
        """Wait until the server is started"""
        host, port = self.server_address
        running = check_server(host, port, '/__application__', retries=retries)
        if running:
            return True
        try:
            self.shutdown()
        finally:
            return False
