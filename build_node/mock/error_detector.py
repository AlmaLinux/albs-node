# -*- mode:python; coding:utf-8; -*-
# author: Diego Marfil <dmarfil@cloudlinux.com>
#         Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-06-04

"""
Mock errors detection module.
"""

import collections
import os
import re

from build_node.ported import to_unicode

__all__ = ['detect_mock_error', 'MockErrorRecap', 'MOCK_ERR_SPEC_SECTION',
           'MOCK_ERR_UNMET_DEPENDENCY', 'MOCK_ERR_ARCH_EXCLUDED',
           'MOCK_ERR_BUILD_HANGUP', 'MOCK_ERR_NO_FREE_SPACE',
           'MOCK_ERR_MISSING_FILE', 'MOCK_ERR_CHANGELOG_ORDER',
           'MOCK_ERR_UNPACKAGED', 'MOCK_ERR_REPO', 'MOCK_ERR_TIMEOUT',
           'build_log_excluded_arch']


(
    # wrong exit status from an RPM file section
    MOCK_ERR_SPEC_SECTION,
    # unmet dependency (root log)
    MOCK_ERR_UNMET_DEPENDENCY,
    # excluded architecture
    MOCK_ERR_ARCH_EXCLUDED,
    # build hangup (mostly relevant to PHP)
    MOCK_ERR_BUILD_HANGUP,
    # insufficient space in download directory (root log)
    MOCK_ERR_NO_FREE_SPACE,
    # missing file error (build log)
    MOCK_ERR_MISSING_FILE,
    # wrong changelog records order (build log)
    MOCK_ERR_CHANGELOG_ORDER,
    # installed but unpackaged files found
    MOCK_ERR_UNPACKAGED,
    # repository metadata or network error (root log)
    MOCK_ERR_REPO,
    # build timeout error
    MOCK_ERR_TIMEOUT
) = range(10)


MockErrorRecap = collections.namedtuple('MockErrorRecap',
                                        ['error_code', 'error_text',
                                         'file_name', 'line_number'])


def check_error_pattern(regex, template, error_code, line):
    """
    Detects a error regular expression pattern in a log file line.

    Parameters
    ----------
    regex : str
        Regular expression pattern.
    template : str
        Error description.
    error_code : int
        Error code (see MOCK_ERR_* constants).
    line : str
        Log file line.

    Returns
    -------
    tuple or None
        Tuple of a error code and a error description generated from a template
        and data extracted from a log file line.
    """
    re_rslt = re.search(regex, to_unicode(line))
    if re_rslt:
        return error_code, template.format(*re_rslt.groups())


def build_log_changelog_order(line):
    """
    Detects an invalid changelog records order error in a mock build log.

    Parameters
    ----------
    line : str
        Mock build log line.

    Returns
    -------
    tuple or None
        Tuple of `MOCK_ERR_CHANGELOG_ORDER` and a human friendly error
        description or None if there is no error found.
    """
    regex = r'error:\s+(%changelog\s+not\s+in\s+.*?chronological\s+order)'
    template = '{0}'
    return check_error_pattern(regex, template, MOCK_ERR_CHANGELOG_ORDER, line)


def build_log_excluded_arch(line):
    """
    Detects an excluded architecture error in a mock build log.

    Parameters
    ----------
    line : str
        Mock build log line.

    Returns
    -------
    tuple or None
        Tuple of `MOCK_ERR_ARCH_EXCLUDED` and a human friendly error
        description or None if there is no error found.
    """
    regex = r'error:\s+Architecture\s+is\s+not\s+included:\s+(.*?)$'
    template = 'architecture "{0}" is excluded'
    error = check_error_pattern(regex, template, MOCK_ERR_ARCH_EXCLUDED, line)
    if not error:
        regex = r'error:\s+No\s+compatible\s+architectures\s+found'
        template = 'target architecture is not compatible'
        error = check_error_pattern(regex, template, MOCK_ERR_ARCH_EXCLUDED,
                                    line)
    return error


def build_log_hangup(line):
    """
    Detects a build process hangup in a mock build log.

    Parameters
    ----------
    line : str
        Mock build log line.

    Returns
    -------
    tuple or None
        Tuple of `MOCK_ERR_BUILD_HANGUP` and a human friendly error description
        or None if there is no error found.
    """
    regex = r'line\s+\d+:\s+\d+\s+(?i)hangup\s+.*?(?i)php'
    template = 'build is hanged-up (probably a build node was overloaded)'
    return check_error_pattern(regex, template, MOCK_ERR_BUILD_HANGUP, line)


def build_log_spec_section_failed(line):
    """
    Detects a spec file section execution error in a mock build log.

    Parameters
    ----------
    line : str
        Mock build log line.

    Returns
    -------
    tuple or None
        Tuple of `MOCK_ERR_SPEC_SECTION` and a human friendly error description
        or None if there is no error found.
    """
    regex = r'error:\s+Bad\s+exit\s+status\s+from\s+.*?\((%.*?)\)$'
    template = 'spec file "{0}" section failed'
    return check_error_pattern(regex, template, MOCK_ERR_SPEC_SECTION, line)


def build_log_timeout(line):
    """
    Detects a timeout error in a mock build log.

    Parameters
    ----------
    line : str
        Mock build log line.

    Returns
    -------
    tuple or None
        Tuple of `MOCK_ERR_TIMEOUT` and a human friendly error description
        or None if there is no error found.
    """
    regex = r'commandTimeoutExpired:\s+Timeout\((\d+)\)\s+expired'
    template = 'build timeout {0} second(s) expired'
    return check_error_pattern(regex, template, MOCK_ERR_TIMEOUT, line)


def build_log_missing_file(line):
    """
    Detects a missing file error in a mock build log.

    Parameters
    ----------
    line : str
        Mock build log line.

    Returns
    -------
    tuple or None
        Tuple of `MOCK_ERR_MISSING_FILE` and a human friendly error description
        or None if there is no error found.
    """
    regex = r'error:\s+File\s+(.*?):\s+No\s+such\s+file\s+or\s+directory'
    template = 'file "{0}" is not found'
    return check_error_pattern(regex, template, MOCK_ERR_MISSING_FILE, line)


def build_log_unpackaged(line):
    """
    Detects an unpackaged file(s) error in a mock build log.

    Parameters
    ----------
    line : str
        Mock build log line.

    Returns
    -------
    tuple or None
        Tuple of `MOCK_ERR_UNPACKAGED` and a human friendly error description
        or None if there is no error found.
    """
    regex = r'Installed\s+.*?but\s+unpackaged.*?file.*?\s+found'
    template = 'installed but unpackaged file(s) found'
    return check_error_pattern(regex, template, MOCK_ERR_UNPACKAGED, line)


def root_log_repository(line):
    """
    Detects a repository metadata or network error in a mock root log.

    Parameters
    ----------
    line : str
        Mock root log line.

    Returns
    -------
    tuple or None
        Tuple of `MOCK_ERR_REPO` and a human friendly error description
        or None if there is no error found.
    """
    regex = r'failure:.*?from\s+(.*?):\s+(.*?No more mirrors to try)'
    template = '"{0}" repository error: {1}'
    return check_error_pattern(regex, template, MOCK_ERR_REPO, line)


def root_log_no_space(line):
    """
    Detects an insufficient space error in a mock root log.

    Parameters
    ----------
    line : str
        Mock root log line.

    Returns
    -------
    tuple or None
        Tuple of `MOCK_ERR_NO_FREE_SPACE` and a human friendly error
        description or None if there is no error found.
    """
    regex = r'Error:\s+Insufficient\s+space\s+in\s+download\s+directory'
    template = 'insufficient space in download directory'
    return check_error_pattern(regex, template, MOCK_ERR_NO_FREE_SPACE, line)


def root_log_unmet_dependency(line):
    """
    Detects an unmet dependency error in a mock root log.

    Parameters
    ----------
    line : str
        Mock root log line.

    Returns
    -------
    tuple or None
        Tuple of `MOCK_ERR_MISSING_REQ` and a human friendly error description
        or None if there is no error found.
    """
    regex = r'Error:\s+No\s+Package\s+found\s+for\s+(.*?)$'
    template = 'unmet dependency "{0}"'
    return check_error_pattern(
        regex, template, MOCK_ERR_UNMET_DEPENDENCY, line)


def analyze_log_file(detectors, log_file):
    """
    Returns a human friendly error recap based on a mock log file analysis.

    Parameters
    ----------
    detectors : list
        List of error detection functions.
    log_file : str
        Mock log file path.

    Returns
    -------
    MockErrorRecap or None
        Human friendly error recap or None if no error was detected.
    """
    file_name = os.path.basename(log_file)
    with open(log_file, 'rb') as fd:
        for line_number, line in enumerate(fd, 1):
            for detector in detectors:
                result = detector(line)
                if result:
                    error_code, error_text = result
                    return MockErrorRecap(error_code, error_text, file_name,
                                          line_number)


def detect_mock_error(build_log, root_log):
    """
    Returns a human friendly error message based on mock logs analysis.

    Parameters
    ----------
    build_log : str
        Mock build log file path.
    root_log : str
        Mock root log file path.

    Returns
    -------
    MockErrorRecap or None
        Human friendly error recap or None if no error was detected.
    """
    build_log_detectors = [
        build_log_unpackaged,
        build_log_changelog_order,
        build_log_hangup,
        build_log_excluded_arch,
        build_log_missing_file,
        build_log_spec_section_failed,
        build_log_timeout
    ]
    root_log_detectors = [
        root_log_no_space,
        root_log_repository,
        root_log_unmet_dependency
    ]
    return analyze_log_file(build_log_detectors, build_log) or \
        analyze_log_file(root_log_detectors, root_log)
