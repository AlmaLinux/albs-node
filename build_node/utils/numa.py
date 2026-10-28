"""
NUMA worker placement policy for the build node.
"""


import logging

from albs_build_lib.builder.numa import numa_nodes


def build_numa_assignments(threads_count, numa_aware):
    """
    Computes per-thread NUMA CPU assignments for the build node workers.

    Workers are distributed round-robin across the NUMA nodes that have CPUs
    assigned to them. Each worker is given the full CPU set of its node so
    that mock and its child processes can use every core on that node while
    still staying confined to it.

    Parameters
    ----------
    threads_count : int
        Number of build threads the node will spawn.
    numa_aware : bool
        Whether NUMA-aware placement is enabled in the configuration.

    Returns
    -------
    list of tuple of (int or None, list of int or None)
        One entry per build thread. ``(None, None)`` means "do not pin";
        otherwise the tuple is ``(node_id, cpus)`` where ``cpus`` is the CPU
        set the corresponding thread must be confined to and ``node_id`` is
        the NUMA node identifier used for cross-node load balancing.
    """
    if not numa_aware:
        return [(None, None)] * threads_count
    nodes = numa_nodes()
    if len(nodes) < 2:
        return [(None, None)] * threads_count
    node_ids = list(nodes)
    assignments = []
    for thread_num in range(threads_count):
        node_id = node_ids[thread_num % len(node_ids)]
        assignments.append((node_id, list(nodes[node_id])))
    logging.info(
        'NUMA-aware placement enabled: %d threads distributed across '
        'nodes %s',
        threads_count,
        node_ids,
    )
    return assignments
