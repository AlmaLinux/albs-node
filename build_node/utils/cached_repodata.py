# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-12-17

"""
Cached repository metadata storage implemented as a context manager.
"""

import abc
import bz2
import errno
import fcntl
import gzip
import hashlib
import logging
import lzma
import os
import shutil
import sqlite3
import tempfile
import time
import traceback
import re
import urllib.parse

import contextlib
import createrepo_c

from build_node.errors import LockError
from build_node.utils.debian_utils import parse_deb_version
from build_node.utils.file_utils import download_file, hash_file, safe_mkdir, \
    urljoin_path
from build_node.utils.hashing import get_hasher


__all__ = ['CachedRepodata', 'CachedDebRepodata', 'RPMPackageMeta',
           'DebPackageMeta', 'DebSrcPackageMeta']


class RPMPackageMeta(object):

    """RPM package metadata wrapper."""

    __slots__ = ['name', 'epoch', 'version', 'release', 'arch',
                 'location_href', 'checksum', 'checksum_type', 'sourcerpm']

    def __init__(self, name, epoch, version, release, arch, location_href,
                 checksum, checksum_type, sourcerpm=None):
        """
        RPM package metadata initialization.

        Parameters
        ----------
        name : str
            Package name.
        epoch : str
            Package epoch.
        version : str
            Package version.
        release : str
            Package release.
        sourcerpm : str, optional
            Source RPM name (for binary packages only).
        """
        self.name = name
        self.epoch = epoch
        self.version = version
        self.release = release
        self.arch = arch
        self.location_href = location_href
        self.checksum = checksum
        self.checksum_type = checksum_type
        self.sourcerpm = sourcerpm

    def __repr__(self):
        return '<RPMPackageMeta({p.name!r}, {p.epoch!r}, {p.version!r}, ' \
               '{p.release!r}, {p.arch!r})>'.format(p=self)


class DebPackageMeta(object):

    """Debian package metadata wrapper."""

    __slots__ = ['name', 'epoch', 'version', 'revision', 'arch',
                 'location_href', 'checksum', 'checksum_type', 'source']

    def __init__(self, name, epoch, version, revision, arch, location_href,
                 checksum, checksum_type, source=None):
        self.name = name
        self.epoch = epoch
        self.version = version
        self.revision = revision
        self.arch = arch
        self.location_href = location_href
        self.checksum = checksum
        self.checksum_type = checksum_type
        if source:
            # wipe debian source version string, since it doesn't
            # match real source name.
            # e.g.:
            # Binary package:
            #   Source: linux-latest (105+deb10u1)
            # Source package:
            #   Package: linux-latest
            source = re.sub(r'\s+(.*)$', '', source)
        self.source = source

    def __repr__(self):
        return '<DebPackageMeta({p.name!r}, {p.epoch!r}, {p.version!r},' \
               '{p.revision!r}, {p.arch!r}), {p.location_href!r}>'.format(
                   p=self)


class DebSrcPackageMeta(object):

    """Debian src package metadata wrapper."""

    __slots__ = ['name', 'epoch', 'version', 'revision', 'arch', 'files',
                 'checksum', 'checksum_type', 'source', 'directory']

    def __init__(self, name, epoch, version, revision, arch, files,
                 checksum_type, directory):
        self.name = name
        self.epoch = epoch
        self.version = version
        self.revision = revision
        self.arch = arch
        self.files = files
        self.checksum_type = checksum_type
        self.directory = directory
        self.source = None

    def __repr__(self):
        return '<DebSrcPackageMeta({p.name!r}, {p.epoch!r}, {p.version!r},' \
               '{p.revision!r}, {p.arch!r})>'.format(p=self)


class BaseCachedRepodata(object, metaclass=abc.ABCMeta):

    def __init__(self, cache_base_dir, hashable_url, lock_timeout):
        self._log = logging.getLogger(__name__)
        self.__lock_timeout = lock_timeout
        url_hash = hashlib.sha256(hashable_url.encode('utf-8')).hexdigest()
        self.__cache_dir = os.path.join(cache_base_dir, url_hash)
        safe_mkdir(self.__cache_dir)
        self.__lock_path = os.path.join(cache_base_dir,
                                        '{0}.lock'.format(url_hash))
        self.__lock_fd = None

    def __enter__(self):
        self.__lock_fd = open(self.__lock_path, 'wb')
        start_time = time.time()
        while True:
            try:
                fcntl.flock(self.__lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._actualize_cache()
                fcntl.flock(self.__lock_fd, fcntl.LOCK_SH)
                break
            except IOError as e:
                if e.errno != errno.EAGAIN:
                    self.__finalize()
                    self._log.error('can not obtain {0} lock: {1}. '
                                    'Traceback:\n{2}'.
                                    format(self.__lock_path, str(e),
                                           traceback.format_exc()))
                    raise e
                elif (time.time() - start_time) >= self.__lock_timeout:
                    self.__finalize()
                    raise LockError('timeout occurred')
                time.sleep(1)
            except Exception as e:
                self.__finalize()
                self._log.error('can not obtain {0} lock: {1}. '
                                'Traceback:\n{2}'.
                                format(self.__lock_path, str(e),
                                       traceback.format_exc()))
                raise e
        return self

    @abc.abstractmethod
    def _actualize_cache(self):
        """Updates the repository metadata cache."""
        raise NotImplementedError()

    @staticmethod
    def _unpack_archive(archive_path, extracted_path):
        """
        Extracts a bz2, gz or xz archive.

        Parameters
        ----------
        archive_path : str
            Archive path.
        extracted_path : str
            Extracted file path.
        """
        re_rslt = re.search(r'\.(bz2|gz|xz)$', archive_path,
                            flags=re.IGNORECASE)
        if not re_rslt:
            raise Exception('unsupported archive type')
        archive_type = re_rslt.group(1).lower()
        if archive_type == 'gz':
            with gzip.open(archive_path, 'rb') as in_fd:
                with open(extracted_path, 'wb') as out_fd:
                    shutil.copyfileobj(in_fd, out_fd)
        else:
            if archive_type == 'bz2':
                decompressor = bz2.BZ2Decompressor()
            else:
                decompressor = lzma.LZMADecompressor()
            with open(archive_path, 'rb') as in_fd:
                with open(extracted_path, 'wb') as out_fd:
                    for data in iter(lambda: in_fd.read(100 * 1024), b''):
                        out_fd.write(decompressor.decompress(data))

    @staticmethod
    def ensure_context(fn):
        def wrapped_fn(self, *args, **kwargs):
            if not self.__lock_fd:
                raise Exception('the function must be called inside the with '
                                'statement')
            return fn(self, *args, **kwargs)
        return wrapped_fn

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.__finalize()

    def __finalize(self):
        if self.__lock_fd:
            fcntl.flock(self.__lock_fd, fcntl.LOCK_UN)
            self.__lock_fd.close()
            self.__lock_fd = None

    @property
    def _cache_dir(self):
        return self.__cache_dir


class CachedRepodata(BaseCachedRepodata):

    def __init__(self, url, cache_base_dir, ssl_cert=None, ssl_key=None,
                 ca_info=None, lock_timeout=120):
        """
        Cached repodata initialization.

        Parameters
        ----------
        url : str
            Repository base URL or path.
        cache_base_dir : str
            Base directory for repository metadata cache storage.
        ssl_cert : str, optional
            SSL certificate file path.
        ssl_key : str, optional
            SSL certificate key file path.
        ca_info : str, optional
            Certificate Authority file path.
        lock_timeout : int, optional
            Lock acquiring timeout in seconds, default value is 120.
        """
        self.__url = url
        self.__ssl_cert = ssl_cert
        self.__ssl_key = ssl_key
        self.__ca_info = ca_info
        self.__lock_timeout = lock_timeout
        super(CachedRepodata, self).__init__(cache_base_dir, url, lock_timeout)
        self.__repodata_dir = os.path.join(self._cache_dir, 'repodata')
        safe_mkdir(self.__repodata_dir)
        self._repodata = None

    @BaseCachedRepodata.ensure_context
    def iter_packages(self):
        if 'primary' in self._repodata:
            return self._iter_packages_xml()
        else:
            return self._iter_packages_sqlite()

    @BaseCachedRepodata.ensure_context
    def get_download_url(self, package):
        return urllib.parse.urljoin(self.__url, package.location_href)

    @BaseCachedRepodata.ensure_context
    def _iter_packages_sqlite(self):
        tmp_dir = None
        try:
            primary_rec = self._repodata['primary_db']
            primary_path = primary_rec['path']
            if self._is_archive(primary_path):
                tmp_dir = tempfile.mkdtemp(dir=self._cache_dir)
                archive_path = primary_path
                primary_path = os.path.join(tmp_dir, 'primary.sqlite')
                self._unpack_archive(archive_path, primary_path)
            sql = """SELECT pkgKey, pkgId as checksum, name, epoch, version,
                            release, arch, location_href, checksum_type,
                            rpm_sourcerpm
                       FROM packages"""
            with contextlib.closing(sqlite3.connect(primary_path)) as con:
                with contextlib.closing(con.cursor()) as cur:
                    for row in cur.execute(sql):
                        pkg = {}
                        for i, col_info in enumerate(cur.description):
                            key = col_info[0]
                            value = row[i]
                            if value is None:
                                continue
                            elif key in ('name', 'epoch', 'version', 'release',
                                         'arch', 'location_href', 'checksum',
                                         'checksum_type'):
                                pkg[key] = value
                            elif key == 'rpm_sourcerpm' and value:
                                pkg['sourcerpm'] = value
                        yield RPMPackageMeta(**pkg)
        finally:
            if tmp_dir and os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir)

    @BaseCachedRepodata.ensure_context
    def _iter_packages_xml(self):
        metadata = createrepo_c.Metadata()
        metadata.locate_and_load_xml(self._cache_dir)
        for key in metadata.keys():
            pkg = metadata.get(key)
            yield RPMPackageMeta(name=pkg.name, epoch=pkg.epoch,
                                 version=pkg.version, release=pkg.release,
                                 arch=pkg.arch,
                                 location_href=pkg.location_href,
                                 checksum=pkg.pkgId,
                                 checksum_type=pkg.checksum_type,
                                 sourcerpm=pkg.rpm_sourcerpm)

    @BaseCachedRepodata.ensure_context
    def _actualize_cache(self):
        """
        Actualizes a repository metadata cache.

        Returns
        -------
        dict
            Repository metadata information.
        """
        repomd_path = self._download_repomd()
        repodata = {'repomd': {'path': repomd_path}}
        repomd = createrepo_c.Repomd(repomd_path)
        for repomd_rec in repomd:
            rec = self._download_repodata_file(repomd_rec)
            repodata[rec['type']] = rec
        self._repodata = repodata

    def _download_repomd(self):
        """
        Downloads a repomd.xml file from a remote repository.

        Returns
        -------
        str
            Downloaded repomd.xml file path.
        """
        repomd_path = os.path.join(self.__repodata_dir, 'repomd.xml')
        prev_checksum = None
        if os.path.exists(repomd_path):
            prev_checksum = hash_file(repomd_path, hashlib.sha256())
        download_file(urllib.parse.urljoin(self.__url, 'repodata/repomd.xml'),
                      repomd_path, ssl_cert=self.__ssl_cert,
                      ssl_key=self.__ssl_key, ca_info=self.__ca_info)
        new_checksum = hash_file(repomd_path, hashlib.sha256())
        if new_checksum != prev_checksum:
            self._cleanup_outdated_repodata(self.__repodata_dir)
        return repomd_path

    def _download_repodata_file(self, repomd_rec):
        """
        Downloads a repodata file from a remote repository.

        Parameters
        ----------
        repomd_rec : createrepo_c.RepomdRecord
            Corresponding repomd.xml record.

        Returns
        -------
        dict
            Downloaded repodata file information.
        """
        checksum_type = repomd_rec.checksum_type
        hasher = get_hasher(checksum_type)
        file_name = os.path.basename(repomd_rec.location_href)
        file_path = os.path.join(self.__repodata_dir, file_name)
        if not os.path.exists(file_path) or \
                hash_file(file_path, hasher) != repomd_rec.checksum:
            file_url = urllib.parse.urljoin(self.__url, repomd_rec.location_href)
            download_file(file_url, file_path, ssl_cert=self.__ssl_cert,
                          ssl_key=self.__ssl_key, ca_info=self.__ca_info)
            checksum = hash_file(file_path, get_hasher(checksum_type))
            if checksum != repomd_rec.checksum:
                raise Exception('downloaded file {0} checksum is wrong'.
                                format(file_path))
        return {'path': file_path,
                'checksum': repomd_rec.checksum,
                'checksum_type': checksum_type,
                'type': repomd_rec.type}

    @staticmethod
    def _cleanup_outdated_repodata(repodata_dir):
        """
        Cleanups a repodata directory from outdated metadata files.

        Parameters
        ----------
        repodata_dir : str
            Repodata directory.
        """
        for file_name in os.listdir(repodata_dir):
            if file_name != 'repomd.xml':
                os.remove(os.path.join(repodata_dir, file_name))

    @staticmethod
    def _is_archive(file_path):
        """
        Checks if a specified file is a bz2, gz or xz archive.

        Parameters
        ----------
        file_path : str
            File path.

        Returns
        -------
        bool
            True if a specified file is an archive, False otherwise.
        """
        re_rslt = re.search(r'\.(bz2|gz|xz)$', file_path, flags=re.IGNORECASE)
        return True if re_rslt else False

    @staticmethod
    def _unpack_archive(archive_path, extracted_path):
        """
        Extracts a bz2, gz or xz archive.

        Parameters
        ----------
        archive_path : str
            Archive path.
        extracted_path : str
            Extracted file path.
        """
        re_rslt = re.search(r'\.(bz2|gz|xz)$', archive_path,
                            flags=re.IGNORECASE)
        if not re_rslt:
            raise Exception('unsupported archive type')
        archive_type = re_rslt.group(1).lower()
        if archive_type == 'gz':
            with gzip.open(archive_path, 'rb') as in_fd:
                with open(extracted_path, 'wb') as out_fd:
                    shutil.copyfileobj(in_fd, out_fd)
        else:
            if archive_type == 'bz2':
                decompressor = bz2.BZ2Decompressor()
            else:
                decompressor = lzma.LZMADecompressor()
            with open(archive_path, 'rb') as in_fd:
                with open(extracted_path, 'wb') as out_fd:
                    for data in iter(lambda: in_fd.read(100 * 1024), b''):
                        out_fd.write(decompressor.decompress(data))


class CachedDebRepodata(BaseCachedRepodata):

    def __init__(self, url, distro, component, arch, cache_base_dir,
                 lock_timeout=120):
        """
        Cached Debian repository repodata initialization.

        Parameters
        ----------
        url : str
            Repository base URL.
        distro : str
            Distribution codename (e.g. trusty).
        component : str
            Distribution component (e.g. main).
        arch : str
            Repository architecture (e.g. amd64, src).
        cache_base_dir : str
            Base directory for repository metadata cache storage.
        lock_timeout : int, optional
            Lock acquiring timeout in seconds, default value is 120.
        """
        self.__url = url
        self.__distro = distro
        self.__component = component
        self.__arch = arch
        if arch == 'src':
            self.__arch_dir = 'source'
            self.__index_file_name = 'Sources'
        else:
            self.__arch_dir = 'binary-{0}'.format(arch)
            self.__index_file_name = 'Packages'
        hashable_url = urljoin_path(url, 'dists', distro, component,
                                    self.__arch_dir)
        super(CachedDebRepodata, self).__init__(cache_base_dir, hashable_url,
                                                lock_timeout)
        self.__index_file_path = os.path.join(self._cache_dir,
                                              self.__index_file_name)

    @BaseCachedRepodata.ensure_context
    def iter_packages(self):
        if self.__arch == 'src':
            return self.iter_source_packages()
        else:
            return self.iter_binary_packages()

    @BaseCachedRepodata.ensure_context
    def iter_source_packages(self):
        package = {}
        current_section = None
        with open(self.__index_file_path, 'r') as fd:
            for line in fd:
                line = line.strip()
                section_name = re.search(r'(.*):$', line)
                if section_name:
                    current_section = section_name.group(1)
                    package[current_section] = []
                    continue
                elif line == '':
                    if 'Checksums-Sha256' in package:
                        files = package['Checksums-Sha256']
                        checksum_type = 'sha256'
                    else:
                        checksum_type = 'sha1'
                        files = package['Checksums-Sha1']
                    epoch, version, revision = parse_deb_version(
                        package['Version'])
                    yield DebSrcPackageMeta(
                        package['Package'], epoch, version, revision,
                        'src', files, checksum_type, package['Directory']
                    )
                    package = {}
                    current_section = None
                re_rslt = re.search(r'^([\w-]+):\s+(.+?)$', line)
                if re_rslt:
                    key, value = re_rslt.groups()
                    package[key] = value
                    current_section = None
                if current_section is not None:
                    if current_section == 'Package-List':
                        package_name = line.split()[0]
                        package[current_section].append(package_name)
                    else:
                        checksum, size, filename = line.split()
                        package[current_section].append({
                            'checksum': checksum, 'filename': filename})

    @BaseCachedRepodata.ensure_context
    def iter_binary_packages(self):
        package = {}
        with open(self.__index_file_path, 'r') as fd:
            for line in fd:
                line = line.strip()
                if line == '':
                    if 'SHA256' in package:
                        checksum = package['SHA256']
                        checksum_type = 'sha256'
                    elif 'SHA1' in package:
                        checksum = package['SHA1']
                        checksum_type = 'sha1'
                    else:
                        continue
                    epoch, version, revision = \
                        parse_deb_version(package['Version'])
                    yield DebPackageMeta(package['Package'], epoch, version,
                                         revision, package['Architecture'],
                                         package['Filename'], checksum,
                                         checksum_type, package.get('Source'))
                    package = {}
                re_rslt = re.search(r'^([\w-]+):\s+(.+?)$', line)
                if re_rslt:
                    key, value = re_rslt.groups()
                    package[key] = value

    @BaseCachedRepodata.ensure_context
    def get_download_url(self, package, filename=None):
        if package.arch != 'src':
            return urljoin_path(self.__url, package.location_href)
        return urljoin_path(
            self.__url, package.directory, filename)

    def _actualize_cache(self):
        """
        Updates the repository Release and Packages/Sources files cache.
        """
        print('Cache dir:', self._cache_dir)
        release_file_url = urljoin_path(self.__url, 'dists', self.__distro,
                                        'Release')
        release_file_path = os.path.join(self._cache_dir, 'Release')
        download_file(release_file_url, release_file_path)
        index_rel_path = os.path.join(self.__component, self.__arch_dir,
                                      self.__index_file_name)
        release_data = self._parse_release_file(release_file_path,
                                                index_rel_path)
        checksum_type = release_data[self.__index_file_name]['checksum_type']
        checksum = release_data[self.__index_file_name]['checksum']
        if not os.path.exists(self.__index_file_path) or \
                hash_file(self.__index_file_path,
                          get_hasher(checksum_type)) != checksum:
            self._download_index_file(release_data)

    def _download_index_file(self, release_data):
        """
        Downloads a packages index file (Packages or Sources).

        Parameters
        ----------
        release_data : dict
            Index files information from a Release file.
        """
        pattern = r'{0}\.(bz2|gz|xz)'.format(self.__index_file_name)
        for file_name in release_data.keys():
            if not re.search(pattern, file_name, flags=re.IGNORECASE):
                continue
            tarball_path = os.path.join(self._cache_dir, file_name)
            tarball_url = urljoin_path(self.__url, 'dists', self.__distro,
                                       release_data[file_name]['path'])
            download_file(tarball_url, tarball_path)
            self._unpack_archive(tarball_path, self.__index_file_path)
            os.remove(tarball_path)
            checksum = release_data[self.__index_file_name]['checksum']
            checksum_type = \
                release_data[self.__index_file_name]['checksum_type']
            if hash_file(self.__index_file_path,
                         get_hasher(checksum_type)) != checksum:
                raise Exception('downloaded file {0} checksum is wrong'.
                                format(self.__index_file_path))
            return
        raise Exception('{0} file is not found in release data'.
                        format(self.__index_file_name))

    @staticmethod
    def _parse_release_file(path, index_rel_path):
        """
        Extracts index files information from a Debian repository Release file.

        Parameters
        ----------
        path : str
            Release file path.
        index_rel_path : str
            Expected index file path based on a component and architecture.

        Returns
        -------
        dict
            Index files information.
        """
        data = {}
        sha256_found = False
        with open(path, 'r') as fd:
            for line in fd:
                if line.startswith('SHA256:'):
                    sha256_found = True
                    continue
                elif re.search(r'^\w+.*?:', line) or not sha256_found:
                    sha256_found = False
                    continue
                re_rslt = re.search(
                    r'\s+([a-zA-Z0-9]+)\s+(\d+)\s+(.*?)$', line)
                if not re_rslt or \
                        not re_rslt.group(3).startswith(index_rel_path):
                    continue
                checksum, _, file_path = re_rslt.groups()
                file_name = os.path.basename(file_path)
                data[file_name] = {'checksum': checksum,
                                   'checksum_type': 'sha256',
                                   'path': file_path}
        if not data or os.path.basename(index_rel_path) not in data:
            raise Exception('can not find {0} in a Release file'.
                            format(index_rel_path))
        return data
