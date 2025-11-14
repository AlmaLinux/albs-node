"""
Base class for AlmaLinux Build System RPM package builders.
"""

import gzip
import itertools
import os
import re
import shutil
import textwrap
import time
import traceback
import urllib.parse
from distutils.dir_util import copy_tree
from typing import Optional, Tuple

import rpm
import validators
from albs_build_lib.builder.base_builder import BaseBuilder, measure_stage
from albs_build_lib.builder.mock.error_detector import build_log_excluded_arch
from albs_build_lib.builder.mock.mock_config import (
    MockBindMountPluginConfig,
    MockChrootFile,
    MockConfig,
    MockPluginChrootScanConfig,
    MockPluginConfig,
)
from albs_build_lib.builder.mock.mock_environment import MockError
from albs_build_lib.builder.mock.yum_config import (
    YumConfig,
    YumRepositoryConfig,
)
from albs_common_lib.errors import (
    BuildConfigurationError,
    BuildError,
    BuildExcluded,
)
from albs_common_lib.utils.file_utils import download_file
from albs_common_lib.utils.git_utils import (
    MirroredGitRepo,
    WrappedGitRepo,
    git_get_commit_id,
)
from albs_common_lib.utils.index_utils import extract_metadata
from albs_common_lib.utils.ported import to_unicode
from albs_common_lib.utils.rpm_utils import unpack_src_rpm
from albs_common_lib.utils.spec_parser import SpecParser, SpecPatch, SpecSource
from pyrpm.spec import Spec, replace_macros

from build_node.utils.git_sources_utils import (
    AlmaSourceDownloader,
    CentpkgDowloader,
    FedpkgDownloader,
)

from .. import build_node_globals as node_globals

# 'r' modifier and the number of slashes is intentional, modify very carefully
# or don't touch this at all
MODSIGN_CONTENT = r"""
%__kmod_brps_added 1
%__brp_kmod_sign %{expand:[ ! -d "$RPM_BUILD_ROOT/lib/modules/"  ] || find "$RPM_BUILD_ROOT/lib/modules/" -type f -name '*.ko' -print -exec /usr/local/bin/modsign %{modsign_os} {} \\\;}
%__brp_kmod_post_sign_process %{expand:[ ! -d "$RPM_BUILD_ROOT/lib/modules/" ] || find "$RPM_BUILD_ROOT/lib/modules/" -type f -name '*.ko.*' -print -exec rm -f {} \\\;}
%__spec_install_post \\
        %{?__debug_package:%{__debug_install_post}} \\
        %{__arch_install_post} \\
        %{__os_install_post} \\
        %{__brp_kmod_sign} \\
        %{__brp_kmod_post_sign_process} \\
        %{nil}
"""
MODSIGN_MACROS_PATH = 'etc/rpm/macros.modsign'


class BaseRPMBuilder(BaseBuilder):

    def __init__(
        self,
        config,
        logger,
        task,
        task_dir,
        artifacts_dir,
        immudb_wrapper,
    ):
        """
        RPM builder initialization.

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
        immudb_wrapper: ImmudbWrapper
            ImmudbWrapper instance
        """
        super().__init__(
            config,
            logger,
            task,
            task_dir,
            artifacts_dir,
        )
        self.immudb_wrapper = immudb_wrapper
        self.codenotary_enabled = config.codenotary_enabled

    @measure_stage("git_checkout")
    def checkout_git_sources(
        self,
        git_sources_dir,
        ref,
        use_repo_cache: bool = True,
    ):
        """
        Checkouts a project sources from the specified git repository.

        Parameters
        ----------
        git_sources_dir : str
            Target directory path.
        ref : TaskRef
            Git (gerrit) reference.
        use_repo_cache : bool
            Switch on or off the repo caching ability

        Returns
        -------
        cla.utils.alt_git_repo.WrappedGitRepo
            Git repository wrapper.
        """
        self.logger.info(
            'checking out {0} from {1}'.format(ref.git_ref, ref.url)
        )
        # FIXME: Understand why sometimes we hold repository lock more
        #  than 60 seconds
        if use_repo_cache:
            with MirroredGitRepo(
                ref.url,
                self.config.git_repos_cache_dir,
                self.config.git_cache_locks_dir,
                timeout=600,
                git_command_extras=self.config.git_extra_options,
            ) as cached_repo:
                repo = cached_repo.clone_to(git_sources_dir)
        else:
            repo = WrappedGitRepo(git_sources_dir)
            repo.clone_from(ref.url, git_sources_dir)
        repo.checkout(ref.git_ref)
        self.__log_commit_id(git_sources_dir)
        return repo

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

    def prepare_autospec_sources(
        self, git_sources_dir: str, downloaded_sources_dir: str = None
    ) -> Tuple[str, Optional[str]]:
        source_srpm_dir = git_sources_dir
        spec_file = self.locate_spec_file(git_sources_dir)
        if downloaded_sources_dir:
            base_path = os.path.join(git_sources_dir, downloaded_sources_dir)
            for file_ in os.listdir(base_path):
                shutil.copy(os.path.join(base_path, file_), git_sources_dir)
        return source_srpm_dir, spec_file

    def prepare_usual_sources(
        self,
        git_repo: WrappedGitRepo,
        git_sources_dir: str,
        sources_dir: str,
        downloaded_sources_dir: str = None,
    ) -> Tuple[str, Optional[str]]:
        source_srpm_dir = os.path.join(self.task_dir, 'source_srpm')
        os.makedirs(source_srpm_dir)
        if downloaded_sources_dir == 'SOURCES':
            copy_tree(sources_dir, source_srpm_dir)
        cwd = os.getcwd()
        try:
            os.chdir(source_srpm_dir)
            self.prepare_koji_sources(
                git_repo,
                git_sources_dir,
                source_srpm_dir,
                src_suffix_dir=downloaded_sources_dir,
            )
        finally:
            os.chdir(cwd)
        spec_file = self.locate_spec_file(source_srpm_dir)
        return source_srpm_dir, spec_file

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
        src_suffix_dir = None
        try:
            centos_sources_downloaded = False
            alma_sources_downloaded = False
            fedora_sources_downloaded = False
            if self.task.is_srpm_build_required():
                url_str = str(self.task.ref.url)
                if url_str.endswith('/'):
                    url_str = url_str.strip('/')
                project_name = os.path.basename(url_str).replace('.git', '')
                git_sources_dir = os.path.join(self.task_dir, project_name)
                os.makedirs(git_sources_dir)
                # TODO: Temporarily disable git caching because it interferes with
                #  centpkg work
                git_repo = self.checkout_git_sources(
                    git_sources_dir, self.task.ref, use_repo_cache=False
                )
                sources_file = os.path.join(git_sources_dir, 'sources')
                sources_dir = os.path.join(git_sources_dir, 'SOURCES')
                if self.task.is_alma_source():
                    if self.codenotary_enabled:
                        self.cas_source_authenticate(git_sources_dir)
                    self.logger.info('Trying to download AlmaLinux sources')
                    alma_sources_downloaded = self.prepare_alma_sources(git_sources_dir)
                    if not alma_sources_downloaded and os.path.exists(sources_file):
                        self.logger.info('AlmaLinux sources were not downloaded, calling centpkg')
                        centos_sources_downloaded = self.prepare_centos_sources(git_sources_dir)
                    if not alma_sources_downloaded and not centos_sources_downloaded:
                        self.logger.info('CentOS sources were not downloaded, calling fedpkg')
                        fedora_sources_downloaded = self.prepare_fedora_sources(git_sources_dir)
                        if not fedora_sources_downloaded:
                            self.logger.warning(
                                'AlmaLinux, CentOS and Fedora downloaders failed, '
                                'assuming all sources are already in place'
                            )
                    if os.path.exists(sources_dir):
                        src_suffix_dir = 'SOURCES'
                autospec_conditions = [
                    self.task.is_rpmautospec_required(),
                    alma_sources_downloaded or centos_sources_downloaded or fedora_sources_downloaded
                ]
                if all(autospec_conditions):
                    source_srpm_dir, spec_file = self.prepare_autospec_sources(
                        git_sources_dir, downloaded_sources_dir=src_suffix_dir
                    )
                else:
                    source_srpm_dir, spec_file = self.prepare_usual_sources(
                        git_repo,
                        git_sources_dir,
                        sources_dir,
                        downloaded_sources_dir=src_suffix_dir,
                    )
            else:
                source_srpm_dir = self.unpack_sources()
                spec_file = self.locate_spec_file(source_srpm_dir)
            if self.task.platform.data.get('allow_sources_download'):
                mock_defines = self.task.platform.data.get('definitions')
                self.download_remote_sources(
                    source_srpm_dir, spec_file, mock_defines
                )
            self.build_packages(source_srpm_dir, spec_file)
        except BuildExcluded as e:
            raise e
        except Exception as e:
            self.logger.warning(
                'can not process: %s\nTraceback:\n%s',
                str(e),
                traceback.format_exc(),
            )
            raise BuildError(str(e))

    @measure_stage("cas_source_authenticate")
    def cas_source_authenticate(self, git_sources_dir: str):
        auth_result = self.immudb_wrapper.authenticate_git_repo(
            git_sources_dir
        )
        self.task.is_cas_authenticated = auth_result.get('verified', False)
        self.task.alma_commit_cas_hash = (
            auth_result.get('value', {})
            .get('Metadata', {})
            .get('git', {})
            .get('Commit')
        )

    @measure_stage("build_srpm")
    def build_srpm(
        self,
        mock_config,
        sources_dir,
        resultdir,
        spec_file=None,
        definitions=None,
    ):
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
            spec_file = self.locate_spec_file(sources_dir)
        with self.mock_supervisor.environment(mock_config) as mock_env:
            return mock_env.buildsrpm(
                spec_file,
                sources_dir,
                resultdir,
                definitions=definitions,
                timeout=self.build_timeout,
            )

    @measure_stage('build_binaries')
    def build_binaries(self, srpm_path, definitions=None):
        """
        Builds binary RPM packages, saves build artifacts
        to the artifacts directory.

        Parameters
        ----------
        srpm_path : str
            Path to SRPM
        definitions : dict, optional
            Dictionary with mock optional definitions
        """
        rpm_result_dir = os.path.join(self.task_dir, 'rpm_result')
        rpm_mock_config = self.generate_mock_config(self.config, self.task)
        rpm_build_result = None
        with self.mock_supervisor.environment(rpm_mock_config) as mock_env:
            try:
                rpm_build_result = mock_env.rebuild(
                    srpm_path,
                    rpm_result_dir,
                    definitions=definitions,
                    timeout=self.build_timeout,
                )
            except MockError as e:
                rpm_build_result = e
                raise BuildError(f'RPM build failed: {str(e)}')
            finally:
                if rpm_build_result:
                    self.save_build_artifacts(rpm_build_result)

    @measure_stage('build_packages')
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
        mock_defines = self.task.platform.data.get('definitions')
        self.logger.info('starting src-RPM build')
        srpm_result_dir = os.path.join(self.task_dir, 'srpm_result')
        os.makedirs(srpm_result_dir)
        srpm_mock_config = self.generate_mock_config(
            self.config,
            self.task,
            srpm_build=True,
        )
        srpm_build_result = None
        try:
            srpm_build_result = self.build_srpm(
                srpm_mock_config,
                src_dir,
                srpm_result_dir,
                spec_file=spec_file,
                definitions=mock_defines,
            )
        except MockError as e:
            excluded, reason = self.is_srpm_build_excluded(e)
            if excluded:
                raise BuildExcluded(reason)
            srpm_build_result = e
            raise BuildError('src-RPM build failed: {0}'.format(str(e)))
        finally:
            if srpm_build_result:
                self.save_build_artifacts(
                    srpm_build_result, srpm_artifacts=True
                )
        srpm_path = srpm_build_result.srpm
        self.logger.info('src-RPM %s was successfully built', srpm_path)
        if self.task.arch == 'src':
            return
        excluded, reason = self.is_build_excluded(srpm_path)
        if excluded:
            raise BuildExcluded(reason)
        self.logger.info('starting RPM build')
        self.build_binaries(srpm_path, mock_defines)
        self.logger.info('RPM build completed')

    @measure_stage("sources_unpack")
    def unpack_sources(self):
        """
        Unpacks already built src-RPM

        Returns
        -------
        str
            Path to the unpacked src-RPM sources.
        """
        if self.task.ref.url.endswith('src.rpm'):
            srpm_url = self.task.ref.url
        else:
            srpm_url = self.task.built_srpm_url
        self.logger.info('repacking previously built src-RPM %s', srpm_url)
        src_dir = os.path.join(self.task_dir, 'srpm_sources')
        os.makedirs(src_dir)
        self.logger.debug('Downloading %s', srpm_url)
        srpm = download_file(srpm_url, src_dir, timeout=900)
        self.logger.debug('Unpacking %s to the %s', srpm, src_dir)
        unpack_src_rpm(srpm, os.path.dirname(srpm))
        self.logger.info('Sources are prepared')
        return src_dir

    @staticmethod
    def prepare_alma_sources(git_sources_dir: str) -> bool:
        downloader = AlmaSourceDownloader(git_sources_dir)
        return downloader.download_all()

    @staticmethod
    def prepare_centos_sources(git_sources_dir: str) -> bool:
        downloader = CentpkgDowloader(git_sources_dir)
        return downloader.download_all()
    
    @staticmethod
    def prepare_fedora_sources(git_sources_dir: str) -> bool:
        downloader = FedpkgDownloader(git_sources_dir)
        return downloader.download_all()

    def prepare_koji_sources(
        self,
        git_repo,
        git_sources_dir,
        output_dir,
        src_suffix_dir=None,
    ):
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
        spec_path = self.locate_spec_file(git_sources_dir)
        spec_file_name = os.path.basename(spec_path)
        new_spec_path = os.path.join(output_dir, spec_file_name)
        shutil.copy(spec_path, new_spec_path)
        try:
            self.logger.debug("Trying to use SpecParser")
            defs = self.task.platform.data.get('definitions', {}).copy()
            sources_dir = os.path.join(git_sources_dir, 'SOURCES')
            if os.path.exists(sources_dir):
                defs['_sourcedir'] = sources_dir
            try:
                parsed_spec = SpecParser(
                    spec_path, defs
                )
            except Exception as exc:
                self.logger.debug(
                    'Error: %s. Trying to use SpecParser with different '
                    '_sourcedir: %s',
                    str(exc),
                    git_sources_dir
                )
                defs['_sourcedir'] = git_sources_dir
                parsed_spec = SpecParser(
                    spec_path, defs
                )
            sources = parsed_spec.source_package.sources
            patches = parsed_spec.source_package.patches
        except Exception as exc:
            self.logger.debug("SpecParser failed: %s", str(exc))
            try:
                parsed_spec = Spec.from_file(spec_path)
                sources = [
                    SpecSource(replace_macros(s, parsed_spec), position)
                    for position, s in enumerate(parsed_spec.sources)
                ]
                patches = [
                    SpecPatch(replace_macros(p, parsed_spec), position)
                    for position, p in enumerate(parsed_spec.patches)
                ]
            except Exception:
                self.logger.exception(
                    "Can't parse spec file, expecting all sources"
                    " to be in the right place already"
                )
                return new_spec_path
        tarball_path = None
        try:
            for source in itertools.chain(sources, patches):
                parsed_url = urllib.parse.urlparse(source.name)
                if parsed_url.scheme == '':
                    file_name = os.path.split(source.name)[1]
                else:
                    # TODO: verify that it works with all valid remote URLs
                    if parsed_url.fragment:
                        file_name = os.path.basename(parsed_url.fragment)
                    else:
                        file_name = os.path.basename(parsed_url.path)
                add_source_path = os.path.join(git_sources_dir, file_name)
                if not src_suffix_dir:
                    source_path = os.path.join(git_sources_dir, file_name)
                else:
                    source_path = os.path.join(
                        git_sources_dir, src_suffix_dir, file_name
                    )
                self.logger.debug(
                    'Original path: %s, file exists: %s',
                    source_path,
                    os.path.exists(source_path),
                )
                self.logger.debug(
                    'Additional path: %s, file exists: %s',
                    add_source_path,
                    os.path.exists(add_source_path),
                )
                if os.path.exists(source_path):
                    shutil.copy(source_path, output_dir)
                elif not os.path.exists(source_path) and os.path.exists(
                    add_source_path
                ):
                    shutil.copy(add_source_path, output_dir)
                elif parsed_url.scheme in ('http', 'https', 'ftp'):
                    download_file(source.name, output_dir)
                if source.position == 0 and isinstance(source, SpecSource):
                    tarball_path = os.path.join(output_dir, file_name)
            if tarball_path is not None and not os.path.exists(tarball_path):
                tarball_prefix = '{0}-{1}/'.format(
                    parsed_spec.source_package.name,
                    parsed_spec.source_package.version,
                )
                git_ref = self.task.ref.git_ref
                git_repo.archive(
                    git_ref,
                    tarball_path,
                    archive_format='tar.bz2',
                    prefix=tarball_prefix,
                )
        except Exception:
            self.logger.exception(
                'Can\'t load sources from remote repo, '
                'expecting them to be in the right place already'
            )
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
    def generate_mock_config(config, task, srpm_build=False):
        """
        Initializes a mock chroot configuration for the specified task.

        Parameters
        ----------
        config : BuildNodeConfig
            Build node configuration object.
        task : build_node.models.Task
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
        for repo in task.repositories:
            if not repo.mock_enabled:
                continue
            repo_kwargs = {}
            if re.search(r'AlmaLinux-\d-.*-\d+-br', repo.url):
                repo_kwargs['module_hotfixes'] = True
            yum_repos.append(
                YumRepositoryConfig(
                    repositoryid=repo.name,
                    name=repo.name,
                    priority=str(repo.priority),
                    baseurl=repo.url,
                    **repo_kwargs,
                ),
            )
        yum_config_kwargs = task.platform.data.get('yum', {})
        yum_config = YumConfig(
            rpmverbosity='info',
            repositories=yum_repos,
            **yum_config_kwargs,
        )
        mock_config_kwargs = {'use_bootstrap_container': False, 'macros': {}}
        target_arch = task.arch
        use_host_resolv = True
        if target_arch == 'src':
            target_arch = config.base_arch
        for key, value in task.platform.data['mock'].items():
            if key == 'target_arch':
                target_arch = value
            elif key == 'macros':
                mock_config_kwargs['macros'].update(value)
            elif key == 'secure_boot_macros':
                if not task.is_secure_boot:
                    continue
                mock_config_kwargs['macros'].update(value)
            elif key == 'rpmautospec_enable':
                continue
            elif key == 'use_host_resolv':
                use_host_resolv = value
            else:
                mock_config_kwargs[key] = value
        mock_config = MockConfig(
            dist=task.platform.data.get('mock_dist'),
            rpmbuild_networking=True,
            use_host_resolv=use_host_resolv,
            yum_config=yum_config,
            target_arch=target_arch,
            basedir=config.mock_basedir,
            **mock_config_kwargs,
        )
        if task.is_secure_boot:
            mock_config.set_config_opts({'isolation': 'simple'})
            mock_config.append_config_opt(
                'nspawn_args', '--bind-ro=/opt/pesign:/usr/local/bin'
            )
            bind_plugin = MockBindMountPluginConfig(
                True, [('/opt/pesign', '/usr/local/bin')]
            )
            mock_config.add_plugin(bind_plugin)
            mock_config.add_file(
                MockChrootFile(MODSIGN_MACROS_PATH, MODSIGN_CONTENT)
            )
        if task.is_rpmautospec_required():
            rpmautospec_plugin = MockPluginConfig(
                'rpmautospec',
                True,
                requires=['rpmautospec'],
                cmd_base=['/usr/bin/rpmautospec', 'process-distgit'],
            )
            mock_config.add_plugin(rpmautospec_plugin)
        if config.npm_proxy:
            BaseRPMBuilder.configure_npm_proxy(mock_config, config.npm_proxy)
        BaseRPMBuilder.configure_mock_chroot_scan(
            mock_config, task.platform.data.get('custom_logs', None)
        )
        return mock_config

    @staticmethod
    def configure_mock_chroot_scan(mock_config, custom_logs=None):
        """
        Configures a mock ChrootScan plugin to save config.log files after
        build.

        Parameters
        ----------
        mock_config : MockConfig
            Mock chroot configuration.
        custom_logs : tuple
            Specified regexes of build logs to save after build.

        Notes
        -----
        https://github.com/rpm-software-management/mock/wiki/Plugin-ChrootScan
        """
        if custom_logs:
            chroot_scan = MockPluginChrootScanConfig(
                name='chroot_scan',
                enable=True,
                only_failed=False,
                regexes=custom_logs,
            )
            mock_config.add_plugin(chroot_scan)

    @staticmethod
    def configure_npm_proxy(mock_config, npm_proxy):
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
            raise BuildConfigurationError(
                'NPM proxy URL {0!r} is invalid'.format(npm_proxy)
            )
        npmrc_content = textwrap.dedent(
            """
            https-proxy={0}
            proxy={0}
            strict-ssl=false
        """.format(
                npm_proxy
            )
        )
        mock_config.add_file(MockChrootFile('/usr/etc/npmrc', npmrc_content))
        # TODO: verify that yarn correctly reads settings from npmrc and
        #       delete that block then
        yarnrc_content = textwrap.dedent(
            """
            https-proxy "{0}"
            proxy "{0}"
            strict-ssl false
        """.format(
                npm_proxy
            )
        )
        mock_config.add_file(MockChrootFile('/usr/etc/yarnrc', yarnrc_content))

    @staticmethod
    def locate_spec_file(sources_dir):
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
        folders_to_search = [sources_dir]
        specs_dir = os.path.join(sources_dir, 'SPECS')
        if os.path.exists(specs_dir):
            folders_to_search.append(specs_dir)
        for folder in folders_to_search:
            for filename in os.listdir(folder):
                if filename.endswith('.spec'):
                    return os.path.join(folder, filename)
        raise BuildError('Spec file is not found')

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
        task_id = self.task.id
        suffix = '.srpm' if srpm_artifacts else ''
        ts = int(time.time())
        mock_cfg_file = os.path.join(
            self.artifacts_dir, f'mock{suffix}.{task_id}.{ts}.cfg'
        )
        with open(mock_cfg_file, 'w') as mock_cfg_fd:
            mock_cfg_fd.write(to_unicode(mock_result.mock_config))
        if mock_result.srpm and not self.task.built_srpm_url:
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
            dst_file_name = (
                f'mock_{re_rslt.group(1)}{suffix}.{task_id}.{ts}.log'
            )
            dst_file_path = os.path.join(self.artifacts_dir, dst_file_name)
            with open(mock_log_path, 'rb') as src_fd, open(
                dst_file_path, 'wb'
            ) as dst_fd:
                dst_fd.write(gzip.compress(src_fd.read()))
        if mock_result.stderr:
            stderr_file_name = f'mock_stderr{suffix}.{task_id}.{ts}.log'
            stderr_file_path = os.path.join(
                self.artifacts_dir, stderr_file_name
            )
            with open(stderr_file_path, 'wb') as dst:
                dst.write(gzip.compress(str(mock_result.stderr).encode()))

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
        arch = self.task.arch
        meta = extract_metadata(srpm_path)

        def get_expanded_value(field):
            return rpm.expandMacro(' '.join(meta.get(field, ()))).split()

        exclusive_arch = get_expanded_value('exclusivearch')
        exclude_arch = get_expanded_value('excludearch')
        if (
            arch in exclude_arch
            or arch == 'x86_64_v2'
            and 'x86_64' in exclude_arch
        ):
            return True, f'the "{arch}" architecture is listed in ExcludeArch'
        if exclusive_arch:
            bit32_arches = {'i386', 'i486', 'i586', 'i686'}
            if arch == 'x86_64_v2' and 'x86_64' in exclusive_arch:
                exclusive_arch.append(arch)
            if (arch not in bit32_arches and arch not in exclusive_arch) or (
                arch in bit32_arches and not bit32_arches & set(exclusive_arch)
            ):
                return (
                    True,
                    f'the "{arch}" architecture is not listed in ExclusiveArch',
                )
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
        castor.mock.supervisor.MockSupervisor
        """
        return node_globals.MOCK_SUPERVISOR
