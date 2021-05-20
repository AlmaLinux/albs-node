# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-11-03


from .blocked_jwt_token import create_blocked_jwt_token_index
from .build import create_build_index
from .build_flavor import create_build_flavors_index
from .deb_package import create_deb_package_index
from .deployment_tool import create_deployment_tool_index
from .git_record import create_git_record_index
from .install_stats import create_install_stats_index, create_systemid_indexes
from .jenkins_job import create_jenkins_job_index
from .pgp_keys import create_pgp_keys_index
from .project import create_project_index
from .release_tracker import create_release_tracker_indexes
from .rpm_package import create_rpm_package_index
from .modular_build_indexes import create_modular_build_indexes_index
from .web_request_stats import create_web_request_stats_index

__all__ = ['create_indexes']


def create_indexes(db):
    """
    Creates Build System database collections indexes.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    """
    create_blocked_jwt_token_index(db)
    create_build_index(db)
    create_build_flavors_index(db)
    create_deb_package_index(db)
    create_deployment_tool_index(db)
    create_git_record_index(db)
    create_install_stats_index(db)
    create_jenkins_job_index(db)
    create_pgp_keys_index(db)
    create_project_index(db)
    create_release_tracker_indexes(db)
    create_rpm_package_index(db)
    create_modular_build_indexes_index(db)
    create_systemid_indexes(db)
    create_web_request_stats_index(db)
