#!/usr/bin/env python3
"""
Test C — Canonical CapabilityLease replay denial at the CapabilityHandler consequence boundary.
"""

import unittest
from unittest.mock import MagicMock
import uuid
import time

from cappo_backend.services.capability_handler import (
    CapabilityHandler, 
    VerifiedExecutionContext, 
    ConsequenceDominanceViolation,
    ReplayDeniedError,
    MaterializationPolicy
)
from cappo_backend.execution.vre_envelope import VREEnvelopeSpec
from cappo_backend.identity.replay_cache import ReplayCache, RedisReplayCache

# Shared global state to simulate an external durable backend (like Redis) that survives process crash
SHARED_DURABLE_STORE = {}

class MockRedis:
    def setnx(self, key, value):
        if key in SHARED_DURABLE_STORE:
            return 0
        SHARED_DURABLE_STORE[key] = value
        return 1
    
    def expire(self, key, ttl):
        pass

class TestCCapabilityLeaseReplay(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.orchestrator = MagicMock()
        self.orchestrator.run_governed = MagicMock(return_value={"status": "SUCCESS"})
        self.handler = CapabilityHandler(db=self.db)
        self.handler._observe_consequence = MagicMock(return_value={"status": "RECONCILED_SUCCEEDED"})
        
    def test_c_replay_denial_battery(self):
        import cappo_backend.security.biscuit as biscuit_mod
        original_verify = getattr(biscuit_mod, 'verify_biscuit_capability', None)
        
        # Patch verify_biscuit_capability to validate execution_id binding
        def mock_verify(token_b64, executor_spiffe_id, action, resource, subject_spiffe_id, trusted_state, execution_id, envelope_digest=None):
            if execution_id != "exec-1":
                return False
            return True
            
        biscuit_mod.verify_biscuit_capability = mock_verify
        
        try:
            # Clear external durable store before test
            SHARED_DURABLE_STORE.clear()
            
            redis_client = MockRedis()
            replay_cache = RedisReplayCache(redis_client)
            self.handler = CapabilityHandler(db=self.db, replay_cache=replay_cache)
            self.handler._observe_consequence = MagicMock(return_value={"status": "RECONCILED_SUCCEEDED"})
            
            ctx = VerifiedExecutionContext(
                principal="spiffe://test/subject",
                workspace_id="ws-1",
                execution_id="exec-1",
                mount_id="mount-1",
                token_id="token-1",
                nonce="nonce-1",
                receipt_id="receipt-1",
                action="test.action",
                intent_hash="intent-1",
                operation_id="op-1",
                resource="any",
                payload={},
                materialization_policy=MaterializationPolicy.EPHEMERAL,
                is_activation=False,
                biscuit_token="mock_biscuit",
            )
            
            # C1: First valid presentation -> ALLOW -> call_count == 1
            self.handler.execute(ctx, self.orchestrator)
            self.assertEqual(self.orchestrator.run_governed.call_count, 1)
            
            # C2: Exact same lease/token/nonce replay -> REPLAY_DENIED -> call_count remains 1
            with self.assertRaises(ReplayDeniedError) as cm:
                self.handler.execute(ctx, self.orchestrator)
            self.assertIn("REPLAY_DENIED", str(cm.exception))
            self.assertEqual(self.orchestrator.run_governed.call_count, 1)
            
            # C4: Same lease, altered execution_id -> execution-binding DENY
            ctx_c4 = VerifiedExecutionContext(
                principal=ctx.principal,
                workspace_id=ctx.workspace_id,
                execution_id="exec-altered",
                mount_id=ctx.mount_id,
                token_id=ctx.token_id,
                nonce=ctx.nonce,
                receipt_id=ctx.receipt_id,
                action=ctx.action,
                intent_hash=ctx.intent_hash,
                operation_id=ctx.operation_id,
                resource=ctx.resource,
                payload={},
                materialization_policy=MaterializationPolicy.EPHEMERAL,
                is_activation=False,
                biscuit_token="mock_biscuit",
            )
            with self.assertRaises(ConsequenceDominanceViolation) as cm:
                self.handler.execute(ctx_c4, self.orchestrator)
            self.assertIn("execution_id mismatch", str(cm.exception))
            self.assertEqual(self.orchestrator.run_governed.call_count, 1)

            # C6: Restart handler/process then replay consumed lease
            # Simulate a full crash by destroying the cache instance entirely.
            del replay_cache
            
            # Create a completely new RedisReplayCache instance pointing to the external durable backend.
            redis_client_new_process = MockRedis()
            durable_replay_cache = RedisReplayCache(redis_client_new_process)
            
            handler2 = CapabilityHandler(db=self.db, replay_cache=durable_replay_cache)
            handler2._observe_consequence = MagicMock(return_value={"status": "RECONCILED_SUCCEEDED"})
            with self.assertRaises(ReplayDeniedError):
                handler2.execute(ctx, self.orchestrator)
            self.assertEqual(self.orchestrator.run_governed.call_count, 1)
                
        finally:
            if original_verify:
                biscuit_mod.verify_biscuit_capability = original_verify

if __name__ == "__main__":
    unittest.main()
