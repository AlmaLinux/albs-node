import typing

from pydantic import BaseModel


__all__ = ['Task']


class TaskRepo(BaseModel):

    name: str
    url: str
    channel = 0


class TaskRef(BaseModel):

    ref_type: str
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
    s3_artifacts_dir: str
    ref: TaskRef
    platform: TaskPlatform
    created_by: TaskCreatedBy
    repositories: typing.List[TaskRepo]

    def is_srpm_build_required(self):
        return self.ref.ref_type != 'srpm'


class Artifact(BaseModel):

    name: str
    # pulp_rpm or s3
    type: str
    href: str
