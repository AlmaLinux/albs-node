from albs_common_lib.utils import hashing


def test_hashing():
    sha1_hasher = hashing.get_hasher("sha")
    assert sha1_hasher.name == 'sha256'
    sha256_hasher = hashing.get_hasher("sha1")
    assert sha256_hasher.name == 'sha1'

