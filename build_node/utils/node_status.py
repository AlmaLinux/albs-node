"""
Build node description and host resources reported to the build server.
"""

import functools
import logging
import os
import subprocess
import tempfile
import typing
from pathlib import Path

import psutil

from build_node import constants

__all__ = [
    'collect_node_resources',
    'get_node_info',
    'get_node_version',
    'get_supported_arches',
    'rotate_instance_id',
]


REPO_DIR = Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=None)
def get_node_version() -> typing.Optional[str]:
    """
    Returns the albs-node version, which is the commit hash of the albs-node
    checkout cut to 12 characters: the node is deployed as a git clone and
    has no release versions. The result is computed once per process.

    Returns
    -------
    str or None
        Commit hash or None if it cannot be determined.
    """
    try:
        result = subprocess.run(
            [
                'git',
                # The checkout is the code being executed, so it is trusted
                # even when it is owned by another user (e.g. a volume mounted
                # into a container)
                '-c',
                f'safe.directory={REPO_DIR}',
                '-C',
                str(REPO_DIR),
                'rev-parse',
                # A fixed length, git makes the shortest unique hash longer
                # as the repository grows
                '--short=12',
                'HEAD',
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except Exception:
        logging.warning('Cannot determine albs-node version', exc_info=True)
        return None
    return result.stdout.strip() or None


def get_supported_arches(config) -> typing.List[str]:
    """
    Returns the architectures the build node builds packages for.

    Parameters
    ----------
    config : build_node.build_node_config.BuildNodeConfig
        Build node configuration.

    Returns
    -------
    list of str
    """
    supported_arches = [config.base_arch]
    if config.base_arch == 'x86_64':
        supported_arches.append('i686')
    if config.build_src:
        supported_arches.append('src')
    return supported_arches


def get_node_info(
    config,
    previous_instance_id: typing.Optional[str] = None,
) -> typing.Dict[str, typing.Any]:
    """
    Describes the build node to the build server: its capacity and
    capabilities.

    instance_id identifies the running build node process, it is unique for
    every start of the node: it changes when the node restarts, and it tells
    two processes which share the same node name from each other.
    previous_instance_id is the instance id of the previous process of this
    node, see rotate_instance_id: when it is the last instance id the build
    server knows for the node, the node was restarted, and the server returns
    the tasks of the previous process to the queue at once.

    Parameters
    ----------
    config : build_node.build_node_config.BuildNodeConfig
        Build node configuration.
    previous_instance_id : str, optional
        Instance id of the previous build node process, None if unknown.

    Returns
    -------
    dict
    """
    return {
        'instance_id': config.instance_id,
        'previous_instance_id': previous_instance_id,
        'version': get_node_version(),
        'supported_arches': get_supported_arches(config),
        'capacity': config.threads_count,
        'node_type': config.node_type,
        'pesign_support': config.pesign_support,
        'maintenance': os.path.exists(config.maintenance_mode_file),
    }


def collect_node_resources(
    disk_path: str,
) -> typing.Optional[typing.Dict[str, typing.Any]]:
    """
    Collects the host resources: CPUs, load, memory and disk space of the
    builds working directory.

    Parameters
    ----------
    disk_path : str
        Directory whose file system space is reported, the builds working
        directory.

    Returns
    -------
    dict or None
        Host resources or None if they cannot be collected, a failure must
        not prevent the node from reporting itself to the build server.
    """
    try:
        cpu_count = psutil.cpu_count()
        if not cpu_count:
            raise RuntimeError('the number of CPUs is undetermined')
        load_avg = psutil.getloadavg()
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(disk_path)
        return {
            'cpu_count': int(cpu_count),
            'load_avg': [float(value) for value in load_avg],
            'mem_total_bytes': int(memory.total),
            'mem_available_bytes': int(memory.available),
            'disk_path': disk_path,
            'disk_total_bytes': int(disk.total),
            'disk_free_bytes': int(disk.free),
        }
    except Exception:
        logging.warning('Cannot collect node resources', exc_info=True)
        return None


def rotate_instance_id(
    path: str,
    instance_id: str,
) -> typing.Optional[str]:
    """
    Replaces the instance id of the previous build node process saved in the
    state file with the instance id of the current one.

    The node reports the previous instance id to the build server, which
    takes it as a proof of a restart: a second host with the same node name
    does not have the file. A failure to read or to save the file must not
    prevent the node from starting, the node just does not prove a restart
    then.

    Parameters
    ----------
    path : str
        State file path.
    instance_id : str
        Instance id of the current build node process.

    Returns
    -------
    str or None
        Instance id of the previous process, None if it is unknown or the
        current one cannot be saved.
    """
    previous_instance_id = _read_instance_id(path)
    try:
        _write_file_atomically(path, f'{instance_id}\n')
    except Exception:
        logging.warning(
            'Cannot save the node instance id to %s', path, exc_info=True
        )
        return None
    return previous_instance_id


def _read_instance_id(path: str) -> typing.Optional[str]:
    try:
        with open(path, encoding='utf-8') as fd:
            # The limit protects from a huge file, it is invalid anyway
            value = fd.read(constants.MAX_INSTANCE_ID_LENGTH + 2).strip()
    except FileNotFoundError:
        logging.info('There is no previous node instance id in %s', path)
        return None
    except Exception:
        logging.warning(
            'Cannot read the previous node instance id from %s',
            path,
            exc_info=True,
        )
        return None
    if (
        not value
        or len(value) > constants.MAX_INSTANCE_ID_LENGTH
        or not value.isprintable()
        or any(char.isspace() for char in value)
    ):
        logging.warning(
            'Ignoring the invalid previous node instance id in %s: %r',
            path,
            value,
        )
        return None
    return value


def _write_file_atomically(path: str, content: str):
    """
    Writes the file content to a temporary file next to it and renames the
    temporary file to it: the file is either the old one or the new one, even
    if the node is killed or the host loses power in between.
    """
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(path)),
        prefix=f'{os.path.basename(path)}.',
        suffix='.tmp',
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
