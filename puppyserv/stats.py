# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

from contextlib import contextmanager
import logging
from operator import attrgetter
import time

import gevent
import gevent.monkey
Lock, = gevent.monkey.get_original('threading', ['Lock'])

from webhelpers.number import format_byte_size

log = logging.getLogger(__name__)

@contextmanager
def dummy_stream_stat_manager(stream, stream_name=None):
    yield stream

class StreamStatManager(object):
    SUMMARY_FMT = (
        u"{time_connected:.1f}s,"
        u" {frames_total} f {frames_avg_rate:.02f}/s;"
        u" {bytes_total} {bytes_avg_rate}/s")


    STATS_FMT = (
        u"{stream_name:15s}: {time_connected:6.1f}s,"
        u" {frames_total:6d} f {frames_avg_rate:4.02f}/s"
        u" [{frames_cur_rate:4.02f}/s],"
        u" {bytes_total} {bytes_avg_rate}/s [{bytes_cur_rate}/s]")


    def __init__(self, name='Current streams', log_interval=30):
        self.name = name
        self.log_interval = log_interval
        self.streams = set()
        self.runner = gevent.spawn(self._logger)
        self.mutex = Lock()

    @contextmanager
    def __call__(self, stream, stream_name=None):
        if stream_name is None:
            stream_name = repr(stream)
        monitored = StatMonitoredStream(stream, stream_name)
        log.info("%s: stream started", stream_name)
        with self.mutex:
            self.streams.add(monitored)

        try:
            yield monitored
        finally:
            log.info("%s: stream terminated: %s",
                     stream_name, monitored.stats(format=self.SUMMARY_FMT))
            with self.mutex:
                self.streams.remove(monitored)

    def log_stats(self):
        with self.mutex:
            streams = sorted(self.streams, key=attrgetter('stream_name'))
        if streams:
            stats = u"\n ".join(stream.stats(format=self.STATS_FMT)
                                 for stream in streams)
            log.info(u"%s:\n %s", self.name, stats)
        else:
            log.debug(u"%s: No streams", self.name)

    def _logger(self):
        while self.log_interval > 0:
            gevent.sleep(self.log_interval)
            try:
                self.log_stats()
            except:
                log.exception('log_stats failed')

class StatMonitoredStream(object):
    time = staticmethod(time.time)

    def __init__(self, stream, stream_name):
        self.stream = iter(stream)
        self.stream_name = stream_name
        self.n_frames = 0
        self.n_bytes = 0
        self.d_frames = 0
        self.d_bytes = 0
        self.t0 = self.t = self.time()

    def __iter__(self):
        return self

    def next(self):
        frame = next(self.stream)
        if frame is not None:
            self.d_frames += 1
            self.d_bytes += len(frame.image_data)
        return frame

    def stats(self, format=None, reset=True):
        t = self.time()
        time_connected = t - self.t0
        dt = t - self.t
        stream_name = self.stream_name
        frames_total = self.n_frames + self.d_frames
        frames_avg_rate = frames_total / max(0.01, time_connected)
        frames_cur_rate = self.d_frames / max(0.01, dt)
        bytes_total_raw = self.n_bytes + self.d_bytes
        bytes_avg_rate_raw = bytes_total_raw / max(0.01, time_connected)
        bytes_cur_rate_raw= self.d_bytes / max(0.01, dt)
        bytes_total = format_byte_size(bytes_total_raw)
        bytes_avg_rate = format_byte_size(bytes_avg_rate_raw)
        bytes_cur_rate = format_byte_size(bytes_cur_rate_raw)

        if reset:
            self.reset()

        if format is None:
            return locals()
        return format.format(**locals())

    def reset(self, t=None):
        if t is None:
            t = self.time()
        self.t = t
        self.n_frames += self.d_frames
        self.n_bytes += self.d_bytes
        self.d_frames = 0
        self.d_bytes = 0

def format_byte_size(nbytes):
    value = nbytes
    if round(value / 1024.0, 2) < 1.0:
        return u"%4d B" % nbytes
    for units in ['kB', 'MB', 'GB', 'TB', 'PB']:
        value /= 1024.0
        if round(value / 1024.0, 2) < 1.0:
            break

    if round(value, 2) < 10:
        fmt = u"%4.2f%s"
    elif round(value, 1) < 100:
        fmt = u"%4.1f%s"
    else:
        fmt = u"%4.0f%s"
    return fmt % (value, units)
