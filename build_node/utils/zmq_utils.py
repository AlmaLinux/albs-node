# -*- mode:python; coding:utf-8; -*-
# author: Darya Malyavkina <dmalyavkina@cloudlinux.com>
#         Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-11-24

"""
CloudLinux Build System ZeroMQ related utility functions.
"""

# import logging
import os

import umsgpack
import zmq.auth
import zmq.auth.thread

from build_node.utils.file_utils import normalize_path, safe_mkdir

__all__ = ['generate_key_pair', 'setup_authenticator', 'setup_client_socket',
           'setup_server_socket', 'DealerRepCommunicator',
           'RepDealerCommunicator']


def generate_key_pair(output_dir, node_name, metadata=None):
    """
    Generates a ZeroMQ Curve key pair.

    Parameters
    ----------
    output_dir : str
        Directory where to save a generated key pair.
    node_name : str
        Build node name.
    metadata : dict, optional
        Additional metadata to store in the secret key file.

    Returns
    -------
    tuple
        Generated public and private key paths.
    """
    output_dir = normalize_path(output_dir)
    secret_key_path = os.path.join(output_dir, '{0}.key_secret')
    if os.path.exists(secret_key_path):
        raise Exception('secret key {0} is already exist'.
                        format(secret_key_path))
    safe_mkdir(output_dir)
    public_key_path, secret_key_path = \
        zmq.auth.create_certificates(output_dir, node_name, metadata=metadata)
    os.chmod(public_key_path, 0o600)
    os.chmod(secret_key_path, 0o600)
    return public_key_path, secret_key_path


def setup_authenticator(zmq_context, authorized_keys_dir):
    """
    Configures a ZeroMQ authenticator to accept messages only from authorized
    nodes.

    Parameters
    ----------
    zmq_context : zmq.Context
        ZeroMQ context object.
    authorized_keys_dir : str
        Authorized public keys directory path.

    Returns
    -------
    zmq.auth.thread.ThreadAuthenticator
        Configured authenticator.

    Notes
    -----
    An authenticator must be stopped before terminating a ZeroMQ context.
    """
    zmq_auth = zmq.auth.thread.ThreadAuthenticator(zmq_context)
    zmq_auth.start()
    zmq_auth.configure_curve(domain='*', location=authorized_keys_dir)
    return zmq_auth


def setup_client_socket(zmq_socket, private_key_path, master_key_path):
    """
    Configures a ZeroMQ Curve encrypted client socket.

    Parameters
    ----------
    zmq_socket : zmq.Socket
        Client ZeroMQ socket.
    private_key_path : str
        Client secret key path.
    master_key_path : str
        Server public key path.
    """
    cli_public_key, cli_secret_key = zmq.auth.load_certificate(
        private_key_path)
    zmq_socket.curve_publickey = cli_public_key
    zmq_socket.curve_secretkey = cli_secret_key
    master_public_key, _ = zmq.auth.load_certificate(master_key_path)
    zmq_socket.curve_serverkey = master_public_key


def setup_server_socket(zmq_socket, private_key_path):
    """
    Configures a ZeroMQ Curve encrypted server socket.

    Parameters
    ----------
    zmq_socket : zmq.Socket
        Server ZeroMQ socket.
    private_key_path : str
        Server secret key path.
    """
    public_key, secret_key = zmq.auth.load_certificate(private_key_path)
    zmq_socket.curve_publickey = public_key
    zmq_socket.curve_secretkey = secret_key
    zmq_socket.curve_server = True


# Commenting logging calls as they produce too much noise in logs.
# They could be useful only when debugging issues with communication.
class ZmqMessageExchanger(object):
    def __init__(self, zmq_socket: zmq.Socket):
        self._zmq_socket = zmq_socket
        self._poller = zmq.Poller()

    def connect(self, endpoint):
        self._zmq_socket.connect(endpoint)
        self._poller.register(self._zmq_socket, zmq.POLLIN)

    @property
    def socket(self):
        return self._zmq_socket

    @staticmethod
    def decode(encoded_data):
        data = umsgpack.loads(encoded_data)
        if not isinstance(data, dict):
            raise ValueError('request must be a dictionary')
        return data

    @staticmethod
    def encode(raw_data):
        return umsgpack.dumps(raw_data)

    def send(self, data):
        raise NotImplementedError

    def recv(self):
        raise NotImplementedError

    def close(self):
        self._zmq_socket.close(linger=0)
        self._poller.unregister(self._zmq_socket)


class DealerRepCommunicator(ZmqMessageExchanger):
    """
    This class implements DEALER - REP communication pattern.
    See more: https://zguide.zeromq.org/docs/chapter3/
    """

    def send(self, data):
        self.socket.send_string('', zmq.SNDMORE)
        self.socket.send(self.encode(data))

    def recv(self, retries: int = 60):
        """
        Performs message request with timeout.

        We need so much retries because of packages upload operation:
        - when we upload chunks from 1 to 1 before last, everything is fine;
        - when we've uploaded the last chunk, the package will be re-assembled
          on master and it may take a lot of time if the package is big
          (1-2 GB);
        - until master will perform all needed operations (package assembly,
          logs saving, DB update, etc.) it will not respond;
        All these factors contribute to the factor that function should wait
        long enough to get the answer. Still, it should be a reasonable number.
        For now it will be 60 retries, when each retry waits for the message
        for 1 minute. 1 hour should be enough to push through pretty much
        anything (until we will have artifacts of 5-10 GB each).

        Parameters
        ----------
        retries : int
            Number of poll() operations to perform before exiting

        Returns
        -------
        dict

        """
        while retries > 0:
            sockets = dict(self._poller.poll(60000))
            if sockets and self.socket in sockets:
                try:
                    self.socket.recv()
                    return self.decode(self.socket.recv())
                except IOError:
                    retries -= 1
            else:
                retries -= 1
        return {}


class RepDealerCommunicator(ZmqMessageExchanger):
    """
    This class implements REP - DEALER communication pattern,
    the opposite to DealerRepCommunicator
    """

    def send(self, data):
        self.socket.send(self.encode(data))

    def recv(self):
        return self.decode(self.socket.recv())
