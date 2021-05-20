# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-09-30

"""
Command line tool for working with mock environments supervisor's data.
"""


import argparse
import datetime
import os
import struct
import sys

import lmdb


def init_args_parser():
    """
    Mock environments supervisor data management utility command line parser
    initialization.

    Returns
    -------
    argparse.ArgumentParser
    """
    parser = argparse.ArgumentParser(
        prog='mock_supervisor_ctl',
        description='Mock environments supervisor data management utility')
    subparsers = parser.add_subparsers(title='commands')
    list_help = 'list mock environments and their usage statistics'
    list_parser = subparsers.add_parser('list', description=list_help,
                                        help=list_help)
    list_parser.set_defaults(command='list')
    parser.add_argument('storage')
    return parser


def format_unix_time(unix_ts):
    """
    Converts the specified UNIX time to an ISO_8601 formatted string.

    Parameters
    ----------
    unix_ts : int
        Time in seconds since the epoch (unix time).

    Returns
    -------
    str
    """
    return datetime.datetime.fromtimestamp(unix_ts).\
        strftime('%Y-%m-%dT%H:%M:%S')


def list_environments(db):
    with db.begin() as txn:
        stats_db = db.open_db('stats', txn=txn)
        locks_cursor = txn.cursor(db=db.open_db('locks', txn=txn))
        for config_file, lock_data in locks_cursor.iternext():
            if lock_data:
                status = 'locked by {0} process "{1}" thread'.\
                    format(*struct.unpack('i20p', lock_data))
            else:
                status = 'free'
            stats_data = txn.get(bytes(config_file), db=stats_db)
            if stats_data:
                creation_ts, usage_ts, usages = struct.unpack(
                    'iii', stats_data)
                stats = 'created {0}, used {1}, {2} times in total'.\
                    format(format_unix_time(creation_ts),
                           format_unix_time(usage_ts), usages)
            else:
                stats = 'there is no statistics available'
            print('{0} is {1}, {2}'.format(config_file, status, stats))


def main(sys_args):
    args_parser = init_args_parser()
    args = args_parser.parse_args(args=sys_args)
    if not os.path.exists(os.path.join(args.storage, 'mock_supervisor.lmdb')):
        print('{0} is not a mock environments storage '
              'directory'.format(args.storage),
              file=sys.stderr)
        return 1
    db = lmdb.open(os.path.join(args.storage, 'mock_supervisor.lmdb'),
                   max_dbs=2)
    if args.command == 'list':
        list_environments(db)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
