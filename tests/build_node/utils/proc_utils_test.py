import errno
import os
from unittest.mock import patch

import pytest
from albs_common_lib.utils import proc_utils


@pytest.mark.skip(reason="We need to rewrite this tests using common library")
def test_proc_utils():
    proc_utils.get_current_thread_ident()
    assert proc_utils.is_pid_exists(os.getpid())

    e = OSError()
    e.errno = errno.ESRCH
    with patch('build_node.utils.proc_utils.os.kill', side_effect=e):
        assert not proc_utils.is_pid_exists(1)
