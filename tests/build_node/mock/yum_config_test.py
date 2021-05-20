# -*- mode:python; coding:utf-8; -*-
# author: Diego Marfil <dmarfil@cloudlinux.com>
# created: 2018-10-09

"""Yum config module unit tests."""

import unittest

from build_node.mock.yum_config import *

__all__ = ['TestYumConfig']


class TestYumConfig(unittest.TestCase):

    def setUp(self):

        auth_params = {}
        yum_repos = []
        repo = {'name': u'cl7-os',
                'url': u'http://koji.cloudlinux.com/cloudlinux/7/updates'
                       u'-testing/x86_64/'}
        yum_repos.append(YumRepositoryConfig(repositoryid=repo['name'],
                                             name=repo['name'],
                                             baseurl=repo['url'],
                                             **auth_params))
        yum_exclude = u'test-package'
        self.yum_config = YumConfig(exclude=yum_exclude, rpmverbosity='info',
                                    repositories=yum_repos)

    def test_render_config(self):
        """render_config generates a yum repository configuration file"""

        self.assertEqual(self.yum_config.render_config(), 'config_opts['
                         '"yum.conf"] = """\n'
                         '[main]\nassumeyes = 1\ncachedir = '
                         '/var/cache/yum\ndebuglevel = 1\nexclude = '
                         'test-package\ngpgcheck = 0\nlogfile = '
                         '/var/log/yum.log\nobsoletes = 1\nreposdir = '
                         '/dev/null\nretries = 20\nrpmverbosity = '
                         'info\nsyslog_device = \nsyslog_ident = mock\n\n['
                         'cl7-os]\nbaseurl = '
                         'http://koji.cloudlinux.com/cloudlinux/7/updates'
                         '-testing/x86_64/\nenabled = 1\nname = '
                         'cl7-os\n\n"""\n')

