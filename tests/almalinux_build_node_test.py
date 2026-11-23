"""almalinux_build_node module unit tests."""

import logging
from unittest.mock import patch

import pytest
import yaml

import almalinux_build_node as node_main


@pytest.fixture
def working_dir(tmp_path):
    path = tmp_path / 'working_dir'
    path.mkdir()
    return path


@pytest.fixture
def start_node(tmp_path, working_dir, monkeypatch):
    """
    Starts the build node with the real configuration and instance id state
    file, but without builders, supervisor and working directory setup.
    Returns the configuration and the previous instance id the supervisor got.
    """
    config_file = tmp_path / 'build_node.yml'
    config_file.write_text(
        yaml.dump({'working_dir': str(working_dir), 'threads_count': 1})
    )

    def stop(_seconds):
        monkeypatch.setattr(node_main, 'running', False)

    with patch.object(node_main, 'init_logger'), patch.object(
        node_main, 'init_working_dir'
    ), patch.object(node_main, 'init_sentry'), patch.object(
        node_main.signal, 'signal'
    ), patch.object(
        node_main.node_globals, 'init_supervisors'
    ), patch.object(
        node_main, 'BuildNodeBuilder'
    ), patch.object(
        node_main, 'BuilderSupervisor'
    ) as supervisor_cls, patch.object(
        node_main.time, 'sleep', side_effect=stop
    ):

        def start():
            monkeypatch.setattr(node_main, 'running', True)
            assert node_main.main(['-c', str(config_file)]) == 0
            supervisor_cls.assert_called_once()
            call = supervisor_cls.call_args
            supervisor_cls.reset_mock()
            return call.args[0], call.kwargs['previous_instance_id']

        yield start


def test_first_start(start_node, working_dir):
    config, previous_instance_id = start_node()
    assert previous_instance_id is None
    state_file = working_dir / '.build_node_instance_id'
    assert config.instance_id_file == str(state_file)
    assert state_file.read_text() == f'{config.instance_id}\n'


def test_restart(start_node, working_dir):
    first_config, _ = start_node()
    second_config, previous_instance_id = start_node()
    assert first_config.instance_id != second_config.instance_id
    assert previous_instance_id == first_config.instance_id
    assert (working_dir / '.build_node_instance_id').read_text() == (
        f'{second_config.instance_id}\n'
    )


def test_start_with_unusable_state_file(start_node, working_dir, caplog):
    # Neither readable nor replaceable
    (working_dir / '.build_node_instance_id').mkdir()
    with caplog.at_level(logging.WARNING):
        _, previous_instance_id = start_node()
    assert previous_instance_id is None
    assert 'Cannot save the node instance id' in caplog.text


@pytest.mark.parametrize('installed', [True, False])
def test_detected_pesign_support_is_logged(
    start_node, tmp_path, monkeypatch, caplog, installed
):
    pesign_client = tmp_path / 'pesign-client'
    if installed:
        pesign_client.write_text('#!/bin/sh\n')
        pesign_client.chmod(0o755)
    monkeypatch.setattr(
        node_main.constants, 'PESIGN_CLIENT_PATH', str(pesign_client)
    )
    with caplog.at_level(logging.INFO):
        config, _ = start_node()
    assert config.pesign_support is installed
    assert (
        f'pesign support: {installed} (autodetect: {pesign_client} is '
        f'{"found" if installed else "not found"})'
    ) in caplog.text
