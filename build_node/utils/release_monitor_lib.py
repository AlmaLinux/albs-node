# -*- mode:python; coding:utf-8; -*-
# author: Eugene G. Zamriy <ezamriy@cloudlinux.com>
# created: 23.06.2015 12:17
# description: Cloud Linux releases monitoring system library functions.


import os
import logging
import shutil


def clean_dir_except_git(dir_path):
    """
    Removes directory content except .git directory.

    Parameters
    ----------
    dir_path : str
        Full path to directory to clean.
    """
    for name in os.listdir(dir_path):
        if name == '.git':
            continue
        path = os.path.join(dir_path, name)
        if os.path.islink(path):
            os.unlink(path)
        elif os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
