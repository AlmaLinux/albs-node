from unittest import TestCase
from unittest.mock import Mock, patch

from build_node.uploaders.base import UploadError
from build_node.uploaders.pulp_session import PulpSession


def login_response(sessionid='xyz', csrftoken='abc', status=201):
    response = Mock(status=status)
    response.headers.getlist.return_value = [
        f'csrftoken={csrftoken}; expires=Thu, 28 Oct 2027 10:00:00 GMT; '
        'Max-Age=31449600; Path=/; SameSite=Lax',
        f'sessionid={sessionid}; expires=Thu, 12 Nov 2026 10:00:00 GMT; '
        'HttpOnly; Max-Age=1209600; Path=/; SameSite=Lax',
    ]
    return response


class TestPulpSession(TestCase):

    def setUp(self):
        login_api_patcher = patch('build_node.uploaders.pulp_session.LoginApi')
        self.login_api = login_api_patcher.start()
        self.addCleanup(login_api_patcher.stop)
        self.login = self.login_api.return_value.login_without_preload_content
        self.login.return_value = login_response()

    def test_authenticate_sets_session(self):
        session = PulpSession('http://pulp/', 'user', 'password')
        client = Mock()
        assert session.authenticate(client) is client
        assert client.cookie == 'sessionid=xyz; csrftoken=abc'
        client.set_default_header.assert_any_call('X-CSRFToken', 'abc')
        # No double slash from the trailing one in the host
        client.set_default_header.assert_any_call('Referer', 'http://pulp/')

    def test_login_uses_basic_auth(self):
        PulpSession('http://pulp', 'user', 'password', requests_timeout=10)
        login_client = self.login_api.call_args.args[0]
        assert login_client.configuration.host == 'http://pulp'
        assert login_client.configuration.username == 'user'
        assert login_client.configuration.password == 'password'
        self.login.assert_called_once_with(_request_timeout=10)
        self.login.return_value.release_conn.assert_called_once()

    def test_failed_login(self):
        self.login.return_value = login_response(status=401)
        with self.assertRaises(UploadError):
            PulpSession('http://pulp', 'user', 'wrong')
        self.login.return_value.release_conn.assert_called_once()

    def test_reset_connections_clears_every_pool(self):
        session = PulpSession('http://pulp', 'user', 'password')
        clients = [session.authenticate(Mock()) for _ in range(2)]
        session.reset_connections()
        for client in clients:
            client.rest_client.pool_manager.clear.assert_called_once_with()

    def test_relogin_updates_every_client(self):
        session = PulpSession('http://pulp', 'user', 'password')
        clients = [session.authenticate(Mock()) for _ in range(2)]
        self.login.return_value = login_response(
            sessionid='new-session', csrftoken='new-token'
        )
        session.login()
        for client in clients:
            assert client.cookie == 'sessionid=new-session; csrftoken=new-token'
            client.set_default_header.assert_any_call(
                'X-CSRFToken', 'new-token'
            )

    def test_relogin_skipped_when_session_already_renewed(self):
        session = PulpSession('http://pulp', 'user', 'password')
        expired = session.cookie
        self.login.return_value = login_response(sessionid='new-session')
        # The first of the parallel uploads logs in again
        session.login(expired_cookie=expired)
        assert self.login.call_count == 2
        # The others were rejected with the same old cookie
        session.login(expired_cookie=expired)
        assert self.login.call_count == 2
        assert session.cookie == 'sessionid=new-session; csrftoken=abc'
