from unittest.mock import ANY, patch

from build_node.utils import git_sources_utils


def test_download_all(fs):
    fs.create_dir('/src')
    fs.create_file('/src/.file.metadata', contents='123ABCDEF data/file.txt\n')

    with patch('build_node.utils.git_sources_utils.download_file') as download_file:
        downloader = git_sources_utils.AlmaSourceDownloader('/src')
        downloader.download_all()

        download_file.assert_called_with(
            'https://sources.almalinux.org/123ABCDEF',
            '/src/data/SOURCES/file.txt',
            http_header=ANY,
        )
