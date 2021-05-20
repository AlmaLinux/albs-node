# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-01-03

"""CloudLinux Build System user database model."""

import pymongo

from build_node.errors import DataNotFoundError
from build_node.utils.authentication import get_JWT_token_JTI, exclude_group_names

__all__ = ['block_user', 'create_user', 'get_user', 'get_user_JWT_identity']


def block_user(db, email=None, _id=None, secret_key=None, app=None):
    """
    Blocks an existent user account.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    email : str, optional
        User e-mail address. Either email or _id is required.
    _id : bson.objectid.ObjectId, optional
        User identifier. Either email or _id is required.
    secret_key : str, optional
        Secret key to use for an authentication token decoding. Either
        secret_key or app is required.
    app : flask.Flask, optional
        Pre-configured Build System Web server Flask application to use for
        an authentication token decoding. Either app or secret_key is required.

    Raises
    ------
    build_node.errors.DataNotFoundError
        If the specified user is not found in the database.
    ValueError
        If an email or a user _id is not specified.
    """
    if not email and not _id:
        raise ValueError('either email or _id is required to block a user')
    query = {}
    if email:
        query['email'] = email
    if _id:
        query['_id'] = _id
    user = db['users'].find_one_and_update(
        query, {'$set': {'active': False},
                '$unset': {'auth_token': '', 'permissions': ''}},
        return_document=pymongo.ReturnDocument.BEFORE
    )
    if not user:
        raise DataNotFoundError('user is not found in the database')
    auth_token = user.get('auth_token')
    if auth_token:
        jti = get_JWT_token_JTI(auth_token, secret_key=secret_key, app=app)
        db['blocked_jwt_tokens'].insert_one({'jti': jti,
                                             'token': auth_token,
                                             'user_id': user['_id']})


def create_user(db, email, name, active=False, enable_notifications=True,
                permissions=None, timezone=None, password=None,
                delete_confirm=True, groups=None):
    """
    Adds a new user to the Build System database.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    email : str
        User e-mail address.
    name : str
        User real name.
    active : bool, optional
        User activation status.
    enable_notifications : bool, optional
        Enable e-mail notifications for that account if True.
    permissions : dict, optional
        User permissions.
    timezone : str, optional
        User timezone.
    password : str, optional
        User password.
    delete_confirm : bool, optional
        Delete build confirmation if True.
    groups : dict, optional
        User groups.

    Returns
    -------
    bson.ObjectId
        Created user _id.
    """
    attrs_groups = exclude_group_names(groups)
    user = {'email': email, 'name': name, 'active': active,
            'enable_notifications': enable_notifications,
            'permissions': permissions or {},
            'delete_confirm': delete_confirm,
            'groups': attrs_groups}
    if timezone:
        user['timezone'] = timezone
    if password:
        user['password'] = password
    return db['users'].insert_one(user).inserted_id


def get_user(db, **query):
    """
    Returns a first Build System user matching the specified query.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    query
        User search MongoDB query arguments.

    Returns
    -------
    dict or None
        Found user.
    """
    return db['users'].find_one(query)


def get_user_JWT_identity(user):
    """
    Generates a JWT identity data for the specified user.

    Parameters
    ----------
    user : dict
        User.

    Returns
    -------
    dict
        JWT identity.
    """
    return {
        '_id': str(user['_id']),
        'email': user['email'],
        "permissions": [k for k, v in user.get("permissions", {}).items() if v]
    }
