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
        self.hostname = platform.node()
        self.config_file = os.path.expanduser(
            '~/.config/castor/build_node.yml')
        node_config_dir = os.path.expanduser('~/.config/castor/build_node')
        os.makedirs(node_config_dir)
        self.default_config = BuildNodeConfig()
        self.working_dir = '/srv/alternatives/castor/build_node'

    def test_default_values(self):
        """BuildNodeConfig provides default values"""
        defaults = {
            'development_mode': False,
            'npm_proxy': '',
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
        data = {
            'development_mode': True,
            'master_url': 'tcp://example.com:32167',
            'npm_proxy': 'http://example.com:8080',
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

    def test_mock_configs_storage_dir(self):
        """BuildNodeConfig provides mock_configs_storage_dir attribute"""
        self.assertEqual(self.default_config.mock_configs_storage_dir,
                         os.path.join(self.working_dir, 'mock_configs'))

    def test_pbuilder_configs_storage_dir(self):
        """BuildNodeConfig provides pbuilder_configs_storage_dir attribute"""
        self.assertEqual(self.default_config.pbuilder_configs_storage_dir,
                         os.path.join(self.working_dir, 'pbuilder_envs'))
