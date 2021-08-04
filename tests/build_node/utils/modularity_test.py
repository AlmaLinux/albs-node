# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2020-02-03

"""
build_node.utils.modularity module unit tests.
"""

import datetime
import random
from unittest import TestCase, mock

# noinspection PyPackageRequirements
import gi
gi.require_version('Modulemd', '2.0')
# noinspection PyPackageRequirements,PyUnresolvedReferences
from gi.repository import Modulemd

from build_node.errors import DataNotFoundError
from build_node.utils.modularity import (
    generate_stream_version,
    get_stream_build_deps,
    get_stream_runtime_deps,
    calc_stream_build_context,
    calc_stream_runtime_context,
    calc_stream_context,
    calc_stream_dist_macro
)

__all__ = ['TestGenerateStreamVersion',
           'TestGetStreamBuildDeps',
           'TestGetStreamRuntimeDeps',
           'TestCalcStreamBuildContext',
           'TestCalcStreamRuntimeContext',
           'TestCalcStreamContext',
           'TestCalcStreamDistMacro']


class TestGenerateStreamVersion(TestCase):

    """build_node.utils.modularity.generate_stream_version unit tests."""

    def setUp(self):
        self._ts = datetime.datetime(2020, 2, 3, 10, 22, 44)

    @mock.patch('build_node.utils.modularity.datetime.datetime')
    def test_generate_version(self, mocked_datetime):
        """generate_stream_version generates stream version"""
        mocked_datetime.utcnow.return_value = self._ts
        platform = {
            'modularity': {
                'platform': {'module_version_prefix': '80100'}
            }
        }
        self.assertEqual(generate_stream_version(platform),
                         8010020200203102244)

    def test_missing_prefix(self):
        """generate_stream_version fails if module_version_prefix is missing"""
        self.assertRaises(DataNotFoundError, generate_stream_version, {})


class TestGetStreamBuildDeps(TestCase):

    """build_node.utils.modularity.get_stream_build_deps unit tests."""

    def setUp(self):
        self.stream = Modulemd.ModuleStreamV2.new()
        self.xmd = {
            'mbs': {
                'buildrequires': {
                    'platform': {
                        'stream': 'el8',
                        'ref': 'virtual',
                        'context': '00000000',
                        'version': '2'
                    },
                    'perl': {
                        'stream': '5.26',
                        'ref': 'e712dc415d7660fc59a5b68c48ddcdad76c4d575',
                        'context': '9edba152',
                        'version': '820181219174508'
                    }
                }
            }
        }

    def test_mbs_only(self):
        """get_stream_build_deps extracts requirements from xmd data"""
        self.stream.set_xmd(self.xmd)
        self.assertEqual(get_stream_build_deps(self.stream),
                         self.xmd['mbs']['buildrequires'])

    def test_deps_only(self):
        """
        get_stream_build_deps extracts requirements from dependencies field
        """
        expected = self._init_build_deps()
        self.assertEqual(get_stream_build_deps(self.stream), expected)

    def test_mbs_and_deps(self):
        """
        get_stream_build_deps prefers xmd data instead of dependencies field
        """
        self.stream.set_xmd(self.xmd)
        self._init_build_deps()
        self.assertEqual(get_stream_build_deps(self.stream),
                         self.xmd['mbs']['buildrequires'])

    def test_empty(self):
        """
        get_stream_build_deps returns empty dictionary for empty requirements
        """
        self.assertEqual(get_stream_build_deps(self.stream), {})

    def test_multiple_versions_error(self):
        """
        get_stream_build_deps raises ValueError for multiple stream versions
        """
        deps = Modulemd.Dependencies.new()
        deps.add_buildtime_stream('perl', '5.24')
        deps.add_buildtime_stream('perl', '5.26')
        self.stream.add_dependencies(deps)
        self.assertRaises(ValueError, get_stream_build_deps, self.stream)

    def _init_build_deps(self):
        expected = {}
        deps = Modulemd.Dependencies.new()
        for name, info in self.xmd['mbs']['buildrequires'].items():
            deps.add_buildtime_stream(name, info['stream'])
            expected[name] = {'stream': info['stream']}
        self.stream.add_dependencies(deps)
        return expected


class TestGetStreamRuntimeDeps(TestCase):

    """build_node.utils.modularity.get_stream_runtime_deps unit tests."""

    def setUp(self):
        self.stream = Modulemd.ModuleStreamV2.new()

    def test_empty(self):
        """
        get_stream_runtime_deps returns empty dictionary for empty requirements
        """
        self.assertEqual(get_stream_runtime_deps(self.stream), {})

    def test_deps(self):
        """get_stream_runtime_deps returns runtime dependencies"""
        expected = {'platform': ['el8'],
                    'perl': ['5.24']}
        deps = Modulemd.Dependencies.new()
        for name, streams in expected.items():
            deps.add_runtime_stream(name, streams[0])
        self.stream.add_dependencies(deps)
        self.assertEqual(get_stream_runtime_deps(self.stream), expected)


class TestCalcStreamBuildContext(TestCase):

    """build_node.utils.modularity.calc_stream_build_context unit tests."""

    def test_calculate(self):
        """calc_stream_build_context returns build context hash"""
        build_deps = {'platform': {'stream': 'el8'}}
        self.assertEqual(calc_stream_build_context(build_deps),
                         'eca50767b80d2887d7cf8e9c28131660c2e39077')


class TestCalcStreamRuntimeContext(TestCase):

    """build_node.utils.modularity.calc_stream_runtime_context unit tests."""

    def test_platform_only(self):
        """
        calc_stream_runtime_context returns hash for single platform dependency
        """
        runtime_deps = {'platform': ['el8']}
        self.assertEqual(calc_stream_runtime_context(runtime_deps),
                         '72c2eccd0ef79ee91dd48daf0f7f14ce48b1fa76')

    def test_multiple(self):
        """
        calc_stream_runtime_context returns hash for multiple dependencies
        """
        runtime_deps = {'perl': ['5.26'],
                        'perl-DBI': ['1.641'],
                        'platform': ['el8']}
        self.assertEqual(calc_stream_runtime_context(runtime_deps),
                         'c047401b6c1bc397265603595a35ecf0f861bade')


class TestCalcStreamContext(TestCase):

    """build_node.utils.modularity.calc_stream_context unit tests."""

    @mock.patch('build_node.utils.modularity.calc_stream_build_context')
    @mock.patch('build_node.utils.modularity.calc_stream_runtime_context')
    def test_calculate(self, calc_runtime, calc_build):
        """calc_stream_context calculates stream context"""
        calc_build.return_value = 'eca50767b80d2887d7cf8e9c28131660c2e39077'
        calc_runtime.return_value = '72c2eccd0ef79ee91dd48daf0f7f14ce48b1fa76'
        stream = Modulemd.ModuleStreamV2.new()
        self.assertEqual(calc_stream_context(stream), '9edba152')


class TestCalcStreamDistMacro(TestCase):

    """build_node.utils.modularity.calc_stream_dist_macro unit tests."""

    def setUp(self):
        stream = Modulemd.ModuleStreamV2.new('satellite-5-client', '1.0')
        stream.set_version(8010020200120174453)
        stream.set_context('cdc1202b')
        self.stream = stream
        self.platform = {
            'modularity': {'platform': {'dist_tag_prefix': 'el8.1.0'}}
        }
        self.build_index = random.randint(1, 9999)
        self.dist_hash = 'ff5fa62e'

    def test_calculate(self):
        """calc_stream_dist_macro calculates dist macros value"""
        expected = '.module_{0}+{1}+{2}'.format(
            self.platform['modularity']['platform']['dist_tag_prefix'],
            self.build_index, self.dist_hash
        )
        calculated = calc_stream_dist_macro(self.stream, self.platform,
                                            self.build_index)
        self.assertEqual(calculated, expected)
