import threading
import logging
import urllib

import requests


class BuilderSupervisor(threading.Thread):

    def __init__(self, config, builders, terminated_event):
        self.config = config
        self.builders = builders
        self.terminated_event = terminated_event
        super(BuilderSupervisor, self).__init__(name='BuildersSupervisor')

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
            headers = {'authorization': f'Bearer {self.config.jwt_token}'}
            requests.post(full_url, json=data, headers=headers)
            self.terminated_event.wait(60)
