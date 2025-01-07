import os
from albs_common_lib.utils import index_utils


def test_index_utils(request):
    pkg_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(request.node.fspath))),
        'test_repo/a-1.0-1.el6.noarch.rpm'
    )
    pkg_data = index_utils.extract_metadata(pkg_path)
    # Check a couple of fields
    assert pkg_data['arch'] == 'noarch'
    assert pkg_data['name'] == 'a'
    assert pkg_data['sourcerpm'] == 'a-1.0-1.el6.src.rpm'
