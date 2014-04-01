# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

import tempfile
import unittest

import gevent
from mock import call, patch, Mock
from paste.deploy import loadapp

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

class Test_main(unittest.TestCase):
    def make_config(self, app_name='main', local_config={}):
        config_ini = tempfile.NamedTemporaryFile(suffix='.ini')
        self.addCleanup(config_ini.close)

        config_ini.writelines([
            '[app:%s]\n' % app_name,
            'use = call:puppyserv:main\n',
            ])
        for key, value in local_config.items():
            config_ini.write('%s = %s\n' % (key, value))

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
        config_uri = 'config:' + config_ini.name
        return config_uri

    @patch('puppyserv.VideoStreamApp', autospec=True)
    @patch('puppyserv.Config', autospec=True)
    def test(self, Config, VideoStreamApp):
        local_config = {'bar': 'baz'}
        config_uri = self.make_config('foo', local_config)
        app = loadapp(config_uri, name='foo')
        self.assertIs(app, VideoStreamApp.return_value)
        (settings,), _ = Config.call_args
        self.assertEqual(dict(settings), local_config)
        (config,), _ = VideoStreamApp.call_args
        self.assertIs(config, Config.return_value)

class Test_watch_config(unittest.TestCase):
    def call_it(self, config, settings, **kwargs):
        from puppyserv import _watch_config
        return _watch_config(config, settings, **kwargs)

    def test(self):
        config = Mock()
        settings = Mock(changed=False)
        gevent.spawn(self.call_it, config, settings, check_interval=0.01)
        gevent.sleep(0.05)
        self.assertFalse(settings.reload.called)
        settings.changed = True
        gevent.sleep(0.05)
        self.assertTrue(settings.reload.called)
        self.assertEqual(config.mock_calls[-1], call.update(settings))
