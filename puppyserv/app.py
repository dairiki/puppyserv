# -*- coding: utf-8 -*-
"""
"""
from __future__ import absolute_import, division

from functools import wraps
import logging
from pkg_resources import resource_filename

import gevent
from gevent.lock import Semaphore

from webob import Response
from webob.dec import wsgify
from webob.exc import HTTPGatewayTimeout, HTTPMethodNotAllowed, HTTPNotFound

from puppyserv import webcam
from puppyserv.greenlet import Condition
from puppyserv.stats import StreamStatManager
from puppyserv.stream import StaticFrame, StaticVideoStreamBuffer
from puppyserv.util import BucketRateLimiter

log = logging.getLogger(__name__)

EOL = b'\r\n'

def _GET_only(view_method, allow=('GET', 'HEAD')):
    @wraps(view_method)
    def wrapper(self, request):
        if request.method not in allow:
            return HTTPMethodNotAllowed(allow=allow)
        response = view_method(self, request)
        if request.method == 'HEAD':
            response.body = b''
        return response
    return wrapper


class VideoStreamApp(object):
    boundary = b'puppyserv-92af5f768c28fad8'

    def __init__(self, config):
        self.config = config
        self.buffer_manager = BufferManager(config)

    @wsgify
    def __call__(self, request):
        if request.path_info == '/':
            return self.stream(request)
        elif request.path_info == '/snapshot':
            return self.snapshot(request)
        return HTTPNotFound()

    @_GET_only
    def stream(self, request):
        return Response(
            content_type='multipart/x-mixed-replace',
            content_type_params={'boundary': self.boundary},
            cache_control='no-cache',
            app_iter = self._app_iter(request))

    @_GET_only
    def snapshot(self, request):
        with self.buffer_manager as stream:
            try:
                frame = next(stream)
            except StopIteration:
                # XXX: maybe different error?
                return HTTPGatewayTimeout('Not connected to webcam')
            if frame is None:
                return HTTPGatewayTimeout('webcam connection timed out')
        return Response(
            cache_control='no-cache',
            content_type=frame.content_type,
            body=frame.image_data)


    def _app_iter(self, request):
        config = self.config
        buffer_manager = self.buffer_manager
        frame = None

        def max_rate():
            return config.max_total_framerate / buffer_manager.n_clients

        stream_name = "> %s" % request.client_addr
        with buffer_manager as stream:
            limiter = BucketRateLimiter(max_rate=max_rate(), bucket_size=10)
            stream = limiter(stream)
            with config.stream_stat_manager(stream, stream_name) as frames:
                for frame in frames:
                    if frame is None:
                        frame = config.timeout_image
                    limiter.max_rate = max_rate()
                    yield self._part_for_frame(frame)

            yield b'--' + self.boundary + b'--' + EOL

    def _part_for_frame(self, frame):
        data = frame.image_data
        return b''.join([
            b'--', self.boundary, EOL,
            b'Content-Type: ', frame.content_type, EOL,
            b'Content-length: ', str(len(data)), EOL,
            EOL,
            data, EOL,
            ])

class Config(object):
    def __init__(self, settings):
        self.stream_stat_manager = StreamStatManager()
        self._condition = Condition()
        self.update(settings)
        self._validate()

    def __enter__(self):
        return self._condition.__enter__()

    def __exit__(self, exc_type, exc_value, tb):
        return self._condition.__exit__(exc_type, exc_value, tb)

    def listen(self, callback):
        # XXX: should only spawn one monitor greelet per thread
        condition = self._condition
        started = Semaphore(0)
        def _listen():
            with condition:
                while True:
                    started.release()
                    condition.wait()
                    callback(self)
        gevent.spawn(_listen)
        started.wait()


    def update(self, settings):
        condition = self._condition

        updated = set()
        def _set(key, value):
            if value != getattr(self, key, None):
                updated.add(key)
                log.info("Configured %s = %s", key, value)
            setattr(self, key, value)

        with condition:
            for key, dflt, type_ in self.CONFIGS:
                value = settings.get(key, dflt)
                coerce_ = getattr(self, '_coerce_%s' % type_)
                try:
                    _set(key, coerce_(value, settings))
                except Exception as ex:
                    log.error("Invalid %s: %s", key, ex)
            if updated:
                condition.notifyAll()

    def _validate(self):
        unset = set()
        for key, dflt, coerce in self.CONFIGS:
            if not hasattr(self, key):
                unset.add(key)
                log.error("%s is not set", key)
        if unset:
            raise ValueError("Unconfigured: %s" % ", ".join(unset))

    DEFAULT_TIMEOUT_IMAGE = resource_filename('puppyserv', 'timeout.jpg')

    CONFIGS = (
        ('max_total_framerate', 50.0, 'positive_float'),
        ('stop_stream_holdoff', 15.0, 'positive_float'),
        ('timeout_image', DEFAULT_TIMEOUT_IMAGE, 'image'),
        ('buffer_factory', None, 'buffer_factory'),
        )

    @staticmethod
    def _coerce_positive_float(value, settings):
        value = float(value)
        if value <= 0:
            raise ValueError("%s is not positive", value)
        return value

    @staticmethod
    def _coerce_image(value, settings):
        return StaticFrame(value)

    def _coerce_buffer_factory(self, value, settings):
        from puppyserv import SERVER_NAME
        if settings.get('static.images'):
            config = dict((k, v) for k, v in settings.items()
                          if k.startswith('static.'))
            return Factory(StaticVideoStreamBuffer.from_settings, config)
        config = dict((k, v) for k, v in settings.items()
                      if k.startswith('webcam.'))
        return Factory(webcam.stream_buffer_from_settings, config,
                       stream_stat_manager=self.stream_stat_manager,
                       user_agent=SERVER_NAME)

class Factory(object):
    """ This is like functools.partial, except it has equality comparison.
    """
    def __init__(self, factory, *args, **kwargs):
        self.factory = factory
        self.args = args
        self.kwargs = kwargs

    def __call__(self):
        return self.factory(*self.args, **self.kwargs)

    def __eq__(self, other):
        return type(self) is type(other) and self.__dict__ == other.__dict__

    def __ne__(self, other):
        return not self.__eq__(other)

_successful_greenlet = gevent.spawn(lambda : None)
_successful_greenlet.join()

class BufferManager(object):
    def __init__(self, config):
        with config:
            self.buffer_factory = config.buffer_factory
            self.stop_stream_holdoff = config.stop_stream_holdoff
            config.listen(self._config_changed)
        self._n_clients = 0
        self._buffer = None
        self._stopper = _successful_greenlet

    @property
    def n_clients(self):
        return self._n_clients

    # XXX: These would need a mutex if they were to be called from more
    # than one thread, but since we're geventing, we don't need it.
    def __enter__(self):
        if self._n_clients == 0:
            if not self._buffer:
                self._start_stream()
            else:
                assert not self._stopper.ready()
                self._stopper.kill(block=False)
        self._n_clients += 1
        log.debug("BufferManager: nclients = %d", self._n_clients)

        return self._stream()

    def _stream(self):
        buffer_ = self._buffer
        stream = buffer_.stream()
        while True:
            try:
                yield next(stream)
            except StopIteration:
                if buffer_ is not self._buffer:
                    buffer_ = self._buffer
                    stream = buffer_.stream()
                else:
                    break

    def __exit__(self, exc_type, exc_value, exc_tb):
        assert self._n_clients > 0
        self._n_clients -= 1
        if self._n_clients == 0:
            assert self._buffer is not None
            self._stop_stream(self.stop_stream_holdoff)
        log.debug("BufferManager: nclients = %d", self._n_clients)

    def _config_changed(self, config):
        self.stop_stream_holdoff = config.stop_stream_holdoff
        if self.buffer_factory != config.buffer_factory:
            self._change_buffer_factory(config.buffer_factory)

    def _change_buffer_factory(self, buffer_factory):
        log.info("Stream configuration changed.")
        self.buffer_factory = buffer_factory
        if self._buffer is not None:
            self._stop_stream(0)
            if self._n_clients > 0:
                self._start_stream()

    def _start_stream(self):
        self._buffer = self.buffer_factory()
        log.info("Started stream capture %r", self._buffer)

    def _stop_stream(self, holdoff):
        def stop_stream():
            log.info("Stopped stream capture %r", self._buffer)
            self._buffer.close()
            self._buffer = None

        self._stopper.kill(block=False)
        if holdoff > 0:
            self._stopper = gevent.spawn_later(holdoff, stop_stream)
        else:
            stop_stream()
