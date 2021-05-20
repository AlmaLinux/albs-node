# -*- mode:python; coding:utf-8; -*-
# author: Vasily Kleschov <vkleschov@cloudlinux.com>
# created: 2017-12-27

"""Cloudlinux implementation of config for pbuilder"""

import os

__all__ = ['PbuilderConfig']


class PbuilderConfig(object):

    def __init__(self, pbuilder_dir, distribution, mirrorsite, components,
                 arch="amd64", othermirror=None, debootstrapopts=None):
        """

        Parameters
        ----------
        pbuilder_dir : str or unicode
            pbuilder working directory. Here will be stored environment
            tarballs, lock files, etc.
        distribution : str or unicode
            Distribution codename (e.g. jessie, wheezy).
        mirrorsite : str or unicode
            Debian/Ubuntu mirror URL to use for pbuilder initialization.
        components : str or unicode
            Distribution components list (e.g. "main contrib non-free" for
            Debian). See COMPONENTS in the pbuilder manual for details.
        arch : str or unicode, optional
            Target architecture, default is amd64.
        othermirror : list of str, optional
            Additional mirrors (e.g. local repositories) list. See OTHERMIRROR
            in the pbuilder manual for details.
        debootstrapopts : dict, optional
            Contains additional options for bootstrapping command. See
            DEBOOTSTRAPOPTS in the pbuilder manual for details.
        """
        self.__configs_dir = pbuilder_dir
        self.dist = distribution
        self.arch = arch
        env_name = "{0}-{1}".format(distribution, arch)
        self.id = env_name
        env_dir = os.path.join(pbuilder_dir, env_name)
        aptcache = os.path.join(env_dir, "aptcache")
        basetgz = os.path.join(pbuilder_dir,
                               "{0}-base.tgz".format(env_name))
        buildresult = os.path.join(env_dir, "result")
        buildplace = os.path.join(env_dir, "build")
        self.__config = {
            "APTCACHE": aptcache,
            "BASETGZ": basetgz,
            "BUILDRESULT": buildresult,
            "BUILDPLACE": buildplace,
            "DEBBUILDOPTS": "-uc -us",
            "DISTRIBUTION": distribution,
            "MIRRORSITE": mirrorsite,
            "COMPONENTS": components,
            # When building non-native package, need to specify 2 environment
            # variables that describe desired architecture
            "ARCH": arch,
            "ARCHITECTURE": arch,
            "APTCACHEHARDLINK": "no",
            "PBUILDERSATISFYDEPENDSCMD":
                "/usr/lib/pbuilder/pbuilder-satisfydepends-experimental",
            "DEBOOTSTRAPOPTS": [],
            "EXTRAPACKAGES": "apt-transport-https ca-certificates"
        }
        # Installing packages of non-native platform can be done only with APT
        # resolver script
        if arch == 'armhf':
            self.__config["PBUILDERSATISFYDEPENDSCMD"] = \
                "/usr/lib/pbuilder/pbuilder-satisfydepends-apt"
        bootstrap_opts = {"arch": arch,
                          "include": "apt devscripts build-essential fakeroot",
                          "variant": "buildd"}
        if debootstrapopts:
            bootstrap_opts.update(debootstrapopts)
        for opt, val in bootstrap_opts.items():
            self.__config["DEBOOTSTRAPOPTS"].extend([f"--{opt}", val])
        if othermirror:
            self.__config["OTHERMIRROR"] = " | ".join(othermirror)

    def __str__(self):
        return PbuilderConfig.generate_config(self.config_dict)

    @property
    def config_dict(self):
        return self.__config

    @staticmethod
    def generate_config(config):
        """
        Renders configuration file content.

        Parameters
        ----------
        config : dict
            pbuilder configuration options.

        Returns
        -------
        str
            Configuration file content.
        """

        def format_str(key, val):
            if key == "DEBOOTSTRAPOPTS":
                return "{0}=({1})".format(key, " ".join(['"{0}"'.format(i)
                                                         for i in val]))
            return "{0}=\"{1}\"".format(key, val)

        return "{0}\n".format("\n".join([format_str(k, v)
                                         for k, v in config.items()]))
