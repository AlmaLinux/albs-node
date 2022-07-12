import typing

from cas_wrapper import CasWrapper

from build_node.models import Task, Artifact
from build_node.utils.rpm_utils import get_rpm_metadata

__all__ = [
    'notarize_build_artifacts',
]


def notarize_build_artifacts(
    task: Task,
    build_artifacts: typing.List[Artifact],
    cas_client: CasWrapper,
    build_host: str,
):
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
        srpm = next((
            artifact for artifact in build_artifacts
            if artifact.name.endswith('.src.rpm')
        ), None)
        if srpm:
            hdr = get_rpm_metadata(srpm.path)
            srpm_nevra = (
                f"{hdr['epoch']}:{hdr['name']}-{hdr['version']}-"
                f"{hdr['release']}.src"
            )
            srpm_sha256 = task.srpm_hash if task.srpm_hash else srpm.sha256
            cas_metadata.update({
                'source_type': 'srpm',
                'srpm_url': task.ref.url,
                'srpm_sha256': srpm_sha256,
                'srpm_nevra': srpm_nevra,
            })

    notarized_artifacts = {}
    artifact_paths = [artifact.path for artifact in build_artifacts]
    _, artifacts = cas_client.notarize_artifacts(
        artifact_paths=artifact_paths,
        metadata=cas_metadata,
    )
    notarized_artifacts.update(artifacts)
    for artifact in build_artifacts:
        artifact.cas_hash = notarized_artifacts.get(artifact.path)
