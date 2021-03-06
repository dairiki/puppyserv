# -*- coding: utf-8 -*-
""" Gevent support pieces
"""
from __future__ import absolute_import

from weakref import WeakKeyDictionary

import gevent
from gevent.monkey import get_original

current_thread = get_original('threading', 'current_thread')
RLock = get_original('threading', 'RLock')

class Condition(object):
    """ A gevent-aware version of threading.Condition.

    This allows greenlets to wait on notifications generated by bona fide
    threads.

    (Calling threading.Condition.wait from a greenlet will block all greenlets
    in the thread.)

    This is currently only a partial re-implementation which supports only
    ``notifyAll``.  It does not currently support ``notify``.

    """
    def __init__(self, lock=None):
        if lock is None:
            lock = RLock()              # a real threading.Lock
        self.lock = lock
        self.waiters_by_thread = WeakKeyDictionary()

        self.acquire = lock.acquire
        self.release = lock.release


    def __enter__(self):
        return self.lock.__enter__()

    def __exit__(self, exc_type, exc_value, tb):
        return self.lock.__exit__(exc_type, exc_value, tb)

    def wait(self, timeout=None):
        # FIXME: check that we own and have locked the lock?
        try:
            async, waiters = self.waiters_by_thread[current_thread()]
        except KeyError:
            # How we communicate from other threads to the gevent thread
            async = gevent.get_hub().loop.async()
            async.start(self._notify_all)
            waiters = []
            self.waiters_by_thread[current_thread()] = async, waiters

        waiter = gevent.lock.Semaphore(0)
        waiters.append(waiter)
        self.release()
        try:
            if not waiter.wait(timeout):
                waiters.remove(waiter)
        finally:
            self.acquire()

    def notifyAll(self):
        # FIXME: check that we own and have locked the lock?
        for async, waiters in self.waiters_by_thread.values():
            async.send()

    def _notify_all(self):
        with self.lock:
            async, waiters = self.waiters_by_thread[current_thread()]
        for waiter in waiters:
            waiter.release()
        waiters[:] = []
