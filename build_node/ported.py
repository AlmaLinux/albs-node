# -*- mode:python; coding:utf-8; -*-
# author: Ruslan Pisarev <rpisarev@cloudlinux.com>
# created: 2020-03-23

"""
CloudLinux Build System functions these were ported from yum, rpm, cla
"""


__all__ = ['cmp', 're_primary_filename', 're_primary_dirname',
           'to_unicode', 'unique', 'return_file_entries']


def cmp(a, b):
    return (a > b) - (a < b)


def re_primary_filename(filename):
    """ Tests if a filename string, can be matched against just primary.
        Note that this can produce false negatives (Eg. /b?n/zsh) but not false
        positives (because the former is a perf hit, and the later is a
        failure). Note that this is a superset of re_primary_dirname(). """
    if re_primary_dirname(filename):
        return True
    if filename == '/usr/lib/sendmail':
        return True
    return False


def re_primary_dirname(dirname):
    """ Tests if a dirname string, can be matched against just primary. Note
        that this is a subset of re_primary_filename(). """
    if 'bin/' in dirname:
        return True
    if dirname.startswith('/etc/'):
        return True
    return False


def unique(seq):
    """
    Parameters
    ----------
    seq : list or tuple or str
        some sequence be make it new one with uniq elements

    Returns
    -------
    list
        list of uniq elements in sequence `seq`
    """
    try:
        unq = set(seq)
        return list(unq)
    except TypeError:
        pass
    unq = []
    for x in seq:
        if x not in unq:
            unq.append(x)
    return unq


def return_file_entries(pkg_files, ftype):
    """
    Parameters
    ----------
    pkg_files : dict
        The structure of files of package.
        See the function `get_files_from_package()`
    ftype: str
        type of file entries. Can be `dir`, `file` & `ghost`

    Returns
    -------
    list
        The list of data from package by type `ftype`

    """
    if pkg_files:
        return pkg_files.get(ftype, [])
    return []


def to_unicode(s):
    """
    Converts string to unicode.

    s :  str, bytes
        Data to convert to unicode string
    Returns
    -------
    str
        Converted string.
    """
    if isinstance(s, bytes):
        return s.decode('utf8')
    elif isinstance(s, str):
        return s
    else:
        return str(s)
