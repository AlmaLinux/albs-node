import pytest
from build_node.models import Task


@pytest.fixture
def build_task():
    task = {
        "id": 1,
        "arch": "x86_64",
        "build_id": 1,
        "platform": {
            "type": "rpm",
            "name": "AlmaLinux8",
            "data": {"arch": "x86_64"},
            "arch_list": ["x86_64"],
        },
        "ref": {
            "url": "https://git.almalinux.org/rpms/almalinux-release.git",
            "git_ref": "8.8-1",
            "ref_type": 2,
            "git_commit_hash": None,
        },
        "is_cas_authenticated": False,
        "alma_commit_cas_hash": None,
        "is_secure_boot": False,
        "created_by": {"name": "test", "email": "test@almalinux.com"},
        "repositories": [{
            "name": "test",
            "url": "http://test.repo.com",
            "priority": 1,
            "mock_enabled": False,
        }],
    }
    return Task(**task)


@pytest.fixture
def build_task_src_rpm():
    task = {
        "id": 2,
        "arch": "x86_64",
        "build_id": 1,
        "platform": {
            "type": "rpm",
            "name": "AlmaLinux8",
            "data": {"arch": "x86_64"},
            "arch_list": ["x86_64"],
        },
        "ref": {
            "url": "https://example.com/test-package-1-1.el7.src.rpm",
            "git_ref": "8.8-1",
            "ref_type": 3,
            "git_commit_hash": None,
        },
        "is_cas_authenticated": False,
        "alma_commit_cas_hash": None,
        "is_secure_boot": False,
        "created_by": {"username": "test", "email": "test@almalinux.com"},
        "repositories": [{
            "name": "test",
            "url": "http://test.repo.com",
            "priority": 1,
            "mock_enabled": False,
        }],
    }
    return Task(**task)
