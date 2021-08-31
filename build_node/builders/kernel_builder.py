# -*- mode:python; coding:utf-8; -*-
# author: Vasiliy Kleschov <vkleschov@cloudlinux.com>
# created: 2017-11-14

"""
Kernel RPM package builder
"""

import os
import re
import time
import shutil
import tarfile
import traceback

from build_node.utils.git_utils import git_push, git_create_tag

from build_node.errors import DataNotFoundError
from .base_builder import measure_stage
from .base_rpm_builder import BaseRPMBuilder
from ..build_node_errors import BuildError


__all__ = ['KernelBuilder']


class KernelBuilder(BaseRPMBuilder):

    def __init__(self, config, logger, task, task_dir, artifacts_dir):
        """
        Kernel RPM packages builder initialization.

        Parameters
        ----------
        config : build_node.build_node_config.BuildNodeConfig
            Build node configuration object.
        logger : logging.Logger
            Build node logger object.
        task : dict
            Build task information.
        task_dir : str
            Build task working directory path.
        artifacts_dir : str
            Build artifacts storage directory path.
        """
        super(KernelBuilder, self).__init__(config, logger, task, task_dir,
                                            artifacts_dir)
        self.__lve_kernel_uri = self.__get_build_kwarg('lve_kernel_git_url')
        self.__add_kernel_srcs = self.__get_build_kwarg('additional_sources')
        self.__kmod_lve_uri = self.__get_build_kwarg('kmod_lve_url')
        self.__kmod_lve_branch = self.__get_build_kwarg('kmod_lve_branch',
                                                        default='master')
        self.__koji_kernel_git_uri = self.__get_build_kwarg(
            'koji_kernel_git_uri')
        # TODO Might find another way to automatically set this define
        self.platform = self.task['build'].get('platform_name')
        # TODO Might need to change this/make a bit fancier
        self.dist_ver = 7 if self.platform == 'CL7' else 6

    @measure_stage('build_all')
    def build(self):
        self.logger.info('Preparing kernel sources')
        if self.platform.endswith('h') or \
                not self.is_srpm_build_required(self.task):
            kernel_src_dir = self.unpack_sources()
        elif self.__get_build_kwarg('centos_kernel'):
            kernel_src_dir = self.__prepare_centos_kernel()
        else:
            kernel_src_dir = self.__prepare_kernel_sources()
        return self.build_packages(kernel_src_dir)

    def __prepare_kernel_sources(self):
        """
        Makes all needed preparations to build kernel from sources

        Returns
        -------
        str
            Path to kernel sources

        """
        src_dir = os.path.join(self.task_dir, 'kernel_sources')
        os.makedirs(src_dir)
        lve_kernel_dir = os.path.join(self.task_dir, 'lve-kernel')
        lve_kmod_dir = os.path.join(self.task_dir, 'lve-kmod')
        for needed_dir in (lve_kernel_dir, lve_kmod_dir):
            if not needed_dir:
                raise BuildError('Parameter "{0}" is not defined, '
                                 'exiting'.format(needed_dir))
        # check out all needed sources
        kernel_repo = self.checkout_git_sources(
            lve_kernel_dir, self.task['build']['git'].get('ref'),
            self.task['build']['git'].get('ref_type'), self.__lve_kernel_uri)
        self.checkout_git_sources(lve_kmod_dir, self.__kmod_lve_branch,
                                  'branch', self.__kmod_lve_uri)
        # Copying additional sources to the kernel sources directory
        add_src_dir = os.path.join(lve_kernel_dir, self.__add_kernel_srcs)
        for item in os.listdir(add_src_dir):
            shutil.copy(os.path.join(add_src_dir, item), src_dir)
        # create lve-kmod tarball
        spec = self.locate_spec_file(lve_kernel_dir, self.task)
        clbuildid = self.get_value_from_spec(spec, 'clbuildid')
        lve_ver = self.get_value_from_spec(spec, 'lvever')
        full_lve_ver = '{0}-{1}'.format(lve_ver, clbuildid)
        self.logger.info('Creating lve-kmod tarball')
        tarball_dir_name = 'lve-kmod-{0}'.format(lve_ver)
        tarball_dir = os.path.join(self.task_dir, tarball_dir_name)
        os.makedirs(tarball_dir)
        tarball_file = os.path.join(src_dir, 'lve-kmod-{0}.tar.gz'.
                                    format(lve_ver))
        for part in ['common', 'ksrc']:
            shutil.copytree(os.path.join(lve_kmod_dir, part),
                            os.path.join(tarball_dir, part))
        with tarfile.open(tarball_file, 'w:gz') as tar:
            tar.add(tarball_dir, tarball_dir_name)
        self.logger.info('lve-kmod tarbal was created')
        # Need to do it for successfull sources check
        shutil.copy(tarball_file, add_src_dir)
        try:
            git_tag = '{0}-el{1}'.format(full_lve_ver, self.dist_ver)
            git_create_tag(lve_kmod_dir, git_tag, force=True)
            git_push(lve_kmod_dir, self.__kmod_lve_uri, tags=True)
        except Exception as e:
            self.logger.error(
                'can not update remote repository {0}: {1}\n'
                ' Traceback:\n{2}'.format(self.__kmod_lve_uri,
                                          str(e),
                                          traceback.format_exc()))
            raise BuildError(str(e))
        self.prepare_koji_sources(kernel_repo, lve_kernel_dir, src_dir,
                                  src_suffix_dir=self.__add_kernel_srcs)
        self.logger.info('Kernel sources are prepared')
        return src_dir

    def __prepare_centos_kernel(self):
        """
        Makes all needed preparations to build centos kernel from sources

        Returns
        -------
        str
            Path to kernel sources

        """
        git_sources_dir = os.path.join(self.task_dir, 'git_sources')
        os.makedirs(git_sources_dir)
        # checkout all needed project sources
        self.checkout_git_sources(
            git_sources_dir, self.task['build']['git'].get('ref'),
            self.task['build']['git'].get('ref_type'),
            self.__koji_kernel_git_uri)
        source_dir = git_sources_dir
        spec_file = self.locate_spec_file(source_dir,
                                          self.task)
        # get Source0 from spec for tarball
        with open(spec_file, 'r') as fd:
            re_rslt = re.search(r'Source0.*', fd.read(), re.MULTILINE)
            if re_rslt:
                tar_source_name = re_rslt.group(0).split(' ')[1].\
                    split('.tar')[0]
            else:
                raise DataNotFoundError('Source data is not found in spec')
        tarball_file = os.path.join(source_dir,
                                    '{0}.tar.bz2'.format(tar_source_name))
        self.logger.info('Creating centos kernel tarball')
        # Create centos kernel tarball if there is no one in the sources
        if not os.path.exists(tarball_file):
            add_srcs_dir = os.path.join(source_dir, self.__add_kernel_srcs)
            with tarfile.open(tarball_file, 'w:bz2') as tar:
                tar.add(source_dir, tar_source_name,
                        filter=lambda tarinfo: None
                        if self.__add_kernel_srcs in
                           os.path.splitext(tarinfo.name)[0]
                        else tarinfo)
            self.logger.info('centos kernel tarball was created')
            # Copying additional sources to the kernel sources directory
            for item in os.listdir(add_srcs_dir):
                full_file = os.path.join(add_srcs_dir, item)
                shutil.copy(full_file, source_dir)
        self.logger.info('Kernel sources are prepared')
        return source_dir

    @staticmethod
    def get_value_from_spec(spec_file, field_name):
        """
        Extracts given field value from the CL7 kernel spec file.

        Parameters
        ----------
        spec_file : str
            Spec file path.

        Returns
        -------
        str
            Extracted 'clbuildid' constant value.
        """
        with open(spec_file, 'r') as fd:
            re_rslt = re.search(r'^%(define|global)\s+{0}\s+(\S*?)$'.
                                format(field_name), fd.read(), re.MULTILINE)
            if re_rslt:
                return re_rslt.group(2)
        raise BuildError('cannot extract "{0}" constant value'.
                         format(field_name))

    def __get_build_kwarg(self, arg_name, default=None):
        return self.task['build'].get('builder', {}).get('kwargs', {}).\
            get(arg_name, default)

    @staticmethod
    def add_gerrit_ref_to_spec(spec_file, ref):
        """

        Parameters
        ----------
        spec_file   : str
            Spec file path
        ref         : str
            Gerrit change reference

        Returns
        -------

        """
        new_release_str = '%define clbuildid {release}.{change}.{patch_set}.' \
                          '{timestamp}\n'
        try:
            _, _, _, change, patch_set = ref.split("/")
        except Exception as e:
            raise BuildError('cannot parse gerrit reference {0!r}: {1}. '
                             'Traceback:\n{2}'.
                             format(ref, str(e), traceback.format_exc()))
        try:
            with open(spec_file, 'r+') as fd:
                lines = []
                for line in fd:
                    re_rslt = re.search(r'^%define clbuildid\s*([^\s#]+)',
                                        line, re.IGNORECASE)
                    if re_rslt:
                        lines.append(new_release_str.format(
                            release=re_rslt.group(1),
                            change=change,
                            patch_set=patch_set,
                            timestamp=int(time.time())))
                    else:
                        lines.append(line)
                fd.seek(0)
                fd.writelines(lines)
                fd.truncate()
        except Exception as e:
            raise BuildError('cannot add gerrit revision to spec file: {0}. '
                             'Traceback:\n{1}'.
                             format(str(e), traceback.format_exc()))
