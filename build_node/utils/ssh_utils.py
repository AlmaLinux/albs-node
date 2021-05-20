# -*- mode:python; coding:utf-8; -*-
# author: Vasiliy Kleschov <vkleschov@cloudlinux.com>
# created: 16.03.2018 09:47
# description: Library to work with SSH connections

from threading import Timer
from contextlib import contextmanager

__all__ = ['close_connection_on_timeout']


@contextmanager
def close_connection_on_timeout(paramiko_ssh_client):
    timer = Timer(10, paramiko_ssh_client.close)
    timer.start()
    yield
    timer.cancel()
