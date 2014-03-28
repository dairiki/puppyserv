# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

from itertools import count
import time
import unittest

from six.moves.queue import Queue

from webob.dec import wsgify
from webob.exc import HTTPNotFound
from webob import Response

from puppyserv.interfaces import VideoFrame
from puppyserv.testing import StopableWSGIServer

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

def setUpModule():
    global test_server, test_app
    test_app = DummyWebcam()
    test_server = StopableWSGIServer.create(test_app)
    test_server.wait()

def tearDownModule():
    global test_server
    test_server.shutdown()

class Test_stream_buffer_from_settings(unittest.TestCase):
    def call_it(self, settings, **kwargs):
        from puppyserv.webcam import stream_buffer_from_settings
        return stream_buffer_from_settings(settings, **kwargs)

    def test_config_failsafe_buffer(self):
        from puppyserv.stream import FailsafeStreamBuffer
        settings = {
            'webcam.stream.url': 'http://example.com/',
            'webcam.still.url': 'http://example.com/snap',
            }
        buf = self.call_it(settings)
        self.assertIsInstance(buf, FailsafeStreamBuffer)

    def test_config_video_buffer(self):
        from puppyserv.webcam import WebcamVideoStream
        settings = {
            'webcam.stream.url': 'http://example.com/',
            }
        buf = self.call_it(settings)
        self.assertIsInstance(buf.source, WebcamVideoStream)

    def test_config_still_buffer(self):
        from puppyserv.webcam import WebcamStillStream
        settings = {
            'webcam.still.url': 'http://example.com/',
            }
        buf = self.call_it(settings)
        self.assertIsInstance(buf.source, WebcamStillStream)

    def test_unconfigured(self):
        from puppyserv.webcam import NotConfiguredError
        with self.assertRaises(NotConfiguredError):
            self.call_it({})

class WebcamStreamTests(object):
    def make_one(self, path=None, **kwargs):
        if path is None:
            path = self.default_path

        kwargs.setdefault('max_rate', 1000)
        kwargs.setdefault('rate_bucket_size', 1)
        kwargs.setdefault('socket_timeout', 0.1)

        url = test_server.application_url + path

        # Clear the test app frame queue
        global frame_queue
        self.frame_queue = frame_queue = Queue()

        stream = self.stream_class(url, **kwargs)
        self.addCleanup(stream.close)
        return stream

    def send_frame(self, frame=None):
        if frame is None:
            frame = DummyVideoFrame()
        self.frame_queue.put(frame)

    def test_from_settings(self):
        settings = {
            'webcam.url': test_server.application_url + 'not_found',
            'webcam.socket_timeout': '1.0',
            }
        stream = self.stream_class.from_settings(settings)
        self.assertIs(next(stream), None)

    def test_connection_failure(self):
        stream = self.make_one('not_found')
        self.assertIs(next(stream), None)

    def test_bad_content_type(self):
        stream = self.make_one('')
        self.assertIs(next(stream), None)

    def test_next_frame(self):
        t0 = time.time()
        stream = self.make_one()
        for n in range(4):
            source_frame = DummyVideoFrame()
            self.send_frame(source_frame)
            frame = next(stream)
            self.assertEqual(frame, source_frame)
        self.assertLess(time.time() - t0, .3)

    def test_max_rate(self):
        t0 = time.time()
        stream = self.make_one(max_rate=10)
        for n in range(4):
            source_frame = DummyVideoFrame()
            self.send_frame(source_frame)
            frame = next(stream)
            self.assertEqual(frame, source_frame)
        self.assertAlmostEqual(time.time(), t0 + 0.3, delta=0.05)

    def test_next_frame_timeout(self):
        stream = self.make_one()
        self.send_frame()
        frame1 = next(stream)
        self.assertIsNot(frame1, None)
        frame2 = next(stream)
        self.assertIs(frame2, None)

    def test_stop_iteration_if_closed(self):
        stream = self.make_one()
        stream.close()
        with self.assertRaises(StopIteration):
            next(stream)

class TestWebcamVideoStream(unittest.TestCase, WebcamStreamTests):
    default_path = 'stream'

    @property
    def stream_class(self):
        from puppyserv.webcam import WebcamVideoStream
        return WebcamVideoStream

    def test_bad_boundary(self):
        stream = self.make_one('bad_boundary')
        self.send_frame()
        self.assertIs(next(stream), None)

    def test_non_image_in_stream(self):
        stream = self.make_one()
        self.send_frame(DummyVideoFrame(content_type='text/plain'))
        self.assertIs(next(stream), None)

    def test_non_uniform_image_type_in_stream(self):
        stream = self.make_one()
        self.send_frame(DummyVideoFrame(content_type='image/jpeg'))
        self.assertIsNot(next(stream), None)
        self.send_frame(DummyVideoFrame(content_type='image/png'))
        self.assertIs(next(stream), None)

class TestWebcamStillStream(unittest.TestCase, WebcamStreamTests):
    default_path = 'snapshot'

    @property
    def stream_class(self):
        from puppyserv.webcam import WebcamStillStream
        return WebcamStillStream

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

class DummyVideoFrame(VideoFrame):
    counter = count(1)

    def __init__(self, content_type='image/jpeg', size=4096):
        image_data = b'IMAGE %d' % next(self.counter)
        pad = size - len(image_data)
        if pad > 0:
            image_data += b'\0' * pad
        super(DummyVideoFrame, self).__init__(content_type=content_type,
                                              image_data=image_data)

    def __eq__(self, other):
        return self.content_type == other.content_type \
               and self.image_data == other.image_data

    def __ne__(self, other):
        return not self.__eq__(other)

class DummyWebcam(object):
    @wsgify
    def __call__(self, request):
        global frame_queue
        self.frame_queue = frame_queue
        self.request = request

        view_name = request.path_info.lstrip('/') or 'index'
        view_method = getattr(self, '%s_view' % view_name, None)
        if not callable(view_method):
            return HTTPNotFound()
        return view_method()

        return method()

    def index_view(self):
        return Response('Hello World!')

    def snapshot_view(self):
        def app_iter():
            yield b''                   # flush headers
            frame = self.frame_queue.get()
            yield frame.image_data
        return Response(
            content_type='image/jpeg',
            app_iter=app_iter())

    def stream_view(self):
        boundary = self.request.params.get('boundary', b'boundary')
        frame_queue = self.frame_queue
        def app_iter():
            yield b''                   # flush headers
            while True:
                frame = frame_queue.get()
                yield b''.join([
                    b'--', boundary, b'\r\n',
                    b'Content-Type: %s\r\n' % frame.content_type,
                    b'Content-Length: %d\r\n' % len(frame.image_data),
                    b'\r\n',
                    frame.image_data, b'\r\n'])

        return Response(
            content_type='multipart/x-mixed-replace',
            content_type_params={'boundary': boundary},
            app_iter=app_iter()
            )

    def bad_boundary_view(self):
        response = self.stream_view()
        response.content_type_params = {'boundary': 'fu'}
        return response
