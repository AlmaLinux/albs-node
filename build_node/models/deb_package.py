# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-07-11

"""
CloudLinux Build System Debian package wrapper.
"""

import pymongo

__all__ = ['create_deb_package_index']


def create_deb_package_index(db):
    """
    Creates deb_packages collection indexes.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    """
    db['deb_packages'].create_index([
        ('alt_repo_id', pymongo.DESCENDING)
    ], name='repository_id')
