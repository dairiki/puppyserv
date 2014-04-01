# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

from datetime import datetime, timedelta
import unittest

from webob import Request, Response
from webob.dec import wsgify

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

class Test_add_server_headers_filter(unittest.TestCase):
    def call_it(self, global_config, **settings):
        from puppyserv.paste import add_server_headers_filter
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
