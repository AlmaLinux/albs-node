# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-11-03

"""
CloudLinux Build System functions for working with RPM files.
"""

import os
import stat
import subprocess
import re

import lxml.etree
import rpm
import plumbum

from build_node.errors import CommandExecutionError
from build_node.ported import unique, to_unicode


__all__ = ['srpm_cpio_sha256sum', 'unpack_src_rpm', 'compare_rpm_packages',
           'string_to_version', 'flag_to_string', 'compare_evr', 'is_pre_req',
           'get_rpm_property', 'init_metadata', 'get_files_from_package',
           'split_filename', 'is_rpm_file', 'evr_to_string', 'evrtofloat',
           'to_str_fixing_len', 'split_segments', 'int_to', 'char_to',
           'get_rpm_metadata']


def get_rpm_metadata(rpm_path: str):
    """
    Returns RPM metadata.

    Parameters
    ----------
    rpm_path : str
        RPM path.

    Returns
    -------
    dict
        RPM metadata.
    """
    ts = rpm.TransactionSet()
    with open(rpm_path, 'rb') as rpm_pkg:
        hdr = ts.hdrFromFdno(rpm_pkg)
    return hdr


def srpm_cpio_sha256sum(srpm_path):
    """
    Returns SHA256 of src-RPM cpio archive.

    Parameters
    ----------
    srpm_path : str
        Src-RPM path.

    Returns
    -------
    str
        SHA256 checksum of the src-RPM cpio archive.

    Raises
    ------
    build_node.errors.CommandExecutionError
        If a checksum calculation command failed.

    Notes
    -----
    There is a plumbum library bug which causes a Unix sockets leakage on
    pipelines containing 3 or more commands (see AL-3388) so we had to use
    subprocess.Popen here.
    """
    cmd = 'rpm2cpio {0} | cpio -i --to-stdout --quiet | sha256sum'.\
        format(srpm_path)
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    out, err = proc.communicate()
    if proc.returncode != 0:
        message = 'Can not calculate checksum: {0}'.format(err)
        raise CommandExecutionError(message, proc.returncode, out, err, cmd)
    return out.split()[0]


def unpack_src_rpm(srpm_path, target_dir):
    """
    Unpacks an src-RPM to the target directory.

    Parameters
    ----------
    srpm_path : str
        Src-RPM path.
    target_dir : str
        Target directory path.

    Raises
    ------
    build_node.errors.CommandExecutionError
        If an unpacking command failed.
    """
    pipe = (plumbum.local["rpm2cpio"][srpm_path] |
            plumbum.local["cpio"]["-idmv", "--no-absolute-filenames"])
    proc = pipe.popen(cwd=target_dir,
                      env={'HISTFILE': '/dev/null', 'LANG': 'C'})
    out, err = proc.communicate()
    if proc.returncode != 0:
        msg = 'Can not unpack src-RPM: {0}'.format(err)
        if not os.path.exists(srpm_path) or not os.path.getsize(srpm_path):
            msg = 'src-RPM file is missing or empty.\n{}'.format(msg)
        raise CommandExecutionError(msg, proc.returncode,
                                    out, err, pipe.formulate())


def compare_rpm_packages(package_a, package_b):
    """
    Compares package versions.

    Parameters
    ----------
    package_a : dict
        First package to compare.
    package_b : dict
        Second package to compare.

    Returns
    -------
    int
        Positive integer if the first version is greater,
        a negative integer if the second version is greater
        and 0 if both versions are equal.
    """
    return rpm.labelCompare(
        (package_a['epoch'], package_a['version'], package_a['release']),
        (package_b['epoch'], package_b['version'], package_b['release'])
    )


def string_to_version(verstring):
    """
    Returns parsed version of rpm by string
    Parameters
    ----------
    verstring : str
        string version e.g. `1:4.3-1.el7.rpm`

    Returns
    -------
    tuple
        tuple of parsed version of rpm
    """
    if verstring in [None, '', b'']:
        return None, None, None
    if isinstance(verstring, bytes):
        verstring = verstring.decode('utf-8')
    i = verstring.find(':')
    if i != -1:
        try:
            epoch = str(int(verstring[:i]))
        except ValueError:
            # look, garbage in the epoch field, how fun, kill it
            epoch = '0'  # this is our fallback, deal
    else:
        epoch = '0'
    j = verstring.find('-')
    if j != -1:
        if verstring[i + 1:j] == '':
            version = None
        else:
            version = verstring[i + 1:j]
        release = verstring[j + 1:]
    else:
        if verstring[i + 1:] == '':
            version = None
        else:
            version = verstring[i + 1:]
        release = None
    return epoch, version, release


def flag_to_string(flags):
    """
    Parameters
    ----------
    flags : int
        some comparison flags from rpm/yum

    Returns
    -------
    None or str or int
        If we can interpret output number we did it,
        otherwise return truncated arg
    """
    flags = flags & 0xf
    res = {0: None, 2: 'LT', 4: 'GT',
           8: 'EQ', 10: 'LE', 12: 'GE'}

    if flags in res:
        return res[flags]
    return flags


def compare_evr(evr1, evr2):
    # return 1: a is newer than b
    # 0: a and b are the same version
    # -1: b is newer than a
    e1, v1, r1 = evr1
    e2, v2, r2 = evr2
    if e1 is None:
        e1 = '0'
    else:
        e1 = str(e1)
    v1 = str(v1)
    r1 = str(r1)
    if e2 is None:
        e2 = '0'
    else:
        e2 = str(e2)
    v2 = str(v2)
    r2 = str(r2)
    rc = rpm.labelCompare((e1, v1, r1), (e2, v2, r2))
    return rc


def is_pre_req(flag):
    """
    Parameters
    ----------
    flag : int
        Bits of RPM flag
    Returns
    -------
    int
        returns 1 when some bits are up and 0 otherwise

    """
    if flag is not None:
        # Note: RPMSENSE_PREREQ == 0 since rpm-4.4'ish
        if flag & (rpm.RPMSENSE_PREREQ |
                   rpm.RPMSENSE_SCRIPT_PRE |
                   rpm.RPMSENSE_SCRIPT_POST):
            return 1
    return 0


def get_rpm_property(hdr, rpm_property):
    """
    Returns property with pre-require bit
    Parameters
    ----------
    hdr : rpm.hdr
        Header of RPM package
    rpm_property : str
        obsoletes, provides, conflicts or requires

    Returns
    -------
    list
        List of property with pre-require bit
    """
    rpm_properties = {'obsoletes': {'name': rpm.RPMTAG_OBSOLETENAME,
                                    'flags': rpm.RPMTAG_OBSOLETEFLAGS,
                                    'evr': rpm.RPMTAG_OBSOLETEVERSION},
                      'provides': {'name': rpm.RPMTAG_PROVIDENAME,
                                   'flags': rpm.RPMTAG_PROVIDEFLAGS,
                                   'evr': rpm.RPMTAG_PROVIDEVERSION},
                      'conflicts': {'name': rpm.RPMTAG_CONFLICTNAME,
                                    'flags': rpm.RPMTAG_CONFLICTFLAGS,
                                    'evr': rpm.RPMTAG_CONFLICTVERSION},
                      'requires': {'name': rpm.RPMTAG_REQUIRENAME,
                                   'flags': rpm.RPMTAG_REQUIREFLAGS,
                                   'evr': rpm.RPMTAG_REQUIREVERSION}}
    if rpm_property not in rpm_properties:
        rpm_property = 'requires'
    prop = rpm_properties[rpm_property]
    name = hdr[prop['name']]
    lst = hdr[prop['flags']]
    flag = list(map(flag_to_string, lst))
    pre = list(map(is_pre_req, lst))
    lstvr = hdr[prop['evr']]
    vers = list(map(string_to_version, lstvr))
    if name is not None:
        lst = list(zip(name, flag, vers, pre))
    return unique(lst)


def init_metadata(rpm_file):
    """
    Parameters
    ----------
    rpm_file : str
        Path to the RPM package

    Returns
    -------
    tuple
        Returns initial metadata of package and header of RPM

    """
    res = ''
    ts = rpm.TransactionSet('', rpm._RPMVSF_NOSIGNATURES)
    with open(rpm_file, 'rb') as fd:
        hdr = ts.hdrFromFdno(fd)
        chglogs = zip(hdr[rpm.RPMTAG_CHANGELOGNAME],
                      hdr[rpm.RPMTAG_CHANGELOGTIME],
                      hdr[rpm.RPMTAG_CHANGELOGTEXT])
        for nm, tm, tx in reversed(list(chglogs)):
            c = lxml.etree.Element('changelog', author=to_unicode(nm),
                                   date=to_unicode(tm))
            c.text = to_unicode(tx)
            res += to_unicode(lxml.etree.tostring(c, pretty_print=True))
        meta = {
            'changelog_xml': res,
            'files': [], 'obsoletes': [], 'provides': [],
            'conflicts': [], 'requires': [],
            'vendor': to_unicode(hdr[rpm.RPMTAG_VENDOR]),
            'buildhost': to_unicode(hdr[rpm.RPMTAG_BUILDHOST]),
            'filetime': int(hdr[rpm.RPMTAG_BUILDTIME]),
        }
        # If package size too large (more than 32bit integer)
        # This fields will became None
        for key, rpm_key in (('archivesize', rpm.RPMTAG_ARCHIVESIZE),
                             ('packagesize', rpm.RPMTAG_SIZE)):
            value = hdr[rpm_key]
            if value is not None:
                value = int(value)
            meta[key] = value
        return meta, hdr


def get_files_from_package(hdr):
    """
    Parameters
    ----------
    hdr : rpm.hdr
        Header of RPM package

    Returns
    -------
    dict
        Structure of files of the package by categories
    """
    mode_cache = {}
    files = hdr[rpm.RPMTAG_BASENAMES]
    fileflags = hdr[rpm.RPMTAG_FILEFLAGS]
    filemodes = hdr[rpm.RPMTAG_FILEMODES]
    filetuple = list(zip(files, filemodes, fileflags))
    res_files = {}
    for (fn, mode, flag) in filetuple:
        # garbage checks
        if mode is None or mode == '':
            if 'file' not in res_files:
                res_files['file'] = []
            res_files['file'].append(to_unicode(fn))
            continue
        if mode not in mode_cache:
            mode_cache[mode] = stat.S_ISDIR(mode)
        fkey = 'file'
        if mode_cache[mode]:
            fkey = 'dir'
        elif flag is not None and (flag & 64):
            fkey = 'ghost'
        res_files.setdefault(fkey, []).append(to_unicode(fn))
    return res_files


def split_filename(filename):
    """
    Pass in a standard style rpm fullname

    Return a name, version, release, epoch, arch, e.g.::
        foo-1.0-1.i386.rpm returns foo, 1.0, 1, i386
        1:bar-9-123a.ia64.rpm returns bar, 9, 123a, 1, ia64
    """

    if filename[-4:] == '.rpm':
        filename = filename[:-4]

    arch_index = filename.rfind('.')
    arch = filename[arch_index+1:]

    rel_index = filename[:arch_index].rfind('-')
    rel = filename[rel_index+1:arch_index]

    ver_index = filename[:rel_index].rfind('-')
    ver = filename[ver_index+1:rel_index]

    epoch_index = filename.find(':')
    if epoch_index == -1:
        epoch = ''
    else:
        epoch = filename[:epoch_index]

    name = filename[epoch_index + 1:ver_index]
    return name, ver, rel, epoch, arch


def is_rpm_file(f_name, check_magic=False):
    """
    Checks if file is RPM package.


    Parameters
    ----------
    f_name : str or unicode
        File name to be checked.
    check_magic : bool
        If True use first 4 bytes of file to detect RPM package,
        use only extension checking otherwise.

    Return
    ----------
    bool
        True if file is RPM package, False otherwise.
    """
    ext_rslt = re.search(r'.*?\.rpm$', f_name, re.IGNORECASE)
    if check_magic:
        f = open(f_name, 'rb')
        bs = f.read(4)
        f.close()
        return bs == b'\xed\xab\xee\xdb' and ext_rslt
    return bool(ext_rslt)


def evr_to_string(evr):
    """
    Converts epoch, version and release of package to unique string.

    Parameters
    ----------
    evr : list or tuple or str or unicode
        List from epoch, version and release

    Return
    ----------
    String
        str for given list
    """
    ret = ''
    if not isinstance(evr, (list, tuple)):
        evr = [evr]
    for i in evr:
        ret += to_str_fixing_len(evrtofloat(split_segments(i)))
    return ret


def to_str_fixing_len(dc):
    """
    Convert Decimal to String with fix length.

    Parameters
    ----------
    dc : str or unicode
        Real-number to str with fix len for compare

    Return
    ----------
    str
        str with separation
    """
    return dc + "00"


def evrtofloat(rpm_data):
    """
    Encode List of Version or Epoch or Release in real-numbers segment.
    See http://en.wikipedia.org/wiki/Arithmetic_coding.

    Parameters
    ----------
    rpm_data : list of (string or str or int or long)
        list to convert in double

    Return
    ----------
    str
        Converted string
    """
    evr = []
    for elem in rpm_data:
        if isinstance(elem, int):
            evr.extend(int_to(elem))
        elif isinstance(elem, str):
            try:
                evr.extend(int_to(int(elem)))
            except ValueError:
                for ch in elem:
                    evr.extend(char_to(ch))
        else:
            raise NameError('ThisStrange: ' + elem)
        evr.extend(char_to(chr(0)))
    return "".join(["%02x" % n for n in evr])


def split_segments(s):
    """
    Split str of epoch or version or release to numbers and strings.

    Parameters
    ----------
    s : str
        str of epoch or version or release

    Return
    ----------
    list
        List strings and numbers from EVR
    """
    if not isinstance(s, str):
        return []
    buff = ''
    segs = []
    ALPHA = 0
    DIGIT = 1
    typesym = ALPHA
    for c in s:
        if c.isdigit():
            if typesym == DIGIT or buff == '':
                buff += c
            else:
                if typesym == ALPHA:
                    segs += [buff]
                    buff = c
            typesym = DIGIT
        elif c.isalpha():
            if typesym == ALPHA or buff == '':
                buff += c
            else:
                segs += [int(buff)]
                buff = c
            typesym = ALPHA
        else:
            if buff != '' and typesym == DIGIT:
                segs += [int(buff)]
            else:
                if buff != '' and typesym == ALPHA:
                    segs += [buff]
            buff = ''
            typesym = None
    if buff != '' and typesym == DIGIT:
        segs += [int(buff)]
    else:
        if buff != '' and typesym == ALPHA:
            segs += [buff]
    return segs


def int_to(intgr):
    """
    Encode int in real-numbers segment.
    See http://en.wikipedia.org/wiki/Arithmetic_coding.

    Parameters
    ----------
    intgr : int or long
       int for coding in Float an segment [seg_begin, seg_end]

    Return
    ----------
    tuple (Decimal, Decimal)
        list encoding segment
    """
    lst = []
    number = int(intgr)
    while number > 0:
        number, ost = divmod(number, 256)
        lst.append(ost)
    lst.append(128 + len(lst))
    lst.reverse()
    return lst


def char_to(ch):
    """
    Encode char in real-numbers segment.
    See http://en.wikipedia.org/wiki/Arithmetic_coding.

    Parameters
    ----------
    ch : char
        Char for coding in Float an segment [seg_begin, seg_end]

    Return
    ----------
    list
        list encoding segment
    """
    return [ord(ch)]
