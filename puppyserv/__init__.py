# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

from datetime import datetime
from functools import wraps
import logging
import logging.config
from pkg_resources import get_distribution, resource_filename

import gevent

from webob import Response
from webob.dec import wsgify
from webob.exc import HTTPGatewayTimeout, HTTPMethodNotAllowed, HTTPNotFound

from puppyserv import webcam
from puppyserv.interfaces import VideoFrame
from puppyserv.stats import dummy_stream_stat_manager, StreamStatManager
from puppyserv.stream import StaticVideoStreamBuffer
from puppyserv.util import BucketRateLimiter

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

    timeout_image = settings.get('timeout_image')

    frame_timeout = config.pop('frame_timeout')

    stream_stat_manager = StreamStatManager()
    config['stream_stat_manager'] = stream_stat_manager

    if 'static.images' in settings:
        def stream_buffer_factory():
            return StaticVideoStreamBuffer.from_settings(settings)
    else:
        def stream_buffer_factory():
            return webcam.stream_buffer_from_settings(
                settings,
                frame_timeout=frame_timeout,
                stream_stat_manager=stream_stat_manager,
                user_agent=SERVER_NAME)

    log.info("App starting!")
    return VideoStreamApp(stream_buffer_factory, **config)

class VideoStreamApp(object):

    boundary = b'puppyserv-92af5f768c28fad8'

    def __init__(self, stream_buffer_factory,
                 max_total_framerate=10,
                 timeout_image=None,
                 stream_stat_manager=dummy_stream_stat_manager,
                 **kwargs):
        if timeout_image is None:
            timeout_image = resource_filename('puppyserv', 'timeout.jpg')
        assert max_total_framerate > 0
        self.max_total_framerate = max_total_framerate
        self.buffer_manager = BufferManager(stream_buffer_factory, **kwargs)
        self.timeout_frame = VideoFrame.from_file(timeout_image)
        self.stream_stat_manager = stream_stat_manager
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
            try:
                frame = next(stream_buffer.stream())
            except StopIteration:
                # XXX: maybe different error?
                return HTTPGatewayTimeout('Not connected to webcam')
            if frame is None:
                return HTTPGatewayTimeout('webcam connection timed out')
        return Response(
            cache_control='no-cache',
            content_type=frame.content_type,
            body=frame.image_data)


    def _app_iter(self, request):
        frame = None

        def max_rate():
            return self.max_total_framerate / self.buffer_manager.n_clients

        stream_name = "> %s" % request.client_addr
        with self.buffer_manager as stream_buffer:
            limiter = BucketRateLimiter(max_rate=max_rate(), bucket_size=10)
            stream = limiter(stream_buffer.stream())
            with self.stream_stat_manager(stream, stream_name) as frames:
                for frame in frames:
                    if frame is None:
                        frame = self.timeout_frame
                    limiter.max_rate = max_rate()
                    yield self._part_for_frame(frame)

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
        if holdoff > 0:
            self._stopper = gevent.spawn_later(holdoff, stop_stream)
        else:
            stop_stream()
