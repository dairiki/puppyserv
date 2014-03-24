# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

import logging
from mimetools import Message
#import random
import time
from urllib2 import urlopen, Request

from puppyserv.stream import StreamTimeout, VideoFrame

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
        self.stream = WebcamVideoStream(streaming_url, headers)
        self.stills = WebcamStillStream(still_url, headers)
        self.content_type = None

    def __iter__(self):
        holdoff = 1
        wait = 0
        stream_iter = None
        still_iter = None
        while True:
            if not stream_iter and wait < time.time():
                try:
                    stream_iter = iter(self.stream)
                except Exception as ex:
                    log.warning("Can not open webcam stream: %s", ex)
                    wait = time.time() + holdoff
                    holdoff = min(64, holdoff * 2)
                else:
                    holdoff = 1
            if stream_iter:
                try:
                    yield next(stream_iter)
                except Exception as ex:
                    log.warning("Streaming failed: %s", ex)
                    holdoff = 1
                    wait = time.time() + holdoff
                    stream_iter = None
                else:
                    continue
            try:
                if not still_iter:
                    still_iter = iter(self.stills)
                yield next(still_iter)
            except Exception as ex:
                log.warning("Still capture failed: %s", ex)
                # FIXME:
                time.sleep(10)
                still_iter = None

    def get_frame(self, current_frame=None, timeout=None):
        holdoff = 1
        wait = 0
        stream_iter = None
        still_iter = None
        while True:
            if not stream_iter and wait < time.time():
                try:
                    stream_iter = iter(self.stream)
                except Exception as ex:
                    log.warning("Can not open webcam stream: %s", ex)
                    wait = time.time() + holdoff
                    holdoff = min(64, holdoff * 2)
                else:
                    holdoff = 1
            if stream_iter:
                try:
                    yield next(stream_iter)
                except Exception as ex:
                    log.warning("Streaming failed: %s", ex)
                    holdoff = 1
                    wait = time.time() + holdoff
                    stream_iter = None
                else:
                    continue
            try:
                if not still_iter:
                    still_iter = iter(self.stills)
                yield next(still_iter)
            except Exception as ex:
                log.warning("Still capture failed: %s", ex)
                # FIXME:
                time.sleep(10)
                still_iter = None

class WebcamVideoStream(object):
    def __init__(self, url, headers=HEADERS, timeout=20):
        self.req = Request(url, headers=headers)
        self.timeout = timeout
        self.fp = None

    def __iter__(self):
        frame = self.get_frame()
        while frame:
            yield frame
            frame = self.get_frame(frame)

    def get_frame(self, current_frame=None, timeout=None):
        if not self.fp:
            fp = urlopen(self.req, timeout=timeout)
            status = fp.getcode()
            info = fp.info()
            if status != 200 or info.getmaintype() != 'multipart':
                raise ConnectionError(
                    u"Unexpected response: {status}\n{info}\n{body}"
                    .format(body=fp.read(), **locals()))
            log.debug("Opened stream\n%s", info)
            boundary = info.getparam('boundary')
            assert boundary is not None
            self.fp = fp
        try:
            fp = self.fp
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
            return VideoFrame(data, headers['content-type'])
        except Exception:
            self.fp = None
            raise

class WebcamStillStream(object):
    def __init__(self, url, headers=HEADERS, timeout=10):
        h = {
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            }
        h.update(headers)
        self.req = Request(url, headers=h)

    def __iter__(self):
        frame = self.get_frame()
        while frame:
            yield frame
            frame = self.get_frame(frame)

    def get_frame(self, current_frame=None, timeout=None):
        try:
            fp = urlopen(self.req, timeout=timeout)
            status = fp.getcode()
            info = fp.info()
            if status != 200 or info.getmaintype() != 'image':
                raise ConnectionError(
                    u"Unexpected response: {status}\n{info}\n{body}"
                    .format(body=fp.read(), **locals()))
            headers = fp.info()
            log.debug("Got image\n%s", headers)
            data = fp.read()
            return VideoFrame(data, headers['content-type'])
        except StreamTimeout as ex:
            # FIXME:
            raise StreamTimeout(unicode(ex))
