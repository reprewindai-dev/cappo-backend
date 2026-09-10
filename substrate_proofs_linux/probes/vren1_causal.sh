#!/usr/bin/env bash
# vren1_causal.sh — VRE-N1 causal control/enforce/recover sandwich
# Runs entirely in a new network namespace on loopback.
# Target: 127.0.0.1:9090 (local TCP echo server)
# No external routing required.
set -euo pipefail

NS="veklom_n1_causal"
ADDR="127.0.0.1"
PORT=9090
RESULT_FILE="/tmp/vren1_causal_result.json"

cleanup() {
    ip netns del "$NS" 2>/dev/null || true
}
trap cleanup EXIT

# ── Create namespace ─────────────────────────────────────────────────────────
ip netns add "$NS"
ip netns exec "$NS" ip link set lo up

# Capture netns identity
NS_STAT=$(stat -L /run/netns/"$NS" --printf='{"st_dev":%d,"st_ino":%i}')

# ── Start a TCP listener inside the namespace ────────────────────────────────
# nc -l listens once; we restart it each time with a small Python server
ip netns exec "$NS" python3 -c "
import socket,threading,time,os

s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('$ADDR', $PORT))
s.listen(10)
s.settimeout(15)
def serve():
    while True:
        try:
            c,_ = s.accept()
            c.sendall(b'OK')
            c.close()
        except Exception:
            break
t = threading.Thread(target=serve, daemon=True)
t.start()
time.sleep(12)  # keep alive for the full test
" &
SERVER_PID=$!
sleep 0.4  # let server bind

# ── Helper: attempt connection inside NS ─────────────────────────────────────
try_connect() {
    ip netns exec "$NS" python3 -c "
import socket,sys
try:
    s=socket.socket()
    s.settimeout(2)
    s.connect(('$ADDR',$PORT))
    d=s.recv(4)
    s.close()
    print('OK' if d==b'OK' else 'BAD_RESPONSE')
except Exception as e:
    print('BLOCKED:'+str(type(e).__name__))
" 2>/dev/null
}

TS_CONTROL=$(date -u +%Y-%m-%dT%H:%M:%SZ)
STEP1=$(try_connect)

# ── Install DROP rule ────────────────────────────────────────────────────────
ip netns exec "$NS" nft add table inet veklom_filter
ip netns exec "$NS" nft add chain inet veklom_filter output \
    '{ type filter hook output priority 0 ; }'
ip netns exec "$NS" nft add rule inet veklom_filter output \
    ip daddr "$ADDR" tcp dport "$PORT" drop

# Capture rule handle and ruleset
RULESET=$(ip netns exec "$NS" nft list ruleset)
RULE_HANDLE=$(ip netns exec "$NS" nft list ruleset -a 2>/dev/null | grep 'handle' | tail -1 | grep -o 'handle [0-9]*' | head -1 || true)

TS_ENFORCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
STEP4=$(try_connect)

# ── Teardown: flush table ────────────────────────────────────────────────────
ip netns exec "$NS" nft flush table inet veklom_filter
ip netns exec "$NS" nft delete table inet veklom_filter
RULESET_AFTER=$(ip netns exec "$NS" nft list ruleset)

TS_RECOVER=$(date -u +%Y-%m-%dT%H:%M:%SZ)
STEP6=$(try_connect)

# Kill server
kill "$SERVER_PID" 2>/dev/null || true

# ── Evaluate ─────────────────────────────────────────────────────────────────
CONTROL_OK=$([ "$STEP1" = "OK" ] && echo "True" || echo "False")
ENFORCE_BLOCKED=$(echo "$STEP4" | grep -q "BLOCKED" && echo "True" || echo "False")
RECOVER_OK=$([ "$STEP6" = "OK" ] && echo "True" || echo "False")
RULES_GONE=$([ -z "$RULESET_AFTER" ] && echo "True" || echo "False")

VERDICT="INVALID"
if [ "$CONTROL_OK" = "True" ] && [ "$ENFORCE_BLOCKED" = "True" ] && \
   [ "$RECOVER_OK" = "True" ] && [ "$RULES_GONE" = "True" ]; then
    VERDICT="VALID"
fi

# ── Emit JSON receipt ─────────────────────────────────────────────────────────
python3 -c "
import json,sys
r = {
  'proof': 'VRE-N1',
  'verdict': '$VERDICT',
  'target': '$ADDR:$PORT',
  'netns_identity': $NS_STAT,
  'rule_handle': '$RULE_HANDLE',
  'sequence': {
    'step1_control_before_rule': {
      'timestamp': '$TS_CONTROL',
      'result': '$STEP1',
      'succeeded': $CONTROL_OK
    },
    'step2_rule_installed': True,
    'step3_ruleset_readback': '''$RULESET'''.strip(),
    'step4_connection_after_rule': {
      'timestamp': '$TS_ENFORCE',
      'result': '$STEP4',
      'blocked': $ENFORCE_BLOCKED
    },
    'step5_rule_flushed': True,
    'step6_recovery_after_flush': {
      'timestamp': '$TS_RECOVER',
      'result': '$STEP6',
      'succeeded': $RECOVER_OK
    }
  },
  'rules_gone_after_flush': $RULES_GONE,
  'causal_chain_complete': ($CONTROL_OK is True and $ENFORCE_BLOCKED is True and $RECOVER_OK is True)
}
print(json.dumps(r, indent=2))
" 2>&1
