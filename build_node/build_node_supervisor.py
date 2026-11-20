import logging
import random
import traceback
import typing
import urllib.parse
from pathlib import Path
from queue import Queue

import requests
import requests.adapters
from cachetools import TTLCache
from albs_build_lib.builder.base_supervisor import BaseSupervisor
from albs_common_lib.utils.file_utils import file_url_exists
from urllib3 import Retry

from build_node import constants
from build_node.utils.node_status import (
    collect_node_resources,
    get_node_info,
    get_supported_arches,
)


class BuilderSupervisor(BaseSupervisor):

    def __init__(
        self,
        config,
        builders,
        terminated_event,
        task_queue: Queue,
        previous_instance_id: typing.Optional[str] = None,
    ):
        self.__session = None
        self.__previous_instance_id = previous_instance_id
        self.__task_session = None
        self.__task_queue = task_queue
        self.__cached_config = TTLCache(
            maxsize=config.cache_size,
            ttl=config.cache_update_interval,
        )
        super().__init__(
            config=config,
            builders=builders,
            terminated_event=terminated_event,
        )

    def __new_request_session(self, **retry_options):
        retry_strategy = Retry(
            total=constants.TOTAL_RETRIES,
            status_forcelist=constants.STATUSES_TO_RETRY,
            backoff_factor=constants.BACKOFF_FACTOR,
            raise_on_status=True,
            **retry_options,
        )
        adapter = requests.adapters.HTTPAdapter(max_retries=retry_strategy)
        session = requests.Session()
        session.headers.update({
            'Authorization': f'Bearer {self.config.jwt_token}',
        })
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        return session

    def __generate_request_session(self):
        self.__session = self.__new_request_session(
            allowed_methods=constants.METHODS_TO_RETRY,
        )
        # get_task must not be repeated once it could reach the server,
        # see GET_TASK_RETRY_OPTIONS
        self.__task_session = self.__new_request_session(
            **constants.GET_TASK_RETRY_OPTIONS,
        )

    def get_active_tasks(self):
        """
        Returns identifiers of the tasks assigned to the node: waiting in the
        local queue for a free builder and being built.

        The task of a dead builder is not reported: it is never going to be
        finished, and the server returns it to the queue only if the node
        stops reporting it.
        """
        # The queue is read before the builders: a builder takes a task from
        # the queue and sets its current_task_id right after that, so the
        # task is missed only if both reads fall between these two steps. A
        # task seen in both places is reported once.
        # Queue.mutex guards the underlying deque of waiting tasks
        with self.__task_queue.mutex:
            queued_tasks = list(self.__task_queue.queue)
        active_tasks = {
            task['id'] for task in queued_tasks if task.get('id') is not None
        }
        active_tasks.update(
            builder.current_task_id
            for builder in self.builders
            if builder.is_alive() and builder.current_task_id is not None
        )
        return active_tasks

    def __request_build_task(self):
        if self.__task_queue.unfinished_tasks >= self.config.threads_count:
            return {}
        excluded_packages = self.get_excluded_packages()
        full_url = urllib.parse.urljoin(
            self.config.master_url, 'build_node/get_task'
        )
        try:
            data = {
                'supported_arches': get_supported_arches(self.config),
                'excluded_packages': excluded_packages,
                'node_name': self.config.build_node_name,
                'node_info': get_node_info(
                    self.config, self.__previous_instance_id
                ),
            }
            response = self.__task_session.post(
                full_url,
                json=data,
                timeout=self.config.request_timeout,
            )
            response.raise_for_status()
            # The server answers null when the node should not take a task
            # now, even if there are tasks in the queue
            return response.json() or {}
        except Exception:
            logging.exception(
                "Failed to request build task from master:",
            )
            return {}

    def __report_active_tasks(self):
        full_url = urllib.parse.urljoin(
            self.config.master_url, 'build_node/ping'
        )
        # Building the request is inside the try as well: a failure to
        # describe the node must not kill the supervisor
        try:
            active_tasks = self.get_active_tasks()
            logging.debug('Sending active tasks: %s', active_tasks)
            data = {
                'active_tasks': sorted(int(item) for item in active_tasks),
                'node_name': self.config.build_node_name,
                'node_info': get_node_info(
                    self.config, self.__previous_instance_id
                ),
                'resources': collect_node_resources(self.config.working_dir),
            }
            response = self.__session.post(
                full_url,
                json=data,
                timeout=self.config.request_timeout,
            )
            response.raise_for_status()
        except requests.HTTPError as error:
            # Without the ping the server does not see the node alive and
            # gives its tasks to other nodes, so a rejected ping must be
            # visible in the log
            logging.warning(
                'Build server rejected ping: HTTP %s: %s',
                error.response.status_code,
                error.response.text[:constants.ERROR_BODY_LOG_LIMIT],
            )
        except Exception:
            logging.error(
                "Can't report active task to master:\n%s",
                traceback.format_exc(),
            )

    def get_excluded_packages(self):
        if 'excluded_packages' not in self.__cached_config:
            uri = urllib.parse.urljoin(
                self.config.exclusions_url,
                self.config.build_node_name,
            )
            if file_url_exists(uri):
                try:
                    response = requests.get(
                        uri,
                        timeout=self.config.request_timeout,
                    )
                    response.raise_for_status()
                    self.__cached_config['excluded_packages'] = (
                        response.text.splitlines()
                    )
                except Exception:
                    logging.exception('Cannot get excluded packages')
                    return []

        excluded_packages = self.__cached_config.get(
            'excluded_packages', []
        )
        logging.debug('Excluded packages in this node: %s', excluded_packages)
        return excluded_packages

    def run(self):
        self.__generate_request_session()
        while not self.terminated_event.is_set():
            builders_aliveness = [t.is_alive() for t in self.builders]
            logging.debug('Builders aliveness: %s', str(builders_aliveness))
            if not any(builders_aliveness):
                logging.warning('All builders are dead, exiting')
                break
            self.__report_active_tasks()

            if Path(self.config.maintenance_mode_file).exists():
                logging.warning(
                    'maintenance mode file detected at "%s". '
                    'not taking new tasks, sleeping for 30s',
                    self.config.maintenance_mode_file
                )
                self.terminated_event.wait(30)
                continue

            task = self.__request_build_task()
            if task:
                if not task.get('is_secure_boot'):
                    task['is_secure_boot'] = False
                self.__task_queue.put(task)
                # Do not grab every free slot at once, let other nodes
                # ask for tasks as well
                pause = random.uniform(*constants.TASK_CLAIM_PAUSE_RANGE)
                logging.debug(
                    'got task %s, sleeping for %.1fs', task.get('id'), pause
                )
                self.terminated_event.wait(pause)
            else:
                logging.debug('nothing to process, sleeping for 10s')
                self.terminated_event.wait(10)
