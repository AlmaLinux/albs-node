# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2019-04-19

"""CloudLinux Build System node global variables."""

from build_node.mock.supervisor import MockSupervisor
from build_node.pbuilder.pbuilder_environment import PbuilderSupervisor

__all__ = ['init_supervisors', 'PBUILDER_SUPERVISOR', 'MOCK_SUPERVISOR']


PBUILDER_SUPERVISOR = None
"""Pbuilder environments supervisor."""

MOCK_SUPERVISOR = None
"""Mock environments supervisor."""


def init_supervisors(config):
    """
    Initializes mock and pbuilder environment global supervisor objects.

    Parameters
    ----------
    config : build_node.build_node_config.BuildNodeConfig
        Build node configuration file.
    """
    global MOCK_SUPERVISOR, PBUILDER_SUPERVISOR
    MOCK_SUPERVISOR = MockSupervisor(config.mock_configs_storage_dir,
                                     config.database_dir)
    PBUILDER_SUPERVISOR = PbuilderSupervisor(
        config.pbuilder_configs_storage_dir, config.database_dir)
