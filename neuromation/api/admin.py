from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Dict, List, Optional

from neuromation.api.config import Config
from neuromation.api.core import _Core
from neuromation.api.utils import NoPublicConstructor


@unique
class _ClusterUserRoleType(str, Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    USER = "user"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class _ClusterUser:
    user_name: str
    role: _ClusterUserRoleType


@dataclass(frozen=True)
class _Quota:
    total_gpu_run_time_minutes: Optional[int]
    total_non_gpu_run_time_minutes: Optional[int]


@dataclass(frozen=True)
class _ClusterUserWithQuota(_ClusterUser):
    quota: _Quota


@dataclass(frozen=True)
class _NodePool:
    min_size: int
    max_size: int
    machine_type: str
    available_cpu: float
    available_memory_mb: int
    gpu: int = 0
    gpu_model: Optional[str] = None
    is_tpu_enabled: bool = False
    is_preemptible: bool = False
    idle_size: int = 0


@dataclass(frozen=True)
class _Storage:
    description: str


@dataclass(frozen=True)
class _CloudProvider:
    type: str
    region: Optional[str]
    zones: List[str]
    node_pools: List[_NodePool]
    storage: Optional[_Storage]


@dataclass(frozen=True)
class _Cluster:
    name: str
    status: str
    cloud_provider: Optional[_CloudProvider] = None


class _Admin(metaclass=NoPublicConstructor):
    def __init__(self, core: _Core, config: Config) -> None:
        self._core = core
        self._config = config

    async def list_clusters(self) -> Dict[str, _Cluster]:
        url = (self._config.api_url / "clusters").with_query(
            include="cloud_provider_infra"
        )
        auth = await self._config._api_auth()
        async with self._core.request("GET", url, auth=auth) as resp:
            payload = await resp.json()
            ret = {}
            for item in payload:
                cluster = _cluster_from_api(item)
                ret[cluster.name] = cluster
            return ret

    async def add_cluster(self, name: str, config: Dict[str, Any]) -> None:
        url = self._config.admin_url / "clusters"
        auth = await self._config._api_auth()
        payload = {"name": name}
        async with self._core.request("POST", url, auth=auth, json=payload) as resp:
            resp
        url = self._config.api_url / "clusters" / name / "cloud_provider"
        url = url.with_query(start_deployment="true")
        async with self._core.request("PUT", url, auth=auth, json=config) as resp:
            resp

    async def list_cluster_users(
        self, cluster_name: Optional[str] = None
    ) -> List[_ClusterUser]:
        cluster_name = cluster_name or self._config.cluster_name
        url = self._config.admin_url / "clusters" / cluster_name / "users"
        auth = await self._config._api_auth()
        async with self._core.request("GET", url, auth=auth) as resp:
            res = await resp.json()
            return [_cluster_user_from_api(payload) for payload in res]

    async def add_cluster_user(
        self, cluster_name: str, user_name: str, role: str
    ) -> _ClusterUser:
        url = self._config.admin_url / "clusters" / cluster_name / "users"
        payload = {"user_name": user_name, "role": role}
        auth = await self._config._api_auth()

        async with self._core.request("POST", url, json=payload, auth=auth) as resp:
            payload = await resp.json()
            return _cluster_user_from_api(payload)

    async def remove_cluster_user(self, cluster_name: str, user_name: str) -> None:
        url = self._config.admin_url / "clusters" / cluster_name / "users" / user_name
        auth = await self._config._api_auth()

        async with self._core.request("DELETE", url, auth=auth):
            # No content response
            pass

    async def set_user_quota(
        self,
        cluster_name: str,
        user_name: str,
        gpu_value_minutes: Optional[float],
        non_gpu_value_minutes: Optional[float],
    ) -> _ClusterUserWithQuota:
        url = (
            self._config.admin_url
            / "clusters"
            / cluster_name
            / "users"
            / user_name
            / "quota"
        )
        payload = {
            "quota": {
                "total_gpu_run_time_minutes": gpu_value_minutes,
                "total_non_gpu_run_time_minutes": non_gpu_value_minutes,
            },
        }
        payload["quota"] = {k: v for k, v in payload["quota"].items() if v is not None}

        auth = await self._config._api_auth()

        async with self._core.request("PATCH", url, json=payload, auth=auth) as resp:
            payload = await resp.json()
            return _cluster_user_with_quota_from_api(user_name, payload)

    async def add_user_quota(
        self,
        cluster_name: str,
        user_name: str,
        additional_gpu_value_minutes: Optional[float],
        additional_non_gpu_value_minutes: Optional[float],
    ) -> _ClusterUserWithQuota:
        url = (
            self._config.admin_url
            / "clusters"
            / cluster_name
            / "users"
            / user_name
            / "quota"
        )
        payload = {
            "additional_quota": {
                "total_gpu_run_time_minutes": additional_gpu_value_minutes,
                "total_non_gpu_run_time_minutes": additional_non_gpu_value_minutes,
            },
        }
        payload["additional_quota"] = {
            k: v for k, v in payload["additional_quota"].items() if v is not None
        }
        auth = await self._config._api_auth()

        async with self._core.request("PATCH", url, json=payload, auth=auth) as resp:
            payload = await resp.json()
            return _cluster_user_with_quota_from_api(user_name, payload)


def _cluster_user_from_api(payload: Dict[str, Any]) -> _ClusterUser:
    return _ClusterUser(
        user_name=payload["user_name"], role=_ClusterUserRoleType(payload["role"])
    )


def _cluster_user_with_quota_from_api(
    user_name: str, payload: Dict[str, Any]
) -> _ClusterUserWithQuota:
    quota_dict = payload.get("quota", {})
    return _ClusterUserWithQuota(
        user_name=user_name,
        role=_ClusterUserRoleType(payload["role"]),
        quota=_Quota(
            quota_dict.get("total_gpu_run_time_minutes"),
            quota_dict.get("total_non_gpu_run_time_minutes"),
        ),
    )


def _cluster_from_api(payload: Dict[str, Any]) -> _Cluster:
    if "cloud_provider" in payload:
        cloud_provider = payload["cloud_provider"]
        return _Cluster(
            name=payload["name"],
            status=payload["status"],
            cloud_provider=_CloudProvider(
                type=cloud_provider["type"],
                region=cloud_provider.get("region"),
                zones=(
                    [cloud_provider["zone"]]
                    if "zone" in cloud_provider
                    else cloud_provider.get("zones", [])
                ),
                node_pools=[
                    _node_pool_from_api(np)
                    for np in cloud_provider.get("node_pools", [])
                ],
                storage=(
                    _storage_from_api(cloud_provider["storage"])
                    if "storage" in cloud_provider
                    else None
                ),
            ),
        )
    return _Cluster(name=payload["name"], status=payload["status"])


def _node_pool_from_api(payload: Dict[str, Any]) -> _NodePool:
    return _NodePool(
        min_size=payload["min_size"],
        max_size=payload["max_size"],
        idle_size=payload.get("idle_size", 0),
        machine_type=payload["machine_type"],
        available_cpu=payload["available_cpu"],
        available_memory_mb=payload["available_memory_mb"],
        gpu=payload.get("gpu", 0),
        gpu_model=payload.get("gpu_model"),
        is_tpu_enabled=payload.get("is_tpu_enabled", False),
        is_preemptible=payload.get("is_preemptible", False),
    )


def _storage_from_api(payload: Dict[str, Any]) -> _Storage:
    return _Storage(description=payload["description"])
