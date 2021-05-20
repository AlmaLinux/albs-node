# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-11-05

"""
CloudLinux Build System utility functions for working with yum repositories.
"""

import os
import tempfile

import createrepo_c
import plumbum

from build_node.errors import DataNotFoundError


__all__ = ['create_repo', 'get_repo_modules_yaml_path']


def create_repo(repo_path, checksum_type=None, group_file=None, update=True,
                simple_md_filenames=True, no_database=False,
                compatibility=True, modules_yaml_content=None,
                keep_all_metadata=False):
    """
    Creates (or updates the existent) a yum repository using a createrepo_c
    tool.

    Parameters
    ----------
    repo_path : str
        Repository directory path.
    checksum_type : str, optional
        Checksum type used in repomd.xml and packages in the metadata.
    group_file : str, optional
        Group file path.
    update : bool, optional
        If True reuse the existent metadata instead of recalculating it when
        RPM size and modification time is unchanged.
    simple_md_filenames : bool, optional
        Do not include file's checksum in the filename if True.
    no_database : bool, optional
        Don't generate a sqlite database if True.
    compatibility : bool, optional
        Enforce maximum compatibility with classical createrepo.
    modules_yaml_content : str, optional
        Modules.yaml file content (only for modular repositories). Warning:
        an existent modules.yaml will be replaced with the new content.
    keep_all_metadata : bool, optional
        If true, additional metadata will be saved during createrepo_c update.
    """
    # TODO: check if there is an existent modules section in repodata and
    #       re-add it after repodata update
    createrepo = plumbum.local['createrepo_c']
    args = []
    if checksum_type:
        args.extend(('--checksum', checksum_type))
    if group_file:
        args.extend(('-g', group_file))
    if update:
        args.append('--update')
    if simple_md_filenames:
        args.append('--simple-md-filenames')
    if no_database:
        args.append('--no-database')
    if compatibility:
        args.append('--compatibility')
    if keep_all_metadata:
        args.append('--keep-all-metadata')
    args.append(repo_path)
    createrepo(*args)
    if modules_yaml_content:
        modifyrepo_c = plumbum.local['modifyrepo_c']
        with tempfile.NamedTemporaryFile(prefix='castor_') as fd:
            fd.write(modules_yaml_content.encode('utf-8'))
            fd.flush()
            modifyrepo_c('--simple-md-filenames', '--mdtype', 'modules',
                         fd.name, '--new-name', 'modules.yaml',
                         os.path.join(repo_path, 'repodata'))


def get_repo_modules_yaml_path(repo_path):
    """
    Returns a repository modules.yaml file path.

    Parameters
    ----------
    repo_path : str
        Repository directory path.

    Returns
    -------
    str or None
        Found modules.yaml file path or None if a repository is not modular.

    Raises
    ------
    build_node.errors.DataNotFoundError
        If repository metadata is not found.
    """
    repomd_path = os.path.join(repo_path, 'repodata/repomd.xml')
    if not os.path.exists(repomd_path):
        raise DataNotFoundError('{0} is not found'.format(repomd_path))
    repomd = createrepo_c.Repomd(repomd_path)
    for rec in repomd.records:
        if rec.type == 'modules':
            return os.path.join(repo_path, rec.location_href)
