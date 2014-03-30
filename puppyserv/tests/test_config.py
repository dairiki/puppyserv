# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from itertools import count
import os
import tempfile
import unittest

from mock import call, patch, Mock

from paste.deploy import loadapp

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

class TestConfig(unittest.TestCase):
    def make_one(self, settings):
        from puppyserv.config import Config
        return Config(settings)

    def test_listen(self):
        settings = {}
        config = self.make_one({'stop_stream_holdoff': '15'})
        callback = Mock(spec=())
        config.listen('stop_stream_holdoff', callback)
        config.update({'max_total_framerate': '42'})
        self.assertFalse(callback.called)
        config.update({'stop_stream_holdoff': '15.0'})
        self.assertFalse(callback.called)
        config.update({'stop_stream_holdoff': '12'})
        self.assertTrue(callback.called)

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
        from puppyserv.config import Factory
        return Factory(factory, *args, **kwargs)

    def test_call(self):
        f = Mock(spec=())
        factory = self.make_one(f, 1, b=2)
        self.assertIs(factory(), f.return_value)
        self.assertEqual(f.mock_calls, [call(1, b=2)])

class TestReloadableSettings(unittest.TestCase):
    def make_one(self, config_file, *args, **kwargs):
        from puppyserv.config import ReloadableSettings
        return ReloadableSettings(config_file, *args, **kwargs)

    def make_ini_file(self):
        tmp = tempfile.NamedTemporaryFile(suffix='.ini')
        self.addCleanup(tmp.close)
        tmp.writelines([
            '[app:main]\n',
            'use = call:puppyserv:main\n',
            ])
        tmp.flush()
        return tmp

    def test_change(self):
        ini_file = self.make_ini_file()
        settings = self.make_one(ini_file.name)
        self.assertFalse(settings.changed)
        ini_file.write('a')
        ini_file.flush()
        self.assertTrue(settings.changed)

    def test_reload(self):
        ini_file = self.make_ini_file()
        settings = self.make_one(ini_file.name)
        self.assertEqual(dict(settings), {})
        ini_file.write('a = b\n')
        ini_file.flush()
        settings.reload()
        self.assertEqual(dict(settings), {'a': 'b'})

    def test_reload_fails_if_config_file_chages(self):
        ini_file = self.make_ini_file()
        settings = self.make_one(ini_file.name)
        counter = count()
        def _hash_file():
            return next(counter)
        with patch.object(settings, '_hash_file', _hash_file):
            with self.assertRaises(RuntimeError):
                settings.reload()

    def test_from_config(self):
        from puppyserv.config import ReloadableSettings

        ini = tempfile.NamedTemporaryFile(suffix='.ini')
        self.addCleanup(ini.close)

        ini.writelines([
            '[app:foo]\n'
            'use = call:%s:_settings_from_config\n' % __name__,
            ])
        ini.flush()
        config_uri = 'config:' + ini.name
        settings = loadapp(config_uri, 'foo')
        self.assertEqual(settings.config_file, ini.name)
        self.assertEqual(settings.name, 'foo')
        self.assertEqual(settings.relative_to, os.path.dirname(ini.name))

def _settings_from_config(global_config, **settings):
    from puppyserv.config import ReloadableSettings
    return ReloadableSettings.from_config(global_config, **settings)
