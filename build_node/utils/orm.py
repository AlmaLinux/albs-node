# -*- mode:python; coding:utf-8; -*-
# author: Ruslan Pisarev  <rpisarev@cloudlinux.com>
# created: 16.01.19  00:50


import traceback
from time import sleep

from pymongo.errors import PyMongoError

from build_node.utils.log import log_put


__all__ = ['update_atomic', 'find_one_record', 'remove_record',
           'collection_count', 'update_atomic_infinity',
           'find_one_record_infinity', 'remove_record_infinity',
           'collection_count_infinity']


def inf_waiting(fn):
    """

    Parameters
    ----------
    fn : function
        function which will be decorated
    Returns
    -------
    function
        decorated function

    """
    def infinity_waiter(*args, **kwargs):
        logs_queue = kwargs.get('logs_queue')
        if logs_queue is not None:
            kwargs.pop('logs_queue')
        trying = 1
        while True:
            trying = 2*trying if trying < 500 else trying
            try:
                return fn(*args, **kwargs)
            except PyMongoError:
                log = u'Database error:\n{}\nNext trying'.format(
                    traceback.format_exc())
                if logs_queue:
                    log_put(logs_queue, log, 'debug')
                sleep(5*trying)
    return infinity_waiter


def update_atomic(db, collection_name, query, update, upsert=False,
                  new=True, multi=False):
    """
    Parameters
    ----------
    db: pymongo.database.Database
            Build System MongoDB database
    collection_name : str
        Name of DB collection
    query : dict
        Query to update collection `collection_name`
    update : dict
        Update dict to set and unset some data
    upsert : bool
        Should it upsert? Optional, Fase by default
    new : bool
        Should it return new (modified) document or not?
        Optional, True by default
    multi : bool
        Should it change more than one documents? Optional
        By default False

    Returns
    -------
    dict
        Modified or original (see `new` parameter) document

    """
    return db[collection_name].find_and_modify(query=query,
                                               update=update,
                                               upsert=upsert,
                                               new=new,
                                               multi=multi)


def find_one_record(db, collection_name, query):
    """

    Parameters
    ----------
    db: pymongo.database.Database
            Build System MongoDB database
    collection_name : str
        Name of DB collection
    query : dict
        Query to update collection `collection_name`

    Returns
    -------

    """
    return db[collection_name].find_one(query)


def remove_record(db, collection_name, query):
    """

    Parameters
    ----------
    db: pymongo.database.Database
            Build System MongoDB database
    collection_name : str
        Name of DB collection
    query : dict
        Query to update collection `collection_name`
    """
    db[collection_name].remove(query)


def collection_count(db, collection_name):
    """

    Parameters
    ----------
    db: pymongo.database.Database
            Build System MongoDB database
    collection_name : str
        Name of DB collection
    """
    return db[collection_name].find().count()


@inf_waiting
def update_atomic_infinity(db, collection_name, query, update,
                           upsert=False, new=True, multi=False):
    """
    This function will try perform action until success
    Parameters
    ----------
    db: pymongo.database.Database
            Build System MongoDB database
    collection_name : str
        Name of DB collection
    query : dict
        Query to update collection `collection_name`
    update : dict
        Update dict to set and unset some data
    upsert : bool
        Should it upsert? Optional, Fase by default
    new : bool
        Should it return new (modified) document or not?
        Optional, True by default
    multi : bool
        Should it change more than one documents? Optional
        By default False

    Returns
    -------
    dict
        Modified or original (see `new` parameter) document
    """
    return db[collection_name].find_and_modify(query=query,
                                               update=update,
                                               upsert=upsert,
                                               new=new,
                                               multi=multi)


@inf_waiting
def find_one_record_infinity(db, collection_name, query):
    """
    This function will try perform action until success
    Parameters
    ----------
    db: pymongo.database.Database
            Build System MongoDB database
    collection_name : str
        Name of DB collection
    query : dict
        Query to update collection `collection_name`

    Returns
    -------
    """
    return db[collection_name].find_one(query)


@inf_waiting
def remove_record_infinity(db, collection_name, query):
    """
    This function will try perform action until success
    Parameters
    ----------
    db: pymongo.database.Database
            Build System MongoDB database
    collection_name : str
        Name of DB collection
    query : dict
        Query to update collection `collection_name`
    """
    db[collection_name].remove(query)


@inf_waiting
def collection_count_infinity(db, collection_name):
    """
    This function will try perform action until success
    Parameters
    ----------
    db: pymongo.database.Database
            Build System MongoDB database
    collection_name : str
        Name of DB collection

    Returns
    -------

    """
    return db[collection_name].find().count()
