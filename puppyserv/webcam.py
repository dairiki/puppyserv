# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from functools import partial
import logging
from mimetools import Message
#import random
import time
from urllib2 import urlopen, Request

from puppyserv.stream import VideoFrame

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
        self.open_stream = partial(WebcamVideoStream, streaming_url, headers)
        self.stream_iter = None
        self.still_iter = iter(WebcamStillStream(still_url, headers))
        self.holdoff = 1
        self.wait = 0
        self.content_type = None

    def __iter__(self):
        while True:
            if not self.stream_iter and self.wait < time.time():
                try:
                    self.stream_iter = iter(self.open_stream())
                except Exception as ex:
                    log.warning("Can not open webcam stream: %s", ex)
                    self.wait = time.time() + self.holdoff
                    self.holdoff = min(64, self.holdoff * 2)
                else:
                    self.holdoff = 1
            if self.stream_iter:
                try:
                    yield next(self.stream_iter)
                except Exception as ex:
                    log.warning("Streaming failed: %s", ex)
                    self.holdoff = 1
                    self.wait = time.time() + self.holdoff
                    self.stream_iter = None
                else:
                    continue
            try:
                yield next(self.still_iter)
            except Exception as ex:
                log.warning("Still capture failed: %s", ex)
                # FIXME:
                time.sleep(10)

class WebcamVideoStream(object):
    def __init__(self, url, headers=HEADERS):
        fp = urlopen(Request(url, headers=headers))
        status = fp.getcode()
        info = fp.info()
        if status != 200 or info.getmaintype() != 'multipart':
            raise ConnectionError(
                u"Unexpected response: {status}\n{info}\n{body}"
                .format(body=fp.read(), **locals()))

        log.debug("Opened stream\n%s", info)
        boundary = info.getparam('boundary')
        assert boundary is not None
        self.boundary = '--' + boundary
        self.fp = fp
        self.headers = info
        self.content_type = None

    def __iter__(self):
        fp = self.fp
        while True:
            sep = fp.readline(80)
            if sep.strip() == '':
                sep = fp.readline(80)
            if not sep.startswith(self.boundary):
                raise StreamingError(u"Bad boundary %r" % sep)
            # Testing
            #if random.randrange(10) < 1:
            #    raise StreamingError(u"random puke")
            headers = Message(fp, seekable=0)
            log.debug("Got part\n%s", headers)
            if not self.content_type:
                self.content_type = headers['content-type']
            content_length = int(headers['content-length'])
            data = fp.read(content_length)
            yield VideoFrame(data, self)

class WebcamStillStream(object):
    def __init__(self, url, headers=HEADERS):
        h = {
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            }
        h.update(headers)
        self.req = Request(url, headers=h)
        self.content_type = None

    def __iter__(self):
        while True:
            fp = urlopen(self.req)
            status = fp.getcode()
            info = fp.info()
            if status != 200 or info.getmaintype() != 'image':
                raise ConnectionError(
                    u"Unexpected response: {status}\n{info}\n{body}"
                    .format(body=fp.read(), **locals()))
            headers = fp.info()
            log.debug("Got image\n%s", headers)
            if not self.content_type:
                self.content_type = headers['content-type']
            content_length = int(headers['content-length'])
            data = fp.read()
            yield VideoFrame(data, self)
