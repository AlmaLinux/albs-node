"""Mock config module unit tests."""

import unittest

from albs_build_lib.builder.mock.mock_config import MockConfig

__all__ = ['TestModuleConfig']


class FakeFile(object):

    """
    file like object
    """

    def __init__(self):
        self.__content = ''
        self.__last_content = ''

    def write(self, data):
        self.__content += data

    def flush(self):
        self.__last_content = self.__content
        self.__content = ''

    @property
    def content(self):
        return self.__content

    @property
    def last_content(self):
        return self.__last_content


class TestModuleConfig(unittest.TestCase):

    """
    test module_install and module_enable options presence
    """

    def test_via_constructor(self):
        """
        Try to add options via MockConfig constructor

        returns
        -------
        None
        """
        mock_config = MockConfig('x86_64', module_enable=['pki-deps:10.6'],
                                 module_install=['perl:5.26'])
        mock_config_file = FakeFile()
        mock_config.dump_to_file(mock_config_file)
        test_str = 'config_opts["module_enable"] = ["pki-deps:10.6"]'
        self.assertTrue(mock_config_file.last_content.find(test_str) >= 0)
        test_str = 'config_opts["module_install"] = ["perl:5.26"]'
        self.assertTrue(mock_config_file.last_content.find(test_str) >= 0)
        with self.assertRaises(ValueError):
            mock_config.add_module_enable('pki-deps:10.6')
        with self.assertRaises(ValueError):
            mock_config.add_module_install('perl:5.26')
        with self.assertRaises(ValueError):
            mock_config.add_module_enable('')
        with self.assertRaises(ValueError):
            mock_config.add_module_install('')
        with self.assertRaises(ValueError):
            mock_config.add_module_enable(None)
        with self.assertRaises(ValueError):
            mock_config.add_module_install(None)
        mock_config.add_module_enable('nginx:1.14')
        mock_config.add_module_install('perl-DBI:1.641')
        mock_config.dump_to_file(mock_config_file)
        test_str = 'config_opts["module_enable"] = '\
                   '["pki-deps:10.6", "nginx:1.14"]'
        self.assertTrue(mock_config_file.last_content.find(test_str) >= 0)
        test_str = 'config_opts["module_install"] = '\
                   '["perl:5.26", "perl-DBI:1.641"]'
        self.assertTrue(mock_config_file.last_content.find(test_str) >= 0)

    def test_via_methods(self):
        """
        Try to add options via MockConfig methods add_module_install and
            add_modile_enable

        returns
        -------
        None
        """
        mock_config = MockConfig('x86_64')
        mock_config_file = FakeFile()
        mock_config.dump_to_file(mock_config_file)
        test_str = 'config_opts["module_enable"]'
        self.assertTrue(mock_config_file.last_content.find(test_str) == -1)
        test_str = 'config_opts["module_install"]'
        self.assertTrue(mock_config_file.last_content.find(test_str) == -1)
        with self.assertRaises(ValueError):
            mock_config.add_module_enable('')
        with self.assertRaises(ValueError):
            mock_config.add_module_install('')
        with self.assertRaises(ValueError):
            mock_config.add_module_enable(None)
        with self.assertRaises(ValueError):
            mock_config.add_module_install(None)
        mock_config.add_module_enable('nginx:1.14')
        mock_config.add_module_install('perl-DBI:1.641')
        mock_config.dump_to_file(mock_config_file)
        test_str = 'config_opts["module_enable"] = ["nginx:1.14"]'
        self.assertTrue(mock_config_file.last_content.find(test_str) >= 0)
        test_str = 'config_opts["module_install"] = ["perl-DBI:1.641"]'
        self.assertTrue(mock_config_file.last_content.find(test_str) >= 0)
        with self.assertRaises(ValueError):
            mock_config.add_module_enable('nginx:1.14')
        with self.assertRaises(ValueError):
            mock_config.add_module_install('perl-DBI:1.641')
        mock_config.add_module_enable('pki-deps:10.6')
        mock_config.add_module_install('perl:5.26')
        mock_config.dump_to_file(mock_config_file)
        test_str = 'config_opts["module_enable"] = '\
                   '["nginx:1.14", "pki-deps:10.6"]'
        self.assertTrue(mock_config_file.last_content.find(test_str) >= 0)
        test_str = 'config_opts["module_install"] = '\
                   '["perl-DBI:1.641", "perl:5.26"]'
        self.assertTrue(mock_config_file.last_content.find(test_str) >= 0)
