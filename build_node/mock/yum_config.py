# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2017-09-27

"""
Yum section configuration of a mock configuration file.
"""


from configparser import ConfigParser
from io import StringIO


__all__ = ['YumConfig', 'YumRepositoryConfig']


class BaseYumConfig(object):

    """Base class for YUM configuration generators"""

    @staticmethod
    def render_config_section(section, options):
        cfg = ConfigParser()
        cfg.add_section(section)
        for key, value in sorted(options.items()):
            if key == 'repositoryid':
                continue
            elif key in ('assumeyes', 'best', 'enabled', 'gpgcheck',
                         'obsoletes', 'module_hotfixes'):
                cfg.set(section, key, BaseYumConfig.render_bool_option(value))
            elif key in ('debuglevel', 'retries'):
                cfg.set(section, key, str(value))
            elif key == 'syslog_device':
                cfg.set(section, key, value.strip())
            elif key == 'baseurl':
                if isinstance(value, (list, tuple)):
                    value = "\n        ".join(value)
                cfg.set(section, key, value.strip())
            elif key == 'failovermethod':
                if value not in ('roundrobin', 'priority'):
                    raise ValueError('unsupported failovermethod value {0}'.
                                     format(value))
                cfg.set(section, key, value)
            else:
                cfg.set(section, key,
                        BaseYumConfig.trim_non_empty_string(key, value))
        fd = StringIO()
        cfg.write(fd)
        fd.flush()
        fd.seek(0)
        return fd.read()

    @staticmethod
    def render_bool_option(value):
        """
        Converts a provided value to the yum configuration boolean value.

        Parameters
        ----------
        value : str or bool or int
            A value to convert.

        Returns
        -------
        str
            A yum configuration boolean value.
        """
        return '1' if value and value != '0' else '0'

    @staticmethod
    def trim_non_empty_string(key, value):
        """
        Verifies that given value is a non-empty string.

        Parameters
        ----------
        key : str
            An attribute name to provide an informative error description.
        value : str
            A string value for checking.

        Returns
        -------
        str
            A trimmed non-empty string.

        Raises
        ------
        ValueError
            If value is not a string or it is empty.
        """
        if not isinstance(value, str) or not value.strip():
            raise ValueError('{0} must be a non-empty string'.format(key))
        return value


class YumConfig(BaseYumConfig):

    """YUM section of a mock configuration file"""

    def __init__(self, assumeyes=True, cachedir='/var/cache/yum', debuglevel=1,
                 exclude=None, gpgcheck=False, logfile='/var/log/yum.log',
                 obsoletes=True, proxy=None, reposdir='/dev/null', retries=20,
                 syslog_device='', syslog_ident='mock', rpmverbosity='info',
                 repositories=None, module_platform_id=None, best=None):
        """
        Yum configuration initialization.

        Parameters
        ----------
        assumeyes : bool, optional
            Assume that the answer to any question which would be asked is yes.
        cachedir : str, optional
            Yum cache storage directory.
        debuglevel : int, optional
            Turns up or down the amount of things that are printed (0 - 10).
        exclude : str, optional
            Packages to exclude from yum update
        gpgcheck : bool, optional
            Check GPG signatures if True.
        logfile : str, optional
            Log file path.
        obsoletes : bool, optional
            Include package obsoletes in dependencies calculation if True.
        proxy : str, optional
            Proxy server URL.
        reposdir : str, optional
            Absolute path to the directory where .repo files are located,
            usually we don't need it in mock.
        retries : int, optional
            Number of tries to retrieve a file before returning an error.
        syslog_device : str, optional
            Syslog messages log URL. Usually we don't need it in mock.
        syslog_ident : str, optional
            Program name for syslog messages.
        rpmverbosity : str, optional
            Debug level to for rpm scriptlets, the supported values are:
            info, critical, emergency, error, warn or debug.
        repositories : list, optional
            Yum repositories list.
        module_platform_id : str, optional
            Target (modular) platform name and stream, see `man dnf.conf`
            for details.
        best : bool, optional

        """
        if rpmverbosity is not None and \
                rpmverbosity not in ('info', 'critical', 'emergency', 'error',
                                     'warn', 'debug'):
            raise ValueError('invalid rpmverbosity value "{0}"'.
                             format(rpmverbosity))
        self.__data = {k: v for (k, v) in iter(list(locals().items()))
                       if k not in ('self', 'repositories') and v is not None}
        self.__repos = {}
        if repositories:
            for repo in repositories:
                self.add_repository(repo)

    def add_repository(self, repo):
        """
        Adds a YUM repository to the configuration.

        Parameters
        ----------
        repo : RepositoryConfig
            A YUM repository.

        Raises
        ------
        ValueError
            If repository name isn't unique for this configuration or given
            repository type isn't supported.
        """
        if not isinstance(repo, YumRepositoryConfig):
            raise ValueError('repository type {0} is not supported'.
                             format(type(repo)))
        if repo.name in self.__repos:
            raise ValueError('repository {0} is already added'.
                             format(repo.name))
        self.__repos[repo.name] = repo

    def render_config(self):
        out = 'config_opts["yum.conf"] = """\n'
        out += self.render_config_section('main', self.__data)
        for repo_name, repo in sorted(self.__repos.items()):
            out += repo.render_config()
        out += '"""\n'
        return out


class YumRepositoryConfig(BaseYumConfig):

    """Yum repository configuration generator"""

    def __init__(self, repositoryid, name, priority, baseurl=None, mirrorlist=None,
                 enabled=True, failovermethod=None,
                 gpgcheck=None, gpgkey=None, username=None, password=None,
                 sslverify=None, module_hotfixes=None):
        """
        Yum repository initialization.

        Parameters
        ----------
        repositoryid : str
            A unique name for each repository, single word.
        name : str
            A human readable repository description.
        priority : str
            A repository priority
        baseurl : str or list or None
            A URL to the directory where 'repodata' directory is located.
            Multiple URLs could be provided as a list.
        mirrorlist : str or None
            A URL to the file containing a list of baseurls.
        enabled : bool or int or None
            Enable (True or 1) or disable (False or 0) this repository.
        failovermethod : str or None
            Either 'roundrobin' or 'priority'.
        gpgcheck : bool or int or None
            Perform a GPG signature check on packages if True / 1.
        gpgkey : str or None
            An URL of the repository GPG key.
        username : str or None
            HTTP Basic authentication login.
        password : str or None
            HTTP Basic authentication password.
        sslverify : str or None
            Enable SSL certificate verification if set to 1.

        Notes
        -----
        See yum.conf(5) man page for detailed arguments description.
        """
        self.__data = {k: v for (k, v) in locals().items()
                       if k != 'self' and v is not None}

    def render_config(self):
        """
        Generates a yum repository configuration.

        Returns
        -------
        str
            A YUM repository configuration.
        """
        section = self.trim_non_empty_string('repositoryid',
                                             self.__data['repositoryid'])
        return self.render_config_section(section, self.__data)

    @property
    def name(self):
        """
        Repository name.

        Returns
        -------
        str
        """
        return self.__data['name']
