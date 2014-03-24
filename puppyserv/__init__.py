# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from contextlib import contextmanager
from datetime import datetime
import glob
import logging
import logging.config
from pkg_resources import get_distribution, resource_filename
import time

import gevent

from webob import Response
from webob.dec import wsgify
from webob.exc import HTTPGatewayTimeout, HTTPNotFound

from puppyserv.stream import StaticVideoStream, StreamTimeout, VideoFrame
from puppyserv.webcam import webcam_stream_from_settings

log = logging.getLogger(__name__)

_dist = get_distribution(__name__)
SERVER_NAME = "%s/%s (<dairiki@dairiki.org>)" % (
    _dist.project_name, _dist.version)

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

def main(global_config, **settings):
    stream = None

    logging.config.fileConfig(global_config['__file__'], global_config)

    max_total_framerate = float(settings.get('max_total_framerate', 100.0))
    frame_timeout = float(settings.get('frame_timeout', 10.0))

    if 'static.images' in settings:
        image_files = sorted(glob.glob(settings['static.images']))
        def stream_factory():
            #return VideoStreamer(StaticVideoStream(image_files))
            return StaticVideoStream(image_files)
    else:
        def stream_factory():
            return webcam_stream_from_settings(settings,
                                               user_agent=SERVER_NAME)

    return VideoStreamApp(stream_factory, max_total_framerate)

class VideoStreamApp(object):
    def __init__(self, stream,
                 frame_timeout=10,
                 max_total_framerate=10,
                 server_name=SERVER_NAME):
        self.stream = VideoBuffer(stream, timeout=frame_timeout)
        self.max_total_framerate = max_total_framerate
        self.server_name = server_name
        assert max_total_framerate > 0
        self.boundary = b'ipcamera'
        self.clients = set()
        gevent.spawn(self.logger)

    def logger(self):
        while True:
            log.info("Clients:\n  %s",
                     "\n  ".join(client.stats() for client in self.clients))
            gevent.sleep(10)

    @wsgify
    def __call__(self, request):
        if request.path_info == '/snapshot':
            return self.snapshot(request)
        elif request.path_info != '/':
            raise HTTPNotFound()

        return Response(
            content_type='multipart/x-mixed-replace',
            content_type_params={'boundary': self.boundary},
            cache_control='no-cache',
            app_iter = self.app_iter(request))

    @wsgify
    def snapshot(self, request):
        frame = self.stream.get_frame()
        if frame is None:
            raise HTTPGatewayTimeout('Not connected to webcam')
        return Response(
            cache_control='no-cache',
            content_type=frame.content_type,
            body=frame.image_data)

    @contextmanager
    def client_stats(self, request):
        client = StreamingClientStats(request)
        self.clients.add(client)
        try:
            yield client
        finally:
            self.clients.remove(client)

    def app_iter(self, request):
        EOL = b'\r\n'
        with self.client_stats(request) as client:
            for frame in self.stream:
                data = frame.image_data
                t0 = time.time()
                client.got_frame()
                yield b''.join([
                    b'--', self.boundary, EOL,
                    b'Content-Type: ', frame.content_type, EOL,
                    b'Content-length: ', str(len(data)), EOL,
                    EOL,
                    data, EOL,
                    ])
                throttle = len(self.clients) / self.max_total_framerate
                gevent.sleep(max(0, t0 + throttle - time.time()))

            yield b''.join([
                b'--', self.boundary, b'--', EOL,
                ])

class StreamingClientStats(object):
    def __init__(self, request):
        self.client_addr = request.client_addr
        self.n_frames = 0
        self.d_frames = 0
        self.t0 = self.t = time.time()

    def got_frame(self):
        self.n_frames += 1
        self.d_frames += 1

    def stats(self):
        t = time.time()
        t_total = t - self.t0
        cum_rate = self.n_frames / max(0.01, t_total)
        rate = self.d_frames / max(0.01, (t - self.t))

        self.d_frames = 0
        self.t = t
        return "%s: connect %.1fs %d frames [%.02f/s]; inst: %.02f/s" % (
            self.client_addr,
            t_total, self.n_frames, cum_rate, rate)

class VideoBuffer(object):
    def __init__(self, stream_factory, timeout=10):
        self.n_clients = 0
        self.stream_factory = stream_factory
        self._stream = None

        self.timeout = timeout
        timeout_image = resource_filename('puppyserv', 'timeout.jpg')
        self.timeout_frame = VideoFrame.from_file(timeout_image)

    # XXX: These would need a mutex if they were to be called from more
    # than one thread, but since we're geventing, we don't need it.
    def __enter__(self):
        if not self._stream:
            assert self.n_clients == 0
            self._stream = self.stream_factory()
        self.n_clients += 1
        log.debug("VideoBuffer: nclients = %d", self.n_clients)
        return self._stream

    def __exit__(self, exc_type, exc_value, exc_tb):
        assert self.n_clients > 0
        self.n_clients -= 1
        if self.n_clients == 0:
            self._stream.close()
            self._stream = None
        log.debug("VideoBuffer: nclients = %d", self.n_clients)

    def __iter__(self):
        timeout = self.timeout
        frame = None
        with self as stream:
            while True:
                try:
                    frame = stream.get_frame(frame, timeout=timeout)
                except StreamTimeout:
                    #timeout = None
                    yield self.timeout_frame
                else:
                    if frame is None:
                        break
                    timeout = self.timeout
                    yield frame

    def get_frame(self, current_frame=None, timeout=None):
        with self as stream:
            return stream.get_frame(current_frame, timeout)
