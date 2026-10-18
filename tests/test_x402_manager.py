from __future__ import annotations

import asyncio

from x402.http.types import RouteConfigurationError, RouteValidationError

from cappo_backend.config import Settings
from cappo_backend.services.x402_payment import (
    X402FreemiumASGI,
    X402PaymentConfig,
    X402PaymentManager,
    get_x402_manager,
)


class TestX402ManagerSingleton:
    def test_get_x402_manager_returns_same_instance(self) -> None:
        """Calling get_x402_manager multiple times should return the same instance."""
        manager1 = get_x402_manager()
        manager2 = get_x402_manager()

        assert manager1 is not None
        assert isinstance(manager1, X402PaymentManager)
        assert manager1 is manager2


class TestX402ConfigWiring:
    def test_settings_drive_treasury_and_networks(self) -> None:
        settings = Settings(
            environment="production",
            veklom_evm_address="0x1234567890abcdef1234567890abcdef12345678",
            x402_networks="base,monad,invalid,base-sepolia",
        )

        config = X402PaymentConfig(settings)

        assert config.evm_address == settings.veklom_evm_address
        assert config.enabled_networks == ["base", "monad", "base-sepolia"]


async def _empty_receive() -> dict[str, object]:
    return {"type": "http.request", "body": b"", "more_body": False}


class _BrokenPaymentApp:
    async def __call__(self, scope, receive, send) -> None:
        error = RouteValidationError(
            route_pattern="POST ^/v1/exec$",
            scheme="exact",
            network="eip155:8453",
            reason="missing_facilitator",
            message="Facilitator doesn't support exact on eip155:8453",
        )
        raise RouteConfigurationError([error])


class _UnexpectedPaymentApp:
    async def __call__(self, scope, receive, send) -> None:
        raise AssertionError("payment middleware should not run for internal API keys")


class TestX402DegradedMode:
    def _middleware(self) -> X402FreemiumASGI:
        async def app(scope, receive, send) -> None:
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = X402FreemiumASGI(
            app,
            server=None,
            routes={},
            settings=Settings(environment="production", api_keys="internal-key"),
        )
        middleware.payment_app = _BrokenPaymentApp()
        return middleware

    def test_route_configuration_failure_allows_valid_internal_key(self) -> None:
        middleware = self._middleware()
        sent: list[dict[str, object]] = []

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "headers": [(b"X-API-Key", b"internal-key")],
            "path": "/v1/exec",
            "method": "POST",
        }

        asyncio.run(middleware._call_payment_app(scope, _empty_receive, send))

        assert sent[0]["status"] == 204
        assert middleware._payment_route_config_broken is True

    def test_route_configuration_failure_fails_closed_without_internal_key(self) -> None:
        middleware = self._middleware()
        sent: list[dict[str, object]] = []

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        scope = {"type": "http", "headers": [], "path": "/v1/exec", "method": "POST"}

        asyncio.run(middleware._call_payment_app(scope, _empty_receive, send))

        assert sent[0]["status"] == 503
        assert middleware._payment_route_config_broken is True

    def test_valid_internal_key_bypasses_payment_middleware(self) -> None:
        middleware = self._middleware()
        middleware.payment_app = _UnexpectedPaymentApp()
        sent: list[dict[str, object]] = []

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "headers": [(b"x-api-key", b"internal-key")],
            "path": "/v1/exec",
            "method": "POST",
        }

        asyncio.run(middleware(scope, _empty_receive, send))

        assert sent[0]["status"] == 204

    def test_validated_auth_scope_bypasses_payment_middleware(self) -> None:
        middleware = self._middleware()
        middleware.payment_app = _UnexpectedPaymentApp()
        sent: list[dict[str, object]] = []

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "headers": [],
            "path": "/v1/exec",
            "method": "POST",
            "cappo_internal_api_key_valid": True,
        }

        asyncio.run(middleware(scope, _empty_receive, send))

        assert sent[0]["status"] == 204

    def test_valid_internal_key_bypasses_with_string_headers(self) -> None:
        middleware = self._middleware()
        middleware.payment_app = _UnexpectedPaymentApp()
        sent: list[dict[str, object]] = []

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "headers": [("X-API-Key", "internal-key")],
            "path": "/v1/exec",
            "method": "POST",
        }

        asyncio.run(middleware(scope, _empty_receive, send))

        assert sent[0]["status"] == 204
