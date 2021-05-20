# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-10-24

"""
Common error classes for CloudLinux Build System build node modules.
"""


class BuildError(Exception):

    """Base class for all kind of build errors."""

    pass


class BuildConfigurationError(BuildError):

    pass


class BuildExcluded(Exception):

    """Indicates that a build was excluded."""

    pass
