# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import

from itertools import repeat
import time

try:
    from gevent import sleep
except ImportError:                     # pragma: NO COVER
    from time import sleep

class RateLimiter(object):
    def __init__(self, max_rate, backoff=1.0, max_delay=180):
        dt0 = 1.0 / max_rate
        if backoff and backoff > 1:
            def deltas():
                dt = dt0
                while True:
                    yield dt
                    dt = min(dt * backoff, max_delay)
            self.deltas = deltas
        else:
            self.deltas = lambda : repeat(dt0)

        self.max_rate = max_rate
        self.backoff = backoff
        self.max_delay = max_delay
        self.reset()

    def reset(self):
        self.dt = self.deltas()
        self.wait_until = None

    def __call__(self):
        now = time.time()
        wait_until = self.wait_until
        if wait_until and wait_until > now:
            sleep(wait_until - now)
            self.wait_until += next(self.dt)
        else:
            self.wait_until = now + next(self.dt)

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
