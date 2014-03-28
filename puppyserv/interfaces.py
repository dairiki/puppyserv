# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

class VideoFrame(object):
    """ A frame in a video stream.

    These currently are always JPEG images.

    """
    def __init__(self, image_data, content_type='image/jpeg'):
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
    def next(self):
        """ Get the next frame in the stream.

        Returns ``None`` on acquisition timeout.

        Raises ``StopIteration`` if there are no more frames in the stream.

        """
        raise NotImplementedError()     # pragma: NO COVER

    def close(self):
        """ Shut it down.
        """
        raise NotImplementedError()     # pragma: NO COVER

    def __iter__(self):
        return self

class VideoBuffer(object):
    """ A buffered source of video frames.

    """
    def stream(self):
        """ Get an iterator for the buffered stream.

        By default, the first frame of the stream will be the most
        recently buffered frame, or, if the buffer is empty, the next
        frame acquired.  Some buffer types may provide optional arguments
        to this method to alter that behavior.

        """
        raise NotImplementedError()     # pragma: NO COVER

    def __iter__(self):
        """ Always returns the default no-arg version of ``stream``.

        """
        return self.stream()

    def close(self):
        """ Shut it down.
        """
        raise NotImplementedError()     # pragma: NO COVER
