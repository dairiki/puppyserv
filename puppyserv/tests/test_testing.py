# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from itertools import count
import logging
import tempfile
import time
import urllib2
import unittest

from webob.dec import wsgify
from webob import Response


from puppyserv.stream import StreamTimeout

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

class TestStopableWSGIServer(unittest.TestCase):
    def create_one(self, app, **kwargs):
        from puppyserv.testing import StopableWSGIServer
        return StopableWSGIServer.create(app, **kwargs)

    def test(self):
        @wsgify
        def app(req):
            return Response('hello world')

        server = self.create_one(app)
        self.addCleanup(server.shutdown)
        server.wait()
        resp = urllib2.urlopen(server.application_url)
        self.assertEqual(resp.read(), 'hello world')
        resp.close()
