# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

import logging
from mimetools import Message
import random
import time
from urllib2 import urlopen, Request


from puppyserv.stream import StreamTimeout, VideoFrame, VideoStreamer
from puppyserv.util import sleep, RateLimiter

DEFAULT_USER_AGENT = 'puppyserv (<dairiki@dairiki.org>)'

log = logging.getLogger(__name__)

class Error(Exception):
    pass
class ConnectionError(Error):
    pass
class StreamingError(Error):
    pass

def webcam_stream_from_settings(settings, prefix='webcam.', **defaults):
    defaults.update(_get_config(settings, prefix))
    stream_config = _get_config(settings, prefix + 'stream.')
    still_config = _get_config(settings, prefix + 'still.')
    for key in ['max_rate', 'connect_timeout', 'user_agent']:
        if key in defaults:
            stream_config.setdefault(key, defaults[key])
            still_config.setdefault(key, defaults[key])

    if 'url' in stream_config:
        if 'url' in still_config:
            return WebcamFailsafeStream(stream_config, still_config)
        else:
            return VideoStreamer(WebcamVideoStream(**stream_config))
    else:
        assert 'url' in still_config
        return VideoStreamer(WebcamStillStream(**still_config))

def _get_config(settings, prefix='webcam.'):
    config = {}
    for key, coerce in [('url', lambda s: s.strip()),
                        ('max_rate', float),
                        ('connect_timeout', float)]:
        if prefix + key in settings:
            config[key] = coerce(settings[prefix + key])
    return config

class WebcamFailsafeStream(object):
    def __init__(self, stream_config, still_config):
        self.stream = VideoStreamer(WebcamVideoStream(**stream_config))
        self.still_config = still_config
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

        stream = self.stream
        stills = self.still_stream

        streaming_frame = getattr(current_frame, 'streaming_frame',
                                  current_frame)

        stream_timeout = timeout if timeout is not None else 10

        if not stills:
            min_timeout = 2 / stream.stream.max_rate
            timeout1 = max(stream_timeout / 3, min_timeout)
            timeout2 = max(stream_timeout - timeout1, 0.01)
            try:
                return stream.get_frame(streaming_frame, timeout=timeout1)
            except StreamTimeout:
                pass
            stills = self.still_stream = \
                     VideoStreamer(WebcamStillStream(**self.still_config))
        else:
            timeout2 = stream_timeout

        try:
            frame = stream.get_frame(streaming_frame, timeout=timeout2)
        except StreamTimeout:
            timeout3 = 0.1 if timeout is not None else None
            frame = stills.get_frame(current_frame, timeout=timeout3)
            frame.streaming_frame = streaming_frame
            log.info("Returning still")
            return frame
        else:
            stills.close()
            self.still_stream = None
            return frame

class WebcamVideoStream(object):
    def __init__(self, url, user_agent=DEFAULT_USER_AGENT,
                 connect_timeout=10, max_rate=3.0):
        headers = {
            'Accept': '*/*',
            'User-Agent': user_agent,
            }
        self.req = Request(url, headers=headers)
        self.connect_timeout = connect_timeout
        self.closed = False
        self.frame = None
        self.stream = None
        self.max_rate = max_rate
        self.rate_limiter = RateLimiter(max_rate)
        self.open_rate_limiter = RateLimiter(1.0 / connect_timeout)

    def close(self):
        self.closed = True
        if self.stream:
            self.stream.close()

    def __iter__(self):
        frame = self.get_frame()
        while frame:
            yield frame
            frame = self.get_frame(frame)

    def get_frame(self, current_frame=None, timeout=None):
        if self.closed:
            return None

        self.rate_limiter()

        frame = self.frame
        if frame and frame is not current_frame:
            return frame

        #if random.randrange(10) < 1:
        #    time.sleep(10)

        try:
            if self.stream is None:
                self.open_rate_limiter()
                self.stream = _Stream(self.req, self.connect_timeout)
            return self.stream.get_frame()
        except Exception as ex:
            self.stream = None
            self.frame = None
            log.warn("Streaming failed: %s", ex)
            raise StreamTimeout()

class _Stream(object):
    def __init__(self, req, timeout):
        fp = urlopen(req, timeout=timeout)
        try:
            status = fp.getcode()
            info = fp.info()
            if status != 200 or info.getmaintype() != 'multipart':
                raise ConnectionError(
                    u"Unexpected response: {status}\n{info}\n{body}"
                    .format(body=fp.read(), **locals()))
            log.debug("Opened stream\n%s", info)
            self.boundary = info.getparam('boundary')
            assert self.boundary is not None
        except:
            fp.close()
            raise
        else:
            self.fp = fp

    def close(self):
        self.fp.close()

    def get_frame(self):
        fp = self.fp
        try:
            sep = fp.readline(80)
            if sep.strip() == '':
                sep = fp.readline(80)
            if not sep.startswith('--' + self.boundary):
                raise StreamingError(u"Bad boundary %r" % sep)
            # Testing
            #if random.randrange(10) < 1:
            #    raise StreamingError(u"random puke")
            headers = Message(fp, seekable=0)
            log.debug("Got part\n%s", headers)
            content_length = int(headers['content-length'])
            data = fp.read(content_length)
            return VideoFrame(data, headers['content-type'])
        except:
            fp.close()
            raise

class WebcamStillStream(object):
    def __init__(self, url, user_agent=DEFAULT_USER_AGENT,
                 connect_timeout=10, max_rate=1.0):
        headers = {
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'Accept': '*/*',
            'User-Agent': user_agent,
            }
        self.req = Request(url, headers=headers)
        self.connect_timeout = connect_timeout
        self.frame = None
        self.closed = False
        self.max_rate = max_rate
        self.rate_limiter = RateLimiter(max_rate)

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

        self.rate_limiter()

        frame = self.frame
        if frame and frame is not current_frame:
            return frame

        try:
            fp = urlopen(self.req, timeout=self.connect_timeout)
            status = fp.getcode()
            info = fp.info()
            if status != 200 or info.getmaintype() != 'image':
                raise ConnectionError(
                    u"Unexpected response: {status}\n{info}\n{body}"
                    .format(body=fp.read(), **locals()))
            headers = fp.info()
            #log.debug("Got image\n%s", headers)
            log.info("Got image\n%s", headers)
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
