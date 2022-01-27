import logging
import os
import csv
import tempfile
import time
import shutil
from concurrent.futures import as_completed, ThreadPoolExecutor
from typing import List

from fsplit.filesplit import Filesplit
from pulpcore.client.pulpcore.configuration import Configuration
from pulpcore.client.pulpcore.api_client import ApiClient
from pulpcore.client.pulpcore.api.tasks_api import TasksApi
from pulpcore.client.pulpcore.api.uploads_api import UploadsApi
from pulpcore.client.pulpcore.api.artifacts_api import ArtifactsApi

from build_node.uploaders.base import BaseUploader, UploadError
from build_node.utils.file_utils import hash_file
from build_node.models import Artifact


__all__ = ['PulpBaseUploader', 'PulpRpmUploader']


class TaskFailedError(Exception):
    pass


DEFAULT_TIMEOUT = 120  # 2 minutes per request


class PulpBaseUploader(BaseUploader):
    """
    Handles uploads to Pulp server.
    """

    def __init__(self, host: str, username: str, password: str,
                 chunk_size: int, requests_timeout: int = DEFAULT_TIMEOUT):
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
        """
        api_client = self._prepare_api_client(host, username, password)
        self._uploads_client = UploadsApi(api_client=api_client)
        self._tasks_client = TasksApi(api_client=api_client)
        self._artifacts_client = ArtifactsApi(api_client=api_client)
        self._file_splitter = Filesplit()
        self._chunk_size = chunk_size
        self._requests_timeout = requests_timeout
        self._logger = logging.getLogger(__file__)

    @staticmethod
    def _prepare_api_client(host: str, username: str, password: str) \
            -> ApiClient:
        """

        Parameters
        ----------
        host : str
        username : str
        password : str

        Returns
        -------
        ApiClient

        """
        api_configuration = Configuration(
            host=host, username=username, password=password)
        return ApiClient(configuration=api_configuration)

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
        result = self._tasks_client.read(task_href)
        while result.state not in ('failed', 'completed'):
            time.sleep(20)
            result = self._tasks_client.read(
                task_href, _request_timeout=self._requests_timeout)
        if result.state == 'failed':
            raise TaskFailedError(f'task {task_href} has failed, '
                                  f'details: {result}')
        return result

    def _create_upload(self, file_path: str) -> (str, int):
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
            {'size': file_size}, _request_timeout=self._requests_timeout)
        return response.pulp_href, file_size

    def _commit_upload(self, file_path: str, reference: str) -> str:
        """
        Commits upload and waits until upload will be transformed to artifact.
        Returns artifact reference upon completion.

        Parameters
        ----------
        file_path : str
            Path to the file.
        reference : str
            Upload reference in Pulp.

        Returns
        -------
        str
            Reference to the created resource.

        """
        file_sha256 = hash_file(file_path, hash_type='sha256')
        response = self._uploads_client.commit(
            reference, {'sha256': file_sha256},
            _request_timeout=self._requests_timeout)
        task_result = self._wait_for_task_completion(response.task)
        return task_result.created_resources[0]

    def _put_large_file(self, file_path: str, reference: str):
        temp_dir = tempfile.mkdtemp(prefix='pulp_uploader_')
        try:
            lower_bytes_limit = 0
            total_size = os.path.getsize(file_path)
            self._file_splitter.split(
                file_path, self._chunk_size, output_dir=temp_dir)
            manifest_path = os.path.join(temp_dir, 'fs_manifest.csv')
            with open(manifest_path, 'r') as f:
                for meta in csv.DictReader(f):
                    split_file_path = os.path.join(temp_dir, meta['filename'])
                    upper_bytes_limit = (
                            lower_bytes_limit + int(meta['filesize']) - 1)
                    self._uploads_client.update(
                        f'bytes {lower_bytes_limit}-{upper_bytes_limit}/'
                        f'{total_size}',
                        reference, split_file_path,
                        _request_timeout=self._requests_timeout)
                    lower_bytes_limit += int(meta['filesize'])
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def _send_file(self, file_path: str):
        reference, file_size = self._create_upload(file_path)
        if file_size > self._chunk_size:
            self._logger.debug('File size exceeded %d, sending file in parts',
                               self._chunk_size)
            self._put_large_file(file_path, reference)
        else:
            self._uploads_client.update(
                f'bytes 0-{file_size - 1}/{file_size}',
                reference,
                file_path,
                _request_timeout=self._requests_timeout
            )
        artifact_href = self._commit_upload(file_path, reference)
        return artifact_href

    def check_if_artifact_exists(self, sha256: str) -> str:
        response = self._artifacts_client.list(
            sha256=sha256, _request_timeout=self._requests_timeout)
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
                    'Cannot upload %s, error: %s', str(artifact), str(e),
                    exc_info=e)
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
        reference = self.check_if_artifact_exists(file_sha256)
        if not reference:
            reference = self._send_file(filename)
        return Artifact(
            name=os.path.basename(filename),
            href=reference,
            type='rpm' if filename.endswith('.rpm') else 'build_log'
        )


class PulpRpmUploader(PulpBaseUploader):

    def get_artifacts_list(self, artifacts_dir: str,
                           only_logs: bool = False) -> List[str]:
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

    def upload(self, artifacts_dir: str,
               only_logs: bool = False) -> List[Artifact]:
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
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(self.upload_single_file, artifact): artifact
                for artifact in self.get_artifacts_list(
                    artifacts_dir, only_logs=only_logs)
            }
            for future in as_completed(futures):
                artifact = futures[future]
                try:
                    success_uploads.append(future.result())
                except Exception as e:
                    self._logger.exception(
                        'Cannot upload %s', artifact, exc_info=e)
                    errored_uploads.append(artifact)
        self._logger.info('Upload has been finished')
        # TODO: Decide what to do with successfully uploaded artifacts
        #  in case of errors during upload.
        if errored_uploads:
            raise UploadError(f'Unable to upload files: {errored_uploads}')
        return success_uploads
