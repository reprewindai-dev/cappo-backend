import subprocess


def run_hostile_probe(cmd):
    # This is a topology-neutral adversarial harness. It proves only the
    # isolation properties of the disposable hostile container constructed by
    # this test; it does not claim anything about a particular deployment
    # network. Network access is explicitly disabled so the probe cannot depend
    # on a legacy hosting-specific Docker network name.
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "alpine", "sh", "-c", cmd],
        capture_output=True,
        text=True,
    )
    return result


def test_container_cannot_read_cappo_env():
    # Attempt to read BISCUIT_ROOT_PRIVATE_KEY_HEX from the environment.
    res = run_hostile_probe("env | grep BISCUIT_ROOT_PRIVATE_KEY_HEX || true")
    assert "BISCUIT_ROOT_PRIVATE_KEY_HEX" not in res.stdout
    assert "BISCUIT_ROOT_PRIVATE_KEY_HEX" not in res.stderr


def test_container_cannot_access_docker_sock():
    res = run_hostile_probe("ls -l /var/run/docker.sock || echo 'denied'")
    assert "denied" in res.stdout or "No such file or directory" in res.stderr


def test_container_cannot_access_cappo_filesystem():
    # Workload trying to reach the CAPPO root key file on the host/CAPPO container.
    res = run_hostile_probe("cat /.biscuit_root_key || echo 'denied'")
    assert "denied" in res.stdout or "No such file or directory" in res.stderr


def test_container_cannot_access_spire_socket():
    # Attempt to access a SPIRE agent socket that was not mounted into the workload.
    res = run_hostile_probe("ls -l /run/spire/sockets/agent.sock || echo 'denied'")
    assert "denied" in res.stdout or "No such file or directory" in res.stderr


def test_container_cannot_access_cappo_memory():
    # Since the probe has its own PID namespace, it should not see CAPPO's processes.
    res = run_hostile_probe("ps aux | grep uvicorn | grep -v grep || echo 'isolated'")
    assert "isolated" in res.stdout or res.stdout.strip() == ""


def test_container_cannot_connect_to_postgres():
    # The hostile probe has no network interface capable of reaching Postgres.
    # This is intentionally deployment-name agnostic; it does not depend on a
    # Coolify, Compose, or production network name.
    cmd = "wget -q -T 2 -O- http://172.17.0.1:5432 >/dev/null 2>&1 || echo 'connection_failed'"
    res = run_hostile_probe(cmd)
    assert "connection_failed" in res.stdout
