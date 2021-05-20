# -*- mode:python; coding:utf-8; -*-
# author: Vyacheslav Potoropin <vpotoropin@cloudlinux.com>
# created: 25.07.2019

import json

import luigi


__all__ = ['luigi_parameter_to_json']


def luigi_parameter_to_json(frozen_object):
    """
    Converts frozen luigi object to json.

    Parameters
    ----------
    frozen_object : luigi.DictParameter or luigi.ListParameter
        Object to serialize.

    Returns
    -------
    dict
        Serialized object.
    """
    return json.loads(luigi.DictParameter().serialize(frozen_object))
