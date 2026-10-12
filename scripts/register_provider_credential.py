"""Register the model provider a workspace may use through CAPPO.

Run on the CAPPO host, with the same DATABASE_URL and VAULT_MASTER_KEY as the server:

    python scripts/register_provider_credential.py --workspace <id> --provider ollama \
        --base-url http://host.docker.internal:11434

For a provider that needs a key, put the key in an environment variable and name it
with --api-key-env (the key is never taken as a command-line argument):

    OPENAI_KEY=... python scripts/register_provider_credential.py --workspace <id> \
        --provider openai --base-url https://api.openai.com/v1 --api-key-env OPENAI_KEY

The provider must also be listed in INFERENCE_ALLOWED_PROVIDERS for veklom.inference@v1
to dispatch to it.
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--provider", required=True, help="e.g. ollama, openai, groq")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key-env", default=None, help="environment variable holding the key")
    parser.add_argument("--profile", default="default")
    args = parser.parse_args(argv)

    api_key = None
    if args.api_key_env:
        api_key = os.environ.get(args.api_key_env)
        if not api_key:
            print(f"environment variable {args.api_key_env} is empty", file=sys.stderr)
            return 2

    from cappo_backend.config import get_settings
    from cappo_backend.db.session import SessionLocal
    from cappo_backend.services.provider_credentials import register_provider_credential

    db = SessionLocal()
    try:
        registered = register_provider_credential(
            db,
            get_settings(),
            workspace_id=args.workspace,
            provider=args.provider,
            base_url=args.base_url,
            api_key=api_key,
            credential_profile=args.profile,
        )
    except ValueError as exc:
        print(f"not registered: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    print(
        f"registered {registered.provider} for workspace {registered.workspace_id} "
        f"(profile {registered.credential_profile}, key version {registered.key_version}, "
        f"{'key stored encrypted' if registered.has_secret else 'no key'}, "
        f"base URL {registered.base_url or 'provider default'})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
