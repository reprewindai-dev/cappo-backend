"""Run the real CAPPO app with a deliberately narrower signed Biscuit grant."""

from __future__ import annotations

import os

import cappo_backend.security.biscuit as biscuit

_original_mint = biscuit.mint_biscuit_capability


def _narrow_mint(**kwargs: object) -> str:
    narrowed = dict(kwargs)
    narrowed["writes"] = []
    return _original_mint(**narrowed)


biscuit.mint_biscuit_capability = _narrow_mint

from runtime_app import PeerCertificateH11Protocol, app  # noqa: E402

if __name__ == "__main__":
    import uvicorn

    config = uvicorn.Config(
        app,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8444")),
        http=PeerCertificateH11Protocol,
        ssl_keyfile=str(
            Path(os.environ.get("ACTIVATION_V1_ROOT", "/home/ubuntu/activation_v1"))
            / "mtls"
            / "server.key"
        ),
        ssl_certfile=str(
            Path(os.environ.get("ACTIVATION_V1_ROOT", "/home/ubuntu/activation_v1"))
            / "mtls"
            / "server.crt"
        ),
        ssl_ca_certs=str(
            Path(os.environ.get("ACTIVATION_V1_ROOT", "/home/ubuntu/activation_v1"))
            / "mtls"
            / "ca.crt"
        ),
        ssl_cert_reqs=1,
        log_level="info",
    )
    uvicorn.Server(config).run()
