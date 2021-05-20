# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-01-23

"""build_node.utils.authentication module unit tests."""

import unittest

import flask
import flask_jwt_extended

from build_node.utils.authentication import get_JWT_token_JTI, \
    generate_permanent_JWT_token

__all__ = ['TestGetJWTTokenJTI', 'TestGeneratePermanentJWTToken']


def setup_app(secret_key):
    """
    Creates a dummy Flask application for JWT authentication testing.

    Parameters
    ----------
    secret_key : str
        JWT secret key.

    Returns
    -------
    flask.Flask
        Dummy Flask application.
    """
    app = flask.Flask(__name__)
    app.config['JWT_SECRET_KEY'] = secret_key
    flask_jwt_extended.JWTManager(app)
    return app


class TestGetJWTTokenJTI(unittest.TestCase):

    """build_node.utils.authentication.get_JWT_token_JTI unit tests."""

    def setUp(self):
        self.secret_key = 'TEST_KEY'
        self.app = setup_app(self.secret_key)
        self.token = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiJiOGVjY' \
                     'mEwOS03YjJkLTRiOWItODIwMi1hZTIzYmE5YzY1ZjMiLCJmcmVzaCI' \
                     '6ZmFsc2UsImlhdCI6MTUzNTEyNTY1NSwidHlwZSI6ImFjY2VzcyIsI' \
                     'm5iZiI6MTUzNTEyNTY1NSwiaWRlbnRpdHkiOnsiZW1haWwiOiJ0ZXN' \
                     '0QGV4YW1wbGUuY29tIn19.orPeMwJnprpc7L-Xz97vPcGawxLxDIxx' \
                     'kAZXTWOsrtk'
        self.jti = 'b8ecba09-7b2d-4b9b-8202-ae23ba9c65f3'

    def test_with_secret_key(self):
        """build_node.utils.authentication.get_JWT_token_JTI should support \
secret_key argument"""
        self.assertEqual(get_JWT_token_JTI(self.token,
                                           secret_key=self.secret_key),
                         self.jti)

    def test_wit_app(self):
        """build_node.utils.authentication.get_JWT_token_JTI should support app \
argument"""
        self.assertEqual(get_JWT_token_JTI(self.token, app=self.app),
                         self.jti)

    def test_app_secret_key(self):
        """build_node.utils.authentication.get_JWT_token_JTI should raise error \
when both app and secret_key were provided"""
        self.assertRaises(ValueError, get_JWT_token_JTI, self.token,
                          self.secret_key, self.app)

    def test_none(self):
        """build_node.utils.authentication.get_JWT_token_JTI should raise error \
when none of app or secret_key were provided"""
        self.assertRaises(ValueError, get_JWT_token_JTI, self.token)

    def test_invalid_token(self):
        """build_node.utils.authentication.get_JWT_token_JTI should raise error \
for invalid token"""
        self.assertRaises(Exception, get_JWT_token_JTI, 'bad token',
                          self.secret_key)


class TestGeneratePermanentJWTToken(unittest.TestCase):

    """build_node.utils.authentication.generate_permanent_JWT_token unit tests."""

    def setUp(self):
        self.secret_key = 'TEST_KEY'
        self.app = setup_app(self.secret_key)
        self.identity = {'_id': '5a64f2320fe624b8117d3042',
                         'email': 'test@example.com'}

    def test_app(self):
        """build_node.utils.authentication.generate_permanent_JWT_token should \
support app argument"""
        token = generate_permanent_JWT_token(self.identity, app=self.app)
        self.__verify_token(token)

    def test_app_secret_key(self):
        """build_node.utils.authentication.generate_permanent_JWT_token should \
raise error when both app and secret_key were provided"""
        self.assertRaises(ValueError, generate_permanent_JWT_token,
                          self.identity, app=self.app,
                          secret_key=self.secret_key)

    def test_secret_key(self):
        """build_node.utils.authentication.generate_permanent_JWT_token should \
support secret_key argument"""
        token = generate_permanent_JWT_token(self.identity,
                                             secret_key=self.secret_key)
        self.__verify_token(token)

    def test_none(self):
        """build_node.utils.authentication.generate_permanent_JWT_token should \
raise error when none of app or secret_key were provided"""
        self.assertRaises(ValueError, generate_permanent_JWT_token,
                          self.identity)

    def __verify_token(self, token):
        """
        Decodes the specified JWT token and compares its identity to the
        reference one.

        Parameters
        ----------
        token : str
            JWT token to verify.
        """
        with self.app.app_context():
            decoded = flask_jwt_extended.decode_token(token)
            self.assertEqual(self.identity, decoded['identity'])
