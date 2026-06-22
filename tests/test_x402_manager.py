from __future__ import annotations

from cappo_backend.services.x402_payment import X402PaymentManager, get_x402_manager

class TestX402ManagerSingleton:
    def test_get_x402_manager_returns_same_instance(self) -> None:
        """Calling get_x402_manager multiple times should return the same instance."""
        manager1 = get_x402_manager()
        manager2 = get_x402_manager()

        assert manager1 is not None
        assert isinstance(manager1, X402PaymentManager)
        assert manager1 is manager2
