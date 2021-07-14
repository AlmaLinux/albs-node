# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2017-12-27

"""Classes to work with Pbuilder environment"""

import copy
import logging
import os
import re
import shutil
import tempfile

import lmdb
import plumbum

from build_node.utils.proc_utils import get_current_thread_ident
from build_node.utils.file_utils import safe_mkdir, filter_files
from build_node.utils.locking import generic_lock
from build_node.pbuilder.pbuilder_config import PbuilderConfig

__all__ = ['PbuilderResult', 'PbuilderError', 'PbuilderSupervisor']


class PbuilderResult(object):

    """Pbuilder execution result object"""

    def __init__(self, command, exit_code, stdout, stderr, pbuilder_config,
                 apt_dir, resultdir=None):
        """
        Pbuilder command execution result initialization.

        Parameters
        ----------
        command : str
            Executed pbuilder command.
        exit_code : int
            Pbuilder command exit code.
        stdout : str
            Pbuilder command stdout.
        stderr : str
            Pbuilder command stderr.
        pbuilder_config : str
            Pbuilder configuration file content.
        apt_dir : str
            Path to the directory with apt.conf file.
        resultdir : str, optional
            Output directory.
        """
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.pbuilder_config = pbuilder_config
        self.apt_dir = apt_dir
        self.resultdir = resultdir

    @property
    def apt_conf_path(self):
        """
        apt.conf path.

        Returns
        -------
        str
        """
        if self.apt_dir:
            cfg_path = os.path.join(self.apt_dir, 'apt.conf')
            if os.path.exists(cfg_path):
                return cfg_path

    @property
    def log(self):
        if not self.resultdir:
            return None
        return next(iter(filter_files(self.resultdir,
                                      lambda f: f.endswith('.log'))), None)

    @property
    def debs(self):
        if not self.resultdir:
            return []
        return filter_files(self.resultdir, lambda f: f.endswith('.deb'))

    @property
    def changes(self):
        if not self.resultdir:
            return []
        return filter_files(self.resultdir, lambda f: f.endswith('.changes'))

    @property
    def sources(self):
        if not self.resultdir:
            return []
        return filter_files(self.resultdir,
                            lambda f: re.search(r'\.(dsc|gz|bz2|xz)$', f))

    @property
    def artifacts(self):
        if not self.resultdir:
            return []
        # TODO: Since we don't have any logic for processing/releasing
        #       debian sources, we're uploading only deb/dsc packages.
        #       If you want to save debian sources properly, please check
        #       that AL-4617 already resolved.
        return filter_files(
            self.resultdir,
            lambda f: re.search(r'\.(deb|dsc)$', f)
        )


class PbuilderError(Exception, PbuilderResult):

    def __init__(self, command, exit_code, stdout, stderr, pbuilder_config,
                 apt_dir, resultdir=None, message=None):
        """
        Pbuilder command execution result initialization.

        Parameters
        ----------
        command : str
            Executed pbuilder command.
        exit_code : int
            Pbuilder command exit code.
        stdout : str
            Pbuilder command stdout.
        stderr : str
            Pbuilder command stderr.
        pbuilder_config : str
            Pbuilder configuration file content.
        apt_dir : str
            Path to the directory with apt.conf file.
        resultdir : str, optional
            Output directory.
        """
        if not message:
            message = 'command "{0}" returned {1}'.format(command, exit_code)
            Exception.__init__(self, message)
            PbuilderResult.__init__(self, command, exit_code, stdout, stderr,
                                    pbuilder_config, apt_dir, resultdir)


class PbuilderSupervisor(object):

    """Pbuilder environment supervisor"""

    def __init__(self, storage_dir):
        """

        Parameters
        ----------
        storage_dir : str or unicode
            Directory to store pbuilder configs and LMDB database
        """
        self.__storage_dir = storage_dir
        self.__log = logging.getLogger(self.__module__)
        self.__db = self.__init_storage()

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
            self.__log.info('initializing pbuilder supervisor storage in the '
                            '{0} directory'.format(self.__storage_dir))
            safe_mkdir(self.__storage_dir)
        return lmdb.open(os.path.join(self.__storage_dir,
                                      'pbuilder_supervisor.lmdb'), max_dbs=1)

    @staticmethod
    def init_update_env(config_dict, apt_dir, extra_repos=None):
        """
        Initializes or updates base environment image.

        Parameters
        ----------
        config_dict :   dict
            Pbuilder config dictionary
        apt_dir : str
            Path to the directory with apt.conf file.
        extra_repos :   str or None, optional

        Raises
        ------
        build_node.errors.CommandExecutionError
            If pbuilder command failed.
        """
        base_tgz = config_dict.get('BASETGZ')
        action = '--update' if os.path.exists(base_tgz) else '--create'
        env = {'HISTFILE': '/dev/null', 'LANG': 'en_US.UTF-8'}
        with tempfile.NamedTemporaryFile(
                prefix="alt_pbuilder_", mode='w+t') as fd:
            fd.write(PbuilderConfig.generate_config(config_dict))
            fd.flush()
            args = ['pbuilder', action, '--configfile', fd.name,
                    '--override-config', '--aptconfdir', apt_dir]
            if extra_repos:
                args.extend(['--othermirror', extra_repos])
            pbuilder = plumbum.local['sudo'][args]
            exit_code, stdout, stderr = pbuilder.run(env=env, retcode=None)
            if exit_code != 0:
                fd.seek(0)
                raise PbuilderError(' '.join(pbuilder.formulate()), exit_code,
                                    stdout, stderr, fd.read(), apt_dir)

    def environment(self, config, result_dir, apt_dir, build_dir):
        """
        Returns prepared Debian/Ubuntu build environment.

        Parameters
        ----------
        config : PbuilderConfig
            Config dictionary from PbuilderConfig.
        result_dir : str
            Path to result directory.
        apt_dir : str
            Path to the directory with apt.conf file.
        build_dir : str
            Path to directory where build process should happen.

        Returns
        -------
        tuple
            Configuration dictionary and ath to config file.

        """
        config_dict = copy.copy(config.config_dict)
        basetgz_path = config_dict.get('BASETGZ')
        aptcache_path = config_dict.get('APTCACHE')
        task_dir = os.path.dirname(result_dir)
        task_basetgz_path = os.path.join(
            task_dir, os.path.basename(basetgz_path))
        task_aptcache_path = os.path.join(
            task_dir, os.path.basename(aptcache_path))

        with generic_lock(self.__db, 'locks', config.id,
                          get_current_thread_ident()):
            self.init_update_env(config_dict, apt_dir)
            # Make a task-only copy of base image and APT cache files
            shutil.copy(basetgz_path, task_basetgz_path)
            shutil.copytree(aptcache_path, task_aptcache_path)

        config_dict['BASETGZ'] = task_basetgz_path
        config_dict['APTCACHE'] = task_aptcache_path
        config_dict['BUILDRESULT'] = result_dir
        config_dict['BUILDPLACE'] = build_dir
        # When building package of non-native architecture, return
        # dependency solver to experimental
        if config_dict.get('ARCH', '') == 'armhf':
            config_dict['PBUILDERSATISFYDEPENDSCMD'] = \
                '/usr/lib/pbuilder/pbuilder-satisfydepends-experimental'
        config_file = os.path.join(result_dir, "build.cfg")
        with open(config_file, 'w') as fd:
            fd.write(config.generate_config(config_dict))
        return config_dict, config_file

    @property
    def db(self):
        return self.__db
