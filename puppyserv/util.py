# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

import time

import gevent

truthy = frozenset(('t', 'true', 'y', 'yes', 'on', '1'))

def asbool(s):
    """ Return the boolean value ``True`` if the case-lowered value of string
    input ``s`` is any of ``t``, ``true``, ``y``, ``on``, or ``1``, otherwise
    return the boolean value ``False``.  If ``s`` is the value ``None``,
    return ``False``.  If ``s`` is already one of the boolean values ``True``
    or ``False``, return it.

    """
    if s is None:
        return False
    if isinstance(s, bool):
        return s
    s = str(s).strip()
    return s.lower() in truthy

class RateLimiterBase(object):
    time = staticmethod(time.time)
    sleep = staticmethod(gevent.sleep)

    def __iter__(self):
        return self

    def __call__(self, iterable):
        while True:
            next(self)
            yield next(iterable)

class BucketRateLimiter(RateLimiterBase):
    def __init__(self, max_rate, bucket_size=None):
        if bucket_size is None:
            # by default allow pre-buffering one second
            bucket_size = max_rate
        self._max_rate = max_rate
        self.bucket_size = bucket_size
        self.reset()

    def reset(self):
        self._tokens = self.bucket_size
        self._last_t = self.time()

    @property
    def max_rate(self):
        return self._max_rate
    @max_rate.setter
    def max_rate(self, value):
        # update token count before changing rate
        self.tokens
        self._max_rate = value

    @property
    def tokens(self):
        """ The current token count.
        """
        tokens = self._tokens
        t = self.time()
        dt = max(0, t - self._last_t)
        tokens = self._tokens = min(tokens + dt * self._max_rate,
                                    self.bucket_size)
        self._last_t = t
        return tokens

    def __iter__(self):
        return self

    def next(self):
        tokens = self.tokens
        if tokens >= 1:
            self._tokens -= 1
        else:
            wait = (1 - tokens) / self.max_rate
            self.sleep(wait)
            self._last_t += wait
            self._tokens = 0

class BackoffRateLimiter(RateLimiterBase):
    def __init__(self, initial_delay, backoff=2, max_delay=300):
        self.initial_delay = initial_delay
        self.backoff = backoff
        self.max_delay = max_delay
        self.reset()

    def reset(self):
        self.wait_until = 0
        self.delay = self.initial_delay

    def next(self):
        now = self.time()
        wait_until = self.wait_until
        delay = self.delay
        if wait_until > now:
            self.sleep(wait_until - now)
            self.wait_until += delay
        else:
            self.wait_until = now + delay
        self.delay = min(delay * self.backoff, self.max_delay)

class ReadlineAdapter(object):
    """ This adapter add a .readline() method to basic file-like objects
    which need only provide a working .read() method.

    """
    def __init__(self, fp):
        self.fp = fp
        self.buf = b''

    def close(self):
        self.fp.close()
        del self.buf                    # break self

    def read(self, size=-1):
        buf = self.buf
        if not buf:
            # optimization
            return self.fp.read(size)
        if size is None or size < 0:
            self.buf = b''
            return buf + self.fp.read()
        elif size <= len(buf):
            self.buf = buf[size:]
            return buf[:size]
        else:
            self.buf = b''
            return buf + self.fp.read(size - len(buf))

    def readline(self, size=-1):
        if size is not None and size > 0:
            line, nl, self.buf = self.read(size).partition('\n')
            return line + nl

        line, nl, self.buf = self.buf.partition('\n')
        pieces = [line]
        while not nl:
            assert not self.buf
            line, nl, self.buf = self.fp.read(128).partition('\n')
            if not line:
                break
            pieces.append(line)
        pieces.append(nl)
        return b''.join(pieces)
