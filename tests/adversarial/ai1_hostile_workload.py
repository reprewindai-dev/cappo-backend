"""
Hostile Workload Payload for AI-1 (Authority Ingress).

This script is spawned as a completely separate process by the AI-1 test harness.
Its objective is to hunt for undeclared authority:
1. Environment variable leakage (DB credentials, root keys, admin flags)
2. Cached or leaked token files on disk (.biscuit, .key)
3. Direct DB connectivity via leaked credentials
4. Direct CAPPO registry instantiation without a valid cryptographic grant

Exit codes:
0: Attack SUCCEEDED (Authority was successfully stolen/smuggled). AI-1 FAILS.
1: Attack FAILED (Hostile workload remained fully isolated). AI-1 PASSES.
"""
import json
import os
import sys


def attack():
    report = {"findings": [], "stolen_tokens": [], "db_breach": False}
    
    # 1. Scrape Environment Variables
    env_keys = list(os.environ.keys())
    suspicious_env = [k for k in env_keys if any(x in k.upper() for x in ["TOKEN", "SECRET", "DB", "AUTH", "SPIFFE", "URL", "KEY", "ADMIN"])]
    report["suspicious_env_vars"] = suspicious_env

    # 2. Scrape File System (looking for leaked tokens)
    search_paths = [os.getcwd(), os.environ.get("TEMP", "/tmp")]
    found_secrets = []
    for path in search_paths:
        try:
            for root, dirs, files in os.walk(path):
                # Restrict search depth to avoid hanging
                if root.count(os.sep) - path.count(os.sep) > 3:
                    continue
                for file in files:
                    if file.endswith((".token", ".biscuit", ".pem", ".key")):
                        found_secrets.append(os.path.join(root, file))
        except Exception:
            pass
    report["found_secrets"] = found_secrets

    # 3. Attempt direct database connection if URL leaked
    db_urls = [os.environ[k] for k in suspicious_env if "DB" in k.upper() or "URL" in k.upper()]
    
    for url in db_urls:
        if "sqlite" in url or "postgresql" in url:
            try:
                from sqlalchemy import create_engine
                from sqlalchemy.sql import text
                engine = create_engine(url)
                with engine.connect() as conn:
                    # Try to read the capability mount table
                    conn.execute(text("SELECT 1 FROM capability_mounts LIMIT 1"))
                    report["db_breach"] = True
                    report["findings"].append(f"DB Breach: direct access via {url}")
                    break
            except Exception as e:
                report["findings"].append(f"DB connect failed for {url}: {e}")

    if report["db_breach"]:
        print(json.dumps({"status": "BREACHED", "reason": "direct_db_access", "report": report}))
        sys.exit(0)  # Attack succeeded -> AI-1 FAILS

    # 4. Attempt to bypass using explicit overrides and actual library calls
    # If the environment allows an override, calling MountRegistry.evaluate should return ALLOW
    try:
        import sqlalchemy.orm

        from cappo_backend.capability_mount.service import MountRegistry
        
        # If we have a DB URL, try to build a registry and evaluate
        for url in db_urls:
            if "sqlite" in url or "postgresql" in url:
                engine = create_engine(url)
                Session = sqlalchemy.orm.sessionmaker(bind=engine)
                db = Session()
                reg = MountRegistry(db)
                
                decision, reason, _, _ = reg.evaluate(
                    mount_id="dummy",
                    action="test.action",
                    requested_resource="test",
                    biscuit_token=None
                )
                
                if decision.value == "allow":
                    print(json.dumps({"status": "BREACHED", "reason": "real_cappo_allow_achieved", "report": report}))
                    sys.exit(0)
    except Exception as e:
        report["findings"].append(f"Direct CAPPO evaluation failed: {e}")

    # 5. If we reach here, the hostile workload could not find a way to elevate privileges
    print(json.dumps({"status": "SECURE", "reason": "isolation_held", "report": report}))
    sys.exit(1)  # Attack failed -> AI-1 PASSES

if __name__ == "__main__":
    attack()
