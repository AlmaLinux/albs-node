import typing

from pydantic import BaseModel


__all__ = ['Task']


class TaskRepo(BaseModel):

    name: str
    url: str
    priority: int
    mock_enabled: bool


class TaskRef(BaseModel):

    url: str
    git_ref: typing.Optional[str]
    ref_type: int
    git_commit_hash: typing.Optional[str]


class TaskCreatedBy(BaseModel):

    name: str
    email: str

    @property
    def full_name(self):
        return f'{self.name} <{self.email}>'


class TaskPlatform(BaseModel):

    name: str
    # Python 3.6 don't have literals
    # type: typing.Literal['rpm', 'deb']
    type: str
    data: typing.Dict[str, typing.Any]


class Task(BaseModel):

    id: int
    arch: str
    ref: TaskRef
    build_id: int
    alma_commit_cas_hash: typing.Optional[str]
    is_cas_authenticated: bool = False
    platform: TaskPlatform
    created_by: TaskCreatedBy
    repositories: typing.List[TaskRepo]
    built_srpm_url: typing.Optional[str]
    srpm_hash: typing.Optional[str]
    is_secure_boot: bool

    def is_srpm_build_required(self):
        return not (self.ref.url.endswith('src.rpm') or self.built_srpm_url)

    def is_alma_source(self):
        return self.ref.url.startswith('https://git.almalinux.org/')


class Artifact(BaseModel):

    name: str
    # pulp_rpm or s3
    type: str
    href: str
    sha256: str
    path: str
    cas_hash: typing.Optional[str]
