# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-03-20

"""
CloudLinux Build System deployment tool wrapper.
"""

import datetime
import uuid

import pymongo

__all__ = ['create_deployment_tool_index', 'generate_one_time_link',
           'activate_one_time_link']


def create_deployment_tool_index(db):
    """
    Generates indexes for the deployment tool one time links collection.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    """
    db['deployment_tool_links'].create_index([
        ('token', pymongo.DESCENDING)
    ], unique=True)
    # NOTE: a one time link will be automatically deleted 24 hours after
    #       creation
    db['deployment_tool_links'].create_index([
        ('creation_ts', pymongo.ASCENDING)
    ], expireAfterSeconds=86400)


def generate_one_time_link(db, domain_name, build_id, user_id):
    """
    Generates a one time deployment tool download link for the specified build.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    domain_name : str
        Build System server domain name.
    build_id : bson.objectid.ObjectId
        Build _id.
    user_id : bson.objectid.ObjectId
        User _id.

    Returns
    -------
    str
        One time deployment tool download link.
    """
    random_token = uuid.uuid4().hex
    db['deployment_tool_links'].insert({
        'build_id': build_id,
        'created_by': user_id,
        'creation_ts': datetime.datetime.utcnow(),
        'token': random_token
    })
    return 'https://{0}/api/v1/deployment-tool/download/{1}/deploy_{2!s}.py'.\
        format(domain_name, random_token, build_id)


def activate_one_time_link(db, token):
    """
    Finds a one-time deployment tool download link info and deletes it from
    the database.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    token : str
        One-time download token.

    Returns
    -------
    dict
        Download link info.
    """
    return db['deployment_tool_links'].find_one_and_delete({'token': token})
