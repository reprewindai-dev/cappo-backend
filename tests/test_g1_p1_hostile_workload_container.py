import pytest
import subprocess
import json

def run_hostile_probe(cmd):
    # Run a command inside an alpine container on the coolify network
    # simulating an external governed workload
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "coolify", "alpine", "sh", "-c", cmd],
        capture_output=True,
        text=True
    )
    return result

def test_container_cannot_read_cappo_env():
    # Attempt to read BISCUIT_ROOT_PRIVATE_KEY_HEX from the environment
    res = run_hostile_probe("env | grep BISCUIT_ROOT_PRIVATE_KEY_HEX || true")
    assert "BISCUIT_ROOT_PRIVATE_KEY_HEX" not in res.stdout
    assert "BISCUIT_ROOT_PRIVATE_KEY_HEX" not in res.stderr

def test_container_cannot_access_docker_sock():
    res = run_hostile_probe("ls -l /var/run/docker.sock || echo 'denied'")
    assert "denied" in res.stdout or "No such file or directory" in res.stderr

def test_container_cannot_access_cappo_filesystem():
    # Workload trying to reach the CAPPO root key file on the host/cappo container
    res = run_hostile_probe("cat /.biscuit_root_key || echo 'denied'")
    assert "denied" in res.stdout or "No such file or directory" in res.stderr

def test_container_cannot_access_spire_socket():
    # Attempt to access SPIRE agent socket
    res = run_hostile_probe("ls -l /run/spire/sockets/agent.sock || echo 'denied'")
    assert "denied" in res.stdout or "No such file or directory" in res.stderr

def test_container_cannot_access_cappo_memory():
    # Attempt to inspect proc memory of other containers
    # (Since it's in its own PID namespace, it shouldn't see cappo-backend's processes)
    res = run_hostile_probe("ps aux | grep uvicorn | grep -v grep || echo 'isolated'")
    assert "isolated" in res.stdout or res.stdout.strip() == ""

def test_container_cannot_connect_to_postgres():
    # Attempt to connect to the postgres database directly
    # Need to install psql in the alpine container
    cmd = r"apk add --no-cache postgresql-client >/dev/null 2>&1 && psql -h lockerphycer-lockerphycer-postgres-1 -U postgres -c '\l' || echo 'connection_failed'"
    res = run_hostile_probe(cmd)
    # The workload container shouldn't have the password, or network policy might block it.
    # Note: The port may be reachable (no network block), but authentication is correctly denied.
    assert "connection_failed" in res.stdout or "password authentication failed" in res.stderr or "could not translate host name" in res.stderr
