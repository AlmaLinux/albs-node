# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-11-20

"""CloudLinux Build System build flavor wrapper."""

import pymongo

from ..utils.validation import verify_schema

__all__ = ['build_flavor_schema', 'create_build_flavors_index',
           'find_build_flavors', 'upsert_build_flavor']


build_flavor_schema = {
    '_id': {'type': 'objectid'},
    'name': {'type': 'string', 'required': True},
    'description': {'type': 'string', 'required': True},
    'mock': {
        'type': 'list',
        'schema': {
            'type': 'dict',
            'schema': {
                'distr_type': {'type': 'string'},
                'distr_version': {'type': 'string'},
                'extra_chroot_setup_cmd': {
                    'type': 'list',
                    'schema': {'type': 'string'}
                }
            }
        }
    },
    'repositories': {
        'type': 'list',
        'schema': {
            'type': 'dict',
            'schema': {
                'name': {'type': 'string', 'required': True},
                'distr_type': {'type': 'string', 'required': True},
                'distr_version': {'type': 'string'}
            }
        }
    }
}
"""dict : Build flavor validation schema for Cerberus."""


def create_build_flavors_index(db):
    """
    Creates a build flavors collection index.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    """
    db['build_flavors'].create_index([('name', pymongo.DESCENDING)],
                                     unique=True)


def find_build_flavors(db, **query):
    """
    Returns build flavors matching the specified query.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.

    Returns
    -------
    list of dict
        Found build flavors.
    """
    return [flavor for flavor in db['build_flavors'].find(query)]


def upsert_build_flavor(db, flavor):
    """
    Creates a new or updates an existent one build flavor.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    flavor : dict
        Build flavor to add or update.

    Returns
    -------
    dict
        Created or updated build flavor.

    Raises
    ------
    build_node.errors.DataSchemaError
        If build flavor data format is invalid.
    """
    flavor = verify_schema(build_flavor_schema, flavor)
    if flavor.get('_id'):
        query = {'_id': flavor['_id']}
    else:
        query = {'name': flavor['name']}
    return db['build_flavors'].\
        find_one_and_replace(query, flavor, upsert=True,
                             return_document=pymongo.ReturnDocument.AFTER)
