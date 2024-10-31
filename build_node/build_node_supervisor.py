import logging
import threading
import traceback
import urllib.parse

import requests
import requests.adapters
from urllib3 import Retry
from cachetools import TTLCache

from build_node import constants
from build_node.utils.file_utils import file_url_exists


class BuilderSupervisor(threading.Thread):

    def __init__(
        self,
        config,
        builders,
        terminated_event,
        task_queue,
    ):
        self.config = config
        self.builders = builders
        self.terminated_event = terminated_event
        self.__session = None
        self.__task_queue = task_queue
        self.__cached_config = TTLCache(
            maxsize=config.cache_size,
            ttl=config.cache_update_interval,
            )
        super(BuilderSupervisor, self).__init__(name='BuildersSupervisor')

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
            'Authorization': f'Bearer {self.config.jwt_token}',
        })
        self.__session.mount('http://', adapter)
        self.__session.mount('https://', adapter)

    def __request_build_task(self):
        if not self.__task_queue.full():
            supported_arches = [self.config.base_arch]
            excluded_packages = self.get_excluded_packages()
            if self.config.base_arch == 'x86_64':
                supported_arches.append('i686')
            if self.config.build_src:
                supported_arches.append('src')
            full_url = urllib.parse.urljoin(
                self.config.master_url, 'build_node/get_task'
            )
            data = {
                'supported_arches': supported_arches,
                'excluded_packages': excluded_packages,
            }
            try:
                response = self.__session.post(
                    full_url, json=data, timeout=self.config.request_timeout
                )
                response.raise_for_status()
                return response.json()
            except Exception:
                logging.error(
                    "Can't report active task to master:\n%s",
                    traceback.format_exc(),
                )

    def get_active_tasks(self):
        return set([b.current_task_id for b in self.builders]) - set([
            None,
        ])

    def __report_active_tasks(self):
        active_tasks = self.get_active_tasks()
        logging.debug('Sending active tasks: {}'.format(active_tasks))
        full_url = urllib.parse.urljoin(
            self.config.master_url, 'build_node/ping'
        )
        data = {'active_tasks': [int(item) for item in active_tasks]}
        try:
            self.__session.post(
                full_url, json=data, timeout=self.config.request_timeout
            )
        except Exception:
            logging.error(
                "Can't report active task to master:\n%s",
                traceback.format_exc(),
            )

    def get_excluded_packages(self):
        if 'excluded_packages' not in self.__cached_config:
            uri = f'{self.__config.exclusions_url}/{self.__config.build_node_name}'
            if file_url_exists(uri):
                response = requests.get(uri).text
                self.__cached_config['excluded_packages'] = response.splitlines()

        return self.__cached_config.get('excluded_packages', [])

    def run(self):
        self.__generate_request_session()
        while not self.terminated_event.is_set():
            builders_aliveness = [t.is_alive() for t in self.builders]
            logging.debug('Builders aliveness: %s', str(builders_aliveness))
            if not any(builders_aliveness):
                logging.warning('All builders are dead, exiting')
                break
            self.__report_active_tasks()
            task = self.__request_build_task()
            if task:
                if not task.get('is_secure_boot'):
                    task['is_secure_boot'] = False
                self.__task_queue.put(task)
            else:
                logging.debug('nothing to process, sleeping for 10s')
                self.terminated_event.wait(10)
