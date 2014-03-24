# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from collections import deque
from datetime import datetime
import gevent.event
import glob
import logging
import logging.config
from operator import attrgetter
import threading
import time

import uwsgi
from webob import Response
from webob.dec import wsgify
from webob.exc import HTTPNotFound

from puppyserv.stream import StaticVideoStream
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

    stream = VideoBuffer(stream)

    # FIXME:
    #def start_streamer():
    #    stream.start()
    #uwsgi.postfork_hook = start_streamer()

    return VideoStreamApp(stream, max_total_framerate)

class VideoStreamApp(object):
    def __init__(self, stream, max_total_framerate):
        self.stream = stream
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
        if request.path_info != '/':
            raise HTTPNotFound()

        return Response(
            content_type='multipart/x-mixed-replace',
            content_type_params={'boundary': self.boundary},
            cache_control='no-cache',
            server="puppyserv/0.1.dev0", # FIXME
            date=datetime.utcnow(),
            #accept_ranges='bytes',
            app_iter = self.app_iter(request))

    def app_iter(self, request):
        stream = self.stream
        EOL = b'\r\n'
        client = ClientStats(request)
        self.clients.add(client)
        try:
            for frame in stream:
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
                # FIXME: make configurable
                throttle = len(self.clients) / self.max_total_framerate
                gevent.sleep(max(0, t0 + throttle - time.time()))

            yield b''.join([
                b'--', self.boundary, b'--', EOL,
                ])
        finally:
            self.clients.remove(client)

class ClientStats(object):
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
    def __init__(self, stream, length=4):
        super(VideoBuffer, self).__init__()

        self.stream = stream
        self.content_type = stream.content_type
        self.buf = deque(maxlen=length)
        self.mutex = threading.Lock()
        self.new_frame = gevent.event.Event()
        self.thread = None
        self.stop = False
        self.n_clients = 0

    def _start(self):
        # Defer setting up the async watcher until after uwsgi
        # has started, otherwise it sets up it's own hub
        self.stop = False
        if not self.thread:
            self.buf.clear()
            async = gevent.get_hub().loop.async()
            @async.start
            def _set_new_frame():
                with self.mutex:
                    new_frame = self.new_frame
                    self.new_frame = gevent.event.Event()
                    new_frame.set()
            self.set_new_frame = async.send
            thread = threading.Thread(
                target=self.run,
                kwargs=dict(set_new_frame=async.send))
            thread.daemon = True
            thread.start()
            self.thread = thread

    def run(self, set_new_frame):
        buf = self.buf
        mutex = self.mutex
        for frame in self.stream:
            with mutex:
                buf.appendleft(frame)
                set_new_frame()            # kick main thread
                if self.stop:
                    break
        with mutex:
            self.thread = None
            set_new_frame()            # kick main thread

    def __iter__(self):
        with self.mutex:
            self._start()
            self.n_clients += 1
        try:
            log.debug("VideoBuffer: nclients = %d", self.n_clients)
            # Start with the most recent frame
            while len(self.buf) == 0:
                self.new_frame.wait()
            frame = self.buf[0]

            while frame:
                yield frame
                # Wait for next frame
                next_frame = self._next_frame(frame)
                while next_frame is None:
                    with self.mutex:
                        if self.thread is None:
                            break
                    self.new_frame.wait()
                    next_frame = self._next_frame(frame)
                frame = next_frame
        finally:
            with self.mutex:
                self.n_clients -= 1
                if self.n_clients == 0:
                    self.stop = True
                log.debug("VideoBuffer: nclients = %d", self.n_clients)

    def _next_frame(self, current_frame):
        buf = self.buf
        with self.mutex:
            next_frame = buf[0]
            if next_frame == current_frame:
                return None
            for n in range(1, len(buf)):
                frame = buf[n]
                if frame is current_frame:
                    if n > 1:
                        log.debug("Skipped %d frames", n - 1)
                    return next_frame
                next_frame = frame
            else:
                # Current frame is no longer in buffer.
                # Skip ahead to oldest buffered frame.
                log.debug("Skipping frames")
                return buf[-1]
