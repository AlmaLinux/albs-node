# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-10-18

"""
Various utility functions for working with files.
"""

import binascii
import re
import errno
import getpass
import itertools
import os
import shutil
import base64
import tempfile
import urllib.request
import urllib.parse
import urllib.error
import ftplib

from glob import glob

import plumbum
import pycurl

from build_node.utils.hashing import get_hasher


__all__ = ['chown_recursive', 'clean_dir', 'rm_sudo', 'hash_file',
           'filter_files', 'normalize_path', 'safe_mkdir', 'safe_symlink',
           'find_files', 'urljoin_path', 'touch_file', 'download_file',
           'copy_dir_recursive', 'is_gzip_file']


def chown_recursive(path, owner=None, group=None):
    """
    Recursively changes a file ownership.

    Parameters
    ----------
    path : str
        File or directory path.
    owner : str, optional
        Owner login. A current user login will be used if omitted.
    group : str, optional
        Owner's group. A current user's group will be used if omitted.
    """
    if not owner:
        owner = getpass.getuser()
    if not group:
        group = plumbum.local['id']('-g', '-n').strip()
    plumbum.local['sudo']['chown', '-R', f'{owner}:{group}', path]()


def clean_dir(path):
    """
    Recursively removes all content from the specified directory.

    Parameters
    ----------
    path : str
        Directory path.
    """
    for root, dirs, files in os.walk(path, topdown=False):
        for name in itertools.chain(files, dirs):
            target = os.path.join(root, name)
            if os.path.islink(target):
                os.unlink(target)
            elif os.path.isdir(target):
                shutil.rmtree(target)
            else:
                os.remove(target)


def rm_sudo(path):
    """
    Recursively removes the specified path using "sudo rm -fr ${path}" command.

    Parameters
    ----------
    path : str
        Path (either directory or file) to remove.

    Warnings
    --------
    Do not use that function unless you are absolutely know what are you doing.
    """
    plumbum.local['sudo']['rm', '-fr', path]()


def filter_files(directory_path, filter_fn):
    return [os.path.join(directory_path, f) for f in os.listdir(directory_path)
            if filter_fn(f)]


def hash_file(file_path, hasher=None, hash_type=None, buff_size=1048576):
    """
    Returns checksum (hexadecimal digest) of the file.

    Parameters
    ----------
    file_path : str or file-like
        File to hash. It could be either a path or a file descriptor.
    hasher : _hashlib.HASH
        Any hash algorithm from hashlib.
    hash_type : str
        Hash type (e.g. sha1, sha256).
    buff_size : int
        Number of bytes to read at once.

    Returns
    -------
    str
        Checksum (hexadecimal digest) of the file.
    """
    if hasher is None:
        hasher = get_hasher(hash_type)

    def feed_hasher(_fd):
        buff = _fd.read(buff_size)
        while len(buff):
            if not isinstance(buff, bytes):
                buff = buff.encode('utf')
            hasher.update(buff)
            buff = _fd.read(buff_size)
    if isinstance(file_path, str):
        with open(file_path, "rb") as fd:
            feed_hasher(fd)
    else:
        file_path.seek(0)
        feed_hasher(file_path)
    return hasher.hexdigest()


def touch_file(file_path):
    """
    Sets the access and modification times of the specified file to the
    current time.

    Parameters
    ----------
    file_path : str
        File path.
    """
    with open(file_path, 'a'):
        os.utime(file_path, None)


def normalize_path(path):
    """
    Returns an absolute pat with all variables expanded.

    Parameters
    ----------
    path : str
        Path to normalize.

    Returns
    -------
    str
        Normalized path.
    """
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def safe_mkdir(path, mode=0o750):
    """
    Creates a directory if it does not exist.

    Parameters
    ----------
    path : str
        Directory path.
    mode : int, optional
        Directory mode (as in chmod).

    Returns
    -------
    bool
        True if directory was created, False otherwise.

    Raises
    ------
    IOError
        If a directory creation failed.
    """
    if not os.path.exists(path):
        os.makedirs(path, mode)
        return True
    elif not os.path.isdir(path):
        raise IOError(errno.ENOTDIR, '{0} is not a directory'.format(path))
    return False


def safe_symlink(src, dst):
    """
    Creates symbolic link if it does not exists.

    Parameters
    ----------
    src : str
        Target name.
    dst : str
        Symlink name.

    Returns
    -------
    bool
        True if symlink has been created, False otherwise.
    """
    if not os.path.exists(dst):
        os.symlink(src, dst)
        return True
    return False


def find_files(src, mask):
    """
    Search files by mask (*.txt, filename.*, etc)
    @type src: str or unicode
    @param src: Source directory
    @type mask: str or unicode
    @param mask: search mask

    @rtype: list
    @return: list of found file paths
    """
    return [y for x in os.walk(src) for y in glob(os.path.join(x[0], mask))]


def urljoin_path(base_url, *args):
    """
    Joins a base URL and relative URL(s) with a slash.

    Parameters
    ----------
    base_url : str
        Base URL
    args : list
        List of relative URLs.

    Returns
    -------
    str
        A full URL combined from a base URL and relative URL(s).
    """
    parsed_base = urllib.parse.urlsplit(base_url)
    paths = itertools.chain((parsed_base.path,),
                            [urllib.parse.urlsplit(a).path for a in args])
    path = '/'.join(p.strip('/') for p in paths if p)
    return urllib.parse.urlunsplit((parsed_base.scheme, parsed_base.netloc,
                                    path, parsed_base.query,
                                    parsed_base.fragment))


def download_file(url, dst, ssl_cert=None, ssl_key=None, ca_info=None,
                  timeout=300, http_header=None, login=None, password=None,
                  no_ssl_verify=False):
    """
    Downloads remote or copies local file to the specified destination. If
    destination is a file or file-like object this function will write data
    to it. If dst is a directory this function will extract file name from url
    and create file with such name.

    Parameters
    ----------
    url : str
        URL (or path) to download.
    dst : str or file
        Destination directory, file or file-like object.
    ssl_cert : str, optional
        SSL certificate file path.
    ssl_key : str, optional
        SSL certificate key file path.
    ca_info : str, optional
        Certificate Authority file path.
    timeout : int
        Maximum time the request is allowed to take (seconds).
    http_header : list, optional
        HTTP headers.
    login : str, optional
        HTTP Basic authentication login.
    password : str, optional
        HTTP Basic authentication password.
    no_ssl_verify : bool, optional
        Disable SSL verification if set to True.

    Returns
    -------
    str or file
        Downloaded file full path if dst was file or directory,
        downloaded file name otherwise.
    """
    parsed_url = urllib.parse.urlparse(url)
    url_scheme = parsed_url.scheme
    file_name = None
    tmp_path = None
    if url_scheme in ('', 'file'):
        file_name = os.path.split(parsed_url.path)[1]

    if isinstance(dst, str):
        if os.path.isdir(dst):
            if file_name:
                # we are "downloading" a local file so we know its name
                dst_fd = open(os.path.join(dst, file_name), 'wb')
            else:
                # create a temporary file for saving data if destination is a
                # directory because we will know a file name only after download
                tmp_fd, tmp_path = tempfile.mkstemp(dir=dst, prefix='alt_')
                dst_fd = open(tmp_fd, 'wb')
        else:
            dst_fd = open(dst, 'wb')
    elif hasattr(dst, 'write'):
        dst_fd = dst
    else:
        raise ValueError('invalid destination')

    try:
        if url_scheme in ('', 'file'):
            with open(parsed_url.path, 'rb') as src_fd:
                shutil.copyfileobj(src_fd, dst_fd)
            return file_name if hasattr(dst, 'write') else dst_fd.name
        elif url_scheme == 'ftp':
            real_url = ftp_file_download(url, dst_fd)
        elif url_scheme in ('http', 'https'):
            real_url = http_file_download(
                url, dst_fd, timeout, login, password, http_header, ssl_cert,
                ssl_key, ca_info, no_ssl_verify
            )
        else:
            raise NotImplementedError('unsupported URL scheme "{0}"'.
                                      format(url_scheme))
    finally:
        # close the destination file descriptor if it was created internally
        if not hasattr(dst, 'write'):
            dst_fd.close()

    file_name = os.path.basename(urllib.parse.urlsplit(real_url)[2]).strip()
    if isinstance(dst, str):
        if tmp_path:
            # rename the temporary file to a real file name if destination
            # was a directory
            return shutil.move(tmp_path, os.path.join(dst, file_name))
        return dst
    return file_name


def http_file_download(url, fd, timeout=300, login=None, password=None,
                       http_header=None, ssl_cert=None, ssl_key=None,
                       ca_info=None, no_ssl_verify=None):
    """
    Download remote http(s) file to the specified file-like object.

    Parameters
    ----------
    url : str
        URL (or path) to download.
    fd : file
        Destination file or file-like object.
    timeout : int
        Maximum time the request is allowed to take (seconds).
    login : str, optional
        HTTP Basic authentication login.
    password : str, optional
        HTTP Basic authentication password.
    http_header : list, optional
        HTTP headers.
    ssl_cert : str, optional
        SSL certificate file path.
    ssl_key : str, optional
        SSL certificate key file path.
    ca_info : str, optional
        Certificate Authority file path.
    no_ssl_verify : bool, optional
        Disable SSL verification if set to True.

    Returns
    -------
    str
        Real download url.
    """
    if login and password:
        auth_hash = base64.b64encode('{0}:{1}'.format(
            login, password).encode('utf-8'))
        auth_header = 'Authorization: Basic {0}'.format(
            auth_hash.decode('utf-8'))
        if not http_header:
            http_header = []
        http_header.append(auth_header)
    curl = pycurl.Curl()
    curl.setopt(pycurl.URL, str(url))
    curl.setopt(pycurl.WRITEDATA, fd)
    curl.setopt(pycurl.FOLLOWLOCATION, 1)
    # maximum time in seconds that you allow the connection phase to the
    # server to take
    curl.setopt(pycurl.CONNECTTIMEOUT, 120)
    # maximum time in seconds that you allow the libcurl transfer
    # operation to take
    curl.setopt(pycurl.TIMEOUT, timeout)
    if http_header:
        curl.setopt(pycurl.HTTPHEADER, http_header)
    if ssl_cert:
        curl.setopt(pycurl.SSLCERT, str(os.path.expanduser(ssl_cert)))
    if ssl_key:
        curl.setopt(pycurl.SSLKEY, str(os.path.expanduser(ssl_key)))
    if ca_info:
        curl.setopt(pycurl.CAINFO, str(os.path.expanduser(ca_info)))
    elif ssl_cert and ssl_key:
        # don't verify certificate validity if we don't have CA
        # certificate
        curl.setopt(curl.SSL_VERIFYPEER, 0)
    if no_ssl_verify:
        curl.setopt(curl.SSL_VERIFYHOST, 0)
        curl.setopt(curl.SSL_VERIFYPEER, 0)
    curl.perform()
    status_code = curl.getinfo(pycurl.RESPONSE_CODE)
    if status_code not in (200, 206, 302):
        curl.close()
        raise Exception("cannot download: {0} status code".
                        format(status_code))
    real_url = urllib.parse.unquote(curl.getinfo(pycurl.EFFECTIVE_URL))
    curl.close()
    return real_url


def ftp_file_download(url, fd):
    """
    Download remote ftp file to the specified file-like object.

    Parameters
    ----------
    url : str
        URL (or path) to download.
    fd : file
        Destination file or file-like object.

    Returns
    -------
    str
        Real download url.
    """
    url_parsed = urllib.parse.urlparse(url)
    ftp = ftplib.FTP(url_parsed.netloc)
    ftp.login()
    ftp.cwd(os.path.dirname(url_parsed.path))
    ftp.retrbinary('RETR {0}'.format(os.path.basename(url_parsed.path)),
                   fd.write)
    ftp.quit()
    return url


def copy_dir_recursive(source, destination, ignore=None):
    """
    This function is much like shutil.copytree but will
    work in situations when destination dir already exists
    and non-empty.

    Parameters
    ----------
    source : str
        Source path for copying.
    destination : file
        Destination path for copying.
    ignore : list or None
        If not None will ignore every file matched by patterns.
    """
    if not ignore:
        ignore = []
    if not os.path.exists(destination):
        os.mkdir(destination)
    for filename in os.listdir(source):
        exclude = False
        for pattern in ignore:
            if re.match(pattern, filename):
                exclude = True
                break
        if exclude:
            continue
        src_name = os.path.join(source, filename)
        dst_name = os.path.join(destination, filename)
        if os.path.isdir(src_name):
            os.mkdir(dst_name)
            copy_dir_recursive(src_name, dst_name)
        else:
            shutil.copy(src_name, dst_name)


def is_gzip_file(file_path):
    """
    Checks if a file is a gzip archive.

    Parameters
    ----------
    file_path : str
        File path.

    Returns
    -------
    bool
        True if given file is a gzip archive, False otherwise.
    """
    with open(file_path, 'rb') as fd:
        return binascii.hexlify(fd.read(2)) == b'1f8b'
