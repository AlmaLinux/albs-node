#!/usr/bin/env python3

import argparse
import logging
import os
import signal
import sys
import time
from threading import Event

import sentry_sdk

import build_node.build_node_globals as node_globals
from build_node.build_node_builder import BuildNodeBuilder
from build_node.build_node_config import BuildNodeConfig
from build_node.build_node_errors import BuildError, BuildExcluded
from build_node.build_node_supervisor import BuilderSupervisor
from build_node.mock.mock_environment import MockError
from build_node.utils.file_utils import chown_recursive, rm_sudo
from build_node.utils.config import locate_config_file
from build_node.utils.spec_parser import SpecParseError

running = True


def init_args_parser():
    """
    Build node command line arguments parser initialization.

    Returns
    -------
    argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser(
        prog='castor_build_node',
        description='CloudLinux Build System build node'
    )
    parser.add_argument('-c', '--config', help='configuration file path')
    parser.add_argument('-i', '--id', help='build node unique identifier')
    parser.add_argument('-m', '--master', help='build server connection URL')
    parser.add_argument('-t', '--threads', type=int,
                        help='build threads count')
    parser.add_argument('-w', '--working-dir', help='working directory path')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='enable additional debug output')
    return parser


def init_logger(verbose):
    """
    Initializes a program root logger.

    Parameters
    ----------
    verbose : bool
        Enable DEBUG messages output if True, print only INFO and higher
        otherwise.
    """
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler()
    handler.setLevel(level)
    log_format = "%(asctime)s %(levelname)-8s [%(threadName)s]: %(message)s"
    formatter = logging.Formatter(log_format, '%y.%m.%d %H:%M:%S')
    handler.setFormatter(formatter)
    logger = logging.getLogger()
    logger.addHandler(handler)
    logger.setLevel(level)


def init_working_dir(config):
    """
    The working directory initialization function. It removes files from
    previous executions and creates the necessary directories.

    Parameters
    ----------
    config : BuildNodeConfig
    """
    working_dir = config.working_dir
    if os.path.exists(working_dir):
        # delete builder directories from a previous execution
        chown_recursive(working_dir)
        for name in os.listdir(working_dir):
            if name.startswith('builder-'):
                # in some cases users have weird permissions on their
                # files / directories and even chown_recursive can't help us
                # to delete them from current user, so the use sudo.
                # e.g.:
                #   $ mkdir test
                #   $ touch test/test.txt
                #   $ chmod 444 test
                #   $ rm -rf test/
                #   rm: cannot remove 'test/test.txt': Permission denied
                rm_sudo(os.path.join(working_dir, name))
    else:
        logging.debug('creating the {0} working directory'.
                      format(config.working_dir))
        os.makedirs(config.working_dir, 0o750)
    if not os.path.exists(config.mock_configs_storage_dir):
        logging.debug('creating the {0} mock configuration files directory'.
                      format(config.mock_configs_storage_dir))
        os.makedirs(config.mock_configs_storage_dir, 0o750)


def init_sentry(config):
    """
    Initializes Sentry if dsn parameter is provided.

    Parameters
    ----------
    config : BuildNodeConfig
    """
    if not config.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=config.sentry_dsn,
        traces_sample_rate=config.sentry_traces_sample_rate,
        environment=config.sentry_environment,
        ignore_errors=[MockError, BuildError, BuildExcluded, SpecParseError],
    )


def main(sys_args):
    args_parser = init_args_parser()
    args = args_parser.parse_args(sys_args)
    try:
        config_file = locate_config_file('build_node', args.config)
        config = BuildNodeConfig(config_file, master_url=args.master,
                                 node_id=args.id, threads_count=args.threads)
    except ValueError as e:
        args_parser.error('Configuration error: {0}'.format(e))
        return 2
    init_logger(args.verbose)
    init_working_dir(config)
    init_sentry(config)

    node_terminated = Event()
    node_graceful_terminated = Event()

    def signal_handler(signum, frame):
        global running
        running = False
        logging.info('terminating build node: {0} received'.format(signum))
        node_terminated.set()
        node_graceful_terminated.set()

    def sigusr_handler(signum, frame):
        global running
        running = False
        logging.info('terminating build node: {0} received'.format(signum))
        node_terminated.set()
        node_graceful_terminated.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGUSR1, sigusr_handler)

    node_globals.init_supervisors(config)
    builders = []
    for i in range(0, config.threads_count):
        builder = BuildNodeBuilder(config, i, node_terminated,
                                   node_graceful_terminated)
        builders.append(builder)
        builder.start()

    builder_supervisor = BuilderSupervisor(config, builders, node_terminated)
    builder_supervisor.start()

    global running
    exit_code = 0
    while running:
        time.sleep(10)
        threads = builders + [builder_supervisor]
        if all([t.is_alive() for t in threads]):
            continue
        if (all([not b.is_alive() for b in builders])
                or not builder_supervisor.is_alive()):
            logging.error('All builders are dead, exiting')
            running = False
            exit_code = 1
            for b in builders:
                b.join(0.1)
            builder_supervisor.join(1.)
    return exit_code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
