# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2020-02-20

"""build_node.utils.yum_repo_utils module unit tests."""

import os
import shutil
import tempfile
from unittest import TestCase

from build_node.errors import DataNotFoundError
from build_node.utils.yum_repo_utils import get_repo_modules_yaml_path

__all__ = ['TestGetRepoModulesYamlPath']


class TestGetRepoModulesYamlPath(TestCase):

    """build_node.utils.yum_repo_utils.get_repo_modules_yaml_path unit tests."""

    def setUp(self):
        self.repo_dir = tempfile.mkdtemp(prefix='castor_test_')
        self.repodata_dir = os.path.join(self.repo_dir, 'repodata')
        # NOTE: we can not use PyFakeFS here because get_repo_modules_yaml_path
        #       uses a C-library under the hood
        os.makedirs(self.repodata_dir)
        self.repomd_xml_path = os.path.join(self.repodata_dir, 'repomd.xml')

    def test_no_modules(self):
        """get_repo_modules_yaml_path handles repository without modules"""
        self._write_xml_header(self.repomd_xml_path)
        self._write_xml_footer(self.repomd_xml_path)
        self.assertIsNone(get_repo_modules_yaml_path(self.repo_dir))

    def test_no_repodata(self):
        """get_repo_modules_yaml_path handles repository without metadata"""
        self.assertRaises(DataNotFoundError, get_repo_modules_yaml_path,
                          self.repo_dir)

    def test_with_modules(self):
        """get_repo_modules_yaml_path finds modules.yaml in repository"""
        modules_location = ('repodata/5ee9d6d2f5d4788b5b5e17b066969c277e99a663'
                            'aab3b285c0505411ddcbc564-modules.yaml.gz')
        modules_rec = """<data type="modules">
        <checksum type="sha256">
          5ee9d6d2f5d4788b5b5e17b066969c277e99a663aab3b285c0505411ddcbc564
        </checksum>
        <open-checksum type="sha256">
          1ef6964e6c25085d477b663ca87f61d8c35db57578057fb364f723c60a84593b
        </open-checksum>
        <location href="{}"/>
        <timestamp>1582053317</timestamp>
        <size>875</size>
        <open-size>3434</open-size>
        </data>\n""".format(modules_location).encode('utf-8')
        self._write_xml_header(self.repomd_xml_path)
        with open(self.repomd_xml_path, 'ab') as fd:
            fd.write(modules_rec)
        self._write_xml_footer(self.repomd_xml_path)
        self.assertEqual(get_repo_modules_yaml_path(self.repo_dir),
                         os.path.join(self.repo_dir, modules_location))

    @staticmethod
    def _write_xml_header(xml_path):
        with open(xml_path, 'ab') as fd:
            fd.write(b"""<?xml version="1.0" encoding="UTF-8"?>
            <repomd xmlns="http://linux.duke.edu/metadata/repo"
                    xmlns:rpm="http://linux.duke.edu/metadata/rpm">
            <revision>1582053316</revision>\n""")

    @staticmethod
    def _write_xml_footer(xml_path):
        with open(xml_path, 'ab') as fd:
            fd.write(b'</repomd>')

    def tearDown(self):
        shutil.rmtree(self.repo_dir)
