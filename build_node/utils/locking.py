# -*- mode:python; coding:utf-8; -*-
# author: Vasiliy Kleschov <vkleschov@cloudlinux.com>
# created: 2017-11-15

"""
Build System functions to work with LMDB database
"""

import contextlib
import logging
import time

__all__ = ['generic_lock']


@contextlib.contextmanager
def generic_lock(db, db_name, key, value):
    """
    Implements generic locking with LMDB which can later be re-used

    Parameters
    ----------
    db      : lmdb.Environment
        LMDB database.
    db_name : str
        Database name
    key     : str
        Entry key.
    value   : str
        Entry value.

    Returns
    -------
    lmdb.Transaction

    """
    locks_db = db.open_db(db_name.encode('utf-8'))
    if isinstance(key, str):
        key = key.encode('utf-8')
    with db.begin(db=locks_db, write=True) as txn:
        i = 0
        locked = None
        try:
            locked = txn.put(key, value, overwrite=False)
            while not locked:
                if i >= 60:
                    logging.error('cannot acquire {0} lock after {1} retries'.
                                  format(key, i))
                    raise Exception('cannot acquire {0} lock'.format(key))
                i += 1
                logging.debug('acquiring {0} lock, try {1}'.format(key, i))
                time.sleep(1)
                locked = txn.put(key, value, overwrite=False)
            logging.debug('lock {0} acquired after {1} tries'.format(key, i))
            yield txn
        finally:
            if locked:
                logging.debug('releasing {0} lock'.format(key))
                txn.delete(key)
