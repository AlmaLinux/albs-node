# -*- mode:python; coding:utf-8; -*-
# author: Darya Malyavkina <dmalyavkina@cloudlinux.com>
# created: 2018-01-17

"""
Build System functions for working with spec files.
"""

from io import StringIO
import datetime
import re
import os
import traceback
import time

from build_node.build_node_errors import BuildError
from ..errors import DataNotFoundError


__all__ = ['add_gerrit_ref_to_spec', 'bump_release', 'bump_release_spec_file',
           'bump_version_datestamp_spec_file', 'add_changelog_spec_file',
           'find_spec_file', 'get_raw_spec_data', 'wipe_rpm_macro',
           'parse_evr']


def find_spec_file(package_name, sources_dir, spec_file=None):
    """
    Finds a package's spec file in the sources directory.

    Parameters
    ----------
    package_name : str
        Package name.
    sources_dir : str
        Sources directory path.
    spec_file : str, optional
        Spec file path.

    Returns
    -------
    str or None
        Spec file path or None if there is no spec file found.

    Raises
    ------
    build_node.errors.DataNotFoundError
        If specified spec file is not found.
    """
    if spec_file:
        spec = os.path.join(sources_dir, spec_file)
        if os.path.exists(spec):
            return spec
        raise DataNotFoundError(spec_file)
    spec = None
    specs = [s for s in os.listdir(sources_dir) if s.endswith(".spec")]
    for f in specs:
        if '{}.spec'.format(package_name) in f:
            return os.path.join(sources_dir, f)
    for f in specs:
        spec = os.path.join(sources_dir, f)
        if f.startswith(package_name):
            return spec
    return spec


def bump_release(release, reset_to=None):
    """
    Increments the last number of the release by one.

    Parameters
    ----------
    release : str
        Release.
    reset_to : str, optional
        Set the last number of the release to this value if specified.

    Returns
    -------
    str
        Bumped release.

    Raises
    ------
    ValueError
        If the release can not be bumped.
    """
    segments = re.split(r'(%{\??\w{3,}}|%\??\w{3,}|el\d+_?\w*|\.|[a-z]+)',
                        release, flags=re.IGNORECASE)
    segment_pos = None
    for i, segment in enumerate(segments):
        if re.search(r'^\d+$', segment):
            segment_pos = i
    if segment_pos is None:
        raise ValueError('invalid release value {0}'.format(release))
    if reset_to:
        release_number = reset_to
    else:
        release_number = str(int(segments[segment_pos]) + 1)
    segments[segment_pos] = release_number
    return str(''.join(segments))


def bump_release_spec_file(spec_file):
    """
    Bump release in spec file.

    Parameters
    ----------
    spec_file : str or unicode
        Path to spec file
    """
    with open(spec_file, 'r') as fd:
        spec = StringIO(fd.read())
    with open(spec_file, 'w') as fd:
        for line in spec:
            re_rslt = re.search(r'^Release:(\s+)(\S+)(?:\n|$)', line)
            if re_rslt:
                release = bump_release(re_rslt.group(2))
                fd.write('Release:{0}{1}\n'.format(re_rslt.group(1), release))
            else:
                fd.write(line)


def bump_version_datestamp_spec_file(spec_file):
    """
    Sets a version to a current datestamp in a spec file. Increases a release
    value if version is bumped already.

    Parameters
    ----------
    spec_file : str or unicode
        Path to the spec file.

    Raises
    ------
    build_node.errors.DataNotFoundError
        If there is no Version or Release in the spec file.
    """
    fields = ['Version', 'Release']
    spec_data = get_raw_spec_data(spec_file, fields)
    for field in fields:
        if not spec_data.get(field):
            raise DataNotFoundError('there is no {0} field in the {1} spec '
                                    'file'.format(field, spec_file))
    today_version = datetime.date.today().strftime('%Y%m%d')
    if spec_data['Version'] == today_version:
        spec_data['Release'] = bump_release(spec_data['Release'])
    else:
        spec_data['Version'] = today_version
        spec_data['Release'] = bump_release(spec_data['Release'], reset_to='1')
    with open(spec_file, 'r') as fd:
        spec = StringIO(fd.read())
    with open(spec_file, 'w') as fd:
        for line in spec:
            re_rslt = re.search(r'^(Version|Release):(\s+)(\S+)(?:\n|$)', line)
            if re_rslt:
                field = re_rslt.group(1)
                fd.write('{0}:{1}{2}\n'.format(field, re_rslt.group(2),
                                               spec_data[field]))
            else:
                fd.write(line)
    return spec_data


def add_changelog_spec_file(spec_file, changelog):
    """
    Adds a changelog record to the spec file.

    Parameters
    ----------
    spec_file : str
        Path to the spec file.
    changelog : str
        Changelog record text.
    """
    with open(spec_file, 'r') as fd:
        spec = StringIO(fd.read())
    with open(spec_file, 'w') as fd:
        for line in spec:
            if re.search(r'%changelog', line):
                fd.write(line)
                fd.write(changelog)
                if not re.search(r'\n$', changelog):
                    fd.write('\n')
                fd.write('\n')
            else:
                fd.write(line)


def add_gerrit_ref_to_spec(spec_file, ref=None):
    """

    Parameters
    ----------
    spec_file   : str
        Spec file path
    ref         : str
        Gerrit change reference

    Returns
    -------

    """
    new_release_str = 'Release: {release}.{timestamp}'
    if ref:
        try:
            _, _, _, change, patch_set = ref.split("/")
            new_release_str = '{0}.{1}.{2}'.format(new_release_str, change,
                                                   patch_set)
        except Exception as e:
            raise BuildError('cannot parse gerrit reference {0!r}: {1}. '
                             'Traceback:\n{2}'.format(ref, str(e),
                                                      traceback.format_exc()))
    try:
        with open(spec_file, 'r+') as fd:
            lines = []
            for line in fd:
                re_rslt = re.search(r'^Release:\s*([^\s#]+)', line,
                                    re.IGNORECASE)
                if re_rslt:
                    new_release_str = new_release_str.format(
                        release=re_rslt.group(1), timestamp=int(time.time()))
                    lines.append('{0}\n'.format(new_release_str))
                else:
                    lines.append(line)
            fd.seek(0)
            fd.writelines(lines)
            fd.truncate()
    except Exception as e:
        raise BuildError('cannot add timestamp to spec file: {0}. '
                         'Traceback:\n{1}'.format(str(e),
                                                  traceback.format_exc()))


def get_raw_spec_data(spec_file, fields):
    """
    Returns raw (without macro expansion) spec file data.

    Parameters
    ----------
    spec_file : str
        Spec file path.
    fields : list of str
        List of spec file fields to extract.

    Returns
    -------
    dict
        Dictionary with the extracted data.

    Examples
    --------
    >>> get_raw_spec_data('/tmp/package.spec', \
                          ['Version', 'Release']) #doctest: +SKIP
    {'Version': '1.2', 'Release': '1%{?dist}'}
    """
    data = {}
    regex = re.compile(r'^({0}):(?:\s+)(\S+)(?:\n|$)'.format('|'.join(fields)))
    with open(spec_file, 'r') as fd:
        for line in fd:
            re_rslt = regex.search(line)
            if re_rslt:
                key = re_rslt.group(1)
                if key not in data:
                    data[key] = re_rslt.group(2)
    return data


def wipe_rpm_macro(string):
    """
    Removes RPM macro definitions from the specified string. It also removes
    trailing dot symbol since it doesn't make sense after macros removal.

    Parameters
    ----------
    string : str
        String to process.

    Returns
    -------
    str
        String without RPM macros.

    Examples
    --------
    >>> wipe_rpm_macro('1%{?dist}')
    '1'
    >>> wipe_rpm_macro('12.%{?dist}')
    '12'
    """
    return re.sub(r'\.+$', '',
                  re.sub(r'(%{[?|!]*\w{3,}[^}]*}|%[?|!]*\w{3,})', '', string))


def parse_evr(evr):
    """
    Extracts epoch, version and release from an RPM version string in the
    "epoch:version-release" format. The epoch part is optional.

    Parameters
    ----------
    evr : str
        RPM version string.

    Returns
    -------
    tuple
        Tuple of three elements: epoch, version and release. The epoch part
        will be None if it wasn't present in the `evr` string.

    Raises
    ------
    ValueError
        If the `evr` format is not valid.
    """
    re_rslt = re.search(r'(?:(\d+):|)([\w.]+?)-(\S*)$', evr)
    if not re_rslt:
        raise ValueError('invalid evr string format')
    return re_rslt.groups()
