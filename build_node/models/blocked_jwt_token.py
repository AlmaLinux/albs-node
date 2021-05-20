# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-08-24

"""CloudLinux Build System blocked JWT tokens database model."""

import pymongo


__all__ = ['create_blocked_jwt_token_index']


def create_blocked_jwt_token_index(db):
    """
    Creates a blocked JWT tokens collection indexes.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    """
    db['blocked_jwt_tokens'].create_index([
        ('jti', pymongo.DESCENDING),
        ('token', pymongo.DESCENDING)
    ], unique=True)
