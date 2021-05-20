# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2018-01-19

"""Utility functions to work with Debian packages and platforms"""

import os
import re
import apt_pkg
import hashlib
import traceback
import time

import plumbum

from build_node.errors import CommandExecutionError
from build_node.utils.file_utils import hash_file
from debian.debfile import DebFile, Deb822
from build_node.build_node_errors import BuildError
from build_node.utils.gerrit import parse_gerrit_ref

__all__ = ['find_debian_control_file', 'deb_extract_metadata',
           'dsc_extract_metadata', 'parse_control_file', 'detect_debian',
           'dpkg_parsechangelog', 'dch_add_changelog_record',
           'parse_deb_version', 'parse_sources_list_url', 'format_deb_version',
           'init_apt_config', 'format_apt_dependency', 'compare_deb_versions',
           'add_timestamp_changelog_deb']


# Common fields mapping to RPM names
cf_mapping = {'package': 'name',
              'architecture': 'arch',
              'installed-size': 'installedsize',
              'homepage': 'url',
              'source': 'source'}

# Dependency solving fields mapping to RPM names
ds_mapping = {'depends': 'deb_requires',
              'pre-depends': 'deb_requires',
              'build-depends': 'deb_buildrequires',
              'build-depends-indep': 'deb_buildrequires',
              'conflicts': 'deb_conflicts'}

# Debian-only dependency fields
debian_only_deps = ['suggests', 'recommends', 'enhances', 'breaks', 'provides',
                    'replaces']

# Debian-only fields, only usable when parsing .deb file
# NOTE: on Debian 9 "auto-built-package" is set to "debug-symbols" for debug
#       packages
debian_only_fields = ['multi-arch', 'build-ids', 'version', 'section',
                      'maintainer', 'priority', 'auto-built-package',
                      'original-maintainer']


def detect_debian(platform_name):
    """
    Checks if platform is Debian-like (Debian, Ubuntu)

    Parameters
    ----------
    platform_name   : str or unicode
        Platform name

    Returns
    -------
    bool
        True if platform is Debian-like, False otherwise

    """
    return platform_name.startswith(('debian', 'ubuntu', 'raspbian'))


def find_debian_control_file(base_dir):
    """
    Finds Debian control file in provided directory

    Parameters
    ----------
    base_dir :  str
        Directory on file system where to look for control file

    Returns
    -------
    str
        Full path to file or empty string if it's absent

    """
    control_path = os.path.join(base_dir, 'debian', 'control')
    if os.path.exists(control_path):
        return control_path
    return ''


def string_to_ds(dep_str):
    """
    Converts a dependency string into Build System dependency set.

    Parameters
    ----------
    dep_str : str
        Dependency string to convert.

    Returns
    -------
    list of dict
        Dependency set.

    Raises
    ------
    ValueError
        If dependency string is malformed.
    Examples

    In [19]: string_to_ds('alt-python35-pam (>= 1.8.4)')
    Out[19]: [{'flag': 'GE', 'name': 'alt-python35-pam', 'version': '1.8.4'}]

    In [20]: string_to_ds('iptables (>= 1.8.5) | iptables (>= 1.4.21-18.0.1)')
    Out[20]:
    [{'flag': 'GE', 'name': 'iptables', 'version': '1.8.5'},
    {'flag': 'GE', 'name': 'iptables', 'version': '1.4.21-18.0.1'}]
    """
    if '|' in dep_str:
        return [string_to_ds(ds)[0] for ds in dep_str.split('|')]
    dep_str = dep_str.strip()
    if ' [' in dep_str:
        dep_str = dep_str.split(' [')[0]
    re_rslt = re.search(r'^(\S+)\s+\(\s*(<<|<|<=|=|>=|>|>>)\s*(\S+)\)$',
                        dep_str)
    if not re_rslt:
        special_cases = re.search(r'^(\S+)\s+(<!stage1>)$', dep_str)
        if re.search('[()<>=]', dep_str) and not special_cases:
            raise ValueError(
                'invalid dependency string "{0}"'.format(dep_str))
        return [{'name': dep_str}]
    flags = {'<<': 'LT', '<': 'LT', '<=': 'LE', '=': 'EQ', '>=': 'GE',
             '>>': 'GT', '>': 'GT'}
    name, flag, version = re_rslt.groups()
    return [{'name': name, 'flag': flags[flag], 'version': version}]


def parse_control_file(file_path):
    """
    Parses Debian control file and returns de-duplicated dependency
    structure for all packages described in it.

    Parameters
    ----------
    file_path :     str or unicode
        Path to Debian control file

    Returns
    -------
    dict
        De-duplicated dependency structure

    """
    dep_struct = {}
    dep_fields = ['deb_requires', 'deb_buildrequires', 'deb_conflicts',
                  'suggests', 'recommends', 'enhances', 'breaks', 'name']

    def parse_pkg_data(pkg):
        """
        Parses dependencies for package

        Parameters
        ----------
        pkg :   debian.deb822.Deb822
            Debian package structure

        Returns
        -------
        dict
            Package dependencies

        """
        deps = {value: [] for value in dep_fields}
        for field, value in pkg.items():
            field_l = field.lower()
            # Check if field is in common mapping
            if field_l in cf_mapping:
                deps[cf_mapping[field_l]] = int(value) if \
                    field_l == 'installed-size' else value
            if field_l == 'source':
                deps[field_l] = value
            # Check if field is in dependency solving mapping
            # If yes then convert dependencies in RPM format and
            # add them into structure
            elif field_l in ds_mapping:
                if value:
                    parsed_values = [string_to_ds(dep_str)[0]
                                     for dep_str in value.split(',')]
                    deps[ds_mapping[field_l]].extend(parsed_values)
            # If field is Debian-only dependency, parse it in RPM format anyway
            elif field_l in debian_only_deps:
                if value:
                    parsed_values = [string_to_ds(dep_str)[0]
                                     for dep_str in value.split(',')]
                    deps[field_l].extend(parsed_values)
            # If field isn't in any mapping, just skip it from parsing
            else:
                continue
        return deps

    try:
        with open(file_path, 'r') as ctrl_file:
            package = Deb822(ctrl_file)
            while package:
                data = parse_pkg_data(package)
                for f in dep_fields:
                    # First stage for data de-duplication
                    values = dep_struct.get(f)
                    if not values:
                        if not data.get(f):
                            values = []
                        else:
                            if data.get(f):
                                values = data.get(f)
                    else:
                        if data.get(f):
                            # Second stage of de-duplication: add only items
                            # that are not in list already. As we use list of
                            # dicts, set -> list conversionis not
                            # applicable here.
                            for item in data.get(f):
                                if item not in values:
                                    values.append(item)
                    dep_struct[f] = values
                package = Deb822(ctrl_file)
    finally:
        return dep_struct


def deb_extract_metadata(deb_file):
    """
    Extracts metadata from the .deb file.

    Parameters
    ----------
    deb_file : str
        .deb file path.

    Returns
    -------
    dict
        .deb package metadata.
    """
    meta = {'type': 'deb'}
    deb = DebFile(deb_file)
    for k, v in deb.debcontrol().items():
        k = k.lower()
        v = v.strip()
        if k in cf_mapping:
            meta[cf_mapping[k]] = int(v) if k == 'installed-size' else v
        elif k in debian_only_fields:
            meta[k] = v
        elif k == 'source':
            meta[k] = v
        elif k == 'description':
            lines = v.split('\n')
            if not lines:
                continue
            meta['summary'] = lines[0].strip()
            description = '\n'.join([s.strip() for s in lines[1:]])
            if description:
                meta['description'] = description
        elif k in ds_mapping:
            if v:
                meta[ds_mapping[k]] = [string_to_ds(ds_str)[0]
                                       for ds_str in v.split(',')]
        elif k in debian_only_deps:
            if v:
                meta[k] = [string_to_ds(ds_str)[0]
                           for ds_str in v.split(',')]
        else:
            raise NotImplementedError(
                'unsupported deb package field {0}'.format(k))
    md5sums = deb.md5sums()
    file_data = deb.data.tgz()
    files = []
    for file_rec in [f for f in file_data if not f.isdir()]:
        rec = dict()
        rec['name'] = file_rec.name
        if rec['name'].startswith('./'):
            rec['name'] = rec['name'][1:]
        if file_rec.issym():
            rec['type'] = 'symlink'
            rec['linkpath'] = file_rec.linkpath
        else:
            rec['type'] = 'file'
        rec_md5 = md5sums.get(rec['name'][1:])
        if rec_md5:
            rec['checksum_type'] = 'md5'
            rec['checksum'] = rec_md5
        else:
            rec['checksum_type'] = 'none'
            rec['checksum'] = ''
        files.append(rec)
    if files:
        meta['files'] = files
    meta['checksum_type'] = 'sha256'
    meta['checksum'] = hash_file(deb_file, hashlib.sha256())
    return meta


def dsc_extract_metadata(dsc_file):
    """
    Extracts metadata from the .dsc file.

    Parameters
    ----------
    dsc_file : str or file-like object.
        .dsc file path or file content.

    Returns
    -------
    dict
        .dsc package metadata.
    """
    mapping = {'source': 'name', 'binary': 'binary_name'}
    meta = {'type': 'dsc'}
    fd = dsc_file
    if isinstance(dsc_file, str):
        fd = open(dsc_file, 'r')
    dsc = Deb822(fd.read())
    # TODO: add support of other .dsc file fields
    for k, v in dsc.items():
        k = k.lower()
        v = v.strip()
        if k in mapping:
            meta[mapping[k]] = v
        elif k in cf_mapping:
            meta[cf_mapping[k]] = v
        elif k in debian_only_fields:
            meta[k] = v
        elif k == 'build-depends':
            if v:
                meta['requires'] = [string_to_ds(ds_str)[0]
                                    for ds_str in v.split(',')]
        elif k == 'files':
            files_list = []
            for f in v.split('\n'):
                re_rslt = re.search(r'^(\S+)\s+\S+\s+(\S+)$', f)
                if re_rslt:
                    md5, f_name = re_rslt.groups()
                    files_list.append({'name': str(f_name),
                                       'checksum': str(md5),
                                       'checksum_type': 'md5',
                                       'type': 'file'})
        elif k == 'checksums-sha256':
            files_list = []
            for f in v.split('\n'):
                f = f.strip()
                re_rslt = re.search(r'^(\S+)\s+\S+\s+(\S+)$', f)
                if re_rslt:
                    sha256, f_name = re_rslt.groups()
                    files_list.append({'name': str(f_name),
                                       'checksum': str(sha256)})
            meta['checksums_sha256'] = files_list
    meta['checksum_type'] = 'sha256'
    meta['checksum'] = hash_file(fd, hashlib.sha256())
    return meta


def dpkg_parsechangelog(sources_dir, field_name):
    """
    Extracts the specified field from the debian/changelog file using the
    dpkg-parsechangelog command.

    Parameters
    ----------
    sources_dir : str
        Package sources directory path.
    field_name : str
        Changelog field name (e.g. Name, Version).

    Returns
    -------
    str
        Extracted field value.

    Raises
    ------
    build_node.errors.CommandExecutionError
        If dpkg-parsechangelog command returned a non-zero exit code or didn't
        print a value.
    """
    command = plumbum.local['dpkg-parsechangelog']['--show-field', field_name]
    exit_code, stdout, stderr = \
        command.run(cwd=sources_dir, env={'HISTFILE': '/dev/null',
                                          'LANG': 'en_US.UTF-8'}, retcode=None)
    stdout = stdout.strip()
    error_message = None
    if exit_code != 0:
        error_message = 'can not parse debian/changelog file: {0}'. \
            format(stderr)
    elif not stdout:
        error_message = 'there is no package {0} in debian/changelog file'.\
            format(field_name.lower())
    if error_message:
        raise CommandExecutionError(error_message, exit_code, stdout, stderr,
                                    command.formulate())
    return stdout


def dch_add_changelog_record(sources_dir, distribution, changelog,
                             new_version=None, user_email=None,
                             user_name=None):
    """
    Adds a new record to a debian/changelog file.

    Parameters
    ----------
    sources_dir : str
        Package sources directory path.
    distribution : str
        Use the specified distribution for a changelog entry.
    changelog : str
        Changelog entry. Note: there is no multi-line strings support in dch.
    new_version : str, optional
        New package version. A current version will be bumped if omitted.
    user_email : str, optional
        Package maintainer e-mail address.
    user_name : str, optional
        Package maintainer name (e.g. Ivan Ivanov).

    Raises
    -------
    build_node.errors.CommandExecutionError
        If dch command returned a non-zero exit code.
    """
    args = ['--no-auto-nmu', '--force-distribution', '--distribution',
            distribution]
    if new_version:
        args.append('--newversion')
        args.append(new_version)
    args.append(changelog)
    env = {'HISTFILE': '/dev/null', 'LANG': 'en_US.UTF-8'}
    if user_email:
        env['EMAIL'] = user_email
    if user_name:
        env['NAME'] = user_name
    command = plumbum.local['dch'][args]
    exit_code, stdout, stderr = command.run(cwd=sources_dir, env=env,
                                            retcode=None)
    if exit_code != 0:
        error_message = 'can not execute dch command: {0}'.format(stderr)
        raise CommandExecutionError(error_message, exit_code, stdout, stderr,
                                    command.formulate())


def parse_deb_version(version_str):
    """
    Parses a debian package version string.

    Parameters
    ----------
    version_str : str or unicode
        Debian package version.

    Returns
    -------
    tuple
        Tuple of three elements: epoch, upstream version and debian revision.
        It will return "0" for epoch and debian revision if those fields are
        missing.
    """
    version_str = str(version_str)
    epoch_rslt = re.search(r'^((\d+)(?::)|)(.*?)$', version_str)
    if not epoch_rslt:
        raise Exception('invalid version string')
    _, epoch, tail_version = epoch_rslt.groups()
    if not epoch:
        epoch = '0'
    index_value = '+' if '-' not in version_str else '-'
    index_function = str.index if '-' not in version_str else str.rindex
    try:
        idx = index_function(tail_version, index_value)
    except ValueError:
        return epoch, tail_version, '0'
    return epoch, tail_version[0:idx], tail_version[idx + 1:]


def parse_sources_list_url(url):
    """
    Splits a Debian sources.list URL to components.

    Parameters
    ----------
    url : str
        Repository URL.

    Returns
    -------
    dict
        Debian repository information: type, URL, distribution and components.
    """
    re_rslt = re.search(r'^(deb(?:-src|))\s+(\S+)\s+([\w/-]+)\s+(.+)$', url)
    if not re_rslt:
        raise ValueError('invalid sources list URL syntax')
    repo_type, base_url, distro, components = re_rslt.groups()
    return {'repo_type': repo_type,
            'distro': distro,
            'components': components.split(),
            'url': base_url}


def format_deb_version(deb_version):
    if deb_version.get('epoch') in (None, '0'):
        epoch = ''
    else:
        epoch = '{0}:'.format(deb_version['epoch'])
    if deb_version.get('revision') in (None, '0'):
        revision = ''
    else:
        revision = '-{0}'.format(deb_version['revision'])
    return '{0}{1}{2}'.format(epoch, deb_version['version'], revision)


def init_apt_config(base_dir):
    """
    Init APT library for using temp dir

    Parameters
    ----------
    base_dir : str
        root dir for storing all cache and configs

    Returns
    -------
    str
        path of 'sources.list' file
    """
    etc_dir = os.path.join(base_dir, 'etc')
    src_list = os.path.join(etc_dir, 'sources.list')
    src_list_dir = os.path.join(etc_dir, 'sources.list.d')
    state_dir = os.path.join(base_dir, 'state')
    lists_dir = os.path.join(state_dir, 'lists')
    status_file = os.path.join(state_dir, 'status')
    cache_dir = os.path.join(base_dir, 'cache')
    log_dir = os.path.join(base_dir, 'log')

    for directory in [etc_dir, src_list_dir, state_dir, lists_dir,
                      cache_dir, log_dir]:
        os.makedirs(directory)

    apt_pkg.config.set('Dir', base_dir)
    apt_pkg.config.set('Dir::Cache', cache_dir)
    apt_pkg.config.set('Dir::Etc', etc_dir)
    apt_pkg.config.set('Dir::Etc::SourceList', src_list)
    apt_pkg.config.set(
        'Dir::Etc::trustedparts', '/etc/apt/trusted.gpg.d')
    apt_pkg.config.set('Dir::Log', log_dir)
    apt_pkg.config.set('Dir::State', state_dir)
    apt_pkg.config.set('Dir::State::status', status_file)

    apt_pkg.config.clear('APT::Update::Post-Invoke')
    apt_pkg.config.clear('APT::Update::Post-Invoke-Success')
    apt_pkg.config.clear('DPkg::Post-Invoke')

    open(status_file, 'w')  # file should exists
    return src_list


def format_apt_dependency(package):
    """
    Convert package dependency to str.

    Parameters
    ----------
    package : apt_pkg.Dependency
        Dependency, loaded from apt cache.

    Returns
    -------
    str
        Formated dependency str.
    """
    result = package.target_pkg.name
    if package.target_ver:
        result += ' ({0} {1})'.format(
            package.comp_type, package.target_ver)
    return result


def compare_deb_versions(comp, candidate_ver, repo_ver):
    """
    Verifies that package versions match the condition.

    Parameters
    ----------
    comp : str
        Condition sign.
    candidate_ver : str
        Left condition operand.
    repo_ver : str
        Right condition operand.

    Returns
    -------
    bool
        True if packages match the condition,
        False otherwise.
    """
    comparison = apt_pkg.version_compare(candidate_ver, repo_ver)
    if (comp == '>=' and comparison >= 0) or (comp == '=' and comparison == 0):
        return True
    if (comp == '<=' and comparison <= 0) or (comp == '>' and comparison == 1):
        return True
    if (comp == '!=' and comparison != 0) or \
            (comp == '<' and comparison == -1):
        return True
    return False


def add_timestamp_changelog_deb(changelog_file, package_name, ref=None):
    """
    Adds timestamp, change and patch set to changelog file
    in debian package
    Parameters
    ----------
    changelog_file : str
        Debian changelog file path
    ref : str
        Gerrit change reference
    package_name : str
        Debian package name
    Raises
    ----------
    build_node.build_node.build_node_errors.BuildError
        If gerrit reference can't be parsed or
        changes can't be added to changelog file
    """
    version_change = f'.{int(time.time())}'
    if ref:
        try:
            change, patch_set = parse_gerrit_ref(ref)
            version_change = '{0}.{1}.{2}'.format(version_change, change,
                                                  patch_set)
        except Exception as e:
            raise BuildError(
                'cannot parse gerrit reference {0!r}: {1}. '
                'Traceback:\n{2}'.format(
                    ref, str(e), traceback.format_exc()))
    try:
        with open(changelog_file, 'r+') as chg_f:
            lines = []
            first_match = True
            regex = re.compile(r'.*{0}\s+\(.*\)'.format(
                                        package_name))
            for line in chg_f:
                if regex.search(line) and first_match:
                    first_match = False
                    scope_find = re.search(r'\)', line).span()[0]
                    lines.append(line[:scope_find] +
                                 '{0}'.format(version_change) +
                                 line[scope_find:])
                else:
                    lines.append(line)
            chg_f.seek(0)
            chg_f.writelines(lines)
            chg_f.truncate()
    except Exception as e:
        raise BuildError(
            'cannot timestamp to changelog file: {0}. '
            'Traceback:\n{1}'.format(
                str(e), traceback.format_exc()))
