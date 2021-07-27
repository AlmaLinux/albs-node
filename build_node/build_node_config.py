# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-10-19

"""
CloudLinux Build System build node configuration storage.
"""

import os

from build_node.utils.config import BaseConfig

DEFAULT_MASTER_URL = 'http://web_server:8000/'
DEFAULT_THREADS_COUNT = 4
DEFAULT_WORKING_DIR = '/srv/alternatives/castor/build_node'
DEFAULT_SENTRY_DSN = None
DEFAULT_NATIVE_BUILDING = True
DEFAULT_ARM64_BUILDING = False
DEFAULT_ARM32_BUILDING = False
DEFAULT_PESIGN_SUPPORT = False
DEFAULT_NODE_TYPE = 'hard'
DEFAULT_PULP_HOST = 'http://pulp'
DEFAULT_PULP_USER = 'pulp'
DEFAULT_PULP_PASSWORD = 'test_pwd'
DEFAULT_PULP_CHUNK_SIZE = 8388608  # 8 MiB

__all__ = ['BuildNodeConfig']


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
        Current build node unique identifier.
    threads_count : int
        The number of build threads.
    working_dir : str
        Build node working directory path. The directory will be used for
        temporary files storage.
    git_cache_locks_dir : str
        Git repositories cache locks directory.
    git_repos_cache_dir : str
        Git repositories cache directory.
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
        default_config = {
            'development_mode': False,
            'master_url': DEFAULT_MASTER_URL,
            'npm_proxy': '',
            'node_id': self.generate_node_id(),
            'threads_count': DEFAULT_THREADS_COUNT,
            'working_dir': DEFAULT_WORKING_DIR,
            # NOTE: those parameters are added for old Build System code
            #       compatibility
            'git_cache_locks_dir': '/srv/alternatives/git_repos_cache/locks/',
            'git_repos_cache_dir': '/srv/alternatives/git_repos_cache/',
            'native_support': DEFAULT_NATIVE_BUILDING,
            'arm64_support': DEFAULT_ARM64_BUILDING,
            'arm32_support': DEFAULT_ARM32_BUILDING,
            'pesign_support': DEFAULT_PESIGN_SUPPORT,
            'node_type': DEFAULT_NODE_TYPE,
            'sentry_dsn': DEFAULT_SENTRY_DSN,
            'pulp_host': DEFAULT_PULP_HOST,
            'pulp_user': DEFAULT_PULP_USER,
            'pulp_password': DEFAULT_PULP_PASSWORD,
            'pulp_chunk_size': DEFAULT_PULP_CHUNK_SIZE,
        }
        schema = {
            'development_mode': {'type': 'boolean', 'default': False},
            'master_url': {'type': 'string', 'required': True},
            'npm_proxy': {'type': 'string'},
            'node_id': {'type': 'string', 'required': True},
            'threads_count': {'type': 'integer', 'min': 1, 'required': True},
            'working_dir': {'type': 'string', 'required': True},
            'git_cache_locks_dir': {'type': 'string', 'required': True},
            'git_repos_cache_dir': {'type': 'string', 'required': True},
            'native_support': {'type': 'boolean', 'default': True},
            'arm64_support': {'type': 'boolean', 'default': False},
            'arm32_support': {'type': 'boolean', 'default': False},
            'pesign_support': {'type': 'boolean', 'default': False},
            'node_type': {'type': 'string', 'nullable': True},
            'sentry_dsn': {'type': 'string', 'nullable': True},
            'pulp_host': {'type': 'string', 'nullable': False},
            'pulp_user': {'type': 'string', 'nullable': False},
            'pulp_password': {'type': 'string', 'nullable': False},
            'pulp_chunk_size': {'type': 'integer', 'nullable': False},
            'jwt_token': {'type': 'string', 'nullable': True}
        }
        super(BuildNodeConfig, self).__init__(default_config, config_file,
                                              schema, **cmd_args)

    @property
    def mock_configs_storage_dir(self):
        """
        Mock environments configuration files storage directory.

        Returns
        -------
        str
        """
        return os.path.join(self.working_dir, 'mock_configs')

    @property
    def pbuilder_configs_storage_dir(self):
        """
        Pbuilder environments storage directory

        Returns
        -------
        str
        """
        return os.path.join(self.working_dir, 'pbuilder_envs')
