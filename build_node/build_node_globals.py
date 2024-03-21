# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2019-04-19

"""CloudLinux Build System node global variables."""

from build_node.mock.supervisor import MockSupervisor

__all__ = ['init_supervisors', 'MOCK_SUPERVISOR']


MOCK_SUPERVISOR = None
"""Mock environments supervisor."""


def init_supervisors(config):
    """
    Initializes mock environment global supervisor objects.

    Parameters
    ----------
    config : build_node.build_node_config.BuildNodeConfig
        Build node configuration file.
    """
    global MOCK_SUPERVISOR
    MOCK_SUPERVISOR = MockSupervisor(config.mock_configs_storage_dir, config.base_arch)
