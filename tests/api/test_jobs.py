from typing import Any, Callable, Dict, List, Optional

import pytest
from aiohttp import web
from yarl import URL

from neuromation.api import (
    Client,
    Container,
    Image,
    JobDescription,
    JobStatus,
    JobStatusHistory,
    JobTelemetry,
    NetworkPortForwarding,
    ResourceNotFound,
    Resources,
    Volume,
)
from tests import _TestServerFactory


class TestJobDescription:
    @pytest.fixture
    def job_desc_primitive(self):
        return {
            "id": "job-id",
            "owner": "owner",
            "status": "running",
            "container": {
                "image": "ubuntu:latest",
                "resources": {"memory_mb": 256, "cpu": 8, "shm": True},
                "command": "submit-command",
                "ssh_auth_server": "ssh_auth_server",
            },
            "history": {},
            "is_preemptible": False,
            "ssh_auth_server": "ssh-auth",
        }

    @pytest.fixture
    def job_desc_factory(self):
        def _factory(http_url: str, job_name: Optional[str] = None):
            return JobDescription(
                http_url=URL(http_url),
                id="job-id",
                name=job_name,
                owner="owner",
                status=JobStatus.RUNNING,
                container=Container(
                    image="ubuntu:latest",
                    command="submit-command",
                    resources=Resources.create(
                        cpu=8, memory=256, extshm=True, gpu=None, gpu_model=None
                    ),
                ),
                history=JobStatusHistory(
                    status=JobStatus.UNKNOWN,
                    reason="",
                    description="",
                    created_at="",
                    started_at="",
                    finished_at="",
                ),
                is_preemptible=False,
                ssh_auth_server=URL("ssh-auth"),
            )

        yield _factory

    def test_from_api_http_url(self, job_desc_primitive, job_desc_factory):
        HTTP_URL = "http://job-id.jobs.neu.ro/"
        job_desc_primitive["http_url"] = HTTP_URL
        expected = job_desc_factory(http_url=HTTP_URL)
        assert JobDescription.from_api(job_desc_primitive) == expected

    def test_from_api_http_url_named(self, job_desc_primitive, job_desc_factory):
        NAME = "job-name"
        HTTP_URL = "http://job-id.jobs.neu.ro/"
        HTTP_URL_NAMED = f"http://{NAME}-owner.jobs.neu.ro/"
        job_desc_primitive["name"] = NAME
        job_desc_primitive["http_url"] = HTTP_URL
        job_desc_primitive["http_url_named"] = HTTP_URL_NAMED
        expected = job_desc_factory(http_url=HTTP_URL_NAMED, job_name=NAME)
        assert JobDescription.from_api(job_desc_primitive) == expected

    def test_from_api_http_url_not_exposed(self, job_desc_primitive, job_desc_factory):
        expected = job_desc_factory(http_url="")
        assert JobDescription.from_api(job_desc_primitive) == expected

    def test_from_api_http_url_named_not_exposed(
        self, job_desc_primitive, job_desc_factory
    ):
        NAME = "job-name"
        job_desc_primitive["name"] = NAME
        expected = job_desc_factory(http_url="", job_name=NAME)
        assert JobDescription.from_api(job_desc_primitive) == expected


_MakeClient = Callable[..., Client]



async def test_jobs_monitor(aiohttp_server, make_client):
    async def log_stream(request: web.Request) -> web.StreamResponse:
        assert request.headers["Accept-Encoding"] == "identity"
        resp = web.StreamResponse()
        resp.enable_chunked_encoding()
        resp.enable_compression(web.ContentCoding.identity)
        await resp.prepare(request)
        for i in range(10):
            await resp.write(b"chunk " + str(i).encode("ascii") + b"\n")
        return resp

    app = web.Application()
    app.router.add_get("/jobs/job-id/log", log_stream)

    srv = await aiohttp_server(app)

    lst = []
    async with make_client(srv.make_url("/")) as client:
        async for data in client.jobs.monitor("job-id"):
            lst.append(data)

    assert b"".join(lst) == b"".join(
        [
            b"chunk 0\n",
            b"chunk 1\n",
            b"chunk 2\n",
            b"chunk 3\n",
            b"chunk 4\n",
            b"chunk 5\n",
            b"chunk 6\n",
            b"chunk 7\n",
            b"chunk 8\n",
            b"chunk 9\n",
        ]
    )


async def test_monitor_notexistent_job(
    aiohttp_server: Any, make_client: _MakeClient
) -> None:
    async def handler(request: web.Request) -> web.Response:
        raise web.HTTPNotFound()

    app = web.Application()
    app.router.add_get("/jobs/job-id/log", handler)

    srv = await aiohttp_server(app)

    lst = []
    async with make_client(srv.make_url("/")) as client:
        with pytest.raises(ResourceNotFound):
            async for data in client.jobs.monitor("job-id"):
                lst.append(data)
    assert lst == []


async def test_job_top(
    aiohttp_server: _TestServerFactory, make_client: _MakeClient
) -> None:
    def get_data_chunk(index: int) -> Dict[str, Any]:
        return {
            "cpu": 0.5,
            "memory": 50,
            "timestamp": index,
            "gpu_duty_cycle": 50,
            "gpu_memory": 55.6,
        }

    def get_job_telemetry(index: int) -> JobTelemetry:
        return JobTelemetry(
            cpu=0.5, memory=50, timestamp=index, gpu_duty_cycle=50, gpu_memory=55.6
        )

    async def top_stream(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        for i in range(10):
            await ws.send_json(get_data_chunk(i))

        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/jobs/job-id/top", top_stream)

    srv = await aiohttp_server(app)

    lst = []
    async with make_client(srv.make_url("/")) as client:
        async for data in client.jobs.top("job-id"):
            lst.append(data)

    assert lst == [get_job_telemetry(i) for i in range(10)]


async def test_top_finished_job(
    aiohttp_server: _TestServerFactory, make_client: _MakeClient
) -> None:
    async def handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/jobs/job-id/top", handler)

    srv = await aiohttp_server(app)

    lst = []
    async with make_client(srv.make_url("/")) as client:
        with pytest.raises(ValueError, match="not running"):
            async for data in client.jobs.top("job-id"):
                lst.append(data)
    assert lst == []


async def test_top_nonexisting_job(
    aiohttp_server: _TestServerFactory, make_client: _MakeClient
) -> None:
    async def handler(request: web.Request) -> web.Response:
        raise web.HTTPBadRequest()

    app = web.Application()
    app.router.add_get("/jobs/job-id/top", handler)

    srv = await aiohttp_server(app)

    lst = []
    async with make_client(srv.make_url("/")) as client:
        with pytest.raises(ValueError, match="not found"):
            async for data in client.jobs.top("job-id"):
                lst.append(data)
    assert lst == []


async def test_kill_not_found_error(
    aiohttp_server: _TestServerFactory, make_client: _MakeClient
) -> None:
    async def handler(request: web.Request) -> web.Response:
        raise web.HTTPNotFound()

    app = web.Application()
    app.router.add_delete("/jobs/job-id", handler)

    srv = await aiohttp_server(app)

    async with make_client(srv.make_url("/")) as client:
        with pytest.raises(ResourceNotFound):
            await client.jobs.kill("job-id")


async def test_kill_ok(
    aiohttp_server: _TestServerFactory, make_client: _MakeClient
) -> None:
    async def handler(request: web.Request) -> web.Response:
        raise web.HTTPNoContent()

    app = web.Application()
    app.router.add_delete("/jobs/job-id", handler)

    srv = await aiohttp_server(app)

    async with make_client(srv.make_url("/")) as client:
        ret = await client.jobs.kill("job-id")

    assert ret is None


async def test_status_failed(
    aiohttp_server: _TestServerFactory, make_client: _MakeClient
) -> None:
    JSON = {
        "status": "failed",
        "id": "job-id",
        "description": "This is job description, not a history description",
        "http_url": "http://my_host:8889",
        "ssh_server": "ssh://my_host.ssh:22",
        "ssh_auth_server": "ssh://my_host.ssh:22",
        "history": {
            "created_at": "2018-08-29T12:23:13.981621+00:00",
            "started_at": "2018-08-29T12:23:15.988054+00:00",
            "finished_at": "2018-08-29T12:59:31.427795+00:00",
            "reason": "ContainerCannotRun",
            "description": "Not enough coffee",
        },
        "is_preemptible": True,
        "owner": "owner",
        "container": {
            "image": "submit-image-name",
            "command": "submit-command",
            "http": {"port": 8181},
            "resources": {
                "memory_mb": "4096",
                "cpu": 7.0,
                "shm": True,
                "gpu": 1,
                "gpu_model": "test-gpu-model",
            },
            "volumes": [
                {
                    "src_storage_uri": "storage://test-user/path_read_only",
                    "dst_path": "/container/read_only",
                    "read_only": True,
                },
                {
                    "src_storage_uri": "storage://test-user/path_read_write",
                    "dst_path": "/container/path_read_write",
                    "read_only": False,
                },
            ],
        },
    }

    async def handler(request: web.Request) -> web.Response:
        return web.json_response(JSON)

    app = web.Application()
    app.router.add_get("/jobs/job-id", handler)

    srv = await aiohttp_server(app)

    async with make_client(srv.make_url("/")) as client:
        ret = await client.jobs.status("job-id")

    assert ret == JobDescription.from_api(JSON)


async def test_status_with_ssh_and_http(
    aiohttp_server: _TestServerFactory, make_client: _MakeClient
) -> None:
    JSON = {
        "status": "running",
        "id": "job-id",
        "description": "This is job description, not a history description",
        "http_url": "http://my_host:8889",
        "ssh_server": "ssh://my_host.ssh:22",
        "ssh_auth_server": "ssh://my_host.ssh:22",
        "history": {
            "created_at": "2018-08-29T12:23:13.981621+00:00",
            "started_at": "2018-08-29T12:23:15.988054+00:00",
            "finished_at": "2018-08-29T12:59:31.427795+00:00",
            "reason": "OK",
            "description": "Everything is fine",
        },
        "is_preemptible": True,
        "owner": "owner",
        "container": {
            "image": "submit-image-name",
            "command": "submit-command",
            "http": {"port": 8181},
            "resources": {
                "memory_mb": "4096",
                "cpu": 7.0,
                "shm": True,
                "gpu": 1,
                "gpu_model": "test-gpu-model",
            },
            "volumes": [
                {
                    "src_storage_uri": "storage://test-user/path_read_only",
                    "dst_path": "/container/read_only",
                    "read_only": True,
                },
                {
                    "src_storage_uri": "storage://test-user/path_read_write",
                    "dst_path": "/container/path_read_write",
                    "read_only": False,
                },
            ],
        },
    }

    async def handler(request: web.Request) -> web.Response:
        return web.json_response(JSON)

    app = web.Application()
    app.router.add_get("/jobs/job-id", handler)

    srv = await aiohttp_server(app)

    async with make_client(srv.make_url("/")) as client:
        ret = await client.jobs.status("job-id")

    assert ret == JobDescription.from_api(JSON)


async def test_job_submit(
    aiohttp_server: _TestServerFactory, make_client: _MakeClient
) -> None:
    JSON = {
        "id": "job-cf519ed3-9ea5-48f6-a8c5-492b810eb56f",
        "status": "failed",
        "history": {
            "status": "failed",
            "reason": "Error",
            "description": "Mounted on Avail\\n/dev/shm     " "64M\\n\\nExit code: 1",
            "created_at": "2018-09-25T12:28:21.298672+00:00",
            "started_at": "2018-09-25T12:28:59.759433+00:00",
            "finished_at": "2018-09-25T12:28:59.759433+00:00",
        },
        "owner": "owner",
        "container": {
            "image": "gcr.io/light-reality-205619/ubuntu:latest",
            "command": "date",
            "resources": {
                "cpu": 1.0,
                "memory_mb": 16384,
                "gpu": 1,
                "shm": False,
                "gpu_model": "nvidia-tesla-p4",
            },
        },
        "http_url": "http://my_host:8889",
        "ssh_server": "ssh://my_host.ssh:22",
        "ssh_auth_server": "ssh://my_host.ssh:22",
        "is_preemptible": False,
    }

    async def handler(request: web.Request) -> web.Response:
        data = await request.json()
        assert data == {
            "container": {
                "image": "submit-image-name",
                "command": "submit-command",
                "http": {"port": 8181, "requires_auth": True},
                "resources": {
                    "memory_mb": 16384,
                    "cpu": 7.0,
                    "shm": True,
                    "gpu": 1,
                    "gpu_model": "test-gpu-model",
                },
                "volumes": [
                    {
                        "src_storage_uri": "storage://test-user/path_read_only",
                        "dst_path": "/container/read_only",
                        "read_only": True,
                    },
                    {
                        "src_storage_uri": "storage://test-user/path_read_write",
                        "dst_path": "/container/path_read_write",
                        "read_only": False,
                    },
                ],
            },
            "is_preemptible": False,
        }

        return web.json_response(JSON)

    app = web.Application()
    app.router.add_post("/jobs", handler)

    srv = await aiohttp_server(app)

    async with make_client(srv.make_url("/")) as client:
        image = Image(image="submit-image-name", command="submit-command")
        network = NetworkPortForwarding({"http": 8181})
        resources = Resources.create(7, 1, "test-gpu-model", 16384, True)
        volumes: List[Volume] = [
            Volume("storage://test-user/path_read_only", "/container/read_only", True),
            Volume(
                "storage://test-user/path_read_write",
                "/container/path_read_write",
                False,
            ),
        ]
        ret = await client.jobs.submit(
            image=image,
            resources=resources,
            network=network,
            volumes=volumes,
            is_preemptible=False,
        )

    assert ret == JobDescription.from_api(JSON)


async def test_job_submit_with_name_and_description(
    aiohttp_server: _TestServerFactory, make_client: _MakeClient
) -> None:
    JSON = {
        "id": "job-cf519ed3-9ea5-48f6-a8c5-492b810eb56f",
        "name": "test-job-name",
        "description": "job description",
        "status": "failed",
        "history": {
            "status": "failed",
            "reason": "Error",
            "description": "Mounted on Avail\\n/dev/shm     " "64M\\n\\nExit code: 1",
            "created_at": "2018-09-25T12:28:21.298672+00:00",
            "started_at": "2018-09-25T12:28:59.759433+00:00",
            "finished_at": "2018-09-25T12:28:59.759433+00:00",
        },
        "owner": "owner",
        "container": {
            "image": "gcr.io/light-reality-205619/ubuntu:latest",
            "command": "date",
            "resources": {
                "cpu": 1.0,
                "memory_mb": 16384,
                "gpu": 1,
                "shm": False,
                "gpu_model": "nvidia-tesla-p4",
            },
        },
        "http_url": "http://my_host:8889",
        "ssh_server": "ssh://my_host.ssh:22",
        "ssh_auth_server": "ssh://my_host.ssh:22",
        "is_preemptible": False,
    }

    async def handler(request: web.Request) -> web.Response:
        data = await request.json()
        assert data == {
            "container": {
                "image": "submit-image-name",
                "command": "submit-command",
                "http": {"port": 8181, "requires_auth": True},
                "resources": {
                    "memory_mb": 16384,
                    "cpu": 7.0,
                    "shm": True,
                    "gpu": 1,
                    "gpu_model": "test-gpu-model",
                },
                "volumes": [
                    {
                        "src_storage_uri": "storage://test-user/path_read_only",
                        "dst_path": "/container/read_only",
                        "read_only": True,
                    },
                    {
                        "src_storage_uri": "storage://test-user/path_read_write",
                        "dst_path": "/container/path_read_write",
                        "read_only": False,
                    },
                ],
            },
            "is_preemptible": False,
            "name": "test-job-name",
            "description": "job description",
        }

        return web.json_response(JSON)

    app = web.Application()
    app.router.add_post("/jobs", handler)

    srv = await aiohttp_server(app)

    async with make_client(srv.make_url("/")) as client:
        image = Image(image="submit-image-name", command="submit-command")
        network = NetworkPortForwarding({"http": 8181})
        resources = Resources.create(7, 1, "test-gpu-model", 16384, True)
        volumes: List[Volume] = [
            Volume("storage://test-user/path_read_only", "/container/read_only", True),
            Volume(
                "storage://test-user/path_read_write",
                "/container/path_read_write",
                False,
            ),
        ]
        ret = await client.jobs.submit(
            image=image,
            resources=resources,
            network=network,
            volumes=volumes,
            is_preemptible=False,
            name="test-job-name",
            description="job description",
        )
    assert ret == JobDescription.from_api(JSON)


async def test_job_submit_no_volumes(
    aiohttp_server: _TestServerFactory, make_client: _MakeClient
) -> None:
    JSON = {
        "id": "job-cf519ed3-9ea5-48f6-a8c5-492b810eb56f",
        "name": "test-job-name",
        "status": "failed",
        "history": {
            "status": "failed",
            "reason": "Error",
            "description": "Mounted on Avail\\n/dev/shm     " "64M\\n\\nExit code: 1",
            "created_at": "2018-09-25T12:28:21.298672+00:00",
            "started_at": "2018-09-25T12:28:59.759433+00:00",
            "finished_at": "2018-09-25T12:28:59.759433+00:00",
        },
        "owner": "owner",
        "container": {
            "image": "gcr.io/light-reality-205619/ubuntu:latest",
            "command": "date",
            "resources": {
                "cpu": 1.0,
                "memory_mb": 16384,
                "gpu": 1,
                "shm": False,
                "gpu_model": "nvidia-tesla-p4",
            },
        },
        "http_url": "http://my_host:8889",
        "ssh_server": "ssh://my_host.ssh:22",
        "ssh_auth_server": "ssh://my_host.ssh:22",
        "is_preemptible": False,
    }

    async def handler(request: web.Request) -> web.Response:
        data = await request.json()
        assert data == {
            "container": {
                "image": "submit-image-name",
                "command": "submit-command",
                "http": {"port": 8181, "requires_auth": True},
                "resources": {
                    "memory_mb": 16384,
                    "cpu": 7.0,
                    "shm": True,
                    "gpu": 1,
                    "gpu_model": "test-gpu-model",
                },
            },
            "is_preemptible": False,
            "name": "test-job-name",
            "description": "job description",
        }

        return web.json_response(JSON)

    app = web.Application()
    app.router.add_post("/jobs", handler)

    srv = await aiohttp_server(app)

    async with make_client(srv.make_url("/")) as client:
        image = Image(image="submit-image-name", command="submit-command")
        network = NetworkPortForwarding({"http": 8181})
        resources = Resources.create(7, 1, "test-gpu-model", 16384, True)
        ret = await client.jobs.submit(
            image=image,
            resources=resources,
            network=network,
            volumes=None,
            is_preemptible=False,
            name="test-job-name",
            description="job description",
        )

    assert ret == JobDescription.from_api(JSON)


async def test_job_submit_preemptible(
    aiohttp_server: _TestServerFactory, make_client: _MakeClient
) -> None:
    JSON = {
        "id": "job-cf519ed3-9ea5-48f6-a8c5-492b810eb56f",
        "name": "test-job-name",
        "status": "failed",
        "history": {
            "status": "failed",
            "reason": "Error",
            "description": "Mounted on Avail\\n/dev/shm     " "64M\\n\\nExit code: 1",
            "created_at": "2018-09-25T12:28:21.298672+00:00",
            "started_at": "2018-09-25T12:28:59.759433+00:00",
            "finished_at": "2018-09-25T12:28:59.759433+00:00",
        },
        "owner": "owner",
        "container": {
            "image": "gcr.io/light-reality-205619/ubuntu:latest",
            "command": "date",
            "resources": {
                "cpu": 1.0,
                "memory_mb": 16384,
                "gpu": 1,
                "shm": False,
                "gpu_model": "nvidia-tesla-p4",
            },
        },
        "is_preemptible": True,
        "http_url": "http://my_host:8889",
        "ssh_server": "ssh://my_host.ssh:22",
        "ssh_auth_server": "ssh://my_host.ssh:22",
    }

    async def handler(request: web.Request) -> web.Response:
        data = await request.json()
        assert data == {
            "container": {
                "image": "submit-image-name",
                "command": "submit-command",
                "http": {"port": 8181, "requires_auth": True},
                "resources": {
                    "memory_mb": 16384,
                    "cpu": 7.0,
                    "shm": True,
                    "gpu": 1,
                    "gpu_model": "test-gpu-model",
                },
                "volumes": [
                    {
                        "src_storage_uri": "storage://test-user/path_read_only",
                        "dst_path": "/container/read_only",
                        "read_only": True,
                    },
                    {
                        "src_storage_uri": "storage://test-user/path_read_write",
                        "dst_path": "/container/path_read_write",
                        "read_only": False,
                    },
                ],
            },
            "is_preemptible": True,
            "name": "test-job-name",
            "description": "job description",
        }

        return web.json_response(JSON)

    app = web.Application()
    app.router.add_post("/jobs", handler)

    srv = await aiohttp_server(app)

    async with make_client(srv.make_url("/")) as client:
        image = Image(image="submit-image-name", command="submit-command")
        network = NetworkPortForwarding({"http": 8181})
        resources = Resources.create(7, 1, "test-gpu-model", 16384, True)
        volumes: List[Volume] = [
            Volume("storage://test-user/path_read_only", "/container/read_only", True),
            Volume(
                "storage://test-user/path_read_write",
                "/container/path_read_write",
                False,
            ),
        ]
        ret = await client.jobs.submit(
            image=image,
            resources=resources,
            network=network,
            volumes=volumes,
            is_preemptible=True,
            name="test-job-name",
            description="job description",
        )

    assert ret == JobDescription.from_api(JSON)


@pytest.mark.parametrize(
    "volume", ["storage:///", ":", "::::", "", "storage:///data/:/data/rest:wrong"]
)
def test_volume_from_str_fail(volume: str) -> None:
    with pytest.raises(ValueError):
        Volume.from_cli("testuser", volume)


def create_job_response(
    id: str, status: str, name: Optional[str] = None
) -> Dict[str, Any]:
    result = {
        "id": id,
        "status": status,
        "history": {
            "status": "failed",
            "reason": "Error",
            "description": "Mounted on Avail\\n/dev/shm     " "64M\\n\\nExit code: 1",
            "created_at": "2018-09-25T12:28:21.298672+00:00",
            "started_at": "2018-09-25T12:28:59.759433+00:00",
            "finished_at": "2018-09-25T12:28:59.759433+00:00",
        },
        "ssh_auth_server": "ssh://my_host.ssh:22",
        "container": {
            "image": "submit-image-name",
            "command": "submit-command",
            "resources": {
                "cpu": 1.0,
                "memory_mb": 16384,
                "gpu": 1,
                "gpu_model": "nvidia-tesla-v100",
            },
        },
        "is_preemptible": True,
        "owner": "owner",
    }
    if name:
        result["name"] = name
    return result


async def test_list_no_filter(
    aiohttp_server: _TestServerFactory, make_client: _MakeClient
) -> None:
    jobs = [
        create_job_response("job-id-1", "pending", name="job-name-1"),
        create_job_response("job-id-2", "running", name="job-name-1"),
        create_job_response("job-id-3", "succeeded", name="job-name-1"),
        create_job_response("job-id-4", "failed", name="job-name-1"),
    ]
    JSON = {"jobs": jobs}

    async def handler(request: web.Request) -> web.Response:
        return web.json_response(JSON)

    app = web.Application()
    app.router.add_get("/jobs", handler)
    srv = await aiohttp_server(app)

    async with make_client(srv.make_url("/")) as client:
        ret = await client.jobs.list()

    job_descriptions = [JobDescription.from_api(job) for job in jobs]
    assert ret == job_descriptions


async def test_list_filter_by_name(
    aiohttp_server: _TestServerFactory, make_client: _MakeClient
) -> None:
    name_1 = "job-name-1"
    name_2 = "job-name-2"
    jobs = [
        create_job_response("job-id-1", "pending", name=name_1),
        create_job_response("job-id-2", "succeeded", name=name_1),
        create_job_response("job-id-3", "failed", name=name_1),
        create_job_response("job-id-4", "running", name=name_2),
        create_job_response("job-id-5", "succeeded", name=name_2),
        create_job_response("job-id-6", "failed", name=name_2),
        create_job_response("job-id-7", "running"),
        create_job_response("job-id-8", "pending"),
        create_job_response("job-id-9", "succeeded"),
        create_job_response("job-id-10", "failed"),
    ]

    async def handler(request: web.Request) -> web.Response:
        name = request.query.get("name")
        assert name
        filtered_jobs = [job for job in jobs if job.get("name") == name]
        JSON = {"jobs": filtered_jobs}
        return web.json_response(JSON)

    app = web.Application()
    app.router.add_get("/jobs", handler)
    srv = await aiohttp_server(app)

    async with make_client(srv.make_url("/")) as client:
        ret = await client.jobs.list(name=name_1)

    job_descriptions = [JobDescription.from_api(job) for job in jobs]
    assert ret == job_descriptions[:3]


async def test_list_filter_by_statuses(
    aiohttp_server: _TestServerFactory, make_client: _MakeClient
) -> None:
    name_1 = "job-name-1"
    name_2 = "job-name-2"
    jobs = [
        create_job_response("job-id-1", "pending", name=name_1),
        create_job_response("job-id-2", "succeeded", name=name_1),
        create_job_response("job-id-3", "failed", name=name_1),
        create_job_response("job-id-4", "running", name=name_2),
        create_job_response("job-id-5", "succeeded", name=name_2),
        create_job_response("job-id-6", "failed", name=name_2),
        create_job_response("job-id-7", "running"),
        create_job_response("job-id-8", "pending"),
        create_job_response("job-id-9", "succeeded"),
        create_job_response("job-id-10", "failed"),
    ]

    async def handler(request: web.Request) -> web.Response:
        statuses = request.query.getall("status")
        assert statuses
        filtered_jobs = [job for job in jobs if job["status"] in statuses]
        JSON = {"jobs": filtered_jobs}
        return web.json_response(JSON)

    app = web.Application()
    app.router.add_get("/jobs", handler)
    srv = await aiohttp_server(app)

    statuses = {JobStatus.FAILED, JobStatus.SUCCEEDED}
    async with make_client(srv.make_url("/")) as client:
        ret = await client.jobs.list(statuses=statuses)

    job_descriptions = [JobDescription.from_api(job) for job in jobs]
    assert ret == [job for job in job_descriptions if job.status in statuses]


class TestVolumeParsing:
    @pytest.mark.parametrize(
        "volume_param", ["dir", "storage://dir", "storage://dir:/var/www:rw:ro"]
    )
    def test_incorrect_params_count(self, volume_param: str) -> None:
        with pytest.raises(ValueError, match=r"Invalid volume specification"):
            Volume.from_cli("bob", volume_param)

    @pytest.mark.parametrize(
        "volume_param", ["storage://dir:/var/www:write", "storage://dir:/var/www:"]
    )
    def test_incorrect_mode(self, volume_param: str) -> None:
        with pytest.raises(ValueError, match=r"Wrong ReadWrite/ReadOnly mode spec"):
            Volume.from_cli("bob", volume_param)

    @pytest.mark.parametrize(
        "volume_param,volume",
        [
            (
                "storage://bob/dir:/var/www",
                Volume(
                    storage_path="storage://bob/dir",
                    container_path="/var/www",
                    read_only=False,
                ),
            ),
            (
                "storage://bob/dir:/var/www:rw",
                Volume(
                    storage_path="storage://bob/dir",
                    container_path="/var/www",
                    read_only=False,
                ),
            ),
            (
                "storage://bob:/var/www:ro",
                Volume(
                    storage_path="storage://bob",
                    container_path="/var/www",
                    read_only=True,
                ),
            ),
            (
                "storage://~/:/var/www:ro",
                Volume(
                    storage_path="storage://bob",
                    container_path="/var/www",
                    read_only=True,
                ),
            ),
            (
                "storage:dir:/var/www:ro",
                Volume(
                    storage_path="storage://bob/dir",
                    container_path="/var/www",
                    read_only=True,
                ),
            ),
            (
                "storage::/var/www:ro",
                Volume(
                    storage_path="storage://bob",
                    container_path="/var/www",
                    read_only=True,
                ),
            ),
        ],
    )
    def test_positive(self, volume_param: str, volume: Volume) -> None:
        assert Volume.from_cli("bob", volume_param) == volume


async def test_list_filter_by_name_and_statuses(
    aiohttp_server: _TestServerFactory, make_client: _MakeClient
) -> None:
    name_1 = "job-name-1"
    name_2 = "job-name-2"
    jobs = [
        create_job_response("job-id-1", "pending", name=name_1),
        create_job_response("job-id-2", "succeeded", name=name_1),
        create_job_response("job-id-3", "failed", name=name_1),
        create_job_response("job-id-4", "running", name=name_2),
        create_job_response("job-id-5", "succeeded", name=name_2),
        create_job_response("job-id-6", "failed", name=name_2),
        create_job_response("job-id-7", "running"),
        create_job_response("job-id-8", "pending"),
        create_job_response("job-id-9", "succeeded"),
        create_job_response("job-id-10", "failed"),
    ]

    async def handler(request: web.Request) -> web.Response:
        statuses = request.query.getall("status")
        assert statuses
        name = request.query.get("name")
        assert name
        filtered_jobs = [
            job for job in jobs if job["status"] in statuses and job.get("name") == name
        ]
        JSON = {"jobs": filtered_jobs}
        return web.json_response(JSON)

    app = web.Application()
    app.router.add_get("/jobs", handler)
    srv = await aiohttp_server(app)

    statuses = {JobStatus.PENDING, JobStatus.SUCCEEDED}
    name = "job-name-1"
    async with make_client(srv.make_url("/")) as client:
        ret = await client.jobs.list(statuses=statuses, name=name)

    job_descriptions = [JobDescription.from_api(job) for job in jobs]
    assert ret == job_descriptions[:2]
