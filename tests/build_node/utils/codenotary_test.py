import threading
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
            lambda: immudb_client,
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
            lambda: immudb_client,
            'localhost',
        )

    assert non_notarized_artifacts == []
    assert notarized_artifacts['/test/package.rpm'] == '123ABCDEF'


def test_notarize_build_artifacts_client_per_thread(fs, build_task):
    artifacts = [f'/test/package{i}.rpm' for i in range(8)]
    for artifact in artifacts:
        fs.create_file(artifact)
    clients = []

    def new_client():
        client = Mock()
        client.thread = threading.get_ident()
        client.notarize_file.side_effect = lambda *_, **__: {
            'verified': True,
            'value': {'Hash': str(threading.get_ident())},
        }
        clients.append(client)
        return client

    with patch('build_node.utils.codenotary.get_rpm_metadata', side_effect=get_rpm_metadata):
        notarized_artifacts, non_notarized_artifacts = codenotary.notarize_build_artifacts(
            build_task,
            '/test',
            new_client,
            'localhost',
            max_workers=3,
        )

    assert non_notarized_artifacts == []
    assert set(notarized_artifacts) == set(artifacts)
    # No client was shared between threads
    assert len({client.thread for client in clients}) == len(clients)
    assert {str(client.thread) for client in clients} == set(
        notarized_artifacts.values()
    )
