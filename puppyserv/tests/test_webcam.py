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

from puppyserv.interfaces import StreamTimeout
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
    def make_one(self, path=None,
                 initial_delay=0, frame_delay=0.05, bad_boundary=False,
                 **kwargs):
        if path is None:
            path = self.default_path
        query = {
            'initial_delay': initial_delay,
            'frame_delay': frame_delay,
            }
        if bad_boundary:
            query['bad_boundary'] = '1'
        qs = urlencode(query)
        url = test_server.application_url + path + '?' + qs

        kwargs.setdefault('max_rate', 1000)
        kwargs.setdefault('socket_timeout', 0.1)

        stream = self.stream_class(url, **kwargs)
        self.addCleanup(stream.close)
        return stream

    def test_connection_failure(self):
        stream = self.make_one('not_found')
        with self.assertRaises(StreamTimeout):
            stream.next_frame()

    def test_bad_content_type(self):
        stream = self.make_one('')
        with self.assertRaises(StreamTimeout):
            stream.next_frame()

    def test_next_frame_returns_none_if_closed(self):
        stream = self.make_one()
        stream.close()
        frame = stream.next_frame()
        self.assertIs(frame, None)

    def test_from_settings(self):
        settings = {
            'webcam.url': test_server.application_url + 'not_found',
            'webcam.socket_timeout': '1.0',
            }
        stream = self.stream_class.from_settings(settings)
        with self.assertRaises(StreamTimeout):
            stream.next_frame()


class TestWebcamVideoStream(unittest.TestCase, WebcamStreamTests):
    default_path = 'stream'

    @property
    def stream_class(self):
        from puppyserv.webcam import WebcamVideoStream
        return WebcamVideoStream

    def test_bad_boundary(self):
        stream = self.make_one(bad_boundary=True)
        with self.assertRaises(StreamTimeout):
            stream.next_frame()

    def test_next_frame(self):
        t0 = time.time()
        stream = self.make_one(frame_delay=0.01, socket_timeout=0.05)
        frame = stream.next_frame()
        self.assertEqual(frame.content_type, 'image/jpeg')
        self.assertRegexpMatches(frame.image_data, '^frame 1')

        frame = stream.next_frame()
        self.assertEqual(frame.content_type, 'image/jpeg')
        self.assertRegexpMatches(frame.image_data, '^frame 2')

        self.assertLess(time.time() - t0, .3)

    def test_next_frame_timeout(self):
        stream = self.make_one(frame_delay=0.1, socket_timeout=0.05)
        frame = stream.next_frame()
        self.assertEqual(frame.content_type, 'image/jpeg')
        self.assertRegexpMatches(frame.image_data, '^frame 1')
        with self.assertRaises(StreamTimeout):
            stream.next_frame()

class TestWebcamStillStream(unittest.TestCase, WebcamStreamTests):
    default_path = 'snapshot'

    @property
    def stream_class(self):
        from puppyserv.webcam import WebcamStillStream
        return WebcamStillStream

    def test_next_frame(self):
        stream = self.make_one(frame_delay=0)
        frame = stream.next_frame()
        self.assertEqual(frame.content_type, 'image/jpeg')
        self.assertRegexpMatches(frame.image_data, '^image data')

    def test_next_frame_timeout(self):
        stream = self.make_one(initial_delay=0.2, socket_timeout=0.15)
        with self.assertRaises(StreamTimeout) as cm:
            stream.next_frame()


class Test_config_from_settings(unittest.TestCase):
    def call_it(self, settings, *args, **kwargs):
        from puppyserv.webcam import config_from_settings
        return config_from_settings(settings, *args, **kwargs)

    def test_coercions(self):
        config = self.call_it({
            'webcam.url': ' URL ',
            'webcam.max_rate': ' 3.5 ',
            'webcam.socket_timeout': ' 2.5 ',
            'webcam.user_agent': ' joe ',
            })
        self.assertEqual(config, {
            'url': 'URL',
            'max_rate': 3.5,
            'socket_timeout': 2.5,
            'user_agent': 'joe',
            })

    def test_defaults(self):
        config = self.call_it({}, url='FOO')
        self.assertEqual(config, {'url': 'FOO'})

    def test_subprefix(self):
        config = self.call_it({'webcam.x.url': 'BAR'}, subprefix='x.')
        self.assertEqual(config, {'url': 'BAR'})

    def test_connect_timeout(self):
        config = self.call_it({'webcam.connect_timeout': '1.5'}, url='URL')
        self.assertEqual(config['socket_timeout'], 1.5)

    def test_raises_if_no_url(self):
        from puppyserv.webcam import NotConfiguredError
        with self.assertRaises(NotConfiguredError):
            self.call_it({})

class DummyWebcam(object):
    def __init__(self, request):
        self.request = request
        self.initial_delay = float(request.params.get('initial_delay', 0))
        self.frame_delay = float(request.params.get('frame_delay', 0))
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
        if self.initial_delay > 0:
            time.sleep(self.initial_delay)
        for chunk in chunks:
            if len(chunk) < self.pad:
                chunk += b' ' * (self.pad - len(chunk))
            yield map_function(chunk)
            if self.frame_delay > 0:
                time.sleep(self.frame_delay)
