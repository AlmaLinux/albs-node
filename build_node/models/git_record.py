# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-02-25

"""
CloudLinux Build System git repository cache wrapper.
"""

import pymongo

__all__ = ['create_git_record_index']


def create_git_record_index(db):
    db['git_records'].create_index([
        ('project_id', pymongo.DESCENDING),
        ('ref_type', pymongo.DESCENDING),
        ('ref_name', pymongo.DESCENDING),
        ('platform_name', pymongo.DESCENDING)
    ], unique=True)
