# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-01-23

"""CloudLinux Build System authentication utility functions."""

import flask
import flask_jwt_extended

__all__ = ['get_JWT_token_JTI', 'generate_JWT_token',
           'generate_permanent_JWT_token', 'exclude_group_names']


def init_auth_application(secret_key=None, app=None):
    """
    Initializes a dummy Flask application for authentication purposes or
        returns the application provided by a user.

    Parameters
    ----------
    secret_key : str, optional
        JWT secret key. Either secret_key or app is required.
    app : flask.Flask, optional
        Pre-configured Flask application. Either app or secret_key is required.

    Returns
    -------
    flask.Flask
        Initialized Flask application.
    """
    if (not secret_key and not app) or (secret_key and app):
        raise ValueError('either secret_key or app is required')
    if app:
        return app
    app = flask.Flask(__name__)
    app.config['JWT_SECRET_KEY'] = secret_key
    flask_jwt_extended.JWTManager(app)
    return app


def get_JWT_token_JTI(encoded_token, secret_key=None, app=None):
    """
    Returns a JTI (unique identifier) of the encoded JWT token.

    Parameters
    ----------
    encoded_token : str
        Encoded JWT token.
    secret_key : str, optional
        Secret key to use for the token decoding. Either secret key or app is
        required.
    app : flask.Flask, optional
        Pre-configured Build System Web server Flask application. Either app
        or secret_key is required.

    Returns
    -------
    str
        JWT token unique identifier (JTI).
    """
    app = init_auth_application(secret_key, app)
    with app.app_context():
        return flask_jwt_extended.get_jti(encoded_token)


def generate_JWT_token(identity, expires_delta, secret_key=None, app=None):
    """
    Generates a JWT authentication token.

    You should provide either a secret key or a pre-configured Flask
        application to use that function.

    Parameters
    ----------
    identity : str or dict
        User or build node identity.
    expires_delta : datetime.timedelta or bool
        How long the token should be active before it expires. The permanent
        token will be generated if expires_delta is False.
    secret_key : str, optional
        Secret key to use for the token generation.
    app : flask.Flask, optional
        Pre-configured Build System Web server Flask application.

    Returns
    -------
    str
        Generated JWT token.
    """
    app = init_auth_application(secret_key, app)
    with app.app_context():
        return flask_jwt_extended.\
            create_access_token(identity, expires_delta=expires_delta)


def generate_permanent_JWT_token(identity, secret_key=None, app=None):
    """
    Generates a permanent JWT authentication token.

    You should provide either a secret key or a pre-configured Flask
        application to use that function.

    Parameters
    ----------
    identity : str or dict
        User or build node identity.
    secret_key : str, optional
        Secret key to use for the token generation.
    app : flask.Flask, optional
        Pre-configured Build System Web server Flask application.

    Returns
    -------
    str
        Generated permanent JWT token.

    Raises
    ------
    ValueError
        If both or none of secret_key and app were provided.
    """
    return generate_JWT_token(identity, False, secret_key, app)


def exclude_group_names(groups):
    """
    Excludes the user SSO group names we don't need for visible permissions

    Parameters
    ----------
    groups : dict
        User SSO group names

    Returns
    -------
    dict
        User SSO group names we need for visible permissions
    """
    attrs_groups = []
    for attr in groups:
        if attr not in ['ipausers', 'departments', 'buildsys-rollout',
                        'buildsys_admin', 'buildsys_users']:
            attrs_groups.append(attr)
    return attrs_groups
