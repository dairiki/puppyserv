# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from collections import deque
from datetime import datetime
import gevent.event
import glob
import itertools
import logging
import logging.config
import mimetypes
import threading
import time

from paste.urlmap import URLMap
from pkg_resources import resource_filename
from webob import Response
from webob.dec import wsgify
from webob.exc import HTTPNotFound
from webob.static import DirectoryApp
from webhelpers.html import HTML


log = logging.getLogger(__name__)

def main(global_config, **settings):
    stream = None

    logging.config.fileConfig(global_config['__file__'], global_config)

    if 'static.images' in settings:
        image_files = sorted(glob.glob(settings['static.images']))
        stream = StaticVideoStream(image_files, loop=False)

    stream = VideoBuffer(stream)

    # FIXME:
    #@uwsgidecorators.postfork
    #def start_streamer():
    #    stream.start()

    return VideoStreamApp(stream)

class VideoStreamApp(object):
    def __init__(self, stream):
        self.stream = stream
        self.boundary = b'ipcamera'

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
            app_iter = self.app_iter())

    def app_iter(self):
        stream = self.stream
        EOL = b'\r\n'
        for frame in stream:
            data = frame.image_data
            yield b''.join([
                b'--', self.boundary, EOL,
                b'Content-Type: ', stream.content_type, EOL,
                b'Content-length: ', str(len(data)), EOL,
                EOL,
                data, EOL,
                ])
        yield b''.join([
            b'--', self.boundary, b'--', EOL,
            ])

class VideoFrame(object):
    def __init__(self, image_data, stream):
        self.image_data = image_data
        self.stream = stream


class VideoBuffer(object):
    def __init__(self, stream, length=4):
        super(VideoBuffer, self).__init__()

        self.stream = stream
        self.content_type = stream.content_type
        self.buf = deque(maxlen=length)
        self.mutex = threading.Lock()
        self.new_frame = gevent.event.Event()
        self.thread = None

    def start(self):
        # Defer setting up the async watcher until after uwsgi
        # has started, otherwise it sets up it's own hub
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
        with mutex:
            self.thread = None
            set_new_frame()            # kick main thread

    def __iter__(self):
        with self.mutex:
            if not self.thread:
                self.start()

        # Start with the most recent frame
        while len(self.buf) == 0:
            self.new_frame.wait()
        frame = self.buf[0]

        while frame:
            print "BUF", self.buf, frame
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

class StaticVideoStream(object):
    def __init__(self, image_filenames, loop=True):
        if not image_filenames:
            raise ValueError("No images given")
        content_type, encoding = mimetypes.guess_type(image_filenames[0])
        if not content_type:
            raise ValueError("Can not guess content type")

        self.image_filenames = image_filenames
        self.loop = loop
        self.content_type = content_type

    def __iter__(self):
        filenames = self.image_filenames
        if self.loop:
            filenames = itertools.cycle(filenames)
        for fn in filenames:
            with open(fn, 'rb') as fp:
                yield VideoFrame(fp.read(), self)
            time.sleep(.25)
