# -*- mode:python; coding:utf-8; -*-
# author: Vasiliy Kleschov <vkleschov@cloudlinux.com>
# created: 2018-01-09

import errno
import os
import struct
import threading


def get_current_thread_ident():
    """
    Returns a current thread unique identifier based on a current process PID
    and the thread name.

    Returns
    -------
    str
        Byte string identifier.
    """
    return struct.pack(b'i20p', os.getpid(),
                       threading.current_thread().name.encode('utf-8'))


def is_pid_exists(pid):
    """
    Checks if a process with specified pid exists.

    Parameters
    ----------
    pid : int
        Process pid.

    Returns
    -------
    bool
        True if a process exists, False otherwise.

    Raises
    ------
    ValueError
        If the specified pid is invalid.
    """
    if pid < 1:
        raise ValueError('invalid pid {0}'.format(pid))
    try:
        os.kill(pid, 0)
    except OSError as e:
        if e.errno == errno.EPERM:
            # process is exist, but we don't have access to it
            return True
        elif e.errno == errno.ESRCH:
            return False
        raise e
    return True
