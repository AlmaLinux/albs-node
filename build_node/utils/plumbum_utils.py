# -*- mode:python; coding:utf-8; -*-
# author: Potoropin Vyacheslav <vpotoropin@cloudlinux.com>
# created: 2020-04-11

import os
import time
import socket
import logging
import functools
import traceback

import paramiko
from paramiko.ssh_exception import SSHException
from plumbum.machines.paramiko_machine import ParamikoMachine
from plumbum.commands.base import BoundCommand, BaseCommand


__all__ = ['RetryParamikoMachine']

"""
This module provides plumbum.SshMachine with retries on every invoked command.

For additional info check out plumbum source code:
    https://github.com/tomerfiliba/plumbum/
"""


class RetryBaseCommand(BaseCommand):

    def bound_command(self, *args):
        """Creates a bound-command with the given arguments"""
        if not args:
            return self
        if isinstance(self, RetryBoundCommand):
            return RetryBoundCommand(self.cmd, self.args + list(args))
        else:
            return RetryBoundCommand(self, args)

    def run(self, *args, **kwargs):
        # we want to save machine retry settings for every run here
        super_run = super(RetryBaseCommand, self).run
        retry_run = self.machine.retry_request_wrapper(super_run)
        return retry_run(*args, **kwargs)


class RetryBoundCommand(RetryBaseCommand, BoundCommand):
    pass


class RetryRemoteCommand(RetryBaseCommand, ParamikoMachine.RemoteCommand):
    def __or__(self, *_):
        raise NotImplementedError('Not supported with RetryParamikoMachine')

    def __gt__(self, *_):
        raise NotImplementedError('Not supported with RetryParamikoMachine')

    def __rshift__(self, *_):
        raise NotImplementedError('Not supported with RetryParamikoMachine')

    def __ge__(self, *_):
        raise NotImplementedError('Not supported with RetryParamikoMachine')

    def __lt__(self, *_):
        raise NotImplementedError('Not supported with RetryParamikoMachine')

    def __lshift__(self, *_):
        raise NotImplementedError('Not supported with RetryParamikoMachine')


class RetryParamikoMachine(ParamikoMachine):

    # This command will be returned on __getitem__ call
    # For additional info see plumbum.machines.remote.BaseRemoteMachine:
    # https://github.com/tomerfiliba/plumbum/
    RemoteCommand = RetryRemoteCommand

    def __init__(self, host, **kwargs):
        self._log = kwargs.pop('retry_logger', None)
        if not self._log:
            self._log = logging.getLogger('castor-releaser')
        self._retry_count = kwargs.pop('retry_count', 10)
        self._retry_timeout = kwargs.pop('retry_timeout', 60)
        self._retry_backoff_factor = kwargs.pop('retry_backoff_factor', 5)
        self.host, self._retry_kwargs = self._load_system_ssh_config(
            host, kwargs)
        self._retry_kwargs['missing_host_policy'] = paramiko.AutoAddPolicy()
        self.download = self.retry_request_wrapper(self.download)
        self.upload = self.retry_request_wrapper(self.upload)
        self._path_listdir = self.retry_request_wrapper(self._path_listdir)
        self._path_read = self.retry_request_wrapper(self._path_read)
        self._path_write = self.retry_request_wrapper(self._path_write)
        self._path_stat = self.retry_request_wrapper(self._path_stat)
        # To prevent infinite recursion we wrap it only for one call
        retry_init = self.retry_request_wrapper(
            self._retry_init, only_connect=True)
        retry_init()

    def _retry_init(self):
        """
        Perform call to ParamikoMachine.__init__() to create new connection.
        """
        super(RetryParamikoMachine, self).__init__(
            self.host, **self._retry_kwargs)
        self.sftp.get_channel().settimeout(self._retry_timeout)

    def retry_request_wrapper(self, func, only_connect=False):
        """
        Wrap function call with retry settings.

        Parameters
        ----------
        func : function
            Repository base path.
        only_connect : bool, optional
            If true only new connection will be initiated.

        Returns
        -------
        function
            Wrapped function.
        """
        @functools.wraps(func)
        def wrapped_request(*args, **kwargs):
            last_exception = None
            last_traceback = None
            connected = True
            if only_connect:
                connected = False
            for retry_index in range(self._retry_count):
                try:
                    if not connected:
                        self._retry_init()
                        if only_connect:
                            return
                    return func(*args, **kwargs)
                except (OSError, SSHException, socket.error) as e:
                    last_exception = e
                    last_traceback = traceback.format_exc()
                    connected = False
                if retry_index != self._retry_count - 1:
                    backoff_timeout = self._retry_backoff_factor * retry_index
                    self._log.debug(
                        'Remote operation failed ({0}). Trying again after '
                        '{1} seconds'.format(retry_index + 1, backoff_timeout)
                    )
                    time.sleep(backoff_timeout)
            self._log.error(last_traceback)
            raise last_exception
        return wrapped_request

    @staticmethod
    def _load_system_ssh_config(host, kwargs):
        """
        Load system ssh configuration like from ~/.ssh/config.

        Parameters
        ----------
        host : str
            Hostname.
        kwargs : dict
            Host arguments (won't be replaced if any).

        Returns
        -------
        tuple (str, dict)
            Hostname and updated host args.
        """
        if not kwargs.get('load_system_ssh_config'):
            return host, kwargs
        ssh_config = paramiko.SSHConfig()
        ssh_config_path = os.path.expanduser('~/.ssh/config')
        if not os.path.exists(ssh_config_path):
            return host, kwargs
        with open(ssh_config_path) as f:
            ssh_config.parse(f)
        host_config = ssh_config.lookup(host)
        for key in ('user', 'port'):
            if not kwargs.get(key) and key in host_config:
                kwargs[key] = host_config.get(key)
        if 'identityfile' in host_config and not kwargs.get('keyfile'):
            kwargs['keyfile'] = host_config['identityfile'][0]
        host = host_config['hostname']
        return host, kwargs
