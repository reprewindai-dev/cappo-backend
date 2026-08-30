import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from cappo_backend.execution.models import KMSKeyRecord

DATABASE_URL = "postgresql+psycopg2://test_user:X1aVFJPYMoRGD3Qwscb7ZfpSix6lkeEd@127.0.0.1:5432/cappo_test"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

keys = db.query(KMSKeyRecord).all()
for k in keys:
    print(f"kid: {k.kid}, active: {k.is_active}, expires: {k.expires_at}")
