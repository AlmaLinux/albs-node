# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2017-12-27

"""Basic class for all other builders"""


import copy
import datetime
from functools import wraps
import os
import traceback

import yaml

from build_node.mock.mock_environment import MockError
from build_node.mock.mock_config import (
    MockConfig, MockBindMountPluginConfig, MockChrootFile
)
from build_node.mock.yum_config import YumConfig, YumRepositoryConfig
from build_node.utils.file_utils import safe_mkdir, chown_recursive
from build_node.utils.git_utils import MirroredGitRepo, git_get_commit_id
from .. import build_node_globals as node_globals

__all__ = ['measure_stage', 'BaseBuilder']


def measure_stage(stage):
    """
    Records a stage start and end time.

    Parameters
    ----------
    stage : str
        Stage name.

    Returns
    -------

    """
    def wrapper(fn):
        @wraps(fn)
        def wrapped(self, *args, **kwargs):
            start_time = datetime.datetime.utcnow()
            try:
                return fn(self, *args, **kwargs)
            except Exception as e:
                print(str(e))
                traceback.print_exc()
                raise e
            finally:
                self._build_stats[stage] = {
                    'start_ts': start_time,
                    'end_ts': datetime.datetime.utcnow()
                }
        return wrapped
    return wrapper


class BaseBuilder(object):

    def __init__(self, config, logger, task, task_dir, artifacts_dir):
        """
        Builder initialization.

        Parameters
        ----------
        config : BuildNodeConfig
            Build node configuration object.
        logger : logging.Logger
            Current build thread logger.
        task : Task
            Build task.
        task_dir : str
            Build task working directory.
        artifacts_dir : str
            Build artifacts (src-RPM, RPM(s), logs, etc) output directory.
        """
        self.config = config
        self.logger = logger
        self.task = task
        self.task_dir = task_dir
        self.artifacts_dir = artifacts_dir
        # created git tag name
        self.created_tag = None
        self._build_stats = {}
        self._pre_build_hook_target_arch = self.config.base_arch

    def checkout_git_sources(self, git_sources_dir, ref):
        """
        Checkouts a project sources from the specified git repository.

        Parameters
        ----------
        git_sources_dir : str
            Target directory path.
        ref : TaskRef
            Git (gerrit) reference.

        Returns
        -------
        cla.utils.alt_git_repo.WrappedGitRepo
            Git repository wrapper.
        """
        self.logger.info('checking out {0} from {1}'.format(
            ref.git_ref, ref.url))
        # FIXME: Understand why sometimes we hold repository lock more
        #  than 60 seconds
        with MirroredGitRepo(
                ref.url, self.config.git_repos_cache_dir,
                self.config.git_cache_locks_dir,
                timeout=600) as cached_repo:
            repo = cached_repo.clone_to(git_sources_dir)
            repo.checkout(ref.git_ref)
        self.__log_commit_id(git_sources_dir)
        return repo

    def get_build_stats(self):
        """
        Returns build time statistics.

        Returns
        -------
        dict
            Dictionary where keys are build stage names and values are tuples
            of start and end time.
        """
        return copy.copy(self._build_stats)

    @staticmethod
    def init_artifacts_dir(task_dir):
        """
        Creates a build task artifacts output directory.

        Parameters
        ----------
        task_dir : str
            Build task working directory.

        Returns
        -------
        str
            Build artifacts directory path.
        """
        artifacts_dir = os.path.join(task_dir, 'artifacts')
        os.makedirs(artifacts_dir)
        return artifacts_dir

    def build(self):
        """
        Builds binary packages from sources. Actual implementation is unknown.

        Raises
        ------
        NotImplementedError
        """
        raise NotImplementedError('build method is not implemented')

    def execute_pre_build_hook(self, git_sources_dir):
        """
        Executes a pre-build hook script "prep-build" in the specified
        directory using a CloudLinux 7 x86_64 stable chroot.

        Parameters
        ----------
        git_sources_dir : str
            Git repository path. A script will be executed in this directory.

        Notes
        -----
        This function will do nothing if a pre-build script does not exist.
        """
        script_path = os.path.join(git_sources_dir, 'buildsys-pre-build')
        if not os.path.exists(script_path):
            self.logger.debug('There is no buildsys-pre-build hook found')
            return
        mock_config = self._gen_pre_build_hook_mock_config(git_sources_dir)
        hook_result_dir = os.path.join(self.task_dir, 'pre_build_mock')
        safe_mkdir(hook_result_dir)
        hook_run_cmd = 'cd /srv/pre_build/ && ' \
                       'source /etc/profile.d/buildsys_vars.sh && ' \
                       './buildsys-pre-build'
        with self.mock_supervisor.environment(mock_config) as mock_env:
            try:
                for dep in self._get_pre_build_hook_deps(git_sources_dir):
                    self.logger.debug('installing buildsys-pre-build hook '
                                      'dependency {0}'.format(dep))
                    mock_env.install(dep, resultdir=hook_result_dir)
                rslt = mock_env.shell(hook_run_cmd, resultdir=hook_result_dir)
                self.logger.info('buildsys-pre-build hook has been '
                                 'successfully executed')
                self.logger.debug('buildsys-pre-build hook stdout:\n{0}'.
                                  format(rslt.stdout))
                self.logger.debug('buildsys-pre-build hook stderr:\n{0}'.
                                  format(rslt.stderr))
            except MockError as e:
                self.logger.error('buildsys-pre-build hook failed with {0} '
                                  'exit code'.format(e.exit_code))
                self.logger.error('buildsys-pre-build hook stdout:\n{0}'.
                                  format(e.stdout))
                self.logger.error('buildsys-pre-build hook stderr:\n{0}'.
                                  format(e.stderr))
                raise e
        chown_recursive(git_sources_dir)

    @staticmethod
    def _gen_pre_build_hook_profile(macros, platform, project_name):
        """
        Generates a bash profile with mock macro definitions for a pre-build
        hook environment.

        Parameters
        ----------
        macros : dict
            Mock macro definitions.
        platform : str
            Build system platform name.
        project_name : str
            Build system project name.

        Returns
        -------
        build_node.mock.mock_config.MockChrootFile
            Bash profile chroot file.
        """
        profile = '#!/bin/bash\n'
        export_template = 'export {0}="{1}"\n'
        for name, value in macros.items():
            profile += export_template.format(name, value)
        profile += export_template.format('BUILD_PLATFORM', platform)
        profile += export_template.format('BUILD_PROJECT', project_name)
        return MockChrootFile('etc/profile.d/buildsys_vars.sh', profile)

    def _gen_pre_build_hook_yum_config(self):
        """
        Generates yum configuration based on CloudLinux OS 7 x86_64 stable
        for a pre-build hook chroot environment.

        Returns
        -------
        build_node.mock.yum_config.YumConfig
            Yum configuration.
        """

        # FIXME: Make repository configs in smarter way to avoid errors with
        #  package installation
        if self._pre_build_hook_target_arch != 'x86_64':
            yum_repos = [
                YumRepositoryConfig(repositoryid='centos7-os', name='centos7-os',
                                    baseurl='http://mirror.centos.org/'
                                            'altarch/7/os/$basearch/',
                                    priority='10'),
                YumRepositoryConfig(repositoryid='centos7-updates',
                                    name='centos7-updates',
                                    baseurl='http://mirror.centos.org/altarch/7'
                                            '/updates/$basearch/', priority='10')
            ]
        else:
            yum_repos = [
                YumRepositoryConfig(repositoryid='cl7-os', name='cl7-os',
                                    baseurl='http://koji.cloudlinux.com/'
                                            'cloudlinux/7/os/x86_64/',
                                    priority='10'),
                YumRepositoryConfig(repositoryid='cl7-updates', name='cl7-updates',
                                    baseurl='http://koji.cloudlinux.com/'
                                            'cloudlinux/7/updates/x86_64/',
                                    priority='10')
            ]
        return YumConfig(repositories=yum_repos)

    def _gen_pre_build_hook_mock_config(self, git_sources_dir):
        """
        Generates mock configuration for a pre-build hook chroot environment.

        Parameters
        ----------
        git_sources_dir : str
            Git repository path.

        Returns
        -------
        build_node.mock.mock_config.MockConfig
            Mock configuration.
        """
        target_arch = self._pre_build_hook_target_arch
        yum_config = self._gen_pre_build_hook_yum_config()
        chroot_setup_cmd = (
            'install bash bzip2 zlib coreutils cpio diffutils '
            'findutils gawk gcc gcc-c++ grep gzip info '
            'make patch redhat-rpm-config rpm-build sed shadow-utils tar '
            'unzip util-linux-ng which xz scl-utils scl-utils-build'
        )
        if target_arch == 'x86_64':
            chroot_setup_cmd += ' cloudlinux-release'
        else:
            chroot_setup_cmd += ' centos-release'
        mock_config = MockConfig(target_arch=target_arch, dist='el7',
                                 chroot_setup_cmd=chroot_setup_cmd,
                                 use_boostrap_container=False,
                                 rpmbuild_networking=True,
                                 use_host_resolv=True,
                                 yum_config=yum_config,
                                 package_manager='yum',  # exactly yum, not dnf
                                 basedir=self.config.mock_basedir)
        bind_plugin = MockBindMountPluginConfig(True, [(git_sources_dir,
                                                        '/srv/pre_build/')])
        mock_config.add_plugin(bind_plugin)
        # FIXME:
        macros = self.task['build'].get('definitions')
        platform = self.task['meta']['platform']
        project_name = self.task['build']['project_name']
        mock_config.add_file(self._gen_pre_build_hook_profile(
            macros, platform, project_name))
        return mock_config

    @staticmethod
    def _get_pre_build_hook_deps(git_sources_dir):
        """
        Extracts a list of pre-build hook dependencies from a
        buildsys-pre-build.yml file located in the root of a repository.

        Parameters
        ----------
        git_sources_dir : str
            Git repository path.

        Returns
        -------
        list of str
            List of RPM package names to install before a pre-build hook
            execution.
        """
        config_path = os.path.join(git_sources_dir, 'buildsys-pre-build.yml')
        if not os.path.exists(config_path):
            return []
        with open(config_path, 'r') as fd:
            return yaml.Loader(fd).get_data().get('dependencies', [])

    def __log_commit_id(self, git_sources_dir):
        """
        Prints a current (HEAD) git repository commit id to a build log.

        Parameters
        ----------
        git_sources_dir : str
            Git repository path.
        """
        try:
            commit_id = git_get_commit_id(git_sources_dir)
            self.task.ref.git_commit_hash = commit_id
            self.logger.info('git commit id: {0}'.format(commit_id))
        except Exception as e:
            msg = 'can not get git commit id: {0}. Traceback:\n{1}'
            self.logger.error(msg.format(str(e), traceback.format_exc()))

    @property
    def build_timeout(self):
        """
        Build timeout in seconds.

        Returns
        -------
        int or None
        """
        return self.task.platform.data.get('timeout')

    @property
    def mock_supervisor(self):
        """
        Mock chroot environments supervisor.

        Returns
        -------
        build_node.mock.supervisor.MockSupervisor
        """
        return node_globals.MOCK_SUPERVISOR

    @property
    def pbuilder_supervisor(self):
        """
        Pbuilder chroot environments supervisor.

        Returns
        -------
        from build_node.pbuilder.pbuilder_environment.PbuilderSupervisor
        """
        return node_globals.PBUILDER_SUPERVISOR
