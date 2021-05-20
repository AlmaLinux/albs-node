# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-07-02

"""
CloudLinux Build System RPM utility functions unit tests.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from build_node.errors import CommandExecutionError
from build_node.utils.file_utils import hash_file
from build_node.utils.test_utils import MockShellCommand
from build_node.utils.rpm_utils import string_to_version, flag_to_string

__all__ = ['TestUnpackSrcRpm']


class TestUnpackSrcRpm(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.input_dir = tempfile.mkdtemp(prefix='castor_')
        cls.cpio_file = os.path.join(cls.input_dir, 'test.cpio')
        cls.rpm_file = os.path.join(cls.input_dir, 'example.src.rpm')
        cls.checksums = {}
        file_names = ('example.spec', 'example.tar.bz2')
        for file_name in file_names:
            file_path = os.path.join(cls.input_dir, file_name)
            content = '{0} content\n'.format(file_name).encode('utf-8')
            cls.checksums[file_name] = hashlib.sha256(content).hexdigest()
            with open(file_path, 'wb') as fd:
                fd.write(content)
        subprocess.call(['cd {0} && ls example* | cpio -ov > {1} 2>/dev/null'.
                        format(cls.input_dir, cls.cpio_file)], shell=True)
        shutil.copy(cls.cpio_file, '/tmp/test.cpio')

    def setUp(self):
        self.output_dir = tempfile.mkdtemp(prefix='castor_')
        self.__unload_modules()

    def test_unpacks_srpm(self):
        """build_node.utils.rpm_utils.unpack_src_rpm unpacks existent src-RPM"""
        rpm2cpio = 'import sys; fd = open("{0}", "rb"); ' \
                   'sys.stdout.buffer.write(fd.read()) ; fd.close()'.format(
                       self.cpio_file)
        with MockShellCommand('rpm2cpio', rpm2cpio) as cmd:
            from build_node.utils.rpm_utils import unpack_src_rpm
            unpack_src_rpm(self.rpm_file, self.output_dir)
            for file_name, checksum in self.checksums.items():
                file_path = os.path.join(self.output_dir, file_name)
                self.assertTrue(os.path.exists(file_path))
                self.assertEqual(hash_file(file_path, hashlib.sha256()),
                                 checksum)
            # rpm2cpio accepts only one argument which is a file name
            self.assertEqual(cmd.get_calls()[0]['argv'][1:], [self.rpm_file])

    def test_missing_file(self):
        """build_node.utils.rpm_utils.unpack_src_rpm reports missing src-RPM"""
        from build_node.utils.rpm_utils import unpack_src_rpm
        self.assertRaises(CommandExecutionError, unpack_src_rpm,
                          'some_missing_file', self.output_dir)

    def tearDown(self):
        shutil.rmtree(self.output_dir)
        self.__unload_modules()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.input_dir)

    def __unload_modules(self):
        # NOTE: plumbum loads environment variables once when module is
        #       imported, so we need to reload it after each test case to
        #       make MockShellCommand work.
        for mod in list(sys.modules.keys()):
            if (mod == 'build_node.utils.rpm_utils') or \
                    ('plumbum' in mod and mod != 'plumbum.commands.processes'):
                del sys.modules[mod]


class TestStringToVersion(unittest.TestCase):
    cases = [{'input': None,
              'result': (None, None, None)},
             {'input': '',
              'result': (None, None, None)},
             {'input': '89-9',
              'result': ('0', '89', '9')},
             {'input': '2:89-9',
              'result': ('2', '89', '9')},
             {'input': '2:899',
              'result': ('2', '899', None)},
             {'input': '2',
              'result': ('0', '2', None)},
             {'input': '2:89-9-8',
              'result': ('2', '89', '9-8')},
             {'input': '2:',
              'result': ('2', None, None)}
            ]

    def test_cases(self):
        for case in self.cases:
            result = string_to_version(case['input'])
            self.assertTrue(type(result) is tuple)
            self.assertEqual(len(result), 3)
            for r in result:
                self.assertTrue(r is None or type(r) is str)
            self.assertEqual(result, case['result'])


class TestFlagsToString(unittest.TestCase):
    cases = [{'input': 0, 'result': None},
             {'input': 2, 'result': 'LT'},
             {'input': 4, 'result': 'GT'},
             {'input': 8, 'result': 'EQ'},
             {'input': 10, 'result': 'LE'},
             {'input': 12, 'result': 'GE'},
             {'input': 14, 'result': 14},
             {'input': 99, 'result': 3},
            ]

    def test_cases(self):
        for case in self.cases:
            result = flag_to_string(case['input'])
            self.assertTrue(result is None or
                            type(result) is str or
                            type(result) is int)
            if result is str:
                self.assertEqual(len(result), 2)
            elif result is int:
                self.assertEqual(case['input'], case['input'] % 16)
            self.assertEqual(result, case['result'])
