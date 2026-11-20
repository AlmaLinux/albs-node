import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class FakeBuildServer:
    """
    Build server stub, records the build node API calls and answers them
    with the configured responses.
    """

    def __init__(self):
        self.url = None
        self.requests = []
        # endpoint name -> (HTTP status, JSON body)
        self.responses = {}

    def calls(self, endpoint):
        return [body for name, body in self.requests if name == endpoint]


@pytest.fixture
def build_server():
    server_stub = FakeBuildServer()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length) or b'null')
            endpoint = self.path.rstrip('/').rsplit('/', 1)[-1]
            server_stub.requests.append((endpoint, body))
            status, payload = server_stub.responses.get(endpoint, (200, {}))
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever,
        # Fast shutdown
        kwargs={'poll_interval': 0.01},
        daemon=True,
    )
    thread.start()
    server_stub.url = f'http://127.0.0.1:{server.server_port}/api/v1/'
    yield server_stub
    server.shutdown()
    server.server_close()
    thread.join()
