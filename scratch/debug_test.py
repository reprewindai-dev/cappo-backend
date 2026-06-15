import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cappo_backend.db.base import Base
from cappo_backend.models.governed_run import GovernedRun


class TestDebug(unittest.TestCase):
    def test_json_column(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        
        run = GovernedRun(
            workspace_id="default",
            tenant_id="default",
            state="CREATED",
        )
        db.add(run)
        db.flush()
        
        run.v4_decision = {"directive": "ALLOW", "risk_tier": "standard"}
        db.flush()
        
        print("--- type(run.v4_decision) ---", type(run.v4_decision))
        print("--- run.v4_decision ---", run.v4_decision)
        
        db.close()

if __name__ == "__main__":
    unittest.main()
