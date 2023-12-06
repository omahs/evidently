import os.path
from abc import ABC
from abc import abstractmethod
from typing import Callable
from typing import Optional

import yaml

from evidently._pydantic_compat import BaseModel
from evidently._pydantic_compat import Field
from evidently.pydantic_utils import EvidentlyBaseModel
from evidently.ui.base import ProjectManager
from evidently.ui.type_aliases import OrgID
from evidently.ui.type_aliases import UserID


class TelemetryConfig(BaseModel):
    url: str = "http://35.232.253.5:8000/api/v1/s2s/event?ip_policy=strict"
    tool_name: str = "evidently"
    service_name: str = "service"
    token: str = "s2s.5xmxpip2ax4ut5rrihfjhb.uqcoh71nviknmzp77ev6rd"
    enabled: bool = False


class ServiceConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class SecurityService(EvidentlyBaseModel):
    @abstractmethod
    def get_user_id_dependency(self) -> Callable[..., Optional[UserID]]:
        raise NotImplementedError

    def get_org_id_dependency(self) -> Callable[..., Optional[OrgID]]:
        return lambda: None


class NoSecurityService(SecurityService):
    def get_user_id_dependency(self) -> Callable[..., Optional[UserID]]:
        return lambda: None


class StorageConfig(EvidentlyBaseModel, ABC):
    @abstractmethod
    def create_project_manager(self) -> ProjectManager:
        raise NotImplementedError


def _default_storage():
    from evidently.ui.storage.local import LocalStorageConfig

    return LocalStorageConfig(path="workspace", autorefresh=True)


class Configuration(BaseModel):
    service: ServiceConfig = Field(default_factory=ServiceConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    storage: StorageConfig = Field(default_factory=_default_storage)
    security: SecurityService = NoSecurityService()


_configuration: Optional[Configuration] = None


def init_configuration(path: str):
    global _configuration

    if not os.path.exists(path):
        _configuration = Configuration()
        return _configuration
    with open(path) as f:
        dict_obj = yaml.load(f, yaml.SafeLoader)
        _configuration = Configuration.parse_obj(dict_obj)
    return _configuration


def get_configuration() -> Configuration:
    if _configuration is None:
        raise ValueError("Configuration isn't loaded")

    yield _configuration
