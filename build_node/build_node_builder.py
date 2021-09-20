# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-10-19

"""
CloudLinux Build System build thread implementation.
"""

import datetime
import logging
import os
import urllib
import platform
import random
import threading
import traceback

import yaml
import requests

from build_node.builders import get_suitable_builder
from build_node.build_node_errors import BuildError, BuildExcluded
from build_node.uploaders.pulp import PulpRpmUploader
from build_node.utils.file_utils import clean_dir, rm_sudo
from build_node.models import Task
from build_node.utils.sentry_utils import Sentry


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
        self.__sentry = Sentry(config.sentry_dsn)
        # current task builder object
        self.__builder = None
        self._pulp_uploader = PulpRpmUploader(
            self.__config.pulp_host, self.__config.pulp_user,
            self.__config.pulp_password, self.__config.pulp_chunk_size
        )

        self.__terminated_event = terminated_event
        self.__graceful_terminated_event = graceful_terminated_event

    def run(self):
        log_file = os.path.join(self.__working_dir,
                                'bt-{0}.log'.format(self.name))
        self.__logger = self.init_thread_logger(log_file)
        self.__logger.info('starting {0}'.format(self.name))
        while not self.__graceful_terminated_event.is_set():
            task = self.__request_task()
            if not task:
                self.__logger.debug('there are no tasks to process')
                self.__terminated_event.wait(random.randint(5, 10))
                continue
            self.__current_task_id = task.id
            self.__start_ts = datetime.datetime.utcnow()
            task_dir = os.path.join(self.__working_dir, str(task.id))
            artifacts_dir = os.path.join(task_dir, 'artifacts')
            task_log_file = os.path.join(task_dir, 'albs.log')
            task_log_handler = None
            success = False
            excluded = False
            excluded_exception = None
            build_artifacts = []
            try:
                self.__logger.info(f'processing the task:\n{task}')
                os.makedirs(artifacts_dir)
                task_log_handler = self.__init_task_logger(task_log_file)
                self.__build_packages(task, task_dir, artifacts_dir)
                build_artifacts = self.__upload_artifacts(
                    task, artifacts_dir, task_log_file)
                success = True
            except BuildError as e:
                self.__logger.error('task {0} build failed: {1}'.
                                    format(task.id, str(e)))
            except BuildExcluded as ee:
                excluded_exception = ee
                excluded = True
                self.__logger.info(
                    'task {0} build excluded: {1}'.format(
                        task.id, str(excluded_exception)))
            except Exception as e:
                self.__logger.error('task {0} build failed: {1}. '
                                    'Traceback:\n{2}'.
                                    format(task.id, str(e),
                                           traceback.format_exc()))
                self.__sentry.capture_exception(e)
            finally:
                if not success:
                    try:
                        build_artifacts = self.__upload_artifacts(
                            task, artifacts_dir, task_log_file)
                        if excluded_exception is not None:
                            self.__report_excluded_task(
                                task, str(excluded_exception))
                    except Exception as e:
                        self.__logger.error(
                            'Cannot upload task artifacts: {0}. '
                            'Traceback:\n{1}'.format(
                                str(e), traceback.format_exc()))
                        self.__sentry.capture_exception(e)
                if not excluded:
                    self.__report_done_task(
                        task, success=success, artifacts=build_artifacts)
                if task_log_handler:
                    self.__close_task_logger(task_log_handler)
                self.__current_task_id = None
                self.__start_ts = None
                if os.path.exists(task_dir):
                    self.__logger.debug('cleaning up task build directory'
                                        ' {0}'.format(task_dir))
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
        task : dict
            Build task information.
        task_dir : str
            Build task working directory path.
        artifacts_dir : str
            Build artifacts storage directory path.
        """
        self.__logger.info('building on the {0} node'.format(platform.node()))
        builder_class = get_suitable_builder(task)
        self.__builder = builder_class(self.__config, self.__logger, task,
                                       task_dir, artifacts_dir)
        self.__builder.build()

    def __upload_artifacts(self, task, artifacts_dir, task_log_file):
        artifacts = self._pulp_uploader.upload(artifacts_dir)
        build_stats = self.__builder.get_build_stats()
        build_stats_path = os.path.join(artifacts_dir, 'build_stats.yml')
        with open(build_stats_path, 'w') as fd:
            fd.write(yaml.dump(build_stats))
        artifacts.append(
            self._pulp_uploader.upload_single_file(build_stats_path)
        )
        artifacts.append(
            self._pulp_uploader.upload_single_file(task_log_file)
        )
        return artifacts

    def __request_task(self):
        supported_arches = [self.__config.base_arch]
        if self.__config.base_arch == 'x86_64':
            supported_arches.append('i686')
        task = None
        try:
            task = self.__call_master(
                'get_task', supported_arches=supported_arches
            )
        except Exception:
            self.__logger.error(
                f"Can't request new task from master:\n"
                f"{traceback.format_exc()}"
            )
        if not task:
            return
        return Task(**task)

    def __report_excluded_task(self, task, reason):
        kwargs = {'task_id': task.id, 'reason': reason}
        self.__call_master('build_excluded', **kwargs)

    def __report_done_task(self, task, success=True, artifacts=None):
        if not artifacts:
            artifacts = []
        kwargs = {
            'task_id': task.id,
            'success': success,
            'artifacts': [artifact.dict() for artifact in artifacts]
        }
        self.__call_master('build_done', **kwargs)

    def __call_master(self, endpoint, **parameters):
        full_url = urllib.parse.urljoin(
            self.__config.master_url, f'build_node/{endpoint}')
        headers = {'authorization': f'Bearer {self.__config.jwt_token}'}
        if endpoint == 'build_done':
            response = requests.post(
                full_url,
                json=parameters,
                headers=headers
            )
        else:
            response = requests.get(full_url, json=parameters, headers=headers)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def init_working_dir(working_dir):
        """
        Creates a non-existent working directory or cleans it up from previous
        builds.
        """
        if os.path.exists(working_dir):
            logging.debug('cleaning the {0} working directory'.
                          format(working_dir))
            clean_dir(working_dir)
        else:
            logging.debug('creating the {0} working directory'.
                          format(working_dir))
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
