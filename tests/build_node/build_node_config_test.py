"""build_node.build_node_config module unit tests."""

import platform
import os
import re
from unittest.mock import patch

import pytest
from pyfakefs.fake_filesystem_unittest import TestCase
import yaml

from build_node import constants
from build_node.build_node_config import BuildNodeConfig, BaseConfig

__all__ = ['TestBuildNodeConfig']


class TestBuildNodeConfig(TestCase):
    def setUp(self):
        self.setUpPyfakefs()
        self.hostname = platform.node()
        self.config_file = os.path.expanduser(
            '~/.config/castor/build_node.yml'
        )
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
            'master_url': 'http://web_server:8000/api/v1/',
            'node_id': BaseConfig.generate_node_id(),
            'threads_count': 4,
            'git_extra_options': None,
            'native_support': True,
            'arm64_support': False,
            'arm32_support': False,
            'pesign_support': False,
            'sentry_dsn': "",
            'sentry_environment': "dev",
            'sentry_traces_sample_rate': 0.2,
            'pulp_host': "http://pulp",
            'pulp_user': "pulp",
            'pulp_password': "test_pwd",
            'pulp_chunk_size': 8388608,
            'pulp_uploader_max_workers': 4,
            'pulp_timeout': 60,
            'pulp_upload_retries': 3,
            'mock_basedir': None,
            'base_arch': platform.machine(),
            'build_src': True,
            'immudb_username': None,
            'immudb_password': None,
            'immudb_database': None,
            'immudb_address': None,
            'immudb_public_key_file': None,
            'notarization_workers': 8,
            'request_timeout': 60,
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
            'sentry_dsn': 'https://1234:567@sentry.example.com/1',
        }
        with open(self.config_file, 'w') as fd:
            yaml.dump(data, fd, default_flow_style=False)
        config = BuildNodeConfig(self.config_file)
        for key, value in data.items():
            self.assertEqual(getattr(config, key), value)

    def test_mock_configs_storage_dir(self):
        """BuildNodeConfig provides mock_configs_storage_dir attribute"""
        self.assertEqual(
            self.default_config.mock_configs_storage_dir,
            os.path.join(self.working_dir, 'mock_configs'),
        )

    def test_queue_size_follows_threads_count(self):
        """BuildNodeConfig queue_size is equal to threads_count"""
        self.assertEqual(self.default_config.queue_size, 4)
        self.assertEqual(BuildNodeConfig(threads_count=3).queue_size, 3)
        # The obsolete queue_size option is accepted, but ignored
        with open(self.config_file, 'w') as fd:
            yaml.dump({'threads_count': 7, 'queue_size': 2}, fd)
        self.assertEqual(BuildNodeConfig(self.config_file).queue_size, 7)

    def test_instance_id(self):
        """BuildNodeConfig instance_id is node_id with a random run id"""
        self.assertRegex(
            self.default_config.instance_id,
            r'^{0}\.{1}\.[0-9a-f]{{8}}$'.format(
                re.escape(self.hostname), os.getpid()
            ),
        )
        # The same for the whole process run
        self.assertEqual(
            self.default_config.instance_id, self.default_config.instance_id
        )
        explicit = BuildNodeConfig(node_id='superNode')
        self.assertRegex(explicit.instance_id, r'^superNode\.[0-9a-f]{8}$')

    def test_instance_id_is_unique_for_every_start(self):
        """BuildNodeConfig instance_id differs between starts with the same
        hostname and pid, e.g. a restarted node container"""
        with patch(
            'build_node.utils.config.platform.node', return_value='node-1'
        ), patch('build_node.utils.config.os.getpid', return_value=1):
            first = BuildNodeConfig()
            second = BuildNodeConfig()
        self.assertEqual(first.node_id, second.node_id)
        self.assertTrue(first.instance_id.startswith('node-1.1.'))
        self.assertTrue(second.instance_id.startswith('node-1.1.'))
        self.assertNotEqual(first.instance_id, second.instance_id)
        # An explicitly set node id is the same on every start as well
        self.assertNotEqual(
            BuildNodeConfig(node_id='superNode').instance_id,
            BuildNodeConfig(node_id='superNode').instance_id,
        )


@pytest.fixture
def pesign_client(tmp_path, monkeypatch):
    """Path of pesign-client, it does not exist until the test creates it"""
    path = tmp_path / 'pesign' / 'pesign-client'
    monkeypatch.setattr(constants, 'PESIGN_CLIENT_PATH', str(path))
    return path


def install_pesign_client(path, mode=0o755):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\n')
    path.chmod(mode)


def write_config(tmp_path, data):
    config_file = tmp_path / 'build_node.yml'
    config_file.write_text(yaml.dump(data))
    return str(config_file)


def test_pesign_support_is_detected(pesign_client):
    """pesign_support which is not set is True if pesign is installed"""
    install_pesign_client(pesign_client)
    config = BuildNodeConfig()
    assert config.pesign_support is True
    assert config.pesign_support_source == 'autodetect'


def test_pesign_support_is_not_detected(pesign_client):
    """pesign_support which is not set is False without pesign"""
    config = BuildNodeConfig()
    assert config.pesign_support is False
    assert config.pesign_support_source == 'autodetect'


def test_pesign_support_needs_executable_client(pesign_client):
    """pesign_support is not detected by a non-executable pesign-client"""
    install_pesign_client(pesign_client, mode=0o644)
    assert BuildNodeConfig().pesign_support is False
    # A directory is not an executable either
    pesign_client.unlink()
    pesign_client.mkdir()
    assert BuildNodeConfig().pesign_support is False


@pytest.mark.parametrize('installed', [True, False])
def test_null_pesign_support_is_detected(tmp_path, pesign_client, installed):
    """pesign_support set to null in the config file is detected"""
    if installed:
        install_pesign_client(pesign_client)
    config = BuildNodeConfig(write_config(tmp_path, {'pesign_support': None}))
    assert config.pesign_support is installed
    assert config.pesign_support_source == 'autodetect'


@pytest.mark.parametrize(
    'value, installed',
    [(False, True), (True, False), (False, False), (True, True)],
)
def test_explicit_pesign_support_wins(
    tmp_path, pesign_client, value, installed
):
    """pesign_support set in the config file takes priority over detection"""
    if installed:
        install_pesign_client(pesign_client)
    config = BuildNodeConfig(write_config(tmp_path, {'pesign_support': value}))
    assert config.pesign_support is value
    assert config.pesign_support_source == 'config'
    # The same for a value passed as an argument
    config = BuildNodeConfig(pesign_support=value)
    assert config.pesign_support is value
    assert config.pesign_support_source == 'config'
