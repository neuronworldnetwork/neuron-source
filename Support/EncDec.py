"""
Neuron encryption algorithm (public transparency export).

This file documents how Neuron protects data at rest and in transit between
peers. It is published so users can inspect the cryptography.

IMPORTANT — what is NOT published:
  - Real master secrets, salts used as IKM, or per-install keys
  - Application source, network protocol, admin tools, or peer logic
  - Secret-file paths, pubkey file loading, or live ECDH secret stores

Published design (algorithm only):
  - Key derivation: HKDF-SHA512 over (secret, salt, obfuscated key index)
  - Content encryption: AES-256-GCM over zlib-compressed UTF-8 plaintext
  - Wire formats:
      legacy:   "<obfuscated_index>:<base64(nonce || ciphertext)>"
      v2:       "v2:<obfuscated_index>:<base64(nonce || ciphertext)>"
      v3:       "v3:<obfuscated_index>:<base64(nonce || ciphertext)>"
      ecdh/e1:  "e1:<obfuscated_index>:<base64(nonce || ciphertext)>"
  - Associated data (AAD) binds ciphertext to protocol version where used
  - Binary release packages: AES-256-GCM with fixed magic header as AAD
  - Chunk payloads: per-file DEK + AES-256-GCM with (icon_id, chk_id) AAD

Secret material is generated and stored privately per deployment / install and
is never shipped in this public repository. Callers pass secrets explicitly.
"""

import os
import zlib
import base64
import struct
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Placeholders only — real IKM values are private and never published.
MASTER_SECRET = b"<PRIVATE_LEGACY_MESH_OR_APP_SECRET>"
HKDF_SALT = b"<PRIVATE_LEGACY_HKDF_SALT>"

# Public protocol labels (HKDF salt / AAD / prefixes). These are not IKM.
HKDF_SALT_V2 = b"neuron-mesh-hkdf-salt-v2"
HKDF_SALT_V3 = b"neuron-mesh-hkdf-salt-v3"
HKDF_SALT_ECDH = b"neuron-peer-ecdh-hkdf-v1"
MESH_V2_PREFIX = "v2:"
MESH_V3_PREFIX = "v3:"
MESH_ECDH_PREFIX = "e1:"
MAGIC_OBFUSCATOR = 0xA3
AAD_ECDH = b"neuron-e1-aes256gcm"
AAD_V3 = b"neuron-v3-aes256gcm"
AAD_LOCAL = b"neuron-local-aes256gcm"

RELEASE_ENC_KEY_INDEX = 0  # private deployments choose their own indices
RELEASE_ENC_MAGIC = b"NRREL1"
CHUNK_ENC_MAGIC = b"NRCHK1"
CHUNK_DEK_WRAP_INDEX = 0  # private deployments choose their own indices


def derive_key(index: int, master_secret: bytes = None, salt: bytes = None) -> bytes:
    obfuscated_index = index ^ MAGIC_OBFUSCATOR
    index_bytes = obfuscated_index.to_bytes(4, "big")
    secret = MASTER_SECRET if master_secret is None else master_secret
    use_salt = HKDF_SALT_V3 if salt is None else salt
    hkdf = HKDF(
        algorithm=hashes.SHA512(),
        length=32,
        salt=use_salt,
        info=index_bytes,
    )
    return hkdf.derive(secret)


def derive_mesh_v2_secret(pubkey_material: bytes) -> bytes:
    """
    v2 mesh secret: HKDF-SHA512 over public key material (release/admin pubkey bytes).
    Production loads that material from private install files — not published here.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA512(),
        length=32,
        salt=HKDF_SALT_V2,
        info=b"neuron-mesh-master-v2",
    )
    return hkdf.derive(pubkey_material)


def _gcm_open(key: bytes, nonce: bytes, ciphertext: bytes, aads):
    aesgcm = AESGCM(key)
    last_error = None
    for aad in aads:
        try:
            return aesgcm.decrypt(nonce, ciphertext, associated_data=aad)
        except Exception as exc:
            last_error = exc
            continue
    raise last_error if last_error else ValueError("Decrypt failed")


def base85_encode(data: bytes) -> str:
    return base64.a85encode(data).decode("ascii")


def base85_decode(data: str) -> bytes:
    return base64.a85decode(data.encode("ascii"))


def base64_encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def base64_decode(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


def encrypt(
    plaintext: str,
    key_index: int = 0,
    *,
    mesh_secret: bytes = None,
    peer_ecdh_secret: bytes = None,
) -> str:
    """
    Encrypt mesh text.

    Prefer peer_ecdh_secret (X25519 shared secret obtained out-of-band) when
    available; otherwise use a per-install mesh_secret (v3). Placeholders are
    used only when neither is supplied — real deployments never do that.
    """
    if not plaintext:
        return ""

    if peer_ecdh_secret:
        key = derive_key(key_index, peer_ecdh_secret, HKDF_SALT_ECDH)
        aad = AAD_ECDH
        prefix = MESH_ECDH_PREFIX
    else:
        secret = mesh_secret if mesh_secret is not None else MASTER_SECRET
        key = derive_key(key_index, secret, HKDF_SALT_V3)
        aad = AAD_V3
        prefix = MESH_V3_PREFIX

    compressed = zlib.compress(plaintext.encode("utf-8"), level=6)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, compressed, associated_data=aad)
    obfuscated_index = key_index ^ MAGIC_OBFUSCATOR
    return f"{prefix}{obfuscated_index}:{base64_encode(nonce + ciphertext)}"


def encrypt_v2(plaintext: str, key_index: int = 0, mesh_v2_secret: bytes = None) -> str:
    """Encrypt with pubkey-derived v2 key (compatible across nodes that share that material)."""
    if not plaintext:
        return ""
    secret = mesh_v2_secret if mesh_v2_secret is not None else MASTER_SECRET
    key = derive_key(key_index, secret, HKDF_SALT_V2)
    compressed = zlib.compress(plaintext.encode("utf-8"), level=6)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, compressed, associated_data=None)
    obfuscated_index = key_index ^ MAGIC_OBFUSCATOR
    return f"{MESH_V2_PREFIX}{obfuscated_index}:{base64_encode(nonce + ciphertext)}"


def decrypt_standard(
    data: str,
    *,
    mesh_secret: bytes = None,
    mesh_v2_secret: bytes = None,
    peer_ecdh_secrets=None,
    accept_legacy: bool = True,
) -> str:
    if not data:
        return ""
    if not isinstance(data, str):
        raise ValueError(f"Encrypted data must be string, got {type(data).__name__}")

    prefer_ecdh = data.startswith(MESH_ECDH_PREFIX)
    prefer_v3 = (not prefer_ecdh) and data.startswith(MESH_V3_PREFIX)
    prefer_v2 = (not prefer_ecdh) and (not prefer_v3) and data.startswith(MESH_V2_PREFIX)
    if prefer_ecdh:
        body = data[len(MESH_ECDH_PREFIX) :]
    elif prefer_v3:
        body = data[len(MESH_V3_PREFIX) :]
    elif prefer_v2:
        body = data[len(MESH_V2_PREFIX) :]
    else:
        body = data

    obfuscated_index_str, encoded_packet = body.split(":", 1)
    key_index = int(obfuscated_index_str) ^ MAGIC_OBFUSCATOR
    try:
        packet = base64_decode(encoded_packet)
    except Exception:
        packet = base85_decode(encoded_packet)
    nonce = packet[:12]
    ciphertext = packet[12:]

    v3_secret = mesh_secret if mesh_secret is not None else MASTER_SECRET
    v2_secret = mesh_v2_secret if mesh_v2_secret is not None else MASTER_SECRET
    ecdh_list = list(peer_ecdh_secrets or [])

    secret_order = []
    if prefer_ecdh:
        for secret in ecdh_list:
            secret_order.append((secret, HKDF_SALT_ECDH, (AAD_ECDH, None)))
        secret_order.append((v3_secret, HKDF_SALT_V3, (AAD_V3, None)))
        secret_order.append((v2_secret, HKDF_SALT_V2, (None,)))
        if accept_legacy:
            secret_order.append((MASTER_SECRET, HKDF_SALT, (None,)))
    elif prefer_v3:
        secret_order = [(v3_secret, HKDF_SALT_V3, (AAD_V3, None))]
        if accept_legacy:
            secret_order.append((v2_secret, HKDF_SALT_V2, (None,)))
            secret_order.append((MASTER_SECRET, HKDF_SALT, (None,)))
    elif prefer_v2:
        secret_order = [(v2_secret, HKDF_SALT_V2, (None,))]
        secret_order.append((v3_secret, HKDF_SALT_V3, (AAD_V3, None)))
        if accept_legacy:
            secret_order.append((MASTER_SECRET, HKDF_SALT, (None,)))
    else:
        if accept_legacy:
            secret_order = [
                (MASTER_SECRET, HKDF_SALT, (None,)),
                (v2_secret, HKDF_SALT_V2, (None,)),
                (v3_secret, HKDF_SALT_V3, (AAD_V3, None)),
            ]
        else:
            secret_order = [
                (v3_secret, HKDF_SALT_V3, (AAD_V3, None)),
                (v2_secret, HKDF_SALT_V2, (None,)),
            ]

    last_error = None
    decompressed = None
    for secret, salt, aads in secret_order:
        try:
            key = derive_key(key_index, secret, salt)
            decompressed = _gcm_open(key, nonce, ciphertext, aads)
            break
        except Exception as e:
            last_error = e
            continue
    if decompressed is None:
        raise last_error if last_error else ValueError("Decrypt failed")

    if len(decompressed) > 100 * 1024 * 1024:
        raise ValueError(f"Decompressed data too large: {len(decompressed)} bytes")
    return zlib.decompress(decompressed).decode("utf-8")


def decrypt(data: str, **kwargs) -> str:
    if not data:
        return ""
    if isinstance(data, list):
        if not data:
            return ""
        data = data[0]
    if ":" not in data:
        return data
    return decrypt_standard(data, **kwargs)


def encrypt_bytes(
    plaintext: bytes,
    key_index: int = RELEASE_ENC_KEY_INDEX,
    mesh_secret: bytes = None,
) -> bytes:
    if not plaintext:
        raise ValueError("Cannot encrypt empty payload")
    secret = mesh_secret if mesh_secret is not None else MASTER_SECRET
    key = derive_key(key_index, secret, HKDF_SALT_V3)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data=RELEASE_ENC_MAGIC)
    obfuscated_index = key_index ^ MAGIC_OBFUSCATOR
    return RELEASE_ENC_MAGIC + struct.pack(">I", obfuscated_index) + nonce + ciphertext


def decrypt_bytes(
    packet: bytes,
    mesh_secret: bytes = None,
    accept_legacy: bool = True,
) -> bytes:
    if not packet or len(packet) < 6 + 4 + 12 + 16:
        raise ValueError("Encrypted packet too short")
    if packet[:6] != RELEASE_ENC_MAGIC:
        raise ValueError("Not a Neuron release package")
    obfuscated_index = struct.unpack(">I", packet[6:10])[0]
    key_index = obfuscated_index ^ MAGIC_OBFUSCATOR
    nonce = packet[10:22]
    ciphertext = packet[22:]
    v3_secret = mesh_secret if mesh_secret is not None else MASTER_SECRET
    candidates = [(v3_secret, HKDF_SALT_V3)]
    if accept_legacy:
        candidates.append((MASTER_SECRET, HKDF_SALT))
    last_error = None
    for secret, salt in candidates:
        try:
            key = derive_key(key_index, secret, salt)
            return AESGCM(key).decrypt(nonce, ciphertext, associated_data=RELEASE_ENC_MAGIC)
        except Exception as e:
            last_error = e
            continue
    raise last_error if last_error else ValueError("Decrypt failed")


def encrypt_local(plaintext: str, key_index: int = 0, local_secret: bytes = None) -> str:
    """Same construction as mesh encrypt, keyed by a private per-install secret + AAD_LOCAL."""
    if not plaintext:
        return ""
    secret = local_secret if local_secret is not None else MASTER_SECRET
    key = derive_key(key_index, secret, HKDF_SALT)
    compressed = zlib.compress(plaintext.encode("utf-8"), level=6)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, compressed, associated_data=AAD_LOCAL)
    obfuscated_index = key_index ^ MAGIC_OBFUSCATOR
    return f"{obfuscated_index}:{base64_encode(nonce + ciphertext)}"


def decrypt_local(data: str, local_secret: bytes = None, accept_legacy: bool = True) -> str:
    """Decrypt local-at-rest data; optionally fall back to legacy mesh secret."""
    if not data:
        return ""
    obfuscated_index_str, encoded_packet = data.split(":", 1)
    key_index = int(obfuscated_index_str) ^ MAGIC_OBFUSCATOR
    packet = base64_decode(encoded_packet)
    nonce, ciphertext = packet[:12], packet[12:]
    secrets = []
    if local_secret is not None:
        secrets.append((local_secret, (AAD_LOCAL, None)))
    if accept_legacy:
        secrets.append((MASTER_SECRET, (None,)))
    for secret, aads in secrets:
        try:
            key = derive_key(key_index, secret, HKDF_SALT)
            decompressed = _gcm_open(key, nonce, ciphertext, aads)
            return zlib.decompress(decompressed).decode("utf-8")
        except Exception:
            continue
    raise ValueError("Unable to decrypt local data")


def generate_chunk_dek() -> bytes:
    return os.urandom(32)


def wrap_chunk_dek(dek: bytes, local_secret: bytes = None) -> str:
    if not dek or len(dek) != 32:
        raise ValueError("DEK must be 32 bytes")
    return encrypt_local(base64_encode(dek), key_index=CHUNK_DEK_WRAP_INDEX, local_secret=local_secret)


def unwrap_chunk_dek(wrapped_dek: str, local_secret: bytes = None) -> bytes:
    if not wrapped_dek:
        raise ValueError("Missing wrapped DEK")
    dek = base64_decode(decrypt_local(wrapped_dek, local_secret=local_secret))
    if len(dek) != 32:
        raise ValueError("Invalid DEK length")
    return dek


def is_encrypted_chunk(packet: bytes) -> bool:
    return bool(packet) and packet.startswith(CHUNK_ENC_MAGIC)


def _chunk_aad(icon_id: str, chk_id: int) -> bytes:
    return icon_id.encode("utf-8") + struct.pack(">I", int(chk_id))


def encrypt_chunk_payload(dek: bytes, icon_id: str, chk_id: int, payload: bytes) -> bytes:
    if not payload:
        return payload
    nonce = os.urandom(12)
    ciphertext = AESGCM(dek).encrypt(nonce, payload, associated_data=_chunk_aad(icon_id, chk_id))
    obfuscated_chk = int(chk_id) ^ MAGIC_OBFUSCATOR
    return CHUNK_ENC_MAGIC + struct.pack(">I", obfuscated_chk) + nonce + ciphertext


def decrypt_chunk_payload(dek: bytes, icon_id: str, chk_id: int, packet: bytes) -> bytes:
    if not packet or not is_encrypted_chunk(packet):
        return packet
    if len(packet) < len(CHUNK_ENC_MAGIC) + 4 + 12 + 16:
        raise ValueError("Encrypted chunk packet too short")
    obfuscated_chk = struct.unpack(">I", packet[len(CHUNK_ENC_MAGIC) : len(CHUNK_ENC_MAGIC) + 4])[0]
    stored_chk = obfuscated_chk ^ MAGIC_OBFUSCATOR
    if stored_chk != int(chk_id):
        raise ValueError("Chunk sequence mismatch")
    nonce = packet[len(CHUNK_ENC_MAGIC) + 4 : len(CHUNK_ENC_MAGIC) + 4 + 12]
    ciphertext = packet[len(CHUNK_ENC_MAGIC) + 4 + 12 :]
    return AESGCM(dek).decrypt(nonce, ciphertext, associated_data=_chunk_aad(icon_id, chk_id))
