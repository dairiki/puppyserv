# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

import mimetypes
from itertools import cycle
try:
    from gevent import sleep
except ImportError:
    from time import sleep

class VideoFrame(object):
    def __init__(self, image_data, stream):
        self.image_data = image_data
        self.content_type = stream.content_type
        self.stream = stream

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
            filenames = cycle(filenames)
        for fn in filenames:
            with open(fn, 'rb') as fp:
                yield VideoFrame(fp.read(), self)
            sleep(.25)
