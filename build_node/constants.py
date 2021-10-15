# -*- mode:python; coding:utf-8; -*-
# author: Vyacheslav Potoropin <vpotoropin@cloudlinux.com>
# created: 2021-05-20

__all__ = [
    'TOTAL_RETRIES',
    'STATUSES_TO_RETRY',
    'METHODS_TO_RETRY',
    'BACKOFF_FACTOR',
]

TOTAL_RETRIES = 5
STATUSES_TO_RETRY = (413, 429, 502, 503, 504)
METHODS_TO_RETRY = ('GET', 'POST')
# {BACKOFF_FACTOR} * (2 ** ({TOTAL_RETRIES} - 1))
# timeout for every retry = 1, 2, 4, 8, 16 sec.
BACKOFF_FACTOR = 2
