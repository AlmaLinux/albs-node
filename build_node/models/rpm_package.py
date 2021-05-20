# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-04-11

"""
CloudLinux Build System RPM package wrapper.
"""

import pymongo

from build_node.errors import DataNotFoundError

__all__ = ['create_rpm_package_index', 'get_srpm_by_id']


def create_rpm_package_index(db):
    """
    Creates rpm_packages collection indexes.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    """
    # this index is used for an unprocessed RHEL updates query
    db['rpm_packages'].create_index([
        ('alt_repo_id', pymongo.DESCENDING),
        ('arch', pymongo.DESCENDING),
        ('alt_cl_presence', pymongo.DESCENDING)
    ], name='unprocessed_rhel_updates')
    # this index is used for built RHEL updates query
    db['rpm_packages'].create_index([
        ('alt_repo_id', pymongo.DESCENDING),
        ('sourcerpm', pymongo.DESCENDING),
        ('name', pymongo.DESCENDING),
        ('alt_ver_hash', pymongo.DESCENDING)
    ], name='built_rhel_updates')


def get_srpm_by_id(db, srpm_id):
    """
    Returns a source RPM package database record by its identifier.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    srpm_id : bson.objectid.ObjectId
        Source RPM identifier.

    Returns
    -------
    dict
        Source RPM package database record.

    Raises
    ------
    build_node.errors.DataNotFoundError
        If a source RPM is not found in the database.
    """
    srpm = db['rpm_packages'].find_one({'_id': srpm_id})
    if not srpm:
        raise DataNotFoundError('unknown src-RPM _id {0!s}'.format(srpm_id))
    return srpm
