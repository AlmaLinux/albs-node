import time
import typing

from cas_wrapper import CasWrapper

from build_node.models import Task
from build_node.utils.file_utils import filter_files, hash_file
from build_node.utils.rpm_utils import get_rpm_metadata

__all__ = [
    'notarize_build_artifacts',
]


def notarize_build_artifacts(
    task: Task,
    artifacts_dir: str,
    cas_client: CasWrapper,
    build_host: str,
) -> typing.Tuple[typing.Dict[str, str], typing.List[str]]:

    srpm_path = None
    artifact_paths = []
    for artifact_path in filter_files(
        artifacts_dir,
        lambda x: any((x.endswith(type) for type in ('.log', '.cfg', '.rpm'))),
    ):
        if artifact_path.endswith('.src.rpm'):
            srpm_path = artifact_path
        artifact_paths.append(artifact_path)

    cas_metadata = {
        'build_id': task.build_id,
        'build_host': build_host,
        'build_arch': task.arch,
        'built_by': task.created_by.full_name,
        'sbom_api': '0.1',
    }
    if task.is_alma_source() and task.alma_commit_cas_hash:
        cas_metadata['alma_commit_sbom_hash'] = task.alma_commit_cas_hash
    if task.ref.git_ref:
        cas_metadata.update({
            'source_type': 'git',
            'git_url': task.ref.url,
            'git_ref': task.ref.git_ref,
            'git_commit': task.ref.git_commit_hash,
        })
    else:
        if srpm_path:
            hdr = get_rpm_metadata(srpm_path)
            srpm_nevra = (
                f"{hdr['epoch']}:{hdr['name']}-{hdr['version']}-"
                f"{hdr['release']}.src"
            )
            if task.srpm_hash:
                srpm_sha256 = task.srpm_hash
            else:
                srpm_sha256 = hash_file(srpm_path, hash_type='sha256')
            cas_metadata.update({
                'source_type': 'srpm',
                'srpm_url': task.ref.url,
                'srpm_sha256': srpm_sha256,
                'srpm_nevra': srpm_nevra,
            })

    notarized_artifacts = {}
    all_artifacts_is_notarized, artifacts = cas_client.notarize_artifacts(
        artifact_paths=artifact_paths,
        metadata=cas_metadata,
    )
    notarized_artifacts.update(artifacts)

    # sometimes we cannot notarize artifacts because of network problems
    max_notarize_retries = 5
    while not all_artifacts_is_notarized and max_notarize_retries:
        time.sleep(10)
        all_artifacts_is_notarized, artifacts = cas_client.notarize_artifacts(
            artifact_paths=[
                path for path in artifact_paths
                if path not in notarized_artifacts
            ],
            metadata=cas_metadata,
        )
        notarized_artifacts.update(artifacts)
        max_notarize_retries -= 1
    non_notarized_artifacts = [
        path for path in artifact_paths
        if path not in notarized_artifacts
    ]

    return notarized_artifacts, non_notarized_artifacts
