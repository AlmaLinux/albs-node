# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2020-02-05

"""
CloudLinux Build System modular build indexes storage.
"""

import pymongo

__all__ = ['create_modular_build_indexes_index',
           'get_next_modular_build_index']


def create_modular_build_indexes_index(db):
    """
    Creates build indexes collection indexes.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    """
    db['modular_build_indexes'].create_index([
        ('distr_name', pymongo.DESCENDING),
    ], name='modular_build_index_query', unique=True)


def get_next_modular_build_index(db, platform, bump_index=True):
    """
    Returns a next module build index for a specified build platform.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    platform : dict
        Target build platform.
    bump_index : bool, optional
        Increment a last build index if True or return a previous build index
        otherwise. We are not incrementing indexes when building packages
        from gerrit to avoid huge numbers in a release field.

    Returns
    -------
    int
        Next module build index.
    """
    query = {'distr_name': platform.get('modularity', {}).get(
        'platform', {}).get('dist_build_index')}
    if bump_index:
        rec = db['modular_build_indexes'].find_one_and_update(
            query, {'$inc': {'build_index': 1}}, {'build_index': True},
            return_document=pymongo.ReturnDocument.AFTER, upsert=True
        )
    else:
        rec = db['modular_build_indexes'].find_one(
            query, {'build_index': True}
        )
    return rec['build_index'] if rec else 1
