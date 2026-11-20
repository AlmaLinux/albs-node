"""build_node.utils.node_status module unit tests."""

import json
import logging
import os
import platform
import re
import subprocess
from unittest.mock import Mock, patch

import pytest

from build_node.build_node_config import BuildNodeConfig
from build_node.utils import node_status


@pytest.fixture(autouse=True)
def clear_version_cache():
    node_status.get_node_version.cache_clear()
    yield
    node_status.get_node_version.cache_clear()


@pytest.fixture
def psutil_mock():
    with patch('build_node.utils.node_status.psutil') as psutil:
        psutil.cpu_count.return_value = 16
        psutil.getloadavg.return_value = (1.5, 1, 0.25)
        psutil.virtual_memory.return_value = Mock(
            total=64 * 1024**3, available=32 * 1024**3
        )
        psutil.disk_usage.return_value = Mock(total=1024**4, free=512 * 1024**3)
        yield psutil


@pytest.mark.parametrize(
    'base_arch, build_src, expected',
    [
        ('x86_64', True, ['x86_64', 'i686', 'src']),
        ('x86_64', False, ['x86_64', 'i686']),
        ('aarch64', True, ['aarch64', 'src']),
        ('ppc64le', False, ['ppc64le']),
    ],
)
def test_get_supported_arches(base_arch, build_src, expected):
    config = BuildNodeConfig(base_arch=base_arch, build_src=build_src)
    assert node_status.get_supported_arches(config) == expected


def test_get_node_info(tmp_path):
    maintenance_file = tmp_path / 'graceful_stop'
    config = BuildNodeConfig(
        node_id='node-1.1234',
        base_arch='aarch64',
        build_src=False,
        threads_count=6,
        node_type='opennebula',
        pesign_support=True,
        maintenance_mode_file=str(maintenance_file),
    )
    with patch.object(node_status, 'get_node_version', return_value='abc1234'):
        assert node_status.get_node_info(config) == {
            'instance_id': config.instance_id,
            'previous_instance_id': None,
            'version': 'abc1234',
            'supported_arches': ['aarch64'],
            'capacity': 6,
            'node_type': 'opennebula',
            'pesign_support': True,
            'maintenance': False,
        }
        maintenance_file.touch()
        assert node_status.get_node_info(config)['maintenance'] is True
        node_info = node_status.get_node_info(config, 'node-1.1234.0f3a9c2e')
    assert node_info['previous_instance_id'] == 'node-1.1234.0f3a9c2e'


@pytest.mark.parametrize('installed', [True, False])
def test_get_node_info_detected_pesign_support(
    tmp_path, monkeypatch, installed
):
    """node_info has the detected pesign_support, never None"""
    pesign_client = tmp_path / 'pesign-client'
    if installed:
        pesign_client.write_text('#!/bin/sh\n')
        pesign_client.chmod(0o755)
    monkeypatch.setattr(
        'build_node.constants.PESIGN_CLIENT_PATH', str(pesign_client)
    )
    config = BuildNodeConfig(maintenance_mode_file=str(tmp_path / 'stop'))
    with patch.object(node_status, 'get_node_version', return_value=None):
        node_info = node_status.get_node_info(config)
    assert node_info['pesign_support'] is installed


def test_collect_node_resources(psutil_mock):
    resources = node_status.collect_node_resources('/srv/build_node')
    assert resources == {
        'cpu_count': 16,
        'load_avg': [1.5, 1.0, 0.25],
        'mem_total_bytes': 64 * 1024**3,
        'mem_available_bytes': 32 * 1024**3,
        'disk_path': '/srv/build_node',
        'disk_total_bytes': 1024**4,
        'disk_free_bytes': 512 * 1024**3,
    }
    assert all(isinstance(value, float) for value in resources['load_avg'])
    psutil_mock.disk_usage.assert_called_once_with('/srv/build_node')


@pytest.mark.parametrize(
    'failing_call',
    ['cpu_count', 'getloadavg', 'virtual_memory', 'disk_usage'],
)
def test_collect_node_resources_error(psutil_mock, failing_call, caplog):
    getattr(psutil_mock, failing_call).side_effect = OSError('no access')
    with caplog.at_level(logging.WARNING):
        assert node_status.collect_node_resources('/srv/build_node') is None
    assert 'Cannot collect node resources' in caplog.text


def test_collect_node_resources_unknown_cpu_count(psutil_mock, caplog):
    psutil_mock.cpu_count.return_value = None
    with caplog.at_level(logging.WARNING):
        assert node_status.collect_node_resources('/srv/build_node') is None
    assert 'Cannot collect node resources' in caplog.text


def test_collect_real_node_resources(tmp_path):
    resources = node_status.collect_node_resources(str(tmp_path))
    assert resources is not None
    assert resources['cpu_count'] > 0
    assert len(resources['load_avg']) == 3
    assert 0 < resources['mem_available_bytes'] <= resources['mem_total_bytes']
    assert 0 < resources['disk_free_bytes'] <= resources['disk_total_bytes']
    assert resources['disk_path'] == str(tmp_path)
    json.dumps(resources)


def test_get_node_version():
    with patch(
        'build_node.utils.node_status.subprocess.run',
        return_value=subprocess.CompletedProcess([], 0, stdout='abc1234\n'),
    ) as run:
        assert node_status.get_node_version() == 'abc1234'
        # Computed once
        assert node_status.get_node_version() == 'abc1234'
    run.assert_called_once()
    assert run.call_args.args[0][-3:] == ['rev-parse', '--short=12', 'HEAD']


def test_get_real_node_version(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(
            ['git', '-C', str(tmp_path), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    git('init', '-q')
    git(
        '-c',
        'user.name=test',
        '-c',
        'user.email=test@almalinux.org',
        'commit',
        '-q',
        '--allow-empty',
        '-m',
        'test',
    )
    monkeypatch.setattr(node_status, 'REPO_DIR', tmp_path)
    # Always 12 characters long, whatever the repository size is
    assert node_status.get_node_version() == git('rev-parse', 'HEAD')[:12]


def test_get_node_version_error(caplog):
    with patch(
        'build_node.utils.node_status.subprocess.run',
        side_effect=subprocess.CalledProcessError(128, 'git'),
    ), caplog.at_level(logging.WARNING):
        assert node_status.get_node_version() is None
    assert 'Cannot determine albs-node version' in caplog.text


def test_get_node_info_default_instance_id():
    config = BuildNodeConfig()
    with patch.object(node_status, 'get_node_version', return_value=None):
        instance_id = node_status.get_node_info(config)['instance_id']
    assert re.fullmatch(
        rf'{re.escape(platform.node())}\.{os.getpid()}\.[0-9a-f]{{8}}',
        instance_id,
    )


INSTANCE_ID = 'node-1.1.0f3a9c2e'
PREVIOUS_INSTANCE_ID = 'node-1.1.5e4d3c2b'


def test_rotate_instance_id_first_start(tmp_path):
    state_file = tmp_path / '.build_node_instance_id'
    assert node_status.rotate_instance_id(str(state_file), INSTANCE_ID) is None
    assert state_file.read_text() == f'{INSTANCE_ID}\n'
    # No temporary files are left
    assert os.listdir(tmp_path) == [state_file.name]


def test_rotate_instance_id(tmp_path):
    state_file = tmp_path / '.build_node_instance_id'
    state_file.write_text(f'{PREVIOUS_INSTANCE_ID}\n')
    assert (
        node_status.rotate_instance_id(str(state_file), INSTANCE_ID)
        == PREVIOUS_INSTANCE_ID
    )
    assert state_file.read_text() == f'{INSTANCE_ID}\n'
    assert os.listdir(tmp_path) == [state_file.name]
    # The next start gets the instance id of this one
    assert (
        node_status.rotate_instance_id(str(state_file), 'node-1.1.00000000')
        == INSTANCE_ID
    )


@pytest.mark.parametrize(
    'content',
    [
        b'',
        b'\n  \n',
        b'node-1.1.0f3a9c2e node-1.1.5e4d3c2b',
        b'node-1\x00.1.0f3a9c2e',
        b'\xff\xfe\x00garbage',
        b'n' * 256,
    ],
    ids=['empty', 'blank', 'two-ids', 'nul', 'binary', 'too-long'],
)
def test_rotate_instance_id_broken_file(tmp_path, caplog, content):
    state_file = tmp_path / '.build_node_instance_id'
    state_file.write_bytes(content)
    with caplog.at_level(logging.WARNING):
        result = node_status.rotate_instance_id(str(state_file), INSTANCE_ID)
    assert result is None
    assert state_file.read_text() == f'{INSTANCE_ID}\n'
    assert 'previous node instance id' in caplog.text


def test_rotate_instance_id_max_length(tmp_path):
    state_file = tmp_path / '.build_node_instance_id'
    state_file.write_text('n' * 255 + '\n')
    assert node_status.rotate_instance_id(str(state_file), INSTANCE_ID) == (
        'n' * 255
    )


def test_rotate_instance_id_read_error(tmp_path, caplog):
    state_file = tmp_path / '.build_node_instance_id'
    state_file.write_text(PREVIOUS_INSTANCE_ID)
    with patch(
        'build_node.utils.node_status.open',
        side_effect=PermissionError('denied'),
        create=True,
    ), caplog.at_level(logging.WARNING):
        result = node_status.rotate_instance_id(str(state_file), INSTANCE_ID)
    assert result is None
    assert 'Cannot read the previous node instance id' in caplog.text
    # The current instance id is saved anyway
    assert state_file.read_text() == f'{INSTANCE_ID}\n'


def test_rotate_instance_id_write_error(tmp_path, caplog):
    state_file = tmp_path / '.build_node_instance_id'
    state_file.write_text(PREVIOUS_INSTANCE_ID)
    with patch(
        'build_node.utils.node_status.os.replace',
        side_effect=OSError('read-only file system'),
    ), caplog.at_level(logging.WARNING):
        result = node_status.rotate_instance_id(str(state_file), INSTANCE_ID)
    assert result is None
    assert 'Cannot save the node instance id' in caplog.text
    # The old file is intact and the temporary one is removed
    assert state_file.read_text() == PREVIOUS_INSTANCE_ID
    assert os.listdir(tmp_path) == [state_file.name]


def test_rotate_instance_id_missing_dir(tmp_path, caplog):
    state_file = tmp_path / 'missing' / '.build_node_instance_id'
    with caplog.at_level(logging.WARNING):
        result = node_status.rotate_instance_id(str(state_file), INSTANCE_ID)
    assert result is None
    assert 'Cannot save the node instance id' in caplog.text
