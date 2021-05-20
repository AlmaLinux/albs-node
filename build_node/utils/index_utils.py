# -*- mode:python; coding:utf-8; -*-
# author: Eugene G. Zamriy <ezamriy@cloudlinux.com>
# created: 27.11.2012 12:47

"""
CloudLinux Build System repositories management utilities.
"""

import datetime
import hashlib
import logging
import re
import os
import subprocess
import traceback
import urllib.parse
import pymongo
import rpm
import dnf

from dnf.rpm.transaction import initReadOnlyTransaction
from build_node.ported import (re_primary_filename, re_primary_dirname,
                           to_unicode,
                           return_file_entries)
from build_node.utils.rpm_utils import (get_rpm_property, init_metadata,
                                    get_files_from_package, is_rpm_file,
                                    srpm_cpio_sha256sum, evr_to_string)
from build_node.utils.file_utils import hash_file
from build_node.utils.repodata_parser import LOCAL_REPO, RepodataParser
from build_node.utils.modularity import extract_stream_metadata

__all__ = ['create_repo', 'index_repo', 'extract_metadata', 'RepodataParser']


def format_envr(rpm_pkg):
    return '{rpm_pkg[epoch]}:{rpm_pkg[name]}-{rpm_pkg[version]}-' \
           '{rpm_pkg[release]}'.format(rpm_pkg=rpm_pkg)


class RepoIndexer(object):

    @staticmethod
    def calculate_rpm_checksums(rpm_meta):
        """
        Calculates RPM package checksums.

        Parameters
        ----------
        rpm_meta : dict
            RPM package metadata extracted from repodata.
        """
        if rpm_meta['checksum_type'] in ('sha', 'sha1'):
            rpm_meta['sha256_checksum'] = \
                to_unicode(hash_file(rpm_meta['full_location'],
                                     hashlib.sha256()))
        elif rpm_meta['checksum_type'] == 'sha256':
            rpm_meta['sha256_checksum'] = rpm_meta['checksum']
            rpm_meta['checksum'] = \
                to_unicode(hash_file(rpm_meta['full_location'],
                                     hashlib.sha1()))
            rpm_meta['checksum_type'] = 'sha'
        else:
            raise NotImplementedError('unsupported checksum type {0}'.
                                      format(rpm_meta['checksum_type']))
        # TODO: alt_srpm_cpio_checksum field is deprecated
        if rpm_meta['arch'] == 'src':
            srpm_cpio_sha256sum(rpm_meta['full_location'])

    @staticmethod
    def calculate_rpm_ver_hash(rpm_meta):
        """
        Calculates ver_hash values for RPM package (required for sorting).

        Parameters
        ----------
        rpm_meta : dict
            RPM package metadata.
        """
        rpm_meta['alt_ver_hash'] = \
            evr_to_string([to_unicode(rpm_meta['epoch']), rpm_meta['version'],
                           rpm_meta['release']])
        for prov in rpm_meta['provides']:
            prov_epoch = str(prov.get('epoch', rpm_meta['epoch']))
            prov_version = prov.get('version', rpm_meta['version'])
            prov_release = prov.get('release', rpm_meta['release'])
            prov['alt_ver_hash'] = evr_to_string([prov_epoch, prov_version,
                                                  prov_release])

    @staticmethod
    def compare_rpm_checksums(rpm_pkg, rpm_meta):
        """
        Compares RPM package and repodata checksums.

        Parameters
        ----------
        rpm_pkg : build_node.db.rpm_packages or dict
            RPM package.
        rpm_meta : dict
            RPM package metadata extracted from repodata.

        Return
        ----------
        bool
            True if checksums are equal, False otherwise.
        """
        if rpm_meta['checksum_type'] == 'sha256':
            checksum = rpm_pkg.get('sha256_checksum')
        elif rpm_meta['checksum_type'] == 'sha':
            checksum = rpm_pkg.get('checksum')
        else:
            raise NotImplementedError('unsupported checksum type {0!r}'.
                                      format(rpm_meta['checksum_type']))
        return checksum == rpm_meta['checksum']


def package_virtual_provides(db, package_name, repo_source_ids, repo_bin_ids):
    scursor = db['rpm_packages'].find({'name': package_name, 'arch': 'src',
                                       'alt_repo_id': {
                                           '$in': repo_source_ids}}).sort(
        'alt_ver_hash',
        pymongo.DESCENDING)
    try:
        src_rpm = next(scursor)
    except:
        return {}
    bcursor = db['rpm_packages'].find({
        'sourcerpm': src_rpm['location'],
        'arch': 'x86_64',
        'alt_repo_id': {'$in': repo_bin_ids}})
    provides = []
    for bin_package in bcursor:
        provides.extend(bin_package['provides'])
    return {'name': package_name,
            'epoch': src_rpm['epoch'],
            'version': src_rpm['version'],
            'release': src_rpm['release'],
            'alt_ver_hash': src_rpm['alt_ver_hash'],
            'provides': provides}


def platform_repos_ids(db, platform, src=False):
    repo_template = ['{}-os'.format(platform),
                     '{}-updates'.format(platform),
                     '{}-updates-testing'.format(platform)]
    arch = 'src' if src else 'x86_64'
    repos = db.repos.find({'name': {'$in': repo_template},
                           'arch': arch}, {'_id': 1})
    return [r['_id'] for r in repos]


def virtual_provides(db, platform, separate_rpm=None):
    """

    Parameters
    ----------
    db : pymongo.database.Database
    platform : str
        E.g. "cl6" or "cl7"
    separate_rpm : None or db.rpm_packages
        if it is None, we'll work with all src-rpms from metaplatform

    Returns
    -------
    """
    repo_source_ids = platform_repos_ids(db, platform, src=True)
    repo_bin_ids = platform_repos_ids(db, platform)
    if separate_rpm is None:
        src_rpms = db['rpm_packages'].find({
            'arch': 'src',
            'alt_repo_id': {'$in': repo_source_ids}})
    else:
        src_rpms = [separate_rpm]
    for src_rpm in src_rpms:
        srpm_virtual_prov = db['srpm_virtual_provides'].find_one(
            {'name': src_rpm['name'], 'platform': platform})
        if (srpm_virtual_prov is None or
                srpm_virtual_prov['alt_ver_hash'] < src_rpm['alt_ver_hash']):
            new_srpm_vp = package_virtual_provides(db,
                                                   src_rpm['name'],
                                                   repo_source_ids,
                                                   repo_bin_ids)
            if not new_srpm_vp:
                continue
            new_srpm_vp['platform'] = platform
            db['srpm_virtual_provides'].find_and_modify(
                query={'name': src_rpm['name'], 'platform': platform},
                update={'$set': new_srpm_vp},
                upsert=True)


def get_platform_from_rpm(db_pkg):
    """
    Detects a RPM package platform.

    Parameters
    ----------
    db_pkg : dict
        RPM package.

    Returns
    -------
    str or None
        Platform or None if detection failed.
    """
    re_rslt = re.search(r'\.el(\d{1})', db_pkg['release'])
    if not re_rslt and 'alt_url' in db_pkg:
        re_rslt = re.search(r'/(6|7)/', db_pkg['alt_url'])
    if re_rslt:
        return 'cl{0}'.format(re_rslt.group(1))


def get_installation_count(db, package):
    """
    Find package installation statistic.

    Parameters
    ----------
    db : pymongo.database.Database
        Build system database.
    package : dict
        RPM package.

    Returns
    -------
    int or None
        Installation count or None if detection failed.
    """
    query = {'name': package['name'], 'version': package['version'],
             'release': package['release']}
    statistic = db['rpm_install_stats'].find_one(
        query, sort=[('ts', pymongo.DESCENDING)])
    if statistic:
        return statistic['count']


def index_repo_repodata(db, repo, log=None):
    """
    Repository (re-)indexation using repodata files.

    Parameters
    ----------
    db : pymongo.database.Database
        MongoDB database object.
    repo : build_node.db.repos
        Repository to process.
    log : logging.logger
        Current context logger.
    """
    if not log:
        log = logging.getLogger(__name__)
    log.info('(re-)indexing {repo[name]}.{repo[arch]} using repodata files'.
             format(repo=repo))
    if repo.get('path'):
        # local repository processing
        parser = RepodataParser(repo['path'])
    else:
        # remote repository processing
        ssl_cert = ssl_key = ca_info = None
        if repo.get('subscription'):
            ssl_cert = repo['subscription'].get('ssl_cert')
            ssl_key = repo['subscription'].get('ssl_key')
            ca_info = repo['subscription'].get('ca_info')
        parser = RepodataParser(repo['url'], ssl_cert=ssl_cert,
                                ssl_key=ssl_key, ssl_cainfo=ca_info)
    try:
        repomd_checksum = parser.repomd_checksum
        if repo.get('repomd_checksum') == repomd_checksum:
            log.info('skipping {repo[name]}.{repo[arch]} re-indexation because'
                     ' repomd.xml checksum ({repo[repomd_checksum]}) is not '
                     'changed'.format(repo=repo))
            db['repos'].update(
                {'_id': repo['_id']},
                {'$set': {'update_ts': datetime.datetime.utcnow()}}
            )
            return
        log.debug('{repo[name]}.{repo[arch]} repomd.xml checksum is {0}'.
                  format(repomd_checksum, repo=repo))
        # location = (_id, checksum, checksum_type)
        db_packages = {}
        for db_pkg in db['rpm_packages'].\
                find({'alt_repo_id': repo['_id']},
                     {'name': 1, 'version': 1, 'release': 1, 'epoch': 1,
                      'arch': 1, 'location': 1, 'alt_url': 1, 'checksum': 1,
                      'checksum_type': 1, 'alt_ver_hash': 1}):
            if db_pkg['arch'] == 'src':
                meta_platform = get_platform_from_rpm(db_pkg)
                if meta_platform:
                    virtual_provides(db, meta_platform, db_pkg)
            if repo.get('url') and not db_pkg.get('alt_url'):
                # add remote download URL if it's missing
                download_url = urllib.parse.urljoin(repo['url'],
                                                    db_pkg['location'])
                db['rpm_packages'].update({'_id': db_pkg['_id']},
                                          {'$set': {'alt_url': download_url}})
                log.debug("setting {0} 'alt_url' to {1}".
                          format(format_envr(db_pkg), download_url))
            db_packages[db_pkg['location']] = (db_pkg['_id'],
                                               db_pkg['checksum'],
                                               db_pkg['checksum_type'])
        for pkg in parser.iter_packages():
            if pkg['location'] in db_packages:
                db_id, db_checksum, db_checksum_type = \
                    db_packages[pkg['location']]
                if db_checksum != pkg['checksum'] or \
                        db_checksum_type != pkg['checksum_type']:
                    # package checksum is changed. Package will be updated
                    pkg['_id'] = db_id
                    del db_packages[pkg['location']]
                else:
                    # checksum isn't changed. Skip this package processing
                    del db_packages[pkg['location']]
                    continue
            if parser.repo_type == LOCAL_REPO:
                if repo.get('url'):
                    pkg['alt_url'] = urllib.parse.urljoin(repo['url'],
                                                          pkg['location'])
            else:
                # NOTE: we use "alt_url" field instead of "full_location" for
                #       remote repositories
                pkg['alt_url'] = pkg['full_location']
                del pkg['full_location']
            pkg['alt_repo_id'] = repo['_id']
            pkg['release_ts'] = datetime.datetime.utcnow()
            pkg['release_install'] = get_installation_count(db, pkg)
            RepoIndexer.calculate_rpm_ver_hash(pkg)
            if pkg.get('_id'):
                # update existent RPM package object
                db['rpm_packages'].update({'_id': pkg['_id']}, pkg)
                log.debug('{0} (_id={1!r}) updated'.format(format_envr(pkg),
                                                           pkg['_id']))
            else:
                # insert new RPM package object
                rpm_id = db['rpm_packages'].insert(pkg)
                log.debug('{0} (_id={1!r}) added to the database'.
                          format(format_envr(pkg), rpm_id))

        if parser.is_modular():
            for module, stream in parser.iter_module_streams():
                dict_stream = extract_stream_metadata(module, stream)
                dict_stream['bs_repo_id'] = repo['_id']
                stream_key = {
                    'bs_repo_id': repo['_id'],
                    'name': dict_stream['name'],
                    'stream': dict_stream['stream'],
                    'version': dict_stream['version'],
                    'context': dict_stream['context'],
                    'arch': dict_stream['arch']
                }
                db['modular_streams'].update_one(
                    stream_key, {'$set': dict_stream}, upsert=True
                )

        # delete from DB RPM's that was removed from repo
        if len(db_packages):
            log.info('deleting {0} RPM packages:\n\t{1}'.
                     format(len(db_packages),
                            '\n\t'.join(db_packages.keys())))
            ids = [_id for _id, _, _ in db_packages.values()]
            db['rpm_packages'].remove({'_id': {'$in': ids}})
        update_q = {'$set': {'repomd_checksum': repomd_checksum,
                             'update_ts': datetime.datetime.utcnow()}}
        if repo.get('groups'):
            update_q['$unset'] = {'groups': ''}
        db['repos'].update({'_id': repo['_id']}, update_q)
    finally:
        parser.close()


def index_repo_scan(db, repo):
    """
    Repository (re-)indexation using recursive directories scanning.

    Parameters
    ----------
    db : pymongo.database.Database
        Alternatives database object.
    repo : build_node.db.repos
        Repository to process.
    """
    log = logging.getLogger(__name__)
    log.info('(re-)indexing %s using recursive directories scanning' % repo)
    repo_id = repo['_id']
    repo_path = repo['path']
    tags = repo.get('tags', [])
    #
    rpm_files = {}           # (full) file path = SHA-1 checksum
    for root, dirs, files in os.walk(repo_path, followlinks=True):
        for f in files:
            f_path = os.path.join(root, f)
            if not is_rpm_file(f_path, check_magic=True):
                log.debug('skipping %s: it is not a RPM file' % f_path)
                continue
            rpm_files[f_path] = hash_file(f_path, hashlib.sha1())
    #
    rpm_collection = db.RPMPackage.collection
    to_delete = []
    for rpm_pkg in rpm_collection.find({'alt_repo_id': repo_id},
                                       {'checksum': 1, 'full_location': 1}):
        full_location = rpm_pkg['full_location']
        if full_location not in rpm_files:
            to_delete.append(rpm_pkg['_id'])
            log.debug('%s is removed from repository' % full_location)
            continue
        elif rpm_files[full_location] != rpm_pkg['checksum']:
            raise NotImplementedError('changed RPM handling is not '
                                      'implemented yet: %s' % full_location)
        log.debug('%s is already indexed' % full_location)
        del rpm_files[full_location]
    if to_delete:
        log.info('deleting %s RPM packages' % len(to_delete))
        rpm_collection.remove({'_id': {'$in': to_delete}})
    #
    txn = initReadOnlyTransaction()
    for f_path, checksum in iter(list(rpm_files.items())):
        try:
            meta = extract_metadata(f_path, txn)
        except Exception as e:
            msg = 'cannot extract %s metadata: %s' % (f_path, str(e))
            log.error('%s. Traceback:\n%s' % (msg, traceback.format_exc()))
            txn.close()
            raise Exception(msg)
        meta['full_location'] = to_unicode(f_path)
        meta['location'] = to_unicode(os.path.relpath(f_path, repo_path))
        meta['alt_repo_id'] = repo_id
        meta['release_ts'] = datetime.datetime.utcnow()
        meta['release_install'] = get_installation_count(db, meta)
        if tags:
            meta['alt_tags'] = tags
        try:
            rpm_id = rpm_collection.insert(meta)
            rpm_str = '%(epoch)s:%(name)s-%(version)s-%(release)s' % meta
            log.debug("%s (_id='%s') added to the database" %
                      (rpm_str, rpm_id))
        except Exception as e:
            msg = 'cannot save %s to the database: %s' % (f_path, str(e))
            log.error('%s. Traceback:\n%s' % (msg, traceback.format_exc()))
            txn.close()
            raise Exception(msg)
    txn.close()


def index_repo(db, repo, log=None):
    """
    Yum (RPM) repository (re-)indexation function.

    Parameters
    ----------
    db : pymongo.database.Database
        Alternatives MongoDB database object.
    repo : build_node.db.repos or dict
        Repository to process.
    log : logging.Logger or None
        Alternatives logger object.
    """
    # TODO: some sort of locking required to protect data
    #  from concurrent update
    if not log:
        log = logging.getLogger(__name__)
    if repo.get('path'):
        # local repository processing
        if not os.path.exists(repo['path']):
            raise Exception('repository directory {0} does not exists'.
                            format(repo['path']))
        elif repo.get('use_repodata'):
            repodata_dir = os.path.join(repo['path'], 'repodata')
            if not os.path.exists(repodata_dir):
                raise Exception('repodata directory {0} does not exists'.
                                format(repodata_dir))
            index_repo_repodata(db, repo, log=log)
        else:
            index_repo_scan(db, repo)
    elif repo.get('url'):
        # remote repository processing
        if repo.get('use_repodata'):
            index_repo_repodata(db, repo, log=log)
        else:
            raise Exception('cannot process remote repository without '
                            'repodata')
    else:
        raise Exception('cannot detect repository path/URL')


def create_repo(path, checksum_type=None, update=False, cache_dir=None,
                group_file=None, simple_md_filenames=False, no_database=False,
                compatibility=False):
    """
    Executes createrepo_c command for given directory.

    Parameters
    ----------
    path : str or unicode
        Directory path.
    checksum_type : str
        Checksum type (e.g. sha, sha256. See
        createrepo_c --checksum argument description for details).
    update : bool
        Use existent repodata to speed up creation of
        new if True, regenerate repodata otherwise.
    group_file : str
        Path to groupfile (comps.xml) to include in repodata.
    simple_md_filenames : bool
        Do not include the file's checksum in the metadata filename.
    no_database : bool
        Do not generate sqlite databases in the repository.
    compatibility : bool
        Enforce maximal compatibility with classical createrepo.

    Raise
    ----------
    Exception
        createrepo_c command execution failed.
    """
    cmd = ['createrepo_c']
    if checksum_type:
        cmd.extend(('--checksum', checksum_type))
    if update:
        cmd.append('--update')
    if cache_dir is not None:
        cmd.extend(('--cachedir', cache_dir))
    if group_file:
        cmd.extend(('--groupfile', group_file))
    if simple_md_filenames:
        cmd.append('--simple-md-filenames')
    if no_database:
        cmd.append('--no-database')
    if compatibility:
        cmd.append('--compatibility')
    cmd.append(path)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    out, err = proc.communicate()
    if proc.returncode != 0:
        raise Exception('cannot createrepo %s: %s. Return code is %s' %
                        (path, out, proc.returncode))


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
