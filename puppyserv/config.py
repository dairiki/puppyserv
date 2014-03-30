# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

import inspect
import logging
import os

from pkg_resources import resource_filename
from six import string_types

from gevent import sleep
from paste.deploy import appconfig

import puppyserv
from puppyserv import webcam
from puppyserv.stats import StreamStatManager
from puppyserv.stream import StaticFrame, StaticVideoStreamBuffer

log = logging.getLogger(__name__)

class Config(object):
    def __init__(self, settings):
        self.stream_stat_manager = StreamStatManager()
        self._listeners = []
        self.update(settings)
        self._validate()

    def listen(self, keys, callback):
        listener = _ConfigListener(keys, callback)
        self._listeners.append(listener)
        return listener

    def update(self, settings):
        updated = set()

        def _set(key, value):
            if value != getattr(self, key, None):
                updated.add(key)
                log.info("Configured %s = %s", key, value)
            setattr(self, key, value)

        for key, dflt, type_ in self.CONFIGS:
            value = settings.get(key, dflt)
            coerce_ = getattr(self, '_coerce_%s' % type_)
            try:
                _set(key, coerce_(value, settings))
            except Exception as ex:
                log.error("Invalid %s: %s", key, ex)

        for listener in self._listeners:
            listener(self, updated)

    def _validate(self):
        unset = set()
        for key, dflt, coerce in self.CONFIGS:
            if not hasattr(self, key):
                unset.add(key)
                log.error("%s is not set", key)
        if unset:
            raise ValueError("Unconfigured: %s" % ", ".join(unset))

    DEFAULT_TIMEOUT_IMAGE = resource_filename('puppyserv', 'timeout.jpg')

    CONFIGS = (
        ('max_total_framerate', 50.0, 'positive_float'),
        ('stop_stream_holdoff', 15.0, 'positive_float'),
        ('timeout_image', DEFAULT_TIMEOUT_IMAGE, 'image'),
        ('buffer_factory', None, 'buffer_factory'),
        )

    @staticmethod
    def _coerce_positive_float(value, settings):
        value = float(value)
        if value <= 0:
            raise ValueError("%s is not positive", value)
        return value

    @staticmethod
    def _coerce_image(value, settings):
        return StaticFrame(value)

    def _coerce_buffer_factory(self, value, settings):
        if settings.get('static.images'):
            config = dict((k, v) for k, v in settings.items()
                          if k.startswith('static.'))
            return Factory(StaticVideoStreamBuffer.from_settings, config)
        config = dict((k, v) for k, v in settings.items()
                      if k.startswith('webcam.'))
        return Factory(webcam.stream_buffer_from_settings, config,
                       stream_stat_manager=self.stream_stat_manager,
                       user_agent=puppyserv.SERVER_NAME)

class _ConfigListener(object):
    def __init__(self, keys, callback):
        if isinstance(keys, string_types):
            keys = [keys]
        self.keys = frozenset(keys)
        self.callback = callback

    def __call__(self, config, updated):
        if not self.keys.isdisjoint(updated):
            self.callback(config)

class Factory(object):
    """ This is like functools.partial, except it has equality comparison.
    """
    def __init__(self, factory, *args, **kwargs):
        self.factory = factory
        self.args = args
        self.kwargs = kwargs

    def __call__(self):
        return self.factory(*self.args, **self.kwargs)

    def __eq__(self, other):
        return type(self) is type(other) and self.__dict__ == other.__dict__

    def __ne__(self, other):
        return not self.__eq__(other)

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
                sleep(0.1)
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
