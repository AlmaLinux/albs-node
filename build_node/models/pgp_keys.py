# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-03-28

"""
CloudLinux Build System PGP key data wrapper.
"""

import pymongo

from ..utils.validation import verify_schema

__all__ = ['create_pgp_keys_index', 'create_pgp_key', 'find_pgp_keys']


pgp_key_schema = {
    '_id': {'type': 'objectid'},
    'name': {'type': 'string', 'empty': False, 'required': True},
    'description': {'type': 'string', 'empty': False, 'required': True},
    'public_key': {'type': 'string', 'empty': False, 'required': True},
    'public_key_url': {'type': 'string', 'empty': False, 'required': True},
    'date': {'type': 'datetime', 'required': True},
    'fingerprint': {'type': 'string', 'empty': False, 'required': True},
    'keyid': {'type': 'string', 'empty': False, 'required': True},
    'uid': {'type': 'string', 'empty': False, 'required': True}
}


def create_pgp_keys_index(db):
    """
    Creates a PGP keys collection indexes.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    """
    db['pgp_keys'].create_index([('name', pymongo.DESCENDING)], unique=True)


def create_pgp_key(db, pgp_key):
    """
    Adds a new PGP key record to the database.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    pgp_key : dict
        PGP key to add.

    Returns
    -------
    bson.objectid.ObjectId
        Created PGP key _id.

    Raises
    ------
    build_node.errors.DataSchemaError
        If a PGP key data format is invalid.
    """
    verify_schema(pgp_key_schema, pgp_key)
    return db['pgp_keys'].insert_one(pgp_key).inserted_id


def find_pgp_keys(db, **query):
    """
    Finds a PGP keys matching the specified query.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    query
        PGP key search MongoDB query arguments.

    Returns
    -------
    list
        List of found PGP keys
    """
    return [k for k in db['pgp_keys'].find(query)]
