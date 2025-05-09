"""
AlmaLinux Build System build thread implementation.
"""

import datetime
import gzip
import logging
import os
import platform
import pprint
import random
import typing
import urllib.parse
from queue import Queue

import requests
import requests.adapters
from albs_build_lib.builder.base_builder import measure_stage
from albs_build_lib.builder.base_thread_slave_builder import BaseSlaveBuilder
from albs_build_lib.builder.models import Task
from albs_common_lib.errors import BuildError, BuildExcluded
from albs_common_lib.utils.file_utils import (
    filter_files,
    rm_sudo,
)
from immudb_wrapper import ImmudbWrapper
from requests.packages.urllib3.util.retry import Retry
from sentry_sdk import capture_exception

from build_node import constants
from build_node.builders.base_rpm_builder import BaseRPMBuilder
from build_node.uploaders.pulp import PulpRpmUploader
from build_node.utils.codenotary import notarize_build_artifacts


class BuildNodeBuilder(BaseSlaveBuilder):
    """Build thread."""

    def __init__(
        self,
        config,
        thread_num,
        terminated_event,
        graceful_terminated_event,
        task_queue: Queue,
    ):
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
        task_queue: queue.Queue
            Shared queue with build tasks
        """
        super().__init__(
            thread_num=thread_num,
        )
        self.__config = config
        # current task processing start timestamp
        self.__start_ts = None
        self.__working_dir = os.path.join(
            config.working_dir, 'builder-{0}'.format(thread_num)
        )
        self.init_working_dir(self.__working_dir)
        self.__terminated_event = terminated_event
        self.__graceful_terminated_event = graceful_terminated_event
        # current task builder object
        self.__builder = None
        self.__session = None
        self.__logger = None
        self.__current_task_id = None
        self._immudb_wrapper = None
        self._codenotary_enabled = self.__config.codenotary_enabled
        self._build_stats: typing.Optional[
            typing.Dict[str, typing.Dict[str, str]]
        ] = None
        self._pulp_uploader = PulpRpmUploader(
            self.__config.pulp_host,
            self.__config.pulp_user,
            self.__config.pulp_password,
            self.__config.pulp_chunk_size,
            self.__config.pulp_uploader_max_workers,
        )
        self.__hostname = platform.node()
        self.__task_queue = task_queue

    def run(self):
        log_file = os.path.join(
            self.__working_dir, 'bt-{0}.log'.format(self.name)
        )
        self.__logger = self.init_thread_logger(log_file)
        if self._codenotary_enabled:
            self._immudb_wrapper = ImmudbWrapper(
                username=self.__config.immudb_username,
                password=self.__config.immudb_password,
                database=self.__config.immudb_database,
                immudb_address=self.__config.immudb_address,
                public_key_file=self.__config.immudb_public_key_file,
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
            self._build_stats = {}
            self.__current_task_id = task.id
            self.__start_ts = datetime.datetime.utcnow()
            ts = int(self.__start_ts.timestamp())
            task_dir = os.path.join(self.__working_dir, str(task.id))
            artifacts_dir = os.path.join(task_dir, 'artifacts')
            task_log_file = os.path.join(task_dir, f'albs.{task.id}.{ts}.log')
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
                only_logs = not (
                    bool(
                        filter_files(
                            artifacts_dir, lambda f: f.endswith('.rpm')
                        )
                    )
                )
                if success is False:
                    only_logs = True
                notarized_artifacts = {}
                non_notarized_artifacts = []
                if self._codenotary_enabled:
                    try:
                        (
                            notarized_artifacts,
                            non_notarized_artifacts,
                        ) = self.__cas_notarize_artifacts(
                            task,
                            artifacts_dir,
                        )
                    except Exception:
                        success = False
                        only_logs = True
                        self.__logger.exception('Cannot notarize artifacts:')
                    self.__logger.debug(
                        'List of notarized and not notarized artifacts:\n%s\n%s',
                        pprint.pformat(notarized_artifacts),
                        pprint.pformat(non_notarized_artifacts),
                    )
                    if non_notarized_artifacts:
                        only_logs = True
                        success = False
                        self.__logger.error(
                            'Cannot notarize following artifacts:\n%s',
                            pprint.pformat(non_notarized_artifacts),
                        )
                build_artifacts = []
                try:
                    build_artifacts = self.__upload_artifacts(
                        artifacts_dir, only_logs=only_logs
                    )
                except Exception:
                    self.__logger.exception('Cannot upload task artifacts')
                    build_artifacts = []
                    success = False
                finally:
                    try:
                        build_artifacts.append(
                            self._pulp_uploader.upload_single_file(
                                task_log_file
                            )
                        )
                    except Exception as e:
                        self.__logger.exception(
                            'Cannot upload task log file: %s', str(e)
                        )

                for artifact in build_artifacts:
                    artifact.cas_hash = notarized_artifacts.get(artifact.path)

                end_ts = datetime.datetime.utcnow()
                delta = end_ts - self.__start_ts
                self._build_stats.update({
                    "build_node_task": {
                        "start_ts": str(self.__start_ts),
                        "end_ts": str(end_ts),
                        "delta": str(delta),
                    },
                    **self.__builder.get_build_stats(),
                })
                try:
                    if not success and excluded:
                        self.__report_excluded_task(task, build_artifacts)
                    else:
                        self.__report_done_task(
                            task,
                            success=success,
                            artifacts=build_artifacts,
                        )
                except Exception:
                    self.__logger.exception(
                        'Cannot report task status to the main node'
                    )
                if task_log_handler:
                    self.__close_task_logger(task_log_handler)
                self.__current_task_id = None
                self.__start_ts = None
                self._build_stats = None
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
                self.__task_queue.task_done()

    @measure_stage("cas_notarize_artifacts")
    def __cas_notarize_artifacts(
        self,
        task: Task,
        artifacts_dir: str,
    ) -> typing.Tuple[typing.Dict[str, str], typing.List[str]]:
        return notarize_build_artifacts(
            task=task,
            artifacts_dir=artifacts_dir,
            immudb_client=self._immudb_wrapper,
            build_host=self.__hostname,
            logger=self.__logger,
        )

    def __build_packages(self, task, task_dir, artifacts_dir):
        """
        Creates a suitable builder instance and builds RPM packages.

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
        self.__builder = BaseRPMBuilder(
            self.__config,
            self.__logger,
            task,
            task_dir,
            artifacts_dir,
            self._immudb_wrapper,
        )
        self.__builder.build()

    @measure_stage("upload")
    def __upload_artifacts(self, artifacts_dir, only_logs: bool = False):
        artifacts = self._pulp_uploader.upload(
            artifacts_dir, only_logs=only_logs
        )
        return artifacts

    def __request_task(self):
        if self.__task_queue.empty():
            return
        task = self.__task_queue.get()
        return Task(**task)

    def __report_excluded_task(self, task, artifacts):
        kwargs = {
            'task_id': task.id,
            'status': 'excluded',
            'artifacts': [artifact.dict() for artifact in artifacts],
            'stats': self._build_stats,
            'is_cas_authenticated': task.is_cas_authenticated,
            'git_commit_hash': task.ref.git_commit_hash,
            'alma_commit_cas_hash': task.alma_commit_cas_hash,
        }
        self.__call_master(
            'build_done',
            err_msg="Can't mark the task as excluded:",
            **kwargs,
        )

    def __report_done_task(
        self,
        task,
        success=True,
        artifacts=None,
    ):
        if not artifacts:
            artifacts = []
        kwargs = {
            'task_id': task.id,
            'status': 'done' if success else 'failed',
            'artifacts': [artifact.dict() for artifact in artifacts],
            'stats': self._build_stats,
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
            allowed_methods=constants.METHODS_TO_RETRY,
            backoff_factor=constants.BACKOFF_FACTOR,
            raise_on_status=True,
        )
        adapter = requests.adapters.HTTPAdapter(max_retries=retry_strategy)
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
            self.__config.master_url,
            f'build_node/{endpoint}',
        )
        if endpoint in ('build_done', 'get_task'):
            session_method = self.__session.post
        else:
            session_method = self.__session.get
        try:
            response = session_method(
                full_url,
                json=parameters,
                timeout=self.__config.request_timeout,
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
        handler = logging.StreamHandler(
            gzip.open(log_file, 'wt', encoding='utf-8'),
        )
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-8s]: %(message)s", "%H:%M:%S %d.%m.%y"
        )
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

    @property
    def current_task_id(self):
        return self.__current_task_id
