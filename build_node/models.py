import typing

from pydantic import BaseModel


__all__ = ['Task']


class TaskRepo(BaseModel):

    name: str
    url: str


class TaskRef(BaseModel):

    url: str
    git_ref: typing.Optional[str]


class TaskCreatedBy(BaseModel):

    name: str
    email: str


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
    platform: TaskPlatform
    created_by: TaskCreatedBy
    repositories: typing.List[TaskRepo]
    is_secure_boot: bool

    def is_srpm_build_required(self):
        return not self.ref.url.endswith('src.rpm')

    def is_alma_source(self):
        return self.ref.url.startswith('https://git.almalinux.org/')


class Artifact(BaseModel):

    name: str
    # pulp_rpm or s3
    type: str
    href: str
