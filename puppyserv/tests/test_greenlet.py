# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

from operator import methodcaller
import thread
from threading import Thread, Timer
import time
import unittest

import gevent
from gevent.monkey import get_original

if not hasattr(unittest.TestCase, 'addCleanup'):
    import unittest2 as unittest

class TestCondition(unittest.TestCase):
    def setUp(self):
        self.assertNotMonkeyPatched()

    def assertNotMonkeyPatched(self):
        self.assertIs(thread.start_new_thread,
                      get_original('thread', 'start_new_thread'))

    def make_one(self, *args):
        from puppyserv.greenlet import Condition
        return Condition(*args)

    def send_notify(self, cond):
        with cond:
            cond.notifyAll()

    def test_wait_for_other_thread(self):
        cond = self.make_one()
        e = _Event(cond)
        Timer(0.05, e.set).start()
        self.assertTrue(e.wait(1))

    def test_wait_timeout(self):
        cond = self.make_one()
        t0 = time.time()
        with cond:
            cond.wait(0.1)
        self.assertAlmostEqual(time.time() - t0, 0.1, delta=0.01)

    def test_wait_for_sample_thread(self):
        cond = self.make_one()
        e = _Event(cond)
        gevent.spawn_later(0.05, e.set)
        self.assertTrue(e.wait(1))

    def test_wait_in_two_other_threads(self):
        cond = self.make_one()
        e = _Event(cond)
        result = []
        def listen():
            result.append(e.wait(1))
        threads = [Thread(target=listen) for n in range(2)]
        map(methodcaller('start'), threads)
        gevent.sleep(0.05)
        e.set()
        map(methodcaller('join'), threads)
        self.assertEqual(result, [True, True])

class _Event(object):
    def __init__(self, cond):
        self.cond = cond
        self.flag = False

    def set(self):
        with self.cond:
            self.flag = True
            self.cond.notifyAll()

    def wait(self, timeout=None):
        with self.cond:
            self.flag = False
            self.cond.wait(timeout)
            return self.flag
