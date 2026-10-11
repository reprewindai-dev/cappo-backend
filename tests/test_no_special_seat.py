"""No special seat (owner rule 12, 2026-10-11): any party can be the center.

Every issuer runs the same protocol. A verifier trusts whichever issuers it chooses; each
issuer's records verify only with that issuer's own keys, and renaming an issuer changes
nothing about how verification behaves.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from cappo_backend.security import evidence as ev
from cappo_backend.services.eee import EEEBuilder, EEEVerifier, VerificationVerdict
from tests.test_eee import _envelope_input, _now

VEKLOM = "https://cappo.veklom.com"
ACME = "https://authority.acme.example"


def _envelope(builder: EEEBuilder) -> dict:
    fields = _envelope_input(_now())
    fields["issuer"] = builder.issuer
    for link in fields["authority_chain"]:
        link["issuer"] = builder.issuer
    return builder.build(fields)


@pytest.fixture()
def two_issuers():
    veklom = EEEBuilder(signing_key="a" * 64, issuer=VEKLOM, kid="key-1")
    acme = EEEBuilder(signing_key="b" * 64, issuer=ACME, kid="key-1")  # same kid name on purpose
    verifier = EEEVerifier(issuer_keys={VEKLOM: {"key-1": veklom.public_key_bytes}, ACME: {"key-1": acme.public_key_bytes}})
    return veklom, acme, verifier


def test_each_trusted_issuer_verifies_the_same_way(two_issuers):
    veklom, acme, verifier = two_issuers
    assert verifier.verify(_envelope(veklom)).verdict is VerificationVerdict.VALID
    assert verifier.verify(_envelope(acme)).verdict is VerificationVerdict.VALID


def test_one_trusted_issuer_cannot_sign_as_another(two_issuers):
    veklom, _acme, verifier = two_issuers
    # Acme's key signing a record that claims to come from Veklom.
    impostor = EEEBuilder(signing_key="b" * 64, issuer=VEKLOM, kid="key-1")
    result = verifier.verify(_envelope(impostor))
    assert result.verdict is VerificationVerdict.INVALID
    assert result.reasons == ["ISSUER_SIGNATURE_INVALID"]
    # And the reverse.
    impostor = EEEBuilder(signing_key="a" * 64, issuer=ACME, kid="key-1")
    assert verifier.verify(_envelope(impostor)).verdict is VerificationVerdict.INVALID


def test_an_issuer_the_verifier_does_not_trust_is_rejected():
    stranger = EEEBuilder(signing_key="c" * 64, issuer="https://unknown.example", kid="key-1")
    verifier = EEEVerifier(issuer_keys={VEKLOM: {"key-1": EEEBuilder(signing_key="a" * 64, issuer=VEKLOM, kid="key-1").public_key_bytes}})
    assert verifier.verify(_envelope(stranger)).verdict is VerificationVerdict.INVALID


@pytest.mark.parametrize("name", [VEKLOM, ACME, "https://anyone.example/authority"])
def test_rename_test_the_name_changes_nothing(name):
    builder = EEEBuilder(signing_key="d" * 64, issuer=name, kid="k")
    verifier = EEEVerifier(issuer_keys={name: {"k": builder.public_key_bytes}})
    envelope = _envelope(builder)
    assert verifier.verify(envelope).verdict is VerificationVerdict.VALID
    tampered = dict(envelope, status="failed")
    assert verifier.verify(tampered).verdict is VerificationVerdict.INVALID


def test_verifier_needs_exactly_one_trust_form():
    with pytest.raises(ValueError):
        EEEVerifier()
    with pytest.raises(ValueError):
        EEEVerifier({"k": b"x" * 32}, issuer_keys={VEKLOM: {}})


def test_receipts_from_two_authorities_verify_with_one_trust_store():
    veklom_key = ed25519.Ed25519PrivateKey.generate()
    acme_key = ed25519.Ed25519PrivateKey.generate()
    trust = {"veklom": [veklom_key.public_key()], "acme": [acme_key.public_key()]}

    from_veklom = ev.mint_signed_execution_evidence({"execution_id": "v1", "decision": 0}, private_key=veklom_key)
    from_acme = ev.mint_signed_execution_evidence({"execution_id": "a1", "decision": 0}, private_key=acme_key)

    assert ev.verify_with_trust_store(from_veklom, trust) == ("veklom", {"execution_id": "v1", "decision": 0})
    assert ev.verify_with_trust_store(from_acme, trust) == ("acme", {"execution_id": "a1", "decision": 0})

    stranger = ev.mint_signed_execution_evidence({"execution_id": "s1"}, private_key=ed25519.Ed25519PrivateKey.generate())
    with pytest.raises(ValueError):
        ev.verify_with_trust_store(stranger, trust)


def test_a_receipt_cannot_borrow_another_issuers_key_id():
    veklom_key = ed25519.Ed25519PrivateKey.generate()
    acme_key = ed25519.Ed25519PrivateKey.generate()
    trust = {"veklom": [veklom_key.public_key()], "acme": [acme_key.public_key()]}
    # Acme signs but labels the receipt with Veklom's key id.
    forged = ev.mint_signed_execution_evidence(
        {"execution_id": "x"}, private_key=acme_key, key_id=ev.evidence_key_id(veklom_key.public_key()).encode()
    )
    with pytest.raises(ValueError):
        ev.verify_with_trust_store(forged, trust)


def test_published_issuer_name_is_the_operators_choice(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cappo_backend.api.routers.evidence_key_router import evidence_key_router

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("EVIDENCE_ROOT_PRIVATE_KEY_HEX", raising=False)
    monkeypatch.setenv("EVIDENCE_ROOT_PRIVATE_KEY_HEX", ed25519.Ed25519PrivateKey.generate().private_bytes_raw().hex())
    ev.reset_evidence_key_cache()
    app = FastAPI()
    app.include_router(evidence_key_router)
    monkeypatch.setenv("EVIDENCE_ISSUER", ACME)
    assert TestClient(app).get("/.well-known/evidence-keys").json()["issuer"] == ACME
    monkeypatch.delenv("EVIDENCE_ISSUER")
    assert TestClient(app).get("/.well-known/evidence-keys").json()["issuer"] == "cappo"
    ev.reset_evidence_key_cache()
