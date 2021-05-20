import threading
import logging
import zmq

from build_node.utils.zmq_utils import setup_client_socket, DealerRepCommunicator


class BuilderSupervisor(threading.Thread):

    def __init__(self, config, builders, zmq_context, terminated_event):
        self.config = config
        self.builders = builders
        self.zmq_context = zmq_context
        self.terminated_event = terminated_event
        super(BuilderSupervisor, self).__init__(name='BuildersSupervisor')

    def get_active_tasks(self):
        return set([b.current_task_id for b in self.builders]) - set([None, ])

    def run(self):
        msg_exchanger = DealerRepCommunicator(
            self.zmq_context.socket(zmq.DEALER))
        setup_client_socket(msg_exchanger.socket, self.config.private_key_path,
                            self.config.master_key_path)
        msg_exchanger.socket.setsockopt(zmq.LINGER, 0)
        msg_exchanger.connect(self.config.master_url)
        try:
            while any([t.is_alive() for t in self.builders]):
                active_tasks = self.get_active_tasks()
                logging.debug('Sending active tasks: {}'.format(active_tasks))
                support_arches = {
                    'native': self.config.native_support,
                    'arm64': self.config.arm64_support,
                    'arm32': self.config.arm32_support,
                    'pesign': self.config.pesign_support
                }
                request = {
                    'node_id': self.config.node_id,
                    'endpoint': 'ping',
                    'parameters': {
                        'active_tasks': list(active_tasks),
                        'node_type': self.config.node_type,
                        'threads_count': self.config.threads_count,
                        'supports': support_arches
                    }
                }
                msg_exchanger.send(request)
                response = msg_exchanger.recv()
                if not isinstance(response, dict) or 'success' not in response:
                    logging.error('Server reported malformed request')
                self.terminated_event.wait(60)
        except zmq.ContextTerminated:
            msg_exchanger.close()
