import subprocess
import sys
import time


def run(cmd, timeout=30):
    print(f"> {cmd}")
    try:
        return subprocess.check_output(cmd, shell=True, timeout=timeout).decode().strip()
    except subprocess.TimeoutExpired:
        print(f"Command timed out: {cmd}")
        return ""
    except Exception as e:
        print(f"Command failed: {e}")
        return ""

print("Checking docker status...")
docker_ps = run("docker ps", timeout=10)
if not docker_ps:
    print("Docker is not responding. Falling back to local process boundary test.")
    sys.exit(1)
else:
    print("Docker is responding. Proceeding with Docker boundary test.")
    sys.exit(0)
