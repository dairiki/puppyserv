
# -*- coding: utf-8 -*-
import os
import sys

from setuptools import setup, find_packages
from setuptools.command.test import test as TestCommand

VERSION = '0.1'

here = os.path.abspath(os.path.dirname(__file__))
README = open(os.path.join(here, 'README.rst')).read()
CHANGES = open(os.path.join(here, 'CHANGES.rst')).read()

requires = [
    'webob',
    'gevent',
    'PasteDeploy',
    'six',
    ]

tests_require = [
    'mock',
    'paste',
    'pytest',
    'pytest-cov',
    ]

if sys.version_info[:2] < (2,7):
    tests_require.append('unittest2')

class PyTest(TestCommand):
    def finalize_options(self):
        TestCommand.finalize_options(self)
        self.test_args = ['puppyserv/tests']
        self.test_suite = True

    def run_tests(self):
        #import here, cause outside the eggs aren't loaded
        import pytest
        errno = pytest.main(self.test_args)
        sys.exit(errno)

setup(name='puppyserv',
      version=VERSION,
      description=('Beyond the P(erl)ALE — '
                   'python reimplementation DiscNW league engine'),
      long_description=README + '\n\n' + CHANGES,
      classifiers=[
          "Programming Language :: Python",
          "Framework :: Pyramid",
          "Intended Audience :: Information Technology",
          "Topic :: Multimedia :: Video",
          "Topic :: Internet :: WWW/HTTP",
          "Topic :: Internet :: WWW/HTTP :: WSGI :: Application",
          "Topic :: Multimedia :: Video",
        ],
      author='Jeff Dairiki',
      author_email='dairiki@dairiki.org',
      url='',
      keywords='webcam broadcasting',

      packages=find_packages(),

      install_requires=requires,

      include_package_data=True,
      zip_safe=False,

      entry_points={
          'paste.app_factory': [
              'main = puppyserv:main',
              ],
          'paste.filter_factory': [
              'add_server_headers = puppyserv:add_server_headers_filter',
              ],
          'console_scripts': [
              #'btp_filestore_gc = btp.scripts.filestore_gc:main',
              ],
          },

      extras_require={
          'test': tests_require,
          },
      tests_require=requires + tests_require,
      cmdclass={'test': PyTest},
      )
