# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2017-12-27

"""Cloudlinux implementation of class for building Debian packages"""

import contextlib
import gzip
import os
import re
import shutil
import tarfile
import tempfile
import time
import traceback
import urllib.parse

try:
    import lzma
except ImportError:
    from backports import lzma

import plumbum

from build_node.utils.file_utils import download_file
from build_node.utils.debian_utils import (
    dpkg_parsechangelog
)
from build_node.builders.base_builder import measure_stage, BaseBuilder
from build_node.build_node_errors import BuildError
from build_node.pbuilder.pbuilder_config import PbuilderConfig
from build_node.pbuilder.pbuilder_environment import PbuilderResult, PbuilderError
from build_node.errors import CommandExecutionError
from build_node.utils.file_utils import (
    chown_recursive,
    filter_files,
    find_files,
    safe_mkdir,
    urljoin_path,
)
from build_node.ported import to_unicode

__all__ = ['DebianBuilder']


class DebianBuilder(BaseBuilder):

    def __init__(self, config, logger, task, task_dir, artifacts_dir, immudb_wrapper):
        """
        Debian builder initialization.

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
            Build artifacts (.dsc(s), .deb(s), logs, etc) output directory.
        """
        chown_recursive(task_dir)
        super(DebianBuilder, self).__init__(config, logger, task, task_dir,
                                            artifacts_dir)
        self._immudb_wrapper = immudb_wrapper
        self.__config_keys = ('components', 'debootstrapopts', 'distribution',
                              'mirrorsite', 'othermirror')

    @measure_stage('build_all')
    def build(self):
        """
        Builds and saves debian packages.

        Raises
        ------
        BuildError
            If build failed.
        """
        pbuilder_work_dir = self.config.pbuilder_configs_storage_dir
        config_vars = self.config_vars
        pb_config = PbuilderConfig(pbuilder_work_dir, **config_vars)
        result = self.deb_building(pb_config)
        self.save_artifacts(result)
        self.logger.info('Build completed')

    def deb_building(self, pb_config):
        """
        Does common circle of building DEB package.
        We need only define PBuilderConfig and save artifacts
        Parameters
        ----------
        pb_config : PbuilderConfig
            Config for PBuilder

        Returns
        -------
        PbuilderResult
            Build result object

        Raises
        ------
        BuildError
            If build failed.

        """
        extra_repos = []
        for repo in self.task['build'].get('repositories', ()):
            # We need to exclude current build repo if it's empty
            # as it causes pbuilder to fail
            is_empty = self._is_empty_repo(
                repo['url'],
                login=self.config.node_id,
                password=self.config.jwt_token,
                no_ssl_verify=self.config.development_mode
            )
            if not is_empty:
                auth_url = self.add_url_credentials(repo['url'],
                                                    self.config.node_id,
                                                    self.config.jwt_token)
                extra_repos.append('deb {0} ./'.format(auth_url))

        extra_repos.extend(self.config_vars.get('othermirror', []))
        extra_mirrors = '|'.join(extra_repos)
        #
        git_sources_dir = os.path.join(self.task_dir, 'git_sources')
        unpacked_sources_dir = os.path.join(self.task_dir, 'unpacked_sources')
        result_dir = os.path.join(self.task_dir, 'results')
        build_dir = os.path.join(self.task_dir, 'build')
        apt_dir = os.path.join(self.task_dir, 'apt')
        log_file = os.path.join(result_dir, 'build.log')
        for directory in [git_sources_dir, result_dir, build_dir,
                          unpacked_sources_dir, apt_dir]:
            safe_mkdir(directory)
        # Save initial pbuilder config to make debugging easier
        with open(os.path.join(
                result_dir, 'initial_pbuilder_config.cfg'), 'wt') as f:
            f.write(pb_config.generate_config(pb_config.config_dict))
        # Prepare apt.conf as there is no other way to install
        # unsigned packages
        self.generate_apt_conf(os.path.join(apt_dir, 'apt.conf'),
                               self.config.development_mode)
        self.checkout_git_sources(git_sources_dir, **self.task['build']['git'])
        self.execute_pre_build_hook(git_sources_dir)
        try:
            source_name = dpkg_parsechangelog(git_sources_dir, 'Source')
            self.logger.debug('source package name: {0}'.format(source_name))
        except CommandExecutionError as e:
            raise BuildError(to_unicode(e))
        source_version = self._get_source_package_version(git_sources_dir)
        self.logger.debug(
            'source package version: {0}'.format(source_version))
        src_archive = self._find_orig_archive(git_sources_dir, source_name,
                                              source_version)
        if src_archive:
            self.logger.debug('extracting .orig tarball {0} to {1}'.
                              format(src_archive, unpacked_sources_dir))
            prep_src_dir = self.__unpack_sources(src_archive,
                                                 unpacked_sources_dir)
            git_deb_dir = filter_files(git_sources_dir,
                                       lambda f: f == 'debian')[0]
            src_deb_dir = os.path.join(prep_src_dir, 'debian')
            if os.path.exists(src_deb_dir):
                shutil.rmtree(src_deb_dir)

            shutil.copytree(git_deb_dir, src_deb_dir, symlinks=True)
            shutil.copy(src_archive, os.path.dirname(prep_src_dir))
        else:
            prep_src_dir = git_sources_dir
            # Create an archive with sources just in case
            src_archive = self.__prepare_sources(prep_src_dir, source_name,
                                                 source_version)
        find_changelogs = find_files(prep_src_dir, r'changelog')
        find_changelog = [x for x in find_changelogs
                          if '/debian/changelog' in x][0]
        self.logger.info('Starting packages build')
        try:
            # On this position system gets a copy of base image
            # and APT cache files. As they are shared resources,
            # the copy is made under the lock.
            (config_dict,
             config_file) = self.pbuilder_supervisor.environment(
                pb_config, result_dir, apt_dir, build_dir)
            # Updating the local copy of environment with new repositories
            self.pbuilder_supervisor.init_update_env(
                config_dict, apt_dir, extra_repos=extra_mirrors)
        except PbuilderError as e:
            msg = ('"{e.command}" command returned {e.exit_code}.\n'
                   'Stdout:\n{e.stdout}\nStderr:\n{e.stderr}')
            self.logger.error(msg.format(e=e))
            self.save_artifacts(e)
            raise BuildError('can not initialize pbuilder environment: {0}'.
                             format(to_unicode(e)))
        try:
            # Building package in local environment
            result = self.execute_pdebuild(prep_src_dir, result_dir,
                                           apt_dir, extra_mirrors,
                                           config_file, log_file)
        except PbuilderError as e:
            self.logger.error('"{e.command}" command returned '
                              '{e.exit_code}.\nStdout:\n{e.stdout}\n'
                              'Stderr:\n{e.stderr}'.format(e=e))
            self.save_artifacts(e)
            raise BuildError('can not build deb packages: {0}'.
                             format(to_unicode(e)))

        self.logger.info('Saving build artifacts')
        if src_archive:
            shutil.copy(src_archive, result_dir)
        return result

    def execute_pdebuild(self, sources_dir, result_dir, apt_dir, extra_mirrors,
                         config_file, log_file):
        """
        Executes pdebuild command which produces both sources and binary deb
        packages.

        Parameters
        ----------
        sources_dir :   str
            Path to source files
        result_dir :    str
            Path to where save built packages, logs, configs, etc.
        apt_dir :       str
            Path to the directory with apt.conf file
        extra_mirrors : str or unicode
            String with extra mirrors delimited with '|' sign
        config_file :   str
            Path to pbuilder config
        log_file :      str
            Path to build log file

        Returns
        -------
        PbuilderResult
            Build result object

        Raises
        ------
        PbuilderError
            If pbuilder execution failed.
        """
        pdebuild_cmd = plumbum.local['sudo']
        # NOTE: "-E" argument is required to pass env variables to pbuilder
        with open(config_file, 'r') as fd:
            config_content = fd.read()
        args = ['-E', 'pdebuild', '--configfile', config_file,
                '--use-pdebuild-internal', '--buildresult', result_dir,
                '--logfile', log_file, '--', '--aptconfdir', apt_dir,
                '--othermirror', extra_mirrors, '--override-config']
        timeout = self.build_timeout
        if timeout is not None:
            args.append('--timeout')
            args.append('{0}s'.format(timeout))
        command = 'sudo {}'.format(' '.join(args))
        try:
            exit_code, out, err = \
                pdebuild_cmd.run(args=args, cwd=sources_dir,
                                 env=self.environment_vars, retcode=None)
            cls_args = (command, exit_code, out, err, config_content, apt_dir,
                        result_dir)
            if exit_code == 0:
                return PbuilderResult(*cls_args)
            else:
                raise PbuilderError(*cls_args)
        finally:
            chown_recursive(self.task_dir)

    @staticmethod
    def __unpack_sources(archive, result_dir):
        """

        Parameters
        ----------
        archive :    str
            Archive path
        result_dir : str
            Path to directory where to store the data

        Returns
        -------
        str
            Path to unpacked sources

        """
        if archive.endswith('.xz'):
            with contextlib.closing(lzma.LZMAFile(archive)) as xz:
                with tarfile.open(fileobj=xz) as f:
                    f.extractall(result_dir)
        else:
            if archive.endswith('.bz2'):
                tar = tarfile.open(archive, 'r:bz2')
            else:
                tar = tarfile.open(archive, 'r:gz')
            tar.extractall(result_dir)
            tar.close()
        unpacked_folder_name = os.listdir(result_dir)[0]
        return os.path.join(result_dir, unpacked_folder_name)

    def __prepare_sources(self, sources_dir, source_name, source_version):
        """
        Checks debian/sources/format file and prepares sources according to it.

        Parameters
        ----------
        sources_dir : str or unicode
            Path to the directory with package sources (usually cloned
            repository)

        Returns
        -------
        str or None
            Path to the archive or None if no archive is needed

        """
        format_file = os.path.join(sources_dir, 'debian/source/format')
        if os.path.exists(format_file):
            with open(format_file, 'r') as f:
                fmt = f.read().strip()
            if fmt == '3.0 (quilt)':
                return self.__create_orig_archive(sources_dir, source_name,
                                                  source_version)
        return

    def __create_orig_archive(self, sources_dir, source_name, source_version):
        """
        Creates archive with sources for Debian package building.
        Places it one level above sources directory

        Parameters
        ----------
        sources_dir : str or unicode
            Path to the directory with package sources (usually cloned
            repository)
        source_name : str
            Source package name.
        source_version : str
            Source package version.

        Returns
        -------
        str
            Path to the archive
        Raises
        ------
        BuildError
            If package name or version cannot be parsed from changelog
        """
        tarball_name = '{}_{}.orig.tar.gz'.format(source_name, source_version)
        self.logger.info('creating {0} archive for 3.0 (quilt) format'.
                         format(tarball_name))
        tarball_path = os.path.join(os.path.dirname(sources_dir), tarball_name)
        exclude_re = re.compile(r'\.(spec|git|gitignore)$',
                                flags=re.IGNORECASE)
        with tarfile.open(tarball_path, 'w:gz') as tar:
            for item in os.listdir(sources_dir):
                if item == 'debian':
                    continue
                elif not exclude_re.search(item):
                    self.logger.debug('adding {0} to the {1} archive'.
                                      format(item, tarball_name))
                    tar.add(os.path.join(sources_dir, item),
                            arcname=os.path.basename(item))
                else:
                    item_path = os.path.join(sources_dir, item)
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)
        return tarball_path

    @staticmethod
    def _find_orig_archive(sources_dir, source_name, source_version):
        """
        Finds an .orig sources archive in the specified directory.

        Parameters
        ----------
        sources_dir : str
            Sources directory path.
        source_name : str
            Source package name.
        source_version : str
            Source package version.

        Returns
        -------
        str or None
            Sources archive path.
        """
        regex = re.compile(r'^{0}_{1}\.orig\.tar\.(bz2|gz|xz)'.
                           format(re.escape(source_name),
                                  re.escape(source_version)))
        archives = filter_files(sources_dir, lambda f: regex.search(f))
        if archives:
            return archives[0]

    @staticmethod
    def _get_source_package_version(sources_dir):
        """
        Extracts a debian source package version (without the release part)
        from the debian/changelog.

        Parameters
        ----------
        sources_dir : str
            Package sources path.

        Returns
        -------
        str
            Debian source package version.
        """
        changelog_version = dpkg_parsechangelog(sources_dir, 'Version').strip()
        re_rslt = re.search(r'^(\d+:|)(.*?)-.*$', changelog_version)
        if re_rslt:
            return re_rslt.group(2)
        raise BuildError('can not parse changelog version "{0}"'.
                         format(changelog_version))

    def _is_empty_repo(self, url, login=None, password=None,
                       no_ssl_verify=False):
        """
        Checks if a repository is empty.

        Parameters
        ----------
        url : str
            URL to Packages.gz file
        login : str, optional
            HTTP Basic authentication login.
        password : str, optional
            HTTP Basic authentication password.
        no_ssl_verify : bool, optional
            Disable SSL verification if set to True.

        Returns
        -------
        bool
            True if repository is empty, False otherwise.
        """
        packages_gz_url = urljoin_path(url, 'Packages.gz')
        with tempfile.NamedTemporaryFile(prefix='castor_deb_builder_') as fd:
            try:
                download_file(packages_gz_url, fd.name, login=login,
                              password=password, no_ssl_verify=no_ssl_verify)
                with gzip.open(fd.name, 'rb') as packages_gz:
                    for line in packages_gz:
                        if 'Package:' in to_unicode(line):
                            return False
                return True
            except Exception as e:
                self.logger.error('can not download {0} Packages.gz: {1}. '
                                  'Traceback:\n{2}'.
                                  format(url, str(e),
                                         traceback.format_exc()))
                return True

    @staticmethod
    def add_url_credentials(url, login, password):
        """
        Adds HTTP basic auth credentials to the specified URL.

        Parameters
        ----------
        url : str
            URL.
        login : str
            Username.
        password : str
            Password.

        Returns
        -------
        str
            URL with login and password.
        """
        parsed = urllib.parse.urlparse(url)
        netloc = '{0}:{1}@{2}'.format(login, password, parsed.netloc)
        return urllib.parse.urlunparse((parsed.scheme, netloc, parsed.path,
                                        parsed.params, parsed.query,
                                        parsed.fragment))

    def save_artifacts(self, result):
        """
        Saves build artifacts to artifacts directory.

        Parameters
        ----------
        result :    PbuilderResult
            Pbuilder result object with all needed data inside

        Returns
        -------

        """
        ts = int(time.time())
        build_config = os.path.join(self.artifacts_dir,
                                    'build-{}.cfg'.format(ts))
        with open(build_config, 'w') as fd:
            fd.write(to_unicode(result.pbuilder_config))
        if result.log:
            log_path = os.path.join(self.artifacts_dir,
                                    'build-{}.log'.format(ts))
            shutil.copy(result.log, log_path)
        if result.apt_conf_path:
            apt_conf_path = os.path.join(self.artifacts_dir,
                                         'apt-{0}.conf'.format(ts))
            shutil.copy(result.apt_conf_path, apt_conf_path)
            self.logger.info('saved apt.conf to {0}'.format(apt_conf_path))
        else:
            self.logger.error('apt.conf is not found')
        for item in result.artifacts:
            name = os.path.basename(item)
            dst_name = os.path.join(self.artifacts_dir, name)
            os.link(item, dst_name)

    @staticmethod
    def generate_apt_conf(apt_conf_path, development_mode):
        """
        Generates an apt configuration file (apt.conf) for pbuilder.

        The generated apt.conf file enables unsigned packages installation
        which is required for the Build System. In the development mode SSL
        verification will be disabled as well.

        Parameters
        ----------
        apt_conf_path : str
            Apt configuration file path.
        development_mode : bool
            Enable development mode if True.
        """
        with open(apt_conf_path, 'w') as fd:
            fd.write('APT::Get::AllowUnauthenticated "true";\n')
            fd.write('Acquire::AllowInsecureRepositories "true";\n')
            # NOTE: this is required to make apt work with outdated Debian
            #       archive repositories
            fd.write('Acquire::Check-Valid-Until "false";\n')
            if development_mode:
                fd.write('Acquire::https {\n    Verify-Peer "false";\n    '
                         'Verify-Host "false";\n}\n')

    @property
    def environment_vars(self):
        """
        Returns environment variables for pdebuild command execution.

        Returns
        -------
        dict
            pdebuild environment variables.
        """
        env = {'HISTFILE': '/dev/null', 'LANG': 'en_US.UTF-8'}
        for key, value in self.task['build'].get('definitions', {}).items():
            if key not in self.__config_keys:
                env[key] = value
        return env

    @property
    def config_vars(self):
        """
        Returns pbuilder config file variables.

        Returns
        -------
        dict
            pbuilder config file variables.
        """
        options = self.task['build'].get('definitions', {})
        res = {key: value for key, value in options.items()
               if key in self.__config_keys}
        res.update({'arch': self.task['build']['arch']})
        return res


def get_pbuilder_distr(config):
    """
    Returns distribution name from pbuilder config
    Parameters
    ----------
    config : str
        pbuilder config

    Returns
    -------
    str
        Name of distribution. E.g. 'bionic', 'focal'
    """
    re_distr = 'DISTRIBUTION=\"([\w\"]*)\"'
    re_result = re.search(re_distr, config)
    if re_result:
        distr = re_result.groups()[0]
    else:
        distr = 'bionic'
    return distr
