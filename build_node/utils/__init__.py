# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-10-18

"""
Cloud Linux Build System utility functions.
"""

import signal


class ExecLimit(object):

    def __init__(self, seconds=60,
                 raise_message='Execution exceed time limit'):
        """Context manager for limit some execution

        Args:
            seconds (int, optional): Set execution limit in seconds
            raise_message (str, optional): Put message for TimeoutException
                exception
        """
        self._seconds = seconds
        self._raise_message = raise_message

    def handle_timeout(self, signum, frame):
        raise TimeoutException(self._raise_message)

    def __enter__(self):
        signal.signal(signal.SIGALRM, self.handle_timeout)
        signal.alarm(self._seconds)

    def __exit__(self, exc_type, value, traceback):
        signal.alarm(0)


class TimeoutException(Exception):
    pass
