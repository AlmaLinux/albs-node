# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-06-08

"""
CloudLinux Build System git wrapper.
"""

import functools
import fcntl
import errno
import pipes
import subprocess
import collections
import tempfile
import shutil
import logging
import hashlib
import re
import os
import time

import rpm
import plumbum
from plumbum.commands.processes import ProcessExecutionError

from build_node.errors import CommandExecutionError, LockError
from build_node.utils.rpm_utils import string_to_version as stringToVersion
from build_node.utils.file_utils import safe_mkdir
from build_node.ported import to_unicode

__all__ = ['git_get_commit_id', 'git_ls_remote', 'GitError', 'GitCommandError',
           'git_merge', 'git_list_branches', 'git_init_repo', 'git_push',
           'git_create_tag', 'git_commit']


class GitError(Exception):

    """Git related errors base class."""

    def __init__(self, message):
        """
        Git error initialization.

        Parameters
        ----------
        message : str
            Error message.
        """
        super(GitError, self).__init__(message)


class GitCommandError(GitError, CommandExecutionError):

    """Git shell command execution error."""

    def __init__(self, message, exit_code, stdout, stderr, command):
        """
        Git shell command execution error initialization.

        Parameters
        ----------
        message : str
            Error message.
        exit_code : int
            Command exit code.
        stdout : str
            Command stdout.
        stderr : str
            Command stderr.
        command : list of str
            Executed git command.
        """
        CommandExecutionError.__init__(self, message, exit_code, stdout,
                                       stderr, command)

    @staticmethod
    def from_common_exception(git_exception):
        """
        Make more specific exception from ProcessExecutionError.

        Parameters
        ----------
        git_exception: plumbum.commands.processes.ProcessExecutionError
            Exception to process.

        Returns
        -------
        GitCommandError
            More specific exception.
        """
        message = git_exception.stderr.strip()
        re_rslt = re.search(r'fatal:\s+(.*?)$', message, re.MULTILINE)
        if re_rslt:
            message = re_rslt.group(1)
        return GitCommandError(
            message, git_exception.retcode, git_exception.stdout,
            git_exception.stderr, git_exception.argv
        )


def handle_git_error(fn):
    """
    Unified error handler for git command wrappers.

    Parameters
    ----------
    fn : function
        Git command execution function.

    Returns
    -------
    function
        Decorated git command execution function.

    Raises
    ------
    GitCommandError
        If the git command execution function failed.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ProcessExecutionError as git_exception:
            raise GitCommandError.from_common_exception(git_exception)
    return wrapper


@handle_git_error
def git_get_commit_id(repo_path, ref='HEAD'):
    """
    Returns a git commit id for the specified git reference.

    Parameters
    ----------
    repo_path : str
        Local git repository path.
    ref : str, optional
        Git repository reference. Default is "HEAD".

    Returns
    -------
    str
        Git commit id.
    """
    git = plumbum.local['git']
    exit_code, stdout, stderr = \
        git.with_env(HISTFILE='/dev/null', LANG='C').with_cwd(
            repo_path).run(args=('log', '--pretty=format:%H', '-n', 1, ref),
                           retcode=None)
    return stdout.strip()


@handle_git_error
def git_ls_remote(repo_path, heads=False, tags=False):
    """
    Returns a list of references in the git repository.

    Parameters
    ----------
    repo_path : str
        Git repository URI. It can be either a local file system path or a
        remote repository URL.
    heads : bool, optional
        Limit output to only refs/heads. Default is False.
    tags : bool, optional
        Limit output to only refs/tags. Default is False.

    Returns
    -------
    list of tuple
        List of git references. Each reference is represented as a commit_id,
        name and type (head, change, tag etc) tuple.

    Notes
    -----
    `heads` and `tags` options aren't mutually exclusive; when given both
    references stored in refs/heads and refs/tags are returned. All references
    (including changes, cache-automerge, etc) will be returned if both options
    are omitted.
    """
    git = plumbum.local['git']
    args = ['ls-remote']
    if heads:
        args.append('--heads')
    if tags:
        args.append('--tags')
    args.append(repo_path)
    exit_code, stdout, stderr = \
        git.with_env(HISTFILE='/dev/null', LANG='C')[args].run(retcode=None)
    refs = []
    for line in stdout.split('\n'):
        line = line.strip()
        re_rslt = re.search(r'^([a-zA-Z0-9]{40})\s*\w+/(\w+)/(\S+)$', line)
        if not re_rslt:
            continue
        logging.info(f'\nRE RESULT\n{re_rslt}\n')
        commit_id, ref_type, ref = re_rslt.groups()
        logging.info(f'\nREF TYPE GIT UTILS\n{ref_type}\n')
        if ref.endswith('^{}'):
            # NOTE: see http://stackoverflow.com/q/15472107 for details
            continue
        elif ref_type in ('changes', 'heads', 'tags', 'notes'):
            ref_type = ref_type[:-1]
        refs.append((commit_id, ref, ref_type))
    return refs


def git_list_branches(repo_path, commit_id=False):
    """
    Returns list of git repository (raw, not parsed) branches.

    Parameters
    ----------
    repo_path : str
        Repository path.
    commit_id : bool
        If true, function result will be list of tuples
        (commit_id, branch_name), otherwise result will be
        list of branch names.

    Returns
    -------
    list
        List of git references.
    """
    return [(commit, ref) if commit_id else ref
            for commit, ref, ref_type in git_ls_remote(repo_path, heads=True)]


@handle_git_error
def git_init_repo(repo_path, bare=False):
    """
    Init new empty repo.

    Parameters
    ----------
    repo_path : str
        Repository path.
    bare : bool
        If true, will init bare repository.
    """
    git = plumbum.local['git']
    git_args = ['init']
    if bare:
        git_args.append('--bare')
    git_args.append(repo_path)
    git[git_args].run()


def git_merge(repo_path, ref, conflict_callback=None):
    """
    Execute "git merge" and tries to solve conflicts.

    Parameters
    ----------
    repo_path : str
        Repository path.
    ref : str
        Any mergeable reference.
    conflict_callback : callable
        Callback-function for fixing merge errors.
    """
    try:
        git = plumbum.local['git']
        git_args = ['merge', ref]
        git.with_cwd(repo_path)[git_args].run()
    except ProcessExecutionError as error:
        files_regex = re.compile(r'Merge\s+conflict\s+in\s+(.*)$', flags=re.M)
        conflict_files = [
            os.path.join(repo_path, filename)
            for filename in re.findall(files_regex, error.stdout)
        ]
        if conflict_files and conflict_callback:
            conflict_callback(conflict_files)
            return
        raise GitCommandError.from_common_exception(error)


def git_push(repo_dir, repository, tags=False, gerrit=False, branch=None,
             set_upstream=False, reviewers=None):
    """
    Executes 'git push' command in local git repository.

    @type repo_dir:     str or unicode
    @param repo_dir:    Local git repository path.
    @type repository:   str or unicode
    @param repository:  The "remote" repository that is destination of a push
        operation (see git-push 'repository' argument description for details).
    @type tags:         bool
    @param tags:        See git-push --tags argument description for details.
    @type gerrit        bool
    @param gerrit       Flag if we need to push commit to gerrit.
    @type branch        str or unicode
    @param branch       Branch to which gerrit change will be related. If not
                        specified, the change will be pushed to the master
    @type set_upstream: bool
    @param set_upstream:For every branch that is up to date or successfully
                        pushed, add upstream (tracking) reference

    @raise AltGitError: If git return status is not 0.
    """
    cmd = ["git", "push", pipes.quote(repository)]
    if tags:
        cmd.append(" --tags")
    if gerrit:
        if branch:
            cmd_str = "HEAD:refs/for/{}".format(str(branch))
        else:
            cmd_str = "HEAD:refs/for/master"
        if reviewers:
            formatted_reviewers = []
            for reviewer in reviewers:
                add_reviewer = "r='{0}'".format(reviewer)
                formatted_reviewers.append(add_reviewer)
            cmd_rev = ",".join(formatted_reviewers)
            cmd_str = cmd_str + '%' + cmd_rev
        cmd.append(cmd_str)
    if set_upstream:
        cmd.insert(2, "--set-upstream")
        if branch:
            cmd.append(str(branch))
        else:
            cmd.append("master")
    cmd = " ".join(cmd)
    try:
        proc = subprocess.Popen(cmd, cwd=repo_dir, shell=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        out, err = proc.communicate()
        if proc.returncode != 0 or "fatal" in out.decode('utf-8').lower():
            raise GitError(
                f"cannot execute git-push in repository {repo_dir}: "
                f"{out.decode('utf-8')}"
            )
        return out
    except GitError as e:
        raise e
    except Exception as e:
        raise GitError(
            f"cannot execute git-push in repository {repo_dir}: {e}")


def git_create_tag(repo_dir, git_tag, force=False):
    """
    Executes 'git tag $git_tag' command in local git repository.

    @type repo_dir:  str or unicode
    @param repo_dir: Local git repository path.
    @type git_tag:   str or unicode
    @param git_tag:  Git tag to add.
    @type force:     bool
    @param force:    Replace an existing tag instead of failing if True.
    """
    try:
        cmd = "git tag"
        if force:
            cmd += " -f"
        proc = subprocess.Popen("{0} {1}".format(cmd, pipes.quote(git_tag)),
                                cwd=repo_dir, shell=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        out, err = proc.communicate()
        if proc.returncode != 0:
            raise GitError(
                f"cannot execute git-tag in repository {repo_dir}: {out}")
    except GitError as e:
        raise e
    except Exception as e:
        raise GitError(f"cannot execute git-tag in repository {repo_dir}: {e}")


def git_commit(repo_dir, message, commit_all=True, signoff=False):
    """
    Executes 'git commit -m $message' command in local git repository.

    @type repo_dir:    str or unicode
    @param repo_dir:   Local git repository path.
    @type message:     str or unicode
    @param message:    Git commit message.
    @type commit_all:  bool
    @param commit_all: See git-commit --all argument description.
    """
    if commit_all:
        cmd = "git commit -a -m %s"
    else:
        cmd = "git commit -m %s"
    if signoff:
        cmd += " --signoff"
    try:
        proc = subprocess.Popen(cmd % pipes.quote(message), cwd=repo_dir,
                                shell=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        out, err = proc.communicate()
        if proc.returncode != 0:
            raise GitError(
                f"cannot execute git-commit in repository {repo_dir}: {out}")
    except GitError as e:
        raise e
    except Exception as e:
        raise GitError(
            f"cannot execute git-commit in repository {repo_dir}: {e}")


def list_git_tags(uri, commit_id=False):
    """
    Returns list of git repository (raw, not parsed) tags. Note: tags are
    returned in the same order, that received from 'git ls-remote --tags',
    no sorting performed.

    @type uri:          str or unicode
    @param uri:         Git repository URI.
    @type commit_id:    bool
    @param commit_id:   Return tuples of (commit_id, tag) instead of raw tags.

    @rtype:             list
    @return:            List of git repository (raw, not parsed) tags.

    @raise AltGitError: When command execution failed.
    """
    return [(commit, ref) if commit_id else ref
            for commit, ref, ref_type in git_ls_remote(uri, tags=True)]


def parse_cl_git_tag(tag):
    """
    Cloud Linux git tags ([name@][epoch+]version[-release][^modifier]) parsing
    function.

    @type tag:  str or unicode
    @param tag: Git tag to parse.

    @rtype:     dict
    @return:    Dictionary that contains parsed git tag information (only
        version field is mandatory).
    """
    re_rslt = re.search(r"^((?P<name>[^@]+)@|)((?P<epoch>\d+)\+|)(?P<vr>.*?)"
                        r"(\^(?P<modifier>[\w\.-]+)|)$", tag)
    if not re_rslt:
        raise ValueError("invalid Cloud Linux git tag ({0}) format".
                         format(tag))
    t = {}
    _, version, release = stringToVersion("0:{0}".format(re_rslt.group("vr")))
    t["version"] = to_unicode(version)
    if release is not None:
        t["release"] = to_unicode(release)
    if re_rslt.group("epoch") is not None:
        t["epoch"] = int(re_rslt.group("epoch"))
    for f in ("name", "modifier"):
        if re_rslt.group(f) is not None:
            t[f] = to_unicode(re_rslt.group(f))
    return t


def cmp_cl_git_tags(tag1, tag2):
    """
    Cloud Linux git tags ([name@][epoch+]version[-release][^modifier])
    comparison function.

    @type tag1:  str or unicode or dict or AltGitTag
    @param tag1: First git tag to compare.
    @type tag2:  str or unicode or dict or AltGitTag
    @param tag2: Second git tag to compare.

    @rtype:      int
    @return:     Positive integer if tag1 is greater than tag2, negative integer
        if tag2 is greater than tag1 or 0 if both tags are equal.
    """
    if isinstance(tag1, AltGitTag):
        tag1 = tag1.as_dict()
    elif not isinstance(tag1, dict):
        tag1 = parse_cl_git_tag(tag1)
    if isinstance(tag2, AltGitTag):
        tag2 = tag2.as_dict()
    elif not isinstance(tag2, dict):
        tag2 = parse_cl_git_tag(tag2)
    epoch1 = str(tag1.get("epoch")) if tag1.get("epoch") is not None else "0"
    epoch2 = str(tag2.get("epoch")) if tag2.get("epoch") is not None else "0"
    get_vr = lambda tag, key: tag.get(key, "") if tag.get(key) is not None \
        else ""
    return rpm.labelCompare(
        (epoch1, get_vr(tag1, "version"), get_vr(tag1, "release")),
        (epoch2, get_vr(tag2, "version"), get_vr(tag2, "release"))
    )


def git_checkout(repo_dir, ref, options=None):
    """
    Checkouts specified git reference.

    @type repo_dir:  str or unicode
    @param repo_dir: Local git repository path.
    @type ref:       str or unicode
    @param ref:      Git reference to checkout.
    @type options:   list or tuple
    @param options:  Additional options for git checkout command
    """
    try:
        cmd = ["git", "checkout"]
        if isinstance(options, (list, tuple)):
            cmd.extend(options)
        cmd.append(ref)
        proc = subprocess.Popen(cmd, cwd=repo_dir, shell=False,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        out, _ = proc.communicate()
        status = proc.returncode
        if status != 0:
            raise GitError("cannot checkout {0} in the git repository {1}"
                           " ({2} return code): {3}".format(ref, repo_dir,
                                                            status, out))
    except GitError as e:
        raise e
    except Exception as e:
        raise GitError("cannot checkout {0} in the git repository {1}: "
                       "{2}".format(ref, repo_dir, str(e)))


class AltGitTag(collections.namedtuple("AltGitTag",
                                       ["tag", "name", "epoch", "version",
                                        "release", "modifier", "commit"])):

    def as_dict(self):
        d = {}
        for f in ("tag", "name", "epoch", "version", "release", "modifier",
                  "commit"):
            d[f] = getattr(self, f)
        return d


class WrappedGitRepo:

    def __init__(self, repo_dir):
        """
        @type repo_dir:  str or unicode
        @param repo_dir: Local git repository directory.
        """
        self.__repo_dir = repo_dir

    def archive(self, ref, archive_path, archive_format="tar.bz2", prefix=None,
                exclude=None):
        """
        git archive command wrapper.

        @type ref:             str or unicode
        @param ref:            Git reference to archive.
        @type archive_path:    str or unicode
        @param archive_path:   Output file full name.
        @type archive_format:  str or unicode
        @param archive_format: Archive format (see git archive -l output for the
            list of supported formats). NOTE: we have special code for 'tar.bz2'
            support.
        @type prefix:          str or unicode
        @param prefix:         Prepend 'prefix' to each filename in the archive
            if specified.
        @type exclude:         list
        @param exclude:        list of exclude files/folders
        """
        cmd = "git archive "
        if archive_format == "tar.bz2":
            cmd += "--format=tar "
        else:
            cmd += "--format={0} --output={1} ".format(archive_format,
                                                       archive_path)
        if prefix:
            cmd += "--prefix={0} ".format(prefix)
        cmd += ref
        if archive_format == "tar.bz2":
            cmd += " | bzip2 > {0}".format(archive_path)
        proc = subprocess.Popen(cmd, cwd=self.__repo_dir, shell=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        out, _ = proc.communicate()
        if exclude:
            self.remove_from_tarball(archive_path, exclude)
        if proc.returncode != 0:
            raise GitError(f"cannot execute git archive command: {out}")

    def remove_from_tarball(self, archive_path, exclude, tmp_dir=None):
        working_dir = None
        try:
            working_dir = tempfile.mkdtemp(prefix='alt_git_', dir=tmp_dir)
            sources_dir = os.path.join(working_dir, 'sources')
            os.makedirs(sources_dir)
            proc = subprocess.Popen('tar -xjpf {0}'.format(archive_path),
                                    cwd=sources_dir, shell=True,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
            out, _ = proc.communicate()
            if proc.returncode != 0:
                raise GitError(
                    f'cannot unpack {archive_path} git archive: {out}')
            for excluded in exclude:
                for sub_dir in os.listdir(sources_dir):
                    excluded_path = os.path.join(sources_dir, sub_dir,
                                                 excluded)
                    if os.path.exists(excluded_path):
                        if os.path.isfile(excluded_path):
                            os.unlink(excluded_path)
                        else:
                            shutil.rmtree(excluded_path)
            new_archive_path = os.path.join(working_dir,
                                            os.path.basename(archive_path))
            proc = subprocess.Popen('tar -cjpf {0} .'.format(new_archive_path),
                                    cwd=sources_dir, shell=True,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
            out, _ = proc.communicate()
            if proc.returncode != 0:
                raise GitError(
                    f'cannot create {new_archive_path} git archive: {out}'
                )
            shutil.move(new_archive_path, archive_path)
        finally:
            if working_dir:
                shutil.rmtree(working_dir)

    def checkout(self, ref, options=None):
        if not isinstance(options, (list, tuple)):
            options = []
        git_checkout(self.__repo_dir, ref, options)

    def get_commit_id(self, ref):
        return git_get_commit_id(self.__repo_dir, ref)

    @staticmethod
    def clone_from(repo_url, repo_dir, mirror=False):
        """
        Clones git repository to the specified directory.

        @type repo_url:     str or unicode
        @param repo_url:    Git repository URL.
        @type repo_dir:     str or unicode
        @param repo_dir:    The name of a new directory to clone into.
        @type mirror:       bool
        @param mirror:      Set up a mirror of the source repository if True.

        @rtype:             WrappedGitRepo
        @return:            WrappedGitRepo object for cloned git repository.

        @raise AltGitError: If something went wrong.
        """
        cmd = ["git", "clone", repo_url, repo_dir]
        if mirror:
            cmd.insert(2, "--mirror")
        git_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
        stdout, _ = git_proc.communicate()
        if git_proc.returncode != 0:
            raise GitError("cannot clone {0} git repository to {1} "
                           "directory: return code {2}, git output: {3}".
                           format(repo_url, repo_dir, git_proc.returncode,
                                  stdout.decode('utf-8')))

    def fetch(self, repository, ref):
        """
        Executes git-fetch command in repository directory.

        @type repository:  str
        @param repository: Git repository
        @type ref:         str
        @param ref:        Git reference to fetch.
        """
        try:
            cmd = "git fetch {0} {1}".format(repository, ref)
            proc = subprocess.Popen(cmd, cwd=self.__repo_dir, shell=True,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
            out, _ = proc.communicate()
            if proc.returncode != 0:
                raise GitError("cannot fetch {0!r} {1!r}: {2}".format(
                    repository, ref, out))
        except GitError as e:
            raise e
        except Exception as e:
            raise GitError("cannot fetch {0!r} {1!r}: {2}".format(
                repository, ref, str(e)))

    def list_tags(self, tag=None, name=None, version=None, release=None,
                  epoch=None, modifier=None, tag_regex=None, **kwargs):
        """
        @rtype:  list
        @return: List that contains AltGitTag object for each matched git
            repository tag.
        """
        raw_tags = list_git_tags(self.__repo_dir, commit_id=True)
        if tag:
            found = False
            for commit, raw_tag in raw_tags:
                if raw_tag == tag:
                    raw_tags = [(commit, tag)]
                    found = True
                    break
            if not found:
                return []
        if tag_regex:
            raw_tags = [(c, t) for c, t in raw_tags if tag_regex.search(t)]
        parsed_tags = []
        for commit, raw_tag in raw_tags:
            try:
                parsed_tag = parse_cl_git_tag(raw_tag)
                if (epoch is not None and parsed_tag.get("epoch") != epoch) or \
                        (version and parsed_tag.get("version") != version) or \
                        (release and parsed_tag.get("release") != release) or \
                        (name and parsed_tag.get("name") != name) or \
                        (modifier and parsed_tag.get("modifier") != modifier):
                    continue
                args = [parsed_tag.get(f) for f in ("name", "epoch", "version",
                                                    "release", "modifier")]
                args.append(commit)
                parsed_tags.append(AltGitTag(raw_tag, *args))
            except ValueError:
                # NOTE: this is ugly hack for existent CL repositories with
                #       bad tags
                if name is None and version is None and release is None and \
                        epoch is None and modifier is None:
                    parsed_tags.append(AltGitTag(raw_tag, None, None, None,
                                                 None, None, commit))
        parsed_tags.sort(cmp_cl_git_tags, reverse=True)
        return parsed_tags

    @property
    def repo_dir(self):
        """
        Repository directory path.

        Returns
        -------
        str
        """
        return self.__repo_dir


class GitCacheError(Exception):
    pass


class MirroredGitRepo(object):

    def __init__(self, repo_url, repos_dir, locks_dir, timeout=60,
                 logger=None):
        """
        @type repo_url:   str or unicode
        @param repo_url:  Git repository URL.
        @type repos_dir:  str or unicode
        @param repos_dir: Directory where git repositories cache is located.
        @type timeout:    int
        @param timeout:   Lock obtaining timeout. Use None or 0 if you don't
            need the timeout.
        @type logger:     logging.Logger
        @param logger:    Logger instance to use (optional).
        """
        if not isinstance(repo_url, str):
            raise ValueError("repo_url must be instance of str or unicode")
        elif isinstance(repo_url, str):
            self.__repo_url = repo_url.encode("utf8")
        else:
            self.__repo_url = repo_url
        if not isinstance(timeout, (int, None)):
            raise ValueError("timeout must be instance of int or None")
        self.__timeout = timeout
        self.__logger = (logger if logger
                         else logging.getLogger("alt_stubs_kcare_build"))
        self.__repo_hash = hashlib.sha256(repo_url.encode('utf-8')).hexdigest()
        try:
            safe_mkdir(repos_dir)
        except Exception as e:
            raise GitCacheError("cannot create {0} directory: {1}".
                                format(repos_dir, str(e)))
        try:
            safe_mkdir(locks_dir)
        except Exception as e:
            raise GitCacheError("cannot create {0} directory: {1}".
                                format(locks_dir, str(e)))
        self.__base_dir = repos_dir
        self.__lock_file = os.path.join(locks_dir, "{0}.lock".
                                        format(self.__repo_hash))
        self.__repo_str = "{0} ({1})".format(repo_url, self.__repo_hash)
        self.__repo_dir = os.path.join(repos_dir, self.__repo_hash)
        self.__lock_file = os.path.join(locks_dir,
                                        "{0}.lock".format(self.__repo_hash))
        self.__fd = None

    def clone_to(self, target_dir, branch=None):
        """
        Clones cached git repository to the specified directory.

        @type target_dir:  str or unicode
        @param target_dir: Directory where you want to clone cached git
            repository.
        """
        if not self.__fd:
            raise GitCacheError("{0} git repository cache is not "
                                "initialized yet".format(self.__repo_str))
        self.__clone_repo(self.__repo_dir, target_dir, branch=branch)
        return WrappedGitRepo(target_dir)

    def __enter__(self):
        self.__fd = open(self.__lock_file, "w")
        start_time = time.time()
        lock_flags = fcntl.LOCK_EX
        if self.__timeout is not None:
            lock_flags = lock_flags | fcntl.LOCK_NB
        self.__logger.debug("obtaining exclusive lock for {0} git repository".
                            format(self.__repo_str))
        while True:
            try:
                fcntl.flock(self.__fd, lock_flags)
                self.__logger.debug("{0} git repository lock has been "
                                    "successfully obtained: fetching changes "
                                    "now".format(self.__repo_str))
                if os.path.exists(self.__repo_dir):
                    self.__update_repo()
                else:
                    self.__clone_repo(self.__repo_url, self.__repo_dir,
                                      mirror=True)
                self.__logger.debug("changing {0} git repository lock from "
                                    "exclusive to shared".
                                    format(self.__repo_str))
                fcntl.flock(self.__fd, fcntl.LOCK_SH)
                break
            except (IOError, BlockingIOError) as e:
                if e.errno != errno.EAGAIN or self.__timeout is None:
                    self.__finalize()
                    raise e
                if (time.time() - start_time) >= self.__timeout:
                    self.__logger.error("cannot obtain {0} git repository "
                                        "lock: timeout occurred ".
                                        format(self.__repo_str))
                    self.__finalize()
                    raise LockError("timeout occurred")
                self.__logger.debug("{0} repository is already locked by "
                                    "another process: will retry after 1 "
                                    "second".format(self.__repo_str))
                time.sleep(1)
            except Exception as e:
                self.__finalize()
                raise e
        return self

    def __clone_repo(self, repo_url, target_dir, mirror=False, branch=None):
        """
        Clones git repository to the specified directory.

        @type repo_url:       str or unicode
        @param repo_url:      Git repository URL.
        @type target_dir:     str or unicode
        @param target_dir:    The name of a new directory to clone into.
        @type mirror:         bool
        @param mirror:        Set up a mirror of the source repository if True.

        @raise GitCacheError: If git-clone execution failed.
        """
        cmd = ["git", "clone"]
        if mirror:
            cmd.append("--mirror")
        cmd.extend((repo_url, target_dir))
        git_clone = subprocess.Popen(cmd, cwd=self.__base_dir,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT)
        stdout, _ = git_clone.communicate()
        status = git_clone.returncode
        if status != 0:
            msg = "cloning failed ({0} return code): {1}".format(
                status, stdout)
            self.__logger.error("{0} git repository {1}".format(
                repo_url, stdout))
            raise GitCacheError(msg)
        if branch:
            self.__checkout_branch(target_dir, branch)

    def __checkout_branch(self, repo_dir, branch):
        """
        Checkout branch to the specified directory.

        @type repo_dir:       str or unicode
        @param repo_dir:      working directory
        @type branch:         str or unicode
        @param branch:        branch for checkout
        @raise GitCacheError: If git-clone execution failed.
        """
        cmd = ["git", "checkout", branch]
        git_clone = subprocess.Popen(cmd, cwd=repo_dir,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT)
        stdout, _ = git_clone.communicate()
        status = git_clone.returncode
        if status != 0:
            msg = "checkout failed ({0} return code): {1}".format(
                status, stdout)
            self.__logger.error("{0} git repository {1}".
                                format(repo_dir, stdout))
            raise GitCacheError(msg)

    def __update_repo(self):
        """
        Updates cached git repository using git-fetch --prune command.

        @raise GitCacheError: If git-fetch execution failed.
        """
        git_fetch = subprocess.Popen(["git", "fetch", "--prune"],
                                     cwd=self.__repo_dir,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT)
        stdout, _ = git_fetch.communicate()
        status = git_fetch.returncode
        if status != 0:
            msg = "update failed ({0} return code): {1}".format(status, stdout)
            self.__logger.error("{0} git repository {1}".
                                format(self.__repo_str, msg))
            raise GitCacheError(msg)

    def __finalize(self):
        """
        Removes lock and closes lock file descriptor if opened.
        """
        if self.__fd:
            fcntl.flock(self.__fd, fcntl.LOCK_UN)
            self.__fd.close()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.__finalize()
