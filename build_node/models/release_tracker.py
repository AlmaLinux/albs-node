# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-12-13

"""CloudLinux Releases Tracking system database models."""

import pymongo

from build_node.utils.validation import verify_schema

__all__ = ['create_release_tracker_indexes', 'find_release_tracker_sources',
           'upsert_release_tracker_source', 'release_tracker_source_schema',
           'release_tracker_release_schema', 'insert_release_tracker_release',
           'update_scout_build_status', 'insert_scout_build',
           'SOURCES_COLLECTION', 'KERNELS_COLLECTION', 'RELEASES_COLLECTION']

SOURCES_COLLECTION = 'rtracker_sources'
"""Release monitor projects collection name."""

KERNELS_COLLECTION = 'kc_kernels'
"""KernelCare kernel versions collection name."""

RELEASES_COLLECTION = 'rtracker_releases'
"""Release monitor software (except kernels) releases collection name"""


release_tracker_source_schema = {
    'name': {'type': 'string', 'required': True},
    'tracker_type': {'type': 'string', 'required': True,
                     'allowed': ['deb_kernel', 'rpm_kernel', 'cve_mitre',
                                 'rpm_libcare', 'deb_libcare']},
    'corresponding_packages': {
        'type': 'list',
        'schema': {
            'type': 'dict',
            'schema': {
                'name': {'type': 'string'}}}},
    'main_package': {'type': 'string'},
    'build_parameters': {
        'type': 'dict',
        'empty': False,
        'schema': {
            'KC_PLATFORM': {'type': 'string', 'required': True}
        }
    },
    'filters': {
        'type': 'list',
        'schema': {
            'type': 'dict', 'schema': {
                'revision_regex': {'type': 'string'},
                'binary_regex': {'type': 'string'},
                'source_regex': {'type': 'string'}
            }
        }
    },
    'repositories': {
        'type': 'list',
        'schema': {
            'type': 'dict',
            'schema': {
                'name': {'type': 'string', 'required': True},
                'arch': {'type': 'string', 'required': True,
                         'allowed': ['src', 'x86_64', 'amd64']},
                'url': {'type': 'string', 'required': True}
            }
        }
    },
    'projects': {'type': 'list'} 
}


release_tracker_release_schema = {
    'name': {'type': 'string', 'required': True},
    'version': {'type': 'string', 'required': True},
    'project': {'type': 'string', 'required': True},
    'branch': {'type': 'string'},
    'scraped_from': {'type': 'string'},
    'build': {
        'type': 'dict',
        'schema': {
            'id': {'type': 'objectid'},
            'status': {'type': 'string'}
        }
    },
    'sources': {
        'type': 'list',
        'schema': {
            'type': 'dict',
            'schema': {
                'checksum': {'type': 'string'},
                'checksum_type': {'type': 'string',
                                  'allowed': ['sha1', 'sha256']},
                'url': {'type': 'string', 'required': True},
                'filename': {'type': 'string'}
            }
        }
    }
}
"""Software release record validation schema."""


def create_release_tracker_indexes(db):
    """
    Creates release tracking system indexes.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    """
    db[SOURCES_COLLECTION].create_index([('type', pymongo.DESCENDING),
                                         ('name', pymongo.DESCENDING)],
                                        unique=True)
    db[KERNELS_COLLECTION].create_index([
        ('name', pymongo.DESCENDING),
        ('version', pymongo.DESCENDING),
        ('release', pymongo.DESCENDING),
        ('project_id', pymongo.DESCENDING),
        ('epoch', pymongo.DESCENDING),
        ('package_type', pymongo.DESCENDING)
    ], unique=True)
    db[RELEASES_COLLECTION].create_index([
        ('name', pymongo.DESCENDING),
        ('project', pymongo.DESCENDING),
        ('version', pymongo.DESCENDING)
    ], unique=True)


def upsert_release_tracker_source(db, source):
    """
    Adds a new release tracker source to the database.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    source : dict
        Source to add.

    Returns
    -------
    dict
        Created or updated release tracker source.

    Raises
    ------
    build_node.errors.DataSchemaError
        If source data format is invalid.
    """
    source = verify_schema(release_tracker_source_schema, source)
    if source.get('_id'):
        query = {'_id': source['_id']}
    else:
        query = {
            'name': source['name'],
            'tracker_type': source['tracker_type']}
    return db[SOURCES_COLLECTION].\
        find_one_and_replace(query, source, upsert=True,
                             return_document=pymongo.ReturnDocument.AFTER)


def find_release_tracker_sources(db, **query):
    return [s for s in db[SOURCES_COLLECTION].find(query)]


def insert_release_tracker_release(db, release):
    """
    Saves a new release information to the release tracker database.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    release : dict
        Release to save.
    """
    release = verify_schema(release_tracker_release_schema, release)
    db[RELEASES_COLLECTION].insert_one(release)


def insert_scout_build(db, build_id, record):
    """
    Insert information about build in source to track build state.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    build_id : bson.ObjectId
        Tracked build id.
    record : dict
        Release record to update
    """
    set_query = {
        '$set': {
            'build': {
                'id': build_id, 'status': 'IDLE',
            }
        }
    }
    find_query = {
        key: record[key] for key in ['name', 'project', 'version']
    }
    db[RELEASES_COLLECTION].update(find_query, set_query)


def update_scout_build_status(db, build, status):
    """
    Updates build status in database.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    build : dict
        Build to update.
    status : str
        New build status.
    """
    db[RELEASES_COLLECTION].update_many(
        {'build.id': build['_id']},
        {'$set': {'build.status': status}}
    )
