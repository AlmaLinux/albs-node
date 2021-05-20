# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-11-03

import datetime
from unittest import TestCase

from build_node.utils.spec_parser import RPMChangelogRecord

__all__ = ['TestRPMChangelogRecord']


class TestRPMChangelogRecord(TestCase):

    def setUp(self):
        self.date = datetime.date.today()
        self.changelog_text = '- example changelog record\n-fixed some bugs'
        self.user_name = 'Example User'
        self.user_email = 'user@example.com'

    def test_generate(self):
        """RPMChangelogRecord.generate initializes RPMChangelogRecord"""
        evr = '2:2.0.2-0.76'
        text = ['update to 2.0.2 version', 'fixed a bug', '- formatted line']
        expected_text = [line if line.startswith('-') else '- {0}'.format(line)
                         for line in text]
        changelog = RPMChangelogRecord.generate(
            self.date, self.user_name, self.user_email, evr, text
        )
        self.assertEqual(changelog.date, self.date)
        self.assertEqual(changelog.text, expected_text)
        self.assertEqual(changelog.packager,
                         '{0} <{1}> - {2}'.format(self.user_name,
                                                  self.user_email, evr))
        expected_str = """* {0} {1}\n{2}""".\
            format(self.date.strftime('%a %b %d %Y'), changelog.packager,
                   '\n'.join(expected_text))
        self.assertEqual(str(changelog), expected_str)

    def test_evr_getters(self):
        """RPMChangelogRecord EVR properties"""
        cases = (('2:2.0.2-0.76', '2', '2.0.2', '0.76'),
                 ('7.6-1.cloudlinux', '0', '7.6', '1.cloudlinux'),
                 (None, None, None, None))
        for evr, epoch, version, release in cases:
            for use_delimiter in (True, False):
                packager = self.__generate_packager(version=evr,
                                                    use_delimiter=use_delimiter)
                changelog = RPMChangelogRecord(self.date, packager,
                                               self.changelog_text)
                self.assertEqual(changelog.evr, evr)
                self.assertEqual(changelog.epoch, epoch)
                self.assertEqual(changelog.version, version)
                self.assertEqual(changelog.release, release)

    def test_format_changelog_text(self):
        """RPMChangelogRecord.format_changelog_text adds "-" at the beginning"""
        text = ['just a simple changelog string',
                '- formatted string',
                'another changelog string']
        expected = [line if line.startswith('-') else '- {0}'.format(line)
                    for line in text]
        self.assertEqual(RPMChangelogRecord.format_changelog_text(text),
                         expected)

    def __generate_packager(self, name=None, email=None, version=None,
                            use_delimiter=True):
        if version:
            delimiter = ' - ' if use_delimiter else ' '
        else:
            version = delimiter = ''
        return '{0} <{1}>{2}{3}'.format(name or self.user_name,
                                        email or self.user_email, delimiter,
                                        version)
