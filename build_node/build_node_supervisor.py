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
        self.__generate_request_session()
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
        while any([t.is_alive() for t in self.builders]):
            active_tasks = self.get_active_tasks()
            logging.debug('Sending active tasks: {}'.format(active_tasks))
            # support_arches = {
            #     'native': self.config.native_support,
            #     'arm64': self.config.arm64_support,
            #     'arm32': self.config.arm32_support,
            #     'pesign': self.config.pesign_support
            # }
            # request = {
            #     'node_id': self.config.node_id,
            #     'parameters': {
            #         'active_tasks': list(active_tasks),
            #         'node_type': self.config.node_type,
            #         'threads_count': self.config.threads_count,
            #         'supports': support_arches
            #     }
            # }
            full_url = urllib.parse.urljoin(
                self.config.master_url, 'build_node/ping'
            )
            data = {'active_tasks': [int(item) for item in active_tasks]}
            try:
                self.__session.post(full_url, json=data)
            except Exception:
                logging.error(
                    f"Can't report active task to master:\n"
                    f"{traceback.format_exc()}"
                )
            self.terminated_event.wait(60)
