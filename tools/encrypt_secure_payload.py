#!/usr/bin/env python3
"""Encrypt a JSON payload for a one-time GitHub Actions bootstrap runner."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--public-key", required=True, type=Path)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("payload must be a JSON object")
    public_key = serialization.load_pem_public_key(args.public_key.read_bytes())
    plaintext = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    aes_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    aad = args.request_id.encode("utf-8")
    ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext, aad)
    encrypted_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    envelope = {
        "version": 1,
        "request_id": args.request_id,
        "encrypted_key": base64.b64encode(encrypted_key).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(envelope, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
