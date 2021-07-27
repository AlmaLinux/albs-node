# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-10-19

"""
CloudLinux Build System build thread implementation.
"""

import datetime
import hashlib
import logging
import math
import os
import platform
import pprint
import random
import threading
import traceback

import yaml
import zmq

from build_node.builders import get_suitable_builder
from build_node.build_node_errors import BuildError, BuildExcluded
from build_node.uploaders.pulp import PulpRpmUploader
from build_node.utils.file_utils import clean_dir, rm_sudo
from build_node.utils.sentry_utils import Sentry
from build_node.utils.zmq_utils import setup_client_socket, DealerRepCommunicator


class BuildNodeBuilder(threading.Thread):

    """Build thread."""

    def __init__(self, zmq_context, config, thread_num, terminated_event,
                 graceful_terminated_event):
        """
        Build thread initialization.

        Parameters
        ----------
        zmq_context : zmq.Context
            ZeroMQ context.
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
        self.__zmq_context = zmq_context
        self.__config = config
        self.__working_dir = os.path.join(config.working_dir,
                                          'builder-{0}'.format(thread_num))
        self.init_working_dir(self.__working_dir)
        # noinspection PyUnresolvedReferences
        self._msg_exchanger = DealerRepCommunicator(
            zmq_context.socket(zmq.DEALER))
        setup_client_socket(self._msg_exchanger.socket,
                            config.private_key_path, config.master_key_path)
        # noinspection PyUnresolvedReferences
        self._msg_exchanger.socket.setsockopt(zmq.LINGER, 0)
        self.__logger = None
        self.__current_task_id = None
        # current task processing start timestamp
        self.__start_ts = None
        self.__sentry = Sentry(config.sentry_dsn)
        # current task builder object
        self.__builder = None
        self._uploader = PulpRpmUploader(
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
        self._msg_exchanger.connect(self.__config.master_url)
        try:
            while not self.__graceful_terminated_event.is_set():
                task = self.__request_task()
                if not task:
                    self.__logger.debug('there are no tasks to process')
                    self.__terminated_event.wait(random.randint(5, 10))
                    continue
                self.__current_task_id = task['id']
                self.__start_ts = datetime.datetime.utcnow()
                task_dir = os.path.join(self.__working_dir, task['id'])
                artifacts_dir = os.path.join(task_dir, 'artifacts')
                task_log_file = os.path.join(task_dir, 'alt.log')
                task_log_handler = None
                success = False
                excluded = False
                excluded_exception = None
                try:
                    self.__logger.info('processing the {0} task:\n{1}'.format(
                        task['id'], pprint.pformat(task)))
                    os.makedirs(artifacts_dir)
                    task_log_handler = self.__init_task_logger(task_log_file)
                    self.__build_packages(task, task_dir, artifacts_dir)
                    self.__upload_artifacts(task, artifacts_dir, task_log_file)
                    success = True
                except BuildError as e:
                    self.__logger.error('task {0} build failed: {1}'.
                                        format(task['id'], str(e)))
                except BuildExcluded as ee:
                    excluded_exception = ee
                    excluded = True
                    self.__logger.info(
                        'task {0} build excluded: {1}'.format(
                            task['id'], str(excluded_exception)))
                except Exception as e:
                    self.__logger.error('task {0} build failed: {1}. '
                                        'Traceback:\n{2}'.
                                        format(task['id'], str(e),
                                               traceback.format_exc()))
                    self.__sentry.capture_exception(e)
                finally:
                    if not success:
                        try:
                            self.__upload_artifacts(
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
                        self.__report_done_task(task, success=success)
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
        except zmq.ContextTerminated:
            self.__logger.info('{0} stopped: ZeroMQ context terminated'.
                               format(self.name))
        finally:
            self._msg_exchanger.close()

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
        self._uploader.upload(artifacts_dir)
        # TODO: Upload logs to S3
        build_stats = self.__builder.get_build_stats()
        start_time = datetime.datetime.utcnow()
        for file_name in os.listdir(artifacts_dir):
            if file_name.endswith('.log'):
                self.__upload_artifact(
                    task, os.path.join(artifacts_dir, file_name))
        end_time = datetime.datetime.utcnow()
        build_stats['upload'] = {'start_ts': start_time, 'end_ts': end_time}
        build_stats['total'] = {'start_ts': self.__start_ts,
                                'end_ts': end_time}
        build_stats_path = os.path.join(artifacts_dir, 'build_stats.yml')
        with open(build_stats_path, 'w') as fd:
            fd.write(yaml.dump(build_stats))
        self.__upload_artifact(task, build_stats_path)
        self.__upload_artifact(task, task_log_file)

    def __upload_artifact(self, task, file_path, chunk_size=4194304):
        """
        Sends file through a ZeroMQ socket.

        Parameters
        ----------
        task : dict
            Build task.
        file_path : str
            Artifact file path.
        chunk_size : int
            Chunk size in bytes, 4MB seems to be a reasonable default since
            only 9% of RPMs (including debuginfo) has greater size.
        """
        self.__logger.info('uploading {0} build artifact'.
                           format(os.path.split(file_path)[1]))
        file_name = os.path.split(file_path)[1]
        total_chunks = int(math.ceil(os.stat(file_path).st_size /
                                     (chunk_size * 1.0)))
        hasher = hashlib.sha256()
        with open(file_path, 'rb') as fd:
            for chunk_number in range(total_chunks):
                chunk = fd.read(chunk_size)
                hasher.update(chunk)
                parameters = {'task_id': task['id'],
                              'file_name': file_name,
                              'chunk_number': chunk_number,
                              'total_chunks': total_chunks,
                              'chunk': chunk}
                if chunk_number == total_chunks - 1:
                    parameters['checksum'] = hasher.hexdigest()
                    parameters['checksum_type'] = 'sha256'
                response = self.__call_master('upload_build_artifact',
                                              **parameters)
                if not response['success']:
                    error = response.get('error', 'unknown')
                    # TODO: raise something more specific which we can handle
                    #       the right way and retry the upload
                    msg = ('{file_path} artifact upload failed '
                           '({chunk_number} of {total_chunks}): '
                           '{error}')
                    raise Exception(msg.format(file_path=file_path,
                                               chunk_number=chunk_number,
                                               total_chunks=total_chunks,
                                               error=error))

    def __request_task(self):
        response = self.__call_master('get_task')
        if not response['success']:
            return {}
        return response.get('task')

    def __report_excluded_task(self, task, reason):
        kwargs = {'task_id': task['id'], 'reason': reason}
        if self.__builder.created_tag:
            kwargs['created_tag'] = self.__builder.created_tag
        response = self.__call_master('build_excluded', **kwargs)
        if not response['success']:
            logging.error('can\'t report excluded task {0}: {1}'.
                          format(task['id'], response.get('error',
                                                          'unknown error')))

    def __report_done_task(self, task, success=True):
        kwargs = {'task_id': task['id'], 'success': success}
        if self.__builder.created_tag:
            kwargs['created_tag'] = self.__builder.created_tag
        response = self.__call_master('build_done', **kwargs)
        if not response['success']:
            logging.error('can\'t report completed task {0}: {1}'.
                          format(task['id'], response.get('error',
                                                          'unknown error')))

    def __call_master(self, endpoint, **parameters):
        request = {
            'node_id': self.__config.node_id,
            'endpoint': endpoint,
            'parameters': parameters
        }
        self._msg_exchanger.send(request)
        response = self._msg_exchanger.recv()
        if not isinstance(response, dict):
            return {'success': False, 'error': 'Message should be dictionary'}
        elif not response:
            return {'success': False, 'error': 'Empty message'}
        elif 'success' not in response:
            return {'success': False, 'error': 'Unknown message format'}
        return response

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
