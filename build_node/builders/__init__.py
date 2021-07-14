# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-10-24

"""
Builder implementations for CloudLinux Build System.
"""

from build_node.utils.debian_utils import detect_debian
from .base_rpm_builder import BaseRPMBuilder
from .kernel_builder import KernelBuilder
from .debian_builder import DebianBuilder, DebianARMHFBuilder

__all__ = ['get_suitable_builder']


def get_suitable_builder(task):
    """
    Returns an appropriate builder class for the specified task processing.

    Parameters
    ----------
    task : dict
        Build task.

    Returns
    -------
    class
        Builder class.
    """
    # TODO: detect other types of builders
    builder = task['build'].get('builder', {})
    if builder.get('class') == 'CL6LveKernelBuilder':
        return KernelBuilder
    elif (task['meta'].get('arch') == 'armhf'
          and task['meta'].get('platform', '').startswith('raspbian')):
        return DebianARMHFBuilder
    elif detect_debian(task['meta'].get('platform')):
        return DebianBuilder
    return BaseRPMBuilder
