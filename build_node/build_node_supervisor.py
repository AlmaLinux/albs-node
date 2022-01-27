import traceback
import threading
import logging
import urllib.parse

import requests
import requests.adapters
from urllib3 import Retry

from build_node import constants


class BuilderSupervisor(threading.Thread):

    def __init__(self, config, builders, terminated_event):
        self.config = config
        self.builders = builders
        self.terminated_event = terminated_event
        self.__session = None
        super(BuilderSupervisor, self).__init__(name='BuildersSupervisor')

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
            'Authorization': f'Bearer {self.config.jwt_token}',
        })
        self.__session.mount('http://', adapter)
        self.__session.mount('https://', adapter)

    def get_active_tasks(self):
        return set([b.current_task_id for b in self.builders]) - set([None, ])

    def run(self):
        self.__generate_request_session()
        while not self.terminated_event.is_set():
            builders_aliveness = [t.is_alive() for t in self.builders]
            logging.debug('Builders aliveness: %s', str(builders_aliveness))
            if not any(builders_aliveness):
                logging.warning('All builders are dead, exiting')
                break
            active_tasks = self.get_active_tasks()
            logging.debug('Sending active tasks: {}'.format(active_tasks))
            full_url = urllib.parse.urljoin(
                self.config.master_url, 'build_node/ping'
            )
            data = {'active_tasks': [int(item) for item in active_tasks]}
            try:
                self.__session.post(
                    full_url, json=data, timeout=self.config.request_timeout)
            except Exception:
                logging.error(
                    "Can't report active task to master:\n%s",
                    traceback.format_exc()
                )
            self.terminated_event.wait(60)
