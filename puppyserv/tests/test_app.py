# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

from itertools import count
import tempfile
import unittest

import gevent
from mock import call, patch, Mock
from webob import Request, Response
from webob.dec import wsgify

from puppyserv.interfaces import VideoBuffer, VideoFrame

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

class TestVideoStreamApp(unittest.TestCase):
    def make_one(self, config=None, **kwargs):
        from puppyserv.app import VideoStreamApp
        if config is None:
            config = self.make_config(**kwargs)
        return VideoStreamApp(config)

    def make_config(self, **kwargs):
        from puppyserv.stats import dummy_stream_stat_manager
        attrs = {
            'buffer_factory': DummyVideoBuffer,
            'max_total_framerate': 50.0,
            'stop_stream_holdoff': 15.0,
            'stream_stat_manager': dummy_stream_stat_manager,
            'timeout_image': VideoFrame(b'timed out'),
            }
        attrs.update(kwargs)
        return DummyConfig(**attrs)

    def test_stream(self):
        req = Request.blank('/', accept='*/*')
        config = self.make_config()
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
        from puppyserv.app import _GET_only
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

class TestConfig(unittest.TestCase):
    def make_one(self, settings):
        from puppyserv.app import Config
        return Config(settings)

    def test_listen(self):
        settings = {}
        config = self.make_one({'stop_stream_holdoff': '15'})
        with config:
            callback = Mock(spec=())
            config.listen(callback)
        config.update({'max_total_framerate': '42'})
        gevent.sleep(0.01)
        self.assertEqual(callback.mock_calls, [call(config)])
        config.update({'stop_stream_holdoff': '15.0'})
        gevent.sleep(0.01)
        self.assertEqual(callback.mock_calls, [call(config), call(config)])

    def test_bad_positive_float(self):
        with self.assertRaises(ValueError):
            self.make_one({'stop_stream_holdoff': '-1'})

    def test_coerce_positive_float(self):
        config = self.make_one({})
        with self.assertRaises(ValueError):
            config._coerce_positive_float('-0.1', {})
        self.assertEqual(config._coerce_positive_float('42', {}), 42.0)

    def test_coerce_image(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg")
        tmp.write(u'data')
        tmp.flush()
        self.addCleanup(tmp.close)

        config = self.make_one({})
        frame = config._coerce_image(tmp.name, {})
        self.assertEqual(frame.image_data, u'data')

    def test_buffer_factory_static_images(self):
        from puppyserv.stream import StaticVideoStreamBuffer
        settings = {'static.images': 'foo_*.jpg'}
        config = self.make_one(settings)
        self.assertEqual(config.buffer_factory.factory,
                         StaticVideoStreamBuffer.from_settings)
        self.assertEqual(config.buffer_factory.args, (settings,))

    def test_buffer_factory_stream_webcam(self):
        from puppyserv import webcam
        settings = {'webcam.foo': 'bar'}
        config = self.make_one(settings)
        self.assertEqual(config.buffer_factory.factory,
                         webcam.stream_buffer_from_settings)
        self.assertEqual(config.buffer_factory.args, (settings,))

    def test_buffer_factory_empty_static_images_is_the_same_as_unset(self):
        from puppyserv import webcam
        settings = {'static.images': ''}
        config = self.make_one(settings)
        self.assertEqual(config.buffer_factory.factory,
                         webcam.stream_buffer_from_settings)
        self.assertEqual(config.buffer_factory.args, ({},))

class TestFactory(unittest.TestCase):
    def make_one(self, factory, *args, **kwargs):
        from puppyserv.app import Factory
        return Factory(factory, *args, **kwargs)

    def test_call(self):
        f = Mock(spec=())
        factory = self.make_one(f, 1, b=2)
        self.assertIs(factory(), f.return_value)
        self.assertEqual(f.mock_calls, [call(1, b=2)])

class TestBufferManager(unittest.TestCase):
    def make_one(self, config=None, **kwargs):
        from puppyserv.app import BufferManager
        if config is None:
            config = self.make_config(**kwargs)
        return BufferManager(config)

    def make_config(self, **kwargs):
        from puppyserv.stats import dummy_stream_stat_manager
        attrs = {
            'buffer_factory': Mock(name='buffer_factory', spec=()),
            'max_total_framerate': 50.0,
            'stop_stream_holdoff': 15.0,
            'stream_stat_manager': dummy_stream_stat_manager,
            'timeout_image': VideoFrame(b'timed out'),
            }
        attrs.update(kwargs)
        return DummyConfig(**attrs)

    def test_n_clients(self):
        manager = self.make_one(stop_stream_holdoff=0)

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
        manager = self.make_one(buffer_factory=buffer_factory,
                                stop_stream_holdoff=0)

        with manager as stream1:
            self.assertIs(manager._buffer, buffer_factory.return_value)
            self.assertEqual(buffer_factory.mock_calls, [call()])
            with manager as stream2:
                self.assertIs(manager._buffer, buffer_factory.return_value)
                self.assertEqual(buffer_factory.mock_calls, [call()])
            self.assertEqual(buffer_factory.mock_calls, [call()])
        self.assertEqual(buffer_factory.mock_calls, [call(), call().close()])

    def test_nonconcurrent_clients_get_different_buffer(self):
        buffer_factory = Mock(name='buffer_factory', spec=())
        manager = self.make_one(buffer_factory=buffer_factory,
                                stop_stream_holdoff=0)
        with manager as stream1:
            self.assertIs(manager._buffer, buffer_factory.return_value)
            self.assertEqual(buffer_factory.mock_calls, [call()])
        self.assertEqual(buffer_factory.mock_calls, [call(), call().close()])

        new_buffer = Mock(name='new streambuffer')
        buffer_factory.return_value = new_buffer
        with manager as stream2:
            self.assertIs(manager._buffer, new_buffer)
            self.assertEqual(new_buffer.mock_calls, [])
        self.assertEqual(new_buffer.mock_calls, [call.close()])

    def test_stop_stream_holdoff(self):
        buffer_factory = Mock(name='buffer_factory', spec=())
        manager = self.make_one(buffer_factory=buffer_factory,
                                stop_stream_holdoff=0.1)
        with manager as buf1:
            pass
        self.assertEqual(buffer_factory.mock_calls, [call()])
        # before holdoff, this should reuse the first buffer
        with manager as buf2:
            pass
        self.assertEqual(buffer_factory.mock_calls, [call()])
        gevent.sleep(0.2)
        self.assertEqual(buffer_factory.mock_calls, [call(), call().close()])

    def test_stream(self):
        manager = self.make_one(buffer_factory=DummyVideoBuffer([b'f1']),
                                stop_stream_holdoff=0)
        with manager as stream:
            frame = next(stream)
            self.assertEqual(frame.image_data, b'f1')
            with self.assertRaises(StopIteration):
                next(stream)

    def test_change_stream_while_closed(self):
        manager = self.make_one(buffer_factory=DummyVideoBuffer([b'f1']),
                                stop_stream_holdoff=0)
        with manager as stream:
            frame = next(stream)
            self.assertEqual(frame.image_data, b'f1')
            manager._change_buffer_factory(DummyVideoBuffer([b'f2']))
        with manager as stream:
            frame = next(stream)
            self.assertEqual(frame.image_data, b'f2')
            with self.assertRaises(StopIteration):
                next(stream)

    def test_change_stream_while_open(self):
        manager = self.make_one(buffer_factory=DummyVideoBuffer([b'f1']),
                                stop_stream_holdoff=0)
        with manager as stream:
            frame = next(stream)
            self.assertEqual(frame.image_data, b'f1')
            manager._change_buffer_factory(DummyVideoBuffer([b'f2']))
            frame = next(stream)
            self.assertEqual(frame.image_data, b'f2')
            with self.assertRaises(StopIteration):
                next(stream)

    def test_change_stream_while_in_holdoff(self):
        manager = self.make_one(buffer_factory=DummyVideoBuffer([b'f1']),
                                stop_stream_holdoff=0.1)
        with manager as stream:
            frame = next(stream)
            self.assertEqual(frame.image_data, b'f1')
        manager._change_buffer_factory(DummyVideoBuffer([b'f2']))
        with manager as stream:
            frame = next(stream)
            self.assertEqual(frame.image_data, b'f2')
            with self.assertRaises(StopIteration):
                next(stream)

    def test_config_changed(self):
        orig = Mock()
        new = Mock()
        manager = self.make_one(buffer_factory=orig,
                                stop_stream_holdoff=0)
        with patch.object(manager, '_change_buffer_factory') \
                 as change_buffer_factory:
            manager._config_changed(Mock(buffer_factory=orig,
                                         stop_stream_holdoff=1))
            self.assertEqual(change_buffer_factory.mock_calls, [])
            self.assertEqual(manager.stop_stream_holdoff, 1)
            manager._config_changed(Mock(buffer_factory=new))
            self.assertEqual(change_buffer_factory.mock_calls, [call(new)])

class DummyConfig(object):
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_value, tb):
        pass

    def listen(self, callback):
        pass

class DummyVideoBuffer(VideoBuffer):
    def __init__(self, image_data_iter=None, frame_delay=0):
        if image_data_iter is None:
            image_data_iter = ("frame %d" % n for n in count(1))
        self.image_data_iter = iter(image_data_iter)
        self.frame_delay = frame_delay
        self.closed = False

    def __call__(self):
        return self                     # hokey - serve as own factory

    def close(self):
        self.closed = True

    def stream(self):
        for image_data in self.image_data_iter:
            if image_data is None:
                yield None              # timeout
            else:
                yield VideoFrame(content_type='image/jpeg',
                                 image_data=image_data)
