# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-10-19

"""
CloudLinux Build System build node configuration storage.
"""

import os
import platform
import re

from build_node.errors import ConfigurationError
from build_node.utils.config import BaseConfig
from build_node.utils.file_utils import normalize_path

DEFAULT_MASTER_URL = 'tcp://127.0.0.1:32167'
DEFAULT_THREADS_COUNT = 4
DEFAULT_WORKING_DIR = '/srv/alternatives/castor/build_node'
DEFAULT_SENTRY_DSN = None
DEFAULT_NATIVE_BUILDING = True
DEFAULT_ARM64_BUILDING = False
DEFAULT_ARM32_BUILDING = False
DEFAULT_PESIGN_SUPPORT = False
DEFAULT_NODE_TYPE = 'hard'

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
    master_key_path : str
        Build master public ZeroMQ Curve key path.
    public_key_path : str
        Build node public ZeroMQ Curve key path.
    private_key_path : str
        Build node private ZeroMQ Curve key path.
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
        self.__jwt_token = None
        default_config = {
            'development_mode': False,
            'master_key_path': '~/.config/castor/build_node/build_server.key',
            'master_url': DEFAULT_MASTER_URL,
            'npm_proxy': '',
            'private_key_path': '~/.config/castor/build_node/'
                                '{0}.key_secret'.format(platform.node()),
            'public_key_path': '~/.config/castor/build_node/'
                               '{0}.key'.format(platform.node()),
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
            'sentry_dsn': DEFAULT_SENTRY_DSN
        }
        schema = {
            'development_mode': {'type': 'boolean', 'default': False},
            'master_key_path': {'type': 'string', 'required': True,
                                'coerce': normalize_path,
                                'zmq_public_key': True},
            'master_url': {'type': 'string', 'required': True},
            'npm_proxy': {'type': 'string'},
            'private_key_path': {'type': 'string', 'required': True,
                                 'coerce': normalize_path,
                                 'zmq_private_key': True},
            'public_key_path': {'type': 'string', 'required': True,
                                'coerce': normalize_path,
                                'zmq_public_key': True},
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
        }
        super(BuildNodeConfig, self).__init__(default_config, config_file,
                                              schema, **cmd_args)

    @property
    def jwt_token(self):
        """
        Returns a build node JWT authentication token which is required for
        repositories access.

        The token will be extracted from the ZeroMQ Curve private key file on
        first call.

        Returns
        -------
        str
            JWT authentication token.
        """
        if not self.__jwt_token:
            with open(self.private_key_path, 'r') as fd:
                for line in fd:
                    re_rslt = re.search(r'^\s*jwt_token\s*=\s*(\S+)\s*$', line)
                    if re_rslt:
                        self.__jwt_token = re_rslt.group(1)
                        break
            if not self.__jwt_token:
                raise ConfigurationError('JWT token is not found')
        return self.__jwt_token

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
