# -*- coding: utf-8 -*-
""" WSGI deployment helpers
"""
from __future__ import absolute_import

from datetime import datetime

from webob.dec import wsgify

def add_server_headers_filter(global_config, **settings):
    """ Middleware to add headers normally added by real http server.

    Used when uwsgi is serving HTTP request directly.

    """
    from puppyserv import SERVER_NAME

    @wsgify.middleware
    def filter(request, app):
        response = request.get_response(app)
        response.server = SERVER_NAME
        response.date=datetime.utcnow()
        return response
    return filter
