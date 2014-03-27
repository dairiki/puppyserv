# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

class StreamTimeout(Exception):
    """ Exception thrown whenever a frame in a stream is unavailable.

    FIXME: This should be renamed/subclassed for more specific errors?

    """

class VideoFrame(object):
    """ A frame in a video stream.

    These currently are always JPEG images.

    """
    def __init__(self, image_data, content_type):
        self.image_data = image_data
        self.content_type = content_type

    @staticmethod
    def from_file(filename):            # pragma: NO COVER
        from puppyserv.stream import video_frame_from_file
        return video_frame_from_file(filename)

class VideoStream(object):
    """ A source of video frames.

    A VideoStream is a source of VideoFrames.

    """
    def next_frame(self):
        """ Get the next frame in the stream.

        Returns ``None`` if there are no more frames in the stream.

        """
        raise NotImplementedError()     # pragma: NO COVER

    def close(self):
        """ Shut it down.
        """
        raise NotImplementedError()     # pragma: NO COVER

class VideoBuffer(object):
    """ A buffered source of video frames.

    """
    def get_frame(self, current_frame=None, timeout=None):
        """ Get the next frame after ``current_frame``.

        If ``current_frame`` is ``None``, or if ``current_frame`` is
        no longer in the video buffer, the most recent frame will be
        returned.

        """
        raise NotImplementedError()     # pragma: NO COVER

    def close(self):
        """ Shut it down.
        """
        raise NotImplementedError()     # pragma: NO COVER
