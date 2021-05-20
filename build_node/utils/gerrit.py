# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-03-10

"""
CloudLinux Build System utility functions for working with gerrit data.
"""

import re


__all__ = ['parse_gerrit_change_url', 'parse_gerrit_ref',
           'is_short_gerrit_ref']


def parse_gerrit_change_url(change_url):
    """
    Extracts a change number and a patchset number from the specified gerrit
    change URL.

    Parameters
    ----------
    change_url : str
        Gerrit change URL.

    Returns
    -------
    tuple
        Change and patchset numbers. The patchset number will be None if the
        URL doesn't contain it.

    Raises
    ------
    ValueError
        If the gerrit change URL is malformed.

    Examples
    --------
    >>> parse_gerrit_change_url('https://gerrit.cloudlinux.com/#/25407')
    ('25407', None)

    >>> parse_gerrit_change_url('https://gerrit.cloudlinux.com/#/c/25407/5')
    ('25407', '5')

    >>> parse_gerrit_change_url('https://gerrit.cloudlinux.com/25021/3')
    ('25021', '3')
    """
    re_rslt = re.search(r'(?:#|[a-zA-Z]{2,})(?:/c|)/(?:.*\+/)*(\d+)(?:/(\d+)|)\S*$',
                        change_url)
    if not re_rslt:
        raise ValueError('invalid gerrit change URL')
    return re_rslt.groups()


def parse_gerrit_ref(ref):
    """
    Extracts a change number and a patchset number from the specified gerrit
    reference.

    Parameters
    ----------
    ref : str
        Gerrit reference.

    Returns
    -------
    tuple
        Change and patchset numbers.

    Raises
    ------
    ValueError
        If the gerrit reference is malformed.

    Examples
    --------
    >>> parse_gerrit_ref('refs/changes/04/25504/2')
    ('25504', '2')

    >>> parse_gerrit_ref('refs/changes/invalid/255504/1')
    Traceback (most recent call last):
    ValueError: invalid gerrit reference
    """
    re_rslt = re.search(r'^refs/changes/(\d{2})/(\d+?\1)/(\d+)$', ref,
                        re.IGNORECASE)
    if not re_rslt:
        raise ValueError('invalid gerrit reference')
    return re_rslt.group(2), re_rslt.group(3)


def is_short_gerrit_ref(ref):
    """
    Check, if gerrit ref in short form.

    Parameters
    ----------
    ref : str
        Gerrit reference.

    Returns
    -------
    boolean
        True if link is short, false otherwise

    Examples
    --------
    >>> is_short_gerrit_ref('1234/567')
    True

    >>> is_short_gerrit_ref('1234/')
    True

    >>> is_short_gerrit_ref('1234')
    True
    """
    return bool(re.match(r'^\d+/?(\d+)?$', ref))
