# -*- mode:python; coding:utf-8; -*-
# author: Darya Malyavkina  <dmalyavkina@cloudlinux.com>
# created: 2018-04-05

import logging

from traceback import format_exc
from raven import Client


__all__ = ['Sentry']


class Sentry(object):
    """
    Sentry Integration to the new Build System.

    Sentry allows to get notifications via email of an existing workflow when
    errors occur or resurface.
    """

    def __init__(self, sentry_dsn=None, logger='sentry_logger'):
        """
        Sentry initialization.

        Parameters
        ----------
        sentry_dsn : str
            client key for send data to Sentry
        logger : str
            name of logger
        """
        self.logger = logging.getLogger(logger)
        self.sentry_dsn = sentry_dsn
        self.client = Client(self.sentry_dsn)

    def capture_exception(self, error, message='', logger=False):
        """
        Parameters
        ----------
        error : Exception
            Exception for processing
        message : str
            Error message
        logger : bool
            Add addition logging
        """
        if logger:
            self.logger.error('{msg}\n{er}:\n{trace}'.format(
                msg=message, er=str(error.message), trace=format_exc()))
        self.client.captureException()
