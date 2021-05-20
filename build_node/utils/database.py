# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-02-19

"""
CloudLinux Build System MongoDB related utility functions.
"""

import logging

import pymongo
import pymongo.errors

from ..errors import ConnectionError

__all__ = ['mongodb_connect', 'replace_dots_for_mongo_field',
           'retrieve_dots_for_mongo_field']


def mongodb_connect(database_name, database_url):
    """
    Initializes a connection to the Build System MongoDB database.

    Parameters
    ----------
    database_name : str
        MongoDB database name.
    database_url : str
        MongoDB connection URL.

    Returns
    -------
    pymongo.database.Database
        Build System MongoDB database connection.

    Raises
    ------
    ConnectionError
        If there was an error while connecting to the database.
    """
    try:
        mongo_client = pymongo.MongoClient(database_url)
        return mongo_client[database_name]
    except pymongo.errors.ConnectionFailure as e:
        err_str = str(e)
        logging.error('can\'t connect to the "{0}" database ({1}): {2}'.
                      format(database_name, database_url, err_str))
        raise ConnectionError(err_str)


def replace_dots_for_mongo_field(raw_string):
    """
    Parameters
    ----------
    raw_string : string
        The string that should be stored into mongoDB

    Returns
    -------
    string
    """
    return raw_string.replace('.', '___DOT___')


def retrieve_dots_for_mongo_field(mongo_string):
    """
    Parameters
    ----------
    mongo_string : string
        The string that should be retrieved from mongoDB

    Returns
    -------
    string
    """
    return mongo_string.replace('___DOT___', '.')
