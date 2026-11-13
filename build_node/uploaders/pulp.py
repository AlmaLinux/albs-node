import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

from albs_build_lib.builder.models import Artifact
from albs_common_lib.utils.file_utils import hash_file
from pulpcore.client.pulp_rpm import ApiClient as RpmApiClient
from pulpcore.client.pulp_rpm import Configuration as RpmConfiguration
from pulpcore.client.pulp_rpm import ContentPackagesApi, RpmPackageResponse
from pulpcore.client.pulp_rpm.exceptions import ApiException as RpmApiException
from pulpcore.client.pulpcore.api.artifacts_api import ArtifactsApi
from pulpcore.client.pulpcore.api.tasks_api import TasksApi
from pulpcore.client.pulpcore.api.uploads_api import UploadsApi
from pulpcore.client.pulpcore.api_client import ApiClient
from pulpcore.client.pulpcore.configuration import Configuration
from pulpcore.client.pulpcore.exceptions import ApiException
from urllib3.exceptions import HTTPError
from urllib3.util.retry import Retry

from build_node.build_node_config import DEFAULT_PULP_CHUNK_WORKERS
from build_node.uploaders.base import BaseUploader, UploadError
from build_node.uploaders.pulp_session import PulpSession

__all__ = ['PulpBaseUploader', 'PulpRpmUploader']


class TaskFailedError(Exception):
    pass


DEFAULT_TIMEOUT = 60  # seconds per request
DEFAULT_UPLOAD_RETRIES = 3
UPLOAD_RETRY_DELAY = 10  # seconds, multiplied by the attempt number
# No transport retries: urllib3 would re-send a PUT chunk after a read
# timeout, and Pulp appends it a second time; a failed file is started
# over in upload_single_file() instead. A Retry instance rather than
# False, which urllib3 converts (and logs) on every request.
NO_RETRIES = Retry(total=False, redirect=0)


class PulpBaseUploader(BaseUploader):
    """
    Handles uploads to Pulp server.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        chunk_size: int,
        max_workers: int,
        chunk_workers: int = DEFAULT_PULP_CHUNK_WORKERS,
        requests_timeout: int = DEFAULT_TIMEOUT,
        upload_retries: int = DEFAULT_UPLOAD_RETRIES,
    ):
        """
        Initiate uploader.

        Parameters
        ----------
        host : str
            Pulp HTTP address.
        username : str
            User name to authenticate in Pulp.
        password : str
            User password.
        chunk_size : int
            Size of chunk to split files during the upload.
        max_workers: int
            Maximum number of parallel workers when uploading content.
        chunk_workers: int
            Maximum number of chunks of one large file uploaded in
            parallel.
        requests_timeout : int
            Timeout of every request to Pulp, in seconds.
        upload_retries : int
            How many times a file upload is started over after a
            connection error, a timeout or a server error.
        """
        self._session = PulpSession(
            host, username, password, requests_timeout=requests_timeout
        )
        api_client = self._prepare_api_client(host, max_workers * chunk_workers)
        self._uploads_client = UploadsApi(api_client=api_client)
        self._tasks_client = TasksApi(api_client=api_client)
        self._artifacts_client = ArtifactsApi(api_client=api_client)
        self._chunk_size = chunk_size
        self._max_workers = max_workers
        self._chunk_workers = chunk_workers
        self._requests_timeout = requests_timeout
        self._upload_retries = upload_retries
        self._logger = logging.getLogger(__file__)

    def _prepare_api_client(self, host: str, pool_size: int) -> ApiClient:
        """

        Parameters
        ----------
        host : str
        pool_size : int
            Number of requests that can be sent in parallel.

        Returns
        -------
        ApiClient

        """
        # No username/password, otherwise the client sends Basic auth
        api_configuration = Configuration(host=host, retries=NO_RETRIES)
        # Every parallel file upload can send its chunks in parallel,
        # keep a connection for each of them instead of reconnecting
        api_configuration.connection_pool_maxsize = max(
            api_configuration.connection_pool_maxsize, pool_size
        )
        return self._session.authenticate(
            ApiClient(configuration=api_configuration)
        )

    def reset_connections(self):
        """
        Closes the pooled connections, see PulpSession.reset_connections().
        """
        self._session.reset_connections()

    def _wait_for_task_completion(self, task_href: str) -> dict:
        """

        Parameters
        ----------
        task_href : str

        Returns
        -------
        dict
            Task final state

        """
        result = self._tasks_client.read(
            task_href, _request_timeout=self._requests_timeout
        )
        while result.state not in ('failed', 'completed'):
            time.sleep(5)
            result = self._tasks_client.read(
                task_href, _request_timeout=self._requests_timeout
            )
        if result.state == 'failed':
            raise TaskFailedError(
                f'task {task_href} has failed, ' f'details: {result}'
            )
        return result

    def _create_upload(self, file_path: str) -> Tuple[str, int]:
        """

        Parameters
        ----------
        file_path : str
            Path to the file.

        Returns
        -------
        tuple
            Upload reference and file size.

        """
        file_size = os.path.getsize(file_path)
        response = self._uploads_client.create(
            {'size': file_size}, _request_timeout=self._requests_timeout
        )
        return response.pulp_href, file_size

    def _commit_upload(self, file_sha256: str, reference: str) -> str:
        """
        Commits upload and waits until upload will be transformed to artifact.
        Returns artifact reference upon completion.

        Parameters
        ----------
        file_sha256 : str
            SHA256 of the file.
        reference : str
            Upload reference in Pulp.

        Returns
        -------
        str
            Reference to the created resource.

        """
        response = self._uploads_client.commit(
            reference,
            {'sha256': file_sha256},
            _request_timeout=self._requests_timeout,
        )
        try:
            task_result = self._wait_for_task_completion(response.task)
            return task_result.created_resources[0]
        except TaskFailedError:
            pulp_href = self.check_if_artifact_exists(file_sha256)
            if pulp_href:
                return pulp_href
            raise

    def _put_chunk(
        self, file_path: str, reference: str, offset: int, total_size: int
    ):
        """
        Sends one chunk of the file to the upload.

        Parameters
        ----------
        file_path : str
            Path to the file.
        reference : str
            Upload reference in Pulp.
        offset : int
            Position of the chunk in the file.
        total_size : int
            Size of the whole file.

        """
        expected = min(self._chunk_size, total_size - offset)
        with open(file_path, 'rb') as f:
            f.seek(offset)
            chunk = f.read(expected)
        # The range is the expected one: if the file got shorter, Pulp
        # rejects the chunk because its size does not match the range
        self._uploads_client.update(
            f'bytes {offset}-{offset + expected - 1}/{total_size}',
            reference,
            (os.path.basename(file_path), chunk),
            _request_timeout=self._requests_timeout,
        )

    def _put_large_file(self, file_path: str, reference: str):
        """
        Sends the file to the upload in chunks of self._chunk_size bytes,
        up to self._chunk_workers of them at once. Pulp accepts the chunks
        in any order.

        Parameters
        ----------
        file_path : str
            Path to the file.
        reference : str
            Upload reference in Pulp.

        """
        start = time.monotonic()
        total_size = os.path.getsize(file_path)
        offsets = range(0, total_size, self._chunk_size)
        workers = min(self._chunk_workers, len(offsets))
        executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix='pulp_chunk'
        )
        try:
            futures = [
                executor.submit(
                    self._put_chunk, file_path, reference, offset, total_size
                )
                for offset in offsets
            ]
            for future in as_completed(futures):
                future.result()
        finally:
            # On an error, do not send the remaining chunks: the whole
            # file is started over with a new upload
            executor.shutdown(wait=True, cancel_futures=True)
        self._logger.info(
            'Sent %s (%d bytes) in %d chunks, %d at once, in %.2f s',
            os.path.basename(file_path),
            total_size,
            len(offsets),
            workers,
            time.monotonic() - start,
        )

    def _send_file(self, file_path: str, file_sha256: str) -> str:
        file_size = os.path.getsize(file_path)
        if file_size > self._chunk_size:
            self._logger.debug(
                'File size exceeded %d, sending file in parts',
                self._chunk_size,
            )
            reference, _ = self._create_upload(file_path)
            self._put_large_file(file_path, reference)
            return self._commit_upload(file_sha256, reference)
        # One request creates the artifact, no upload object to commit
        # and no task to poll
        response = self._artifacts_client.create(
            file=file_path,
            sha256=file_sha256,
            size=file_size,
            _request_timeout=self._requests_timeout,
        )
        return response.pulp_href

    def _upload_file(self, file_path: str, file_sha256: str) -> str:
        """
        Uploads a file unless Pulp already has it.

        Parameters
        ----------
        file_path : str
            Path to the file.
        file_sha256 : str
            Pre-computed SHA256 of the file.

        Returns
        -------
        str
            Reference to the Pulp resource.

        """
        reference = self.check_if_artifact_exists(file_sha256)
        if not reference:
            reference = self._send_file(file_path, file_sha256)
        return reference

    def check_if_artifact_exists(self, sha256: str) -> Optional[str]:
        response = self._artifacts_client.list(
            sha256=sha256, _request_timeout=self._requests_timeout
        )
        if response.results:
            return response.results[0].pulp_href

    def upload(self, artifacts_dir: str, **kwargs) -> List[Artifact]:
        """

        Parameters
        ----------
        artifacts_dir : str
            Path to files that need to be uploaded.

        Returns
        -------
        list
            List of the references to the artifacts inside Pulp

        """
        artifacts = []
        errored_uploads = []
        for artifact in self.get_artifacts_list(artifacts_dir):
            try:
                artifacts.append(self.upload_single_file(artifact))
            except Exception as e:
                self._logger.exception(
                    'Cannot upload %s, error: %s',
                    str(artifact),
                    str(e),
                    exc_info=e,
                )
                errored_uploads.append(artifact)
        # TODO: Decide what to do with successfully uploaded artifacts
        #  in case of errors during upload.
        if errored_uploads:
            raise UploadError(f'Unable to upload files: {errored_uploads}')
        return artifacts

    def upload_single_file(self, filename: str) -> Artifact:
        """

        Parameters
        ----------
        filename : str
            Path to file that needs to be uploaded.

        Returns
        -------
        Artifact
            Object describing a single upload

        """
        file_sha256 = hash_file(filename, hash_type='sha256')
        for attempt in range(1, self._upload_retries + 2):
            cookie = self._session.cookie
            try:
                reference = self._upload_file(filename, file_sha256)
                break
            except (ApiException, RpmApiException) as e:
                if e.status in (401, 403):
                    # The session has expired (after 2 weeks by default),
                    # log in again and retry once. Parallel uploads share
                    # the session, so only the first of them logs in.
                    self._session.login(expired_cookie=cookie)
                    reference = self._upload_file(filename, file_sha256)
                    break
                # Status 0 is an SSL error from the client
                if 0 < e.status < 500 or attempt > self._upload_retries:
                    raise
                error = e
            except HTTPError as e:
                # urllib3: no connection, timeout, connection reset
                if attempt > self._upload_retries:
                    raise
                error = e
            # Start the file over: Pulp returns what the earlier attempt
            # managed to create (artifacts are looked up by checksum,
            # packages by pkgId), so nothing is uploaded twice
            delay = UPLOAD_RETRY_DELAY * attempt
            self._logger.warning(
                'Cannot upload %s (attempt %d of %d), retrying in %d s: %s',
                filename,
                attempt,
                self._upload_retries + 1,
                delay,
                error,
            )
            time.sleep(delay)
        return Artifact(
            name=os.path.basename(filename),
            href=reference,
            sha256=file_sha256,
            path=filename,
            type='rpm' if filename.endswith('.rpm') else 'build_log',
        )


class PulpRpmUploader(PulpBaseUploader):
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        chunk_size: int,
        max_workers: int,
        chunk_workers: int = DEFAULT_PULP_CHUNK_WORKERS,
        requests_timeout: int = DEFAULT_TIMEOUT,
        upload_retries: int = DEFAULT_UPLOAD_RETRIES,
    ):
        super().__init__(
            host,
            username,
            password,
            chunk_size,
            max_workers,
            chunk_workers=chunk_workers,
            requests_timeout=requests_timeout,
            upload_retries=upload_retries,
        )
        rpm_api_client = self._session.authenticate(
            RpmApiClient(
                configuration=RpmConfiguration(host=host, retries=NO_RETRIES)
            )
        )
        self._packages_client = ContentPackagesApi(api_client=rpm_api_client)

    def _send_rpm(self, file_path: str, file_sha256: str) -> str:
        """
        Creates RPM content with the synchronous upload endpoint, so no
        Pulp tasks have to be queued and polled.

        Parameters
        ----------
        file_path : str
            Path to the RPM file.
        file_sha256 : str
            Pre-computed SHA256 of the file.

        Returns
        -------
        str
            Reference to the RPM package.

        """
        if os.path.getsize(file_path) > self._chunk_size:
            package = self._post_rpm_chunked(file_path)
        else:
            package = self._packages_client.upload(
                file=file_path, _request_timeout=self._requests_timeout
            )

        if package.sha256 != file_sha256:
            raise UploadError(
                f'{file_path} checksum mismatch: expected {file_sha256}, '
                f'Pulp has {package.sha256}'
            )
        return package.pulp_href

    def _post_rpm_chunked(self, file_path: str) -> RpmPackageResponse:
        """
        Uploads a large RPM in chunks and creates the package from
        the resulting upload.

        Parameters
        ----------
        file_path: str
            Path to the RPM file
        """
        upload_href, _ = self._create_upload(file_path)
        try:
            self._put_large_file(file_path, upload_href)
            return self._packages_client.upload(
                upload=upload_href, _request_timeout=self._requests_timeout
            )
        finally:
            # Unlike the commit task, the synchronous endpoint keeps
            # the upload and its chunks, so remove them ourselves
            try:
                self._uploads_client.delete(
                    upload_href, _request_timeout=self._requests_timeout
                )
            except Exception as e:
                self._logger.warning(
                    'Cannot delete upload %s: %s', upload_href, e
                )

    def _upload_file(self, file_path: str, file_sha256: str) -> str:
        if not file_path.endswith('.rpm'):
            return super()._upload_file(file_path, file_sha256)
        return self._send_rpm(file_path, file_sha256)

    def get_artifacts_list(
        self, artifacts_dir: str, only_logs: bool = False
    ) -> List[str]:
        """

        Returns the list of the files in artifacts directory
        that need to be uploaded.

        Parameters
        ----------
        artifacts_dir : str
            Path to artifacts directory.
        only_logs : bool
            Flag if only logs needs to be uploaded

        Returns
        -------
        list
            List of files.

        """
        artifacts = []
        for file_ in super().get_artifacts_list(artifacts_dir):
            if file_.endswith('.rpm') and not only_logs:
                artifacts.append(file_)
            elif file_.endswith('.log'):
                artifacts.append(file_)
            elif file_.endswith('.cfg'):
                artifacts.append(file_)
        return artifacts

    def upload(
        self, artifacts_dir: str, only_logs: bool = False
    ) -> List[Artifact]:
        """

        Parameters
        ----------
        artifacts_dir : str
            Path to files that need to be uploaded.
        only_logs : bool
            Flag if only logs needs to be uploaded

        Returns
        -------
        list
            List of the references to the artifacts inside Pulp

        """
        success_uploads = []
        errored_uploads = []
        self._logger.info('Starting files upload')
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(self.upload_single_file, artifact): artifact
                for artifact in self.get_artifacts_list(
                    artifacts_dir, only_logs=only_logs
                )
            }
            for future in as_completed(futures):
                artifact = futures[future]
                try:
                    success_uploads.append(future.result())
                except Exception as e:
                    self._logger.exception(
                        'Cannot upload %s', artifact, exc_info=e
                    )
                    errored_uploads.append(artifact)
        self._logger.info('Upload has been finished')
        # TODO: Decide what to do with successfully uploaded artifacts
        #  in case of errors during upload.
        if errored_uploads:
            raise UploadError(f'Unable to upload files: {errored_uploads}')
        return success_uploads
