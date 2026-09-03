"""
Self-test for the public EncDec transparency export.

Uses only fake in-memory secrets. Never loads install keys, peer lists, or app state.
Run from repo root:

    python Support/test_encdec.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from EncDec import (
    CHUNK_ENC_MAGIC,
    RELEASE_ENC_MAGIC,
    decrypt,
    decrypt_bytes,
    decrypt_chunk_payload,
    decrypt_local,
    encrypt,
    encrypt_bytes,
    encrypt_chunk_payload,
    encrypt_local,
    encrypt_v2,
    generate_chunk_dek,
    is_encrypted_chunk,
    unwrap_chunk_dek,
    wrap_chunk_dek,
)

# Deterministic fake material for CI / public scrutiny only.
FAKE_MESH = b"neuron-public-test-mesh-secret-32b!"
FAKE_V2 = b"neuron-public-test-v2-secret-32by!"
FAKE_ECDH = b"neuron-public-test-ecdh-secret32!"
FAKE_LOCAL = b"neuron-public-test-local-secret3!"


def _expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def run_tests():
    plain = "hello neuron transparency"

    wire_v3 = encrypt(plain, key_index=7, mesh_secret=FAKE_MESH)
    _expect(wire_v3.startswith("v3:"), "v3 prefix missing")
    _expect(decrypt(wire_v3, mesh_secret=FAKE_MESH) == plain, "v3 round-trip failed")

    wire_e1 = encrypt(plain, key_index=7, peer_ecdh_secret=FAKE_ECDH)
    _expect(wire_e1.startswith("e1:"), "e1 prefix missing")
    _expect(
        decrypt(wire_e1, peer_ecdh_secrets=[FAKE_ECDH], mesh_secret=FAKE_MESH) == plain,
        "e1 round-trip failed",
    )

    wire_v2 = encrypt_v2(plain, key_index=3, mesh_v2_secret=FAKE_V2)
    _expect(wire_v2.startswith("v2:"), "v2 prefix missing")
    _expect(decrypt(wire_v2, mesh_v2_secret=FAKE_V2, mesh_secret=FAKE_MESH) == plain, "v2 round-trip failed")

    local = encrypt_local(plain, key_index=1, local_secret=FAKE_LOCAL)
    _expect(decrypt_local(local, local_secret=FAKE_LOCAL) == plain, "local round-trip failed")

    blob = encrypt_bytes(b"release-bytes", key_index=0, mesh_secret=FAKE_MESH)
    _expect(blob.startswith(RELEASE_ENC_MAGIC), "release magic missing")
    _expect(decrypt_bytes(blob, mesh_secret=FAKE_MESH) == b"release-bytes", "release round-trip failed")

    dek = generate_chunk_dek()
    wrapped = wrap_chunk_dek(dek, local_secret=FAKE_LOCAL)
    _expect(unwrap_chunk_dek(wrapped, local_secret=FAKE_LOCAL) == dek, "DEK wrap failed")

    chunk = encrypt_chunk_payload(dek, "icon-test", 42, b"chunk-payload")
    _expect(is_encrypted_chunk(chunk) and chunk.startswith(CHUNK_ENC_MAGIC), "chunk magic missing")
    _expect(
        decrypt_chunk_payload(dek, "icon-test", 42, chunk) == b"chunk-payload",
        "chunk round-trip failed",
    )

    print("EncDec public self-test: OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run_tests())
    except Exception as exc:
        print("EncDec public self-test: FAIL -", exc)
        raise SystemExit(1)
