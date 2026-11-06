import operator
import os
from unittest.mock import Mock, patch

from albs_build_lib.builder.models import Artifact
from albs_common_lib.utils.file_utils import hash_file
from pulpcore.client.pulp_rpm.exceptions import ApiException as RpmApiException
from pulpcore.client.pulpcore.exceptions import ApiException
from pyfakefs.fake_filesystem_unittest import TestCase

from build_node.uploaders.base import UploadError
from build_node.uploaders.pulp import PulpRpmUploader


class TestPulpRpmUploader(TestCase):

    def setUp(self):
        self.setUpPyfakefs()
        session_patcher = patch('build_node.uploaders.pulp.PulpSession')
        self.session = session_patcher.start().return_value
        self.addCleanup(session_patcher.stop)
        # authenticate() must return the client it was given
        self.session.authenticate.side_effect = lambda client: client

        self.fs.create_dir('/build_dir/tmp')

        self.file_map = {}
        self.file_lst = []
        self.file_paths = sorted([
            '/build_dir/package.rpm',
            '/build_dir/build.log',
            '/build_dir/config.cfg',
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

        with (
            patch('build_node.uploaders.pulp.ArtifactsApi', new=ArtifactsApi),
            patch.object(
                PulpRpmUploader,
                '_send_rpm',
                side_effect=lambda _, sha256: self.file_map[sha256].href,
            ),
        ):
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
            patch.object(
                PulpRpmUploader, 'check_if_artifact_exists', return_value=None
            ),
        ):
            uploader = PulpRpmUploader('localhost', 'user', 'password', f_size, 1)
            artifact_href = uploader._send_file(f_path)
            assert artifact_href == f_href

    def test_send_rpm(self):
        f_path = '/build_dir/package.rpm'
        f_hash = hash_file(f_path, hash_type="sha256")
        f_size = os.path.getsize(f_path)
        f_href = self.file_map[f_hash].href

        class ArtifactsApi:
            def __init__(*_, **__):
                pass

            def list(_, sha256, **__):
                raise AssertionError('RPM must not be looked up as artifact')

        class UploadsApi:
            def __init__(*_, **__):
                pass

            def create(_, opts, **__):
                raise AssertionError('small RPM must not be chunked')

        class ContentPackagesApi:
            def __init__(*_, **__):
                pass

            def upload(_, file=None, upload=None, **__):
                assert file == f_path
                assert upload is None
                response = Mock()
                response.pulp_href = f_href
                response.sha256 = f_hash
                return response

        with (
            patch('build_node.uploaders.pulp.ArtifactsApi', new=ArtifactsApi),
            patch('build_node.uploaders.pulp.UploadsApi', new=UploadsApi),
            patch(
                'build_node.uploaders.pulp.ContentPackagesApi',
                new=ContentPackagesApi,
            ),
        ):
            uploader = PulpRpmUploader(
                'localhost', 'user', 'password', f_size, 1
            )
            artifact = uploader.upload_single_file(f_path)
            assert artifact.href == f_href
            assert artifact.sha256 == f_hash
            assert artifact.type == 'rpm'

    def test_send_rpm_chunked(self):
        f_path = '/build_dir/package.rpm'
        f_hash = hash_file(f_path, hash_type="sha256")
        f_size = os.path.getsize(f_path)
        f_href = self.file_map[f_hash].href
        upload_href = 'upload1'
        updated_chunks = []
        deleted_uploads = []

        class UploadsApi:
            def __init__(*_, **__):
                pass

            def create(_, opts, **__):
                assert opts['size'] == f_size
                response = Mock()
                response.pulp_href = upload_href
                return response

            def update(_, content_range, upload_href_, file, **__):
                assert upload_href_ == upload_href
                updated_chunks.append(content_range)

            def commit(_, upload_href, upload_commit, **__):
                raise AssertionError('RPM upload must not be committed')

            def delete(_, upload_href_, **__):
                deleted_uploads.append(upload_href_)

        class ContentPackagesApi:
            def __init__(*_, **__):
                pass

            def upload(_, file=None, upload=None, **__):
                assert file is None
                assert upload == upload_href
                response = Mock()
                response.pulp_href = f_href
                response.sha256 = f_hash
                return response

        with (
            patch('build_node.uploaders.pulp.UploadsApi', new=UploadsApi),
            patch(
                'build_node.uploaders.pulp.ContentPackagesApi',
                new=ContentPackagesApi,
            ),
        ):
            uploader = PulpRpmUploader(
                'localhost', 'user', 'password', f_size - 1, 1
            )
            package_href = uploader._send_rpm(f_path, f_hash)
            assert package_href == f_href
            assert len(updated_chunks) == 2
            assert deleted_uploads == [upload_href]

    def test_send_rpm_checksum_mismatch(self):
        f_path = '/build_dir/package.rpm'
        f_hash = hash_file(f_path, hash_type="sha256")
        f_size = os.path.getsize(f_path)

        class ContentPackagesApi:
            def __init__(*_, **__):
                pass

            def upload(_, file=None, upload=None, **__):
                response = Mock()
                response.pulp_href = 'rpm_href'
                response.sha256 = 'other'
                return response

        with patch(
            'build_node.uploaders.pulp.ContentPackagesApi',
            new=ContentPackagesApi,
        ):
            uploader = PulpRpmUploader(
                'localhost', 'user', 'password', f_size, 1
            )
            with self.assertRaises(UploadError):
                uploader._send_rpm(f_path, f_hash)

    def test_send_rpm_chunked_deletes_upload_on_error(self):
        f_path = '/build_dir/package.rpm'
        f_hash = hash_file(f_path, hash_type="sha256")
        f_size = os.path.getsize(f_path)
        upload_href = 'upload1'
        deleted_uploads = []

        class UploadsApi:
            def __init__(*_, **__):
                pass

            def create(_, opts, **__):
                response = Mock()
                response.pulp_href = upload_href
                return response

            def update(_, content_range, upload_href_, file, **__):
                pass

            def delete(_, upload_href_, **__):
                deleted_uploads.append(upload_href_)

        class ContentPackagesApi:
            def __init__(*_, **__):
                pass

            def upload(_, file=None, upload=None, **__):
                raise RuntimeError('RPM file cannot be parsed for metadata')

        with (
            patch('build_node.uploaders.pulp.UploadsApi', new=UploadsApi),
            patch(
                'build_node.uploaders.pulp.ContentPackagesApi',
                new=ContentPackagesApi,
            ),
        ):
            uploader = PulpRpmUploader(
                'localhost', 'user', 'password', f_size - 1, 1
            )
            with self.assertRaises(RuntimeError):
                uploader._send_rpm(f_path, f_hash)
            assert deleted_uploads == [upload_href]

    def test_send_rpm_chunked_ignores_delete_error(self):
        f_path = '/build_dir/package.rpm'
        f_hash = hash_file(f_path, hash_type="sha256")
        f_size = os.path.getsize(f_path)
        f_href = self.file_map[f_hash].href
        upload_href = 'upload1'

        class UploadsApi:
            def __init__(*_, **__):
                pass

            def create(_, opts, **__):
                response = Mock()
                response.pulp_href = upload_href
                return response

            def update(_, content_range, upload_href_, file, **__):
                pass

            def delete(_, upload_href_, **__):
                raise RuntimeError('Pulp is unavailable')

        class ContentPackagesApi:
            def __init__(*_, **__):
                pass

            def upload(_, file=None, upload=None, **__):
                response = Mock()
                response.pulp_href = f_href
                response.sha256 = f_hash
                return response

        with (
            patch('build_node.uploaders.pulp.UploadsApi', new=UploadsApi),
            patch(
                'build_node.uploaders.pulp.ContentPackagesApi',
                new=ContentPackagesApi,
            ),
        ):
            uploader = PulpRpmUploader(
                'localhost', 'user', 'password', f_size - 1, 1
            )
            with self.assertLogs(level='WARNING') as logs:
                package_href = uploader._send_rpm(f_path, f_hash)
            assert package_href == f_href
            assert any(upload_href in line for line in logs.output)

    def test_upload_relogins_on_expired_session(self):
        uploader = PulpRpmUploader('localhost', 'user', 'password', 42, 1)
        # Each SDK raises its own ApiException, both must trigger a re-login
        for error in (ApiException(status=401), RpmApiException(status=403)):
            with self.subTest(error=type(error).__module__):
                self.session.login.reset_mock()
                with patch.object(
                    PulpRpmUploader,
                    '_upload_file',
                    side_effect=[error, 'href'],
                ) as upload_file:
                    artifact = uploader.upload_single_file(
                        '/build_dir/package.rpm'
                    )
                assert artifact.href == 'href'
                assert upload_file.call_count == 2
                # The cookie the request was rejected with, so parallel
                # uploads do not log in again one after another
                self.session.login.assert_called_once_with(
                    expired_cookie=self.session.cookie
                )

    def test_upload_does_not_relogin_on_other_errors(self):
        uploader = PulpRpmUploader('localhost', 'user', 'password', 42, 1)
        with patch.object(
            PulpRpmUploader,
            '_upload_file',
            side_effect=ApiException(status=500),
        ) as upload_file:
            with self.assertRaises(ApiException):
                uploader.upload_single_file('/build_dir/package.rpm')
        upload_file.assert_called_once()
        self.session.login.assert_not_called()

    def test_upload_relogins_only_once(self):
        uploader = PulpRpmUploader('localhost', 'user', 'password', 42, 1)
        with patch.object(
            PulpRpmUploader,
            '_upload_file',
            side_effect=[ApiException(status=401), ApiException(status=401)],
        ) as upload_file:
            with self.assertRaises(ApiException):
                uploader.upload_single_file('/build_dir/package.rpm')
        assert upload_file.call_count == 2
        self.session.login.assert_called_once()

    def test_clients_use_the_session(self):
        uploader = PulpRpmUploader('localhost', 'user', 'password', 42, 1)
        clients = [c.args[0] for c in self.session.authenticate.call_args_list]
        # The pulpcore client and the pulp_rpm client
        assert len(clients) == 2
        assert uploader._packages_client.api_client is clients[1]
        for client in clients:
            # No credentials, so the SDK sends no Basic auth
            assert client.configuration.username is None
            assert client.configuration.password is None
