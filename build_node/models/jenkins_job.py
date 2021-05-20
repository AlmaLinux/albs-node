# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-02-12

"""
CloudLinux Build System Jenkins job wrapper.
"""

import pymongo
from ..errors import DataNotFoundError

__all__ = ['create_jenkins_job_index', 'delete_jenkins_job']


def create_jenkins_job_index(db):
    """
    Creates a Jenkins jobs collection indexes.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    """
    db['jenkins_jobs'].create_index(
        [('name', pymongo.DESCENDING)], unique=True)


def delete_jenkins_job(db, job_id):
    """
    Deletes a Jenkins job with the specified _id from the database.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    job_id : bson.objectid.ObjectId
        Jenkins job _id.

    Raises
    ------
    build_node.errors.DataNotFoundError
        If Jenkins job with the specified _id does not exist.
    """
    job = db['jenkins_jobs'].find_one_and_delete(
        {'_id': job_id}, {'_id': True})
    if not job:
        raise DataNotFoundError('Jenkins job {0!s} does not exist'.
                                format(job_id))
    db['cl_recipes'].update_many({'jenkins_jobs._id': job['_id']},
                                 {'$pull': {'jenkins_jobs': {'_id': job_id}}})
