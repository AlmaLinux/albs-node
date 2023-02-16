# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-10-19

"""
CloudLinux Build System build thread implementation.
"""

import datetime
import gzip
import logging
import os
import time
import urllib.parse
import platform
import pprint
import random
import threading
import typing

from cas_wrapper import CasWrapper
import yaml
import requests
import requests.adapters
from requests.packages.urllib3.util.retry import Retry
from sentry_sdk import capture_exception

from build_node import constants
from build_node.builders import get_suitable_builder
from build_node.build_node_errors import BuildError, BuildExcluded
from build_node.uploaders.pulp import PulpRpmUploader
from build_node.utils.file_utils import (
    clean_dir,
    filter_files,
    rm_sudo,
)
from build_node.models import Task
from build_node.utils.codenotary import notarize_build_artifacts


class BuildNodeBuilder(threading.Thread):

    """Build thread."""

    def __init__(self, config, thread_num, terminated_event,
                 graceful_terminated_event):
        """
        Build thread initialization.

        Parameters
        ----------
        config : build_node.build_node_config.BuildNodeConfig
            Build node configuration object.
        thread_num : int
            Number of a build thread to construct a "unique" name.
        terminated_event : threading.Event
            Shows, if process got "kill -15" signal.
        graceful_terminated_event : threading.Event
            Shows, if process got "kill -10" signal.
        """
        super(BuildNodeBuilder, self).__init__(name='Builder-{0}'.format(
            thread_num))
        self.__config = config
        self.__working_dir = os.path.join(config.working_dir,
                                          'builder-{0}'.format(thread_num))
        self.init_working_dir(self.__working_dir)
        self.__logger = None
        self.__current_task_id = None
        # current task processing start timestamp
        self.__start_ts = None
        # current task builder object
        self.__builder = None
        self.__session = None
        self._cas_wrapper = None
        self._codenotary_enabled = self.__config.codenotary_enabled
        self._pulp_uploader = PulpRpmUploader(
            self.__config.pulp_host, self.__config.pulp_user,
            self.__config.pulp_password, self.__config.pulp_chunk_size,
            self.__config.pulp_uploader_max_workers
        )

        self.__terminated_event = terminated_event
        self.__graceful_terminated_event = graceful_terminated_event
        self.__hostname = platform.node()

    def run(self):
        log_file = os.path.join(self.__working_dir,
                                'bt-{0}.log'.format(self.name))
        self.__logger = self.init_thread_logger(log_file)
        if self._codenotary_enabled:
            self._cas_wrapper = CasWrapper(
                cas_api_key=self.__config.cas_api_key,
                cas_signer_id=self.__config.cas_signer_id,
                logger=self.__logger,
            )
        self.__logger.info('starting %s', self.name)
        self.__generate_request_session()
        while not self.__graceful_terminated_event.is_set():
            task = self.__request_task()
            if not task:
                self.__logger.debug('there are no tasks to process')
                self.__terminated_event.wait(random.randint(5, 10))
                continue
            self.__current_task_id = task.id
            self.__start_ts = datetime.datetime.utcnow()
            ts = int(self.__start_ts.timestamp())
            task_dir = os.path.join(self.__working_dir, str(task.id))
            artifacts_dir = os.path.join(task_dir, 'artifacts')
            task_log_file = os.path.join(task_dir, f'albs.{ts}.log')
            task_log_handler = None
            success = False
            excluded = False
            try:
                self.__logger.info('processing the task:\n%s', task)
                os.makedirs(artifacts_dir)
                task_log_handler = self.__init_task_logger(task_log_file)
                self.__build_packages(task, task_dir, artifacts_dir)
                success = True
            except BuildError:
                self.__logger.exception(
                    'task %i build failed',
                    task.id,
                )
            except BuildExcluded:
                excluded = True
                self.__logger.info(
                    'task %i build excluded',
                    task.id,
                )
            except Exception as e:
                self.__logger.exception(
                    'task %i build failed',
                    task.id,
                )
                capture_exception(e)
            finally:
                only_logs = (not (bool(filter_files(
                    artifacts_dir, lambda f: f.endswith('.rpm')))))
                notarized_artifacts = {}
                if self._codenotary_enabled:
                    (
                        notarized_artifacts,
                        non_notarized_artifacts,
                    ) = notarize_build_artifacts(
                        task,
                        artifacts_dir,
                        self._cas_wrapper,
                        self.__hostname,
                    )
                    self.__logger.debug(
                        'List of notarized and not notarized artifacts:')
                    self.__logger.debug(pprint.pformat(notarized_artifacts))
                    self.__logger.debug(pprint.pformat(non_notarized_artifacts))
                    if non_notarized_artifacts:
                        only_logs = True
                        success = False
                        self.__logger.error(
                            'Cannot notarize following artifacts:\n%s',
                            '\n'.join(non_notarized_artifacts),
                        )
                build_artifacts = []
                try:
                    build_artifacts = self.__upload_artifacts(
                        artifacts_dir, only_logs=only_logs)
                except Exception:
                    self.__logger.exception('Cannot upload task artifacts')
                    build_artifacts = []
                    success = False
                finally:
                    with open(task_log_file, 'rb+') as src:
                        src.write(gzip.compress(src.read()))
                    build_artifacts.append(
                        self._pulp_uploader.upload_single_file(task_log_file)
                    )

                for artifact in build_artifacts:
                    artifact.cas_hash = notarized_artifacts.get(artifact.path)

                try:
                    if not success and excluded:
                        self.__report_excluded_task(
                            task, build_artifacts)
                    else:
                        self.__report_done_task(
                            task, success=success, artifacts=build_artifacts)
                except Exception:
                    self.__logger.exception(
                        'Cannot report task status to the main node')
                if task_log_handler:
                    self.__close_task_logger(task_log_handler)
                self.__current_task_id = None
                self.__start_ts = None
                if os.path.exists(task_dir):
                    self.__logger.debug(
                        'cleaning up task build directory %s',
                        task_dir,
                    )
                    # NOTE: sometimes source files have weird permissions
                    #       which makes their deletion merely impossible
                    #       without root permissions
                    rm_sudo(task_dir)
                self.__builder = None

    def __build_packages(self, task, task_dir, artifacts_dir):
        """
        Creates a suitable builder instance and builds RPM or Debian packages.

        Parameters
        ----------
        task : Task
            Build task information.
        task_dir : str
            Build task working directory path.
        artifacts_dir : str
            Build artifacts storage directory path.
        """
        self.__logger.info('building on the %s node', platform.node())
        builder_class = get_suitable_builder(task)
        self.__builder = builder_class(self.__config, self.__logger, task,
                                       task_dir, artifacts_dir,
                                       self._cas_wrapper)
        self.__builder.build()

    def __upload_artifacts(self, artifacts_dir,
                           only_logs: bool = False):
        artifacts = self._pulp_uploader.upload(
            artifacts_dir, only_logs=only_logs)
        build_stats = self.__builder.get_build_stats()
        timestamp = time.time()
        build_stats_path = os.path.join(
            artifacts_dir, f'build_stats_{timestamp}.yml')
        with open(build_stats_path, 'w') as fd:
            fd.write(yaml.dump(build_stats))
        artifacts.append(
            self._pulp_uploader.upload_single_file(build_stats_path)
        )
        return artifacts

    def __request_task(self):
        supported_arches = [self.__config.base_arch]
        if self.__config.base_arch == 'x86_64':
            supported_arches.append('i686')
        task = self.__call_master(
            'get_task',
            err_msg="Can't request new task from master:",
            supported_arches=supported_arches,
        )
        if not task:
            return
        if not task.get('is_secure_boot'):
            task['is_secure_boot'] = False
        return Task(**task)

    def __report_excluded_task(self, task, artifacts):
        kwargs = {
            'task_id': task.id,
            'status': 'excluded',
            'artifacts': [artifact.dict() for artifact in artifacts],
            'is_cas_authenticated': task.is_cas_authenticated,
            'git_commit_hash': task.ref.git_commit_hash,
            'alma_commit_cas_hash': task.alma_commit_cas_hash,
        }
        self.__call_master(
            'build_done',
            err_msg="Can't mark the task as excluded:",
            **kwargs,
        )

    def __report_done_task(self, task, success=True, artifacts=None):
        if not artifacts:
            artifacts = []
        kwargs = {
            'task_id': task.id,
            'status': 'done' if success else 'failed',
            'artifacts': [artifact.dict() for artifact in artifacts],
            'is_cas_authenticated': task.is_cas_authenticated,
            'git_commit_hash': task.ref.git_commit_hash,
            'alma_commit_cas_hash': task.alma_commit_cas_hash,
        }
        self.__call_master(
            'build_done',
            err_msg="Can't mark the task as done:",
            **kwargs,
        )

    def __generate_request_session(self):
        retry_strategy = Retry(
            total=constants.TOTAL_RETRIES,
            status_forcelist=constants.STATUSES_TO_RETRY,
            method_whitelist=constants.METHODS_TO_RETRY,
            backoff_factor=constants.BACKOFF_FACTOR,
            raise_on_status=True,
        )
        adapter = requests.adapters.HTTPAdapter(
            max_retries=retry_strategy)
        self.__session = requests.Session()
        self.__session.headers.update({
            'Authorization': f'Bearer {self.__config.jwt_token}',
        })
        self.__session.mount('http://', adapter)
        self.__session.mount('https://', adapter)

    def __call_master(
            self,
            endpoint,
            err_msg: typing.Optional[str] = '',
            **parameters,
    ):
        full_url = urllib.parse.urljoin(
            self.__config.master_url, f'build_node/{endpoint}')
        if endpoint == 'build_done':
            session_method = self.__session.post
        else:
            session_method = self.__session.get
        try:
            response = session_method(
                full_url, json=parameters,
                timeout=self.__config.request_timeout
            )
            # Special case when build was already done
            if response.status_code == requests.codes.conflict:
                return {}
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RetryError:
            self.__logger.exception('Max retries exceeded. %s', err_msg)
        except Exception:
            self.__logger.exception('%s', err_msg)

    @staticmethod
    def init_working_dir(working_dir):
        """
        Creates a non-existent working directory or cleans it up from previous
        builds.
        """
        if os.path.exists(working_dir):
            logging.debug('cleaning the %s working directory',
                          working_dir)
            clean_dir(working_dir)
        else:
            logging.debug('creating the %s working directory',
                          working_dir)
            os.makedirs(working_dir, 0o750)

    def __init_task_logger(self, log_file):
        """
        Task logger initialization, configures a build thread logger to write
        output to the given log file.

        Parameters
        ----------
        log_file : str
            Task log file path.

        Returns
        -------
        logging.Handler
            Task logging handler.
        """
        handler = logging.FileHandler(log_file)
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s %(levelname)-8s]: "
                                      "%(message)s", "%H:%M:%S %d.%m.%y")
        handler.setFormatter(formatter)
        self.__logger.addHandler(handler)
        return handler

    def __close_task_logger(self, task_handler):
        """
        Closes the specified task log handler and removes it from the current
        thread logger.

        Parameters
        ----------
        task_handler : logging.Handler
            Task log handler.
        """
        task_handler.flush()
        task_handler.close()
        self.__logger.handlers.remove(task_handler)

    @staticmethod
    def init_thread_logger(log_file):
        """
        Build thread logger initialization.

        Parameters
        ----------
        log_file : str
            Log file path.

        Returns
        -------
        logging.Logger
            Build thread logger.
        """
        logger = logging.getLogger('bt-{0}-logger'.
                                   format(threading.current_thread().name))
        logger.handlers = []
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s %(levelname)-8s: "
                                      "%(message)s",
                                      "%H:%M:%S %d.%m.%y")
        handler = logging.FileHandler(log_file)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    @property
    def current_task_id(self):
        return self.__current_task_id
