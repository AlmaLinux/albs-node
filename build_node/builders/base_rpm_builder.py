# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-10-24

"""
Base class for CloudLinux Build System RPM package builders.
"""

import datetime
import itertools
import os
import re
import shutil
import textwrap
import traceback
import time
import urllib.parse

import validators
import rpm

from build_node.builders.base_builder import measure_stage, BaseBuilder
from build_node.build_node_errors import (
    BuildError, BuildConfigurationError, BuildExcluded
)
from build_node.constants import REPO_CHANNEL_BUILD
from build_node.errors import DataNotFoundError
from build_node.mock.error_detector import build_log_excluded_arch
from build_node.mock.yum_config import YumConfig, YumRepositoryConfig
from build_node.mock.mock_config import (
    MockConfig, MockChrootFile, MockPluginConfig, MockBindMountPluginConfig)
from build_node.mock.mock_environment import MockError
from build_node.utils.debian_utils import dch_add_changelog_record
from build_node.utils.rpm_utils import unpack_src_rpm
from build_node.utils.spec_parser import RPMChangelogRecord
from build_node.utils.file_utils import download_file
from build_node.utils.spec_utils import (add_gerrit_ref_to_spec, find_spec_file,
                                     bump_release_spec_file,
                                     bump_version_datestamp_spec_file,
                                     add_changelog_spec_file,
                                     get_raw_spec_data, wipe_rpm_macro)

from build_node.utils.index_utils import extract_metadata
from build_node.utils.git_utils import git_commit, git_create_tag, git_push
from build_node.utils.spec_parser import SpecParser, SpecSource
from build_node.ported import to_unicode

__all__ = ['BaseRPMBuilder']


class BaseRPMBuilder(BaseBuilder):

    def __init__(self, config, logger, task, task_dir, artifacts_dir):
        """
        RPM builder initialization.

        Parameters
        ----------
        config : BuildNodeConfig
            Build node configuration object.
        logger : logging.Logger
            Current build thread logger.
        task : dict
            Build task.
        task_dir : str
            Build task working directory.
        artifacts_dir : str
            Build artifacts (src-RPM, RPM(s), logs, etc) output directory.
        """
        super(BaseRPMBuilder, self).__init__(config, logger, task, task_dir,
                                             artifacts_dir)

    @measure_stage('build_all')
    def build(self):
        spec_file = None
        # There is the case when the git project has more than one spec file
        # In this case it will be found the spec by steps:
        #   1. 'spec_file' inside recipes, e.g. like in
        #       "spec_file": "imunify360.spec" for imunify360-firewall and
        #       "spec_file": "imunify-antivirus.spec" for imunify-antivirus
        #   2. If in the root of project there is the spec with the same name
        #       e.g. `lve-stats.spec` for lve-stats project
        #   3. The git project `lve` contains two specs those start with `lve`
        #       for user and kernel part:
        #       `lve.spec` and `lve-kmod.spec`
        #       In case when `lve.spec` is removed (e.g. in buildsys-pre-build)
        #       will be used `lve-kmod.spec`
        #   4. Last case when we don't have spec_file in recipe, spec is not
        #       the same with project name or name that starts with projectname
        #       then will be found the last of specs in alphabet order
        #       e.g. `securelve.spec` for `cagefs` project
        try:
            info = self.task['build']
            if self.is_srpm_build_required(self.task):
                git_sources_dir = os.path.join(self.task_dir, 'git_sources')
                os.makedirs(git_sources_dir)
                git_repo = self.checkout_git_sources(
                    git_sources_dir, **self.task['build']['git'])
                self.execute_pre_build_hook(git_sources_dir)
                if self.is_koji_sources(git_sources_dir, self.task):
                    source_srpm_dir = git_sources_dir
                    spec_file = self.locate_spec_file(source_srpm_dir,
                                                      self.task)
                    # NOTE: that setting is used by some packages like
                    #       httpd24-mod_lsapi
                    if self.builder_kwargs.get('sources_dir'):
                        source_srpm_dir = \
                            os.path.join(source_srpm_dir,
                                         self.builder_kwargs['sources_dir'])
                    if self.is_bump_required():
                        self.created_tag = \
                            self.bump_release_and_commit(git_sources_dir,
                                                         spec_file)
                else:
                    source_srpm_dir = os.path.join(self.task_dir,
                                                   'source_srpm')
                    os.makedirs(source_srpm_dir)
                    spec_file = self.prepare_koji_sources(git_repo,
                                                          git_sources_dir,
                                                          source_srpm_dir)
                module = '.module' in info['definitions']['dist']
                if info['git'].get('ref_type') == 'gerrit_change':
                    add_gerrit_ref_to_spec(spec_file,
                                           info['git'].get('ref'))
                elif info['git'].get('ref_type') == 'branch' and not module:
                    add_gerrit_ref_to_spec(spec_file)
            else:
                source_srpm_dir = self.unpack_sources()
                spec_file = self.locate_spec_file(source_srpm_dir, self.task)
            if info.get('allow_sources_download'):
                mock_defines = info.get('definitions')
                self.download_remote_sources(source_srpm_dir, spec_file,
                                             mock_defines)
            self.build_packages(source_srpm_dir, spec_file)
        except BuildExcluded as e:
            raise e
        except Exception as e:
            self.logger.error('can not process: {0}\nTraceback:\n{1}'.format(
                              str(e), traceback.format_exc()))
            raise BuildError(str(e))

    def build_srpm(self, mock_config, sources_dir, resultdir, spec_file=None,
                   definitions=None):
        """
        Build a src-RPM package from the specified sources.

        Parameters
        ----------
        mock_config : MockConfig
            Mock chroot environment configuration.
        sources_dir : str
            Sources directory path.
        resultdir : str
            Directory to store build artifacts in.
        spec_file : str, optional
            Spec file path. It will be automatically located in the sources
            directory if omitted.
        definitions : dict, optional
            Dictionary with mock optional definitions

        Returns
        -------
        MockResult
            Mock command execution result.
        """
        if not spec_file:
            spec_file = self.locate_spec_file(sources_dir, self.task)
        with self.mock_supervisor.environment(mock_config) as mock_env:
            return mock_env.buildsrpm(spec_file, sources_dir, resultdir,
                                      definitions=definitions,
                                      timeout=self.build_timeout)

    @measure_stage('build_binary')
    def build_packages(self, src_dir, spec_file=None):
        """
        Builds src-RPM and binary RPM packages, saves build artifacts to the
        artifacts directory.

        Parameters
        ----------
        src_dir : str
            Path to the src-RPM sources.
        spec_file : str, optional
            Spec file path. It will be detected automatically if omitted.
        """
        mock_defines = self.task['build'].get('definitions')
        self.logger.info('starting src-RPM build')
        srpm_result_dir = os.path.join(self.task_dir, 'srpm_result')
        os.makedirs(srpm_result_dir)
        srpm_mock_config = self.generate_mock_config(self.config, self.task,
                                                     srpm_build=True)
        srpm_build_result = None
        try:
            srpm_build_result = self.build_srpm(srpm_mock_config, src_dir,
                                                srpm_result_dir,
                                                spec_file=spec_file,
                                                definitions=mock_defines)
        except MockError as e:
            excluded, reason = self.is_srpm_build_excluded(e)
            if excluded:
                raise BuildExcluded(reason)
            srpm_build_result = e
            raise BuildError('src-RPM build failed: {0}'.format(str(e)))
        finally:
            if srpm_build_result:
                self.save_build_artifacts(srpm_build_result,
                                          srpm_artifacts=True)
        srpm_path = srpm_build_result.srpm
        self.logger.info('src-RPM {0} was successfully built'.
                         format(srpm_path))
        excluded, reason = self.is_build_excluded(srpm_path)
        if excluded:
            raise BuildExcluded(reason)
        self.logger.info('starting RPM build')
        rpm_result_dir = os.path.join(self.task_dir, 'rpm_result')
        rpm_mock_config = self.generate_mock_config(self.config, self.task)
        rpm_build_result = None
        with self.mock_supervisor.environment(rpm_mock_config) as mock_env:
            try:
                rpm_build_result = mock_env.rebuild(srpm_path, rpm_result_dir,
                                                    definitions=mock_defines,
                                                    timeout=self.build_timeout)
            except MockError as e:
                rpm_build_result = e
                raise BuildError('RPM build failed: {0}'.format(str(e)))
            finally:
                if rpm_build_result:
                    self.save_build_artifacts(rpm_build_result)
        self.logger.info('RPM build completed')

    def bump_release_and_commit(self, source_srpm_dir, spec_file):
        """
        Bumps a spec file release and commits a corresponding git tag to a
        project's git repository.

        Parameters
        ----------
        source_srpm_dir : str
            Unpacked source RPM path.
        spec_file : str
            Spec file path.

        Returns
        -------
        str
            Created git tag name.
        """
        task = self.task
        bump_policy = task['build'].get('bump_policy')
        if bump_policy not in ('release:last_commit', 'version:datestamp'):
            raise BuildError('unsupported release bumping policy "{0}"'.
                             format(bump_policy))
        try:
            git_uri = task['build']['git']['uri']
            self.logger.info('bumping release in the {0} spec file'.
                             format(spec_file))
            if bump_policy == 'release:last_commit':
                bump_release_spec_file(spec_file)
                spec_data = get_raw_spec_data(spec_file, ['Version',
                                                          'Release'])
            else:
                spec_data = bump_version_datestamp_spec_file(spec_file)
            new_tag = '{0}-{1}'. \
                format(wipe_rpm_macro(spec_data.get('Version', '')),
                       wipe_rpm_macro(spec_data.get('Release', '')))
            # insert changelog to a spec file
            author = task['created_by']
            changelog_text = task['build'].get('changelog')
            if changelog_text:
                rpm_changelog = \
                    RPMChangelogRecord.generate(datetime.date.today(),
                                                author['name'],
                                                author['email'], new_tag,
                                                changelog_text.split('\n'))
                add_changelog_spec_file(spec_file, str(rpm_changelog))
            # create a new debian package version
            if os.path.exists(os.path.join(source_srpm_dir, 'debian')):
                self.logger.info('adding "{0}" version to a debian package'.
                                 format(new_tag))
                if not changelog_text:
                    changelog_text = '{0} version'.format(new_tag)
                dch_add_changelog_record(source_srpm_dir, 'unstable',
                                         changelog_text, new_tag,
                                         author['email'], author['name'])
            # commit a new git tag
            self.logger.info('creating a "{0}" git tag with bumped release'.
                             format(new_tag))
            git_commit(source_srpm_dir, new_tag, commit_all=True)
            git_create_tag(source_srpm_dir, new_tag)
            git_push(source_srpm_dir, git_uri)
            git_push(source_srpm_dir, git_uri, tags=True)
            return new_tag
        except Exception as e:
            msg = ('can not bump release in the {0} spec file: {1}.'
                   ' Traceback:\n{2}')
            self.logger.error(msg.format(spec_file, str(e),
                                         traceback.format_exc()))
            raise BuildError('can not bump release')

    def unpack_sources(self):
        """
        Unpacks already built src-RPM

        Returns
        -------
        str
            Path to the unpacked src-RPM sources.
        """
        srpm_url = self.task['srpm']['url']
        self.logger.info('repacking previously built src-RPM {0}'.
                         format(srpm_url))
        src_dir = os.path.join(self.task_dir, 'srpm_sources')
        os.makedirs(src_dir)
        self.logger.debug('Downloading {0}'.format(srpm_url))
        credentials = self.task['srpm'].get('download_credentials', {})
        if self.task['srpm']['type'] == 'internal':
            credentials['login'] = self.config.node_id
            credentials['password'] = self.config.jwt_token
        if self.config.development_mode:
            credentials['no_ssl_verify'] = True
        srpm = download_file(srpm_url, src_dir, timeout=900, **credentials)
        self.logger.debug('Unpacking {0} to the {1}'.format(srpm, src_dir))
        unpack_src_rpm(srpm, os.path.dirname(srpm))
        self.logger.info('Sources are prepared')
        return src_dir

    def prepare_koji_sources(self, git_repo, git_sources_dir, output_dir,
                             src_suffix_dir=None):
        """
        Generates a koji compatible sources (spec file, tarball and patches)
        from a project sources.

        Parameters
        ----------
        git_repo : cla.utils.alt_git_repo.WrappedGitRepo
            Git repository wrapper.
        git_sources_dir : str
            Project sources directory path.
        output_dir : str
            Output directory path.
        src_suffix_dir : str, optional
            Additional folder to join to the path to check on source files

        Returns
        -------
        str
            Spec file path in the koji compatible sources directory.
        """
        spec_path = self.locate_spec_file(git_sources_dir, self.task)
        parsed_spec = SpecParser(spec_path,
                                 self.task['build'].get('definitions'))
        tarball_path = None
        tarball_name = None
        excludes = self.task['build'].get('builder', {}).get('kwargs', {}).\
            get('exclude', [])
        excludes.append(os.path.split(spec_path)[1])
        for source in itertools.chain(parsed_spec.source_package.sources,
                                      parsed_spec.source_package.patches):
            if source.position == 0 and isinstance(source, SpecSource):
                tarball_path = os.path.join(output_dir, source.name)
                tarball_name = source.name
            else:
                parsed_url = urllib.parse.urlparse(source.name)
                if parsed_url.scheme == '':
                    file_name = os.path.split(source.name)[1]
                else:
                    # TODO: verify that it works with all valid remote URLs
                    file_name = os.path.basename(parsed_url.path)
                excludes.append(file_name)
                if not src_suffix_dir:
                    source_path = os.path.join(git_sources_dir, file_name)
                else:
                    source_path = os.path.join(git_sources_dir, src_suffix_dir,
                                               file_name)
                if os.path.exists(source_path):
                    shutil.copy(source_path, output_dir)
                elif parsed_url.scheme in ('http', 'https', 'ftp'):
                    download_file(source.name, output_dir)
                else:
                    raise BuildError('can\'t locate {0} source'.
                                     format(source.name))
        git_source_tarball = os.path.join(git_sources_dir, tarball_name)
        if os.path.exists(git_source_tarball):
            shutil.copy(git_source_tarball, output_dir)
        else:
            tarball_prefix = '{0}-{1}/'.format(
                parsed_spec.source_package.name,
                parsed_spec.source_package.version)
            excludes = [os.path.join(tarball_prefix, e.strip('/'))
                        for e in excludes]
            git_ref = self.task['build']['git']['ref']
            if self.task['build']['git']['ref_type'] == 'gerrit_change':
                git_ref = 'HEAD'
            if 'git_archive_dir' in self.builder_kwargs:
                git_ref = '{0}:{1}'.format(
                    git_ref, self.builder_kwargs['git_archive_dir'])
            git_repo.archive(git_ref, tarball_path, archive_format='tar.bz2',
                             prefix=tarball_prefix, exclude=excludes)
        if self.is_bump_required():
            self.created_tag = self.bump_release_and_commit(git_sources_dir,
                                                            spec_path)
        spec_file_name = os.path.basename(spec_path)
        new_spec_path = os.path.join(output_dir, spec_file_name)
        shutil.copy(spec_path, new_spec_path)
        return new_spec_path

    @staticmethod
    def download_remote_sources(sources_dir, spec_file, mock_defines):
        """
        Downloads spec file remote sources which aren't present in the sources
        directory.

        Parameters
        ----------
        sources_dir : str
            Sources directory path.
        spec_file : str
            Spec file path.
        mock_defines : dict or None
            Mock definitions.
        """
        parser = SpecParser(spec_file, mock_defines)
        for source in parser.source_package.sources:
            parsed_url = urllib.parse.urlparse(source.name)
            if parsed_url.scheme == '':
                continue
            file_name = os.path.basename(parsed_url.path)
            if os.path.exists(os.path.join(sources_dir, file_name)):
                continue
            download_file(source.name, sources_dir)

    @staticmethod
    def is_srpm_build_required(task):
        return 'srpm' not in task

    @staticmethod
    def is_koji_sources(sources_dir, task):
        """
        Checks if sources in the specified directory are compatible with
        a standard RPM building process (i.e. the directory contains an archive
        and a spec file).

        Parameters
        ----------
        sources_dir : str
            Sources directory path.
        task : dict
            Build task.

        Returns
        -------
        bool
            True if sources in the specified directory are compatible with a
            standard RPM building process, False otherwise.
        """
        if task['build'].get('builder', {}).get('class') in \
                ('KojiBuilder', 'CL6LveKernelKmodBuilder', 'AltPHPBuilder'):
            return True
        # TODO: parse spec file and verify that all sources are present in the
        #       sources directory
        return False

    @staticmethod
    def generate_mock_config(config, task, srpm_build=False):
        """
        Initializes a mock chroot configuration for the specified task.

        Parameters
        ----------
        config : BuildNodeConfig
            Build node configuration object.
        task : dict
            Task for which to generate a mock chroot configuration.
        srpm_build : bool, optional
            Use only yum repositories which are required for src-RPM build if
            True, use all yum repositories otherwise.

        Returns
        -------
        MockConfig
            Mock chroot configuration.
        """
        yum_repos = []
        for repo in task['build']['repositories']:
            # NOTE: this was disabled to make module_enable and module_install
            #       yum options work. Some modules (e.g. cl-MySQLXY) are
            #       living in external repositories.
            # if srpm_build and not repo.get('required_for_srpm'):
            #     continue
            auth_params = {}
            if repo.get('channel') == REPO_CHANNEL_BUILD:
                auth_params['username'] = config.node_id
                auth_params['password'] = config.jwt_token
                auth_params['sslverify'] = \
                    '0' if config.development_mode else '1'
            yum_repos.append(YumRepositoryConfig(repositoryid=repo['name'],
                                                 name=repo['name'],
                                                 baseurl=repo['url'],
                                                 **auth_params))
        yum_config_kwargs = task['build'].get('yum', {})
        yum_config = YumConfig(rpmverbosity='info', repositories=yum_repos,
                               **yum_config_kwargs)
        mock_config_kwargs = {'use_bootstrap_container': False}
        for key, value in task['build']['mock'].items():
            mock_config_kwargs[key] = value
        mock_config = MockConfig(
            dist=task['build'].get('mock_dist'), use_nspawn=False,
            rpmbuild_networking=True, use_host_resolv=True,
            yum_config=yum_config, **mock_config_kwargs
        )
        if config.pesign_support:
            bind_plugin = MockBindMountPluginConfig(
                True, [('/var/run/pesign', '/var/run/pesign')])
            mock_config.add_plugin(bind_plugin)
        if config.npm_proxy:
            BaseRPMBuilder.configure_mock_npm_proxy(mock_config,
                                                    config.npm_proxy)
        BaseRPMBuilder.configure_mock_chroot_scan(mock_config)
        return mock_config

    @staticmethod
    def configure_mock_chroot_scan(mock_config):
        """
        Configures a mock ChrootScan plugin to save config.log files after
        build.

        Parameters
        ----------
        mock_config : MockConfig
            Mock chroot configuration.

        Notes
        -----
        https://github.com/rpm-software-management/mock/wiki/Plugin-ChrootScan
        """
        # TODO: it would be nice if user can configure a list of files to
        #       save for his project
        chroot_scan = MockPluginConfig(name='chroot_scan', enable=True,
                                       only_failed=False,
                                       regexes=[r'config\.log$'])
        mock_config.add_plugin(chroot_scan)

    @staticmethod
    def configure_mock_npm_proxy(mock_config, npm_proxy):
        """
        Adds an NPM proxy configuration to the mock chroot configuration.

        Parameters
        ----------
        mock_config : MockConfig
            Mock chroot configuration.
        npm_proxy : str
            NPM proxy server URL.

        Raises
        ------
        BuildConfigurationError
            If NPM proxy URL is not valid.
        """
        if not validators.url(npm_proxy):
            raise BuildConfigurationError('NPM proxy URL {0!r} is invalid'.
                                          format(npm_proxy))
        npmrc_content = textwrap.dedent("""
            https-proxy={0}
            proxy={0}
            strict-ssl=false
        """.format(npm_proxy))
        mock_config.add_file(MockChrootFile('/usr/etc/npmrc', npmrc_content))
        # TODO: verify that yarn correctly reads settings from npmrc and
        #       delete that block then
        yarnrc_content = textwrap.dedent("""
            https-proxy "{0}"
            proxy "{0}"
            strict-ssl false
        """.format(npm_proxy))
        mock_config.add_file(MockChrootFile('/usr/etc/yarnrc', yarnrc_content))

    @staticmethod
    def locate_spec_file(sources_dir, task):
        """
        Locates a spec file in the specified sources directory.

        Parameters
        ----------
        sources_dir : str
            Sources directory path.
        task : dict
            Build task.

        Returns
        -------
        str
            Spec file path.
        """
        # TODO: do we really need that method? I think we could use
        #       find_spec_file directly everywhere
        try:
            spec_file = find_spec_file(task['build']['project_name'],
                                       sources_dir,
                                       task['build'].get('spec_file'))
            if not spec_file:
                raise DataNotFoundError()
            return spec_file
        except DataNotFoundError:
            raise BuildError('spec file is not found')

    def save_build_artifacts(self, mock_result, srpm_artifacts=False):
        """
        Saves mock build artifacts for future processing.

        Parameters
        ----------
        mock_result : MockResult
            Mock command execution result.
        srpm_artifacts : bool
            Artifacts were produced during src-RPM build if True or during
            binary RPM(s) build otherwise.
        """
        suffix = '.srpm' if srpm_artifacts else ''
        ts = int(time.time())
        mock_cfg_file = os.path.join(self.artifacts_dir,
                                     'mock{0}.{1}.cfg'.format(suffix, ts))
        with open(mock_cfg_file, 'w') as mock_cfg_fd:
            mock_cfg_fd.write(to_unicode(mock_result.mock_config))
        if mock_result.srpm:
            # NOTE: artifacts saving function could be called two times (after
            #       src-RPM build and after RPMs build)
            srpm_file = os.path.split(mock_result.srpm)[1]
            srpm_dst = os.path.join(self.artifacts_dir, srpm_file)
            if os.path.exists(srpm_dst):
                os.remove(srpm_dst)
            os.link(mock_result.srpm, srpm_dst)
        for rpm_path in mock_result.rpms:
            rpm_name = os.path.split(rpm_path)[1]
            rpm_dst = os.path.join(self.artifacts_dir, rpm_name)
            os.link(rpm_path, rpm_dst)
        # save mock logs
        for mock_log_path in mock_result.mock_logs:
            file_name = os.path.split(mock_log_path)[1]
            re_rslt = re.search(r'^(.*?)\.log$', file_name)
            if not re_rslt:
                continue
            dst_file_name = 'mock_{log_name}{suffix}.{ts}.log'.\
                format(log_name=re_rslt.group(1), suffix=suffix, ts=ts)
            dst_file_path = os.path.join(self.artifacts_dir, dst_file_name)
            # NOTE: mock saves artifacts with broken permissions making
            #       impossible symlinks usage when modularity is enabled.
            try:
                os.link(mock_log_path, dst_file_path)
            except OSError:
                shutil.copyfile(mock_log_path, dst_file_path)
        if mock_result.stderr:
            stderr_file_name = 'mock_stderr{suffix}.{ts}.log'.\
                format(suffix=suffix, ts=ts)
            stderr_file_path = os.path.join(self.artifacts_dir,
                                            stderr_file_name)
            with open(stderr_file_path, 'w') as fd:
                fd.write(mock_result.stderr)
        # TODO: save chroot_scan artifacts

    def is_build_excluded(self, srpm_path):
        """
        Checks if the specified src-RPM build should be excluded
        from the build.

        Parameters
        ----------
        srpm_path : str
            Source RPM path.

        Returns
        -------
        tuple
            Pair where the first element is checking status (True if the build
            should be excluded, False otherwise) and the second is an exclusion
            reason description.
        """
        arch = self.task['build']['arch']
        meta = extract_metadata(srpm_path)

        def get_expanded_value(field):
            return rpm.expandMacro(' '.join(meta.get(field, ()))).split()

        exclusive_arch = get_expanded_value('exclusivearch')
        exclude_arch = get_expanded_value('excludearch')
        if arch in exclude_arch:
            return True, 'the "{0}" architecture is listed in ExcludeArch'.\
                format(arch)
        elif exclusive_arch:
            bit32_arches = {'i386', 'i486', 'i586', 'i686'}
            if (arch not in bit32_arches and arch not in exclusive_arch) or \
                    (arch in bit32_arches and
                     not bit32_arches & set(exclusive_arch)):
                return True, 'the "{0}" architecture is not listed in ' \
                             'ExclusiveArch'.format(arch)
        return False, None

    @staticmethod
    def is_srpm_build_excluded(mock_error):
        """
        Checks if the specified mock error happened because of incompatible
        sources (e.g. a target architecture is excluded).

        Parameters
        ----------
        mock_error : build_node.mock.mock_environment.MockError
            Mock error.

        Returns
        -------
        tuple
            Pair where the first element is checking status (True if the build
            was incompatible, False otherwise) and the second is an exclusion
            reason description.
        """
        for log_file in mock_error.mock_logs:
            log_file_name = os.path.basename(log_file)
            if log_file_name != 'build.log':
                continue
            with open(log_file, 'r') as fd:
                for line in fd:
                    error = build_log_excluded_arch(line)
                    if error:
                        return True, error[1]
        return False, None

    def is_bump_required(self):
        """
        Checks if a version or release bump is required (i.e. we're building
        a package from a git branch and a bump policy is defined).

        Returns
        -------
        bool
            True if version or release bump is required, False otherwise.
        """
        return (self.task['build']['git']['ref_type'] == 'branch'
                and self.task['build'].get('bump_policy'))
