# -*- mode:python; coding:utf-8; -*-
# author: Eugene G. Zamriy <ezamriy@cloudlinux.com>
# created: 27.11.2012 12:47

"""
CloudLinux Build System repositories management utilities.
"""

import hashlib
import rpm
import dnf

from dnf.rpm.transaction import initReadOnlyTransaction
from build_node.ported import (
    re_primary_filename, re_primary_dirname, to_unicode, return_file_entries
)
from build_node.utils.rpm_utils import (
    get_rpm_property, init_metadata, get_files_from_package,
    evr_to_string
)
from build_node.utils.file_utils import hash_file

__all__ = ['extract_metadata']


def extract_metadata(rpm_file, txn=None, checksum=None):
    """
    Extracts metadata from RPM file.

    Parameters
    ----------
    rpm_file : str or unicode
        RPM file absolute path.
    txn : dnf.rpm.transaction
        RPM transaction object.
    checksum : str or unicode
        SHA-1 checksum of the file (will be calculated if omitted).
    """
    transaction = initReadOnlyTransaction() if txn is None else txn
    try:
        sack = dnf.sack.Sack()
        yum_pkg = sack.add_cmdline_package(rpm_file)
    except Exception as e:
        raise Exception('Cannot extract %s metadata: %s' %
                        (rpm_file, str(e)))
    meta, hdr = init_metadata(rpm_file)
    pkg_files = get_files_from_package(hdr)
    # string fields
    if not checksum:
        checksum = hash_file(rpm_file, hashlib.sha1())
    meta['checksum'] = to_unicode(checksum)
    meta['checksum_type'] = 'sha'
    meta['sha256_checksum'] = to_unicode(hash_file(rpm_file, hashlib.sha256()))
    for f in ('name', 'version', 'arch', 'release', 'summary', 'description',
              'packager', 'url', 'license', 'group', 'sourcerpm'):
        v = getattr(yum_pkg, f)
        if v is not None:
            meta[f] = to_unicode(v)
    # int fields
    for f in ('epoch', 'buildtime',
              'installedsize',  # "hdrstart", "hdrend"
              ):
        if f == 'installedsize':
            v = getattr(yum_pkg, 'installsize')
        else:
            v = getattr(yum_pkg, f)
        if v is not None:
            meta[f] = int(v)
    meta['alt_ver_hash'] = evr_to_string([to_unicode(meta['epoch']),
                                          to_unicode(meta['version']),
                                          to_unicode(meta['release'])])
    for f in ('obsoletes', 'provides', 'conflicts'):
        for (name, flag, (epoch, ver, rel), _) in get_rpm_property(hdr, f):
            data = {'name': to_unicode(name)}
            if flag is not None:
                data['flag'] = to_unicode(flag)
            if epoch is not None:
                data['epoch'] = int(epoch)
            if ver is not None:
                data['version'] = to_unicode(ver)
            if rel is not None:
                data['release'] = to_unicode(rel)
            if f == 'provides':
                data['alt_ver_hash'] = evr_to_string([
                    to_unicode(epoch if epoch is not None else meta['epoch']),
                    to_unicode(ver if ver else meta['version']),
                    to_unicode(rel if rel else meta['release'])])
            if data not in meta[f]:
                meta[f].append(data)
    for (name, flag, (epoch, ver, rel), pre) in get_rpm_property(hdr,
                                                                 'requires'):
        data = {'name': to_unicode(name)}
        if flag is not None:
            data['flag'] = to_unicode(flag)
        if epoch is not None:
            data['epoch'] = int(epoch)
        if ver is not None:
            data['version'] = to_unicode(ver)
        if rel is not None:
            data['release'] = to_unicode(rel)
        if pre is not None:
            data['pre'] = int(pre)
        if data not in meta['requires']:
            meta['requires'].append(data)
    for f_type in ('file', 'dir', 'ghost'):
        for file_ in sorted(return_file_entries(pkg_files, f_type)):
            file_rec = {'name': to_unicode(file_), 'type': f_type}
            if f_type == 'dir':
                if re_primary_dirname(file_):
                    file_rec['primary'] = True
            elif re_primary_filename(file_):
                file_rec['primary'] = True
            if file_rec not in meta['files']:
                meta['files'].append(file_rec)
    if hdr[rpm.RPMTAG_EXCLUDEARCH]:
        meta['excludearch'] = [to_unicode(arch) for arch in
                               hdr[rpm.RPMTAG_EXCLUDEARCH]]
    if hdr[rpm.RPMTAG_EXCLUSIVEARCH]:
        meta['exclusivearch'] = [to_unicode(arch) for arch in
                                 hdr[rpm.RPMTAG_EXCLUSIVEARCH]]
    sign_txt = hdr.sprintf('%{DSAHEADER:pgpsig}')
    if sign_txt == '(none)':
        sign_txt = hdr.sprintf('%{RSAHEADER:pgpsig}')
    if sign_txt != '(none)':
        meta['alt_sign_txt'] = str(sign_txt)
    if txn is None:
        transaction.close()
    return meta
