# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-01-23

"""build_node.utils.file_utils module unit tests."""

import os
import unittest
import tempfile
import shutil

import mock
from pyfakefs.fake_filesystem_unittest import TestCase

from build_node.utils.file_utils import clean_dir, normalize_path, urljoin_path, \
    download_file, is_gzip_file

__all__ = ['TestCleanDir', 'TestNormalizePath', 'TestUrljoinPath',
           'TestIsGzipFile']


class TestCleanDir(unittest.TestCase):

    """build_node.utils.file_utils.clean_dir unit tests"""

    def setUp(self):
        self.tmppath = tempfile.mkdtemp()

    @mock.patch("build_node.utils.file_utils.os.remove")
    @mock.patch("build_node.utils.file_utils.os.unlink")
    def test_leave_empty_dir(self, mock_rm, mock_unlink):
        testdir = os.path.join(self.tmppath, 'test1')
        os.mkdir(testdir)
        clean_dir(testdir)
        self.assertTrue(os.path.exists(testdir))
        self.assertTrue(not mock_rm.called)
        self.assertTrue(not mock_unlink.called)

    @mock.patch("build_node.utils.file_utils.os.remove")
    @mock.patch("build_node.utils.file_utils.os.unlink")
    def test_unlink(self, mock_rm, mock_unlink):
        testdir = os.path.join(self.tmppath, 'test2')
        os.mkdir(testdir)
        link_source = os.path.join(self.tmppath, 'test2link')
        open(link_source, 'a').close()
        link_dest = os.path.join(testdir, 'link')
        os.link(link_source, link_dest)
        clean_dir(testdir)
        self.assertTrue(not mock_rm.called)
        self.assertTrue(mock_unlink.called)
        self.assertTrue(os.path.exists(link_source))

    def tearDown(self):
        shutil.rmtree(self.tmppath)


class TestNormalizePath(unittest.TestCase):

    """build_node.utils.file_utils.normalize_path unit tests"""

    def test_expand_home(self):
        """build_node.utils.file_utils.normalize_path expands ~"""
        self.assertEqual(normalize_path('~/.config/test-file'),
                         os.path.expanduser('~/.config/test-file'))

    def test_expand_relative(self):
        """build_node.utils.file_utils.normalize_path expands relative path"""
        self.assertEqual(normalize_path('/tmp/nested-dir/../../etc/fstab'),
                         '/etc/fstab')

    def test_expand_vars(self):
        """build_node.utils.file_utils.normalize_path expands environment variables"""
        self.assertEqual(normalize_path('${HOME}/.config/test-file'),
                         os.path.expanduser('~/.config/test-file'))


class TestUrljoinPath(unittest.TestCase):

    """build_node.utils.file_utils.urljoin_path unit tests"""

    base_url = 'https://alternatives.test/api/v1/download'

    paths = ['artifacts', '5b4460e0c39534345ef9772a', 'test-1-0.rpm']

    expected_url = '{0}/{1}/{2}/{3}'.format(base_url, *paths)

    def test_trailing_slash(self):
        """
        build_node.utils.file_utils.urljoin_path handles URL with trailing slash
        """
        base_url = self.base_url + '/'
        self.assertEqual(urljoin_path(base_url, *self.paths), self.expected_url)

    def test_no_trailing_slash(self):
        """
        build_node.utils.file_utils.urljoin_path handles URL without trailing slash
        """
        self.assertEqual(urljoin_path(self.base_url, *self.paths),
                         self.expected_url)


class TestDownloadFile(TestCase):

    """build_node.utils.file_utils.download_file unit tests """

    def setUp(self):
        self.setUpPyfakefs()
        self.test_dir = '/TEST_DOWNLOAD_FILE'
        os.mkdir(self.test_dir)
        self.dst_file = os.path.join(self.test_dir, 'downloaded.txt')

    def test_ftp_file_download(self):
        """
        build_node.utils.file_utils.download_file should call ftp_file_download
        when ftp file passed as first argument
        """
        url = 'ftp://test.net/source.zip'
        with mock.patch('build_node.utils.file_utils.ftp_file_download') as func:
            func.return_value = url
            result = download_file(url, self.dst_file)
            self.assertEqual(result, self.dst_file)
            self.assertTrue(func.called)
            self.assertTrue(os.path.exists(self.dst_file))

    def test_http_file_download(self):
        """
        build_node.utils.file_utils.download_file should call http_file_download
        when http file passed as first argument
        """
        url = 'http://test.net/source.zip'
        with mock.patch('build_node.utils.file_utils.http_file_download') as func:
            func.return_value = url
            result = download_file(url, self.dst_file)
            self.assertEqual(result, self.dst_file)
            self.assertTrue(func.called)
            self.assertTrue(os.path.exists(self.dst_file))


class TestIsGzipFile(TestCase):

    """build_node.utils.file_utils.is_gzip_file unit tests."""

    def setUp(self):
        self.setUpPyfakefs()
        self.test_dir = '/is_gzip_file_test/'
        os.mkdir(self.test_dir)
        self.test_file = os.path.join(self.test_dir, 'test-archive.gz')

    def test_gzip_file(self):
        """is_gzip_file detects gzip archive"""
        with open(self.test_file, 'wb') as fd:
            fd.write(b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03')
        self.assertTrue(is_gzip_file(self.test_file))

    def test_other_file(self):
        """is_gzip_file ignores non-gzip file"""
        with open(self.test_file, 'wb') as fd:
            fd.write(b'does not matter')
        self.assertFalse(is_gzip_file(self.test_file))
