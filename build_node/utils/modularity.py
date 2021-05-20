# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2020-02-03

"""
Utility functions for modularity support in RPM-based distributions.
"""

import re
import collections
import datetime
import gzip
import json
import hashlib

# noinspection PyPackageRequirements
import gi
gi.require_version('Modulemd', '2.0')
# noinspection PyPackageRequirements,PyUnresolvedReferences
from gi.repository import Modulemd

from build_node.errors import DataNotFoundError, DataSchemaError
from build_node.utils.file_utils import is_gzip_file
from build_node.ported import to_unicode


__all__ = ['generate_stream_version',
           'get_stream_build_deps',
           'get_stream_runtime_deps',
           'calc_stream_build_context',
           'calc_stream_runtime_context',
           'calc_stream_context',
           'calc_stream_dist_macro',
           'is_modular_platform',
           'ModuleTemplateWrapper']


def generate_stream_version(platform):
    """
    Generates a module stream version.

    Parameters
    ----------
    platform : dict
        Target build platform.

    Returns
    -------
    long
        Module stream version.

    Raises
    ------
    build_node.errors.DataNotFoundError
        If a build platform has no module_version_prefix defined.
    """
    prefix = platform.get('modularity',
                          {}).get('platform', {}).get('module_version_prefix')
    if not prefix:
        raise DataNotFoundError('module_version_prefix is not defined')
    ts = datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')
    return int('{0}{1}'.format(prefix, ts))


def get_stream_build_deps(stream):
    """
    Extracts a module stream's build dependencies.

    Parameters
    ----------
    stream : gi.repository.Modulemd.ModuleStreamV2
        Module stream object.

    Returns
    -------
    dict
        Module stream's build dependencies.

    Raises
    ------
    ValueError
        If multiple dependency stream versions are specified.
    """
    build_deps = {}
    # try to extract a detailed requirements list from the
    # xmd['mbs']['buildrequires'] section first
    xmd = stream.get_xmd()
    if xmd:
        build_deps = xmd.get('mbs', {}).get('buildrequires')
        if build_deps:
            return build_deps
    # convert dependencies['buildrequires'] to the xmd-like format
    for deps in stream.get_dependencies():
        for name in deps.get_buildtime_modules():
            streams = deps.get_buildtime_streams(name)
            if len(streams) > 1:
                raise ValueError('multiple stream versions are not supported')
            if streams:
                build_deps[name] = {'stream': streams[0]}
    return build_deps


def get_stream_runtime_deps(stream):
    """
    Extracts a module stream's runtime dependencies.

    Parameters
    ----------
    stream : gi.repository.Modulemd.ModuleStreamV2
        Module stream object.

    Returns
    -------
    dict
        Module stream's runtime dependencies.
    """
    requires = {}
    for deps in stream.get_dependencies():
        for name in deps.get_runtime_modules():
            streams = deps.get_runtime_streams(name)
            requires[name] = requires.get(name, set()).union(streams)
    return {name: sorted(list(streams)) for name, streams in requires.items()}


def calc_stream_build_context(build_deps):
    """
    Calculates a context hash of module stream's build requirements.

    Parameters
    ----------
    build_deps : dict
        Module stream's build requirements.

    Returns
    -------
    str
        Module stream's build requirements context hash.
    """
    requires = {name: info['stream'] for name, info in build_deps.items()}
    js = json.dumps(collections.OrderedDict(sorted(requires.items())))
    return hashlib.sha1(js.encode('utf-8')).hexdigest()


def calc_stream_runtime_context(runtime_deps):
    """
    Calculates the context hash of module stream's runtime dependencies.

    Parameters
    ----------
    runtime_deps : dict
        Module stream's runtime dependencies.

    Returns
    -------
    str
        Module stream's runtime dependencies context hash.
    """
    requires = {dep: sorted(list(streams))
                for dep, streams in runtime_deps.items()}
    js = json.dumps(collections.OrderedDict(sorted(requires.items())))
    return hashlib.sha1(js.encode('utf-8')).hexdigest()


def calc_stream_context(stream):
    """
    Calculates a module stream's context based on build and runtime contexts.

    Parameters
    ----------
    stream : gi.repository.Modulemd.ModuleStreamV2
        Module stream object.

    Returns
    -------
    str
        Module stream's context.
    """
    build_deps = get_stream_build_deps(stream)
    build_context = calc_stream_build_context(build_deps)
    runtime_deps = get_stream_runtime_deps(stream)
    runtime_context = calc_stream_runtime_context(runtime_deps)
    hashes = '{0}:{1}'.format(build_context, runtime_context)
    return hashlib.sha1(hashes.encode('utf-8')).hexdigest()[:8]


def calc_stream_dist_macro(stream, platform, build_index=None, template=False):
    """
    Calculates a modular package %{dist} macros value.

    Parameters
    ----------
    stream : gi.repository.Modulemd.ModuleStreamV2
        Module stream object.
    platform : dict
        Target build platform.
    build_index : int
        Numeric module build identifier.
    template : bool
        If true, instead of real build_index value,
        will be used ${BUILD_INDEX} string.

    Returns
    -------
    str
        Modular package %{dist} macros value.
    """
    dist_str = '.'.join([stream.get_module_name(),
                         stream.get_stream_name(),
                         str(stream.get_version()),
                         str(stream.get_context())]).encode('utf-8')
    dist_hash = hashlib.sha1(dist_str).hexdigest()[:8]
    prefix = platform['modularity']['platform']['dist_tag_prefix']
    if template:
        return '.module_{prefix}+${{BUILD_INDEX}}+{dist_hash}'.format(
            prefix=prefix, dist_hash=dist_hash
        )
    return '.module_{prefix}+{build_index}+{dist_hash}'.format(
        prefix=prefix, build_index=build_index, dist_hash=dist_hash
    )


def is_modular_platform(platform):
    """
    Check if a given build platform supports modularity.

    Parameters
    ----------
    platform : dict
        Target build platform.

    Returns
    -------
    bool
        True if a build platform supports modularity, False otherwise.
    """
    return 'modularity' in platform


def create_defaults(module_name):
    """
    Create new modulemd defaults object.

    Parameters
    ----------
    module_name : str
        Defaults module name.

    Returns
    -------
    Modulemd.DefaultsV1
        Created modulemd defaults.
    """
    return Modulemd.DefaultsV1.new(module_name)


def dump_stream_to_yaml(stream):
    """
    Returns a module stream's YAML string representation.

    Parameters
    ----------
    stream : gi.repository.Modulemd.ModuleStreamV2
        Module stream object.

    Returns
    -------
    str
    """
    module_index = Modulemd.ModuleIndex.new()
    module_index.add_module_stream(stream)
    return module_index.dump_to_string()


def extract_stream_metadata(module, stream):
    """
    Returns a module stream's general info.

    Parameters
    ----------
    module: gi.repository.Modulemd.Module
        Module object.
    stream : gi.repository.Modulemd.ModuleStreamV2
        Module stream object.

    Returns
    -------
    dict
    """
    response = {
        'name': stream.get_module_name(),
        'stream': stream.get_stream_name(),
        'arch': stream.get_arch(),
        'version': stream.get_version(),
        'context': stream.get_context(),
        'summary': stream.get_summary(),
        'is_default_stream': False,
        'default_profiles': [],
        'yaml_template': dump_stream_to_yaml(stream)
    }
    defaults = module.get_defaults()
    if not defaults:
        return response
    default_stream = defaults.get_default_stream()
    response['is_default_stream'] = stream.get_stream_name() == default_stream
    response['default_profiles'] = defaults.get_default_profiles_for_stream(
        stream.get_stream_name())
    return response


def module_from_file(file_path, module_name=None, module_stream=None):
    """
    Makes modulemd module object from given file.

    Parameters
    ----------
    file_path : str
        Path to modules.yaml(.gz)

    Returns
    -------
    Modulemd.ModuleIndex
        Created modulemd object.
    """
    file_open = open
    if is_gzip_file(file_path):
        file_open = gzip.open
    with file_open(file_path, 'rb') as fd:
        template = fd.read()
    template = to_unicode(template)
    modules_idx = Modulemd.ModuleIndex.new()
    if module_name is None:
        ret, failures = modules_idx.update_from_string(template, strict=True)
        if not ret:
            raise DataSchemaError('can not parse modules.yaml template')
    else:
        stream = Modulemd.ModuleStreamV2.read_string(
            template, True, module_name, module_stream)
        if not stream:
            raise DataSchemaError('can not parse modules.yaml template')
        modules_idx.add_module_stream(stream)
    return modules_idx


def merge_modules(modules_a, modules_b):
    """
    Merge two modules.yaml into one object.

    Parameters
    ----------
    modules_a : Modulemd.ModuleIndex
        Module to merge.
    modules_b : Modulemd.ModuleIndex
        Module to merge.

    Returns
    -------
    Modulemd.ModuleIndex
        Merged modulemd object.
    """
    merger = Modulemd.ModuleIndexMerger.new()
    merger.associate_index(modules_b, 0)
    merger.associate_index(modules_a, 0)
    return merger.resolve()


class RpmArtifact:

    def __init__(self, name, version, release, source=None,
                 epoch=None, arch=None):
        self._name = name
        self._version = version
        self._release = release
        self._source = source
        self._epoch = epoch
        self._arch = arch

    def as_ref(self):
        return f'{self.name}-{self.version}-{self.release}'

    def as_artifact(self):
        if self._source is not None:
            return self._source
        epoch = self._epoch if self._epoch else '0'
        return f'{self.name}-{epoch}:{self.version}-{self.release}.{self.arch}'

    def as_src_rpm(self):
        return f'{self.name}-{self.version}-{self.release}.src.rpm'

    @staticmethod
    def from_str(artifact):
        """
        Parse package name/epoch/version/release from package artifact record.

        Parameters
        ----------
        artifact : str
            Stream artifact record.

        Returns
        -------
        RpmArtifact or None
            Parsed package metadata or None.
        """
        regex = re.compile(
            r'^(?P<name>[\w+-.]+)-'
            r'((?P<epoch>\d+):)?'
            r'(?P<version>\d+?[\w.]*)-'
            r'(?P<release>\d+?[\w.+]*?)'
            r'\.(?P<arch>(i686)|(noarch)|(x86_64)|(aarch64)|(src))(\.rpm)?$'
        )
        result = re.search(regex, artifact)
        if not result:
            return None
        return RpmArtifact(**result.groupdict(), source=artifact)

    @property
    def name(self):
        return self._name

    @property
    def version(self):
        return self._version

    @property
    def release(self):
        return self._release

    @property
    def epoch(self):
        return self._epoch

    @property
    def arch(self):
        return self._arch

    def __str__(self):
        return self.as_artifact()


class ModuleTemplateWrapper(object):

    """Provides common functions for manipulating modules.yaml templates."""

    def __init__(self, template, module_name, stream_name,
                 stream_version=None, stream_context=None, stream_arch=None):
        """
        Parameters
        ----------
        template : str
            Modules.yaml template content.
        module_name : str
            Target module name.
        stream_name : str
            Target stream name.
        stream_version : long, optional
            Target stream version. Latest version will be processed if omitted.
        stream_context : str, optional
            Target stream context. It is the required argument if
            `stream_version` is set.
        stream_arch : str, optional
            Target stream architecture. It is the required argument if
            `stream_version` is set.

        Raises
        ------
        build_node.errors.DataSchemaError
            If template is not valid.
        build_node.errors.DataNotFoundError
            If given module or stream definition is not found.
        """
        self._template = to_unicode(template)
        self._modules_idx = Modulemd.ModuleIndex.new()
        ret, failures = self._modules_idx.update_from_string(self._template,
                                                             strict=True)
        if not ret:
            raise DataSchemaError('can not parse modules.yaml template')
        self._module = self._modules_idx.get_module(module_name)
        if not self._module:
            raise DataNotFoundError(f'module {module_name} is not found')
        if stream_version is None:
            self.stream = self._get_stream_by_name(self._module, stream_name)
        else:
            self.stream = self._get_stream_by_nsvca(
                self._module, stream_name, stream_version, stream_context,
                stream_arch
            )

    @classmethod
    def init_from_file(cls, file_path, module_name, stream_name,
                       stream_version=None, stream_context=None,
                       stream_arch=None):
        file_open = open
        if is_gzip_file(file_path):
            file_open = gzip.open
        with file_open(file_path, 'rb') as fd:
            template = fd.read()
        return ModuleTemplateWrapper(template, module_name, stream_name,
                                     stream_version=stream_version,
                                     stream_context=stream_context,
                                     stream_arch=stream_arch)

    @classmethod
    def from_index(cls, index, module_name, stream_name,
                   stream_version=None, stream_context=None,
                   stream_arch=None):
        return ModuleTemplateWrapper(
            index.dump_to_string(), module_name, stream_name,
            stream_version=stream_version,
            stream_context=stream_context,
            stream_arch=stream_arch
        )

    def add_rpm_artifact(self, rpm_pkg):
        """
        Adds an RPM package build artifact to a module stream.

        Parameters
        ----------
        rpm_pkg : dict or str
            RPM package.
        """
        if isinstance(rpm_pkg, str):
            self.stream.add_rpm_artifact(rpm_pkg)
            return
        epoch = rpm_pkg.get('epoch', 0)
        rpm_str = '{name}-{epoch}:{version}-{release}.{arch}'.format(
            name=rpm_pkg['name'], epoch=epoch, version=rpm_pkg['version'],
            release=rpm_pkg['release'], arch=rpm_pkg['arch']
        )
        self.stream.add_rpm_artifact(rpm_str)

    def generate_version_context(self, platform, version=None):
        """
        Generates version and context values for a module stream.

        Parameters
        ----------
        platform : dict
            Target build platform.
        """
        self.stream.set_version(generate_stream_version(platform))
        if version is not None:
            self.stream.set_version(int(version))
        self.stream.set_context(calc_stream_context(self.stream))

    def set_stream_arch(self, arch):
        """
        Sets a module stream build architecture.

        Parameters
        ----------
        arch : str
            Target architecture.
        """
        self.stream.set_arch(arch)

    def set_component_ref(self, srpm_name, ref):
        """
        Sets a module component reference.

        Parameters
        ----------
        srpm_name : str
            Source RPM (component) name.
        ref : str
            Component reference.
        """
        component = self.stream.get_rpm_component(srpm_name)
        if not component:
            raise DataNotFoundError('component {0} is not found'.
                                    format(srpm_name))
        component.set_ref(ref)

    def render(self):
        """
        Renders modules.yaml file.

        Returns
        -------
        str
            Rendered modules.yaml file content.
        """
        return self._modules_idx.dump_to_string()

    def cleanup_arches(self, allowed_list):
        """
        Removes architectures which aren't supported by CL from components.

        Parameters
        ----------
        allowed_list : list
            List of architectures allowed for the component
        """
        for component_name in self.stream.get_rpm_component_names():
            component = self.stream.get_rpm_component(component_name)
            arches = component.get_arches()[:]
            component.reset_arches()
            for arch in arches:
                if arch in allowed_list:
                    component.add_restricted_arch(arch)

    def debrand_tracker(self, platform):
        """
        Replaces RHEL bugzilla URL with ours if necessary.

        Parameters
        ----------
        platform : dict
            Build platform definition.
        """
        our_tracker = platform['modularity']['platform']['tracker']
        upstream_tracker = self.stream.get_tracker()
        if upstream_tracker and 'redhat' in upstream_tracker:
            self.stream.set_tracker(our_tracker)

    def iter_mock_definitions(self):
        """
        Iterate and parse buildopts modules.yaml section.

        Returns
        -------
        generator
            (name, value) for every parsed mock macros.
        """
        buildopts = self.stream.get_buildopts()
        if buildopts is None:
            return
        macros_template = buildopts.get_rpm_macros() or ''
        for macros in macros_template.splitlines():
            macros = macros.strip()
            if not macros or macros.startswith('#'):
                continue
            name, *value = macros.split()
            # erasing %...
            name = name[1:]
            value = ' '.join(value)
            yield name, value

    def iter_dependencies(self):
        """
        Iterate over stream dependencies.

        Returns
        -------
        generator
            (module, stream) for every module dependency.
        """
        for dep in self.stream.get_dependencies():
            for module in dep.get_buildtime_modules():
                for stream in dep.get_buildtime_streams(module):
                    yield module, stream

    def get_rpm_artifacts(self, only_src=False, raw=False):
        artifacts = []
        for art_str in self.stream.get_rpm_artifacts():
            artifact = RpmArtifact.from_str(art_str)
            if only_src and not artifact.arch == 'src':
                continue
            if raw:
                artifact = art_str
            artifacts.append(artifact)
        return artifacts

    def clear_rpm_artifacts(self):
        self.stream.clear_rpm_artifacts()

    def iter_rpm_components(self):
        """
        Iterate over stream components.

        Returns
        -------
        generator
            Every item in components.rpm section.
        """
        for component in self.stream.get_rpm_component_names():
            yield self.stream.get_rpm_component(component)

    def get_rpm_component(self, component_name):
        return self.stream.get_rpm_component(component_name)

    @staticmethod
    def _get_stream_by_name(module, stream_name):
        streams = module.get_streams_by_stream_name(stream_name)
        if not streams:
            raise DataNotFoundError('stream {0} is not found'.
                                    format(stream_name))
        return streams[0]

    @staticmethod
    def _get_stream_by_nsvca(module, stream_name, stream_version,
                             stream_context, stream_arch):
        stream = module.get_stream_by_NSVCA(stream_name, stream_version,
                                            stream_context, stream_arch)
        if not stream:
            raise DataNotFoundError('stream {0} is not found'.
                                    format(stream_name))
        return stream

    @property
    def module_name(self):
        """
        Module name.

        Returns
        -------
        str
        """
        return self.stream.get_module_name()

    @property
    def stream_name(self):
        """
        Module stream name.

        Returns
        -------
        str
        """
        return self.stream.get_stream_name()

    @property
    def stream_version(self):
        """
        Module stream version.

        Returns
        -------
        long
        """
        return self.stream.get_version()

    @property
    def stream_context(self):
        """
        Module stream context.

        Returns
        -------
        str
        """
        return self.stream.get_context()

    @property
    def stream_arch(self):
        """
        Module stream arch.

        Returns
        -------
        str
        """
        return self.stream.get_arch()
