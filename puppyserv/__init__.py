# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

import logging
import logging.config
from pkg_resources import get_distribution

import gevent

from puppyserv.app import Config, VideoStreamApp
from puppyserv.settings import ReloadableSettings

log = logging.getLogger(__name__)

_dist = get_distribution(__name__)
SERVER_NAME = "%s/%s (<dairiki@dairiki.org>)" % (
    _dist.project_name, _dist.version)

def main(global_config, **local_config):
    logging.config.fileConfig(global_config['__file__'], global_config)
    settings = ReloadableSettings.from_config(global_config, **local_config)
    config = Config(settings)

    gevent.spawn(_watch_config, config, settings)

    log.info("App starting!")
    return VideoStreamApp(config)

def _watch_config(config, settings, check_interval=5):
    while True:
        gevent.sleep(check_interval)
        if settings.changed:
            settings.reload()
            config.update(settings)
