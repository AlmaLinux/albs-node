# Agent instructions

## Build node names

- `build_node_name` of every build node must be unique. The web server keeps
  one registry row per name (`build_nodes` table). Nodes sharing a name are
  merged into one row and overwrite each other's capacity, arches, version and
  host resources on every ping (seen on a test stand: capacity flapped 2↔1 and
  the node with fewer slots got no tasks for 7 minutes), so task balancing
  counts slots wrongly.
- The default name is the hostname without its last two domain labels
  (`BaseConfig.get_node_name` in `build_node/utils/config.py`, used as the
  default in `BuildNodeConfig` in `build_node/build_node_config.py`), so hosts
  that differ only by domain collide. Set `build_node_name` explicitly in the
  node config when deploying.
- Two processes on the same host with the same `working_dir` are not
  supported: the second one reads `<working_dir>/.build_node_instance_id` and
  the server takes it for a restart of the first.
