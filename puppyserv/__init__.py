# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from datetime import datetime
from functools import wraps
import glob
import logging
import logging.config
from pkg_resources import get_distribution
import time

import gevent

from webob import Response
from webob.dec import wsgify
from webob.exc import HTTPGatewayTimeout, HTTPMethodNotAllowed, HTTPNotFound

from puppyserv import webcam
from puppyserv.stats import StreamStatManager
from puppyserv.stream import StaticVideoStreamBuffer, TimeoutStreamBuffer

log = logging.getLogger(__name__)

_dist = get_distribution(__name__)
SERVER_NAME = "%s/%s (<dairiki@dairiki.org>)" % (
    _dist.project_name, _dist.version)

EOL = b'\r\n'

def add_server_headers_filter(global_config, **settings):
    """ Middleware to add headers normally added by real http server.

    Used when uwsgi is serving HTTP request directly.

    """
    @wsgify.middleware
    def filter(request, app):
        response = request.get_response(app)
        response.server = SERVER_NAME
        response.date=datetime.utcnow()
        return response
    return filter

def _GET_only(view_method, allow=('GET', 'HEAD')):
    @wraps(view_method)
    def wrapper(self, request):
        if request.method not in allow:
            return HTTPMethodNotAllowed(allow=allow)
        response = view_method(self, request)
        if request.method == 'HEAD':
            response.body = b''
        return response
    return wrapper

def main(global_config, **settings):
    stream = None

    logging.config.fileConfig(global_config['__file__'], global_config)

    config = dict(
        (key, float(settings.get(key, dflt)))
        for key, dflt in [('max_total_framerate', 50.0),
                          ('frame_timeout', 5.0),
                          ('stop_stream_holdoff', 15.0)])

    if 'static.images' in settings:
        image_files = sorted(glob.glob(settings['static.images']))
        def stream_buffer_factory():
            test_stream_buffer = StaticVideoStreamBuffer(image_files)
            return TimeoutStreamBuffer(test_stream_buffer)
    else:
        def stream_buffer_factory():
            stream_buffer = webcam.stream_buffer_from_settings(
                settings,
                user_agent=SERVER_NAME,
                )
            return TimeoutStreamBuffer(stream_buffer)

    log.info("App starting!")
    return VideoStreamApp(stream_buffer_factory, **config)

class VideoStreamApp(object):

    boundary = b'puppyserv-92af5f768c28fad8'

    def __init__(self, stream_buffer_factory,
                 max_total_framerate=10,
                 frame_timeout=5,
                 **kwargs):
        assert max_total_framerate > 0
        assert frame_timeout > 0
        self.max_total_framerate = max_total_framerate
        self.frame_timeout = frame_timeout
        self.buffer_manager = BufferManager(stream_buffer_factory, **kwargs)
        self.stats = StreamStatManager()


    @wsgify
    def __call__(self, request):
        if request.path_info == '/':
            return self.stream(request)
        elif request.path_info == '/snapshot':
            return self.snapshot(request)
        return HTTPNotFound()

    @_GET_only
    def stream(self, request):
        return Response(
            content_type='multipart/x-mixed-replace',
            content_type_params={'boundary': self.boundary},
            cache_control='no-cache',
            app_iter = self._app_iter(request))

    @_GET_only
    def snapshot(self, request):
        with self.buffer_manager as stream_buffer:
            frame = stream_buffer.get_frame(timeout=self.frame_timeout)
            if not frame:
                # XXX: maybe different error?
                return HTTPGatewayTimeout('Not connected to webcam')
            if TimeoutStreamBuffer.is_timeout(frame):
                return HTTPGatewayTimeout('webcam connection timed out')
        return Response(
            cache_control='no-cache',
            content_type=frame.content_type,
            body=frame.image_data)


    def _app_iter(self, request):
        buffer_manager = self.buffer_manager
        frame_timeout = self.frame_timeout
        max_total_framerate = self.max_total_framerate
        with self.stats(request.client_addr) as stats:
            with buffer_manager as stream_buffer:
                t0 = time.time()
                frame = stream_buffer.get_frame(timeout=frame_timeout)
                while frame:
                    stats.got_frame()
                    yield self._part_for_frame(frame)

                    n_clients = buffer_manager.n_clients
                    wait_until = t0 + n_clients / max_total_framerate
                    now = time.time()
                    if wait_until > now:
                        gevent.sleep(wait_until - now)
                    t0 = max(wait_until, now)
                    frame = stream_buffer.get_frame(frame, frame_timeout)

            yield b'--' + self.boundary + b'--' + EOL

    def _part_for_frame(self, frame):
        data = frame.image_data
        return b''.join([
            b'--', self.boundary, EOL,
            b'Content-Type: ', frame.content_type, EOL,
            b'Content-length: ', str(len(data)), EOL,
            EOL,
            data, EOL,
            ])

class BufferManager(object):
    def __init__(self, buffer_factory, stop_stream_holdoff=15):
        self.stop_stream_holdoff = stop_stream_holdoff
        self._n_clients = 0
        self.buffer_factory = buffer_factory
        self._buffer = None
        self._stopper = None

    @property
    def n_clients(self):
        return self._n_clients

    # XXX: These would need a mutex if they were to be called from more
    # than one thread, but since we're geventing, we don't need it.
    def __enter__(self):
        if self._n_clients == 0:
            self._start_stream()
        self._n_clients += 1
        log.debug("BufferManager: nclients = %d", self._n_clients)
        return self._buffer

    def __exit__(self, exc_type, exc_value, exc_tb):
        assert self._n_clients > 0
        self._n_clients -= 1
        if self._n_clients == 0:
            self._stop_stream()
        log.debug("BufferManager: nclients = %d", self._n_clients)

    def _start_stream(self):
        assert self._n_clients == 0
        if not self._buffer:
            self._buffer = self.buffer_factory()
            log.info("Started stream capture %r", self._buffer)
        else:
            assert not self._stopper.ready()
            self._stopper.kill(block=False)
            self._stopper = None

    def _stop_stream(self):
        assert self._n_clients == 0
        assert self._buffer
        assert not self._stopper
        def stop_stream():
            log.info("Stopped stream capture %r", self._buffer)
            self._buffer.close()
            self._buffer = None
        holdoff = self.stop_stream_holdoff
        self._stopper = gevent.spawn_later(holdoff, stop_stream)
