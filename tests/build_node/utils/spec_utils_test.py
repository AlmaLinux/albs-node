# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-03-06

"""build_node.utils.spec_utils module unit tests."""

import datetime
import os
import shutil
import tempfile
import unittest

from pyfakefs.fake_filesystem_unittest import TestCase

from build_node.errors import DataNotFoundError
from build_node.utils.spec_utils import *
from build_node.build_node_errors import BuildError

__all__ = ['TestBumpRelease', 'TestBumpReleaseSpecFile',
           'TestBumpVersionDatestampSpecFile', 'TestGetRawSpecData',
           'TestWipeRpmMacro', 'TestAddGerritRefToSpec']


def generate_spec_file(spec_file, version, release):
    """
    Generates a test spec file.

    Parameters
    ----------
    spec_file : str
        Spec file path.
    version : str
        Version field value.
    release : str
        Release field value.
    """
    template = """
Name:      test-package
Version:   {0}
Release:   {1}
Summary:   Test package.

%build
./configure

%changelog
* Wed Aug 22 2018 Example User <user@example.com> - {0}-{1}
- Initial RPM package build
    """
    with open(spec_file, 'w') as fd:
        fd.write(template.format(version, release))


class TestBumpRelease(unittest.TestCase):

    """build_node.utils.spec_utils.bump_release unit tests"""

    def test_invalid_release(self):
        """build_node.utils.spec_utils.bump_release should raise ValueError for an \
invalid release"""
        for release in ('broken', '%{dist}'):
            self.assertRaises(ValueError, bump_release, release)

    def test_no_macro(self):
        """build_node.utils.spec_utils.bump_release should process a release \
without macro"""
        cases = (('1', '2'), ('1.el7', '2.el7'),
                 ('12.el6_9', '13.el6_9'),
                 ('18.el6h.cloudlinux.8', '18.el6h.cloudlinux.9'))
        for release, result in cases:
            self.assertEqual(bump_release(release), result)

    def test_with_macro(self):
        """build_node.utils.spec_utils.bump_release should process a release with \
macro"""
        cases =(
            ('1%{?dist}', '2%{?dist}'), ('1%dist', '2%dist'),
            ('3%{?dist}.1.cloudlinux', '3%{?dist}.2.cloudlinux'),
            ('12.%{kversionreltag}%{?dist}', '13.%{kversionreltag}%{?dist}'),
            ('42%{?dist}%{rhel}.1.cl.13', '42%{?dist}%{rhel}.1.cl.14')
        )
        for release, result in cases:
            self.assertEqual(bump_release(release), result)

    def test_reset_to(self):
        """build_node.utils.spec_utils.bump_release resets last segment to \
specified value"""
        cases = (('12', '1', '1'), ('2%{?dist}', '1', '1%{?dist}'),
                 ('3%{?dist}.3.cloudlinux', '1', '3%{?dist}.1.cloudlinux'),
                 ('42%{?dist}%{rhel}.1.cl.13', '5', '42%{?dist}%{rhel}.1.cl.5'))
        for release, reset_to, result in cases:
            self.assertEqual(bump_release(release, reset_to), result)


class TestBumpReleaseSpecFile(unittest.TestCase):

    def test_bump(self):
        """build_node.utils.spec_utils.bump_release_spec_file should bump release"""
        template = """
Name:        test-package
Version:     0.1.14
Release:     {0}
Summary:     Test package.
%build
./configure
        """
        with tempfile.NamedTemporaryFile(prefix='castor_test_', mode='r+') as fd:
            fd.write(template.format('9%{?dist}.13.cloudlinux'))
            fd.flush()
            bump_release_spec_file(fd.name)
            fd.seek(0)
            self.assertEqual(fd.read(),
                              template.format('9%{?dist}.14.cloudlinux'))


class TestBumpVersionDatestampSpecFile(TestCase):

    def setUp(self):
        self.setUpPyfakefs()
        self.spec_dir = '/test_spec'
        os.makedirs(self.spec_dir)
        self.spec_file = os.path.join(self.spec_dir, 'test-package.spec')
        self.today = datetime.date.today()
        self.today_version = self.today.strftime('%Y%m%d')
        self.spec_release = '12%{?dist}'

    def test_bumps_version(self):
        """build_node.utils.spec_utils.bump_version_datestamp_spec_file bumps \
outdated Version field"""
        spec_version = (self.today - datetime.timedelta(days=10)).\
            strftime('%Y%m%d')
        generate_spec_file(self.spec_file, spec_version, self.spec_release)
        self.__check_bump_function(self.today_version, '1%{?dist}')

    def test_bumps_release(self):
        """build_node.utils.spec_utils.bump_version_datestamp_spec_file bumps \
Release field if Version is up-to-date"""
        generate_spec_file(self.spec_file, self.today_version,
                           self.spec_release)
        self.__check_bump_function(self.today_version, '13%{?dist}')

    def test_reports_missing_fields(self):
        """build_node.utils.spec_utils.bump_version_datestamp_spec_file reports \
missing spec fields"""
        spec_file = os.path.join(self.spec_dir, 'broken.spec')
        with open(spec_file, 'w') as fd:
            fd.write('Name: broken-project\n')
        self.assertRaises(DataNotFoundError, bump_version_datestamp_spec_file,
                          spec_file)

    def __check_bump_function(self, version, release):
        result = bump_version_datestamp_spec_file(self.spec_file)
        spec_data = get_raw_spec_data(self.spec_file, ['Version', 'Release'])
        self.assertEqual(spec_data['Version'], version)
        self.assertEqual(spec_data['Release'], release)
        self.assertEqual(result['Version'], version)
        self.assertEqual(result['Release'], release)

    def tearDown(self):
        if os.path.exists(self.spec_dir):
            shutil.rmtree(self.spec_dir)


class TestGetRawSpecData(unittest.TestCase):

    def test_get_nevra(self):
        """build_node.utils.spec_utils.get_raw_spec_data should extract NEVRA"""
        spec = """
Name: test-package
Epoch:      1
Version:  1.2
Release:    1%{?dist}
Summary: Test package
        """
        with tempfile.NamedTemporaryFile(prefix='castor_test_', mode = 'w') as fd:
            fd.write(spec)
            fd.flush()
            self.assertEqual(get_raw_spec_data(fd.name,
                                                ['Name', 'Epoch', 'Version',
                                                 'Release']),
                              {'Name': 'test-package', 'Epoch': '1',
                               'Version': '1.2', 'Release': '1%{?dist}'})


class TestWipeRpmMacro(unittest.TestCase):

    def test_no_braces(self):
        """
        build_node.utils.spec_utils.wipe_rpm_macro should remove macros without \
braces
        """
        self.assertEqual(wipe_rpm_macro('1%dist.4%rhel.cloudlinux'),
                          '1.4.cloudlinux')

    def test_with_braces(self):
        """
        build_node.utils.spec_utils.wipe_rpm_macro should remove macros with braces
        """
        self.assertEqual(
            wipe_rpm_macro('1%{?dist}%{!?req_kver: %(uname -r)}.cloudlinux'),
            '1.cloudlinux'
        )

    def test_remove_dot(self):
        """
        build_node.utils.spec_utils.wipe_rpm_macro should remove trailing dots
        """
        self.assertEqual(wipe_rpm_macro('1.%{?dist}'), '1')


class TestAddGerritRefToSpec(unittest.TestCase):

    def test_invalid_ref(self):
        """
        build_node.utils.spec_utils.add_gerrit_ref_to_spec reports invalid ref
        """
        spec = """
Name: test-package
Epoch:      1
Version:  1.2
Release:    1%{?dist}
Summary: Test package
        """
        with tempfile.NamedTemporaryFile(prefix='castor_test_', mode = 'w') as fd:
            fd.write(spec)
            fd.flush()
            self.assertRaises(BuildError, add_gerrit_ref_to_spec,
                              fd.name, 'a_malformed_ref')

    def test_missing_spec_file(self):
        """
        build_node.utils.spec_utils.add_gerrit_ref_to_spec reports missing
        spec file
        """
        example_ref = 'refs/changes/20/28520/5'
        self.assertRaises(BuildError, add_gerrit_ref_to_spec, 'a missing file',
                          example_ref)
