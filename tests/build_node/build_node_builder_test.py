"""build_node.build_node_builder module unit tests."""

import errno
import os
import shutil
from queue import Queue
from threading import Event
from unittest.mock import Mock, patch

import pytest
from albs_build_lib.builder.models import Task

from build_node import constants
from build_node.build_node_builder import BuildNodeBuilder
from build_node.build_node_config import BuildNodeConfig


@pytest.fixture
def builder(build_server, tmp_path, monkeypatch):
    monkeypatch.setattr(constants, 'BACKOFF_FACTOR', 0)
    config = BuildNodeConfig(
        master_url=build_server.url,
        build_node_name='node-1',
        working_dir=str(tmp_path),
    )
    with patch('build_node.build_node_builder.PulpRpmUploader'):
        builder = BuildNodeBuilder(config, 0, Event(), Event(), Queue())
    # Initialized by the build thread itself
    builder._BuildNodeBuilder__logger = Mock()
    builder._BuildNodeBuilder__generate_request_session()
    return builder


def report_done_task(builder, task, success=True):
    builder._BuildNodeBuilder__report_done_task(
        task, success=success, artifacts=[]
    )


def test_build_done_body(builder, build_server, build_task):
    report_done_task(builder, build_task)
    assert build_server.calls('build_done') == [{
        'task_id': build_task.id,
        'status': 'done',
        'artifacts': [],
        'stats': None,
        'is_cas_authenticated': False,
        'git_commit_hash': None,
        'alma_commit_cas_hash': None,
        'node_name': 'node-1',
    }]


def test_excluded_build_done_body(builder, build_server, build_task):
    builder._BuildNodeBuilder__report_excluded_task(build_task, [])
    body = build_server.calls('build_done')[0]
    assert body['status'] == 'excluded'
    assert body['node_name'] == 'node-1'


def test_build_done_conflict_is_ignored(builder, build_server, build_task):
    build_server.responses['build_done'] = (
        409,
        {'detail': 'Task is assigned to another build node'},
    )
    report_done_task(builder, build_task, success=False)
    assert len(build_server.calls('build_done')) == 1
    logger = builder._BuildNodeBuilder__logger
    logger.exception.assert_not_called()
    logger.warning.assert_called_once_with(
        'task %s: the build server rejected %s, the result is discarded: %s',
        build_task.id,
        'build_done',
        '{"detail": "Task is assigned to another build node"}',
    )


def test_build_done_is_retried(builder, build_server, build_task):
    build_server.responses['build_done'] = (503, {})
    report_done_task(builder, build_task)
    assert len(build_server.calls('build_done')) == constants.TOTAL_RETRIES + 1


@pytest.fixture
def run_builder(build_server, tmp_path, monkeypatch):
    """
    Runs a build thread loop in the current thread until it has taken the
    given tasks from the queue.
    """
    monkeypatch.setattr(constants, 'BACKOFF_FACTOR', 0)
    # A real node removes the task directories with sudo
    monkeypatch.setattr(
        'build_node.build_node_builder.rm_sudo',
        lambda path: shutil.rmtree(path, ignore_errors=True),
    )
    config = BuildNodeConfig(
        master_url=build_server.url,
        build_node_name='node-1',
        working_dir=str(tmp_path),
    )

    def run(tasks):
        task_queue = Queue()
        for task in tasks:
            task_queue.put(task)
        graceful_terminated_event = Mock()
        graceful_terminated_event.is_set.side_effect = (
            lambda: task_queue.empty()
        )
        # The build thread does not sleep when there is no task to process
        terminated_event = Mock(**{'is_set.return_value': False})
        with patch('build_node.build_node_builder.PulpRpmUploader'):
            builder = BuildNodeBuilder(
                config,
                0,
                terminated_event,
                graceful_terminated_event,
                task_queue,
            )
        builder._pulp_uploader.upload.return_value = []
        builder._pulp_uploader.upload_single_file.side_effect = (
            FileNotFoundError('no task log')
        )
        logger = Mock(handlers=[])
        logger.addHandler.side_effect = logger.handlers.append
        with patch.object(
            BuildNodeBuilder, 'init_thread_logger', return_value=logger
        ):
            builder.run()
        return builder, task_queue, logger

    return run


def logged_exceptions(logger):
    return [call.args[0] for call in logger.exception.call_args_list]


def test_task_failed_before_build_is_reported(
    run_builder,
    build_server,
    build_task_data,
):
    makedirs = os.makedirs

    def broken_makedirs(path, *args, **kwargs):
        if path.endswith('artifacts'):
            raise OSError(errno.ENOSPC, 'No space left on device')
        return makedirs(path, *args, **kwargs)

    # The task fails before the build, there is no RPM builder for it
    with patch(
        'build_node.build_node_builder.os.makedirs',
        side_effect=broken_makedirs,
    ):
        builder, task_queue, _ = run_builder([build_task_data])
    build_done = build_server.calls('build_done')
    assert len(build_done) == 1
    assert build_done[0]['task_id'] == build_task_data['id']
    assert build_done[0]['status'] == 'failed'
    assert 'build_node_task' in build_done[0]['stats']
    assert builder.current_task_id is None
    assert task_queue.unfinished_tasks == 0


def test_failed_task_finish_releases_task(
    run_builder,
    build_server,
    build_task_data,
):
    with patch.object(
        BuildNodeBuilder, '_BuildNodeBuilder__build_packages'
    ), patch.object(
        BuildNodeBuilder,
        '_BuildNodeBuilder__report_task',
        side_effect=RuntimeError('cannot report'),
    ), patch(
        'build_node.build_node_builder.rm_sudo',
        side_effect=OSError('cannot remove'),
    ) as rm_sudo:
        # The second task is taken: the build thread survives the first one
        builder, task_queue, logger = run_builder([
            build_task_data,
            {**build_task_data, 'id': 2},
        ])
    assert rm_sudo.call_count == 2
    assert builder.current_task_id is None
    assert task_queue.unfinished_tasks == 0
    assert logged_exceptions(logger) == [
        'task %i: cannot report the task status',
        'task %i: cannot clean up the task',
    ] * 2
    assert build_server.calls('build_done') == []


def test_task_is_claimed_right_after_it_is_taken(builder, build_task_data):
    task_queue = builder._BuildNodeBuilder__task_queue
    task_queue.put(build_task_data)
    claimed_task_ids = []

    def parse_task(**task):
        claimed_task_ids.append(builder.current_task_id)
        return Task(**task)

    with patch('build_node.build_node_builder.Task', side_effect=parse_task):
        task = builder._BuildNodeBuilder__request_task()
    assert task.id == build_task_data['id']
    # The task is reported by the builder before it is parsed
    assert claimed_task_ids == [build_task_data['id']]
    assert builder.current_task_id == build_task_data['id']


def test_broken_task_is_skipped(run_builder, build_task_data):
    broken_task = {'id': 5, 'arch': 'x86_64'}
    with patch.object(
        BuildNodeBuilder, '_BuildNodeBuilder__build_packages'
    ) as build_packages, patch.object(
        BuildNodeBuilder, '_BuildNodeBuilder__report_task'
    ):
        builder, task_queue, logger = run_builder([
            broken_task,
            build_task_data,
        ])
    assert logged_exceptions(logger) == [
        'task %s: cannot parse the task, skipping it:\n%s',
    ]
    assert logger.exception.call_args.args[1:] == (5, broken_task)
    # The build thread goes on with the next task
    build_packages.assert_called_once()
    assert build_packages.call_args.args[0].id == build_task_data['id']
    assert builder.current_task_id is None
    assert task_queue.unfinished_tasks == 0
