"""Governed model inference (``veklom.inference@v1``).

A mount of this package lets one execution send one prompt through ``POST /v1/exec``
to a model provider. Which providers it may reach is fixed here, by the operator's
configuration, and never by the caller: ``/v1/exec`` binds this list into the
execution identity and the executor refuses any provider outside it.
"""

from __future__ import annotations

from cappo_backend.config import Settings

from .models import CapabilityPackage

INFERENCE_PACKAGE_ID = "veklom.inference@v1"
INFERENCE_ACTION = "llm.exec"
ALLOWED_PROVIDERS_KEY = "allowed_providers"


def inference_package(allowed_providers: list[str]) -> CapabilityPackage:
    return CapabilityPackage(
        id=INFERENCE_PACKAGE_ID,
        family="inference",
        title="Governed Model Inference",
        purpose=(
            "Send one prompt to an operator-approved model provider and return its "
            "answer as evidence-bearing output. The model proposes; nothing it says "
            "is applied anywhere."
        ),
        reads=[],
        writes=[INFERENCE_ACTION],
        blocked=[],
        outputs=["llm.response"],
        policy_defaults={ALLOWED_PROVIDERS_KEY: list(allowed_providers)},
    )


def parse_provider_list(raw: str) -> list[str]:
    seen: list[str] = []
    for item in raw.split(","):
        name = item.strip().lower()
        if name and name not in seen:
            seen.append(name)
    return seen


def inference_package_from_settings(settings: Settings) -> CapabilityPackage | None:
    providers = parse_provider_list(settings.inference_allowed_providers)
    if not providers:
        return None
    return inference_package(providers)


def package_allowed_providers(package: CapabilityPackage | None) -> list[str]:
    """The provider set a package authorises; empty (deny all) when it names none."""
    if package is None:
        return []
    value = package.policy_defaults.get(ALLOWED_PROVIDERS_KEY)
    if not isinstance(value, list):
        return []
    return [p.strip().lower() for p in value if isinstance(p, str) and p.strip()]
