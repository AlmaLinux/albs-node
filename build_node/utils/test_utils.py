# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-06-09

"""
CloudLinux Build System testing utility functions.
"""

import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest

import pymongo
import mockupdb

__all__ = ['MOCK_COMMAND_TEMPLATE', 'MockShellCommand', 'MockupDBTestCase',
           'unload_plumbum_modules', 'change_cwd']


MOCK_COMMAND_TEMPLATE = """#!{python}
import json, os, sys

with open({output_file!r}, 'a') as fd:
    json.dump(
        {{'argv': sys.argv, 'cwd': os.getcwd(), 'env': dict(os.environ)}},
        fd)
    fd.write('\\x1e')

{user_code}
"""


class MockShellCommand(object):

    """
    Context manager to mock a shell command.

    Notes
    -----
    The code is inspired by MockCommand class from the testpath project
    (https://github.com/jupyter/testpath). There are some differences through:

      - the original code doesn't remove commands directory after a context
        manager exit.
      - the original code doesn't populate a user provided content with a
        "recording_file" variable but creates the file and its parent directory
        anyway.
      - the original code uses global variables and contains unnecessary code
        for compatibility with MS Windows.
    """

    def __init__(self, name, user_code=None, tmp_dir=None):
        """
        Parameters
        ----------
        name : str
            Mocked shell command name.
        user_code : str, optional
            Additional Python code to be executed in the mocked command (e.g.
            print some data).
        tmp_dir : str, optional
            Base directory for temporary files.
        """
        self.__name = name
        self.__user_code = user_code
        self.__tmp_dir = tmp_dir
        self.__command_dir = None
        self.__output_file = None

    def __enter__(self):
        self.__command_dir = tempfile.mkdtemp(prefix='castor_msc_',
                                              dir=self.__tmp_dir)
        fd, self.__output_file = tempfile.mkstemp(prefix='castor_msc_',
                                                  dir=self.__tmp_dir)
        os.close(fd)
        self.__create_command_file()
        self.modify_env_path(self.__command_dir)
        return self

    def __create_command_file(self):
        command_path = os.path.join(self.__command_dir, self.__name)
        command = MOCK_COMMAND_TEMPLATE.format(
            python=sys.executable,
            output_file=self.__output_file,
            user_code=self.__user_code or '')
        with open(command_path, 'w') as fd:
            fd.write(command)
        os.chmod(command_path, 0o755)

    def get_calls(self):
        if not self.__output_file or not os.path.isfile(self.__output_file):
            return []
        with open(self.__output_file, 'r') as fd:
            return [json.loads(chunk)
                    for chunk in fd.read().split('\x1e')[:-1]]

    @staticmethod
    def modify_env_path(command_dir):
        os.environ['PATH'] = '{0}{1}{2}'.format(command_dir, os.pathsep,
                                                os.environ['PATH'])

    @staticmethod
    def revert_env_path(command_dir):
        paths = os.environ['PATH'].split(os.pathsep)
        if command_dir in paths:
            paths.remove(command_dir)
        os.environ['PATH'] = os.pathsep.join(paths)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.__command_dir:
            self.revert_env_path(self.__command_dir)
            if os.path.exists(self.__command_dir):
                shutil.rmtree(self.__command_dir)
        if self.__output_file and os.path.exists(self.__output_file):
            os.remove(self.__output_file)


class MockupDBTestCase(unittest.TestCase):

    """
    Base class for unit tests using the MockupDB library.
    """

    def setUp(self):
        self.mongo_server = mockupdb.MockupDB()
        self.mongo_server.autoresponds('ismaster', maxWireVersion=6)
        self.mongo_server.run()
        self.mongo_client = pymongo.MongoClient(self.mongo_server.uri)
        self.mongo_db = self.mongo_client['cla']

    def tearDown(self):
        self.mongo_client.close()
        self.mongo_server.stop()


def unload_plumbum_modules(test_module):
    """
    Unloads a `test_module` and all plumbum submodules except
    `plumbum.commands.processes`.

    This is required because plumbum loads environment variables once when
    module is imported, so we need to reload it after each test case to make
    MockShellCommand work.

    Parameters
    ----------
    test_module : str
        Name of a module to unload alon with plumbum submodules.
    """
    for mod in list(sys.modules.keys()):
        if (mod == test_module) or \
                ('plumbum' in mod and mod != 'plumbum.commands.processes'):
            del sys.modules[mod]


@contextlib.contextmanager
def change_cwd(path):
    """
    Context manager that temporarily changes current working directory.

    Parameters
    ----------
    path : str
        Temporary working directory path.

    Returns
    -------
    generator
    """
    old_cwd = os.getcwd()
    os.chdir(path)
    try:
        yield os.getcwd()
    finally:
        os.chdir(old_cwd)
