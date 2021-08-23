import os
import re
import urllib.parse

from build_node.utils.file_utils import download_file


class BaseSourceDownloader:

    def __init__(self, sources_dir: str):
        self._sources_dir = sources_dir

    def find_metadata_file(self) -> str:
        for candidate in os.listdir(self._sources_dir):
            if re.search(r'^\..*\.metadata$', candidate):
                return os.path.join(self._sources_dir, candidate)

    def iter_source_records(self):
        metadata_file = self.find_metadata_file()
        if metadata_file is None:
            return
        for line in open(metadata_file, 'r').readlines():
            checksum, path = line.strip().split()
            yield checksum, os.path.join(self._sources_dir, path)

    def download_all(self):
        for checksum, path in self.iter_source_records():
            self.download_source(checksum, path)

    def download_source(self):
        raise NotImplementedError()


class AlmaSourceDownloader(BaseSourceDownloader):

    blob_storage = 'https://sources.almalinux.org/'

    def download_source(self, checksum: str, download_path: str) -> str:
        full_url = urllib.parse.urljoin(self.blob_storage, checksum)
        # sources.almalinux.org doesn't accept default pycurl user-agent
        headers = ['User-Agent: Almalinux build node']
        return download_file(full_url, download_path, http_header=headers)
