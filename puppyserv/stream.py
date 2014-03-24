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
    def __init__(self, image_data, content_type):
        self.image_data = image_data
        self.content_type = content_type

    @classmethod
    def from_file(cls, filename):
        content_type, encoding = mimetypes.guess_type(filename)
        if not content_type:
            raise ValueError("Can not guess content type")
        with open(filename, 'rb') as fp:
            return cls(fp.read(), content_type)

class StaticVideoStream(object):
    def __init__(self, image_filenames, loop=True):
        if not image_filenames:
            raise ValueError("No images given")
        self.frames = map(VideoFrame.from_file, image_filenames)
        self.loop = loop

    def __iter__(self):
        frames = self.frames
        if self.loop:
            frames = cycle(frames)
        for frame in frames:
            yield frame
            sleep(2.25)
