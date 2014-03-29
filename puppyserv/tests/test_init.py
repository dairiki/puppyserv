# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

from datetime import datetime, timedelta
from itertools import count
import tempfile
import unittest

from gevent import sleep
from mock import call, patch, Mock
from webob import Request, Response
from webob.dec import wsgify

from puppyserv.interfaces import VideoBuffer, VideoFrame

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

class Test_add_server_headers_filter(unittest.TestCase):
    def call_it(self, global_config, **settings):
        from puppyserv import add_server_headers_filter
        return add_server_headers_filter(global_config, **settings)

    def test(self):
        from puppyserv import SERVER_NAME

        @wsgify
        def app(req):
            return Response('Hi')
        middleware = self.call_it({})
        wrapped = middleware(app)

        resp = Request.blank('/').get_response(wrapped)
        self.assertEqual(resp.server, SERVER_NAME)
        self.assertAlmostEqual(resp.date.replace(tzinfo=None),
                               datetime.utcnow(),
                               delta=timedelta(seconds=2))

@patch('puppyserv.VideoStreamApp', autospec=True)
class Test_main(unittest.TestCase):
    def make_global_config(self):
        config_ini = tempfile.NamedTemporaryFile(suffix='.ini')
        self.addCleanup(config_ini.close)
        config_ini.writelines([
            '[loggers]\n',
            'keys = root\n',

            '[handlers]\n',
            'keys = default\n',

            '[formatters]\n',
            'keys = generic\n',

            '[logger_root]\n',
            'level = ERROR\n',
            'handlers = default\n',

            '[handler_default]\n',
            'class = StreamHandler\n',
            'args = (sys.stderr,)\n',
            'level = NOTSET\n',

            '[formatter_generic]\n',
            'format = %(message)s\n',
            ])
        config_ini.flush()
        return {'__file__': config_ini.name}

    def call_it(self, global_config, **settings):
        from puppyserv import main
        return main(global_config, **settings)

    def test_static_images(self, VideoStreamApp):
        global_config = self.make_global_config()
        settings = {
            'static.images': 'foo_*.jpg',
            }
        app = self.call_it(global_config, **settings)
        self.assertIs(app, VideoStreamApp.return_value)
        (buffer_factory,), config = VideoStreamApp.call_args
        self.assertIsInstance(buffer_factory(), VideoBuffer)

    def test_stream_webcam(self, VideoStreamApp):
        global_config = self.make_global_config()
        settings = {
            'webcam.stream.url': 'http://example.com/stream',
            }
        app = self.call_it(global_config, **settings)
        self.assertIs(app, VideoStreamApp.return_value)
        (buffer_factory,), config = VideoStreamApp.call_args
        self.assertIsInstance(buffer_factory(), VideoBuffer)

    def test_empty_static_images_is_the_same_as_unset(self, VideoStreamApp):
        from puppyserv.webcam import WebcamVideoStream
        global_config = self.make_global_config()
        settings = {
            'static.images': '',
            'webcam.stream.url': 'http://example.com/stream',
            'webcam.still.url': '',
            }
        app = self.call_it(global_config, **settings)
        self.assertIs(app, VideoStreamApp.return_value)
        (buffer_factory,), config = VideoStreamApp.call_args
        self.assertIsInstance(buffer_factory(), VideoBuffer)
        self.assertIsInstance(buffer_factory().source, WebcamVideoStream)

class TestVideoStreamApp(unittest.TestCase):
    def make_one(self, buffer_factory, **kwargs):
        from puppyserv import VideoStreamApp
        return VideoStreamApp(buffer_factory, **kwargs)

    def test_stream(self):
        req = Request.blank('/', accept='*/*')
        app = self.make_one(buffer_factory=DummyVideoBuffer)
        resp = app(req)
        self.assertEqual(resp.content_type, 'multipart/x-mixed-replace')
        self.assertRegexpMatches(next(resp.app_iter), r'\r\nframe 1\r\n\Z')
        self.assertRegexpMatches(next(resp.app_iter), r'\r\nframe 2\r\n\Z')

    def test_stream_empty(self):
        req = Request.blank('/', accept='*/*')
        app = self.make_one(buffer_factory=DummyVideoBuffer([]))
        resp = app(req)
        self.assertEqual(resp.content_type, 'multipart/x-mixed-replace')
        self.assertRegexpMatches(resp.body, r'^--\S+--\r\n\Z')

    def test_stream_timeout(self):
        req = Request.blank('/', accept='*/*')
        app = self.make_one(buffer_factory=DummyVideoBuffer(['frame1', None]))
        app.timeout_frame = VideoFrame(content_type="image/jpeg",
                                       image_data=b'timed out')
        resp = app(req)
        self.assertEqual(resp.content_type, 'multipart/x-mixed-replace')
        self.assertRegexpMatches(next(resp.app_iter), r'\r\nframe1\r\n\Z')
        self.assertRegexpMatches(next(resp.app_iter), r'\r\ntimed out\r\n\Z')

    def test_snapshot(self):
        req = Request.blank('/snapshot', accept='*/*')
        app = self.make_one(buffer_factory=DummyVideoBuffer)
        resp = app(req)
        self.assertEqual(resp.content_type, 'image/jpeg')
        self.assertEqual(resp.body, b'frame 1')

    def test_snapshot_empty_stream(self):
        req = Request.blank('/snapshot', accept='*/*')
        app = self.make_one(buffer_factory=DummyVideoBuffer([]))
        resp = app(req)
        self.assertEqual(resp.status_code, 504)
        self.assertRegexpMatches(req.get_response(resp).body, r'Not connected')

    def test_snapshot_timeout(self):
        req = Request.blank('/snapshot', accept='*/*')
        app = self.make_one(buffer_factory=DummyVideoBuffer([None]))
        app.timeout_frame = VideoFrame(content_type="image/jpeg",
                                       image_data=b'timed out')
        resp = app(req)
        self.assertEqual(resp.status_code, 504)
        self.assertRegexpMatches(req.get_response(resp).body, r'timed out')

    def test_not_found(self):
        req = Request.blank('/not_found', accept='*/*')
        app = self.make_one(buffer_factory=DummyVideoBuffer)
        resp = app(req)
        self.assertEqual(resp.status_code, 404)
        self.assertLess(len(resp.body), 1024)

class Test_GET_only(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from puppyserv import _GET_only
        class App(object):
            @wsgify
            @_GET_only
            def __call__(self, req):
                return Response('Hello')
        cls.test_app = App()

    def test_get(self):
        req = Request.blank('/')
        resp = self.test_app(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.body, 'Hello')

    def test_head(self):
        req = Request.blank('/', method='HEAD')
        resp = self.test_app(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.body, '')

    def test_post(self):
        req = Request.blank('/', POST={'foo': 1})
        resp = self.test_app(req)
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(set(resp.allow), set(['GET', 'HEAD']))

class TestBufferManager(unittest.TestCase):
    def make_one(self, buffer_factory, **kwargs):
        from puppyserv import BufferManager
        return BufferManager(buffer_factory, **kwargs)

    def test_n_clients(self):
        buffer_factory = Mock(name='buffer_factory', spec=())
        manager = self.make_one(buffer_factory, stop_stream_holdoff=0)

        self.assertEqual(manager.n_clients, 0)
        with manager:
            self.assertEqual(manager.n_clients, 1)
        self.assertEqual(manager.n_clients, 0)
        with manager:
            self.assertEqual(manager.n_clients, 1)
            with manager:
                self.assertEqual(manager.n_clients, 2)
            self.assertEqual(manager.n_clients, 1)
        self.assertEqual(manager.n_clients, 0)

    def test_concurrent_clients_get_same_buffer(self):
        buffer_factory = Mock(name='buffer_factory', spec=())
        manager = self.make_one(buffer_factory, stop_stream_holdoff=0)

        with manager as buf1:
            self.assertIs(buf1, buffer_factory.return_value)
            self.assertEqual(buffer_factory.mock_calls, [call()])
            with manager as buf2:
                self.assertIs(buf2, buf1)
                self.assertEqual(buffer_factory.mock_calls, [call()])
            self.assertEqual(buffer_factory.mock_calls, [call()])
        self.assertEqual(buffer_factory.mock_calls, [call(), call().close()])

    def test_nonconcurrent_clients_get_differnt_buffer(self):
        buffer_factory = Mock(name='buffer_factory', spec=())
        manager = self.make_one(buffer_factory, stop_stream_holdoff=0)

        with manager as buf1:
            self.assertIs(buf1, buffer_factory.return_value)
            self.assertEqual(buffer_factory.mock_calls, [call()])
        self.assertEqual(buffer_factory.mock_calls, [call(), call().close()])

        new_buffer = Mock(name='new streambuffer')
        buffer_factory.return_value = new_buffer
        with manager as buf2:
            self.assertIs(buf2, new_buffer)
            self.assertEqual(new_buffer.mock_calls, [])
        self.assertEqual(new_buffer.mock_calls, [call.close()])

    def test_stop_stream_holdoff(self):
        buffer_factory = Mock(name='buffer_factory', spec=())
        manager = self.make_one(buffer_factory, stop_stream_holdoff=0.1)
        with manager as buf1:
            pass
        self.assertEqual(buffer_factory.mock_calls, [call()])
        # before holdoff, this should reuse the first buffer
        with manager as buf2:
            pass
        self.assertEqual(buffer_factory.mock_calls, [call()])
        sleep(0.2)
        self.assertEqual(buffer_factory.mock_calls, [call(), call().close()])


class DummyVideoBuffer(VideoBuffer):
    def __init__(self, image_data_iter=None, frame_delay=0):
        if image_data_iter is None:
            image_data_iter = ("frame %d" % n for n in count(1))
        self.image_data_iter = iter(image_data_iter)
        self.frame_delay = frame_delay

    def __call__(self):
        return self                     # hokey - serve as own factory

    def stream(self):
        for image_data in self.image_data_iter:
            if image_data is None:
                yield None              # timeout
            else:
                yield VideoFrame(content_type='image/jpeg',
                                 image_data=image_data)
