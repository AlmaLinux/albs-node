"""
Build System functions for working with configuration files.
"""

import datetime
import os
import platform

import cerberus
import yaml
from albs_common_lib.utils.file_utils import normalize_path


class ConfigValidator(cerberus.Validator):
    """
    Custom validator for AlmaLinux Build System configuration objects.
    """

    def _validate_type_timedelta(self, value):
        """
        Checks that the value is a datetime.timedelta instance.
        Parameters
        ----------
        value : datetime.timedelta
            Value to check.

        Returns
        -------
        bool
            True if the given value is a datetime.timedelta instance, False
            otherwise.
        """
        return isinstance(value, datetime.timedelta)


def locate_config_file(component, config_path=None):
    """
    Locates a Build System process configuration file in the following
    locations (ordered by priorities):

      1. user specified `config_path`
      2. default location `~/.config/castor/{component}.yml`

    Parameters
    ----------
    component : str
        Build System component name (e.g. build_master).
    config_path : str, optional
        User specified configuration file path.

    Returns
    -------
    str or None
        Existent configuration file path or None if user didn't provide it
        and default location is empty.

    Raises
    ------
    ValueError
        If configuration file provided by user doesn't exist.
    """
    if config_path:
        config_path = normalize_path(config_path)
        if not os.path.exists(config_path):
            raise ValueError(
                'configuration file {0} is not found'.format(config_path)
            )
        return config_path
    config_path = normalize_path('~/.config/castor/{0}.yml'.format(component))
    if os.path.exists(config_path):
        return config_path


class BaseConfig(object):
    """Base configuration object for Build System processes."""

    def __init__(
        self,
        default_config,
        config_path=None,
        schema=None,
        **cmd_args,
    ):
        """
        Configuration object initialization.

        Parameters
        ----------
        default_config : dict
            Default configuration values.
        config_path : str, optional
            Configuration file path.
        schema : dict, optional
            Validation schema compatible with Cerberus.
        cmd_args : dict
            Command line configuration arguments.

        Raises
        ------
        ValueError
            If configuration didn't pass validation.
        """
        self._config = default_config
        if config_path:
            self.__parse_config_file(config_path)
        for key, value in cmd_args.items():
            if value is not None and key in self._config:
                self._config[key] = value
        self.__validate_config(schema)

    @staticmethod
    def generate_node_id(postfix=''):
        """
        Generates a node identifier based on a hostname and a process PID.

        Parameters
        ----------
        postfix : str, optional
            Optional postfix to indicate a node purpose (e.g. ".build").

        Returns
        -------
        str
            Node identifier.
        """
        return '{0}.{1}{2}'.format(platform.node(), os.getpid(), postfix)

    @staticmethod
    def get_node_name():
        host_name = platform.node()
        return host_name.rsplit('.', 2)[0]

    def __dir__(self):
        return list(self._config.keys())

    def __getattr__(self, attr):
        if attr in self._config:
            return self._config[attr]
        raise AttributeError(attr)

    def __parse_config_file(self, config_path):
        with open(config_path, 'rb') as fd:
            config = yaml.safe_load(fd)
        if config:
            self._config.update(config)

    def __validate_config(self, schema):
        validator = ConfigValidator(schema or {})
        if not validator.validate(self._config):
            error_list = [
                '{0}: {1}'.format(k, ', '.join(v))
                for k, v in validator.errors.items()
            ]
            raise ValueError('. '.join(error_list))
        self._config = validator.document
