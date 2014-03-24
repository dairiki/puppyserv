# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

import time

try:
    from gevent import sleep
except ImportError:
    from time import sleep

class RateLimiter(object):
    def __init__(self, max_rate):
        self.dt = 1.0/max_rate
        self.wait_until = None

    def __call__(self):
        now = time.time()
        wait_until = self.wait_until
        if wait_until and wait_until > now:
            sleep(wait_until - now)
            self.wait_until += self.dt
        else:
            self.wait_until = now + self.dt
