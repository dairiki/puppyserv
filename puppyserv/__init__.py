# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from webob.dec import wsgify

def main(global_config, **settings):
    @wsgify
    def app(req):
        return "Hello"
    return app
