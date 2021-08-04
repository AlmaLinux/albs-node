# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-07-10

"""Debian packages builder unit tests."""

import re
import os
import shutil
import unittest
from unittest import mock

import pyfakefs.fake_filesystem_unittest

from build_node.builders.debian_builder import DebianBuilder
from build_node.errors import CommandExecutionError
from build_node.utils.file_utils import touch_file

__all__ = ['TestAddUrlCredentials', 'TestGenerateAptConf',
           'TestFindOrigArchive', 'TestGetSourcePackageVersion']


class TestAddUrlCredentials(unittest.TestCase):

    """DebianBuilder.add_url_credentials unit tests."""

    def test_add_url_credentials(self):
        """DebianBuilder.add_url_credentials adds credentials to URL"""
        url = '{0}://alternatives.test/downloads/test_url/'
        auth_url = '{0}://{1}:{2}@alternatives.test/downloads/test_url/'
        login = 'example_user'
        password = '34r2f434t434fg'
        for scheme in ('http', 'https'):
            result = DebianBuilder.add_url_credentials(url.format(scheme),
                                                       login, password)
            self.assertEqual(result, auth_url.format(scheme, login, password))


class TestGenerateAptConf(pyfakefs.fake_filesystem_unittest.TestCase):

    """DebianBuilder.generate_apt_conf unit tests."""

    def setUp(self):
        self.setUpPyfakefs()
        self.apt_conf_dir = '/apt_conf_test'
        os.makedirs(self.apt_conf_dir)
        self.apt_conf_path = os.path.join(self.apt_conf_dir, 'apt.conf')

    def test_development_mode(self):
        """
        DebianBuilder.generate_apt_conf disables SSL verification in \
development mode"""
        DebianBuilder.generate_apt_conf(self.apt_conf_path, True)
        content = self.__check_allow_unauthenticated()
        https_re_rslt = re.search(r'^Acquire::https\s+{\s*?(.*?)\s*?}',
                                  content, re.DOTALL | re.MULTILINE)
        self.assertTrue(https_re_rslt)
        rules = https_re_rslt.group(1)
        self.assertTrue(re.search(r'Verify-Peer\s+?"false"\s*?;', rules))
        self.assertTrue(re.search(r'Verify-Host\s+?"false"\s*?;', rules))

    def test_production_mode(self):
        """
        DebianBuilder.generate_apt_conf permits unsigned packages in \
production mode
        """
        DebianBuilder.generate_apt_conf(self.apt_conf_path, False)
        self.__check_allow_unauthenticated()

    def __check_allow_unauthenticated(self):
        self.assertTrue(os.path.isfile(self.apt_conf_path))
        with open(self.apt_conf_path, 'rb') as fd:
            content = fd.read()
            re_rslt = re.search(r'^APT::Get::AllowUnauthenticated\s+"true"\s*?;',
                                content)
            self.assertTrue(re_rslt)
            return content

    def tearDown(self):
        shutil.rmtree(self.apt_conf_dir)


class TestFindOrigArchive(pyfakefs.fake_filesystem_unittest.TestCase):

    """DebianBuilder._find_orig_archive unit tests."""

    source_name = 'example_package'

    source_version = '1.4.2'

    def setUp(self):
        self.setUpPyfakefs()
        self.sources_dir = '/castor/test/sources_dir'
        os.makedirs(self.sources_dir)
        for file_name in ('irrelevant_1.4.2.orig.tar.gz', 'README.md'):
            touch_file(os.path.join(self.sources_dir, file_name))

    def test_finds_bz2_archive(self):
        """DebianBuilder._find_orig_archive locates a .tar.bz2 archive"""
        self.__check_finds_archive('bz2')

    def test_finds_gz_archive(self):
        """DebianBuilder._find_orig_archive locates a .tar.gz archive"""
        self.__check_finds_archive('gz')

    def test_finds_xz_archive(self):
        """DebianBuilder._find_orig_archive locates a .tar.xz archive"""
        self.__check_finds_archive('xz')

    def test_ignores_irrelevant(self):
        """DebianBuilder._find_orig_archive ignores irrelevant files"""
        self.assertIsNone(DebianBuilder._find_orig_archive(self.sources_dir,
                                                           self.source_name,
                                                           self.source_version))

    def __check_finds_archive(self, ext):
        tarball_name = '{0}_{1}.orig.tar.{2}'.format(self.source_name,
                                                     self.source_version, ext)
        tarball_path = os.path.join(self.sources_dir, tarball_name)
        touch_file(tarball_path)
        self.assertEqual(DebianBuilder._find_orig_archive(self.sources_dir,
                                                          self.source_name,
                                                          self.source_version),
                         tarball_path)

    def tearDown(self):
        shutil.rmtree(self.sources_dir)


class TestGetSourcePackageVersion(unittest.TestCase):

    """DebianBuilder._get_source_package_version unit tests."""

    @mock.patch('build_node.builders.debian_builder.dpkg_parsechangelog')
    def test_returns_version(self, dpkg_parsechangelog):
        """DebianBuilder._get_source_package_version returns version without \
release"""
        dpkg_parsechangelog.return_value = '1.3-11_cloudlinux'
        self.assertEqual(DebianBuilder._get_source_package_version(''), '1.3')

    @mock.patch('build_node.builders.debian_builder.dpkg_parsechangelog')
    def test_skips_epoch(self, dpkg_parsechangelog):
        """DebianBuilder._get_source_package_version returns version without \
release and epoch"""
        dpkg_parsechangelog.return_value = '1:1.3-11_cloudlinux'
        self.assertEqual(DebianBuilder._get_source_package_version(''), '1.3')

    @mock.patch('build_node.builders.debian_builder.dpkg_parsechangelog')
    def test_reports_error(self, dpkg_parsechangelog):
        """DebianBuilder._get_source_package_version throws an error if \
version is not found"""
        dpkg_parsechangelog.side_effect = \
            CommandExecutionError('', 1, '', '', [])
        self.assertRaises(CommandExecutionError,
                          DebianBuilder._get_source_package_version,
                          '/non-existent')
