# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-07-22

"""Debian utility functions unit tests."""

import os
import shutil
import tempfile
import unittest
from unittest import mock
from pyfakefs.fake_filesystem_unittest import TestCase

from build_node.errors import CommandExecutionError
from build_node.utils.test_utils import unload_plumbum_modules, MockShellCommand
from build_node.build_node_errors import BuildError
from build_node.utils.debian_utils import string_to_ds

__all__ = ['TestGetDebianSourceName']


class DebianUtilsShellTest(unittest.TestCase):

    """Base class for build_node.utils.debian_utils shell function tests."""

    def setUp(self):
        self.repo_dir = tempfile.mkdtemp(prefix='castor_test')
        self._unload_modules()

    def tearDown(self):
        if os.path.exists(self.repo_dir):
            shutil.rmtree(self.repo_dir)
        self._unload_modules()

    @staticmethod
    def _unload_modules():
        unload_plumbum_modules('build_node.utils.debian_utils')


class TestGetDebianSourceName(DebianUtilsShellTest):

    """build_node.utils.debian_utils.get_debian_source_name function unit tests."""

    def test_gets_source_name(self):
        """build_node.utils.debian_utils.dpkg_parsechangelog returns source \
package name"""
        self.__verify_results('Source', 'example_package')

    def test_gets_source_version(self):
        """build_node.utils.debian_utils.dpkg_parsechangelog returns source \
package version"""
        self.__verify_results('Version', '3.5.5-1cloudlinux')

    def test_no_changelog(self):
        """build_node.utils.debian_utils.dpkg_parsechangelog throws error if \
changelog is missing"""
        user_code = r"""
print("tail: cannot open 'debian/changelog' for reading: No " \
      "such file or directory", file=sys.stderr)
print("dpkg-parsechangelog: error: tail of debian/changelog " \
      "gave error exit status 1", file=sys.stderr)
sys.exit(1)
        """
        with MockShellCommand('dpkg-parsechangelog', user_code):
            from build_node.utils.debian_utils import dpkg_parsechangelog
            with self.assertRaisesRegex(CommandExecutionError,
                                        'can not parse debian/changelog'):
                dpkg_parsechangelog(self.repo_dir, 'Source')

    def test_empty_output(self):
        """build_node.utils.debian_utils.dpkg_parsechangelog throws error if \
output is empty"""
        with MockShellCommand('dpkg-parsechangelog', 'print(" ")'):
            from build_node.utils.debian_utils import dpkg_parsechangelog
            with self.assertRaisesRegex(CommandExecutionError,
                                         'there is no package source'):
                dpkg_parsechangelog(self.repo_dir, 'Source')

    def __verify_results(self, field, expected):
        user_code = 'print("{0}")'.format(expected)
        with MockShellCommand('dpkg-parsechangelog', user_code) as cmd:
            from build_node.utils.debian_utils import dpkg_parsechangelog
            package_name = dpkg_parsechangelog(self.repo_dir, field)
            self.assertEqual(package_name, expected)
            self.assertEqual(cmd.get_calls()[0]['argv'][1:],
                             ['--show-field', field])
            self.assertEqual(cmd.get_calls()[0]['cwd'], self.repo_dir)


class TestDchAddChangelogRecord(DebianUtilsShellTest):

    """
    build_node.utils.debian_utils.dch_add_changelog_record function unit tests.
    """

    def setUp(self):
        super(TestDchAddChangelogRecord, self).setUp()
        self.distribution = 'unstable'
        self.changelog = 'example changelog record'

    def test_without_version(self):
        """
        build_node.utils.git_utils.dch_add_changelog_record call without version
        """
        with MockShellCommand('dch') as cmd:
            from build_node.utils.debian_utils import dch_add_changelog_record
            dch_add_changelog_record(self.repo_dir, self.distribution,
                                     self.changelog)
            self.__verify_cmd_args(cmd)

    def test_with_version(self):
        """build_node.utils.git_utils.dch_add_changelog_record call with version"""
        version = '20181017-1'
        with MockShellCommand('dch') as cmd:
            from build_node.utils.debian_utils import dch_add_changelog_record
            dch_add_changelog_record(self.repo_dir, self.distribution,
                                     self.changelog, version)
            self.__verify_cmd_args(cmd)
            cmd_args = cmd.get_calls()[0]['argv']
            version_idx = cmd_args.index('--newversion')
            self.assertEqual(cmd_args[version_idx + 1], version)

    def test_with_user_email(self):
        """build_node.utils.git_utils.dch_add_changelog_record call with user name \
and email"""
        user_name = 'Example User'
        user_email = 'user@example.com'
        with MockShellCommand('dch') as cmd:
            from build_node.utils.debian_utils import dch_add_changelog_record
            dch_add_changelog_record(self.repo_dir, self.distribution,
                                     self.changelog, user_email=user_email,
                                     user_name=user_name)
            self.__verify_cmd_args(cmd)
            cmd_env = cmd.get_calls()[0]['env']
            self.assertEqual(cmd_env['EMAIL'], user_email)
            self.assertEqual(cmd_env['NAME'], user_name)

    def test_dch_error(self):
        """build_node.utils.git_utils.dch_add_changelog_record handles dch error"""
        with MockShellCommand('dch', 'sys.exit(1)') as cmd:
            from build_node.utils.debian_utils import dch_add_changelog_record
            with self.assertRaises(CommandExecutionError):
                dch_add_changelog_record(self.repo_dir, self.distribution,
                                         self.changelog)

    def __verify_cmd_args(self, cmd):
        call = cmd.get_calls()[0]
        cmd_args = call['argv']
        self.assertTrue('--no-auto-nmu' in cmd_args)
        self.assertTrue('--force-distribution' in cmd_args)
        distro_idx = cmd_args.index('--distribution')
        self.assertEqual(cmd_args[distro_idx + 1], self.distribution)
        self.assertEqual(cmd_args[-1], self.changelog)
        self.assertEqual(call['cwd'], self.repo_dir)


class TestParseDebVersion(unittest.TestCase):

    def setUp(self):
        self.epoch = '3'
        self.version = '3.2.14'
        self.revision = '1~bpo70+1+b1'

    def test_version(self):
        """build_node.utils.debian_utils.parse_deb_version extracts version"""
        self.__verify(self.version, '0', self.version, '0')

    def test_epoch_version(self):
        """build_node.utils.debian_utils.parse_deb_version extracts epoch and \
version"""
        self.__verify('{0}:{1}'.format(self.epoch, self.version),
                      self.epoch, self.version, '0')

    def test_epoch_version_revision(self):
        """build_node.utils.debian_utils.parse_deb_version extracts epoch, version \
and revision"""
        self.__verify('{0}:{1}-{2}'.format(self.epoch, self.version,
                                           self.revision),
                      self.epoch, self.version, self.revision)

    def __verify(self, version_str, epoch, upstream_version, revision):
        from build_node.utils.debian_utils import parse_deb_version
        self.assertEqual(parse_deb_version(version_str),
                         (epoch, upstream_version, revision))


class TestParseSourcesListUrl(unittest.TestCase):

    def test_single_component(self):
        """build_node.utils.debian_utils.parse_sources_list_url extracts single \
component"""
        self.__verify('deb', 'http://archive.ubuntu.com/ubuntu', 'trusty',
                      ['main'])

    def test_multiple_components(self):
        """build_node.utils.debian_utils.parse_sources_list_url extracts multiple \
components"""
        self.__verify('deb-src', 'http://archive.ubuntu.com/ubuntu',
                      'trusty', ['main', 'contrib', 'non-free'])

    def test_invalid_url(self):
        """build_node.utils.debian_utils.parse_sources_list_url detects invalid \
URL"""
        self.assertRaises(Exception, self.__verify, '',
                          'http://archive.ubuntu.com/ubuntu', 'trusty', [])

    def __verify(self, repo_type, base_url, distro, components):
        url = '{0} {1} {2} {3}'.format(repo_type, base_url, distro,
                                       ' '.join(components))
        from build_node.utils.debian_utils import parse_sources_list_url
        self.assertEqual(parse_sources_list_url(url),
                         {'repo_type': repo_type, 'url': base_url,
                          'distro': distro, 'components': components})


class TestAddTimestampChangelogDeb(TestCase):

    def setUp(self):
        from build_node.utils.debian_utils import add_timestamp_changelog_deb
        self.fn = add_timestamp_changelog_deb
        self.setUpPyfakefs()
        self.tst_chg_dir = '/test_add_timestamp'
        os.makedirs(self.tst_chg_dir)
        self.file_name = 'changelog'
        self.test_changelog = os.path.join(self.tst_chg_dir, self.file_name)

    def test_invalid_ref(self):

        """
        build_node.utils.file_utils.add_timestamp_changelog_deb reports invalid ref
        """
        tst_changelog_file = """
        testnamepackage (0.9.12-1) unstable; urgency=medium
        """
        tst_source_name = """
        testnamepackage
                          """
        with open(self.test_changelog, 'w') as test_file:
            test_file.write(tst_changelog_file)
            test_file.flush()
            self.assertRaises(BuildError, self.fn, test_file.name,
                              tst_source_name, 'a_malformed_ref')

    def test_missing_changelog_file(self):
        """
        build_node.utils.file_utils.add_timestamp_changelog_deb reports missing
        changelog_file
        """
        example_ref = 'refs/changes/20/28520/5'
        tst_source_name = """
        testnamepackage
                          """
        self.assertRaises(BuildError, self.fn, 'a missing file',
                          tst_source_name, example_ref)

    def test_correct_execution(self):
        """
        build_node.utils.file_utils.add_timestamp_changelog_deb reports
        the changelog file was changed and overwritten
        """
        tst_changelog_file = """
        testnamepackage (0.9.12-1) unstable; urgency=medium
        teststring 1
        teststring 2
        testnamepackage (0.9.12-1) unstable; urgency=medium
        teststring 3
        teststring 4
                             """
        example_ref = 'refs/changes/20/28520/5'
        tst_source_name = """
        testnamepackage
                          """
        expected_file = """
        testnamepackage (0.9.12-1.123456.28520.5) unstable; urgency=medium
        teststring 1
        teststring 2
        testnamepackage (0.9.12-1) unstable; urgency=medium
        teststring 3
        teststring 4
                             """
        with open(self.test_changelog, 'w+') as test_file:
            test_file.write(tst_changelog_file)
            test_file.flush()
            with mock.patch('time.time') as fake_time:
                fake_time.return_value = 123456
                self.fn(
                    test_file.name, tst_source_name.strip(), example_ref)
            test_file.seek(0)
            self.assertEqual(expected_file, test_file.read(),
                             msg='File was not changed')

    def test_only_timestamp(self):
        """
        build_node.utils.file_utils.add_timestamp_changelog_deb reports
        the changelog file was changed and overwritten
        """
        tst_changelog_file = """
        testnamepackage (0.9.12-1) unstable; urgency=medium
        teststring 1
        teststring 2
        testnamepackage (0.9.12-1) unstable; urgency=medium
        teststring 3
        teststring 4
                             """

        tst_source_name = """
        testnamepackage
                          """

        expected_file = """
        testnamepackage (0.9.12-1.123456) unstable; urgency=medium
        teststring 1
        teststring 2
        testnamepackage (0.9.12-1) unstable; urgency=medium
        teststring 3
        teststring 4
                             """
        with open(self.test_changelog, 'w+') as test_file:
            test_file.write(tst_changelog_file)
            test_file.flush()
            with mock.patch('time.time') as fake_time:
                fake_time.return_value = 123456
                self.fn(
                    test_file.name, tst_source_name.strip())
            test_file.seek(0)
            self.assertEqual(expected_file, test_file.read(),
                             msg='File was not changed')

class StringToDebianDependsForBS(unittest.TestCase):
    cases = [{'in': 'alt-python35-pam (<= 1.8.4)',
              'out': [{'name': 'alt-python35-pam',
                       'flag': 'LE', 'version': '1.8.4'}]},
             {'in': 'iptables',
              'out': [{'name': 'iptables'}]},
             {'in': 'iptables (>= 1.8.5) | iptables (>= 1.4.21-18.0.1)',
              'out': [{'name': 'iptables', 'flag': 'GE', 'version': '1.8.5'},
                      {'name': 'iptables', 'flag': 'GE',
                       'version': '1.4.21-18.0.1'}]}
             ]

    def test_depends(self):
        for case in self.cases:
            ds = string_to_ds(case['in'])
            self.assertTrue(type(ds) is list)
            self.assertTrue(len(ds) > 0)
            for d in ds:
                self.assertTrue(type(d) is dict)
                self.assertTrue('name' in d)
            self.assertEqual(case['out'], ds)
