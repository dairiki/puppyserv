# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

from contextlib import contextmanager
import logging
import time

import gevent

log = logging.getLogger(__name__)

class StreamStatManager(object):
    SUMMARY_FMT = (u"connected {time_connected:.1f}s {total_frames} frames"
                   u" [{average_rate:.02f}/s]")

    STATS_FMT = (u"{stream_name}: " + SUMMARY_FMT
                 + u"; current rate {current_rate:.02f}/s")

    def __init__(self, name='Current streams', log_interval=30):
        self.name = name
        self.log_interval = log_interval
        self.streams = set()
        self.runner = None

    @contextmanager
    def __call__(self, stream_name):
        stream = StreamStats(stream_name)
        log.info("%s: stream started", stream_name)
        if not self.streams:
            self.runner = gevent.spawn(self._logger)
        self.streams.add(stream)
        try:
            yield stream
        finally:
            log.info("%s: stream terminated: %s",
                     stream_name, stream.stats(format=self.SUMMARY_FMT))
            self.streams.remove(stream)
            if not self.streams:
                self.runner.kill()

    def log_stats(self):
        if self.streams:
            stats = u"\n  ".join(stream.stats(format=self.STATS_FMT)
                                 for stream in self.streams)
            log.info(u"%s:\n  %s", self.name, stats)
        else:
            log.info("No clients")

    def _logger(self):
        while self.log_interval > 0:
            gevent.sleep(self.log_interval)
            try:
                self.log_stats()
            except:
                log.exception('log_stats failed')

class StreamStats(object):
    def __init__(self, stream_name):
        self.stream_name = stream_name
        self.n_frames = 0
        self.d_frames = 0
        self.t0 = self.t = time.time()

    def got_frame(self):
        self.n_frames += 1
        self.d_frames += 1

    def summary(self):
        t = time.time() - self.t0
        rate = self.n_frames / max(0.01, t)
        return '%.1fs %d frames [%.02f/s]' % (t, self.n_frames, rate)

    def stats(self, format=None, reset=True):
        t = time.time()
        time_connected = t - self.t0
        data = {
            'stream_name': self.stream_name,
            'time_connected': time_connected,
            'total_frames': self.n_frames,
            'average_rate': self.n_frames / max(0.01, time_connected),
            'current_rate': self.d_frames / max(0.01, (t - self.t)),
            }
        if reset:
            self.d_frames = 0
            self.t = t
        if format:
            return format.format(**data)
        return data
