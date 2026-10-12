# Veklom receipt verifier (independent)

Checks a CAPPO execution receipt without trusting CAPPO. It shares no code with CAPPO:
the CBOR decoder and the COSE `Sig_structure` encoder are written from RFC 8949 and
RFC 9052. `verify.py` needs only the Python standard library and `cryptography`;
`verify.html` needs only a browser with Ed25519 WebCrypto.

## Inputs

- **Receipt bundle**: JSON from `GET /v1/capability/receipts/{receipt_id}`
  (`receipt_id`, `mount_id`, `receipt_schema_version`, `content_hash`, `signed`, `kid`,
  `cose_b64`, `key_endpoint`).
- **Published key**: JSON from `GET <cappo>/.well-known/evidence-keys`
  (`{"keys": [{"kid", "kty": "OKP", "crv": "Ed25519", "x", ...}]}`).

## What it checks

1. `cose_b64` is a well-formed COSE_Sign1: CBOR tag 18 around 4 elements. Parsing is
   strict: no trailing bytes, no indefinite lengths, no duplicate map keys.
2. The protected header says `alg = -8` (EdDSA).
3. The `kid` in the receipt names exactly one published key. That key is a valid
   Ed25519 JWK, and `kid` equals `"ed25519-sha256:" + sha256(raw public key)`, which is
   the fingerprint of the key that verifies the signature.
4. The Ed25519 signature verifies over
   `["Signature1", protected, h'', payload]`, built from the exact bytes in the message.
5. `content_hash` in the bundle equals the SHA-256 of the signed fields as compact JSON
   with sorted keys (`json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)`).
   So the hash shown in the UI is the hash of exactly what was signed.
6. Unsigned bundle metadata (`kid`, `mount_id`, `receipt_schema_version`) agrees with the
   signed content.

The result is VALID only when every check passes.

## What it does NOT prove

- It proves that the holder of the published key signed the receipt and that the
  receipt has not changed since. It does **not** prove that the key holder behaved
  honestly, that the action really happened, or that the key was not stolen.
- Trust in the published key is up to you. Fetch it from a source you trust, and
  compare it over time: a new `kid` means a new key.
- The `kid` sits in the COSE *unprotected* header, so the signature does not cover it.
  That is why the verifier checks separately that the kid is the fingerprint of the
  key that verified the signature.
- `receipt_id` and other bundle fields that are not part of the signed payload are not
  covered by the signature.
- A bundle without `content_hash` fails check 5, even if the signature is valid.

## Run it

```
pip install cryptography
python verify.py --bundle receipt.json --keys evidence-keys.json
python verify.py --bundle receipt.json --keys-url http://127.0.0.1:28202/.well-known/evidence-keys
python verify.py --bundle receipt.json --keys evidence-keys.json --tamper signature   # shows a failure
python verify.py --bundle receipt.json --keys evidence-keys.json --tamper payload
```

Exit codes: `0` valid, `1` invalid, `2` usage or input-file error. The last line is
`RESULT: VALID` or `RESULT: INVALID — <reason>`.

**Browser:** open `verify.html` directly from disk. Paste the bundle and the key, then
press **Verify**. **Flip one byte** changes one byte in the middle of the signature
inside the pasted bundle and verifies again. Press it a second time to flip the byte
back. The page needs Ed25519 WebCrypto, which current Chrome, Edge, Firefox and Safari
have. Without it, the page says so and does not report the signature as valid.
Nothing leaves the page.

**Tests** (`cbor2` is used only by the tests, to build receipts the same way CAPPO does):

```
pip install cryptography cbor2 pytest
python -m pytest -q test_verify.py
```
