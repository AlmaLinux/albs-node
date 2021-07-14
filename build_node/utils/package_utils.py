# -*- mode:python; coding:utf-8; -*-
# author: Vyacheslav Potoropin <vpotoropin@cloudlinux.com>
# created: 23.01.20 12:39

import re


__all__ = ['is_debug_package']


def is_debug_package(file_name, package_type):
    """
    Checks if package contains debug information.

    Parameters
    ----------
    file_name : str
        Package filename.
    package_type : str
        One of the following: dsc, srpm, deb, rpm

    Returns
    -------
    bool
        True if package contains debug info, False otherwise.
    """
    if package_type in ('dsc', 'srpm'):
        return False
    elif package_type == 'deb':
        return '-dbg' in file_name
    return bool(re.search(r'-debug(info|source)', file_name))
