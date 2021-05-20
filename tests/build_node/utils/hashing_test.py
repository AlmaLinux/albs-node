# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-01-23

"""build_node.utils.hashing module unit tests."""

import unittest

from build_node.utils.hashing import get_hasher

__all__ = ['TestHasherUtils']


class TestHasherUtils(unittest.TestCase):

    """build_node.utils.get_hasher unit tests"""

    def test_get_hasher_sha1(self):
        """build_node.utils.hashing.get_hasher should support sha1 algorithm"""
        for algo in ('sha', 'sha1'):
            self.assertEqual(get_hasher(algo).name, 'sha1')

    def test_get_hasher_sha256(self):
        """build_node.utils.hashing.get_hasher should support sha256 algorithm"""
        self.assertEqual(get_hasher('sha256').name, 'sha256')

    def test_get_hasher_md5(self):
        """build_node.utils.hashing.get_hasher should support md5 algorithm"""
        self.assertEqual(get_hasher('md5').name, 'md5')
