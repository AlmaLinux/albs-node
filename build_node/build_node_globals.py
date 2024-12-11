"""AlmaLinux Build System node global variables."""

from albs_build_lib.builder.mock.supervisor import MockSupervisor

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
    MOCK_SUPERVISOR = MockSupervisor(
        storage_dir=config.mock_configs_storage_dir,
        host_arch=config.base_arch,
    )
