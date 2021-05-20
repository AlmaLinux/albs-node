# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-04-04

"""
Product (release target) wrapper.
"""

from collections import OrderedDict
import urllib.parse
import re
import copy
import datetime

import pymongo

from build_node.constants import ReleaseStatus, SlotStatus, SlotCommand
from ..errors import DataNotFoundError, WorkflowError
from ..utils.validation import verify_schema

__all__ = ['create_products_index', 'create_product', 'find_product',
           'find_products', 'replace_product', 'Product', 'create_slot',
           'update_slot']


product_distribution_schema = {
    'type': 'dict', 'required': True, 'empty': False,
    'schema': {
        'name': {'type': 'string', 'required': True, 'empty': False},
        'description': {'type': 'string', 'required': True, 'empty': False},
        'distr_type': {'type': 'string', 'required': True,
                       'allowed': ['debian', 'rhel']},
        'distr_version': {'type': 'string', 'required': True, 'empty': False}
    }
}

product_repo_schema = {
    'type': 'dict', 'required': True, 'empty': False,
    'keyschema': {'type': 'string', 'empty': False},
    'valueschema': {
        'type': 'list', 'empty': False,
        'schema': {
            'type': 'dict',
            'schema': {
                'name': {'type': 'string', 'required': True, 'empty': False},
                'slot': {'type': 'string', 'empty': False},
                'read_only': {'type': 'boolean'},
                'ignore_comps': {'type': 'boolean'},
                'debuginfo': {'type': 'boolean'},
                'public': {'type': 'boolean'}
            }
        }
    }
}

product_reference_repo_schema = copy.deepcopy(product_repo_schema)
product_reference_repo_schema['required'] = False

spacewalk_channels_schema = {
    'type': 'dict', 'empty': False,
    'keyschema': {'type': 'string', 'empty': False},
    'valueschema': {
        'type': 'list', 'empty': False,
        'schema': {
            'type': 'dict',
            'schema': {
                'name': {'type': 'string', 'required': True, 'empty': False}
            }
        }
    }
}

stage_distributions_schema = {
    'type': 'list', 'required': True, 'empty': False,
    'schema': {
        'type': 'dict', 'required': True, 'empty': False,
        'schema': {
            'name': {'type': 'string', 'required': True, 'empty': False},
            'repositories': product_repo_schema,
            'reference_repositories': product_reference_repo_schema,
            'spacewalk_channels': spacewalk_channels_schema
        }
    }
}

product_schema = {
    '_id': {'type': 'objectid'},
    'name': {'type': 'string', 'required': True, 'empty': False},
    'description': {'type': 'string', 'required': True, 'empty': False},
    'sign_repodata': {'type': 'boolean', 'default': False},
    # Shows if dependency check during release should
    #       be together with some other products
    'depends_on': {'type': 'list', 'schema': {'type': 'dict', 'schema': {
        'name': {'type': 'string', 'required': True, 'empty': False}
    }}},
    'default': {'type': 'boolean'},
    'pgp_keyid': {'type': 'string', 'required': True, 'empty': False},
    'slots': {
        'type': 'dict', 'required': False,
        'schema': {
            'host': {'type': 'string', 'required': True, 'empty': False},
            'links_directory': {'type': 'string'},
            'systemid_mapping': {'type': 'string'},
            'hybrid_mapping': {'type': 'string'},
            'instances': {
                'type': 'list', 'schema': {
                    'type': 'dict', 'schema': {
                        'name': {'type': 'string', 'required': True}
                    }
                }
            }
        }
    },
    'slots_links_directory': {'type': 'string', 'empty': False},
    'slots_nginx_config': {'type': 'string', 'empty': False},
    'distributions': {
        'type': 'list', 'required': True, 'empty': False,
        'schema': product_distribution_schema
    },
    'pipeline': {
        'type': 'list', 'required': True, 'empty': False,
        'schema': {
            'type': 'dict', 'required': True, 'empty': False,
            'schema': {
                'stage': {'type': 'string', 'required': True, 'empty': False},
                'distributions': stage_distributions_schema
            }
        }
    },
    'virtual_pipeline': {
        'type': 'list', 'required': False,
        'schema': {
            'type': 'dict', 'required': True, 'empty': False,
            'schema': {
                'name': {'type': 'string', 'required': True, 'empty': False},
                'distributions': {
                    'type': 'list', 'required': True,
                    'schema': {
                        'type': 'dict', 'required': True,
                        'schema': {
                            'name': {'type': 'string', 'required': True,
                                     'empty': False},
                            'stages': {
                                'type': 'list',
                                'schema': {
                                    'type': 'string', 'empty': False
                                }
                            },
                            'default': {'type': 'string', 'required': True,
                                        'empty': False}
                        }
                    }
                }
            }
        }
    }
}

product_slot_schema = {
    '_id': {'type': 'objectid'},
    'name': {'type': 'string', 'required': True, 'empty': False},
    'index': {'type': 'integer', 'required': True},
    'release_id': {'type': 'objectid'},
    'status': {'type': 'integer', 'default': SlotStatus.FREE}
}


class ProductSlot(object):

    def __init__(self, _id=None, name=None):
        if _id is None and name is None:
            raise WorkflowError('Either _id or name should be provided')
        self.__id = _id
        self.__name = name
        self.__loaded_from_db = False
        self.__systemid_list = None
        self.__removed_systemids = None
        self.__rollout_percent = None
        self.__rollout_history = None
        self.__release_id = None
        self.__status = None
        self.__index = None
        self.__current_rollout = None

    def __eq__(self, other_slot):
        if self.name and other_slot.name:
            return self.name == other_slot.name
        elif self._id and other_slot._id:
            return self._id == other_slot._id
        raise WorkflowError('Slots don\'t have any fields in common')

    def load_db_data(self, db):
        """
        Loads slot information from the database.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.

        Raises
        ------
        build_node.errors.DataNotFoundError
            If the slot is not found in the database.
        """
        if self.__loaded_from_db:
            return
        query = {}
        if self.__name:
            query['name'] = self.__name
        if self.__id:
            query['_id'] = self.__id
        db_slot = db['slots'].find_one(query)
        if not db_slot:
            raise DataNotFoundError('slot {0} is not found in the '
                                    'database'.format(self.__name))
        self.__loaded_from_db = True
        self.__id = db_slot['_id']
        self.__name = db_slot['name']
        self.__status = db_slot['status']
        self.__index = db_slot['index']
        self.__release_id = db_slot.get('release_id')
        self.__locked_until = db_slot.get('locked_until', None)
        self.__current_release = {}
        if self.__release_id:
            feature_release = db['feature_releases'].find_one(
                {'releases._id': self.__release_id})
            self.__current_release = [
                release for release in feature_release['releases']
                if release['_id'] == self.__release_id
            ][0]
        self.__current_rollout = self.__current_release.get('rollout', {})
        self.__systemid_list = self.__current_rollout.get('systemid_list', [])
        self.__removed_systemids = self.__current_rollout.get(
            'removed_systemids', [])
        self.__rollout_percent = self.__current_rollout.get('percent', 0)
        self.__rollout_history = self.__current_rollout.get('history', [])

    @staticmethod
    def get_from_db(db, _id=None, name=None):
        """
        Creates new ProductSlot instance from the database information.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        _id : bson.ObjectId, optional
            Slot database id.
        name : str, optional
            Slot name.

        Returns
        -------
        build_node.models.product.ProductSlot
            Created ProductSlot instance.
        """
        slot = ProductSlot(_id=_id, name=name)
        slot.load_db_data(db)
        return slot

    def iter_repositories(self, db):
        """
        Iterates over a list of slot repositories.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.

        Returns
        -------
        generator
            Iterator over a list of slot repositories.
        """
        self.__check_db_loaded()
        for repo in db['repos'].find({'slot': self.__name}):
            yield DistroRepository(
                name=repo['name'], arch=repo['arch'], slot=repo['slot'])

    def schedule_rollout_update(self, db, user_id, new_rollout_percent):
        """
        Schedules rollout updating.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        user_id : bson.ObjectId
            User, who invoke operation.
        new_rollout_percent : float
            Expected rollout percentage.
        """
        self.__check_schedule_available(db)
        if self.empty:
            raise WorkflowError('Trying to update empty slot: {0}'.format(
                self.name))
        if new_rollout_percent <= 0:
            raise WorkflowError('Trying to set negative percent for '
                                'slot: {0}'.format(self.name))
        if new_rollout_percent > 100:
            raise WorkflowError(
                'Trying to increase rollout percent > 100: {0}'.format(
                    self.name))
        command = SlotCommand.REPO_RELEASE
        if new_rollout_percent < 100:
            command = SlotCommand.UPDATE_PERCENT
        rollout_update = {
            'command': command,
            'percent': new_rollout_percent,
            'user_id': user_id
        }
        self.__schedule_db_update(db, rollout_update)

    def schedule_rollout_replace(self, db, user_id, release):
        """
        Schedules rollout replacing.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        user_id : bson.ObjectId
            User, who invoke operation.
        release : dict
            Database-like release record.
        """
        self.__check_schedule_available(db)
        if self.empty:
            raise WorkflowError('Trying to replace empty slot: {0}'.format(
                self.name))
        rollout_update = {
            'command': SlotCommand.REPLACE,
            'user_id': user_id
        }
        self.__schedule_db_update(db, rollout_update, release=release)

    def schedule_rollout_pause(self, db, user_id):
        """
        Schedules rollout pause.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        user_id : bson.ObjectId
            User, who invoke operation.
        """
        self.__check_schedule_available(db)
        if self.empty:
            raise WorkflowError('Trying to pause empty slot: {0}'.format(
                self.name))
        if self.paused:
            raise WorkflowError('Slot is already paused: {0}'.format(
                self.name))
        rollout_update = {
            'command': SlotCommand.PAUSE,
            'user_id': user_id
        }
        self.__schedule_db_update(db, rollout_update)

    def schedule_rollout_unpause(self, db, user_id):
        """
        Schedules rollout unpause.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        user_id : bson.ObjectId
            User, who invoke operation.
        """
        self.__check_schedule_available(db)
        if self.empty:
            raise WorkflowError('Trying to unpause empty slot: {0}'.format(
                self.name))
        if not self.paused:
            raise WorkflowError('Slot is not paused: {0}'.format(
                self.name))
        rollout_update = {
            'command': SlotCommand.UNPAUSE,
            'user_id': user_id
        }
        self.__schedule_db_update(db, rollout_update)

    def schedule_rollout_clean(self, db, user_id):
        """
        Schedules rollout cleaning.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        user_id : bson.ObjectId
            User, who invoke operation.
        """
        self.__check_schedule_available(db)
        if self.empty:
            raise WorkflowError('Trying to clean empty slot: {0}'.format(
                self.name))
        rollout_update = {
            'command': SlotCommand.CLEAN,
            'user_id': user_id
        }
        self.__schedule_db_update(db, rollout_update)

    def schedule_new_release(self, db, release_id):
        """
        Schedules new rollout.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        release_id : bson.ObjectId
            Release, which will fill the slot.
        """
        self.__check_schedule_available(db)
        db['slots'].update_one(
            {'name': self.name},
            {'$set': {'release_id': release_id, 'status': SlotStatus.IN_USE}}
        )

    def schedule_packages_update(self, db, user_id, release_plan,
                                 depsolver_strategies):
        """
        Schedule release additional packages in rollout repository.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        user_id : bson.ObjectId
            User, who invoke operation.
        release_plan : list
            Release packages list.
        depsolver_strategies : dict
            Dependency solver strategies.
        """
        self.__check_schedule_available(db)
        if self.empty:
            raise WorkflowError('Trying to update empty slot: {0}'.format(
                self.name))
        rollout_update = {
            'command': SlotCommand.UPDATE_PACKAGES,
            'user_id': user_id
        }
        self.__schedule_db_update(
            db, rollout_update,
            release_plan=release_plan,
            depsolver_strategies=depsolver_strategies
        )

    def init_release(self, db, user_id, rollout_percent, systemid_list):
        """
        Fills slot with the new release.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        user_id : bson.ObjectId
            User, who invoke operation.
        rollot_percent : float
            New rollout percentage.
        systemid_list : list
            Lists of machines added to rollout.
        """
        self.__check_db_loaded()
        self.__systemid_list = systemid_list
        self.__rollout_percent = rollout_percent
        text_status = 'Init rollout with {0}%'.format(self.__rollout_percent)
        self.__status = SlotStatus.IN_USE
        self.__update_db_release(db, user_id, text_status)

    def lock_release(self, db, lock_time, user_id):
        """
        Places slot on "quarantine" until clients will update their metadata.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        lock_time : datetime.datetime
            Timestamp, until slot will be locked.
        user_id : bson.ObjectId
            User, who invoke operation.
        """
        self.__check_db_loaded()
        text_status = 'Slot is locked until users update their metadata'
        self.__status = SlotStatus.LOCKED
        self.__update_db_release(db, user_id, text_status, lock_time=lock_time)

    def update_release(self, db, user_id, rollout_percent,
                       additional_systemids=None, removed_systemids=None):
        """
        Updates slot rollout percentage.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        user_id : bson.ObjectId
            User, who invoke operation.
        rollot_percent : float
            New rollout percentage.
        additional_systemids : list
            List of machines added to rollout.
        removed_systemids : list
            List of machines removed from rollout.
        """
        self.__check_db_loaded()
        if additional_systemids:
            for systemid in additional_systemids:
                if systemid in self.__removed_systemids:
                    self.__removed_systemids.remove(systemid)
            self.__systemid_list.extend(additional_systemids)
        if removed_systemids:
            for systemid in removed_systemids:
                self.__systemid_list.remove(systemid)
            self.__removed_systemids.extend(removed_systemids)
        change_status = 'Decreased' if removed_systemids else 'Increased'
        text_status = '{0} rollout from {1}% to {2}%'.format(
            change_status, self.__rollout_percent, rollout_percent)
        self.__rollout_percent = rollout_percent
        self.__status = SlotStatus.IN_USE
        self.__update_db_release(db, user_id, text_status)

    def packages_update(self, db, user_id, additional_systemid_list):
        """
        Update slot status: slot recieve additional packages.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        user_id : bson.ObjectId
            User, who invoke operation.
        additional_systemid_list : list
            List of machines added to rollout.
        """
        self.__check_db_loaded()
        self.__systemid_list.extend(additional_systemid_list)
        text_status = 'Added additional packages to rollout'
        self.__update_db_release(db, user_id, text_status)

    def pause_release(self, db, user_id):
        """
        Pauses release, machines won't be getting packages
        from this slot.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        user_id : bson.ObjectId
            User, who invoke operation.
        """
        self.__check_db_loaded()
        self.__status = SlotStatus.PAUSED
        self.__update_db_release(db, user_id, 'Paused rollout')

    def unpause_release(self, db, user_id):
        """
        Unpauses release, machines will get packages from this slot again.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        user_id : bson.ObjectId
            User, who invoke operation.
        """
        self.__check_db_loaded()
        self.__status = SlotStatus.IN_USE
        self.__update_db_release(db, user_id, 'Unpaused rollout')

    def replace_release(self, db, user_id, additional_systemid_list):
        """
        Replaces packages in rollout repositories.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        user_id : bson.ObjectId
            User, who invoke operation.
        additional_systemid_list : list
            Lists of machines added to rollout.
        """
        self.__check_db_loaded()
        self.__systemid_list.extend(additional_systemid_list)
        self.__update_db_release(db, user_id, 'Replaced packages')

    def clean_release(self, db, user_id, reason):
        """
        Cleaning packages from rollout repositories.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        user_id : bson.ObjectId
            User, who invoke operation.
        """
        self.__check_db_loaded()
        if self.empty:
            return
        text_status = 'Rollout is cleaned: {0}'.format(reason)
        self.__update_db_release(db, user_id, text_status, SlotStatus.FREE)
        self.__systemid_list = []
        self.__rollout_percent = 0
        self.__rollout_history = []
        self.__release_id = None

    @property
    def name(self):
        return self.__name

    @property
    def _id(self):
        return self.__id

    @property
    def empty(self):
        self.__check_db_loaded()
        return self.__status == SlotStatus.FREE

    @property
    def locked(self):
        self.__check_db_loaded()
        return self.__status == SlotStatus.LOCKED

    @property
    def paused(self):
        self.__check_db_loaded()
        return self.__status == SlotStatus.PAUSED

    @property
    def status(self):
        self.__check_db_loaded()
        return self.__status

    @property
    def systemid_list(self):
        self.__check_db_loaded()
        return self.__systemid_list

    @property
    def removed_systemids(self):
        self.__check_db_loaded()
        return self.__removed_systemids

    @property
    def index(self):
        self.__check_db_loaded()
        return self.__index

    @property
    def release_id(self):
        self.__check_db_loaded()
        return self.__release_id

    @property
    def rollout_percent(self):
        self.__check_db_loaded()
        return self.__rollout_percent

    @property
    def last_update_user(self):
        self.__check_db_loaded()
        return self.__rollout_history[-1]['user_id']

    @property
    def rollout_platforms(self):
        self.__check_db_loaded()
        if self.empty:
            return []
        return list(set(
            item['distribution'] for item in self.__current_release['plan']
        ))

    def __schedule_db_update(self, db, rollout_update, release=None,
                             release_plan=None, depsolver_strategies=None):
        """
        Schedule rollout update.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        rollout_update : dict
            Update payload information.
        release : dict
            Database-like release record.
        release_plan : list
            Release packages list.
        depsolver_strategies : dict, optional
            Dependency solver strategies.
        """
        set_query = {
            '$set': {
                'status': ReleaseStatus.QUEUED,
                'rollout.update': rollout_update
            }
        }
        if release is not None:
            set_query['$set'].update(release)
        if release_plan is not None:
            set_query['$set']['plan'] = release_plan
        if depsolver_strategies is not None:
            set_query['$set']['depsolver_strategy'] = depsolver_strategies
        for key in list(set_query['$set'].keys()):
            value = set_query['$set'].pop(key)
            set_query['$set']['releases.$.{0}'.format(key)] = value
        db['feature_releases'].update_one(
            {'releases._id': self.release_id}, set_query)

    def __update_db_release(self, db, user_id, text_status, clean_status=None,
                            lock_time=None):
        """
        Update slot database record.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        user_id : bson.ObjectId
            User, who invoke operation.
        text_status : str
            Infromation about update operation.
        clean_status : int, optional
            If set, will clean release from slot db record.
        lock_time : datetime.datetime, optional
            Timestamp, until slot will be locked.
        """
        self.__rollout_history.append({
            'ts': datetime.datetime.now(),
            'user_id': user_id,
            'status': text_status
        })
        rollout = {
            'slot_name': self.name,
            'percent': self.rollout_percent,
            'systemid_list': self.systemid_list,
            'removed_systemids': self.removed_systemids,
            'history': self.__rollout_history
        }
        db['feature_releases'].update_one(
            {'releases._id': self.release_id},
            {'$set': {'releases.$.rollout': rollout}}
        )
        set_query = {'$set': {'status': self.__status}}
        if lock_time is not None:
            lock_ts = datetime.datetime.utcnow() + datetime.timedelta(
                seconds=lock_time)
            set_query['$set']['locked_until'] = lock_ts
        if clean_status is not None:
            self.__status = clean_status
            set_query['$unset'] = {'release_id': 1, 'locked_until': 1}
            set_query['$set']['status'] = self.__status
        db['slots'].update_one({'_id': self._id}, set_query)

    def __check_schedule_available(self, db):
        """
        Checks if update can be scheduled into slot.

        Raises
        ------
        build_node.errors.WorkflowError
            If update can't be scheduled into slot.
        """
        self.__check_db_loaded()
        if self.empty:
            return
        if self.locked:
            raise WorkflowError(
                'Slot is locked until {}, can\'t schedule update'.format(
                    self.__locked_until)
            )
        feature_release = db['feature_releases'].find_one(
            {'releases._id': self.release_id})
        for release in feature_release['releases']:
            if release['_id'] == self.release_id:
                if release.get('rollout', {}).get('update'):
                    raise WorkflowError('Slot update is already scheduled')

    def __check_db_loaded(self):
        """
        Checks if a slot data was loaded from the database.

        Raises
        ------
        build_node.errors.WorkflowError
            If the slot data wasn't loaded from the database.
        """
        if not self.__loaded_from_db:
            raise WorkflowError('Slot is not loaded from the DB')


class DistroRepository(object):

    """Distribution repository."""

    def __init__(self, name, arch, debuginfo=False, ignore_comps=False,
                 read_only=False, slot=None, public=False):
        """
        Distribution repository initialization.

        Parameters
        ----------
        name : str
            Repository name.
        arch : str
            Repository architecture.
        debuginfo : bool, optional
            Repository contains debuginfo packages if True, False otherwise.
        ignore_comps : bool, optional
            Ignore repository groups (comps.xml) while performing checks if
            True, default is False.
        read_only : bool, optional
            Don't allow the repository modification if True, default is False.
        slot : str, optional
            Repository slot name.
        public : bool, optional
            If true, repository available for clients outside company.
            Otherwise, repository available only for developers.
        """
        self.__name = name
        self.__arch = arch
        self.__debuginfo = debuginfo
        self.__ignore_comps = ignore_comps
        self.__read_only = read_only
        self.__slot = slot
        self.__public = public
        self.__id = None
        self.__remote_path = None
        self.__url = None
        self.__remote_hostname = None
        self.__remote_port = None
        self.__remote_user = None
        self.__repo_path = None
        self.__loaded_from_db = False

    def load_db_data(self, db):
        """
        Loads a repository remote path and URL information from the database.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.

        Raises
        ------
        build_node.errors.DataNotFoundError
            If the repository is not found in the database.
        """
        if self.__loaded_from_db:
            return
        repo = db['repos'].find_one(
            {'name': self.__name, 'arch': self.__arch, 'slot': self.__slot},
            {'_id': True, 'debuginfo': True, 'remote_path': True, 'url': True,
             'public_sync_path': True, 'swng_list_all_packages_checksum': True}
        )
        if not repo:
            raise DataNotFoundError('repository {0}.{1} is not found in the '
                                    'database'.format(self.__name,
                                                      self.__arch))
        self.__loaded_from_db = True
        self.__id = repo['_id']
        self.__debuginfo = repo.get('debuginfo', False)
        self.__remote_path = repo.get('remote_path')
        self.__url = repo.get('url')
        self.__swng_list_all_packages_checksum = repo.get(
            'swng_list_all_packages_checksum')
        self.__public_sync_path = None
        if self.__remote_path:
            parsed_url = urllib.parse.urlparse(self.__remote_path)
            self.__remote_hostname = parsed_url.hostname
            self.__remote_user = parsed_url.username
            self.__remote_port = parsed_url.port
            self.__repo_path = parsed_url.path
        public_sync_path = repo.get('public_sync_path')
        if public_sync_path:
            parsed_url = urllib.parse.urlparse(public_sync_path)
            username = parsed_url.username + '@' if parsed_url.username else ''
            port = f':{parsed_url.port}' if parsed_url.port else ''
            self.__public_sync_path = (
                f'{username}{parsed_url.hostname}{port}:{parsed_url.path}'
            )

    @property
    def _id(self):
        """
        Database _id.

        Returns
        -------
        bson.objectid.ObjectId
        """
        self.__check_db_loaded()
        return self.__id

    @property
    def name(self):
        """
        Repository name.

        Returns
        -------
        str
        """
        return self.__name

    @property
    def arch(self):
        """
        Repository architecture.

        Returns
        -------
        str
        """
        return self.__arch

    @property
    def debuginfo(self):
        """
        Indicates that repository contains debuginfo packages.

        Returns
        -------
        bool
        """
        return self.__debuginfo

    @property
    def ignore_comps(self):
        """
        Indicates that comps.xml (groups) processing shouldn't be performed
        for the repository.

        Returns
        -------
        bool
        """
        return self.__ignore_comps

    @property
    def slot(self):
        return self.__slot

    @property
    def read_only(self):
        """
        Indicates that the repository modification is not allowed.

        Returns
        -------
        bool
        """
        return self.__read_only

    @property
    def remote_hostname(self):
        """
        Remote host hostname or IP address.

        Returns
        -------
        str
        """
        self.__check_db_loaded()
        return self.__remote_hostname

    @property
    def remote_port(self):
        """
        Remote host TCP port.

        Returns
        -------
        int or None
        """
        self.__check_db_loaded()
        return self.__remote_port

    @property
    def remote_user(self):
        """
        Remote host user name.

        Returns
        -------
        str or None
        """
        self.__check_db_loaded()
        return self.__remote_user

    @property
    def repo_path(self):
        """
        Remote repository directory path (without hostname and protocol data).

        Returns
        -------
        str
        """
        self.__check_db_loaded()
        return self.__repo_path

    @property
    def remote_path(self):
        """
        Repository remote path.

        Returns
        -------
        str
        """
        self.__check_db_loaded()
        return self.__remote_path

    @property
    def public_sync_path(self):
        self.__check_db_loaded()
        return self.__public_sync_path

    @property
    def public(self):
        """
        Is repository public or internal.

        Returns
        -------
        bool
        """
        return bool(self.__public)

    @property
    def swng_list_all_packages_checksum(self):
        """
        Spacewalk replacement listAllPackagesChecksum file path.

        Returns
        -------
        str or None
        """
        return self.__swng_list_all_packages_checksum

    @property
    def url(self):
        """
        Repository URL.

        Returns
        -------
        str
        """
        self.__check_db_loaded()
        return self.__url

    def __check_db_loaded(self):
        """
        Checks if a repository data was loaded from the database.

        Raises
        ------
        build_node.errors.WorkflowError
            If the repository data wasn't loaded from the database.
        """
        if not self.__loaded_from_db:
            raise WorkflowError('repository is not loaded from the DB')

    def _asdict(self):
        """
        Returns a dictionary representation of the object.

        Returns
        -------
        dict
        """
        return {'_id': self.__id,
                'name': self.__name,
                'arch': self.__arch,
                'debuginfo': self.__debuginfo,
                'ignore_comps': self.__ignore_comps,
                'read_only': self.__read_only,
                'remote_path': self.__remote_path,
                'url': self.__url}

    def __hash__(self):
        """
        Calculates a repository configuration hash so that it can be used as
        a dictionary key.

        Returns
        -------
        int
            Configuration hash.
        """
        return hash((self.__id, self.__name, self.__arch, self.__debuginfo,
                     self.__remote_path, self.__url, self.__ignore_comps,
                     self.__read_only))

    def __eq__(self, other):
        """
        Compares a repository with other repository.

        Parameters
        ----------
        other : DistroRepository
            Other repository.

        Returns
        -------
        bool
            Comparison result: True if both repositories are equal, False
            otherwise.
        """
        if not isinstance(other, DistroRepository):
            return False
        return self._asdict() == other._asdict()

    def __str__(self):
        return '<DistroRepository(name={0!r}, arch={1!r}, _id={2!r})>'.\
            format(self.__name, self.__arch, self.__id)


class ProductDistro(object):

    def __init__(self, name, description, distr_type, distr_version):
        """
        Product distribution initialization.

        Parameters
        ----------
        name : str
            Distribution name.
        description : str
            Distribution description.
        distr_type : str
            Distribution type (e.g. debian, rhel).
        distr_version : str
            Distribution version.
        """
        self.__name = name
        self.__description = description
        self.__distr_type = distr_type
        self.__distr_version = distr_version
        self.__repos = {}
        self.__reference_repos = {}
        self.__unusual_repos = {}

    def _add_repository(self, repo):
        """
        Adds a new repository configuration to the distribution.

        Parameters
        ----------
        repo : DistroRepository
            Repository configuration.
        """
        arch = repo.arch
        if arch not in self.__repos:
            self.__repos[arch] = []
        self.__repos[arch].append(repo)

    def _add_reference_repository(self, repo):
        """
        Adds a new repository configuration to the distribution.

        Parameters
        ----------
        repo : DistroRepository
            Repository configuration.
        """
        arch = repo.arch
        if arch not in self.__reference_repos:
            self.__reference_repos[arch] = []
        self.__reference_repos[arch].append(repo)

    def add_custom_release_rule(self, recipe, query):
        """
        Adds a new repository configuration to the distribution.

        Parameters
        ----------
        recipe : bson.ObjectId
            recipe id.
        query : str
            regex rules for package filter.
        """
        if recipe not in self.__unusual_repos:
            self.__unusual_repos[recipe] = {}
        compiled_query = re.compile(query)
        if compiled_query not in self.__unusual_repos[recipe]:
            self.__unusual_repos[recipe][compiled_query] = []

    def add_custom_release_repo(self, recipe, query, repo):
        """
        Append custrom release repos to regex mapping.

        Parameters
        ----------
        recipe : bson.ObjectId
            recipe id.
        query : str
            regex rules for package filter.
        repo : dict
            custom repository for mapping.
        """
        compiled_query = re.compile(query)
        self.__unusual_repos[recipe][compiled_query].append(repo)

    def unusual_projects(self):
        """
        List of projects with custom release rules.

        Returns
        -------
        list
            Project ids with custom release rules.
        """
        return list(self.__unusual_repos.keys())

    def skip_in_rollout(self, project_id, package_name):
        """
        Checks, if there are custom rules for package releasing.

        Parameters
        ----------
        project_id : bson.ObjectId
            BuildSystem build recipe id.
        package_name : str
            Package to check.

        Returns
        -------
        bool
            True if there is custom rule for package releasing,
            False otherwise.
        """
        if project_id in self.__unusual_repos:
            for query, rule in self.__unusual_repos[project_id].items():
                if re.match(query, package_name):
                    for item in rule:
                        return item.get('rollout_skip', False)
        return False

    def support_rollout_feature(self, project_id):
        """
        Checks, if project can be released in rollout.

        Parameters
        ----------
        project_id : bson.ObjectId
            BuildSystem build recipe id.

        Returns
        -------
        bool
            True if project can be released in rollout, False otherwise.
        """
        if project_id not in self.__unusual_repos:
            return True
        for rule in self.__unusual_repos[project_id].values():
            for item in rule:
                return item.get('rollout_skip', False)

    def iter_reference_repositories(self, arch, debuginfo=False):
        """
        Iterates over a list of reference repositories.

        Parameters
        ----------
        arch : str
            Repository architecture.
        debuginfo : bool, optional
            Repository contains debuginfo packages if True, False otherwise.


        Returns
        -------
        generator
            Iterator over a list of reference repositories.
        """
        for repo in self.__reference_repos.get(arch, ()):
            if repo.debuginfo != debuginfo:
                continue
            yield repo

    def iter_release_repositories(self, arch, debuginfo=False,
                                  package_name=None, project_id=None,
                                  slot_name=None, public_release=False):
        """
        Iterates over a list of release repositories for the specified
        architecture.

        Parameters
        ----------
        arch : str
            Release architecture.
        debuginfo : bool
            Include only debug information repositories if True, otherwise
            include only normal package repositories.
        project_id : bson.ObjectId, optional
            If provided, will check special rules for project.
        package_name : str, optional
            Return repos from special rules, if matched
        public_release : bool, optional
            Return public repos if true

        Returns
        -------
        generator
            Iterator over a list of release repositories.
        """
        if project_id in self.__unusual_repos and slot_name is None:
            matched = False
            project = self.__unusual_repos[project_id]
            for query in project:
                if re.match(query, package_name):
                    matched = True
                    for repo in project[query]:
                        if repo['debug'] == debuginfo:
                            yield DistroRepository(repo['name'], arch,
                                                   debuginfo=repo['debug'])
            if matched:
                return

        for repo in self.__repos.get(arch, ()):
            if repo.debuginfo != debuginfo or repo.read_only:
                continue
            if repo.slot != slot_name:
                continue
            if repo.public > public_release:
                continue
            yield repo

    def iter_repositories(self, arch, slots=False, public=False,
                          slot_name=None):
        for repo in self.__repos.get(arch, []):
            if (not slots and repo.slot) or (slots and not repo.slot):
                continue
            if (not public and repo.public) or (public and not repo.public):
                continue
            if slot_name is not None and repo.slot != slot_name:
                continue
            yield repo

    def is_platform_compatible(self, platform):
        hybrid = ['CL6h', 'CL7h']
        compatible = platform.get('distr_type') == self.__distr_type and \
            platform.get('distr_version') == self.__distr_version
        if platform.get('name') in hybrid:
            compatible = compatible and platform.get('name') == self.__name
        return compatible

    @property
    def name(self):
        """
        Distribution name.

        Returns
        -------
        str
        """
        return self.__name

    @property
    def description(self):
        """
        Distribution description.

        Returns
        -------
        str
        """
        return self.__description

    @property
    def distr_type(self):
        """
        Distribution type.

        Returns
        -------
        str
        """
        return self.__distr_type

    @property
    def distr_version(self):
        """
        Distribution version.

        Returns
        -------
        str
        """
        return self.__distr_version


class PipelineStage(object):

    """Product release pipeline stage."""

    def __init__(self, name):
        self.__name = name
        self.__distros = {}

    def _add_distro(self, distro):
        """
        Adds a new distribution to the pipeline.

        Parameters
        ----------
        distro : ProductDistro
            Product distribution.
        """
        self.__distros[distro.name] = distro

    def get_distribution(self, distro_name):
        return self.__distros.get(distro_name)

    def iter_distros(self):
        for distro in self.__distros.values():
            yield distro

    @property
    def name(self):
        """
        Release pipeline stage name.

        Returns
        -------
        str
        """
        return self.__name


class PipelineVirtualStage:

    def __init__(self, name):
        self._name = name
        self._distributions = {}
        self._default_stages = {}

    def _add_distro(self, distro, default_stage):
        self._distributions[distro.name] = {default_stage.name: default_stage}
        self._default_stages[distro.name] = default_stage

    def _add_stage(self, distro, stage):
        self._distributions[distro.name][stage.name] = stage

    def resolve_real_stage(self, db, distro_name, package_name, arch):
        # For i686 packages we don't have rhel repos, which can cause
        # errors in resolve pipeline, we are using x86_64 repos instead.
        arch = arch if arch != 'i686' else 'x86_64'
        for reference in (True, False):
            stage = self.__check_repos(
                db, distro_name, package_name, arch, is_ref=reference)
            if stage:
                return stage
        return self._default_stages[distro_name]

    def __check_repos(self, db, distro_name, package_name, arch, is_ref=False):
        for stage in self._distributions[distro_name].values():
            distro = stage.get_distribution(distro_name)
            repo_iterator = distro.iter_release_repositories
            if is_ref:
                repo_iterator = distro.iter_reference_repositories
            repo_ids = []
            for repo in repo_iterator(arch):
                repo.load_db_data(db)
                repo_ids.append(repo._id)
            for repo in repo_iterator(arch, debuginfo=True):
                repo.load_db_data(db)
                repo_ids.append(repo._id)
            query = {'alt_repo_id': {'$in': repo_ids}, 'name': package_name}
            if db['rpm_packages'].find_one(query):
                return stage


class Product(object):

    """
    Release target (product).
    """

    def __init__(self, data):
        """
        Product initialization.

        Parameters
        ----------
        data : dict
            Product data (see `product_schema` for the schema description).
        """
        self.__id = data.get('_id')
        self.__default = data.get('default', False)
        self.__name = data['name']
        self.__description = data['description']
        self.__pgp_keyid = data['pgp_keyid']
        self.__sign_repodata = data.get('sign_repodata', False)

        slots = data.get('slots', {})
        self.__slots_host = slots.get('host')
        self.__slots_links_directory = slots.get('links_directory')
        self.__slots_systemid_mapping = slots.get('systemid_mapping')
        self.__slost_hybrid_mapping = slots.get('hybrid_mapping')
        self.__slots = {slot['name']: ProductSlot(**slot)
                        for slot in slots.get('instances', [])}

        self.__depends_on = [
            item['name'] for item in data.get('depends_on', [])
        ]

        self.__pipeline = OrderedDict()
        for stage_data in data['pipeline']:
            stage_name = stage_data['stage']
            stage = PipelineStage(stage_name)
            for distro_data in stage_data['distributions']:
                distro_name = distro_data['name']
                distro_base = next((d for d in data['distributions']
                                    if d['name'] == distro_name))
                distro = ProductDistro(**distro_base)
                for arch, repos in distro_data['repositories'].items():
                    for repo_data in repos:
                        repo = DistroRepository(arch=arch, **repo_data)
                        distro._add_repository(repo)
                for arch, repos in distro_data.get(
                        'reference_repositories', {}).items():
                    for repo_data in repos:
                        repo = DistroRepository(arch=arch, **repo_data)
                        distro._add_reference_repository(repo)
                stage._add_distro(distro)
            self.__pipeline[stage_name] = stage

        self.__virtual_pipeline = OrderedDict()
        for stage_data in data.get('virtual_pipeline', []):
            virtual_stage = PipelineVirtualStage(stage_data['name'])
            for distro_data in stage_data['distributions']:
                distro_name = distro_data['name']
                distro_base = next((d for d in data['distributions']
                                    if d['name'] == distro_name))
                distro = ProductDistro(**distro_base)
                virtual_stage._add_distro(
                    distro, self.get_stage(distro_data['default'])
                )
                # TODO: we are making assumption, that all real stages
                #       have same virtual_stage distributions, it's better
                #       to add additional validation for it
                for real_stage in distro_data['stages']:
                    virtual_stage._add_stage(
                        distro, self.get_stage(real_stage)
                    )
            self.__virtual_pipeline[stage_data['name']] = virtual_stage

    def is_virtual_stage(self, stage_name):
        return stage_name in self.__virtual_pipeline

    def depends_on(self, product):
        return product.name in self.__depends_on

    def iter_slots(self):
        for slot in self.__slots.values():
            yield slot

    def get_slot(self, slot_name):
        return self.__slots[slot_name]

    def get_virtual_stage(self, stage_name):
        return self.__virtual_pipeline[stage_name]

    def get_stage(self, stage_name):
        """
        Returns a pipeline stage with the given name.

        Parameters
        ----------
        stage_name : str
            Pipeline stage name.

        Returns
        -------
        PipelineStage
            Pipeline stage.
        """
        return self.__pipeline[stage_name]

    @staticmethod
    def get_from_db(db, _id=None, name=None):
        """
        Finds a product in the database by its _id or name.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        _id : bson.objectid.ObjectId, optional
            Product identifier.
        name : str, optional
            Product name.

        Returns
        -------
        Product or None
            Found product.

        Raises
        ------
        ValueError
            If both _id and name were not provided.
        """
        query = {}
        if _id:
            query['_id'] = _id
        if name:
            query['name'] = name
        if not query:
            raise ValueError('either _id or name is required')
        data = db['products'].find_one(query)
        if data:
            product = Product(data)
            product.load_unusual_repos(db)
            return product

    def load_unusual_repos(self, db):
        """
        Loads additional release rules from cl_recipes.

        Parameters
        ----------
        db : pymongo.database.Database
            Build System MongoDB database.
        """
        query = {'release_info.rules.{0}'.format(self.name): {'$exists': True}}
        for recipe in db['cl_recipes'].find(query):
            for rule in recipe['release_info']['rules'][self.name]:
                rollout_skip = rule.get('skip_in_rollout', False)
                package_query = rule['regex']
                for stage in self.__pipeline.values():
                    for distro in stage.iter_distros():
                        distro.add_custom_release_rule(
                            recipe['_id'], package_query)
                for repo in rule['repos']:
                    pipeline = self.__pipeline.values()
                    if repo.get('stage'):
                        pipeline = [stage for stage in pipeline
                                    if stage.name == repo['stage']]
                    for stage in pipeline:
                        distro = stage.get_distribution(repo['distro'])
                        if not distro:
                            continue
                        repo_rule = {'name': repo['name'],
                                     'debug': repo.get('debuginfo', False),
                                     'rollout_skip': rollout_skip}
                        distro.add_custom_release_repo(
                            recipe['_id'], package_query, repo_rule)

    @property
    def _id(self):
        """
        Product database identifier.

        Returns
        -------
        bson.objectid.ObjectId
        """
        return self.__id

    @property
    def default(self):
        """
        Product is the default release target if True, False otherwise.

        Returns
        -------
        bool
        """
        return self.__default

    @property
    def name(self):
        """
        Product name.

        Returns
        -------
        str
        """
        return self.__name

    @property
    def description(self):
        """
        Product description.

        Returns
        -------
        str
        """
        return self.__description

    @property
    def sign_repodata(self):
        """
        Should repository metadata be signed or not.

        Returns
        -------
        bool
        """
        return self.__sign_repodata

    @property
    def slots_host(self):
        """
        Slots remote hostname.

        Returns
        -------
        str
        """
        return self.__slots_host

    @property
    def slots_systemid_mapping(self):
        """
        Slots systemid mapping file path.

        Returns
        -------
        str
        """
        return self.__slots_systemid_mapping

    @property
    def slots_links_directory(self):
        """
        Slots remote links directory path.

        Returns
        -------
        str
        """
        return self.__slots_links_directory

    @property
    def slots_hybrid_mapping(self):
        """
        Slots remote hybrid mapping file path.

        Returns
        -------
        str
        """
        return self.__slost_hybrid_mapping

    @property
    def pgp_keyid(self):
        """
        Product signing PGP keyid.

        Returns
        -------
        str
        """
        return self.__pgp_keyid


def create_products_index(db):
    """
    Creates a products collection index.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    """
    db['products'].create_index([('name', pymongo.DESCENDING)], unique=True)


def find_product(db, _id=None, name=None):
    """
    Finds a product either by its _id or name.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    _id : bson.objectid.ObjectId, optional
        Product identifier.
    name : str, optional
        Product name.

    Returns
    -------
    Product or None
        Product or None if there is no matching product found.

    Raises
    ------
    ValueError
        If both _id and name were not provided.
    """
    query = {}
    if _id:
        query['_id'] = _id
    if name:
        query['name'] = name
    if not query:
        raise ValueError('either _id or name is required')
    data = db['products'].find_one(query)
    if data:
        return Product(data)


def find_products(db, **query):
    """
    Finds products matching the specified query.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    query
        Product search MongoDB query arguments.

    Returns
    -------
    list

    """
    return [p for p in db['products'].find(query)]


def create_product(db, product):
    """
    Adds a new product to the database.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    product : dict
        Product to add.

    Returns
    -------
    bson.objectid.ObjectId
        Created product _id.

    Raises
    ------
    build_node.errors.DataSchemaError
        If a product data format is invalid.
    """
    verify_schema(product_schema, product)
    return db['products'].insert_one(product).inserted_id


def replace_product(db, product):
    """
    Replaces an existent product record with a new one.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    product : dict
        Updated product document. It should contain the "_id" field.

    Returns
    -------
    dict or None
        Updated product or None if there is no product found.

    Raises
    ------
    build_node.errors.DataNotFound
        IF a product is not found in the database.
    build_node.errors.DataSchemaError
        If a product data format is invalid.
    """
    verify_schema(product_schema, product)
    updated = db['products'].find_one_and_replace(
        {'_id': product['_id']}, product,
        return_document=pymongo.ReturnDocument.AFTER
    )
    if not updated:
        raise DataNotFoundError('product {0} is not found'.
                                format(product['_id']))
    return updated


def create_slot(db, slot):
    """
    Adds a new slot to the database.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    slot : dict
        Slot to add.

    Returns
    -------
    bson.objectid.ObjectId
        Created slot _id.

    Raises
    ------
    build_node.errors.DataSchemaError
        If a slot data format is invalid.
    """
    document = verify_schema(product_slot_schema, slot)
    return db['slots'].insert_one(document).inserted_id


def update_slot(db, slot):
    """
    Updates an existing slot record.

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database.
    slot : dict
        Updated slot document. It should contain the "_id" field.

    Returns
    -------
    dict or None
        Updated slot or None if there is no slot found.

    Raises
    ------
    build_node.errors.DataNotFound
        IF a slot is not found in the database.
    build_node.errors.DataSchemaError
        If a slot data format is invalid.
    """
    document = verify_schema(product_slot_schema, slot)
    updated = db['slots'].find_one_and_update(
        {'_id': document['_id']}, {'$set': document},
        return_document=pymongo.ReturnDocument.AFTER
    )
    if not updated:
        raise DataNotFoundError('slot {0} is not found'.format(
            document['_id']))
    return updated
