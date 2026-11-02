import logging
import operator
import os
import threading
from unittest.mock import Mock, patch

from albs_build_lib.builder.models import Artifact
from albs_common_lib.utils.file_utils import hash_file
from pyfakefs.fake_filesystem_unittest import TestCase

from build_node.uploaders.pulp import PulpRpmUploader


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
            artifact_href = uploader._send_file(f_path, f_hash)
            assert artifact_href == f_href

    def test_send_file_direct_artifact(self):
        """Small files (< chunk_size) use direct artifact creation."""
        f_path = '/build_dir/package.rpm'
        f_hash = hash_file(f_path, hash_type="sha256")
        f_href = 'direct_artifact_href'
        chunk_size = 100  # file is smaller than this

        class ArtifactsApi:
            def __init__(*_, **__):
                pass

            def create(_, file_path, sha256=None, **__):
                assert file_path == f_path
                assert sha256 == f_hash
                response = Mock()
                response.pulp_href = f_href
                return response

            def list(_, sha256, **__):
                response = Mock()
                response.results = []
                return response

        class UploadsApi:
            def __init__(*_, **__):
                pass

            def create(*_, **__):
                raise AssertionError('UploadsApi.create should not be called')

            def update(*_, **__):
                raise AssertionError('UploadsApi.update should not be called')

            def commit(*_, **__):
                raise AssertionError('UploadsApi.commit should not be called')

        with (
            patch('build_node.uploaders.pulp.ArtifactsApi', new=ArtifactsApi),
            patch('build_node.uploaders.pulp.UploadsApi', new=UploadsApi),
        ):
            uploader = PulpRpmUploader(
                'localhost', 'user', 'password', chunk_size, 1
            )
            artifact = uploader.upload_single_file(f_path)
            assert artifact.href == f_href
            assert artifact.sha256 == f_hash

    def test_send_large_file(self):
        """Large files are uploaded via parallel chunk PUTs."""
        f_path = '/build_dir/largefile.rpm'
        self.fs.create_file(f_path, contents='A' * 100)
        f_hash = hash_file(f_path, hash_type="sha256")
        f_size = 100
        chunk_size = 30  # 4 chunks: 30+30+30+10
        upload_href = 'upload_href_large'
        artifact_href = 'artifact_href_large'

        lock = threading.Lock()
        update_calls = []

        class UploadsApi:
            def __init__(*_, **__):
                pass

            def create(_, opts, **__):
                assert opts['size'] == f_size
                response = Mock()
                response.pulp_href = upload_href
                return response

            def update(_, content_range, ref, file, **__):
                assert ref == upload_href
                with lock:
                    update_calls.append(content_range)

            def commit(_, ref, commit_data, **__):
                assert ref == upload_href
                assert commit_data['sha256'] == f_hash
                response = Mock()
                response.task = 'task_large'
                return response

        class TasksApi:
            def __init__(*_, **__):
                pass

            def read(_, task_href, **__):
                result = Mock()
                result.state = 'completed'
                result.created_resources = [artifact_href]
                return result

        with (
            patch('build_node.uploaders.pulp.UploadsApi', new=UploadsApi),
            patch('build_node.uploaders.pulp.TasksApi', new=TasksApi),
            patch.object(
                PulpRpmUploader, 'check_if_artifact_exists',
                return_value=None,
            ),
        ):
            uploader = PulpRpmUploader(
                'localhost', 'user', 'password', chunk_size, 4
            )
            href = uploader._send_file(f_path, f_hash)
            assert href == artifact_href
            assert len(update_calls) == 4
            ranges = sorted(update_calls)
            assert ranges == [
                'bytes 0-29/100',
                'bytes 30-59/100',
                'bytes 60-89/100',
                'bytes 90-99/100',
            ]

    def test_send_large_file_chunk_error(self):
        """Errors in chunk uploads propagate correctly."""
        f_path = '/build_dir/errfile.rpm'
        self.fs.create_file(f_path, contents='B' * 100)
        f_hash = hash_file(f_path, hash_type="sha256")
        upload_href = 'upload_href_err'

        class UploadsApi:
            def __init__(*_, **__):
                pass

            def create(_, opts, **__):
                response = Mock()
                response.pulp_href = upload_href
                return response

            def update(*_, **__):
                raise RuntimeError('chunk upload failed')

        with (
            patch('build_node.uploaders.pulp.UploadsApi', new=UploadsApi),
            patch.object(
                PulpRpmUploader, 'check_if_artifact_exists',
                return_value=None,
            ),
        ):
            uploader = PulpRpmUploader(
                'localhost', 'user', 'password', 30, 4
            )
            try:
                uploader._send_file(f_path, f_hash)
                assert False, 'Expected RuntimeError'
            except RuntimeError as e:
                assert 'chunk upload failed' in str(e)

    def test_upload_timing_log_direct(self):
        """Direct artifact upload emits a timing log."""
        f_path = '/build_dir/package.rpm'
        f_hash = hash_file(f_path, hash_type="sha256")
        f_href = 'timing_direct_href'

        class ArtifactsApi:
            def __init__(*_, **__):
                pass

            def create(_, file_path, sha256=None, **__):
                response = Mock()
                response.pulp_href = f_href
                return response

            def list(_, sha256, **__):
                response = Mock()
                response.results = []
                return response

        with (
            patch('build_node.uploaders.pulp.ArtifactsApi', new=ArtifactsApi),
            patch('build_node.uploaders.pulp.UploadsApi', Mock),
        ):
            uploader = PulpRpmUploader(
                'localhost', 'user', 'password', 100, 1
            )
            with self.assertLogs(level=logging.INFO) as cm:
                uploader._send_file(f_path, f_hash)
            log_output = '\n'.join(cm.output)
            assert 'Upload complete' in log_output
            assert 'direct artifact' in log_output

    def test_upload_timing_log_chunked(self):
        """Chunked upload emits a timing log."""
        f_path = '/build_dir/largefile2.rpm'
        self.fs.create_file(f_path, contents='C' * 50)
        f_hash = hash_file(f_path, hash_type="sha256")
        upload_href = 'upload_timing'
        artifact_href = 'artifact_timing'

        class UploadsApi:
            def __init__(*_, **__):
                pass

            def create(_, opts, **__):
                response = Mock()
                response.pulp_href = upload_href
                return response

            def update(*_, **__):
                pass

            def commit(_, ref, data, **__):
                response = Mock()
                response.task = 'task_timing'
                return response

        class TasksApi:
            def __init__(*_, **__):
                pass

            def read(_, task_href, **__):
                result = Mock()
                result.state = 'completed'
                result.created_resources = [artifact_href]
                return result

        with (
            patch('build_node.uploaders.pulp.UploadsApi', new=UploadsApi),
            patch('build_node.uploaders.pulp.TasksApi', new=TasksApi),
            patch.object(
                PulpRpmUploader, 'check_if_artifact_exists',
                return_value=None,
            ),
        ):
            uploader = PulpRpmUploader(
                'localhost', 'user', 'password', 20, 4
            )
            with self.assertLogs(level=logging.INFO) as cm:
                uploader._send_file(f_path, f_hash)
            log_output = '\n'.join(cm.output)
            assert 'Upload complete' in log_output
            assert 'chunks' in log_output
