=========
Puppyserv
=========

A Simple MJPEG webcam rebroadcaster/proxy
=========================================

The idea is to make one streaming connection to a webcam, and then
distribute that stream to many clients.

Sorry, these notes are lacking. Bug me if you have question.


Requirements
~~~~~~~~~~~~

This should run using python 2.6 or 2.7.  Since we use gevent_, python 3 is
not currently supported.

Installation
~~~~~~~~~~~~

We use buildout_ for installation/deployment.
If you’re lucky, the following should get you a basic working installation::

    python bootstrap.py
    bin/buildout

Run the unit tests::

    bin/py.test puppyserv

To run a test-configured version of the server (which streams a static test
video loop), in a terminal run::

    bin/uwsgi etc/puppyserv.ini

Then browse to http://localhost:8000/.  If the gods are with you, you will see a nice video loop.

Real Configuration
------------------

The real deployment configuration was in a ``production.cfg`` buildout
configuration file, which included and specialized ``buildout.cfg``.
``Production.cfg`` is not checked into the git repository since it
contains sensitive information (e.g. the URL of the source webcam).
I’m positive that I backed up the production configuration before I
deleted the production VPS server, but at the moment I am unable
to find it :-/

**UPDATE**: I did find an older version of the production configuration file.
With that and memory, I’ve generated an *untested* reconstruction of what
the real ``production.cfg`` looked like.  This is included here (with
sensitive data elided) in ``production.cfg.example``.

Author
------

`Jeff Dairiki`_.

.. _Jeff Dairiki: mailto:dairiki@dairiki.org

.. _gevent: http://www.gevent.org/
.. _buildout: https://pypi.python.org/pypi/zc.buildout/
