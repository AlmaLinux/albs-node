from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import typing

from cas_wrapper import CasWrapper
import rpm

from build_node.models import Task, Artifact


__all__ = [
    'notarize_build_artifacts',
]


def notarize_build_artifacts(
    task: Task,
    build_artifacts: typing.List[Artifact],
    cas_client: CasWrapper,
    logger: logging.Logger,
    build_host: str,
):
    def notarize_artifacts() -> bool:
        result = True
        with cas_client as cas:
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(cas.notarize, artifact.path,
                                    cas_metadata): artifact
                    for artifact in build_artifacts
                    if not artifact.cas_hash
                }
                for future in as_completed(futures):
                    artifact = futures[future]
                    try:
                        cas_artifact_hash = future.result()
                    except Exception:
                        logger.exception('Cannot notarize artifact:')
                        result = False
                        continue
                    artifact.cas_hash = cas_artifact_hash
        return result

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
            ts = rpm.TransactionSet()
            with open(srpm.path, 'rb') as rpm_pkg:
                hdr = ts.hdrFromFdno(rpm_pkg)
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

    all_artifacts_notarized = notarize_artifacts()
    # ensure that all artifacts notarized
    while not all_artifacts_notarized:
        all_artifacts_notarized = notarize_artifacts()
