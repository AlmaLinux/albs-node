# -*- mode:python; coding:utf-8; -*-
# author: Ruslan Pisarev <rpisarev@cloudlinux.com>
# created: 2021-02-27

"""CloudLinux Build System Web-server stats wrapper."""


import pymongo


__all__ = ['create_web_request_stats_index', 'web_request_stats_schema']


web_request_stats_schema = {
    '_id': {'type': 'objectid'},
    'endpoint_type': {'type': 'string', 'required': True},
    'request': {'type': 'string', 'required': True},
    'perf_time': {'type': 'float', 'required': True}
}


def create_web_request_stats_index(db):
    db['web_request_stats'].create_index([
        ('perf_time', pymongo.DESCENDING)
    ])
    db['web_request_stats'].create_index([
        ('ts', pymongo.DESCENDING)
    ], expireAfterSeconds=90 * 24 * 3600)
    db['web_request_stats'].create_index([
        ('endpoint_type', pymongo.DESCENDING)
    ])
