# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-09-27

"""
mock configuration file generator.

Examples
--------
Defining mock configuration:

>>> mock_config = MockConfig(root='cl7-stable-x86_64', target_arch='x86_64', \
                             chroot_setup_cmd='install @buildsys-build', \
                             dist='7', use_boostrap_container=False, \
                             use_nspawn=False)

Which produces the following configuration file:

    config_opts["root"] = "cl7-stable-x86_64"
    config_opts["chroot_setup_cmd"] = "install @buildsys-build"
    config_opts["dist"] = "7"
    config_opts["legal_host_arches"] = ["x86_64"]
    config_opts["use_bootstrap_container"] = False
    config_opts["use_nspawn"] = False


Enabling tmpfs mock plugin:

>>> tmpfs_plugin = MockPluginConfig(name='tmpfs', enable=True, \
                                    required_ram_mb=1024, \
                                    max_fs_size='2048m', mode='0755', \
                                    keep_mounted=False)
>>> mock_config.add_plugin(tmpfs_plugin)

Which produces the following configuration file section:

    config_opts["plugin_conf"]["tmpfs_enable"] = True
    config_opts["plugin_conf"]["tmpfs_opts"] = {}
    config_opts["plugin_conf"]["tmpfs_opts"]["keep_mounted"] = False
    config_opts["plugin_conf"]["tmpfs_opts"]["max_fs_size"] = "2048m"
    config_opts["plugin_conf"]["tmpfs_opts"]["mode"] = "0755"
    config_opts["plugin_conf"]["tmpfs_opts"]["required_ram_mb"] = 1024


Embedding a file into the mock chroot:

>>> yarnrc = MockChrootFile('/usr/etc/yarnrc', \"\"\" \
https-proxy "http://127.0.0.1/8090/" \
proxy "http://127.0.0.1/8090/" \
strict-ssl false \
\"\"\"
>>> mock_config.add_file(yarnrc)

Which produces the following configuration file section:

    config_opts["files"]["/usr/etc/yarnrc"] = \"\"\"
    https-proxy "http://127.0.0.1/8090/"
    proxy "http://127.0.0.1/8090/"
    strict-ssl false
    \"\"\"
"""

import copy
from io import StringIO
import hashlib
import json
import logging


__all__ = ['MockConfig', 'MockPluginConfig', 'MockBindMountPluginConfig',
           'MockChrootFile', 'MockPluginChrootScanConfig']


def to_mock_config_string(value):
    """
    Converts the given value to a mock configuration file compatible string.

    Parameters
    ----------
    value : bool or int or str or list or tuple or None
        Value to convert.

    Returns
    -------
    str
        mock configuration file compatible string representation.

    Raises
    ------
    ValueError
        If value type isn't supported.
    """
    if value is None or isinstance(value, (bool, int)):
        return str(value)
    elif isinstance(value, (str, list, tuple)):
        return json.dumps(value)
    raise ValueError('unsupported type {0} of "{1}" value'.
                     format(type(value), value))


class MockConfig(object):

    """
    mock configuration file generator.
    """

    def __init__(self, target_arch, legal_host_arches=None,
                 chroot_setup_cmd='install @buildsys-build', dist=None,
                 releasever=None, files=None, yum_config=None, **kwargs):
        """
        Mock configuration initialization.

        Parameters
        ----------
        target_arch : str
            Target architecture (config_opts['target_arch']).
        legal_host_arches : list or tuple, optional
            List of acceptable build architectures. Default values are
            dependant on a `target_arch` value:

            x86_64 => ('x86_64',)
            i386 / i586 / i686 => ('i386', 'i586', 'i686', 'x86_64')
        chroot_setup_cmd : str, optional
            Chroot initialization command, the default value is compatible
            with EPEL / Fedora.
        dist : str, optional
            Distribution name shortening (e.g. 'el6') which is used for
            --resultdir variable substitution.
        releasever : str, optional
            Distribution release version (e.g. '7').
        files : list, optional
            List of chroot files (config_opts['files']).
        yum_config : build_node.mock.yum_config.YumConfig
            Yum configuration.

        Raises
        ------
        ValueError
            If `legal_host_arches` default value could't be detected for the
            specified target architecture.

        Warnings
        --------
        Do not pass a `root` (a chroot name) keyword argument if you are going
        to use a config with the mock environments manager since it generates
        the name based on a configuration checksum and an internal counter.

        Notes
        -----
        See mock(1) man page for `dist` / --resultdir description.

        It's possible to pass additional ``config_opts`` section definitions
        with `kwargs`. See /etc/mock/*.cfg for examples.
        """
        if not legal_host_arches:
            legal_host_arches = self.get_default_legal_host_arches(target_arch)
        self.__config_opts = {'target_arch': target_arch,
                              'legal_host_arches': legal_host_arches,
                              'chroot_setup_cmd': chroot_setup_cmd,
                              'dist': dist, 'releasever': releasever}
        self.__config_opts.update(**kwargs)
        self.__files = {}
        if files:
            for chroot_file in files:
                self.add_file(chroot_file)
        self.__plugins = {}
        self.__yum_config = yum_config

    def add_module_install(self, module_name):
        """
        Adds a module to module_install configuration.

        Parameters
        ----------
        module_name : str
            Module Name

        Raises
        ------
        ValueError
            If a module with the same name is already added to the module
                configuration.
            Or if empty name was specified

        Returns
        -------
        None
        """
        self._add_module('module_install', module_name)

    def add_module_enable(self, module_name):
        """
        Adds a module to module_enable configuration.

        Parameters
        ----------
        module_name : str
            Module Name

        Raises
        ------
        ValueError
            If a module with the same name is already added to the module
                configuration.
            Or if empty name was specified

        Returns
        -------
        None
        """
        self._add_module('module_enable', module_name)

    def add_file(self, chroot_file):
        """
        Adds a chroot file to the configuration.

        Parameters
        ----------
        chroot_file : MockChrootFile
            Chroot file.

        Raises
        ------
        ValueError
            If a file with the same name is already added to the configuration.
        """
        if chroot_file.name in self.__files:
            raise ValueError('file {0} is already added'.
                             format(chroot_file.name))
        self.__files[chroot_file.name] = chroot_file

    def add_plugin(self, plugin):
        """
        Adds a mock plugin to the configuration.

        Parameters
        ----------
        plugin : MockPluginConfig
            mock plugin configuration.

        Raises
        ------
        ValueError
            If a plugin with the same name is already added to the
            configuration.
        """
        if plugin.name in self.__plugins:
            raise ValueError('plugin {0} is already configured'.
                             format(plugin.name))
        self.__plugins[plugin.name] = plugin

    @staticmethod
    def get_default_legal_host_arches(target_arch):
        """
        Returns a list of acceptable build architectures for the specified
        target architecture.

        Parameters
        ----------
        target_arch : str
            Target architecture.

        Returns
        -------
        tuple
            `legal_host_arches` value.

        Raises
        ------
        ValueError
            If `legal_host_arches` default value could't be detected for the
            specified target architecture.
        """
        if target_arch == 'x86_64':
            return 'x86_64',
        elif target_arch in ('i386', 'i586', 'i686'):
            return 'i386', 'i586', 'i686', 'x86_64'
        elif target_arch == 'noarch':
            return 'i386', 'i586', 'i686', 'x86_64', 'noarch', 'aarch64', 'armhf'
        elif target_arch == 'aarch64':
            return 'aarch64',
        # TODO: Investigate if 32-bit packages will really be able to be built on 64-bit ARM
        elif target_arch in ('armhfp', 'armhf'):
            return 'aarch64', 'armhf', 'armhfp'
        elif target_arch in ('ppc64le', ):
            return 'ppc64le'
        elif target_arch in ('s390x', ):
            return 's390x'
        raise ValueError('there is no default_host_arches value for {0} '
                         'architecture'.format(target_arch))

    def set_yum_config(self, yum_config):
        """
        Adds Yum configuration section to the configuration file.

        Parameters
        ----------
        yum_config : build_node.mock.yum_config.YumConfig
            Yum configuration
        """
        self.__yum_config = yum_config

    @staticmethod
    def render_config_option(option, value):
        """
        Renders ``config_opts`` mock config definition.

        Parameters
        ----------
        option : str
            Option name.
        value : bool or int or str or list or tuple or None
            Option value. Warning: nested dictionaries aren't supported.

        Returns
        -------
        str
            mock configuration file string.
        """
        out = ''
        option = to_mock_config_string(option)
        if isinstance(value, dict):
            for k, v in sorted(value.items()):
                out += 'config_opts[{0}][{1}] = {2}\n'.\
                    format(option, to_mock_config_string(k),
                           to_mock_config_string(v))
        else:
            out += 'config_opts[{0}] = {1}\n'.\
                format(option, to_mock_config_string(value))
        # it is needed til we use EL7 Build Server
        out = out.replace('config_opts["use_bootstrap_container"] = True', 
                'config_opts["use_bootstrap_container"] = False')
        return out

    def dump_to_file(self, config_file, root=None):
        """
        Dumps mock configuration to the specified file.

        Parameters
        ----------
        config_file : str of file-like
            File path or any file-like object.
        root : str, optional
            Chroot configuration name (config_opts['root']), a value passed
            to the constructor will be replaced with this one if specified.
        """
        if not root:
            root = self.__config_opts.get('root')
        fd = open(config_file, 'w') if isinstance(config_file, str) \
            else config_file
        try:
            if root:
                fd.write(self.render_config_option('root', root))
            for option, value in sorted(self.__config_opts.items()):
                if option == 'root' or value is None:
                    continue
                fd.write(self.render_config_option(option, value))
            for plugin in self.__plugins.values():
                fd.write(plugin.render_config())
            if self.__yum_config:
                fd.write(self.__yum_config.render_config())
            for chroot_file in self.__files.values():
                fd.write(chroot_file.render_config())
        finally:
            fd.close() if isinstance(config_file, str) else fd.flush()

    @property
    def config_hash(self):
        """
        Calculates a SHA256 checksum of the configuration.

        Returns
        -------
        str
            Configuration SHA256 checksum.
        """
        # TODO: use module specific logging configuration
        if self.__config_opts.get('root'):
            logging.warning('mock chroot "root" option is defined which is '
                            'not compatible with mock environments manager')
        hasher = hashlib.sha256()
        fd = StringIO()
        self.dump_to_file(fd)
        fd.seek(0)
        hasher.update(fd.read().encode('utf-8'))
        fd.close()
        return hasher.hexdigest()

    def _add_module(self, option, module_name):
        """
        add mock option (module_install or module_enable) to the mock config.

        Parameters
        ----------
        option : str
            module_enable or module_install option
        module_name : str
            name of module to enable/install in config. eg.: perl:5.26

        Raises
        ------
        ValueError
            If a module with the same name is already added to the module
                configuration.
            Or if empty name was specified

        Returns
        -------
        None
        """
        if not module_name:
            raise ValueError('invalid module name')
        if option not in self.__config_opts:
            self.__config_opts[option] = []
        if module_name in self.__config_opts[option]:
            raise ValueError('{0} is already added to the {1}'.
                             format(module_name, option))
        self.__config_opts[option].append(module_name)


class MockPluginConfig(object):

    """
    mock plugin configuration.
    """

    def __init__(self, name, enable, **kwargs):
        """
        mock plugin configuration initialization.

        Parameters
        ----------
        name : str
            Plugin name (e.g. tmpfs).
        enable : bool
            Enable (True) or disable (False) this plugin.

        Notes
        -----
        It's possible to pass additional plugin options with `kwargs`.
        """
        self.__name = name
        self.__enable = enable
        self.__opts = copy.copy(kwargs)

    def render_config(self):
        """
        Dumps a mock plugin configuration as a configuration file string.

        Returns
        -------
        str
            mock plugin configuration.
        """
        out = 'config_opts["plugin_conf"]["{0}_enable"] = {1}\n'. \
            format(self.__name, to_mock_config_string(self.__enable))
        if not self.__enable:
            return out
        out += 'config_opts["plugin_conf"]["{0}_opts"] = {{}}\n'. \
            format(self.__name)
        for key, opt in sorted(self.__opts.items()):
            out += 'config_opts["plugin_conf"]["{0}_opts"][{1}] = {2}\n'. \
                format(self.__name, to_mock_config_string(key),
                       to_mock_config_string(opt))
        return out

    @property
    def name(self):
        """
        mock plugin name.

        Returns
        -------
        str
        """
        return self.__name

    @property
    def enable(self):
        return self.__enable


class MockPluginChrootScanConfig(object):

    """
    mock plugin configuration.
    """

    def __init__(self, name, enable, **kwargs):
        """
        mock plugin configuration initialization.

        Parameters
        ----------
        name : str
            Plugin name (e.g. tmpfs).
        enable : bool
            Enable (True) or disable (False) this plugin.

        Notes
        -----
        It's possible to pass additional plugin options with `kwargs`.
        """
        self.__name = name
        self.__enable = enable
        self.__opts = copy.copy(kwargs)

    def render_config(self):
        """
        Dumps a mock plugin configuration as a configuration file string.

        Returns
        -------
        str
            mock plugin configuration.
        """
        out = 'config_opts["plugin_conf"]["{0}_enable"] = {1}\n'. \
            format(self.__name, to_mock_config_string(self.__enable))
        if not self.__enable:
            return out
        opts_dict = {}
        for key, opt in sorted(self.__opts.items()):
            opts_dict[key] = opt
        out += f'config_opts["plugin_conf"]["{self.__name}_opts"] = {opts_dict}\n'
        return out

    @property
    def name(self):
        """
        mock plugin name.

        Returns
        -------
        str
        """
        return self.__name

    @property
    def enable(self):
        return self.__enable


class MockBindMountPluginConfig(MockPluginConfig):

    """
    Mock bind mount plugin configuration.

    Notes
    -----
    See https://github.com/rpm-software-management/mock/wiki/Plugin-BindMount
    for the plugin description.
    """

    def __init__(self, enable, mounts):
        """
        Mock bind mount plugin configuration initialization.

        Parameters
        ----------
        enable : bool
            Enable (True) or disable (False) this plugin.
        mounts : list of tuple
            List of pairs where first element is a local file system path and
            the second one is a chroot file system path.
        """
        super(MockBindMountPluginConfig, self).__init__('bind_mount', enable)
        self.__mounts = mounts

    def render_config(self):
        """
        Dumps a bind mount plugin configuration as a configuration file string.

        Returns
        -------
        str
            Bind mount mock plugin configuration.
        """
        out = 'config_opts["plugin_conf"]["bind_mount_enable"] = {0}\n'. \
            format(to_mock_config_string(self.enable))
        if not self.enable or not self.__mounts:
            return out
        for local_path, mock_path in self.__mounts:
            out += 'config_opts["plugin_conf"]["bind_mount_opts"]["dirs"].' \
                   'append(("{0}", "{1}"))\n'.format(local_path, mock_path)
        return out


class MockChrootFile(object):

    """Allows file embedding into a mock chroot."""

    def __init__(self, name, content):
        """
        Embedded file initialization.

        Parameters
        ----------
        name : str
            File path.
        content : str
            File content.
        """
        self.__name = name
        self.__content = content

    def render_config(self):
        """
        Dumps an embedded file configuration as a configuration file string.

        Returns
        -------
        str
            Embedded file configuration.
        """
        return 'config_opts["files"]["{0}"] = """{1}"""\n'.\
            format(self.__name, self.__content)

    @property
    def name(self):
        """
        Embedded file path.

        Returns
        -------
        str
        """
        return self.__name
