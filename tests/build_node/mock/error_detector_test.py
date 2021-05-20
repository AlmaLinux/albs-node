# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-09-21

"""Mock errors detection module unit tests."""

import unittest

from build_node.mock.error_detector import *
from build_node.mock.error_detector import build_log_changelog_order, \
    build_log_excluded_arch, build_log_hangup, build_log_spec_section_failed, \
    build_log_timeout, build_log_missing_file, build_log_unpackaged, \
    root_log_repository, root_log_no_space, root_log_unmet_dependency

__all__ = ['TestErrorDetector']


class TestErrorDetector(unittest.TestCase):

    def setUp(self):
        self.sample_str = 'some irrelevant string'

    def test_build_log_changelog_order(self):
        """build_node.mock.error_detector.build_log_changelog_order detects \
invalid changelog records order error"""
        error = 'BUILDSTDERR: error: %changelog not in descending ' \
                'chronological order'
        self.assertEqual(build_log_changelog_order(error),
                         (MOCK_ERR_CHANGELOG_ORDER,
                          '%changelog not in descending chronological order'))
        self.assertIsNone(build_log_changelog_order(self.sample_str))

    def test_build_log_excluded_arch(self):
        """build_node.mock.error_detector.build_log_excluded_arch detects \
excluded architecture error"""
        error = 'BUILDSTDERR: error: No compatible architectures found for ' \
                'build'
        self.assertEqual(build_log_excluded_arch(error),
                         (MOCK_ERR_ARCH_EXCLUDED,
                          'target architecture is not compatible'))
        error = 'BUILDSTDERR: error: Architecture is not included: i686'
        self.assertEqual(build_log_excluded_arch(error),
                         (MOCK_ERR_ARCH_EXCLUDED,
                          'architecture "i686" is excluded'))
        self.assertIsNone(build_log_excluded_arch(self.sample_str))

    def test_build_log_hangup(self):
        """build_node.mock.error_detector.build_log_hangup detects hangup error"""
        error = 'BUILDSTDERR: /var/tmp/rpm-tmp.tsNw8d: line 51: 3458731 ' \
                'Hangup                  $PWD/sapi/cli/php../run-tests.php ' \
                '-d extension_dir=$PWD/modules/  ' \
                '${PHP_TEST_SHARED_EXTENSIONS} -l tests.lst --show-all'
        self.assertEqual(build_log_hangup(error),
                         (MOCK_ERR_BUILD_HANGUP,
                          'build is hanged-up (probably a build node was '
                          'overloaded)'))
        self.assertIsNone(build_log_hangup(self.sample_str))

    def test_build_log_spec_section_failed(self):
        """build_node.mock.error_detector.build_log_spec_section_failed detects \
spec section error"""
        error = 'BUILDSTDERR: error: Bad exit status from ' \
                '/var/tmp/rpm-tmp.G4bZj0 (%build)'
        self.assertEqual(build_log_spec_section_failed(error),
                         (MOCK_ERR_SPEC_SECTION,
                          'spec file "%build" section failed'))
        self.assertIsNone(build_log_spec_section_failed(self.sample_str))

    def test_build_log_timeout(self):
        """build_node.mock.error_detector.build_log_timeout detects build timeout \
error"""
        error = 'commandTimeoutExpired: Timeout(112) expired for command:'
        self.assertEqual(build_log_timeout(error),
                         (MOCK_ERR_TIMEOUT,
                          'build timeout 112 second(s) expired'))
        self.assertIsNone(build_log_timeout(self.sample_str))

    def test_build_log_missing_file(self):
        """build_node.mock.error_detector.build_log_missing_file detects missing \
file error"""
        error = 'error: File /builddir/build/SOURCES/test_project-x86_64.zip:' \
                ' No such file or directory'
        self.assertEqual(build_log_missing_file(error),
                         (MOCK_ERR_MISSING_FILE,
                          'file "/builddir/build/SOURCES/test_project-x86_64.'
                          'zip" is not found'))
        self.assertIsNone(build_log_missing_file(self.sample_str))

    def test_build_log_unpackaged(self):
        """build_node.mock.error_detector.build_log_unpackaged detects unpackaged \
file error"""
        error = 'BUILDSTDERR: error: Installed (but unpackaged) file(s) found:'
        self.assertEqual(build_log_unpackaged(error),
                         (MOCK_ERR_UNPACKAGED,
                          'installed but unpackaged file(s) found'))
        self.assertIsNone(build_log_unpackaged(self.sample_str))

    def test_root_log_repository(self):
        """build_node.mock.error_detector.root_log_repository detects repository \
error"""
        error = 'failure: repodata/repomd.xml from cl7-updates: [Errno 256] ' \
                'No more mirrors to try.'
        self.assertEqual(root_log_repository(error),
                         (MOCK_ERR_REPO, '"cl7-updates" repository error: '
                                         '[Errno 256] No more mirrors to try'))
        self.assertIsNone(root_log_repository(self.sample_str))

    def test_root_log_no_space(self):
        """build_node.mock.error_detector.root_log_no_space detects insufficient \
space error"""
        error = 'DEBUG util.py:485:  Error: Insufficient space in download ' \
                'directory /var/lib/mock/'
        self.assertEqual(root_log_no_space(error),
                         (MOCK_ERR_NO_FREE_SPACE,
                          'insufficient space in download directory'))
        self.assertIsNone(root_log_no_space(self.sample_str))

    def test_root_log_unmet_dependency(self):
        """build_node.mock.error_detector.root_log_unmet_dependency detects unmet \
dependency error"""
        error = 'DEBUG util.py:484:  Error: No Package found for ' \
                'ea-openssl-devel >= 1:1.0.2n-3'
        self.assertEqual(root_log_unmet_dependency(error),
                         (MOCK_ERR_UNMET_DEPENDENCY,
                          'unmet dependency "ea-openssl-devel >= 1:1.0.2n-3"'))
        self.assertIsNone(root_log_unmet_dependency(self.sample_str))
