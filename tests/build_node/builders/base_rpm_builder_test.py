# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-12-03

"""
build_node.builders.base_rpm_builder_tests module unit tests.
"""

import copy
import re
import time
import os

import logging
from unittest.mock import patch, Mock
from pyfakefs.fake_filesystem_unittest import TestCase

from build_node.build_node_errors import BuildConfigurationError
from build_node.builders.base_rpm_builder import BaseRPMBuilder
from build_node.build_node_errors import BuildError
from build_node.mock.mock_environment import MockError, MockResult
from build_node.mock.mock_config import MockConfig
from build_node.utils.file_utils import clean_dir


class TestConfigureMockNpmProxy(TestCase):

    def setUp(self):
        self.setUpPyfakefs()
        config_dir = '/test/mock/'
        os.makedirs(config_dir)
        self.config_file = os.path.join(config_dir, 'mock.cfg')
        self.mock_config = MockConfig('x86_64')
        self.npm_proxy = 'http://127.0.0.1:8080/'

    def test_npm_proxy(self):
        """
        BaseRPMBuilder.configure_mock_npm_proxy configures NPM and Yarn proxy
        """
        BaseRPMBuilder.configure_mock_npm_proxy(self.mock_config,
                                                self.npm_proxy)
        self.mock_config.dump_to_file(self.config_file)
        with open(self.config_file, 'r') as fd:
            config = fd.read()
        npm_file = self.__get_file_option(config, '/usr/etc/npmrc')
        self.__validate_npm_config(npm_file)
        yarn_file = self.__get_file_option(config, '/usr/etc/yarnrc')
        self.__validate_yarn_config(yarn_file)

    def test_invalid_proxy_url(self):
        """BaseRPMBuilder.configure_mock_npm_proxy reports invalid proxy URL"""
        self.assertRaises(BuildConfigurationError,
                          BaseRPMBuilder.configure_mock_npm_proxy,
                          self.mock_config, 'bad proxy')

    def __get_file_option(self, config, file_name):
        """
        Extracts an environment file content from a mock configuration file.
        """
        regex = r'config_opts\["files"\]\["{0}"\]\s*?=\s*?"""(.*?)"""'.\
            format(file_name)
        re_rslt = re.search(regex, config, flags=re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(re_rslt,
                             '{0} mock file is not found'.format(file_name))
        return re_rslt.group(1)

    def __validate_npm_config(self, npm_config):
        """Validates npm configuration file proxy options."""
        regexes = (r'https-proxy\s*?=\s*?{0}'.format(self.npm_proxy),
                   r'proxy\s*?=\s*?{0}'.format(self.npm_proxy),
                   r'strict-ssl\s*?=\s*?false')
        for regex in regexes:
            self.assertIsNotNone(re.search(regex, npm_config,
                                           flags=re.MULTILINE),
                                 '{0} option is not found'.format(regex))

    def __validate_yarn_config(self, yarn_config):
        """Validates yarn configuration file proxy options."""
        regexes = (r'https-proxy\s+?"{0}"'.format(self.npm_proxy),
                   r'proxy\s+?"{0}"'.format(self.npm_proxy),
                   r'strict-ssl\s+?false')
        for regex in regexes:
            self.assertIsNotNone(re.search(regex, yarn_config,
                                           flags=re.MULTILINE),
                                 '{0} option is not found'.format(regex))


class TestLocateSpecFile(TestCase):

    def setUp(self):
        self.setUpPyfakefs()
        self.test_dir = '/locate_spec_test'
        self.task = {'build': {'project_name': 'my-project'}}
        os.mkdir(self.test_dir)
        with open(os.path.join(self.test_dir, 'README.md'), 'w') as fd:
            fd.write('# Test project')
        for dir_name in ('src', 'tests'):
            os.mkdir(os.path.join(self.test_dir, dir_name))

    def test_project_name_spec(self):
        """
        BaseRPMBuilder.locate_spec_file should find spec corresponding to \
project name
        """
        spec_file = os.path.join(self.test_dir, 'my-project.spec')
        with open(spec_file, 'w') as fd:
            fd.write('Name: my-project')
        self.assertEqual(BaseRPMBuilder.locate_spec_file(self.test_dir,
                                                         self.task),
                         spec_file)

    def test_first_found_spec(self):
        """
        BaseRPMBuilder.locate_spec_file should find any spec
        """
        for i in range(3):
            with open(os.path.join(self.test_dir,
                                   'random.{0}.spec'.format(i)), 'w') as fd:
                fd.write('Name: random-project-{0}'.format(i))
        found_spec = BaseRPMBuilder.locate_spec_file(self.test_dir, self.task)
        path, name = os.path.split(found_spec)
        self.assertTrue(path == self.test_dir and name.endswith('.spec'))

    def test_not_found_spec(self):
        """
        BaseRPMBuilder.locate_spec_file should raise BuildError if there is no
        spec found
        """
        with self.assertRaises(BuildError):
            BaseRPMBuilder.locate_spec_file(self.test_dir, self.task)


class TestIsSrpmBuildExcluded(TestCase):

    def setUp(self):
        self.setUpPyfakefs()
        self.test_dir = '/test/lib/mock/result'
        self.build_log_path = os.path.join(self.test_dir, 'build.log')
        os.makedirs(self.test_dir)
        for file_name in ('hw_info.log', 'root.log', 'state.log',
                          'installed_pkgs.log'):
            with open(os.path.join(self.test_dir, file_name), 'w') as fd:
                fd.write('dummy file content\n')
        self.mock_error = MockError('mock', 1, 'stdout', 'stderr', None,
                                    resultdir=self.test_dir)

    def test_excluded_build(self):
        """BaseRPMBuilder.is_srpm_build_excluded detects incompatible \
architecture"""
        with open(self.build_log_path, 'w') as fd:
            fd.write('BUILDSTDERR: Mock Version: 1.4.9\nBUILDSTDERR: error: '
                     'No compatible architectures found for build\nBuilding '
                     'target platforms: i686\n')
        excluded, reason = \
            BaseRPMBuilder.is_srpm_build_excluded(self.mock_error)
        self.assertTrue(excluded)
        self.assertRegex(reason, r'architecture\s+is\s+not\s+compatible')

    def test_normal_build(self):
        """BaseRPMBuilder.is_srpm_build_excluded ignores normal builds"""
        with open(self.build_log_path, 'w') as fd:
            fd.write('Mock Version: 1.4.9\nBuilding for target x86_64\n')
        self.assertEqual(BaseRPMBuilder.is_srpm_build_excluded(self.mock_error),
                         (False, None))


class TestSaveBuildArtifacts(TestCase):

    """BaseRPMBuilder.save_build_artifacts unit tests."""

    def setUp(self):
        self.setUpPyfakefs()
        self.artifacts_dir = '/test/artifacts'
        os.makedirs(self.artifacts_dir)
        self.mock_dir = '/test/mock'
        os.makedirs(self.mock_dir)
        self.mock_stderr = 'Mock\nStandard\nError\n'
        self.mock_config = 'Mock\nConfiguration\nFile\n'
        self.mock_build_log = 'Mock Version: 1.4.11\nMock build log\n'
        self.mock_root_log = 'INFO buildroot.py:350:  Mock Version: 1.4.11\n'
        with open(os.path.join(self.mock_dir, 'build.log'), 'w') as fd:
            fd.write(self.mock_build_log)
        with open(os.path.join(self.mock_dir, 'root.log'), 'w') as fd:
            fd.write(self.mock_root_log)
        self.mock_result = MockResult('mock -r cloudlinux-7-x86_64', 0,
                                      '', self.mock_stderr, self.mock_config,
                                      self.mock_dir)
        self.srpm_artifacts_values = {False: '', True: '.srpm'}
        self.task = {'build': {}}
        # noinspection PyTypeChecker,PyTypeChecker
        self.builder = BaseRPMBuilder(Mock(), Mock(), self.task, Mock(),
                                      self.artifacts_dir)

    def test_saves_src_rpm(self):
        """BaseRPMBuilder.save_build_artifacts saves source RPM"""
        srpm_path = os.path.join(self.mock_dir,
                                 'test-package-1.2-1.el7.src.rpm')
        content = 'Source RPM file content\n'
        with open(srpm_path, 'w') as fd:
            fd.write(content)
        self.__test_saves_file(r'^test-package-1\.2-1\.el7\.src\.rpm$',
                               content)

    def test_saves_rpm(self):
        """BaseRPMBuilder.save_build_artifacts saves RPM package"""
        rpm_path = os.path.join(self.mock_dir,
                                'test-package-1.2-1.el7.x86_64.rpm')
        content = 'RPM file content\n'
        with open(rpm_path, 'w') as fd:
            fd.write(content)
        self.__test_saves_file(r'^test-package-1\.2-1\.el7\.x86_64\.rpm$',
                               content)

    def test_saves_debuginfo(self):
        """BaseRPMBuilder.save_build_artifacts saves debug RPM package"""
        debug_path = os.path.join(self.mock_dir,
                                  'test-package-debuginfo-1.2-1.el7.x86_64.rpm')
        content = 'Debug RPM file content\n'
        with open(debug_path, 'w') as fd:
            fd.write(content)
        regex = r'^test-package-debuginfo-1\.2-1\.el7\.x86_64\.rpm'
        self.__test_saves_file(regex, content)

    def test_saves_mock_build_log(self):
        """BaseRPMBuilder.save_build_artifacts saves mock build log"""
        self.__test_saves_file(r'^mock_build{0}\.\d+\.log$',
                               self.mock_build_log)

    def test_saves_mock_root_log(self):
        """BaseRPMBuilder.save_build_artifacts saves mock root log"""
        self.__test_saves_file(r'^mock_root{0}\.\d+\.log$',
                               self.mock_root_log)

    def test_saves_mock_config(self):
        """BaseRPMBuilder.save_build_artifacts saves mock config"""
        self.__test_saves_file(r'^mock{0}\.\d+\.cfg$', self.mock_config)

    def test_saves_mock_stderr(self):
        """BaseRPMBuilder.save_build_artifacts saves mock stderr"""
        self.__test_saves_file(r'mock_stderr{0}\.\d+\.log$', self.mock_stderr)

    def __test_saves_file(self, regex, content):
        for srpm_artifacts, suffix in self.srpm_artifacts_values.items():
            self.builder.save_build_artifacts(self.mock_result, srpm_artifacts)
            file_path = self.__locate_artifact(regex.format(suffix))
            with open(file_path, 'r') as fd:
                self.assertEqual(content, fd.read())
            self.__validate_timestamp(file_path)
            clean_dir(self.artifacts_dir)

    def __locate_artifact(self, regex):
        for file_name in os.listdir(self.artifacts_dir):
            if re.search(regex, file_name):
                return os.path.join(self.artifacts_dir, file_name)
        self.fail('there is no artifact matching {0!r}'.format(regex))

    def __validate_timestamp(self, file_name):
        re_rslt = re.search(r'.*?\.(\d+)\.(log|cfg|rpm|src\.rpm)$', file_name)
        if re_rslt:
            ts = int(re_rslt.group(1))
            delta = int(time.time()) - ts
            self.assertTrue(0 <= delta <= 30,
                            '{0} timestamp is invalid'.format(file_name))


class TestIsBuildExcluded(TestCase):

    """BaseRPMBuilder.is_build_excluded unit tests."""

    def setUp(self):
        self.meta = {'name': 'alt-python27', 'epoch': 0, 'version': '2.7.15',
                     'release': '1.el7'}
        task = {'build': {'arch': 'x86_64'}}
        # noinspection PyTypeChecker,PyTypeChecker,PyTypeChecker
        self.builder = BaseRPMBuilder(Mock(), None, task, None, None)

    @patch('build_node.builders.base_rpm_builder.extract_metadata')
    def test_exclusive_arch(self, extract_metadata):
        """BaseRPMBuilder.is_build_excluded detects ExclusiveArch"""
        meta = copy.copy(self.meta)
        meta['exclusivearch'] = 'i686'
        extract_metadata.return_value = meta
        status, message = self.builder.is_build_excluded('test.rpm')
        self.assertTrue(status)
        self.assertRegex(message,
                         r'architecture is not listed in ExclusiveArch')

    @patch('build_node.builders.base_rpm_builder.extract_metadata')
    def test_exclude_arch(self, extract_metadata):
        """BaseRPMBuilder.is_build_excluded detects ExcludeArch"""
        meta = copy.copy(self.meta)
        # noinspection PyTypeChecker
        meta['excludearch'] = ['x86_64', 'i686']
        extract_metadata.return_value = meta
        status, message = self.builder.is_build_excluded('test.rpm')
        self.assertTrue(status)
        self.assertRegex(message, r'architecture is listed in ExcludeArch')

    @patch('build_node.builders.base_rpm_builder.extract_metadata')
    def test_normal_build(self, extract_metadata):
        """BaseRPMBuilder.is_build_excluded skips non-excluded src-RPM"""
        extract_metadata.return_value = self.meta
        self.assertEqual(self.builder.is_build_excluded('test.rpm'),
                         (False, None))


class TestIsKojiSources(TestCase):

    """BaseRPMBuilder.is_koji_sources unit tests."""

    def setUp(self):
        self.setUpPyfakefs()
        self.sources_dir = '/test/sources'
        os.makedirs(self.sources_dir)

    def test_koji_builder(self):
        """BaseRPMBuilder.is_koji_sources matches KojiBuilder"""
        task = self.__generate_task('KojiBuilder',
                                    'cla.build_system.builders.koji_builder')
        self.assertTrue(BaseRPMBuilder.is_koji_sources(self.sources_dir, task))

    def test_alt_php_builder(self):
        """BaseRPMBuilder.is_koji_sources matches AltPHPBuilder"""
        task = self.__generate_task('AltPHPBuilder',
                                    'cla.build_system.builders.alt_php_builder')
        self.assertTrue(BaseRPMBuilder.is_koji_sources(self.sources_dir, task))

    def test_cl6_lve_kernel_kmod_builder(self):
        """BaseRPMBuilder.is_koji_sources matches CL6LveKernelKmodBuilder"""
        task = self.__generate_task('CL6LveKernelKmodBuilder',
                                    'cla.build_system.builders.kernel')
        self.assertTrue(BaseRPMBuilder.is_koji_sources(self.sources_dir, task))

    def test_cl_package_builder(self):
        """BaseRPMBuilder.is_koji_sources ignores CLPackageBuilder"""
        task = \
            self.__generate_task('CLPackageBuilder',
                                 'cla.build_system.builders.cl_package_builder')
        self.assertFalse(BaseRPMBuilder.is_koji_sources(self.sources_dir, task))

    def test_no_builder(self):
        """BaseRPMBuilder.is_koji_sources ignores undefined builder"""
        task = {'build': {}}
        self.assertFalse(BaseRPMBuilder.is_koji_sources(self.sources_dir, task))

    def __generate_task(self, builder_class, builder_module):
        return {'build': {'builder': {'class': builder_class,
                                      'module': builder_module}}}


class TestIsSrpmBuildRequired(TestCase):

    """BaseRPMBuilder.is_srpm_build_required unit tests"""

    def test_srpm_in_task(self):
        """
        BaseRPMBuilder.is_srpm_build_required returns False for existent src-RPM
        """
        task = {'srpm': {'url': 'http://example.com/test-package.src.rpm'}}
        self.assertFalse(BaseRPMBuilder.is_srpm_build_required(task))

    def test_no_srpm(self):
        """
        BaseRPMBuilder.is_srpm_build_required returns True for missing src-RPM
        """
        self.assertTrue(BaseRPMBuilder.is_srpm_build_required({}))


class TestUnpackSources(TestCase):

    """BaseRPMBuilder.unpack_sources unit tests"""

    def setUp(self):
        self.setUpPyfakefs()
        self.config = Mock()
        self.config.development_mode = False
        self.config.node_id = 'build_node_id'
        self.config.jwt_token = 'test JWT token'
        self.artifacts_dir = '/test/artifacts'
        os.makedirs(self.artifacts_dir)
        self.task_dir = '/test/task_dir'
        os.makedirs(self.task_dir)
        self.srpm_url = 'https://example.com/test-package-1-1.el7.src.rpm'
        self.srpm_path = \
            '/test/task_dir/srpm_sources/test-package-1-1.el7.src.rpm'
        self.task = {'build': {},
                     'srpm': {'type': 'external',
                              'url': self.srpm_url}}
        self.logger = logging.getLogger('BaseRPMBuilderLogger')
        # SSL download credentials
        self.ca_info = '/test/subscription/example-uep.pem'
        self.ssl_key = '/test/subscription/example-key.pem'
        self.ssl_cert = '/test/subscription/example-cert.pem'

    @patch('build_node.builders.base_rpm_builder.unpack_src_rpm')
    @patch('build_node.builders.base_rpm_builder.download_file')
    def test_external_without_credentials(self, download_file, unpack_src_rpm):
        """BaseRPMBuilder.unpack_sources unpacks external src-RPM without \
authentication"""
        download_file.return_value = self.srpm_path
        builder = BaseRPMBuilder(self.config, self.logger, self.task,
                                 self.task_dir, self.artifacts_dir)
        src_dir = builder.unpack_sources()
        self.__check_src_dir_exists(src_dir)
        self.__check_unpack_src_rpm_call(unpack_src_rpm)
        self.__check_download_file_call(download_file)

    @patch('build_node.builders.base_rpm_builder.unpack_src_rpm')
    @patch('build_node.builders.base_rpm_builder.download_file')
    def test_external_with_credentials(self, download_file, unpack_src_rpm):
        """BaseRPMBuilder.unpack_sources unpacks external src-RPM with \
authentication"""
        credentials = {'ca_info': self.ca_info, 'ssl_key': self.ssl_key,
                       'ssl_cert': self.ssl_cert}
        task = copy.deepcopy(self.task)
        # noinspection PyTypeChecker
        task['srpm']['download_credentials'] = credentials
        download_file.return_value = self.srpm_path
        builder = BaseRPMBuilder(self.config, self.logger, task,
                                 self.task_dir, self.artifacts_dir)
        src_dir = builder.unpack_sources()
        self.__check_src_dir_exists(src_dir)
        self.__check_unpack_src_rpm_call(unpack_src_rpm)
        self.__check_download_file_call(download_file, key_args=credentials)

    @patch('build_node.builders.base_rpm_builder.unpack_src_rpm')
    @patch('build_node.builders.base_rpm_builder.download_file')
    def test_internal_production(self, download_file, unpack_src_rpm):
        """BaseRPMBuilder.unpack_sources unpacks internal src-RPM in \
production mode"""
        task = copy.deepcopy(self.task)
        task['srpm']['type'] = 'internal'
        download_file.return_value = self.srpm_path
        builder = BaseRPMBuilder(self.config, self.logger, task,
                                 self.task_dir, self.artifacts_dir)
        src_dir = builder.unpack_sources()
        self.__check_src_dir_exists(src_dir)
        self.__check_unpack_src_rpm_call(unpack_src_rpm)
        self.__check_download_file_call(
            download_file, key_args={'login': self.config.node_id,
                                     'password': self.config.jwt_token}
        )

    @patch('build_node.builders.base_rpm_builder.unpack_src_rpm')
    @patch('build_node.builders.base_rpm_builder.download_file')
    def test_internal_development(self, download_file, unpack_src_rpm):
        """BaseRPMBuilder.unpack_sources unpacks internal src-RPM in \
development mode"""
        self.config.development_mode = True
        task = copy.deepcopy(self.task)
        task['srpm']['type'] = 'internal'
        download_file.return_value = self.srpm_path
        builder = BaseRPMBuilder(self.config, self.logger, task,
                                 self.task_dir, self.artifacts_dir)
        src_dir = builder.unpack_sources()
        self.__check_src_dir_exists(src_dir)
        self.__check_unpack_src_rpm_call(unpack_src_rpm)
        self.__check_download_file_call(
            download_file, key_args={'login': self.config.node_id,
                                     'password': self.config.jwt_token,
                                     'no_ssl_verify': True}
        )

    def __check_src_dir_exists(self, src_dir):
        self.assertTrue(os.path.isdir(src_dir),
                        'src-RPM sources directory is not created')

    def __check_download_file_call(self, download_file, key_args=None):
        (srpm_url_arg, _), kwargs = download_file.call_args
        self.assertEqual(srpm_url_arg, self.srpm_url,
                         'download_file has been called with wrong src-RPM URL')
        if key_args:
            self.assertDictContainsSubset(key_args, kwargs)

    def __check_unpack_src_rpm_call(self, unpack_src_rpm):
        unpack_src_rpm.assert_called_with(self.srpm_path,
                                          os.path.dirname(self.srpm_path))
