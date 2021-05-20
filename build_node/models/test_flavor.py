# -*- mode:python; coding:utf-8; -*-
# author: Ruslan Pisarev <rpisarev@cloudlinux.com>
# created: 2019-02-27

"""CloudLinux Build System test flavor wrapper."""

import pymongo

from ..utils.validation import verify_schema


__all__ = ['test_flavor_schema', 'create_test_flavors_index',
           'find_test_flavors', 'upsert_test_flavor']


test_flavor_schema = {
    '_id': {'type': 'objectid'},
    'name': {'type': 'string', 'required': True},
    'description': {'type': 'string'},
    'panel_name': {'type': 'string', 'required': True},
    'panel_version': {'type': 'string', 'required': True},
    'label': {'type': 'list', 'required': True},
    'active': {'type': 'boolean', 'required': True},
    'supported_platforms': {'type': 'list'},
    'supported_arches': {'type': 'list'}
}
"""dict : test flavor validation schema for Cerberus."""


def create_test_flavors_index(db):
    """
    Creates a test flavors collection index.

    Parameters
    ----------
    db : pymongo.database.Database
        test System MongoDB database.
    """
    db['test_flavors'].create_index([('name', pymongo.DESCENDING)],
                                    unique=True)


def find_test_flavors(db, **query):
    """
    Returns test flavors matching the specified query.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.

    Returns
    -------
    list of dict
        Found test flavors.
    """
    found_test_flavors = [flavor for flavor in db['test_flavors'].find(query)]
    if not any([tf.get('name') == 'minimal' for tf in found_test_flavors]):
        found_test_flavors.append({'name': 'minimal'})
    return found_test_flavors


def upsert_test_flavor(db, flavor):
    """
    Creates a new or updates an existent one test flavor.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    flavor : dict
        test flavor to add or update.

    Returns
    -------
    dict
        Created or updated test flavor.

    Raises
    ------
    build_node.errors.DataSchemaError
        If test flavor data format is invalid.
    """
    flavor = verify_schema(test_flavor_schema, flavor)
    if flavor.get('_id'):
        query = {'_id': flavor['_id']}
    else:
        query = {'name': flavor['name']}
    return db['test_flavors'].find_one_and_replace(
        query, flavor, upsert=True,
        return_document=pymongo.ReturnDocument.AFTER)
