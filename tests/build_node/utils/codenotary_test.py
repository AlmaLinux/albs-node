from unittest.mock import Mock, patch

from build_node.utils import codenotary


def get_rpm_metadata(*args, **kwargs):
    return {
        'name': 'package',
        'epoch': '0',
        'version': '1',
        'release': '2',
        'arch': 'x86_64',
        'sourcerpm': 'package.src.rpm',
    }


immudb_client = Mock()
immudb_client.notarize_file.return_value = {
    'verified': True,
    'value': {'Hash': '123ABCDEF'},
}


def test_notarize_build_artifacts_src_rpm(fs, build_task_src_rpm):
    fs.create_file('/test/package.rpm')

    def download_file(url, dst_path):
        fs.create_file(dst_path, contents='Hello World!\n')

    with (
        patch('build_node.utils.codenotary.get_rpm_metadata', side_effect=get_rpm_metadata),
        patch('build_node.utils.codenotary.download_file', side_effect=download_file),
    ):
        notarized_artifacts, non_notarized_artifacts = codenotary.notarize_build_artifacts(
            build_task_src_rpm,
            '/test',
            immudb_client,
            'localhost',
        )

    assert non_notarized_artifacts == []
    assert notarized_artifacts['/test/package.rpm'] == '123ABCDEF'


def test_notarize_build_artifacts_git(fs, build_task):
    fs.create_file('/test/package.rpm')

    with patch('build_node.utils.codenotary.get_rpm_metadata', side_effect=get_rpm_metadata):
        notarized_artifacts, non_notarized_artifacts = codenotary.notarize_build_artifacts(
            build_task,
            '/test',
            immudb_client,
            'localhost',
        )

    assert non_notarized_artifacts == []
    assert notarized_artifacts['/test/package.rpm'] == '123ABCDEF'

