# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-09-28

"""
mock environments orchestration module.
"""

import os
import re
import struct
import time
import logging

import lmdb

from build_node.utils.proc_utils import get_current_thread_ident, is_pid_exists
from .mock_environment import MockEnvironment, MockError


__all__ = ['MockSupervisor', 'MockSupervisorError']


class MockSupervisorError(Exception):

    """mock supervisor execution error."""

    pass


class MockSupervisor(object):

    def __init__(self, storage_dir, idle_time=7200, refresh_time=86400):
        """
        mock environments supervisor initialization.

        Parameters
        ----------
        storage_dir : str
            Working directory path. It will be used for mock configuration
            files and the supervisor database storage.
        idle_time : int, optional
            Maximum allowed idle time for a mock environment. Unused mock
            environments will be deleted after that period.
        refresh_time : int, optional
            Maximum allowed lifetime (in seconds) for a mock environment. The
            environment will be regenerated after that period even if it is
            actively used. Default value is 24 hours.
        """
        self.__log = logging.getLogger(self.__module__)
        self.__storage_dir = storage_dir
        self.__idle_time = idle_time
        self.__refresh_time = refresh_time
        self.__db = self.__init_storage()

    def environment(self, mock_config):
        """
        Finds a free mock environment for the specified configuration or
        creates it.

        Parameters
        ----------
        mock_config : build_node.mock.mock_config.MockConfig
            mock environment configuration.

        Returns
        -------
        MockEnvironment
            Initialized mock environment.
        """
        config_hash = mock_config.config_hash
        with self.__db.begin(write=True) as txn:
            existent_configs = self.__find_existent_configs(config_hash)
            locks_db = self.__db.open_db(b'locks', txn=txn)
            #
            generate = False
            config_file = next((c for c in existent_configs
                                if not txn.get(c.encode('utf-8'),
                                               db=locks_db)), None)
            if not config_file:
                i = 0
                while True:
                    config_file = '{0}.{1}.cfg'.format(config_hash, i)
                    if config_file in existent_configs:
                        i += 1
                        continue
                    generate = True
                    break
            root = self.__get_mock_root_name(config_file)
            config_path = os.path.join(self.__storage_dir, config_file)
            if generate:
                mock_config.dump_to_file(config_path, root)
            txn.put(
                config_file.encode('utf-8'),
                get_current_thread_ident(),
                db=locks_db)
            self.__update_usage_count(txn, config_file.encode('utf-8'))
            self.__cleanup_environments(txn)
            return MockEnvironment(self, config_path, root)

    def free_environment(self, environment):
        """
        Marks the specified mock environment as free so it can be used again.

        Parameters
        ----------
        environment : MockEnvironment
            mock environment.
        """
        config_file = os.path.split(environment.config_path)[1]
        try:
            environment.clean()
        except MockError as e:
            self.__log.error('{0} mock environment cleanup failed with {1} '
                             'exit code: {2}'.format(config_file, e.exit_code,
                                                      e.stderr))
        with self.__db.begin(write=True) as txn:
            locks_db = self.__db.open_db(b'locks', txn=txn)
            txn.put(config_file.encode('utf-8'), b'', dupdata=False, db=locks_db)
            stats_db = self.__db.open_db(b'stats', txn=txn)
            self.__update_usage_timestamp(txn, stats_db, config_file)
            self.__cleanup_environments(txn)

    def __cleanup_environments(self, txn):
        """
        Deletes idle or expired mock environments, marks as free environments
        which were used by dead processes.

        Parameters
        ----------
        txn : lmdb.Transaction
            LMDB database transaction to use.
        """
        stats_db = self.__db.open_db(b'stats', txn=txn)
        locks_cursor = txn.cursor(db=self.__db.open_db(b'locks', txn=txn))
        for config_file, lock_data in locks_cursor.iternext():
            if lock_data:
                # check if an environment owning process is still active,
                # mark the environment as free if not
                pid, thread_name = struct.unpack('i20p', lock_data)
                if is_pid_exists(pid):
                    # we can't cleanup an environment which is used by some
                    # process, just skip it
                    continue
                self.__log.warning('{0} mock environment is locked by dead '
                                   'process {1}, marking it as free'.
                                   format(config_file, pid))
                locks_cursor.put(config_file,
                                 b'', dupdata=False)
            # an environment isn't used by any process, check if it's time to
            # cleanup it
            stats_data = txn.get(config_file, db=stats_db)
            if not stats_data:
                # statistics absence means a serious bug in our code or the
                # database corruption
                self.__raise_missing_stats_error(config_file)
            config_path = os.path.join(self.__storage_dir.encode('utf-8'), config_file)
            current_ts = round(time.time())
            creation_ts, usage_ts, _ = struct.unpack('iii', stats_data)
            if current_ts - usage_ts > self.__idle_time:
                # an environment is expired, delete it
                self.__log.info('deleting expired environment {0}'.
                                format(config_file))
                self.__scrub_mock_environment(config_path.decode('utf-8'))
                locks_cursor.pop(config_file)
                txn.delete(config_file, db=stats_db)
                if os.path.exists(config_path):
                    os.remove(config_path)
                continue
            elif current_ts - creation_ts > self.__refresh_time:
                # an environment is outdated, regenerate its cache
                self.__log.info('updating outdated environment {0}'.
                                format(config_file))
                self.__scrub_mock_environment(config_path)
                txn.put(config_file, struct.pack('iii', current_ts,
                                                 current_ts, 0), db=stats_db)

    def __generate_site_defaults_config(self):
        """
        Generate site-defaults.cfg in MockSupervisor working directory path
        """
        config_params = (
            # Secure Boot options
            'config_opts["macros"]["%__pesign_cert"] = "%pe_signing_cert"\n',
            'config_opts["macros"]["%__pesign_client_cert"] = "%pe_signing_cert"\n',
            'config_opts["macros"]["%__pesign_client_token"] = "%pe_signing_token"\n',
            'config_opts["macros"]["%__pesign_token"] = "-t %pe_signing_token"\n',
            'config_opts["plugin_conf"]["bind_mount_enable"] = True\n',
        )
        config_path = os.path.join(self.__storage_dir, 'site-defaults.cfg')
        self.__log.info(
            'generating site-defaults.cfg in the %s directory',
            self.__storage_dir,
        )
        with open(config_path, 'w') as config:
            config.writelines(config_params)

    def __init_storage(self):
        """
        Creates a mock supervisor storage directory and initializes an LMDB
        database in it.

        Returns
        -------
        lmdb.Environment
            Opened LMDB database.

        Notes
        -----
        The configuration directory should contain logging.ini and
        site-defaults.cfg mock configuration files, this function creates
        symlinks to the system files since we don't have any specific
        settings yet.
        """
        if not os.path.exists(self.__storage_dir):
            self.__log.info('initializing mock supervisor storage in the {0} '
                            'directory'.format(self.__storage_dir))
            os.makedirs(self.__storage_dir)
        self.__generate_site_defaults_config()
        log_file = 'logging.ini'
        dst = os.path.join(self.__storage_dir, log_file)
        src = os.path.join('/etc/mock/', log_file)
        if not os.path.exists(src):
            raise IOError("No such file or directory: '{}'".format(src))

        if not os.path.exists(dst):
            os.symlink(src, dst)
        return lmdb.open(os.path.join(self.__storage_dir,
                                      'mock_supervisor.lmdb'), max_dbs=2)

    def __find_existent_configs(self, config_hash):
        """
        Finds existent mock configuration files for the specified configuration
        hash.

        Parameters
        ----------
        config_hash : str
            mock environment configuration hash.

        Returns
        -------
        list
            List of existent mock configuration file names.
        """
        return [c for c in os.listdir(self.__storage_dir)
                if re.search(r'^{0}\.\d+\.cfg'.format(config_hash), c)]

    def __get_mock_root_name(self, config):
        if isinstance(config, bytes):
            config = config.decode('utf-8')
        return re.search(r'^(.*?\.\d+)\.cfg$', config).group(1)

    def __update_usage_count(self, txn, config_file):
        """
        Increments usage count and sets usage timestamp to the current time
        for the specified mock environment.

        Parameters
        ----------
        txn : lmdb.Transaction
            LMDB database transaction to use.
        config_file : str
            mock environment configuration.
        """
        stats_db = self.__db.open_db(b'stats', txn=txn)
        key = config_file
        creation_ts = usage_ts = round(time.time())
        usages = 0
        stats = txn.get(key, db=stats_db)
        if stats:
            creation_ts, _, usages = struct.unpack('iii', stats)
        txn.put(key, struct.pack('iii', creation_ts, usage_ts, usages + 1),
                db=stats_db)

    def __update_usage_timestamp(self, txn, stats_db, config_file):
        """
        Sets a mock environment last usage timestamp to the current time.

        Parameters
        ----------
        txn : lmdb.Transaction
            LMDB transaction.
        stats_db : lmdb._Database
            Statistics database.
        config_file : str
            mock environment configuration.
        """
        key = config_file.encode('utf-8')
        config_stats = txn.get(key, db=stats_db)
        if not config_stats:
            # statistics absence means a serious bug in our code or the
            # database corruption
            self.__raise_missing_stats_error(config_file)
        creation_ts, _, usages = struct.unpack('iii', config_stats)
        txn.put(key, struct.pack('iii', creation_ts,
                                 round(time.time()), usages), db=stats_db)

    def __raise_missing_stats_error(self, config_file):
        """
        Reports missing environment statistics data to the log and raises
        an error.

        Parameters
        ----------
        config_file : str
            mock environment configuration file name.

        Raises
        ------
        MockSupervisorError
        """
        err = 'there is no statistics found for {0} mock environment'.\
            format(config_file)
        self.__log.error('{0}. Please verify the database integrity'.
                         format(err))
        raise MockSupervisorError(err)

    def __scrub_mock_environment(self, config_path):
        """
        Completely removes mock environment's root and cache.

        Parameters
        ----------
        config_path : str
            mock environment configuration file path.
        """
        config_file = os.path.split(config_path)[1]
        mock_env = MockEnvironment(self, config_path,
                                   self.__get_mock_root_name(config_file))
        mock_env.scrub('all')
