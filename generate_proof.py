import asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from cappo_backend.db.base import Base
from cappo_backend.models.pgl_certificate import PGLCertificate
from tests.test_execution_evidence_lifecycle import _orchestrator
import json

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
db = sessionmaker(bind=engine)()
orc = _orchestrator(db)
orc.run_governed({"prompt": "Hello World", "directive": "ALLOW"})
eee = orc.last_run.pgl_identity

pre = db.get(PGLCertificate, eee["pre_execution_certificate_id"])
post = db.get(PGLCertificate, eee["post_execution_certificate_id"])

def dump_obj(obj):
    return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}

print("--- PROOF ---")
print(json.dumps({
    "pre_execution_certificate": dump_obj(pre),
    "post_execution_certificate": dump_obj(post),
    "run_id": orc.last_run.run_id,
    "execution_identity": orc.last_run.execution_identity
}, indent=2, default=str))
