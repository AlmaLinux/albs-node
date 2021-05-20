# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-10-07

"""build_node.build_node_config module unit tests."""

import platform
import os

from pyfakefs.fake_filesystem_unittest import TestCase
import yaml

from build_node.build_node_config import BuildNodeConfig

__all__ = ['TestBuildNodeConfig']


class TestBuildNodeConfig(TestCase):

    def setUp(self):
        self.setUpPyfakefs()
        self.secret_key = """
metadata
    jwt_token = example_JWT_token
curve
    public-key = "ZiUz=4+V7Vpp>>^)P[AgZm[Gx6!YI6u?/(gb}RbZ"
    secret-key = "QD^N]QAL^xof%$fV3/%}dPw[zx$Ca2b0zda5vKO5"
"""
        self.public_key = """
metadata
curve
    public-key = "ZiUz=4+V7Vpp>>^)P[AgZm[Gx6!YI6u?/(gb}RbZ"
        """
        self.master_key = """
metadata
curve
    public-key = "/e&E$]w7Y9Dc<X^VE{i)vR:GmM{JgLJfd:/2)-Xr"
"""
        self.hostname = platform.node()
        self.config_file = \
            os.path.expanduser('~/.config/castor/build_node.yml')
        node_config_dir = os.path.expanduser('~/.config/castor/build_node')
        os.makedirs(node_config_dir)
        self.private_key_path = \
            os.path.join(node_config_dir,
                         '{0}.key_secret'.format(self.hostname))
        with open(self.private_key_path, 'w') as fd:
            fd.write(self.secret_key)
        self.public_key_path = \
            os.path.join(node_config_dir, '{0}.key'.format(self.hostname))
        with open(self.public_key_path, 'w') as fd:
            fd.write(self.public_key)
        self.master_key_path = os.path.join(node_config_dir,
                                            'build_server.key')
        with open(self.master_key_path, 'w') as fd:
            fd.write(self.master_key)
        self.default_config = BuildNodeConfig()
        self.working_dir = '/srv/alternatives/castor/build_node'

    def test_default_values(self):
        """BuildNodeConfig provides default values"""
        defaults = {
            'development_mode': False,
            'master_key_path': self.master_key_path,
            'master_url': 'tcp://127.0.0.1:32167',
            'npm_proxy': '',
            'private_key_path': self.private_key_path,
            'public_key_path': self.public_key_path,
            'working_dir': self.working_dir,
            'git_cache_locks_dir': '/srv/alternatives/git_repos_cache/locks/',
            'git_repos_cache_dir': '/srv/alternatives/git_repos_cache/',
            'node_type': 'hard',
            'sentry_dsn': None
        }
        for key, value in defaults.items():
            self.assertEqual(getattr(self.default_config, key), value)
        self.assertTrue(self.default_config.node_id.startswith(self.hostname))
        self.assertGreater(self.default_config.threads_count, 0)

    def test_values_from_config(self):
        """BuildNodeConfig extracts data from config file"""
        my_dir = '/test/my_keys'
        os.makedirs(my_dir)
        secret_key_path = os.path.join(my_dir, 'node_private_key')
        with open(secret_key_path, 'w') as fd:
            fd.write(self.secret_key)
        public_key_path = os.path.join(my_dir, 'node_public_key')
        with open(public_key_path, 'w') as fd:
            fd.write(self.public_key)
        master_key_path = os.path.join(my_dir, 'master_public_key')
        with open(master_key_path, 'w') as fd:
            fd.write(self.master_key)
        data = {
            'development_mode': True,
            'master_url': 'tcp://example.com:32167',
            'npm_proxy': 'http://example.com:8080',
            'master_key_path': master_key_path,
            'private_key_path': secret_key_path,
            'public_key_path': public_key_path,
            'node_id': 'superNode',
            'threads_count': 69,
            'working_dir': '/test/working_dir',
            'node_type': 'opennebula',
            'sentry_dsn': 'https://1234:567@sentry.example.com/1'
        }
        with open(self.config_file, 'w') as fd:
            yaml.dump(data, fd, default_flow_style=False)
        config = BuildNodeConfig(self.config_file)
        for key, value in data.items():
            self.assertEqual(getattr(config, key), value)

    def test_jwt_token(self):
        """BuildNodeConfig extracts JWT token from private key"""
        self.assertEqual(self.default_config.jwt_token, 'example_JWT_token')
        self.assertEqual(self.default_config.jwt_token, 'example_JWT_token')

    def test_mock_configs_storage_dir(self):
        """BuildNodeConfig provides mock_configs_storage_dir attribute"""
        self.assertEqual(self.default_config.mock_configs_storage_dir,
                         os.path.join(self.working_dir, 'mock_configs'))

    def test_pbuilder_configs_storage_dir(self):
        """BuildNodeConfig provides pbuilder_configs_storage_dir attribute"""
        self.assertEqual(self.default_config.pbuilder_configs_storage_dir,
                         os.path.join(self.working_dir, 'pbuilder_envs'))
