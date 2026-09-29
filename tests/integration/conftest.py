import os
import socket
import subprocess
import sys
import time
from pathlib import Path
import httpx
import pytest
from pymongo import MongoClient
from gax.credentials import LocalOnlyCredentialBroker

ROOT = Path(__file__).resolve().parents[2]
MONGO_URI = "mongodb://localhost:27017/?directConnection=true"
TEST_DB = "gax_test"
TEST_KEY = "integration-test-local-only-signing-key-0123456789"


def pytest_collection_modifyitems(items):
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def mongo():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    client.admin.command("ping")
    yield client[TEST_DB]
    client.drop_database(TEST_DB)
    client.close()


@pytest.fixture(scope="session")
def fleet_url(mongo, tmp_path_factory):
    port = free_port()
    env = {**os.environ, "LOCAL_BROKER_SIGNING_KEY": TEST_KEY, "GAX_DB": TEST_DB, "MONGO_URI": MONGO_URI}
    log = open(tmp_path_factory.mktemp("fleet") / "fleet-api.log", "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "fleet_api.app:create_app", "--factory", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"fleet-api exited with {proc.returncode}, see {log.name}")
            try:
                if httpx.get(f"{url}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.time() > deadline:
                raise TimeoutError("fleet-api did not become healthy")
            time.sleep(0.2)
        yield url
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        proc.wait(timeout=10)
        log.close()


@pytest.fixture
def fleet(fleet_url):
    with httpx.Client(base_url=fleet_url, timeout=10) as client:
        assert client.post("/admin/reset").status_code == 200
        yield client


@pytest.fixture
def broker():
    return LocalOnlyCredentialBroker(TEST_KEY)


@pytest.fixture
def auth(broker):
    def make(action, target="orders-consumer", environment="staging", workflow_id="INC-1", attempt=1):
        return {"Authorization": f"Bearer {broker.issue(action, target, environment, workflow_id, attempt).token}"}
    return make
