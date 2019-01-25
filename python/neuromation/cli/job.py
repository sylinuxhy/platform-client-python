import logging
import os
import shlex
import sys
from typing import Sequence

import aiohttp
import click

from neuromation.clientv2 import Image, NetworkPortForwarding, Resources, Volume
from neuromation.strings.parse import to_megabytes_str

from . import rc
from .defaults import DEFAULTS, GPU_MODELS
from .formatter import JobListFormatter, JobStatusFormatter, OutputFormatter
from .ssh_utils import connect_ssh
from .utils import Context, run_async


log = logging.getLogger(__name__)


@click.group()
def job() -> None:
    """
    Job operations.
    """


@job.command()
@click.argument("image")
@click.argument("cmd", nargs=-1)
@click.option(
    "-g",
    "--gpu",
    metavar="NUMBER",
    type=float,
    help="Number of GPUs to request",
    default=DEFAULTS["model_train_gpu_number"],
    show_default=True,
)
@click.option(
    "--gpu-model",
    metavar="MODEL",
    type=click.Choice(GPU_MODELS),
    help="GPU to use",
    default=DEFAULTS["model_train_gpu_model"],
    show_default=True,
)
@click.option(
    "-c",
    "--cpu",
    metavar="NUMBER",
    type=float,
    help="Number of CPUs to request",
    default=DEFAULTS["model_train_cpu_number"],
    show_default=True,
)
@click.option(
    "-m",
    "--memory",
    metavar="AMOUNT",
    type=str,
    help="Memory amount to request",
    default=DEFAULTS["model_train_memory_amount"],
    show_default=True,
)
@click.option("-x", "--extshm", is_flag=True, help="Request extended '/dev/shm' space")
@click.option("--http", type=int, help="Enable HTTP port forwarding to container")
@click.option("--ssh", type=int, help="Enable SSH port forwarding to container")
@click.option(
    "--preemptible/--non-preemptible",
    help="Run job on a lower-cost preemptible instance",
    default=True,
)
@click.option(
    "-d", "--description", metavar="DESC", help="Add optional description to the job"
)
@click.option(
    "-q", "--quiet", is_flag=True, help="Run command in quiet mode (print only job id)"
)
@click.option(
    "--volume",
    metavar="MOUNT",
    multiple=True,
    help="Mounts directory from vault into container. "
    "Use multiple options to mount more than one volume",
)
@click.option(
    "-e",
    "--env",
    metavar="VAR=VAL",
    multiple=True,
    help="Set environment variable in container "
    "Use multiple options to define more than one variable",
)
@click.option(
    "--env-file",
    type=click.Path(exists=True),
    help="File with environment variables to pass",
)
@click.pass_obj
@run_async
async def submit(
    ctx: Context,
    image: str,
    gpu: int,
    gpu_model: str,
    cpu: int,
    memory: str,
    extshm: bool,
    http: int,
    ssh: int,
    cmd: Sequence[str],
    volume: Sequence[str],
    env: Sequence[str],
    env_file: str,
    preemptible: bool,
    description: str,
    quiet: bool,
):
    """
    Start job using IMAGE.

    COMMANDS list will be passed as commands to model container.

    Examples:

    \b
    # Starts a container pytorch:latest with two paths mounted. Directory /q1/
    # is mounted in read only mode to /qm directory within container.
    # Directory /mod mounted to /mod directory in read-write mode.
    neuro job submit --volume storage:/q1:/qm:ro --volume storage:/mod:/mod:rw \
    pytorch:latest

    \b
    # Starts a container pytorch:latest with connection enabled to port 22 and
    # sets PYTHONPATH environment value to /python.
    # Please note that SSH server should be provided by container.
    neuro job submit --env PYTHONPATH=/python --volume \
    storage:/data/2018q1:/data:ro --ssh 22 pytorch:latest
    """

    config = rc.ConfigFactory.load()
    username = config.get_platform_user_name()

    # TODO (Alex Davydow 12.12.2018): Consider splitting env logic into
    # separate function.
    if env_file:
        with open(env_file, "r") as ef:
            env = ef.read().splitlines() + list(env)

    env_dict = {}
    for line in env:
        splited = line.split("=", 1)
        if len(splited) == 1:
            val = os.environ.get(splited[0], "")
            env_dict[splited[0]] = val
        else:
            env_dict[splited[0]] = splited[1]

    cmd = " ".join(cmd) if cmd is not None else None
    log.debug(f'cmd="{cmd}"')

    memory = to_megabytes_str(memory)
    image = Image(image=image, command=cmd)
    network = NetworkPortForwarding.from_cli(http, ssh)
    resources = Resources.create(cpu, gpu, gpu_model, memory, extshm)
    volumes = Volume.from_cli_list(username, volume)

    async with ctx.make_client() as client:
        job = await client.jobs.submit(
            image=image,
            resources=resources,
            network=network,
            volumes=volumes,
            is_preemptible=preemptible,
            description=description,
            env=env_dict,
        )
        click.echo(OutputFormatter.format_job(job, quiet))


@job.command()
@click.argument("id")
@click.argument("cmd", nargs=-1)
@click.option(
    "-t",
    "--tty",
    is_flag=True,
    help="Allocate virtual tty. Useful for interactive jobs.",
)
@click.option(
    "--no-key-check",
    is_flag=True,
    help="Disable host key checks. Should be used with caution.",
)
@click.pass_obj
@run_async
async def exec(
    ctx: Context, id: str, tty: bool, no_key_check: bool, cmd: Sequence[str]
) -> None:
    """
    Executes command in a running job.
    """
    cmd = shlex.split(" ".join(cmd))
    async with ctx.make_client() as client:
        retcode = await client.jobs.exec(id, tty, no_key_check, cmd)
    sys.exit(retcode)


@job.command()
@click.argument("id")
@click.option(
    "--user",
    help="Container user name",
    default=DEFAULTS["job_ssh_user"],
    show_default=True,
)
@click.option("--key", help="Path to container private key.")
@click.pass_obj
@run_async
async def ssh(ctx: Context, id: str, user: str, key: str) -> None:
    """
    Starts ssh terminal connected to running job.
    Job should be started with SSH support enabled.

    Examples:

    \b
    neuro job ssh --user alfa --key ./my_docker_id_rsa job-abc-def-ghk
    """
    config = rc.ConfigFactory.load()
    git_key = config.github_rsa_path

    async with ctx.make_client() as client:
        await connect_ssh(client, id, git_key, user, key)


@job.command()
@click.argument("id")
@click.pass_obj
@run_async
async def monitor(ctx: Context, id: str) -> None:
    """
    Monitor job output stream
    """
    timeout = aiohttp.ClientTimeout(
        total=None, connect=None, sock_read=None, sock_connect=30
    )

    async with ctx.make_client(timeout=timeout) as client:
        async for chunk in client.jobs.monitor(id):
            if not chunk:
                break
            click.echo(chunk.decode(errors="ignore"), nl=False)


@job.command()
@click.option(
    "-s",
    "--status",
    multiple=True,
    type=click.Choice(["pending", "running", "succeeded", "failed", "all"]),
    help="Filter out job by status (multiple option)",
)
@click.option(
    "-d",
    "--description",
    metavar="DESCRIPTION",
    help="Filter out job by job description (exact match)",
)
@click.option("-q", "--quiet", is_flag=True)
@click.pass_obj
@run_async
async def list(
    ctx: Context, status: Sequence[str], description: str, quiet: bool
) -> None:
    """
    List all jobs.

    Examples:

    \b
    neuro job list --description="my favourite job"
    neuro job list --status=all
    neuro job list -s pending -s running -q
    """

    status = status or ["running", "pending"]

    # TODO: add validation of status values
    statuses = set(status)
    if "all" in statuses:
        statuses = set()

    async with ctx.make_client() as client:
        jobs = await client.jobs.list()

    formatter = JobListFormatter(quiet=quiet)
    click.echo(formatter.format_jobs(jobs, statuses, description))


@job.command()
@click.argument("id")
@click.pass_obj
@run_async
async def status(ctx: Context, id: str) -> None:
    """
    Display status of a job
    """
    async with ctx.make_client() as client:
        res = await client.jobs.status(id)
        click.echo(JobStatusFormatter.format_job_status(res))


@job.command()
@click.argument("id", nargs=-1, required=True)
@click.pass_obj
@run_async
async def kill(ctx: Context, id: Sequence[str]):
    """
    Kill job(s)
    """
    errors = []
    async with ctx.make_client() as client:
        for job in id:
            try:
                await client.jobs.kill(job)
                print(job)
            except ValueError as e:
                errors.append((job, e))

    def format_fail(job: str, reason: Exception) -> str:
        return f"Cannot kill job {job}: {reason}"

    for job, error in errors:
        click.echo(format_fail(job, error))