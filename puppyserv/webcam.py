# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

import logging
from mimetools import Message
import mimetypes
import os
import shutil
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
            headers = Message(fp, seekable=0)
            log.debug("Got part\n%s", headers)
            if not self.content_type:
                self.content_type = headers['content-type']
            content_length = int(headers['content-length'])
            data = fp.read(content_length)
            yield VideoFrame(data, self)
