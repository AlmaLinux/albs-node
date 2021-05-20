# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-01-15

"""CloudLinux Build System validation utilities."""

import bson
import cerberus
import yaml

from ..errors import DataSchemaError

__all__ = ['verify_schema', 'BuildSysValidator']


class BuildSysValidator(cerberus.Validator):

    """
    Custom validator for CloudLinux Build System objects.
    """

    def _validate_type_objectid(self, value):
        """
        Checks that the value is a valid bson.ObjectId instance.

        Examples
        --------
        validator.schema = {'_id': {'type': 'objectid'}}

        Parameters
        ----------
        value : bson.objectid.ObjectId
            Value to check.

        Returns
        -------
        bool
            True if the given value is a bson.ObjectId instance, False
            otherwise.
        """
        return isinstance(value, bson.ObjectId)

    def _normalize_coerce_to_boolean(self, value):
        """
        Converts a given (YAML-compatible) value to a boolean.

        Parameters
        ----------
        value : str or bool
            Value to convert.

        Returns
        -------
        bool
            True or False.

        Raises
        ------
        ValueError
            If a given value isn't a boolean.
        """
        v = value if isinstance(value, bool) else yaml.load(value)
        if isinstance(v, bool):
            return v
        raise ValueError('invalid value for the boolean type')


def verify_schema(schema, data):
    """
    Validates the data against the specified schema.

    Parameters
    ----------
    schema : dict
        Validation schema.
    data : dict
        Data.

    Returns
    -------
    dict
        Validated and normalized data.

    Raises
    ------
    build_node.errors.DataSchemaError
        If the data doesn't match the schema.
    """
    validator = BuildSysValidator(schema)
    if not validator.validate(data):
        raise DataSchemaError(str(validator.errors))
    return validator.document
