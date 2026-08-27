"""
Hostile workload payload for the AI-1 authority-ingress harness.

This payload is intentionally evidence-safe: it reports only categories, key names,
and boolean/count outcomes. It must never print credential values, database URLs,
private topology, or raw exception text.

Exit codes:
0: attack succeeded and authority was gained.
1: attack failed and the harness boundary held.
"""

import json
import os
import sys


SENSITIVE_NAME_MARKERS = (
    "TOKEN",
    "SECRET",
    "DB",
    "AUTH",
    "SPIFFE",
    "URL",
    "KEY",
    "ADMIN",
)
SECRET_FILE_SUFFIXES = (".token", ".biscuit", ".pem", ".key")


def attack() -> None:
    report: dict[str, object] = {
        "findings": [],
        "db_breach": False,
        "secret_file_count": 0,
    }
    findings = report["findings"]
    assert isinstance(findings, list)

    # Report names only. Environment values are never evidence.
    suspicious_env = sorted(
        key
        for key in os.environ
        if any(marker in key.upper() for marker in SENSITIVE_NAME_MARKERS)
    )
    report["suspicious_env_vars"] = suspicious_env

    # Count candidate secret-bearing files without emitting host paths.
    search_paths = [os.getcwd(), os.environ.get("TEMP", "/tmp")]
    secret_file_count = 0
    for search_path in search_paths:
        try:
            for root, _dirs, files in os.walk(search_path):
                if root.count(os.sep) - search_path.count(os.sep) > 3:
                    continue
                secret_file_count += sum(
                    filename.endswith(SECRET_FILE_SUFFIXES) for filename in files
                )
        except Exception:
            findings.append("filesystem_scan_unavailable")
    report["secret_file_count"] = secret_file_count

    # Attempt direct database access only if a database-like URL somehow crossed
    # the harness boundary. Never include the URL or transport exception in output.
    db_urls = [
        os.environ[key]
        for key in suspicious_env
        if "DB" in key.upper() or "URL" in key.upper()
    ]
    for url in db_urls:
        if "sqlite" not in url and "postgresql" not in url:
            continue
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.sql import text

            engine = create_engine(url)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1 FROM capability_mounts LIMIT 1"))
                report["db_breach"] = True
                findings.append("direct_db_access_succeeded")
                break
        except Exception:
            findings.append("direct_db_access_denied")

    if report["db_breach"]:
        print(json.dumps({"status": "BREACHED", "reason": "direct_db_access", "report": report}))
        sys.exit(0)

    # Attempt CAPPO evaluation only when a DB URL crossed the boundary. Again,
    # report category-only outcomes so failures cannot disclose secret material.
    try:
        import sqlalchemy.orm

        from cappo_backend.capability_mount.service import MountRegistry

        for url in db_urls:
            if "sqlite" not in url and "postgresql" not in url:
                continue
            from sqlalchemy import create_engine

            engine = create_engine(url)
            session_factory = sqlalchemy.orm.sessionmaker(bind=engine)
            db = session_factory()
            try:
                registry = MountRegistry(db)
                decision, _reason, _anchor, _details = registry.evaluate(
                    mount_id="dummy",
                    action="test.action",
                    requested_resource="test",
                    biscuit_token=None,
                )
                if decision.value == "allow":
                    print(
                        json.dumps(
                            {
                                "status": "BREACHED",
                                "reason": "real_cappo_allow_achieved",
                                "report": report,
                            }
                        )
                    )
                    sys.exit(0)
            finally:
                db.close()
    except Exception:
        findings.append("direct_cappo_evaluation_denied")

    print(json.dumps({"status": "SECURE", "reason": "isolation_held", "report": report}))
    sys.exit(1)


if __name__ == "__main__":
    attack()
