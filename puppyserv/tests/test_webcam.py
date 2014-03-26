# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from itertools import count
import time
import unittest
from urllib import urlencode

from webob.dec import wsgify
from webob.exc import HTTPNotFound
from webob import Response

from puppyserv.stream import StreamTimeout
from puppyserv.testing import StopableWSGIServer

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

def setUpModule():
    global test_server
    test_server = StopableWSGIServer.create(DummyWebcam.app)
    test_server.wait()

def tearDownModule():
    global test_server
    test_server.shutdown()

class WebcamStreamTests(object):
    def test_connection_failure(self):
        stream = self.make_one('not_found')
        with self.assertRaises(StreamTimeout):
            stream.get_frame(timeout=0.1)

    def test_bad_content_type(self):
        stream = self.make_one('')
        with self.assertRaises(StreamTimeout):
            stream.get_frame(timeout=0.1)

    def test_get_frame_returns_none_if_closed(self):
        stream = self.make_one()
        stream.close()
        frame = stream.get_frame(timeout=0.01)
        self.assertIs(frame, None)

    def test_get_frame_returns_cached_frame(self):
        stream = self.make_one()
        frame = stream.get_frame(timeout=0.1)
        frame2 = stream.get_frame(timeout=0.1)
        self.assertEqual(frame2.image_data, frame.image_data)


class TestWebcamVideoStream(unittest.TestCase, WebcamStreamTests):
    def make_one(self, path='stream', frame_delay=0.05, bad_boundary=False,
                 max_rate=1000, connect_timeout = 0.1, **kwargs):
        from puppyserv.webcam import WebcamVideoStream
        query = {'delay': frame_delay}
        if bad_boundary:
            query['bad_boundary'] = '1'
        qs = urlencode(query)
        url = test_server.application_url + path + '?' + qs
        stream = WebcamVideoStream(url, max_rate=max_rate,
                                   connect_timeout=connect_timeout, **kwargs)
        self.addCleanup(stream.close)
        return stream

    def test_bad_boundary(self):
        stream = self.make_one(bad_boundary=True)
        with self.assertRaises(StreamTimeout):
            stream.get_frame(timeout=0.1)

    def test_get_frame(self):
        t0 = time.time()
        #stream = self.make_one(frame_delay=0.1)
        stream = self.make_one(frame_delay=0.1, connect_timeout=0.15)

        frame = stream.get_frame(timeout=0.15)
        self.assertEqual(frame.content_type, 'image/jpeg')
        self.assertRegexpMatches(frame.image_data, '^frame 1')

        frame = stream.get_frame(frame, timeout=0.1)
        self.assertEqual(frame.content_type, 'image/jpeg')
        self.assertRegexpMatches(frame.image_data, '^frame 2')

        self.assertLess(time.time() - t0, .3)

    def test_iter(self):
        stream = self.make_one()
        frames = iter(stream)
        frame = next(frames)
        self.assertEqual(frame.content_type, 'image/jpeg')
        self.assertRegexpMatches(frame.image_data, '^frame 1')

        frame = next(frames)
        self.assertEqual(frame.content_type, 'image/jpeg')
        self.assertRegexpMatches(frame.image_data, '^frame 2')

    def test_get_frame_timeout(self):
        #stream = self.make_one(frame_delay=0.1)
        stream = self.make_one(frame_delay=0.1, connect_timeout=0.15)

        frame = stream.get_frame(timeout=0.15)
        self.assertEqual(frame.content_type, 'image/jpeg')
        self.assertRegexpMatches(frame.image_data, '^frame 1')
        with self.assertRaises(StreamTimeout):
            stream.get_frame(timeout=0.01)

class TestWebcamStillStream(unittest.TestCase, WebcamStreamTests):
    def make_one(self, path='snapshot', frame_delay=0,
                 max_rate=1000, connect_timeout = 0.1, **kwargs):
        from puppyserv.webcam import WebcamStillStream
        qs = urlencode({
            'delay': frame_delay,
            })
        url = test_server.application_url + path + '?' + qs
        stream = WebcamStillStream(url, max_rate=max_rate,
                                   connect_timeout=connect_timeout, **kwargs)
        self.addCleanup(stream.close)
        return stream

    def test_get_frame(self):
        stream = self.make_one(frame_delay=0)
        frame = stream.get_frame(timeout=0.05)
        self.assertEqual(frame.content_type, 'image/jpeg')
        self.assertRegexpMatches(frame.image_data, '^image data')

    def test_get_frame_timeout(self):
        stream = self.make_one(frame_delay=0.2, connect_timeout=0.25)
        with self.assertRaises(StreamTimeout) as cm:
            stream.get_frame(timeout=0.05)

    def test_iter(self):
        stream = self.make_one()
        frames = iter(stream)
        for n in range(2):
            frame = next(frames)
            self.assertEqual(frame.content_type, 'image/jpeg')
            self.assertRegexpMatches(frame.image_data, '^image data')


class DummyWebcam(object):
    def __init__(self, request):
        self.request = request
        self.delay = float(request.params.get('delay', 0))
        self.pad = int(request.params.get('pad', 4096))
        self.boundary = request.params.get('boundary', b'boundary')
        self.bad_boundary = bool(request.params.get('bad_boundary', 0))
        open_delay = float(request.params.get('open_delay', 0))
        if open_delay > 0:
            time.sleep(open_delay)

    @wsgify
    @classmethod
    def app(cls, request):
        if request.path_info == '/snapshot':
            return cls(request).snapshot()
        if request.path_info == '/stream':
            return cls(request).stream()
        if request.path_info == '/':
            return Response('Hello World!')
        return HTTPNotFound()

    def snapshot(self):
        return Response(
            content_type='image/jpeg',
            app_iter=self.app_iter([b'image data']))

    def stream(self):
        def mime_part(image_data):
            boundary = 'not-good' if self.bad_boundary else self.boundary
            return b''.join([
                b'--', boundary, '\r\n',
                b'Content-Type: image/jpeg\r\n',
                b'Content-Length: %d\r\n' % len(image_data),
                b'\r\n',
                image_data, b'\r\n'])

        images = ('frame %d' % n for n in count(1))

        return Response(
            content_type='multipart/x-mixed-replace',
            content_type_params={'boundary': self.boundary},
            app_iter=self.app_iter(images, map_function=mime_part),
            )

    def app_iter(self, chunks, map_function=lambda x: x):
        yield b''                       # flush headers
        for chunk in chunks:
            if self.delay > 0:
                time.sleep(self.delay)
            if len(chunk) < self.pad:
                chunk += b' ' * (self.pad - len(chunk))
            yield map_function(chunk)
