# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-11-06

"""
CloudLinux Build System build platform wrapper.
"""


def find_build_platform(db, platform_name):
    """
    Returns a build platform with the specified name.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    platform_name : str
        Build platform name.

    Returns
    -------
    dict or None
        Build platform or None if there is no build platform found.
    """
    return db['build_platforms'].find_one({'name': platform_name})
