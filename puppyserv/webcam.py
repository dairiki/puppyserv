# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

import logging
from mimetools import Message
import random
import urlparse

from six.moves.http_client import HTTPConnection

from puppyserv.interfaces import StreamTimeout, VideoFrame, VideoStream
from puppyserv.stream import FailsafeStreamBuffer, ThreadedStreamBuffer
from puppyserv.util import sleep, RateLimiter, ReadlineAdapter

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
    for key in ['max_rate', 'timeout', 'user_agent']:
        if key in defaults:
            stream_config.setdefault(key, defaults[key])
            still_config.setdefault(key, defaults[key])

    def still_buffer_factory():
        return ThreadedStreamBuffer(WebcamStillStream(**still_config))

    if 'url' in stream_config:
        video_buffer = ThreadedStreamBuffer(WebcamVideoStream(**stream_config))
        if 'url' in still_config:
            return FailsafeStreamBuffer(video_buffer, still_buffer_factory)
        else:
            return video_buffer
    else:
        assert 'url' in still_config
        return still_buffer_factory()

def _get_config(settings, prefix='webcam.'):
    config = {}
    for key, coerce in [('url', lambda s: s.strip()),
                        ('max_rate', float),
                        ('timeout', float),
                        ('connect_timeout', float)]:
        if prefix + key in settings:
            config[key] = coerce(settings[prefix + key])

    # b/c: connect_timeout has been renamed to timeout
    connect_timeout = config.pop('connect_timeout', None)
    if connect_timeout:
        config.setdefault('timeout', connect_timeout)

    return config

class WebcamVideoStream(VideoStream):
    def __init__(self, url,
                 timeout=10,
                 max_rate=3.0,
                 user_agent=DEFAULT_USER_AGENT):
        netloc, self.url = _parse_url(url)
        self.conn = HTTPConnection(netloc, timeout=timeout)
        self.request_headers = {
            'Accept': '*/*',
            'User-Agent': user_agent,
            }

        self.stream = None
        self.max_rate = max_rate
        self.rate_limiter = RateLimiter(max_rate)
        self.open_rate_limiter = RateLimiter(1.0 / timeout)

    def close(self):
        if self.stream:
            self.stream = None
        if self.conn:
            self.conn.close()
            self.conn = None

    @property
    def closed(self):
        return not self.conn

    def next_frame(self):
        if self.closed:
            return None
        self.rate_limiter()
        #if random.randrange(10) < 1:
        #    sleep(10)
        try:
            if self.stream is None:
                self.open_rate_limiter()
                self.stream = self._open_stream()
            return next(self.stream, None)
        except Exception as ex:
            self.stream = None
            log.warn("Streaming failed: %s", ex)
            raise StreamTimeout(unicode(ex))

    def _open_stream(self):
        self.conn.request("GET", self.url, headers=self.request_headers)
        resp = self.conn.getresponse()
        try:
            if resp.status != 200 or resp.msg.getmaintype() != 'multipart':
                raise ConnectionError(
                    u"Unexpected response: {resp.status}\n"
                    u"{resp.msg}\n{data}"
                    .format(data=resp.read(), **locals()))
            log.debug("Opened stream\n%s", resp.msg)
            boundary = resp.msg.getparam('boundary')
            assert boundary

            fp = ReadlineAdapter(resp)
            while True:
                sep = fp.readline().rstrip()
                if not sep:
                    # XXX: instead of this should just read two bytes
                    # after the end of the data?
                    sep = fp.readline().rstrip()
                if sep != b'--' + boundary:
                    if sep != b'--' + boundary + b'--':
                        raise StreamingError(u"Bad boundary %r" % sep)
                    break
                # Testing
                #if random.randrange(10) < 1:
                #    raise StreamingError(u"random puke")
                msg = Message(fp, seekable=0)
                log.debug("Got part\n%s", msg)
                content_length = int(msg['content-length'])
                # XXX: impose maximum limit on content_length?
                data = fp.read(content_length)
                yield VideoFrame(data, msg.gettype())

        finally:
            resp.close()


class WebcamStillStream(VideoStream):
    request_headers = {
        'Connection': 'keep-alive',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Accept': '*/*',
        }

    def __init__(self, url, user_agent=DEFAULT_USER_AGENT,
                 timeout=10, max_rate=1.0):
        netloc, self.url = _parse_url(url)
        self.conn = HTTPConnection(netloc, timeout=timeout)
        self.request_headers = self.request_headers.copy()
        self.request_headers['User-Agent'] = user_agent

        self.max_rate = max_rate
        self.rate_limiter = RateLimiter(max_rate)

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    @property
    def closed(self):
        return not self.conn

    def next_frame(self):
        if self.closed:
            return None
        self.rate_limiter()
        try:
            self.conn.request("GET", self.url, headers=self.request_headers)
            resp = self.conn.getresponse()
            data = resp.read()
            if resp.status != 200 or resp.msg.getmaintype() != 'image':
                raise ConnectionError(
                    u"Unexpected response: {resp.status}\n"
                    u"{resp.msg}\n{data}"
                    .format(**locals()))
            log.debug("Got image\n%s", resp.msg)
            return VideoFrame(data, resp.msg.gettype())
        except Exception as ex:
            log.warn("Still capture failed: %s", ex)
            raise StreamTimeout(unicode(ex))

def _parse_url(url):
    u = urlparse.urlsplit(url)
    if u.scheme != 'http':
        raise ValueError("Only http URLs are currently supported")
    if u.username or u.password:
        raise ValueError("HTTP authentication is not currently supported")
    path = u.path
    if u.query:
        path += '?' + u.query
    return u.netloc, path
