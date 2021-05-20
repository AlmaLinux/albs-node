# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-03-14

"""
CloudLinux Build System project (formerly build recipe) wrapper.
"""

import pymongo

__all__ = ['create_project_index']


def create_project_index(db):
    db['cl_recipes'].create_index([
        ('name', pymongo.ASCENDING)
    ])
    db['cl_recipes'].create_index([
        ('group_id', pymongo.DESCENDING),
        ('name', pymongo.ASCENDING),
        ('major_version', pymongo.DESCENDING)
    ], unique=True)
    db['cl_recipes'].create_index([
        ('build_info.type', pymongo.DESCENDING),
        ('git_cache_sync_ts', pymongo.ASCENDING)
    ])
