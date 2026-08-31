import shutil
import subprocess

import pytest


def _coolify_probe_prerequisites_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "network", "inspect", "coolify"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


pytestmark = pytest.mark.skipif(
    not _coolify_probe_prerequisites_available(),
    reason="requires a Docker host with the production-like Coolify network",
)


def run_hostile_probe(cmd: str):
    """Run a hostile workload probe on the production-like Coolify substrate."""
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "coolify",
            "alpine",
            "sh",
            "-c",
            cmd,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_container_cannot_read_cappo_env():
    res = run_hostile_probe("env | grep BISCUIT_ROOT_PRIVATE_KEY_HEX || true")
    assert "BISCUIT_ROOT_PRIVATE_KEY_HEX" not in res.stdout
    assert "BISCUIT_ROOT_PRIVATE_KEY_HEX" not in res.stderr


def test_container_cannot_access_docker_sock():
    res = run_hostile_probe("ls -l /var/run/docker.sock || echo 'denied'")
    assert "denied" in res.stdout or "No such file or directory" in res.stderr


def test_container_cannot_access_cappo_filesystem():
    res = run_hostile_probe("cat /.biscuit_root_key || echo 'denied'")
    assert "denied" in res.stdout or "No such file or directory" in res.stderr


def test_container_cannot_access_spire_socket():
    res = run_hostile_probe("ls -l /run/spire/sockets/agent.sock || echo 'denied'")
    assert "denied" in res.stdout or "No such file or directory" in res.stderr


def test_container_cannot_access_cappo_memory():
    res = run_hostile_probe("ps aux | grep uvicorn | grep -v grep || echo 'isolated'")
    assert "isolated" in res.stdout or res.stdout.strip() == ""


def test_container_cannot_connect_to_postgres():
    cmd = (
        "apk add --no-cache postgresql-client >/dev/null 2>&1 && "
        "psql -h lockerphycer-lockerphycer-postgres-1 -U postgres -c '\\l' "
        "|| echo 'connection_failed'"
    )
    res = run_hostile_probe(cmd)
    assert (
        "connection_failed" in res.stdout
        or "password authentication failed" in res.stderr
        or "could not translate host name" in res.stderr
    )
