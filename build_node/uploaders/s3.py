import logging
import os
import typing

import boto3
from boto3.exceptions import S3UploadFailedError

from build_node.uploaders.base import BaseUploader, UploadError
from build_node.models import Artifact


__all__ = ['S3BaseUploader', 'S3LogsUploader']


class S3BaseUploader(BaseUploader):
    argument_required_message = "'s3_upload_dir' argument is required"

    def __init__(self, bucket: str, secret_access_key: str,
                 access_key_id: str, region: str):
        self._s3_client = boto3.client(
            's3', region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key
        )
        self._s3_bucket = bucket
        self._logger = logging.getLogger(__file__)

    def upload_single_file(self, file_path: str, s3_upload_dir: str) -> str:
        """
        Uploads provided file into provided directory on S3

        Parameters
        ----------
        file_path : str
            Path to file on local file.
        s3_upload_dir : str
            Directory on S3 to upload file into.

        Returns
        -------
        str
            URL to the file.

        """
        file_base_name = os.path.basename(file_path)
        object_name = os.path.join(s3_upload_dir, file_base_name)
        try:
            self._logger.info(f'Uploading artifact {file_path} to S3')
            self._s3_client.upload_file(
                file_path, self._s3_bucket, object_name)
            reference = self._s3_client.generate_presigned_url(
                'get_object', ExpiresIn=0,
                Params={'Bucket': self._s3_bucket, 'Key': object_name})
            return Artifact(name=file_base_name, href=reference, type='s3')
        except (S3UploadFailedError, ValueError) as e:
            self._logger.error(f'Cannot upload artifact {file_path}'
                               f' to S3: {e}')

    def upload(self, artifacts_dir: str, **kwargs) -> typing.List[str]:
        # To avoid warning about signature we assume that `s3_upload_dir`
        # is required keyword argument.
        if not kwargs.get('s3_upload_dir'):
            self._logger.error(self.argument_required_message)
            raise UploadError(self.argument_required_message)

        s3_upload_dir = kwargs.get('s3_upload_dir')
        references = []
        for file_ in self.get_artifacts_list(artifacts_dir):
            reference = self.upload_single_file(file_, s3_upload_dir)
            if reference:
                references.append(reference)
        return references


class S3LogsUploader(S3BaseUploader):

    @staticmethod
    def get_artifacts_list(artifacts_dir: str) -> typing.List['str']:
        all_files = super().get_artifacts_list(artifacts_dir)
        return [file_ for file_ in all_files if file_.endswith('.log')]
