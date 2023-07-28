# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 04.11.2014 23:41
# description: RPM spec files parsing module.


import collections
import datetime
import os
import re
import tempfile
import time
from collections import namedtuple
from functools import cmp_to_key

import rpm
from build_node.utils.rpm_utils import string_to_version, flag_to_string
from build_node.ported import to_unicode, cmp


__all__ = ["SpecParser", "PackageFeature", "ChangelogRecord", "SpecPatch",
           "SpecSource", "SpecParseError"]


class SpecParseError(ValueError):
    pass


class RPMChangelogRecord(namedtuple('RPMChangelogRecord',
                                    ['date', 'packager', 'text'])):

    @staticmethod
    def generate(date, user_name, user_email, evr, text):
        """
        An alternative initialization method with EVR argument.

        Parameters
        ----------
        date : datetime.date
            Changelog record datestamp.
        user_name : str
            User name.
        user_email : str
            User e-mail address.
        evr : str
            EVR (epoch, version, release).
        text : str or list
            Changelog text.

        Returns
        -------
        RPMChangelogRecord
            Initialized changelog record.
        """
        packager = '{0} <{1}> - {2}'.format(user_name, user_email, evr)
        text = [text] if isinstance(text, str) else text
        formatted_text = RPMChangelogRecord.format_changelog_text(text)
        return RPMChangelogRecord(date, packager, formatted_text)

    @staticmethod
    def format_changelog_text(text):
        """
        Formats a changelog text according to an RPM spec standards.

        Parameters
        ----------
        text : list of str
            Changelog text.

        Returns
        -------
        list of str
            Formatted changelog text.
        """
        formatted = []
        for line in text:
            if not line.startswith('-'):
                line = '- {0}'.format(line)
            formatted.append(line)
        return formatted

    @property
    def evr(self):
        """
        Returns a package EVR (epoch, version and release) substring of a
        changelog record.

        Returns
        -------
        str or None
            Package EVR substring or None if there was no version
            information found.
        """
        re_rslt = re.search(r'[\s-]+(\d+[-\w:.]*)$', self.packager)
        return re_rslt.group(1) if re_rslt else None

    @property
    def epoch(self):
        """
        Returns a package epoch from a changelog record.

        Returns
        -------
        str or None
            Package epoch if a version information is present, None otherwise.
            Note: it will return "0" if epoch is not specified.
        """
        return string_to_version(self.evr)[0]

    @property
    def version(self):
        """
        Returns a package version from a changelog record.

        Returns
        -------
        str or None
            Package version if found.
        """
        return string_to_version(self.evr)[1]

    @property
    def release(self):
        """
        Returns a package release from a changelog record.

        Returns
        -------
        str or None
            Package release if found.
        """
        return string_to_version(self.evr)[2]

    def __str__(self):
        header = '* {0} {1}'.format(self.date.strftime('%a %b %d %Y'),
                                    self.packager)
        return '{0}\n{1}'.format(header, '\n'.join(self.text))

    def __unicode__(self):
        header = '* {0} {1}'.format(self.date.strftime('%a %b %d %Y'),
                                    self.packager)
        return '{0}\n{1}'.format(header, '\n'.join(self.text))


class PackageFeature(collections.namedtuple("PackageFeature",
                                            ["name", "flag", "evr", "epoch",
                                             "version", "release"])):

    def to_dict(self):
        d = {"name": self.name}
        if self.flag:
            d["flag"] = self.flag
            d["evr"] = self.evr
        return d


class ChangelogRecord(collections.namedtuple("ChangelogRecord",
                                             ["date", "packager", "text"])):

    @property
    def evr(self):
        re_rslt = re.search(r"[\s-]+(\d+[-\w:\.]*)$", self.packager)
        return re_rslt.group(1) if re_rslt else None

    @property
    def epoch(self):
        return string_to_version(self.evr)[0]

    @property
    def version(self):
        return string_to_version(self.evr)[1]

    @property
    def release(self):
        return string_to_version(self.evr)[2]

    def __str__(self):
        return str(self).encode("utf8")

    def __unicode__(self):
        header = "* {0} {1}".format(self.date.strftime("%a %b %d %Y"),
                                     self.packager)
        return "{0}\n{1}".format(header, "\n".join(self.text))


SpecSource = collections.namedtuple("SpecSource", ["name", "position"])


SpecPatch = collections.namedtuple("SpecPatch", ["name", "position"])


def none_or_unicode(value):
    """
    @type value:  str or None
    @param value: String to convert.

    @rtype:       unicode or None
    @return:      String converted to unicode if string wasn't None, None
        otherwise.
    """
    return None if value is None else to_unicode(value)


class RPMHeaderWrapper(object):

    """RPM package header wrapper."""

    def __init__(self, hdr):
        """
        @type hdr:  rpm.hdr
        @param hdr: RPM package header.
        """
        self._hdr = hdr

    @property
    def name(self): return none_or_unicode(self._hdr[rpm.RPMTAG_NAME])

    @property
    def epoch(self):
        if self._hdr["epoch"] is None:
            return None
        return int(self._hdr["epoch"])

    @property
    def version(self): return none_or_unicode(self._hdr[rpm.RPMTAG_VERSION])

    @property
    def release(self): return none_or_unicode(self._hdr[rpm.RPMTAG_RELEASE])

    @property
    def evr(self): return none_or_unicode(self._hdr[rpm.RPMTAG_EVR])

    @property
    def summary(self): return none_or_unicode(self._hdr[rpm.RPMTAG_SUMMARY])

    @property
    def description(self): return to_unicode(self._hdr[rpm.RPMTAG_DESCRIPTION])

    @property
    def license(self): return none_or_unicode(self._hdr[rpm.RPMTAG_LICENSE])

    @property
    def vendor(self): return none_or_unicode(self._hdr[rpm.RPMTAG_VENDOR])

    @property
    def group(self): return none_or_unicode(self._hdr[rpm.RPMTAG_GROUP])

    @property
    def url(self): return none_or_unicode(self._hdr[rpm.RPMTAG_URL])

    @property
    def provides(self):
        return self.__read_package_features(rpm.RPMTAG_PROVIDENAME,
                                            rpm.RPMTAG_PROVIDEFLAGS,
                                            rpm.RPMTAG_PROVIDEVERSION)

    @property
    def requires(self):
        return self.__read_package_features(rpm.RPMTAG_REQUIRENAME,
                                            rpm.RPMTAG_REQUIREFLAGS,
                                            rpm.RPMTAG_REQUIREVERSION)

    @property
    def conflicts(self):
        return self.__read_package_features(rpm.RPMTAG_CONFLICTNAME,
                                            rpm.RPMTAG_CONFLICTFLAGS,
                                            rpm.RPMTAG_CONFLICTVERSION)

    @property
    def obsoletes(self):
        return self.__read_package_features(rpm.RPMTAG_OBSOLETENAME,
                                            rpm.RPMTAG_OBSOLETEFLAGS,
                                            rpm.RPMTAG_OBSOLETEVERSION)

    @property
    def changelogs(self):
        changelogs = []
        for packager, date, text in \
                sorted(zip(self._hdr[rpm.RPMTAG_CHANGELOGNAME],
                                      self._hdr[rpm.RPMTAG_CHANGELOGTIME],
                                      self._hdr[rpm.RPMTAG_CHANGELOGTEXT]),
                       key=cmp_to_key(lambda a, b: cmp(b[1], a[1]))):
            changelogs.append(ChangelogRecord(datetime.date.fromtimestamp(date),
                                              to_unicode(packager),
                                              [to_unicode(i)
                                               for i in text.decode('utf-8').split("\n")]))
        return changelogs

    def __read_package_features(self, name_tag, flags_tag, version_tag):
        features = []
        for name, flag, evr in zip(self._hdr[name_tag],
                                   self._hdr[flags_tag],
                                   self._hdr[version_tag]):
            flag = flag_to_string(flag)
            if not evr or flag is None:
                evr = e = v = r = None
            else:
                evr = to_unicode(evr)
                flag = to_unicode(flag)
                e, v, r = string_to_version(evr)
                e = int(e) if re.search(r"^\d+:", evr) else None
                v = to_unicode(v)
            features.append(PackageFeature(to_unicode(name), flag, evr, e, v,
                                           none_or_unicode(r)))
        return features


class SrcRPMHeaderWrapper(RPMHeaderWrapper):

    """Src-RPM package header wrapper."""

    def __init__(self, hdr, sources):
        RPMHeaderWrapper.__init__(self, hdr)
        self.__sources = []
        self.__patches = []
        for name, pos, type_ in sorted(
                sources, key=cmp_to_key(
                    lambda a, b: (a[1] > b[1]) - (a[1] < b[1]))):
            name = to_unicode(name)
            if type_ == rpm.RPMBUILD_ISSOURCE:
                self.__sources.append(SpecSource(name, pos))
            elif type_ == rpm.RPMBUILD_ISPATCH:
                self.__patches.append(SpecPatch(name, pos))
            else:
                raise NotImplementedError("unsupported source type {0!r}".
                                          format(type_))
    @property
    def patches(self):
        return self.__patches[:]

    @property
    def sources(self):
        return self.__sources[:]


class SpecParser(object):

    """RPM spec files parser."""

    def __init__(self, spec_file, macros=None):
        """
        @type spec_file:  str or unicode
        @param spec_file: Spec file path.
        @type macros:     dict
        @param macros:    Additional RPM macro definitions (e.g.
            {"dist": ".el6", "rhel": "6"}).
        """
        try:
            if macros:
                for key, value in macros.items():
                    rpm.addMacro(key, value)
            self.__dist_macro = rpm.expandMacro("%{?dist}")
            ts = rpm.ts()
            try:
                self.__spec = ts.parseSpec(spec_file)
            except ValueError as rpm_error:
                # NOTE: sometimes CL developers are forgiving to arrange
                #       changelogs in right order and RPM fails on it. Here
                #       I'm trying to fix this type of error.
                tmp_file = None
                try:
                    tmp_file = tempfile.NamedTemporaryFile("w", delete=False)
                    self.__fix_spec_file(spec_file, tmp_file)
                    self.__spec = ts.parseSpec(tmp_file.name)
                except:
                    # seems we had no success on fixing file - raise original
                    # error
                    raise SpecParseError('Cannot parse spec') from rpm_error
                finally:
                    if tmp_file:
                        tmp_file.close()
                        os.remove(tmp_file.name)
        finally:
            # reset previously added macro definitions
            rpm.reloadConfig()
        self.__source_package = SrcRPMHeaderWrapper(self.__spec.sourceHeader,
                                                    sources=self.__spec.sources)

    @property
    def source_package(self):
        return self.__source_package

    @property
    def packages(self):
        return [RPMHeaderWrapper(i.header) for i in self.__spec.packages]

    @property
    def dist_macro(self):
        return self.__dist_macro

    def __fix_spec_file(self, spec_f, tmp_spec_fd):
        header_re = re.compile(r"^\*\s*(?P<weekday>[a-zA-Z]{3})\s+"
                               r"(?P<month>[a-zA-Z]{3})\s+"
                               r"(?P<day>\d{1,2})\s+(?P<year>\d{4})\s+.*")
        changelogs = []
        with open(spec_f, "r") as fd:
            parsing_changelog = False
            changelog = None
            for line in fd:
                if line.strip() == "%changelog":
                    parsing_changelog = True
                    tmp_spec_fd.write(line)
                    continue
                elif not parsing_changelog:
                    tmp_spec_fd.write(line)
                    continue
                header_rslt = header_re.search(line)
                if header_rslt:
                    ts_str = "{0} {1} {2}".format(header_rslt.group("month"),
                                                  header_rslt.group("day"),
                                                  header_rslt.group("year"))
                    ts = time.mktime(time.strptime(ts_str, "%b %d %Y"))
                    changelog = {"date": datetime.date.fromtimestamp(ts),
                                 "header": line,
                                 "text": []}
                    changelogs.append(changelog)
                    continue
                elif changelog:
                    changelog["text"].append(line.strip())
        changelogs.sort(key=cmp_to_key(lambda a, b: cmp(b["date"], a["date"])))
        for changelog in changelogs:
            # remove empty lines from the beginning and the end of list
            while changelog["text"] and changelog["text"][-1] == "":
                changelog["text"].pop()
            while changelog["text"] and changelog["text"][0] == "":
                changelog["text"].pop(0)
            tmp_spec_fd.write(changelog["header"])
            tmp_spec_fd.write("\n".join(changelog["text"]))
            tmp_spec_fd.write("\n\n")
        tmp_spec_fd.flush()
