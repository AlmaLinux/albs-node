# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-10-19

"""
Cloud Linux Build System logging functions.
"""

import logging

__all__ = ['log_put', 'configure_logger']


def configure_logger(verbose, name=None, filelog=None):
    """
    Initializes a program root logger.

    Parameters
    ----------
    verbose : bool
        Enable DEBUG messages output if True, print only INFO and higher
        otherwise.
    name : str, optional
        Logger name. A root logger will be used if omitted.
    filelog : str, optional
        Logger path for file.

    Returns
    -------
    logging.Logger
        Configured root logger.
    """
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler()
    handler.setLevel(level)
    log_format = "%(asctime)s %(levelname)-8s [%(threadName)s]: %(message)s"
    formatter = logging.Formatter(log_format, '%y.%m.%d %H:%M:%S')
    handler.setFormatter(formatter)
    logger = logging.getLogger(name)
    logger.addHandler(handler)
    logger.setLevel(level)
    if filelog is not None:
        fh = logging.FileHandler(filelog)
        fh.setLevel(level)
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    return logger


def create_log_dict(log, status):
    """
    Create dict for log

    Parameters
    ----------
    log:       str or unicode
          String for log
    status:       str or unicode
          Status
    Returns
    -------
    dict
        Dict with status and message

    """
    if status not in ['info', 'debug', 'error']:
        status = 'error'
    return {'status': status, 'log': log}


def log_put(logs_queue, message, log_type='info'):
    log_type = log_type if log_type in ['info', 'debug', 'error'] else 'info'
    logs_queue.put(create_log_dict(message, log_type), True)