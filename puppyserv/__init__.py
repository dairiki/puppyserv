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
        def stream_factory():
            #return VideoStreamer(StaticVideoStream(image_files))
            return StaticVideoStream(image_files)
    else:
        def stream_factory():
            return webcam_stream_from_settings(settings,
                                               user_agent=SERVER_NAME)

    log.info("App starting!")
    return VideoStreamApp(stream_factory, **config)

class VideoStreamApp(object):

    boundary = b'puppyserv-92af5f768c28fad8'

    def __init__(self, stream, max_total_framerate=10, **kwargs):
        assert max_total_framerate > 0
        self.video_buffer = VideoBuffer(stream, **kwargs)
        self.max_total_framerate = max_total_framerate
        self.clients = set()
        gevent.spawn(self._logger)

    def _logger(self):
        while True:
            gevent.sleep(10)
            if self.clients:
                log.info("Clients:%s", "".join(
                    "\n  " + client.stats() for client in self.clients))
            else:
                log.info("No clients")

    @wsgify
    def __call__(self, request):
        if request.path_info == '/':
            return self.stream(request)
        elif request.path_info == '/snapshot':
            return self.snapshot(request)
        raise HTTPNotFound()

    def stream(self, request):
        return Response(
            content_type='multipart/x-mixed-replace',
            content_type_params={'boundary': self.boundary},
            cache_control='no-cache',
            app_iter = self._app_iter(request))

    def snapshot(self, request):
        frame = self.video_buffer.get_frame()
        if frame is None:
            raise HTTPGatewayTimeout('Not connected to webcam')
        return Response(
            cache_control='no-cache',
            content_type=frame.content_type,
            body=frame.image_data)

    @contextmanager
    def _client_stats(self, request):
        client = StreamingClientStats(request)
        self.clients.add(client)
        try:
            yield client
        finally:
            self.clients.remove(client)

    def _app_iter(self, request):
        with self._client_stats(request) as client:
            for frame in self.video_buffer:
                t0 = time.time()
                client.got_frame()
                data = frame.image_data
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
    def __init__(self, stream_factory,
                 frame_timeout=10,
                 stop_stream_holdoff=15):
        self.n_clients = 0
        self.stream_factory = stream_factory
        self._stream = None
        self._stopper = None

        self.frame_timeout = frame_timeout
        self.stop_stream_holdoff = stop_stream_holdoff

        timeout_image = resource_filename('puppyserv', 'timeout.jpg')
        self.timeout_frame = VideoFrame.from_file(timeout_image)

    # XXX: These would need a mutex if they were to be called from more
    # than one thread, but since we're geventing, we don't need it.
    def __enter__(self):
        if self.n_clients == 0:
            self._start_stream()
        self.n_clients += 1
        log.debug("VideoBuffer: nclients = %d", self.n_clients)
        return self._stream

    def __exit__(self, exc_type, exc_value, exc_tb):
        assert self.n_clients > 0
        self.n_clients -= 1
        if self.n_clients == 0:
            self._stop_stream()
        log.debug("VideoBuffer: nclients = %d", self.n_clients)

    def _start_stream(self):
        assert self.n_clients == 0
        if not self._stream:
            self._stream = self.stream_factory()
            log.info("Started stream capture %r", self._stream)
        else:
            assert not self._stopper.ready()
            self._stopper.kill(block=False)
            self._stopper = None

    def _stop_stream(self):
        assert self.n_clients == 0
        assert self._stream
        assert not self._stopper
        def stop_stream():
            log.info("Stopped stream capture %r", self._stream)
            self._stream.close()
            self._stream = None
        holdoff = self.stop_stream_holdoff
        self._stopper = gevent.spawn_later(holdoff, stop_stream)

    def __iter__(self):
        frame = None
        n_timeouts = 0
        with self as stream:
            while True:
                try:
                    timeout = self.frame_timeout if n_timeouts < 2 else None
                    frame = stream.get_frame(frame, timeout=timeout)
                except StreamTimeout:
                    yield self.timeout_frame
                    n_timeouts += 1
                else:
                    if frame is None:
                        break
                    yield frame
                    n_timeouts = 0

    def get_frame(self, current_frame=None, timeout=None):
        with self as stream:
            return stream.get_frame(current_frame, timeout)
