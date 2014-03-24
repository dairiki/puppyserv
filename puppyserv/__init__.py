# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from collections import deque
from contextlib import contextmanager
from datetime import datetime
from functools import partial, wraps
import gevent.event
from gevent.monkey import get_original
import glob
import logging
import logging.config
from operator import attrgetter
from pkg_resources import resource_filename
import time

threading_Thread, threading_Lock = get_original('threading',
                                                ['Thread', 'Lock'])

import uwsgi
from webob import Response
from webob.dec import wsgify
from webob.exc import HTTPGatewayTimeout, HTTPNotFound

from puppyserv.stream import StaticVideoStream, VideoFrame
from puppyserv.webcam import (
    WebcamFailsafeStream,
    WebcamStillStream,
    WebcamVideoStream,
    )

log = logging.getLogger(__name__)

def main(global_config, **settings):
    stream = None

    logging.config.fileConfig(global_config['__file__'], global_config)

    max_total_framerate = float(settings.get('max_total_framerate', 100.0))

    if 'static.images' in settings:
        image_files = sorted(glob.glob(settings['static.images']))
        stream = StaticVideoStream(image_files)
    else:
        streaming_url = settings.get('webcam.streaming_url').strip()
        still_url = settings.get('webcam.still_url').strip()
        if streaming_url and still_url:
            stream = WebcamFailsafeStream(streaming_url, still_url)
        elif streaming_url:
            stream = WebcamVideoStream(streaming_url)
        elif still_url:
            stream = WebcamStillStream(still_url)

    return VideoStreamApp(stream, max_total_framerate)

class VideoStreamApp(object):
    def __init__(self, stream, max_total_framerate):
        self.stream = VideoBuffer(stream)
        self.max_total_framerate = max_total_framerate
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
            server="puppyserv/0.1.dev0", # FIXME
            date=datetime.utcnow(),
            app_iter = self.app_iter(request))

    @wsgify
    def snapshot(self, request):
        frame = self.stream.get_frame()
        if frame is None:
            raise HTTPGatewayTimeout('Not connected to webcam')
        return Response(
            cache_control='no-cache',
            server="puppyserv/0.1.dev0", # FIXME
            date=datetime.utcnow(),
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
    def __init__(self, stream, length=4, timeout=1):
        self.n_clients = 0
        self._stream_factory = partial(VideoStreamer, stream, length)
        self._stream = None

        self.timeout = timeout
        timeout_image = resource_filename('puppyserv', 'timeout.jpg')
        self.timeout_frame = VideoFrame.from_file(timeout_image)

    # XXX: These would need a mutex if they were to be called from more
    # than one thread, but since we're geventing, we don't need it.
    def __enter__(self):
        if not self._stream:
            assert self.n_clients == 0
            self._stream = self._stream_factory()
            # FIXME: move to VideoStreamer.__init__?
            self._stream.start()
        self.n_clients += 1
        log.debug("VideoBuffer: nclients = %d", self.n_clients)
        return self._stream

    def __exit__(self, exc_type, exc_value, exc_tb):
        assert self.n_clients > 0
        self.n_clients -= 1
        if self.n_clients == 0:
            self._stream.stop()
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
                    timeout = None
                    yield self.timeout_frame
                else:
                    if frame is None:
                        break
                    timeout = self.timeout
                    yield frame

    def get_frame(self, current_frame=None, timeout=None):
        with self as stream:
            return stream.get_frame(current_frame, timeout)

def synchronized(method):
    @wraps(method)
    def wrapper(self, *args):
        with self.mutex:
            return method(self, *args)
    return wrapper

class StreamTimeout(Exception):
    pass

class VideoStreamer(threading_Thread):
    """ Stream video is a separate thread.
    """
    def __init__(self, stream, length=4):
        super(VideoStreamer, self).__init__()
        self.daemon = True

        self.stream = stream
        self.buf = deque(maxlen=length)
        self.mutex = threading_Lock()
        self.shutdown = False

        self.new_frame_event = gevent.event.Event()

        # gevent magic:
        # Hook to to set the new_frame event in the main thread
        async = gevent.get_hub().loop.async()
        async.start(self._signal_new_frame)
        self.signal_new_frame = async.send

    def stop(self):
        self.shutdown = True

    def run(self):
        # FIXME: how to get to stop even if no frames are coming in
        buf = self.buf
        mutex = self.mutex

        log.info("Capture thread starting: %r", self)
        for frame in self.stream:
            with mutex:
                buf.appendleft(frame)
                self.signal_new_frame()            # kick main thread
            if self.shutdown:
                break
        log.info("Capture thread stopping: %r", self)

    @synchronized
    def _signal_new_frame(self):
        new_frame_event = self.new_frame_event
        self.new_frame_event = gevent.event.Event()
        new_frame_event.set()

    def __iter__(self):
        frame = self.get_frame()
        while frame:
            yield frame
            frame = self.get_frame(frame)

    def get_frame(self, current_frame=None, timeout=None):
        frame = self._get_frame(current_frame)
        while isinstance(frame, gevent.event.Event):
            if not self.is_alive():
                return None             # end of stream
            if not frame.wait(timeout):
                raise StreamTimeout()
            frame = self._get_frame(current_frame)
        return frame

    @synchronized
    def _get_frame(self, current_frame=None):
        buf = self.buf

        if len(buf) == 0:
            return self.new_frame_event

        if current_frame is None:
            # Start with the most recent frame
            return buf[0]

        frames = iter(buf)
        next_frame = next(frames)
        if next_frame == current_frame:
            return self.new_frame_event

        for nskip, frame in enumerate(frames):
            if frame is current_frame:
                if nskip:
                    log.debug("Skipped %d frames", nskip)
                return next_frame
            next_frame = frame
        # Current frame is no longer in buffer.
        # Skip ahead to oldest buffered frame.
        log.debug("Skipping frames")
        return buf[-1]
