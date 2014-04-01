# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from itertools import count
import os
import tempfile
import unittest

from mock import patch

from paste.deploy import loadapp

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

class TestReloadableSettings(unittest.TestCase):
    def make_one(self, config_file, *args, **kwargs):
        from puppyserv.settings import ReloadableSettings
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
        from puppyserv.settings import ReloadableSettings

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
    from puppyserv.settings import ReloadableSettings
    return ReloadableSettings.from_config(global_config, **settings)
