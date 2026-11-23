import os

__all__ = [
    'TOTAL_RETRIES',
    'STATUSES_TO_RETRY',
    'METHODS_TO_RETRY',
    'GET_TASK_RETRY_OPTIONS',
    'BACKOFF_FACTOR',
    'TASK_CLAIM_PAUSE_RANGE',
    'ERROR_BODY_LOG_LIMIT',
    'INSTANCE_ID_FILE',
    'MAX_INSTANCE_ID_LENGTH',
    'PESIGN_DIR',
    'PESIGN_CLIENT_PATH',
]

TOTAL_RETRIES = 5
STATUSES_TO_RETRY = (413, 429, 502, 503, 504)
METHODS_TO_RETRY = ('GET', 'POST')
# get_task is a POST which claims a task. Once the request may have reached the
# server, the server may have already assigned a task to the node, and a
# repeated request would claim one more task while the first one is never
# built. So get_task is retried only on connection errors, when the request was
# never sent, urllib3 retries them regardless of the options below:
# - error responses (502, 503, 504...) and read errors (read timeouts, broken
#   connections) are not retried for methods missing from allowed_methods.
#   read=0 and status=0 forbid them for any method, so that this does not
#   depend on allowed_methods alone (e.g. an empty collection there would make
#   urllib3 retry every method);
# - other errors (e.g. an SSLError while reading the response) are retried for
#   any method, other=0 forbids them. A failed TLS handshake is such an error
#   too, so it is not retried either: the node asks again in 10 seconds.
GET_TASK_RETRY_OPTIONS = {
    'allowed_methods': ('GET',),
    'read': 0,
    'status': 0,
    'other': 0,
}
# {BACKOFF_FACTOR} * (2 ** ({TOTAL_RETRIES} - 1))
# timeout for every retry = 1, 2, 4, 8, 16 sec.
BACKOFF_FACTOR = 2
# Pause (in seconds, random within the range) after a task was claimed before
# asking for the next one, so that other nodes get their share of tasks
TASK_CLAIM_PAUSE_RANGE = (1, 3)
# Maximum number of characters of an error response body written to the log
ERROR_BODY_LOG_LIMIT = 500
# Name of the file in the working directory which keeps the instance id of
# the last started build node process
INSTANCE_ID_FILE = '.build_node_instance_id'
# A previous instance id read from the file which is longer is garbage
MAX_INSTANCE_ID_LENGTH = 255
# Secure boot signing scripts (pesign, pesign-client, modsign...) installed
# on the build node host, mounted into mock as /usr/local/bin for secure boot
# builds
PESIGN_DIR = '/opt/pesign'
# The node signs secure boot builds if it is installed, see the
# pesign_support option of the build node configuration
PESIGN_CLIENT_PATH = os.path.join(PESIGN_DIR, 'pesign-client')
