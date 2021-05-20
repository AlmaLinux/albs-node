# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-03-11

"""build_node.utils.gerrit module unit tests."""

import unittest
import urllib.parse

from build_node.utils.gerrit import parse_gerrit_change_url

__all__ = ['TestParseGerritChangeURL']


class TestParseGerritChangeURL(unittest.TestCase):

    def test_with_patchset(self):
        """
        build_node.utils.gerrit.parse_gerrit_change_url supports URLs with patchset
        """
        for case in ('#/c/25407/5', '#/25407/5', '25407/5'):
            url = self.__construct_gerrit_url(case)
            self.assertEqual(parse_gerrit_change_url(url), ('25407', '5'))

    def test_without_patchset(self):
        """
        build_node.utils.gerrit.parse_gerrit_change_url supports URLs without \
patchset
        """
        for case in ('#/c/25407', '#/25407/', '#/25407', '25407', '25407/'):
            url = self.__construct_gerrit_url(case)
            self.assertEqual(parse_gerrit_change_url(url), ('25407', None))

    def __construct_gerrit_url(self, path):
        return urllib.parse.urljoin('https://gerrit.cloudlinux.com/', path)
