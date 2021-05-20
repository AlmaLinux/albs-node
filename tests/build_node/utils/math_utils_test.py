# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2019-02-26

import unittest

from build_node.utils.math_utils import trim_mean

__all__ = ['TestTrimMean']


class TestTrimMean(unittest.TestCase):

    """build_node.utils.math_utils.trim_mean function unit tests."""

    def test_20(self):
        """build_node.utils.math_utils.trim_mean calculates 20% truncated mean"""
        numbers = [2, 3, 4, 5, 7, 9, 10, 11, 12, 15]
        expected = 7.666666666666667
        self.assertEqual(trim_mean(numbers, 20), expected)
        numbers.reverse()
        self.assertEqual(trim_mean(numbers, 20), expected)

    def test_percentage_limit(self):
        """build_node.utils.math_utils.trim_mean limits percentage to 40"""
        self.assertEqual(trim_mean(list(range(50)), 40), 24.5)
        self.assertRaises(ValueError, trim_mean, list(range(20)), 41)
