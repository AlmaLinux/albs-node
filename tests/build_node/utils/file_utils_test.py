import gzip
import hashlib
import os
from unittest.mock import Mock, patch

import pycurl
from albs_common_lib.utils import file_utils
from pyfakefs.fake_filesystem_unittest import TestCase


class TestFileUtils(TestCase):

    def setUp(self):
        self.setUpPyfakefs()

    def test_chown_recursive(self):
        with patch('build_node.utils.file_utils.plumbum') as plumbum:
            chown = Mock()
            plumbum.local = {
                'sudo': {
                    ('chown', '-R', 'owner:group', '/tmp/file.txt'): chown
                }
            }
            file_utils.chown_recursive('/tmp/file.txt', 'owner', 'group')
            chown.assert_called_once()

    def test_clean_dir(self):
        self.fs.create_file('/test_dir/sub_dir/file.txt', contents='Hello World!\n')
        file_utils.clean_dir('/test_dir')
        assert not os.path.exists('/test_dir/sub_dir')
        assert os.path.exists('/test_dir')

    def test_rm_sudo(self):
        with patch('build_node.utils.file_utils.plumbum') as plumbum:
            rm = Mock()
            plumbum.local = {
                'sudo': {
                    ('rm', '-fr', '/tmp/file.txt'): rm,
                    ('rm', '-rf', '/tmp/file.txt'): rm,
                }
            }
            file_utils.rm_sudo('/tmp/file.txt')
            rm.assert_called_once()

    def test_filter_files(self):
        self.fs.create_dir('/test_dir/sub_dir')
        self.fs.create_file('/test_dir/file.txt')
        files = file_utils.filter_files('/test_dir', lambda *_: True)
        files.sort()
        assert files == ['/test_dir/file.txt', '/test_dir/sub_dir']

    def test_hash_file(self):
        self.fs.create_file('/file0.txt', contents=b'')
        msg = hashlib.sha1()
        msg.update(b'')
        file0_hash1 = msg.hexdigest()
        file0_hash2 = file_utils.hash_file('/file0.txt', hash_type='sha1')
        assert file0_hash1 == file0_hash2

        self.fs.create_file('/file1.txt', contents=b'Hello World!')
        msg = hashlib.sha1()
        msg.update(b'Hello World!')
        file1_hash1 = msg.hexdigest()
        file1_hash2 = file_utils.hash_file(
            '/file1.txt', hash_type='sha1', buff_size=2
        )
        assert file1_hash1 == file1_hash2

        file1_hash3 = file_utils.hash_file(
            open('/file1.txt', 'rb'), hashlib.sha1(), buff_size=1
        )
        assert file1_hash1 == file1_hash3

    def test_touch_file(self):
        file_utils.touch_file('/file.txt')
        assert os.path.exists('/file.txt')

    def test_safe_mkdir(self):
        assert file_utils.safe_mkdir('/test/dir')
        assert os.path.isdir('/test/dir')
        assert not file_utils.safe_mkdir('/test/dir')

    def test_safe_symlink(self):
        assert file_utils.safe_symlink('/file.txt', '/file.lnk')
        assert os.path.islink('/file.lnk')
        assert not file_utils.safe_symlink('/file.txt', '/file.lnk')

    def test_find_files(self):
        file_txt_paths = [
            '/test_dir/file0.txt',
            '/test_dir/file1.txt',
        ]
        file_bin_paths = [
            '/test_dir/file2.bin',
            '/test_dir/file3.bin',
        ]
        for path in file_txt_paths + file_bin_paths:
            self.fs.create_file(path)
        assert file_txt_paths == file_utils.find_files('/test_dir', '*.txt')

    def test_copy_dir_recursive(self):
        self.fs.create_file('/src/dir1/file1.txt')
        self.fs.create_file('/src/dir1/file1.xtxt')
        self.fs.create_file('/src/file2.txt')
        file_utils.copy_dir_recursive('/src', '/dst', [r'.*\.xtxt$'])

        assert os.path.exists('/dst/dir1/file1.txt')
        assert not os.path.exists('/dst/dir1/file1.xtxt')
        assert os.path.exists('/dst/file2.txt')

    def test_is_gzip_file(self):
        with gzip.open('/file.txt.gz', 'wb') as f:
            f.write(b'Hello World!\n')
        assert file_utils.is_gzip_file('/file.txt.gz')

    def test_urljoin_path(self):
        url1 = file_utils.urljoin_path('http://example.com', 'index.html')
        url2 = file_utils.urljoin_path('http://example.com/', 'index.html')
        url3 = file_utils.urljoin_path('http://example.com', '/index.html')
        url4 = file_utils.urljoin_path('http://example.com/', '/new', '/index.html')
        url5 = file_utils.urljoin_path('http://example.com', 'new', 'index.html')
        assert url1 == url2
        assert url1 == url3
        assert url4 == url5

    def test_file_download(self):
        self.fs.create_file('/src-file.txt', contents='Hello World1!')
        file_utils.download_file('file:///src-file.txt', '/dst-file.txt')
        with open('dst-file.txt') as dst_file:
            dst_content = dst_file.read()
        assert dst_content == 'Hello World1!'

        self.fs.create_file('/src/file.txt', contents='Hello World1!')
        self.fs.create_dir('/dst')
        file_utils.download_file('file:///src/file.txt', '/dst')
        with open('/dst/file.txt') as dst_file:
            dst_content = dst_file.read()
        assert dst_content == 'Hello World1!'

    def test_http_download(self):
        self.fs.create_dir('/dst')
        file_url = 'http://example.com/file.html'

        def getinfo(info):
            if info == pycurl.RESPONSE_CODE:
                return 200
            if info == pycurl.EFFECTIVE_URL:
                return file_url

        curl = Mock()
        curl.getinfo = getinfo

        with patch('build_node.utils.file_utils.pycurl.Curl', return_value=curl):
            dst_file = file_utils.download_file(file_url, '/dst')
        assert dst_file == '/dst/file.html'
        assert os.path.exists(dst_file)

    def test_ftp_download(self):
        file_url = 'ftp://example.com/file.html'
        with patch('build_node.utils.file_utils.ftplib.FTP'):
            dst_file = file_utils.download_file(file_url, '/dst-file.dat')
        assert dst_file == '/dst-file.dat'
        assert os.path.exists(dst_file)
