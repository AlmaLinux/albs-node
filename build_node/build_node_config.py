"""
AlmaLinux Build System build node configuration storage.
"""

import os
import platform
import secrets

from build_node import constants
from build_node.utils.config import BaseConfig

DEFAULT_MASTER_URL = 'http://web_server:8000/api/v1/'
DEFAULT_THREADS_COUNT = 4
DEFAULT_WORKING_DIR = '/srv/alternatives/castor/build_node'
DEFAULT_MAINTENANCE_MODE_FILE = '/tmp/graceful_stop'
DEFAULT_SENTRY_DSN = ''
DEFAULT_SENTRY_ENVIRONMENT = 'dev'
DEFAULT_SENTRY_TRACES_SAMPLE_RATE = 0.2
DEFAULT_NATIVE_BUILDING = True
DEFAULT_ARM64_BUILDING = False
DEFAULT_ARM32_BUILDING = False
# Detected by the pesign installation, see detect_pesign_support
DEFAULT_PESIGN_SUPPORT = None
DEFAULT_NODE_TYPE = 'hard'
DEFAULT_PULP_HOST = 'http://pulp'
DEFAULT_PULP_USER = 'pulp'
DEFAULT_PULP_PASSWORD = 'test_pwd'
DEFAULT_PULP_CHUNK_SIZE = 8388608  # 8 MiB
DEFAULT_PULP_UPLOADER_MAX_WORKERS = 4
DEFAULT_MOCK_BASEDIR = None
DEFAULT_REQUEST_TIMEOUT = 60  # 1 minute
DEFAULT_PULP_TIMEOUT = 60  # seconds
DEFAULT_PULP_UPLOAD_RETRIES = 3
DEFAULT_NOTARIZATION_WORKERS = 8
DEFAULT_JWT_TOKEN = 'test_jwt'
DEFAULT_CACHE_UPDATE_INTERVAL = 600
DEFAULT_EXCLUSIONS_URL = (
    'https://git.almalinux.org/almalinux/build-node-exclusions/raw/branch/main/'
)
DEFAULT_CACHE_SIZE = 1
DEFAULT_NUMA_AWARE = False

__all__ = ['BuildNodeConfig', 'detect_pesign_support']


def detect_pesign_support() -> bool:
    """
    Checks whether the build node can sign secure boot builds: the pesign
    scripts are installed on the host (albs-deploy installs them on every
    build node), see constants.PESIGN_CLIENT_PATH.

    Returns
    -------
    bool
        True if pesign-client is an executable file, False otherwise.
    """
    path = constants.PESIGN_CLIENT_PATH
    return os.path.isfile(path) and os.access(path, os.X_OK)


class BuildNodeConfig(BaseConfig):
    """
    Build node configuration storage.

    Attributes
    ----------
    development_mode : bool
        Enable development mode if True. In that mode no SSL verification will
        be performed. Please, NEVER USE IT FOR PRODUCTION.
    master_url : str
        Build server connection URL.
    npm_proxy : str
        NPM (Yarn) proxy URL.
    node_id : str
        Current build node identifier, hostname.pid unless it is set
        explicitly, so it can stay the same across restarts. See instance_id
        for the identifier of the running process.
    threads_count : int
        The number of build threads, it is also the number of tasks the node
        takes from the build server at once.
    queue_size : int
        Local build tasks queue size, always equal to ``threads_count``. The
        ``queue_size`` configuration option is obsolete and ignored, it is
        still accepted to keep old configuration files valid.
    working_dir : str
        Build node working directory path. The directory will be used for
        temporary files storage.
    maintenance_mode_file : str
        Path to maintenance mode file. If the file exists, the build node will
        not request new tasks.
    git_cache_locks_dir : str
        Git repositories cache locks directory.
    git_repos_cache_dir : str
        Git repositories cache directory.
    git_extra_options : list of str
        Git options to be passed to underlying git commands
    sentry_dsn : str
        Client key to send build data to Sentry.
    pulp_host : str
        HTTP address of Pulp server.
    pulp_user : str
        Pulp username.
    pulp_password : str
        Pulp password.
    pulp_chunk_size : int
        Size of file chunk to upload through Pulp.
    pulp_uploader_max_workers : int
        Maximum number of parallel workers when uploading content to Pulp.
    pulp_timeout : int
        Timeout of every request to Pulp, in seconds.
    pulp_upload_retries : int
        How many times a file upload is started over after a connection
        error, a timeout or a server error.
    notarization_workers : int
        Number of threads notarizing build artifacts in immudb. Each
        notarization is a few network round trips, so this is bound by
        latency to immudb, not by CPU.
    pesign_support : bool
        The build node signs secure boot builds, the build server gives
        secure boot tasks only to such nodes. Unless it is set explicitly
        (true or false), it is detected at start: True if
        /opt/pesign/pesign-client is installed and executable. See
        pesign_support_source.
    numa_aware : bool
        Pin each build thread (and the mock processes it spawns) to a single
        NUMA node. Disabled by default: on x86_64 and ppc64le hosts builds
        perform fine spanning all nodes, so enable it only where cross-node
        traffic hurts, which so far is aarch64 only.
    """

    def __init__(self, config_file=None, **cmd_args):
        """
        Build node configuration initialization.

        Parameters
        ----------
        config_file : str, optional
            Configuration file path.
        cmd_args : dict
            Command line arguments.
        """
        # Random, so the instance id of every start of the node is unique:
        # the pid of the node process is the same on every start in a
        # container, and node_id can be set explicitly
        self.__run_id = secrets.token_hex(4)
        default_config = {
            'development_mode': False,
            'master_url': DEFAULT_MASTER_URL,
            'npm_proxy': '',
            'node_id': self.generate_node_id(),
            'threads_count': DEFAULT_THREADS_COUNT,
            'working_dir': DEFAULT_WORKING_DIR,
            'maintenance_mode_file': DEFAULT_MAINTENANCE_MODE_FILE,
            # NOTE: those parameters are added for old Build System code
            #       compatibility
            'git_cache_locks_dir': '/srv/alternatives/git_repos_cache/locks/',
            'git_repos_cache_dir': '/srv/alternatives/git_repos_cache/',
            'git_extra_options': None,
            'native_support': DEFAULT_NATIVE_BUILDING,
            'arm64_support': DEFAULT_ARM64_BUILDING,
            'arm32_support': DEFAULT_ARM32_BUILDING,
            'pesign_support': DEFAULT_PESIGN_SUPPORT,
            'node_type': DEFAULT_NODE_TYPE,
            'sentry_dsn': DEFAULT_SENTRY_DSN,
            'sentry_environment': DEFAULT_SENTRY_ENVIRONMENT,
            'sentry_traces_sample_rate': DEFAULT_SENTRY_TRACES_SAMPLE_RATE,
            'pulp_host': DEFAULT_PULP_HOST,
            'pulp_user': DEFAULT_PULP_USER,
            'pulp_password': DEFAULT_PULP_PASSWORD,
            'pulp_chunk_size': DEFAULT_PULP_CHUNK_SIZE,
            'pulp_uploader_max_workers': DEFAULT_PULP_UPLOADER_MAX_WORKERS,
            'pulp_timeout': DEFAULT_PULP_TIMEOUT,
            'pulp_upload_retries': DEFAULT_PULP_UPLOAD_RETRIES,
            'mock_basedir': DEFAULT_MOCK_BASEDIR,
            'base_arch': platform.machine(),
            'build_src': True,
            'immudb_username': None,
            'immudb_password': None,
            'immudb_database': None,
            'immudb_address': None,
            'immudb_public_key_file': None,
            'notarization_workers': DEFAULT_NOTARIZATION_WORKERS,
            'request_timeout': DEFAULT_REQUEST_TIMEOUT,
            'jwt_token': DEFAULT_JWT_TOKEN,
            'cache_update_interval': DEFAULT_CACHE_UPDATE_INTERVAL,
            'exclusions_url': DEFAULT_EXCLUSIONS_URL,
            'build_node_name': self.get_node_name(),
            'cache_size': DEFAULT_CACHE_SIZE,
            'numa_aware': DEFAULT_NUMA_AWARE,
        }
        schema = {
            'development_mode': {'type': 'boolean', 'default': False},
            'master_url': {'type': 'string', 'required': True},
            'npm_proxy': {'type': 'string'},
            'node_id': {'type': 'string', 'required': True},
            'threads_count': {'type': 'integer', 'min': 1, 'required': True},
            # Obsolete, see the queue_size property
            'queue_size': {'type': 'integer', 'nullable': True},
            'working_dir': {'type': 'string', 'required': True},
            'maintenance_mode_file': {'type': 'string', 'required': True},
            'git_cache_locks_dir': {'type': 'string', 'required': True},
            'git_repos_cache_dir': {'type': 'string', 'required': True},
            'git_extra_options': {
                'type': 'list',
                'required': False,
                'nullable': True,
            },
            'native_support': {'type': 'boolean', 'default': True},
            'arm64_support': {'type': 'boolean', 'default': False},
            'arm32_support': {'type': 'boolean', 'default': False},
            # None (not set) is replaced with the detected value
            'pesign_support': {'type': 'boolean', 'nullable': True},
            'node_type': {'type': 'string', 'nullable': True},
            'sentry_dsn': {'type': 'string', 'nullable': True},
            'sentry_environment': {'type': 'string', 'nullable': True},
            'sentry_traces_sample_rate': {'type': 'float', 'nullable': True},
            'pulp_host': {'type': 'string', 'nullable': False},
            'pulp_user': {'type': 'string', 'nullable': False},
            'pulp_password': {'type': 'string', 'nullable': False},
            'pulp_chunk_size': {'type': 'integer', 'nullable': False},
            'pulp_uploader_max_workers': {'type': 'integer', 'nullable': False},
            'mock_basedir': {'type': 'string', 'nullable': True},
            'jwt_token': {'type': 'string', 'required': True},
            'immudb_username': {'type': 'string', 'nullable': True},
            'immudb_password': {'type': 'string', 'nullable': True},
            'immudb_database': {'type': 'string', 'nullable': True},
            'immudb_address': {'type': 'string', 'nullable': True},
            'immudb_public_key_file': {'type': 'string', 'nullable': True},
            'notarization_workers': {'type': 'integer', 'min': 1},
            'base_arch': {'type': 'string', 'nullable': False},
            'build_src': {'type': 'boolean', 'nullable': False},
            'pulp_timeout': {
                'type': 'integer',
                'min': 1,
                'required': True,
            },
            'pulp_upload_retries': {'type': 'integer', 'min': 0},
            'request_timeout': {'type': 'integer', 'required': True},
            'cache_update_interval': {'type': 'integer', 'required': True},
            'exclusions_url': {'type': 'string', 'required': True},
            'build_node_name': {'type': 'string', 'required': True},
            'cache_size': {'type': 'integer', 'required': True},
            'numa_aware': {'type': 'boolean', 'default': DEFAULT_NUMA_AWARE},
        }
        super(BuildNodeConfig, self).__init__(
            default_config, config_file, schema, **cmd_args
        )
        # An explicit false is kept: the node may have pesign installed, but
        # be not allowed to sign
        if self._config['pesign_support'] is None:
            self._config['pesign_support'] = detect_pesign_support()
            self.__pesign_support_source = 'autodetect'
        else:
            self.__pesign_support_source = 'config'

    @property
    def instance_id(self) -> str:
        """
        Identifier of this start of the build node process: node_id followed
        by a random run id, e.g. "node-1.1234.0f3a9c2e". The build server
        tells the processes which share the same node name from each other
        by it, and a node restart from a second process.

        Returns
        -------
        str
        """
        return f'{self.node_id}.{self.__run_id}'

    @property
    def instance_id_file(self) -> str:
        """
        State file which keeps the instance id of the last started build node
        process, it is in the working directory: the working directory
        persists across the node restarts.

        Returns
        -------
        str
        """
        return os.path.join(self.working_dir, constants.INSTANCE_ID_FILE)

    @property
    def pesign_support_source(self) -> str:
        """
        Where the pesign_support value comes from: "config" if it is set
        explicitly in the configuration, "autodetect" if it is detected by
        the pesign installation.

        Returns
        -------
        str
        """
        return self.__pesign_support_source

    @property
    def queue_size(self) -> int:
        """
        Local build tasks queue size, equal to the number of build threads:
        the node does not take more tasks than it can build at once.

        Returns
        -------
        int
        """
        return self.threads_count

    @property
    def codenotary_enabled(self) -> bool:
        return bool(self.immudb_username) and bool(self.immudb_password)

    @property
    def mock_configs_storage_dir(self):
        """
        Mock environments configuration files storage directory.

        Returns
        -------
        str
        """
        return os.path.join(self.working_dir, 'mock_configs')
