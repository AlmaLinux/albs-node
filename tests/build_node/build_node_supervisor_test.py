"""build_node.build_node_supervisor module unit tests."""

import errno
import logging
import socket
import ssl
import threading
from queue import Queue
from unittest.mock import Mock, patch

import pytest
from urllib3.connection import HTTPConnection

from build_node import constants
from build_node.build_node_config import BuildNodeConfig
from build_node.build_node_supervisor import BuilderSupervisor

NODE_VERSION = 'abc1234'
RESOURCES = {
    'cpu_count': 16,
    'load_avg': [1.5, 1.0, 0.5],
    'mem_total_bytes': 64 * 1024**3,
    'mem_available_bytes': 32 * 1024**3,
    'disk_path': '/srv/alternatives/castor/build_node',
    'disk_total_bytes': 1024**4,
    'disk_free_bytes': 512 * 1024**3,
}


class StopAfterWaits(threading.Event):
    """
    Termination event which records the supervisor sleeps instead of sleeping
    and stops the supervisor after the given number of them.
    """

    def __init__(self, waits_count=1):
        super().__init__()
        self.waits = []
        self.__waits_count = waits_count

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if len(self.waits) >= self.__waits_count:
            self.set()
        return self.is_set()


def make_builder(task_id=None):
    return Mock(is_alive=Mock(return_value=True), current_task_id=task_id)


@pytest.fixture
def config(build_server, tmp_path):
    return BuildNodeConfig(
        master_url=build_server.url,
        build_node_name='node-1',
        node_id='node-1.1234',
        threads_count=4,
        base_arch='x86_64',
        build_src=True,
        node_type='hard',
        pesign_support=True,
        working_dir=str(tmp_path),
        maintenance_mode_file=str(tmp_path / 'graceful_stop'),
    )


@pytest.fixture(autouse=True)
def node_state():
    with patch(
        'build_node.utils.node_status.get_node_version',
        return_value=NODE_VERSION,
    ), patch(
        'build_node.build_node_supervisor.collect_node_resources',
        return_value=RESOURCES,
    ) as collect_resources, patch.object(
        BuilderSupervisor,
        'get_excluded_packages',
        return_value=['excluded-pkg'],
    ):
        yield collect_resources


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(constants, 'BACKOFF_FACTOR', 0)


def run_supervisor(
    config,
    builders=None,
    queued_tasks=(),
    waits_count=1,
    previous_instance_id=None,
):
    task_queue = Queue(maxsize=config.queue_size)
    for task in queued_tasks:
        task_queue.put(task)
    terminated_event = StopAfterWaits(waits_count)
    supervisor = BuilderSupervisor(
        config,
        builders or [make_builder()],
        terminated_event,
        task_queue,
        previous_instance_id=previous_instance_id,
    )
    supervisor.run()
    return task_queue, terminated_event


def expected_node_info(config, maintenance=False, previous_instance_id=None):
    return {
        'instance_id': config.instance_id,
        'previous_instance_id': previous_instance_id,
        'version': NODE_VERSION,
        'supported_arches': ['x86_64', 'i686', 'src'],
        'capacity': 4,
        'node_type': 'hard',
        'pesign_support': True,
        'maintenance': maintenance,
    }


def test_ping_body(config, build_server):
    build_server.responses['get_task'] = (200, None)
    run_supervisor(
        config,
        builders=[make_builder(7), make_builder(), make_builder(3)],
        queued_tasks=[{'id': 12}, {'id': 11}],
    )
    assert build_server.calls('ping') == [{
        # The tasks waiting in the queue are assigned to the node as well
        'active_tasks': [3, 7, 11, 12],
        'node_name': 'node-1',
        'node_info': expected_node_info(config),
        'resources': RESOURCES,
    }]


def test_dead_builder_task_is_not_reported(config, build_server):
    dead_builder = make_builder(7)
    dead_builder.is_alive.return_value = False
    build_server.responses['get_task'] = (200, None)
    run_supervisor(config, builders=[dead_builder, make_builder(3)])
    # The task of the dead builder is never finished, the server must
    # return it to the queue
    assert build_server.calls('ping')[0]['active_tasks'] == [3]


def test_ping_without_resources(config, build_server, node_state):
    node_state.return_value = None
    build_server.responses['get_task'] = (200, None)
    run_supervisor(config)
    assert build_server.calls('ping') == [{
        'active_tasks': [],
        'node_name': 'node-1',
        'node_info': expected_node_info(config),
        'resources': None,
    }]
    node_state.assert_called_with(config.working_dir)


def test_get_task_body(config, build_server):
    build_server.responses['get_task'] = (200, None)
    run_supervisor(config)
    assert build_server.calls('get_task') == [{
        'supported_arches': ['x86_64', 'i686', 'src'],
        'excluded_packages': ['excluded-pkg'],
        'node_name': 'node-1',
        'node_info': expected_node_info(config),
    }]


def test_previous_instance_id(config, build_server):
    build_server.responses['get_task'] = (200, None)
    run_supervisor(config, previous_instance_id='node-1.1234.0f3a9c2e')
    expected = expected_node_info(
        config, previous_instance_id='node-1.1234.0f3a9c2e'
    )
    assert build_server.calls('ping')[0]['node_info'] == expected
    assert build_server.calls('get_task')[0]['node_info'] == expected


def test_null_task_means_sleep(config, build_server):
    build_server.responses['get_task'] = (200, None)
    task_queue, terminated_event = run_supervisor(config)
    assert task_queue.empty()
    assert terminated_event.waits == [10]


def test_pause_after_claimed_task(config, build_server):
    build_server.responses['get_task'] = (200, {'id': 42})
    with patch(
        'build_node.build_node_supervisor.random.uniform',
        return_value=2.5,
    ) as uniform:
        task_queue, terminated_event = run_supervisor(config)
    uniform.assert_called_once_with(1, 3)
    assert terminated_event.waits == [2.5]
    assert task_queue.get_nowait() == {'id': 42, 'is_secure_boot': False}


def test_claims_one_task_per_pause(config, build_server):
    build_server.responses['get_task'] = (200, {'id': 42})
    task_queue, terminated_event = run_supervisor(config, waits_count=3)
    assert len(build_server.calls('get_task')) == 3
    assert task_queue.qsize() == 3
    for pause in terminated_event.waits:
        assert 1 <= pause <= 3
    # The claimed tasks are reported until builders take them
    assert build_server.calls('ping')[-1]['active_tasks'] == [42]


def test_no_task_requested_when_all_slots_are_busy(config, build_server):
    queued_tasks = [{'id': task_id} for task_id in range(1, 5)]
    task_queue, terminated_event = run_supervisor(
        config, queued_tasks=queued_tasks
    )
    assert build_server.calls('get_task') == []
    assert task_queue.qsize() == 4
    assert terminated_event.waits == [10]
    assert build_server.calls('ping')[0]['active_tasks'] == [1, 2, 3, 4]


def test_maintenance_mode(config, build_server, tmp_path):
    (tmp_path / 'graceful_stop').touch()
    _, terminated_event = run_supervisor(config)
    assert build_server.calls('get_task') == []
    assert terminated_event.waits == [30]
    ping = build_server.calls('ping')[0]
    assert ping['node_info'] == expected_node_info(config, maintenance=True)


@pytest.mark.parametrize('status', [502, 503, 504])
def test_get_task_is_not_retried(config, build_server, status):
    build_server.responses['get_task'] = (status, {})
    task_queue, terminated_event = run_supervisor(config)
    assert len(build_server.calls('get_task')) == 1
    assert task_queue.empty()
    assert terminated_event.waits == [10]


@pytest.mark.parametrize(
    'error',
    [
        # urllib3 counts it as an "other" error, retried for any method
        ssl.SSLError(1, '[SSL: DECRYPTION_FAILED_OR_BAD_RECORD_MAC]'),
        # urllib3 read errors
        ConnectionResetError(errno.ECONNRESET, 'Connection reset by peer'),
        socket.timeout('The read operation timed out'),
    ],
    ids=['ssl-error', 'connection-reset', 'read-timeout'],
)
def test_get_task_is_not_retried_after_it_was_sent(
    config,
    build_server,
    monkeypatch,
    error,
):
    getresponse = HTTPConnection.getresponse

    def broken_getresponse(connection):
        # The server has handled the request, but the node fails to read
        # the response
        getresponse(connection)
        raise error

    monkeypatch.setattr(HTTPConnection, 'getresponse', broken_getresponse)
    build_server.responses['get_task'] = (200, {'id': 1})
    task_queue, terminated_event = run_supervisor(config)
    assert len(build_server.calls('get_task')) == 1
    assert task_queue.empty()
    assert terminated_event.waits == [10]
    assert len(build_server.calls('ping')) == constants.TOTAL_RETRIES + 1


def test_get_task_is_retried_on_connection_errors(config, build_server):
    create_connection = Mock(
        side_effect=ConnectionRefusedError(
            errno.ECONNREFUSED,
            'Connection refused',
        ),
    )
    with patch('urllib3.util.connection.create_connection', create_connection):
        task_queue, terminated_event = run_supervisor(config)
    assert build_server.requests == []
    # ping and then get_task, each of them is tried 1 + TOTAL_RETRIES times
    assert create_connection.call_count == 2 * (constants.TOTAL_RETRIES + 1)
    assert task_queue.empty()
    assert terminated_event.waits == [10]


def test_ping_is_retried(config, build_server):
    build_server.responses['ping'] = (503, {})
    build_server.responses['get_task'] = (200, None)
    run_supervisor(config)
    assert len(build_server.calls('ping')) == constants.TOTAL_RETRIES + 1
    # The node keeps asking for tasks when it cannot report itself
    assert len(build_server.calls('get_task')) == 1


@pytest.mark.parametrize('status', [400, 401, 422, 500])
def test_rejected_ping_is_logged(config, build_server, caplog, status):
    build_server.responses['ping'] = (status, {'detail': 'x' * 1000})
    build_server.responses['get_task'] = (200, None)
    with caplog.at_level(logging.WARNING):
        run_supervisor(config)
    assert len(build_server.calls('ping')) == 1
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert warnings[0].startswith(
        f'Build server rejected ping: HTTP {status}: '
    )
    # The response body is cut
    body_start = '{"detail": "'
    assert warnings[0].endswith(
        body_start
        + 'x' * (constants.ERROR_BODY_LOG_LIMIT - len(body_start))
    )
    # The node keeps asking for tasks
    assert len(build_server.calls('get_task')) == 1


def test_ping_build_error_does_not_stop_supervisor(
    config,
    build_server,
    node_state,
    caplog,
):
    node_state.side_effect = RuntimeError('psutil is broken')
    build_server.responses['get_task'] = (200, None)
    with caplog.at_level(logging.ERROR):
        _, terminated_event = run_supervisor(config, waits_count=2)
    assert build_server.calls('ping') == []
    assert "Can't report active task to master" in caplog.text
    assert 'psutil is broken' in caplog.text
    # The supervisor survives the failed ping and goes on
    assert len(build_server.calls('get_task')) == 2
    assert terminated_event.waits == [10, 10]
