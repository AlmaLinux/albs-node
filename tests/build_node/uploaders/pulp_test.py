import operator
import os
from unittest.mock import Mock, patch

from pyfakefs.fake_filesystem_unittest import TestCase

from build_node.models import Artifact
from build_node.uploaders.pulp import PulpRpmUploader
from build_node.utils.file_utils import hash_file


class TestPulpRpmUploader(TestCase):

    def setUp(self):
        self.setUpPyfakefs()

        self.fs.create_dir('/build_dir/tmp')

        self.file_map = {}
        self.file_lst = []
        self.file_paths = sorted([
            '/build_dir/package.rpm',
            '/build_dir/build.log',
            '/build_dir/config.cfg'
        ])

        for file_path in self.file_paths:
            self.fs.create_file(file_path, contents=file_path)
            hsh = hash_file(file_path, hash_type="sha256")
            artifact = Artifact(
                name=os.path.basename(file_path),
                type='rpm' if file_path.endswith('.rpm') else 'build_log',
                href='pulp_href' + str(len(self.file_map)),
                sha256=hsh,
                path=file_path,
            )
            self.file_map[hsh] = artifact
            self.file_lst.append(artifact)

    def test_get_artifacts_list(self):
        uploader = PulpRpmUploader('localhost', 'user', 'password', 42, 1)
        files1 = uploader.get_artifacts_list('/build_dir')
        files1.sort()
        assert files1 == self.file_paths

    def test_upload_funcs(self):
        class ArtifactsApi:
            def __init__(*_, **__):
                pass

            def list(_, sha256, **__):
                assert sha256 in self.file_map
                data = Mock()
                data.pulp_href = self.file_map[sha256].href
                response = Mock()
                response.results = [data]
                return response

        with patch('build_node.uploaders.pulp.ArtifactsApi', new=ArtifactsApi):
            uploader = PulpRpmUploader('localhost', 'user', 'password', 42, len(self.file_lst))
            rpm_pkg = uploader.upload_single_file('/build_dir/package.rpm')
            assert rpm_pkg in self.file_lst

            files = uploader.upload('/build_dir')
            files.sort(key=operator.attrgetter('name'))
            assert files == self.file_lst

    def test_send_file(self):
        f_path = '/build_dir/package.rpm'
        f_hash = hash_file(f_path, hash_type="sha256")
        f_size = os.path.getsize(f_path)
        f_href = self.file_map[f_hash].href

        class UploadsApi:
            def __init__(*_, **__):
                pass

            def create(_, opts, **__):
                assert opts['size'] == f_size
                response = Mock()
                response.pulp_href = f_href
                return response

            def update(_, content_range, upload_href, file, **__):
                assert upload_href == f_href
                assert file == f_path

            def commit(_, upload_href, upload_commit, **__):
                assert upload_href == f_href
                assert upload_commit['sha256'] == f_hash
                response = Mock()
                response.task = TasksApi.TASK_HREF
                return response

        class TasksApi:
            TASK_HREF = 'task1'

            def __init__(self, *_, **__):
                pass

            def read(self, task_href):
                assert task_href == self.TASK_HREF
                result = Mock()
                result.created_resources = [f_href]
                result.state = 'completed'
                return result

        with (
            patch('build_node.uploaders.pulp.UploadsApi', new=UploadsApi),
            patch('build_node.uploaders.pulp.TasksApi', new=TasksApi),
            patch.object(PulpRpmUploader, 'check_if_artifact_exists', return_value=None)
        ):
            uploader = PulpRpmUploader('localhost', 'user', 'password', f_size, 1)
            artifact_href = uploader._send_file(f_path)
            assert artifact_href == f_href
