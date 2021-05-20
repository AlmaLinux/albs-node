# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 11.12.2014 10:38

"""
CloudLinux Build System Yum repository repodata parsing utilities.
"""

import gzip
import hashlib
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import functools
import urllib.parse

from bz2 import BZ2Decompressor
from contextlib import closing
from lzma import LZMADecompressor

import gi
gi.require_version('Modulemd', '2.0')
from gi.repository import Modulemd
import createrepo_c as cr

from build_node.errors import DataNotFoundError
from build_node.utils.file_utils import download_file, hash_file

__all__ = ['LOCAL_REPO', 'REMOTE_REPO']

LOCAL_REPO = 0
REMOTE_REPO = 1


def _with_repodata_files(fn):
    """
    Decorator which executes the _download_repodata method if repodata files
    aren't downloaded yet.

    Parameters
    ----------
    fn
        Decorated class method.

    Returns
    -------
    function
        Function decorator.
    """
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        if not self._files_cache:
            self._download_files()
        return fn(self, *args, **kwargs)
    return wrapper


class RepodataParser(object):

    def __init__(self, repo_url, ssl_cert=None, ssl_key=None, ssl_cainfo=None,
                 log=None, errors='strict'):
        """
        Parameters
        ----------
        repo_url : str
            Repository base URL.
        ssl_cert : str
            SSL client certificate path (optional).
        ssl_key : str
            SSL client certificate key path (optional).
        ssl_cainfo : str
            CA info path (optional).
        log : logging.Logger
            Current context logger.
        errors : str
            Errors handling policy. Default is 'strict' which
            means that error will be raised if repodata contains some broken
            data. Another possible value is 'ignore' which means that errors
            will be ignored or malformed data will be fixed when its possible.
        """
        self.__repo_url = repo_url
        self.__ssl_cert = ssl_cert
        self.__ssl_key = ssl_key
        self.__ssl_cainfo = ssl_cainfo
        self._files_cache = {}
        if not log:
            self.__logger = logging.getLogger(__name__)
        else:
            self.__logger = log
        self.__error_policy = errors
        if urllib.parse.urlparse(repo_url).scheme in ('', 'file'):
            self.__repo_type = LOCAL_REPO
            self.__repomd_url = os.path.join(repo_url, 'repodata/repomd.xml')
        else:
            self.__repo_type = REMOTE_REPO
            self.__repomd_url = urllib.parse.urljoin(repo_url,
                                                     'repodata/repomd.xml')
        self.__repo_dir, self.__repomd_path = self.__get_repomd_path()

    @property
    def repo_type(self):
        return self.__repo_type

    @property
    def repomd_checksum(self):
        """
        Returns
        -------
        unicode
            repomd.xml file SHA256 checksum
        """
        return str(hash_file(self.__repomd_path, hashlib.sha256()))

    def parse_repomd(self):
        """
        repomd.xml parsing function.

        Returns
        -------
        dict
            Dictionary with extracted information.
        """
        repomd = cr.Repomd(self.__repomd_path)
        repo_info = {'checksum_type': 'sha256',
                     'checksum': str(hash_file(self.__repomd_path,
                                               hashlib.sha256()))}
        for rec in repomd.records:
            rec_info = {
                'checksum': rec.checksum,
                'checksum_type': rec.checksum_type,
                'open-checksum': rec.checksum_open,
                'open-checksum_type': rec.checksum_open_type,
                'timestamp': rec.timestamp,
                'location': self.__full_location(rec.location_href),
                'open-size': int(rec.size_open),
                'size': int(rec.size)
            }
            if rec.db_ver:
                rec_info['database_version'] = int(rec.db_ver)

            repo_info[rec.type] = rec_info
        return repo_info

    @_with_repodata_files
    def iter_packages(self, repomd=None):
        """Iterates over repository packages."""

        repomd = cr.Repomd(self.__repomd_path)
        if {'primary_db', 'filelists_db', 'other_db'} <= set(self._files_cache):
            for pkg in self.__iter_packages_sqlite(self._files_cache):
                yield pkg
        else:
            packages = {}

            def pkgcb(pkg):
                packages[pkg.pkgId] = pkg
                return True

            def newpkgcb(pkgId, name, arch):
                return packages.get(pkgId, None)

            def warningcb(warning_type, message):
                self.__logger.warning(message)
                return True

            self.__logger.debug('processing {0} repository using xml '
                                'repodata'.format(self.__repo_url))
            for record in repomd.records:

                if record.type == 'primary':
                    cr.xml_parse_primary(self._files_cache[record.type],
                                         pkgcb=pkgcb,
                                         do_files=False,
                                         warningcb=warningcb)
                elif record.type == 'filelists':
                    cr.xml_parse_filelists(self._files_cache[record.type],
                                           pkgcb=pkgcb,
                                           newpkgcb=newpkgcb,
                                           warningcb=warningcb)
                elif record.type == 'other':
                    cr.xml_parse_other(self._files_cache[record.type],
                                       pkgcb=pkgcb,
                                       newpkgcb=newpkgcb,
                                       warningcb=warningcb)

            for pkg in list(packages.values()):
                yield self.__get_pkg_dict(pkg)

    @_with_repodata_files
    def is_modular(self):
        """
        Checks if a repository has modules defined.

        Returns
        -------
        bool
            True if a repository is modular, False otherwise.
        """
        return 'modules' in self._files_cache

    def close(self):
        """Deletes temporary created files."""
        if os.path.exists(self.__repo_dir):
            shutil.rmtree(self.__repo_dir)

    def __iter_packages_sqlite(self, sql_files):
        sql = """SELECT pkgKey, pkgId AS checksum, name, arch, version, epoch,
                   release, summary, description, url, time_file AS filetime,
                   time_build AS buildtime, rpm_license AS license,
                   rpm_vendor AS vendor, rpm_group AS 'group',
                   rpm_buildhost AS buildhost, rpm_sourcerpm AS sourcerpm,
                   rpm_header_start AS hdrstart, rpm_header_end AS hdrend,
                   rpm_packager as packager, size_package AS packagesize,
                   size_installed AS installedsize,
                   size_archive AS archivesize, location_href AS location,
                   checksum_type AS checksum_type FROM packages"""

        self.__logger.debug('processing {0} repository using SQLite '
                            'repodata'.format(self.__repo_url))
        with closing(sqlite3.connect(sql_files['primary_db'])) as con:
            with closing(con.cursor()) as cur:
                for row in cur.execute(sql):
                    pkg = {'provides': [], 'conflicts': [], 'requires': [],
                           'obsoletes': [], 'files': [], 'changelogs': []}
                    pkg_key = None
                    for i, col_info in enumerate(cur.description):
                        key = col_info[0]
                        value = row[i]
                        if value is None:
                            continue
                        elif key == 'pkgKey':
                            pkg_key = value
                        elif key == 'epoch':
                            pkg['epoch'] = int(value)
                        else:
                            pkg[key] = value
                    pkg['full_location'] = self.__full_location(
                        pkg['location'])
                    self.__read_primary_files_sqlite(pkg_key, pkg, con)
                    self.__read_filelists_sqlite(pkg_key, pkg,
                                                 sql_files['filelists_db'])
                    self.__read_other_sqlite(pkg_key, pkg,
                                             sql_files['other_db'])
                    self.__read_pcro_sqlite(pkg_key, pkg, con)
                    yield pkg

    @staticmethod
    def __read_primary_files_sqlite(pkg_key, pkg, con):
        sql = 'SELECT name, type FROM files WHERE pkgKey = ?'
        with closing(con.cursor()) as cur:
            for name, type_ in cur.execute(sql, (pkg_key,)):
                pkg['files'].append({'name': name, 'type': type_,
                                     'primary': True})

    @staticmethod
    def __read_sqlite_db(file_path, pkg_key, pkg, callback):
        """

        Reads data from SQL query from database into RPM package object.

        Parameters
        ----------
        file_path : str or unicode
            Path to the database on filesystem
        pkg_key : int
            Package's unique identifier (pkgKey column).
        pkg : dict
            RPM package to read files list into.
        callback
            Function to fill pkg object with data from database

        Raises
        ------
        DataNotFoundError
            If there are no records for this RPM package.
        ValueError
            If database record checksum doesn't match RPM package checksum.

        """
        con = sqlite3.connect(file_path)
        with closing(con.cursor()) as cur:
            cur.execute('SELECT pkgId FROM packages WHERE pkgKey = ?',
                        (pkg_key,))
            row = cur.fetchone()
            if not row:
                raise DataNotFoundError(
                    f'pkgKey {pkg_key} is not found in the filelists database')
            if row[0] != pkg['checksum']:
                raise ValueError('pkgKey {0} filelists record\'s checksum '
                                 '{1!r} is different from expected {2!r}'.
                                 format(pkg_key, row[0], pkg['checksum']))
        with closing(con.cursor()) as cur:
            callback(cur, pkg)
        con.close()

    @staticmethod
    def __read_filelists_sqlite(pkg_key, pkg, file_path):
        """
        Reads files list from filelists.sqlite database into
        RPM package object.

        Parameters
        ----------
        pkg_key : int
            Package's unique identifier (pkgKey column).
        pkg : dict
            RPM package to read files list into.

        Raises
        ------
        ValueError
            If database record checksum doesn't match RPM package checksum.
        """

        def process_filelists(cursor, pkg_):
            file_types = {'d': 'dir', 'f': 'file', 'g': 'ghost'}
            pkg_files = [f['name'] for f in pkg_['files']]
            cursor.execute(
                'SELECT dirname, filenames, filetypes FROM filelist '
                'WHERE pkgKey = ?', (pkg_key,))
            for row in cursor:
                dir_name = row[0]
                for file_name, file_type in zip(row[1].split('/'), row[2]):
                    file_ = os.path.join(dir_name, file_name)
                    if file_ not in pkg_files:
                        file_rec = {'name': file_}
                        if file_type in file_types:
                            file_rec['type'] = file_types[file_type]
                        else:
                            raise ValueError('unknown file type {0!r}'.
                                             format(file_type))
                        pkg_['files'].append(file_rec)

        RepodataParser.__read_sqlite_db(file_path, pkg_key, pkg,
                                        process_filelists)

    @staticmethod
    def __read_other_sqlite(pkg_key, pkg, file_path):
        """
        Reads changelogs from other.sqlite database into RPM package object.

        Parameters
        ----------
        pkg_key : int
            Package's unique identifier (pkgKey column).
        pkg : dict
            RPM package to read changelogs into.
        """

        def process_data(cursor, pkg_):
            cursor.execute('SELECT author, date, changelog FROM changelog '
                           'WHERE pkgKey = ? ORDER BY date DESC', (pkg_key,))
            for row in cursor:
                changelog = {'text': row[2]}
                if row[0]:
                    changelog['author'] = row[0]
                if row[1]:
                    changelog['date'] = row[1]
                pkg_['changelogs'].append(changelog)

        RepodataParser.__read_sqlite_db(file_path, pkg_key, pkg, process_data)

    @staticmethod
    def __read_pcro_sqlite(pkg_key, pkg, con):
        """
        Reads PCRO (provides, conflicts, requires, obsoletes) information from
        primary.sqlite database into RPM package object.

        Parameters
        ----------
        pkg_key : int
            Package's unique identifier (pkgKey column).
        pkg : dict
            RPM package to read PCRO information into.
        """
        columns = ['name', 'flags', 'epoch', 'version', 'release']
        for field in ('provides', 'conflicts', 'requires', 'obsoletes'):
            sql = 'SELECT {0} FROM {1} WHERE pkgKey = ?'.\
                format(', '.join(
                    columns + ['pre'] if field == 'requires' else columns),
                    field)
            with closing(con.cursor()) as cur:
                for row in cur.execute(sql, (pkg_key,)):
                    feature = {'name': row[0]}
                    if row[1]:
                        feature['flag'] = row[1]
                        if row[2] is not None:
                            feature['epoch'] = int(row[2])
                        if row[3] is not None:
                            feature['version'] = row[3]
                        if row[4] is not None:
                            feature['release'] = row[4]
                    if field == 'requires' and row[5] in ('TRUE', 1, True):
                        feature['pre'] = True
                    pkg[field].append(feature)

    def __full_location(self, location):
        if self.__repo_type == LOCAL_REPO:
            return os.path.join(self.__repo_url, location)
        return urllib.parse.urljoin(self.__repo_url, location)

    @staticmethod
    def __get_field_info(fields):
        list_info = []
        for field in fields:
            info = {'name': field[0],
                    'flag': field[1],
                    'epoch': field[2],
                    'version': field[3],
                    'release': field[4],
                    'pre': field[5]}
            info = dict((k, v)
                        for k, v in iter(list(info.items()))
                        if v)
            list_info.append(info)
        return list_info

    @staticmethod
    def __get_files(pkg, pkg_primary=None):
        file_list = []
        for f in pkg.files:
            file_info = {'type': 'dir' if f[0] == 'dir' else 'file',
                         'name': os.path.join(f[1], f[2])}
            if pkg_primary:
                file_info['primary'] = True
            file_list.append(file_info)
        return file_list

    def __get_pkg_dict(self, pkg):
        """Convert the package to the required dict"""
        pkg_info = {
            'filetime': pkg.time_file,
            'archivesize': int(pkg.size_archive),
            'buildhost': pkg.rpm_buildhost,
            'installedsize': int(pkg.size_installed),
            'hdrend': pkg.rpm_header_end,
            'group': pkg.rpm_group,
            'epoch': int(pkg.epoch),
            'version': pkg.version,
            'obsoletes': self.__get_field_info(pkg.obsoletes),
            'provides': self.__get_field_info(pkg.provides),
            'full_location': self.__full_location(pkg.location_href),
            'location': pkg.location_href,
            'files': self.__get_files(pkg),
            'vendor': pkg.rpm_vendor or '',
            'description': pkg.description,
            'hdrstart': pkg.rpm_header_start,
            'buildtime': pkg.time_build,
            'conflicts': self.__get_field_info(pkg.conflicts),
            'arch': pkg.arch,
            'name': pkg.name,
            'license': pkg.rpm_license,
            'url': pkg.url,
            'checksum': pkg.pkgId,
            'summary': pkg.summary,
            'packagesize': int(pkg.size_package),
            'changelogs': [{'author': log[0],
                            'date': int(log[1]),
                            'text': log[2]} for log in pkg.changelogs],
            'release': pkg.release,
            'checksum_type': pkg.checksum_type,
            'requires': self.__get_field_info(pkg.requires),
            'sourcerpm': pkg.rpm_sourcerpm,
            'packager': pkg.rpm_packager
        }
        return dict((k, v)
                    for k, v in pkg_info.items()
                    if v is not None)

    def __get_repomd_path(self):
        tmpdir = tempfile.mkdtemp(prefix='parse_repomd_')
        try:
            repomd_path = download_file(
                self.__full_location('repodata/repomd.xml'),
                tmpdir,
                ssl_cert=self.__ssl_cert,
                ssl_key=self.__ssl_key,
                ca_info=self.__ssl_cainfo)
            return tmpdir, repomd_path
        except Exception:
            shutil.rmtree(tmpdir)
            raise

    @staticmethod
    def __extract_archive(archive_path):
        """
        Parameters
        ----------
        archive_path : str
            Path to archive

        Return
        ------
        str
           Path to extracted file
        """
        archive_content = None
        extracted_file = re.sub(r'\.gz|\.bz2|\.xz', '', archive_path)
        if re.search(r'\.gz$', archive_path, re.IGNORECASE):
            with gzip.open(archive_path, 'rb') as f:
                archive_content = f.read()
        if re.search(r'\.bz2$', archive_path, re.IGNORECASE):
            with open(archive_path, 'rb') as f:
                archive_content = BZ2Decompressor().decompress(f.read())
        if re.search(r'\.xz$', archive_path, re.IGNORECASE):
            with open(archive_path, 'rb') as f:
                archive_content = LZMADecompressor().decompress(f.read())
        if extracted_file is not None:
            with open(extracted_file, 'wb') as f:
                f.write(archive_content)
            os.remove(archive_path)
        return extracted_file

    @_with_repodata_files
    def iter_module_streams(self):
        """
        Iterates over repository module streams.

        Yields
        ------
        tuple(Modulemd.Module, Modulemd.ModuleStreamV2)
            Next module stream object.
        """
        if not self.is_modular():
            return
        supported_version = Modulemd.ModuleStreamVersionEnum.TWO
        modules_path = self._files_cache['modules']
        modules_idx = Modulemd.ModuleIndex.new()
        ret, failures = modules_idx.update_from_file(modules_path, True)
        if not ret:
            raise Exception('can not update module index')
        for module_name in modules_idx.get_module_names():
            module = modules_idx.get_module(module_name)
            for stream in module.get_all_streams():
                # ensure that module metadata is valid
                stream.validate()
                # currently we support only version 2 module metadata
                stream_mdversion = stream.get_mdversion()
                if stream_mdversion != supported_version:
                    raise NotImplementedError(
                        f'{stream_mdversion} metadata version is not '
                        f'supported yet'
                    )
                yield module, stream

    def _download_files(self):
        """Download and decompress records files"""
        repomd = cr.Repomd(self.__repomd_path)
        local_file_url = {}
        try:
            for rec in repomd.records:
                file_path = download_file(
                    self.__full_location(rec.location_href),
                    self.__repo_dir,
                    ssl_cert=self.__ssl_cert,
                    ssl_key=self.__ssl_key,
                    ca_info=self.__ssl_cainfo
                )
                # unpack sqlite databases and modules.yaml so that we can work
                # with them
                if re.search(r'(\.sqlite|modules.*?)\.(gz|bz2|xz)$', file_path,
                             re.IGNORECASE):
                    file_path = self.__extract_archive(file_path)
                local_file_url[rec.type] = file_path
            self._files_cache = local_file_url
            return local_file_url
        except Exception:
            shutil.rmtree(self.__repomd_path)
            raise
