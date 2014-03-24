# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

import logging
from mimetools import Message
import random
import time
from urllib2 import urlopen, Request

try:
    from gevent import sleep
except ImportError:
    from time import sleep

from puppyserv.stream import StreamTimeout, VideoFrame, VideoStreamer

HEADERS = {
    'Accept': '*/*',
    'Referer': 'http://example.com/',
    'User-Agent': 'violet/0.1 (<dairiki@dairiki.org>)',
    }

log = logging.getLogger(__name__)

class Error(Exception):
    pass
class ConnectionError(Error):
    pass
class StreamingError(Error):
    pass

class WebcamFailsafeStream(object):
    def __init__(self, streaming_url, still_url, headers=HEADERS):
        self.stream = VideoStreamer(WebcamVideoStream(streaming_url, headers))
        self.still_url = still_url
        self.headers = headers
        self.still_stream = None
        self.closed = False

    def close(self):
        self.closed = True
        self.stream.close()
        if self.still_stream:
            self.still_stream.close()
            self.still_stream = None

    def __iter__(self):
        frame = self.get_frame()
        while frame:
            yield frame
            frame = self.get_frame(frame)

    def get_frame(self, current_frame=None, timeout=None):
        if self.closed:
            return None

        streaming_frame = getattr(current_frame, 'streaming_frame',
                                  current_frame)

        if not self.still_stream:
            timeout1 = timeout / 2 if timeout is not None else 5
            timeout2 = timeout1
            try:
                return self.stream.get_frame(streaming_frame, timeout=timeout1)
            except StreamTimeout:
                self.still_stream = VideoStreamer(
                    WebcamStillStream(self.still_url, self.headers))
            else:
                return frame
        else:
            timeout2 = timeout if timeout is not None else 10

        try:
            frame = self.stream.get_frame(streaming_frame, timeout=timeout2)
        except StreamTimeout:
            timeout3 = 0.1 if timeout is not None else None
            frame = self.still_stream.get_frame(current_frame, timeout=timeout3)
            frame.streaming_frame = streaming_frame
            return frame
        else:
            self.still_stream.close()
            self.still_stream = None
            return frame

class WebcamVideoStream(object):
    def __init__(self, url, headers=HEADERS, timeout=20, max_rate=3.0):
        self.req = Request(url, headers=headers)
        self.timeout = timeout
        self.closed = False
        self.frame = None
        self.stream = None
        self.rate_limit = RateLimiter(max_rate)

    def close(self):
        self.closed = True

    def __iter__(self):
        frame = self.get_frame()
        while frame:
            yield frame
            frame = self.get_frame(frame)

    def get_frame(self, current_frame=None, timeout=None):
        if self.closed:
            return None

        self.rate_limit()

        frame = self.frame
        if frame and frame is not current_frame:
            return frame

        if random.randrange(10) < 1:
            time.sleep(10)

        try:
            if self.stream is None:
                self.stream = self._open_stream()
            return next(self.stream)
        except Exception as ex:
            self.stream = None
            self.frame = None
            log.warn("Streaming failed: %s", ex)
            raise StreamTimeout()

    def _open_stream(self):
        fp = urlopen(self.req, timeout=self.timeout)
        status = fp.getcode()
        info = fp.info()
        if status != 200 or info.getmaintype() != 'multipart':
            raise ConnectionError(
                u"Unexpected response: {status}\n{info}\n{body}"
                .format(body=fp.read(), **locals()))
        log.debug("Opened stream\n%s", info)
        boundary = info.getparam('boundary')
        assert boundary is not None

        while True:
            sep = fp.readline(80)
            if sep.strip() == '':
                sep = fp.readline(80)
            if not sep.startswith('--' + boundary):
                raise StreamingError(u"Bad boundary %r" % sep)
            # Testing
            #if random.randrange(10) < 1:
            #    raise StreamingError(u"random puke")
            headers = Message(fp, seekable=0)
            log.debug("Got part\n%s", headers)
            content_length = int(headers['content-length'])
            data = fp.read(content_length)
            yield VideoFrame(data, headers['content-type'])

class WebcamStillStream(object):
    def __init__(self, url, headers=HEADERS, timeout=10, max_rate=1.0):
        h = {
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            }
        h.update(headers)
        self.req = Request(url, headers=h)
        self.timeout = timeout
        self.frame = None
        self.closed = False
        self.rate_limit = RateLimiter(max_rate)

    def close(self):
        self.closed = True

    def __iter__(self):
        frame = self.get_frame()
        while frame:
            yield frame
            frame = self.get_frame(frame)

    def get_frame(self, current_frame=None, timeout=None):
        if self.closed:
            return None

        self.rate_limit()

        frame = self.frame
        if frame and frame is not current_frame:
            return frame

        try:
            fp = urlopen(self.req, timeout=self.timeout)
            status = fp.getcode()
            info = fp.info()
            if status != 200 or info.getmaintype() != 'image':
                raise ConnectionError(
                    u"Unexpected response: {status}\n{info}\n{body}"
                    .format(body=fp.read(), **locals()))
            headers = fp.info()
            log.debug("Got image\n%s", headers)
            data = fp.read()
            self.frame = VideoFrame(data, headers['content-type'])
            return self.frame
        except Exception as ex:
            self.frame = None
            log.warn("Still capture failed: %s", ex)
            raise StreamTimeout(unicode(ex))

class RateLimiter(object):
    def __init__(self, max_rate):
        self.dt = 1.0/max_rate
        self.wait_until = None

    def __call__(self):
        now = time.time()
        wait_until = self.wait_until
        if wait_until and wait_until > now:
            sleep(wait_until - now)
            self.wait_until += self.dt
        else:
            self.wait_until = now + self.dt
        print now, self.wait_until
