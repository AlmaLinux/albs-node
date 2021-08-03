import os
import typing
from abc import abstractmethod


__all__ = ['BaseUploader', 'UploadError']


class UploadError(Exception):
    pass


class BaseUploader(object):

    @staticmethod
    def get_artifacts_list(artifacts_dir: str) -> typing.List[str]:
        """
        Returns the list of the files in artifacts directory
        that need to be uploaded.

        Parameters
        ----------
        artifacts_dir : str
            Path to artifacts directory.

        Returns
        -------
        list
            List of files.

        """
        _, _, files = os.walk(artifacts_dir)
        return files

    @abstractmethod
    def upload(self, artifacts_dir: str, **kwargs) -> typing.List[str]:
        raise NotImplementedError()
