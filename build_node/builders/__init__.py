# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-10-24

"""
Builder implementations for CloudLinux Build System.
"""

from build_node.builders.base_rpm_builder import BaseRPMBuilder
from build_node.builders.debian_builder import DebianBuilder

__all__ = ['get_suitable_builder']


def get_suitable_builder(task):
    """
    Returns an appropriate builder class for the specified task processing.

    Parameters
    ----------
    task : Task
        Build task.

    Returns
    -------
    class
        Builder class.
    """
    # TODO: detect other types of builders
    if task.platform.type == 'rpm':
        return BaseRPMBuilder
    elif task.platform.type == 'deb':
        return DebianBuilder
    else:
        raise ValueError(f'Unknown platform type: {task.platform.type}')
