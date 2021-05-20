# -*- mode:python; coding:utf-8; -*-
# author: Potoropin Vyacheslav <vpotoropin@cloudlinux.com>
# created: 2020-05-12

import os
import copy

from pyfakefs.fake_filesystem_unittest import TestCase

from build_node.utils.plumbum_utils import RetryParamikoMachine


class TestLoadSystemSshConfig(TestCase):

    """
    build_node.utils.plumbum_utils.RetryParamikoMachine._load_system_ssh_config
    tests
    """

    def setUp(self):
        self.setUpPyfakefs()
        self.ssh_dir = os.path.expanduser('~/.ssh')
        self.config_path = os.path.join(self.ssh_dir, 'config')
        self.fs.makedirs(self.ssh_dir)
        self.fs.create_file(self.config_path)

    def test_empty_config(self):
        """
        build_node.utils.plumbum_utils.RetryParamikoMachine._load_system_ssh_config
        should return input args, if there is no record in ~/.ssh/config for
        input host
        """
        host = '127.0.0.1'
        kwargs = {'load_system_ssh_config': True}
        expected_kwargs = copy.deepcopy(kwargs)
        output = RetryParamikoMachine._load_system_ssh_config(host, kwargs)
        self.assertEqual((host, expected_kwargs), output)

    def test_filled_config(self):
        """
        build_node.utils.plumbum_utils.RetryParamikoMachine._load_system_ssh_config
        should return parsed config info
        """
        with open(self.config_path, 'w') as ssh_config:
            ssh_config.write(
                'Host rollout.cloudlinux.com\n'
                '   Hostname 192.168.246.38\n'
                '   IdentityFile ~/.ssh/id_rsa\n'
                '   User root\n'
                '   PreferredAuthentications publickey\n'
            )
        host = 'rollout.cloudlinux.com'
        kwargs = {'load_system_ssh_config': True}
        expected = (
            '192.168.246.38',
            {
                'user': 'root',
                'keyfile': os.path.expanduser('~/.ssh/id_rsa'),
                'load_system_ssh_config': True
            }
        )
        output = RetryParamikoMachine._load_system_ssh_config(host, kwargs)
        self.assertEqual(expected, output)

    def test_override_settings(self):
        """
        build_node.utils.plumbum_utils.RetryParamikoMachine._load_system_ssh_config
        shouldn't override passed args
        """
        with open(self.config_path, 'w') as ssh_config:
            ssh_config.write(
                'Host rollout.cloudlinux.com\n'
                '   Port 2020\n'
                '   IdentityFile ~/.ssh/id_rsa\n'
                '   User root\n'
                '   PreferredAuthentications publickey\n'
            )
        host = 'rollout.cloudlinux.com'
        kwargs = {
            'load_system_ssh_config': True,
            'user': 'alt',
            'keyfile': '/path/to/keyfile',
            'port': '5050'
        }
        expected_kwargs = copy.deepcopy(kwargs)
        output = RetryParamikoMachine._load_system_ssh_config(host, kwargs)
        self.assertEqual((host, expected_kwargs), output)
