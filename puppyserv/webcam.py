# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

import logging
from mimetools import Message
import urlparse

from six import text_type
from six.moves.http_client import HTTPConnection

from puppyserv.interfaces import StreamTimeout, VideoFrame, VideoStream
from puppyserv.stream import FailsafeStreamBuffer, ThreadedStreamBuffer
from puppyserv.util import RateLimiter, ReadlineAdapter

DEFAULT_USER_AGENT = 'puppyserv (<dairiki@dairiki.org>)'

log = logging.getLogger(__name__)

class Error(Exception):
    pass
class ConnectionError(Error):
    pass
class StreamingError(Error):
    pass

class NotConfiguredError(Error, ValueError):
    pass

def stream_buffer_from_settings(settings, **kwargs):
    try:
        video_stream = WebcamVideoStream.from_settings(settings, **kwargs)
    except NotConfiguredError:
        video_buffer = None
    else:
        video_buffer = ThreadedStreamBuffer(video_stream)

    try:
        still_config = config_from_settings(settings, subprefix='still.',
                                            **kwargs)
    except NotConfiguredError:
        still_buffer_factory = None
    else:
        def still_buffer_factory():
            still_stream = WebcamStillStream(**still_config)
            return ThreadedStreamBuffer(still_stream)


    if video_buffer and still_buffer_factory:
        return FailsafeStreamBuffer(video_buffer, still_buffer_factory)
    elif video_buffer:
        return video_buffer
    elif still_buffer_factory:
        return still_buffer_factory()
    raise NotConfiguredError(
        'Neither webcam streaming nor still capture was configured')


class WebcamStreamBase(VideoStream):
    request_headers = {
        'Accept': '*/*',
        }

    def __init__(self, url,
                 max_rate=3.0,
                 socket_timeout=10,
                 user_agent=DEFAULT_USER_AGENT):
        netloc, self.url = _parse_url(url)
        self.conn = HTTPConnection(netloc, timeout=socket_timeout)
        self.request_headers = self.request_headers.copy()
        self.request_headers['User-Agent'] = user_agent

        self.stream = None
        self.rate_limiter = RateLimiter(max_rate)
        self.open_rate_limiter = RateLimiter(1.0 / socket_timeout, backoff=1.5)

    @classmethod
    def from_settings(cls, settings, prefix='webcam.', **defaults):
        config = config_from_settings(settings, prefix=prefix,
                                      subprefix=cls.settings_subprefix,
                                      **defaults)
        return cls(**config)


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
        try:
            if self.stream is None:
                self.open_rate_limiter()
                self.stream = self._open_stream()
            frame = next(self.stream, None)
            self.open_rate_limiter.reset()
            return frame
        except Exception as ex:
            self.stream = None
            log.warn("Streaming failed: %s", text_type(ex) or repr(ex))
            self.conn.close()
            raise StreamTimeout(unicode(ex))

class WebcamVideoStream(WebcamStreamBase):
    settings_subprefix = 'stream.'

    def _open_stream(self):
        self.conn.request("GET", self.url, headers=self.request_headers)
        resp = self.conn.getresponse()
        content_type = None
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
                msg = Message(fp, seekable=0)
                content_length = int(msg['content-length'])
                # XXX: impose maximum limit on content_length?
                data = fp.read(content_length)
                if content_type:
                    bad_type = msg.gettype() != content_type
                else:
                    bad_type = msg.getmaintype() != 'image'
                    content_type = msg.gettype()
                if bad_type:
                    raise StreamingError(
                        u"Unexpected content-type\n{msg}\n{data}"
                        .format(**locals()))
                log.debug("Got part\n%s", msg)
                yield VideoFrame(data, msg.gettype())

        finally:
            resp.close()


class WebcamStillStream(WebcamStreamBase):
    request_headers = {
        'Connection': 'keep-alive',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Accept': '*/*',
        }

    settings_subprefix = 'still.'

    def _open_stream(self):
        while True:
            self.conn.request("GET", self.url, headers=self.request_headers)
            resp = self.conn.getresponse()
            data = resp.read()
            if resp.status != 200 or resp.msg.getmaintype() != 'image':
                raise ConnectionError(
                    u"Unexpected response: {resp.status}\n"
                    u"{resp.msg}\n{data}"
                    .format(**locals()))
            log.debug("Got image\n%s", resp.msg)
            yield VideoFrame(data, resp.msg.gettype())

def config_from_settings(settings, prefix='webcam.', subprefix=None,
                         **defaults):
    config = _get_config(defaults, prefix='')
    config.update(_get_config(settings, prefix))
    if subprefix:
        config.update(_get_config(settings, prefix + subprefix))

    # b/c: connect_timeout has been renamed to timeout
    connect_timeout = config.pop('connect_timeout', None)
    if connect_timeout:
        config.setdefault('socket_timeout', connect_timeout)

    if not config.get('url'):
        raise NotConfiguredError("No url is configured")

    return config

def _get_config(settings, prefix='webcam.'):
    def _strip(s):
        return text_type(s).strip()
    config = {}
    for key, coerce in [('url', _strip),
                        ('max_rate', float),
                        ('socket_timeout', float),
                        ('user_agent', _strip),
                        ('connect_timeout', float)]:
        if prefix + key in settings:
            config[key] = coerce(settings[prefix + key])

    return config

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
