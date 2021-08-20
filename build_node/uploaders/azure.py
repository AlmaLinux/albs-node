import logging
import os
import typing

from azure.core.exceptions import HttpResponseError
from azure.storage.blob import BlobServiceClient

from build_node.models import Artifact
from build_node.uploaders.base import (
    BaseUploader,
    BaseLogsUploader,
    UploadError,
)


__all__ = ['AzureBaseUploader', 'AzureLogsUploader']


class AzureBaseUploader(BaseUploader):
    argument_required_message = "'azure_upload_dir' argument is required"

    def __init__(self, connection_string: str, container_name: str):
        self._connection_string = connection_string
        self._container_name = container_name
        self._blob_client = BlobServiceClient.from_connection_string(
            connection_string)
        self._container_client = self._blob_client.get_container_client(
            container=container_name)
        self._logger = logging.getLogger(__name__)

    def upload_single_file(self, file_path: str, azure_upload_dir: str) -> Artifact:
        file_name = os.path.basename(file_path)
        blob_name = os.path.join(azure_upload_dir, file_name)
        blob_client = self._blob_client.get_blob_client(
            container=self._container_name, blob=blob_name)
        try:
            with open(file_path, 'rb') as f:
                blob_client.upload_blob(f)
            return Artifact(
                name=file_name,
                href=blob_client.url,
                type=self.ITEM_TYPE
            )
        except HttpResponseError as e:
            self._logger.error(f'Cannot upload artifact {file_path}'
                               f' to Azure: {e}')

    def upload(self, artifacts_dir: str, **kwargs) -> typing.List[Artifact]:
        """
        Uploads files from provided directory into Azure Blob storage.

        Parameters
        ----------
        artifacts_dir : str
            Directory where local files are stored
        kwargs

        Returns
        -------
        list
            List of references to uploaded artifacts

        """
        # To avoid warning about signature we assume that `s3_upload_dir`
        # is required keyword argument.
        artifacts = []

        if not kwargs.get('azure_upload_dir'):
            self._logger.error(self.argument_required_message)
            raise UploadError(self.argument_required_message)
        azure_upload_dir = kwargs.get('azure_upload_dir')
        for file_ in self.get_artifacts_list(artifacts_dir):
            artifact = self.upload_single_file(file_, azure_upload_dir)
            if artifact:
                artifacts.append(artifact)
        return artifacts


class AzureLogsUploader(BaseLogsUploader, AzureBaseUploader):
    pass
