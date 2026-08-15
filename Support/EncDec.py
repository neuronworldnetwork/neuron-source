"""
Neuron encryption algorithm (public transparency export).

This file documents how Neuron protects data at rest and in transit between
peers. It is published so users can inspect the cryptography.

IMPORTANT — what is NOT published:
  - Real master secrets, salts, or per-install keys
  - Application source, network protocol, admin tools, or peer logic

Published design (algorithm only):
  - Key derivation: HKDF-SHA512 over an application secret + salt + key index
  - Content encryption: AES-256-GCM over zlib-compressed UTF-8 plaintext
  - Wire format: "<obfuscated_index>:<base64(nonce || ciphertext)>"
  - Binary packages: AES-256-GCM with a fixed associated-data magic header

Secrets are generated and stored privately per deployment / install and are
never shipped in this public repository.
"""

import os
import zlib
import base64
import struct
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Placeholders only — real values are private and never published.
MASTER_SECRET = b"<PRIVATE_MESH_OR_APP_SECRET>"
HKDF_SALT = b"<PRIVATE_HKDF_SALT>"
MAGIC_OBFUSCATOR = 0xA3

RELEASE_ENC_KEY_INDEX = 0  # private deployments choose their own indices
RELEASE_ENC_MAGIC = b"NRREL1"


def derive_key(index: int, master_secret: bytes = None) -> bytes:
    obfuscated_index = index ^ MAGIC_OBFUSCATOR
    index_bytes = obfuscated_index.to_bytes(4, "big")
    secret = MASTER_SECRET if master_secret is None else master_secret
    hkdf = HKDF(
        algorithm=hashes.SHA512(),
        length=32,
        salt=HKDF_SALT,
        info=index_bytes,
    )
    return hkdf.derive(secret)


def base85_encode(data: bytes) -> str:
    return base64.a85encode(data).decode("ascii")


def base85_decode(data: str) -> bytes:
    return base64.a85decode(data.encode("ascii"))


def base64_encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def base64_decode(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


def encrypt(plaintext: str, key_index: int = 0) -> str:
    if not plaintext:
        return ""
    key = derive_key(key_index)
    compressed = zlib.compress(plaintext.encode("utf-8"), level=6)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, compressed, associated_data=None)
    encoded_packet = base64_encode(nonce + ciphertext)
    obfuscated_index = key_index ^ MAGIC_OBFUSCATOR
    return f"{obfuscated_index}:{encoded_packet}"


def decrypt_standard(data: str) -> str:
    if not data:
        return ""
    if not isinstance(data, str):
        raise ValueError(f"Encrypted data must be string, got {type(data).__name__}")
    obfuscated_index_str, encoded_packet = data.split(":", 1)
    key_index = int(obfuscated_index_str) ^ MAGIC_OBFUSCATOR
    key = derive_key(key_index)
    try:
        packet = base64_decode(encoded_packet)
    except Exception:
        packet = base85_decode(encoded_packet)
    nonce = packet[:12]
    ciphertext = packet[12:]
    decompressed = AESGCM(key).decrypt(nonce, ciphertext, associated_data=None)
    if len(decompressed) > 100 * 1024 * 1024:
        raise ValueError(f"Decompressed data too large: {len(decompressed)} bytes")
    return zlib.decompress(decompressed).decode("utf-8")


def decrypt(data: str) -> str:
    if not data:
        return ""
    if isinstance(data, list):
        if not data:
            return ""
        data = data[0]
    if ":" not in data:
        return data
    return decrypt_standard(data)


def encrypt_bytes(plaintext: bytes, key_index: int = RELEASE_ENC_KEY_INDEX) -> bytes:
    if not plaintext:
        raise ValueError("Cannot encrypt empty payload")
    key = derive_key(key_index)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data=RELEASE_ENC_MAGIC)
    obfuscated_index = key_index ^ MAGIC_OBFUSCATOR
    return RELEASE_ENC_MAGIC + struct.pack(">I", obfuscated_index) + nonce + ciphertext


def decrypt_bytes(packet: bytes) -> bytes:
    if not packet or len(packet) < 6 + 4 + 12 + 16:
        raise ValueError("Encrypted packet too short")
    if packet[:6] != RELEASE_ENC_MAGIC:
        raise ValueError("Not a Neuron release package")
    obfuscated_index = struct.unpack(">I", packet[6:10])[0]
    key_index = obfuscated_index ^ MAGIC_OBFUSCATOR
    nonce = packet[10:22]
    ciphertext = packet[22:]
    key = derive_key(key_index)
    return AESGCM(key).decrypt(nonce, ciphertext, associated_data=RELEASE_ENC_MAGIC)


def encrypt_local(plaintext: str, key_index: int = 0, local_secret: bytes = None) -> str:
    """Same construction as encrypt(), but keyed by a private per-install secret."""
    if not plaintext:
        return ""
    secret = local_secret if local_secret is not None else MASTER_SECRET
    key = derive_key(key_index, secret)
    compressed = zlib.compress(plaintext.encode("utf-8"), level=6)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, compressed, associated_data=None)
    obfuscated_index = key_index ^ MAGIC_OBFUSCATOR
    return f"{obfuscated_index}:{base64_encode(nonce + ciphertext)}"


def decrypt_local(data: str, local_secret: bytes = None) -> str:
    """Decrypt local-at-rest data using a private per-install secret."""
    if not data:
        return ""
    obfuscated_index_str, encoded_packet = data.split(":", 1)
    key_index = int(obfuscated_index_str) ^ MAGIC_OBFUSCATOR
    packet = base64_decode(encoded_packet)
    nonce, ciphertext = packet[:12], packet[12:]
    secrets = []
    if local_secret is not None:
        secrets.append(local_secret)
    secrets.append(MASTER_SECRET)
    for secret in secrets:
        try:
            key = derive_key(key_index, secret)
            decompressed = AESGCM(key).decrypt(nonce, ciphertext, associated_data=None)
            return zlib.decompress(decompressed).decode("utf-8")
        except Exception:
            continue
    raise ValueError("Unable to decrypt local data")
