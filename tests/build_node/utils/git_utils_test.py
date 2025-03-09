"""build_node.utils.git_utils module unit tests."""

import os
import shutil
import tempfile
import textwrap
import unittest
import pytest

from build_node.utils.test_utils import (
    MockShellCommand,
    unload_plumbum_modules,
)

__all__ = ['TestGitGetCommitId', 'TestGitLsRemote']


class GitUtilsShellTest(unittest.TestCase):
    """Base class for build_node.utils.git_utils shell function tests."""

    def setUp(self):
        self._unload_modules()

    def tearDown(self):
        self._unload_modules()

    @staticmethod
    def _unload_modules():
        unload_plumbum_modules('build_node.utils.git_utils')


class TestGitGetCommitId(GitUtilsShellTest):
    """build_node.utils.git_utils.git_get_commit_id function unit tests."""

    commit_id = '23cbcfbea8ee4a52adaed158e63ccf7178da9a28'

    def setUp(self):
        self.repo_dir = tempfile.mkdtemp(prefix='castor_test_')
        super(TestGitGetCommitId, self).setUp()

    @pytest.mark.skip(reason="We need to rewrite this tests using common library")
    def test_head_ref(self):
        """build_node.utils.git_utils.git_get_commit_id returns HEAD commit id by \
default"""
        self.__test_ref(None, 'HEAD')

    @pytest.mark.skip(reason="We need to rewrite this tests using common library")
    def test_custom_ref(self):
        """build_node.utils.git_utils.git_get_commit_id returns specified \
reference commit id"""
        self.__test_ref('master', 'master')

    @pytest.mark.skip(reason="We need to fix this test")
    def test_no_repo(self):
        """
        build_node.utils.git_utils.git_get_commit_id throws error for invalid repo
        """
        from build_node.utils.git_utils import (
            GitCommandError,
            git_get_commit_id,
        )

        with self.assertRaisesRegex(GitCommandError, 'not a git repository'):
            git_get_commit_id(self.repo_dir)

    @pytest.mark.skip(reason="We need to fix this test")
    def test_no_ref(self):
        """
        build_node.utils.git_utils.git_get_commit_id throws error for invalid ref
        """
        user_code = r"""
print("fatal: ambiguous argument 'brokenref': unknown " \
      "revision or path not in the working tree.\nUse '--' to " \
      "separate paths from revisions, like this:",
      file=sys.stderr)
sys.exit(128)
        """
        with MockShellCommand('git', user_code):
            from build_node.utils.git_utils import (
                GitCommandError,
                git_get_commit_id,
            )

            with self.assertRaisesRegex(
                GitCommandError, 'unknown revision or path'
            ):
                git_get_commit_id(self.repo_dir, 'brokenref')

    def __test_ref(self, ref_arg, expected_ref):
        user_code = 'print({0!r})'.format(self.commit_id)
        with MockShellCommand('git', user_code) as cmd:
            # NOTE: an import statement should be executed in a mocked command
            #       context to let plumbum library use modified PATH
            from build_node.utils.git_utils import git_get_commit_id

            if ref_arg:
                result = git_get_commit_id(self.repo_dir, ref_arg)
            else:
                result = git_get_commit_id(self.repo_dir)
            self.assertEqual(result, self.commit_id)
            self.assertEqual(
                cmd.get_calls()[0]['argv'][1:],
                ['log', '--pretty=format:%H', '-n', '1', expected_ref],
            )

    def tearDown(self):
        if os.path.exists(self.repo_dir):
            shutil.rmtree(self.repo_dir)
        super(TestGitGetCommitId, self).tearDown()


class TestGitLsRemote(GitUtilsShellTest):
    """build_node.utils.git_utils.git_ls_remote function unit tests."""

    @pytest.mark.skip(reason="We need to rewrite this tests using common library")
    def test_empty_refs_list(self):
        """git_ls_remote returns an empty refs list for an empty repository"""
        repo_path = '/test/git-repository'
        with MockShellCommand('git') as cmd:
            from build_node.utils.git_utils import git_ls_remote

            refs = git_ls_remote(repo_path)
            self.assertEqual(refs, [], msg='refs list must be empty')
            self.__verify_git_args(cmd.get_calls()[0], repo_path)

    @pytest.mark.skip(reason="We need to rewrite this tests using common library")
    def test_tags(self):
        """git_ls_remote returns tags"""
        tags = [
            ('9325ead8de9cfa1fa27b4354cf6fade86b88bef9', '7.0.30-6', 'tag'),
            ('0c8ffca915a88e791d8b041ecd25ca14ac3bea57', '7.0.33-6', 'tag'),
        ]
        self.__test_git_refs(
            tags, 'tag', 'ssh://user@example.com:29418/alt-php70'
        )

    @pytest.mark.skip(reason="We need to rewrite this tests using common library")
    def test_heads(self):
        """git_ls_remote returns heads"""
        heads = [
            ('93182f76f55a7967f77372a06abed6a00bf646f9', 'master', 'head'),
            (
                '9708ca946f80960ad9bb5447fb2a494b95f776d0',
                'refactoring-ui',
                'head',
            ),
        ]
        self.__test_git_refs(
            heads, 'head', 'ssh://user@example.com:29418/alt-php70'
        )
    @pytest.mark.skip(reason="We need to fix this test")
    def test_not_git_repo(self):
        """
        git_ls_remote throws an error if a local path is not a git repository
        """
        user_code = """\
        print("fatal: '/test/not-a-repo' does not appear to " \
              "be a git repository",
              file=sys.stderr)
        print("fatal: Could not read from remote repository.", file=sys.stderr)
        sys.exit(128)
        """
        self.__test_git_command_error(
            user_code,
            '/test/not-a-repo',
            'does not appear to be a git repository',
        )

    @pytest.mark.skip(reason="We need to fix this test")
    def test_project_not_found(self):
        """git_ls_remote throws an error if a remote project is not found"""
        user_code = """\
        print("fatal: Project not found: missing-repo", file=sys.stderr)
        print("fatal: Could not read from remote repository.", file=sys.stderr)
        sys.exit(128)
        """
        repo_path = 'ssh://user@example.com:29418/missing-repo'
        self.__test_git_command_error(
            user_code, repo_path, 'Project not found'
        )

    def __test_git_command_error(self, user_code, repo_path, error_message):
        with MockShellCommand('git', textwrap.dedent(user_code)):
            from build_node.utils.git_utils import (
                GitCommandError,
                git_ls_remote,
            )

            with self.assertRaisesRegex(GitCommandError, error_message):
                git_ls_remote(repo_path)

    def __test_git_refs(self, refs, ref_type, repo_path):
        user_code = '\n'.join(
            ['print("{0}\trefs/{2}s/{1}")'.format(*r) for r in refs]
        )
        with MockShellCommand('git', user_code) as cmd:
            from build_node.utils.git_utils import git_ls_remote

            result_refs = git_ls_remote(
                repo_path, **{'{0}s'.format(ref_type): True}
            )
            self.assertEqual(refs, result_refs)
            call = cmd.get_calls()[0]
            ref_arg = '--{0}s'.format(ref_type)
            self.assertTrue(
                ref_arg in call['argv'][1:],
                msg='git must be called with {0} argument'.format(ref_arg),
            )
            self.__verify_git_args(call, repo_path)

    def __verify_git_args(self, call, repo_path):
        self.assertEqual(
            call['env'].get('LANG'),
            'C',
            msg='git must be called with C locale'
        )
        self.assertEqual(
            call['argv'][1],
            'ls-remote',
            msg='first git argument must be "ls-remote"',
        )
        self.assertEqual(
            call['argv'][-1],
            repo_path,
            msg='last git argument must be a repository path',
        )
