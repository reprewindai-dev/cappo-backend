#!/bin/bash
set -e

echo "Downloading SPIRE 1.9.0..."
curl -sL https://github.com/spiffe/spire/releases/download/v1.9.0/spire-1.9.0-linux-amd64-musl.tar.gz | tar xz
export PATH=$PATH:$(pwd)/spire-1.9.0/bin

mkdir -p /opt/spire/data
mkdir -p /tmp/spire-sockets

echo "Starting SPIRE Server..."
spire-server run -config scripts/spire/server.conf > spire-server.log 2>&1 &
SERVER_PID=$!

echo "Waiting for SPIRE Server to start..."
sleep 5

echo "Generating Join Token for Agent..."
TOKEN_OUTPUT=$(spire-server token generate -socketPath /opt/spire/data/api.sock -spiffeID spiffe://example.org/agent)
echo "$TOKEN_OUTPUT"
TOKEN=$(echo "$TOKEN_OUTPUT" | grep Token | awk '{print $2}')
echo "Token: $TOKEN"

echo "Starting SPIRE Agent..."
spire-agent run -config scripts/spire/agent.conf -joinToken $TOKEN > spire-agent.log 2>&1 &
AGENT_PID=$!
sleep 5

echo "Registering Workload..."
# We register a workload for any process running as root (uid 0) since we run the script as root in the container.
spire-server entry create \
    -socketPath /opt/spire/data/api.sock \
    -spiffeID spiffe://example.org/workload/cappo-backend \
    -parentID spiffe://example.org/agent \
    -selector unix:uid:0

echo "Running Workload Test..."
export SPIFFE_ENDPOINT_SOCKET=unix:///tmp/spire-sockets/workload_api.sock
pytest tests/test_g0b1_spiffe.py -v -s || {
    echo "Test failed! Spire Agent Logs:"
    cat spire-agent.log
    exit 1
}

echo "Cleaning up..."
kill $AGENT_PID
kill $SERVER_PID
