"""Fixtures for the run infrastructure: a throwaway Postgres cluster, object stores and the Temporal dev server.

Postgres: a session cluster is initialised with ``initdb`` on a free port (as the ``postgres`` user
when the tests run as root, since Postgres refuses to run as root), migrated once into a template
database, and every test gets its own database cloned from the template. Set
``ADVISOR_INTEL_TEST_DATABASE_URL`` to use an existing server instead (CI service container).

Temporal: tests start ``temporal server start-dev`` through ``WorkflowEnvironment.start_local``
with the binary from ``TEMPORAL_CLI_PATH`` or ``pipeline/.tools/temporal``.
"""

from __future__ import annotations

import asyncio
import glob
import os
import pwd
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest

PIPELINE_DIR = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- Postgres


def _pg_bin_dir() -> Path | None:
    env = os.environ.get("ADVISOR_INTEL_PG_BIN")
    if env and (Path(env) / "initdb").exists():
        return Path(env)
    found = shutil.which("initdb")
    if found:
        return Path(found).parent
    for cand in sorted(glob.glob("/usr/lib/postgresql/*/bin/initdb"), reverse=True):
        return Path(cand).parent
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class PgCluster:
    admin_dsn: str  # connects to the 'postgres' database
    template: str

    def dsn(self, dbname: str) -> str:
        return self.admin_dsn.rsplit("/", 1)[0] + f"/{dbname}"


def _admin(dsn: str, sql: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql)


@pytest.fixture(scope="session")
def pg_cluster():
    from advisor_intel.storage.migrate import migrate

    external = os.environ.get("ADVISOR_INTEL_TEST_DATABASE_URL")
    proc_dir: str | None = None
    prefix: list[str] = []
    bin_dir = None
    data_dir = None
    if external:
        admin = external
    else:
        bin_dir = _pg_bin_dir()
        if bin_dir is None:
            pytest.skip("PostgreSQL binaries (initdb, pg_ctl) not found; set ADVISOR_INTEL_PG_BIN or ADVISOR_INTEL_TEST_DATABASE_URL")
        proc_dir = tempfile.mkdtemp(prefix="advisor-intel-pg-", dir="/tmp")
        if os.geteuid() == 0:
            try:
                pw = pwd.getpwnam("postgres")
            except KeyError:
                pytest.skip("running as root and there is no 'postgres' user to run the test cluster as")
            os.chown(proc_dir, pw.pw_uid, pw.pw_gid)
            runner = shutil.which("runuser") or shutil.which("su")
            if runner is None:
                pytest.skip("running as root without runuser/su to drop privileges for Postgres")
            prefix = [runner, "-u", "postgres", "--"] if runner.endswith("runuser") else []
        data_dir = os.path.join(proc_dir, "data")
        port = _free_port()
        subprocess.run(
            [*prefix, str(bin_dir / "initdb"), "-D", data_dir, "-U", "postgres", "--auth=trust", "-E", "UTF8", "--no-locale"],
            check=True,
            capture_output=True,
        )
        opts = f"-p {port} -k {proc_dir} -c listen_addresses=127.0.0.1 -c fsync=off -c synchronous_commit=off -c full_page_writes=off"
        subprocess.run(
            [*prefix, str(bin_dir / "pg_ctl"), "-D", data_dir, "-l", os.path.join(proc_dir, "server.log"), "-o", opts, "-w", "start"],
            check=True,
            capture_output=True,
        )
        admin = f"postgresql://postgres@127.0.0.1:{port}/postgres"
    template = f"advisor_intel_tmpl_{uuid.uuid4().hex[:8]}"
    _admin(admin, f'CREATE DATABASE "{template}"')
    cluster = PgCluster(admin_dsn=admin, template=template)
    asyncio.run(migrate(cluster.dsn(template)))
    try:
        yield cluster
    finally:
        try:
            _admin(admin, f'DROP DATABASE IF EXISTS "{template}" WITH (FORCE)')
        except psycopg.Error:
            pass
        if bin_dir is not None and data_dir is not None:
            subprocess.run(
                [*prefix, str(bin_dir / "pg_ctl"), "-D", data_dir, "-m", "immediate", "-w", "stop"], capture_output=True, check=False
            )
            shutil.rmtree(proc_dir, ignore_errors=True)


@pytest.fixture
def pg_dsn(pg_cluster: PgCluster):
    """A fresh, migrated database for one test."""
    name = f"t_{uuid.uuid4().hex[:12]}"
    for attempt in range(20):
        try:
            _admin(pg_cluster.admin_dsn, f'CREATE DATABASE "{name}" TEMPLATE "{pg_cluster.template}"')
            break
        except psycopg.errors.ObjectInUse:  # the template is briefly busy while another clone runs
            time.sleep(0.05 * (attempt + 1))
    yield pg_cluster.dsn(name)
    try:
        _admin(pg_cluster.admin_dsn, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    except psycopg.Error:
        pass


@pytest.fixture
def empty_pg_dsn(pg_cluster: PgCluster):
    """A fresh database with no migrations applied."""
    name = f"e_{uuid.uuid4().hex[:12]}"
    _admin(pg_cluster.admin_dsn, f'CREATE DATABASE "{name}"')
    yield pg_cluster.dsn(name)
    try:
        _admin(pg_cluster.admin_dsn, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    except psycopg.Error:
        pass


@pytest.fixture
async def store(pg_dsn):
    from advisor_intel.storage import Store

    s = Store.from_dsn(pg_dsn)
    yield s
    await s.close()


@pytest.fixture
def objects(tmp_path):
    from advisor_intel.storage import LocalObjectStore

    return LocalObjectStore(tmp_path / "objects")


# --------------------------------------------------------------------------- Temporal


def temporal_cli_path() -> str | None:
    for cand in (
        os.environ.get("TEMPORAL_CLI_PATH"),
        str(PIPELINE_DIR / ".tools" / "temporal"),
        "/home/user/shahil_fna/pipeline/.tools/temporal",
    ):
        if cand and Path(cand).is_file() and os.access(cand, os.X_OK):
            return cand
    return shutil.which("temporal")


@pytest.fixture
def temporal_bin() -> str:
    path = temporal_cli_path()
    if path is None:
        pytest.skip("Temporal CLI dev server binary not found; set TEMPORAL_CLI_PATH")
    return path
