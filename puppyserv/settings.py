# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

import inspect
import logging
import os


import gevent
from paste.deploy import appconfig

log = logging.getLogger(__name__)

class ReloadableSettings(dict):
    def __init__(self, config_file, name=None, relative_to=None):
        config_file = os.path.abspath(config_file)
        if relative_to is None:
            relative_to = os.path.dirname(config_file)
        self.config_file = config_file
        self.name = name
        self.relative_to = relative_to
        self.reload()

    @classmethod
    def from_config(cls, global_config, **local_config):
        config = global_config.copy()
        config.update(local_config)
        config_file = config['__file__']
        relative_to = config.get('here')
        name = _glean_app_name()
        return cls(config_file, name, relative_to)

    def reload(self):
        config_uri = 'config:' + self.config_file
        for retry in range(5):
            try:
                hash_ = self._hash_file()
                conf = appconfig(config_uri, self.name, self.relative_to)
                if hash_ != self._hash_file():
                    raise RuntimeError("Config file changed")
                break
            except Exception as ex:
                gevent.sleep(0.1)
        else:
            raise ex

        self.clear()
        self.update(conf.local_conf)
        self._current_hash = hash_

    @property
    def changed(self):
        try:
            return self._current_hash != self._hash_file()
        except OSError:                 # pragma: NO COVER
            return True

    def _hash_file(self):
        st = os.stat(self.config_file)
        return st.st_mtime, st.st_size

def _glean_app_name():
    # Horrible hack
    try:
        for frame, filename, lineno, function, code_context, index \
                in inspect.getouterframes(inspect.currentframe()):
            if '/paste/deploy/loadwsgi.py' in filename \
                   and function in ('get_app', 'loadapp'):
                return frame.f_locals['name']
        else:                           # pragma: NO COVER
            raise RuntimeError("Can not deduce app name")
    finally:
        del frame
