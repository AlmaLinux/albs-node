# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-04-16
# TODO: that module is a copy-paste of the cla.build_system.multilib module
#       and should be reviewed / rethought when we have time.

"""Multilib packages detection utility functions."""

import logging
import re


__all__ = ["is_multilib_package"]


def clean_release(release):
    """
    Removes CloudLinux specific data (currently only ".cloudlinux") from
    package's release field.

    Parameters
    ----------
    release : str
        Package release.

    Returns
    -------
    str
        Package release without CloudLinux specific data.
    """
    release = re.sub(r'\.el\d.*', '', release)
    return re.compile(f'^{re.escape(release)}')


def is_multilib_package(db, platform, rpm_package, log=None):
    """
    Checks if given package is a multilib package (32bit package in 64bit
    repository).

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    platform : dict
        Target build platform.
    rpm_package : dict
        RPM package.
    log : logging.Logger
        Logger to use.

    Returns
    -------
    bool
        True if given package is a multilib package, False otherwise.
    """
    if not log:
        log = logging.getLogger(__name__)
    nevra = '{p[epoch]}:{p[name]}-{p[version]}-{p[release]}.{p[arch]}'.\
        format(p=rpm_package)
    supported = ['centos6', 'centos6els', 'CL6']
    log.debug('checking if {0} is a multilib package'.format(nevra))
    repos_q = []
    db_list_q = []
    for src in platform.get('multilib_sources', ()):
        type_ = src.get('type')
        if type_ == 'repo':
            repos_q.append({'name': src['name'], 'arch': src['arch']})
        elif type_ == 'db_multilib_list':
            db_list_q.append({'platform': src['platform']})
            # TODO: is this really needed?
            if src['platform'] == 'CL7RHEL':
                db_list_q.append({'platform': 'CL7'})
    # check if package is present in reference repositories
    if repos_q:
        repo_ids = [r['_id'] for r in db['repos'].find({'$or': repos_q},
                                                       {'_id': True})]
        if repo_ids:
            query = {'alt_repo_id': {'$in': repo_ids},
                     'name': rpm_package['name'],
                     'epoch': rpm_package['epoch'],
                     'version': rpm_package['version'],
                     'release': clean_release(rpm_package['release']),
                     'arch': {'$nin': ['x86_64', 'src']}
                     }
            if platform['name'] in supported:
                query.pop('release')
            rpm_pkg = db['rpm_packages'].find_one(query, {'_id': True})
            if rpm_pkg:
                log.debug('{0} is a multilib package: found it in reference '
                          'repositories {1}'.
                          format(nevra, ', '.join([str(i) for i in repo_ids])))
                return True
            # check if we have some packages built from sourcerpm in reference
            # repositories
            rpm_pkg = db['rpm_packages'].\
                find_one({'alt_repo_id': {'$in': repo_ids},
                          'epoch': rpm_package['epoch'],
                          'version': rpm_package['version'],
                          'sourcerpm': clean_release(
                              rpm_package['sourcerpm'])})
            if rpm_pkg:
                return False
    # check package's presence in static multilib lists
    if db_list_q:
        if db['multilib_list'].find_one({'$or': db_list_q,
                                         'name': rpm_package['name']}):
            log.debug('{0} is a multilib package: found it in static lists'.
                      format(nevra))
            return True
    return False
