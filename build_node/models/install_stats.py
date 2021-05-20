# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-05-07

"""
CloudLinux Build System installation statistics storage.
"""

import pymongo


def create_install_stats_index(db):
    """
    Creates installation statistics collection indexes.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    """
    db['rpm_install_stats'].create_index([
        ('name', pymongo.DESCENDING),
        ('version', pymongo.DESCENDING),
        ('release', pymongo.DESCENDING),
        ('_id', pymongo.DESCENDING)
    ], name='install_stats_query')

    db['rpm_install_stats'].create_index([
        ('up_to_date', pymongo.DESCENDING)
    ], name='install_stats_up_to_date_query')

    # delete records after 90 days
    db['rpm_install_stats'].create_index([
        ('ts', pymongo.ASCENDING)
    ], expireAfterSeconds=90 * 24 * 3600, name='install_stats_expire')


def create_systemid_indexes(db):
    """
    Creates systemid collection indexes.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    """
    db['systemid'].create_index([
        ('server_id', pymongo.DESCENDING)
    ], name='systemid_server_id_query')
    db['systemid'].create_index([
        ('os_version', pymongo.DESCENDING)
    ], name='systemid_os_version_query')
    db['systemid'].create_index([
        ('kernel_version', pymongo.DESCENDING)
    ], name='systemid_kernel_version_query')
