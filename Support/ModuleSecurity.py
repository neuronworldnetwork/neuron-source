"""
Module Security Functions
Handles CRC calculation, signature generation/verification, and module integrity checks
"""
import hashlib
import hmac
import json
import os
import random
import shutil
import tempfile
import threading
import time
from typing import Tuple, List, Dict, Any, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_DPAPI_PREFIX = b"NEURON_DPAPI_V1\n"


def _dpapi_available() -> bool:
    try:
        import Support.Header as init
        if not getattr(init, 'DPAPI_PROTECT_PRIVATE_KEYS', True):
            return False
        return os.name == 'nt'
    except Exception:
        return False


def dpapi_protect(data: bytes) -> bytes:
    """Windows DPAPI CurrentUser protect. Returns plaintext unchanged if unavailable."""
    if not data or not _dpapi_available():
        return data
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        buffer = ctypes.create_string_buffer(data, len(data))
        blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        if not crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return data
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return data


def dpapi_unprotect(data: bytes) -> bytes:
    """Windows DPAPI CurrentUser unprotect."""
    if not data or not _dpapi_available():
        return data
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        buffer = ctypes.create_string_buffer(data, len(data))
        blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        if not crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return data
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return data


def read_private_key_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw.startswith(_DPAPI_PREFIX):
        return dpapi_unprotect(raw[len(_DPAPI_PREFIX):])
    return raw


def write_private_key_bytes(path: str, pem_bytes: bytes) -> None:
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    payload = pem_bytes
    if _dpapi_available():
        protected = dpapi_protect(pem_bytes)
        if protected and protected != pem_bytes:
            payload = _DPAPI_PREFIX + protected
    with open(path, "wb") as handle:
        handle.write(payload)


def protect_existing_private_key_file(path: str) -> None:
    """Re-write a plaintext PEM private key as DPAPI-wrapped (idempotent)."""
    if not path or not os.path.isfile(path) or not _dpapi_available():
        return
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        if raw.startswith(_DPAPI_PREFIX):
            return
        if b'PRIVATE KEY' not in raw:
            return
        write_private_key_bytes(path, raw)
    except Exception:
        pass


def peer_pubkey_fingerprint(pubkey_hex: str) -> str:
    pk = str(pubkey_hex or '').strip().lower()
    if len(pk) != 64:
        return ''
    try:
        return hashlib.sha256(bytes.fromhex(pk)).hexdigest()[:16]
    except Exception:
        return ''


def clear_tls_pin(host: str) -> bool:
    """Clear a TLS TOFU pin for a host (IP or hostname)."""
    try:
        host_key = _tls_pin_host_key(host or '')
        if not host_key:
            return False
        pins = _load_tls_pins()
        if host_key in pins:
            pins.pop(host_key, None)
            _save_tls_pins(pins)
            return True
    except Exception:
        return False
    return False


def clear_tls_pins_for_hosts(*hosts) -> None:
    """Clear TLS pins for any of the given hosts (used when a peer changes IP)."""
    for host in hosts:
        if host:
            clear_tls_pin(str(host))


def calculate_crc(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def calculate_release_hash(data: bytes) -> str:
    return hashlib.sha512(data).hexdigest()


def ensure_release_keypair(privkey_path: str, pubkey_path: str) -> None:
    if os.path.exists(privkey_path) and os.path.exists(pubkey_path):
        protect_existing_private_key_file(privkey_path)
        return

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    os.makedirs(os.path.dirname(privkey_path), exist_ok=True)
    os.makedirs(os.path.dirname(pubkey_path), exist_ok=True)

    write_private_key_bytes(
        privkey_path,
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )

    with open(pubkey_path, "wb") as handle:
        handle.write(
            public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )


def migrate_admin_private_keys() -> None:
    """
    Keep undistributable private keys under ADMIN/keys only.
    Moves leftovers out of System/json; deletes them on peer machines without ADMIN.
    """
    import Support.Header as init
    import shutil

    admin_keys = getattr(init, 'PATH_ADMIN_KEYS', '') or ''
    path_json = getattr(init, 'PATH_JSON', '') or ''
    path_admin = getattr(init, 'PATH_ADMIN', '') or ''
    if not path_json:
        return

    pairs = (
        ('release_signing_key.pem', getattr(init, 'PATH_RELEASE_PRIVKEY', '')),
        ('admin_signing_key.pem', getattr(init, 'PATH_ADMIN_PRIVKEY', '')),
    )
    legacy_admin = os.path.join(path_admin, 'admin_signing_key.pem') if path_admin else ''

    for name, dest in pairs:
        src = os.path.join(path_json, name)
        if name == 'admin_signing_key.pem' and legacy_admin and os.path.isfile(legacy_admin):
            src_candidates = [legacy_admin, src]
        else:
            src_candidates = [src]
        for src_path in src_candidates:
            if not src_path or not os.path.isfile(src_path):
                continue
            if dest and os.path.normcase(os.path.abspath(src_path)) == os.path.normcase(os.path.abspath(dest)):
                continue
            if dest and path_admin and os.path.isdir(path_admin):
                try:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    if not os.path.isfile(dest):
                        shutil.move(src_path, dest)
                    else:
                        os.remove(src_path)
                except Exception:
                    pass
            else:
                # Peer build / no ADMIN folder: never leave master privkeys in System/json
                try:
                    os.remove(src_path)
                except Exception:
                    pass

    for path in (
        getattr(init, 'PATH_ADMIN_PRIVKEY', ''),
        getattr(init, 'PATH_RELEASE_PRIVKEY', ''),
        getattr(init, 'PATH_PEER_PRIVKEY', ''),
        getattr(init, 'PATH_PEER_ECDH_PRIVKEY', ''),
    ):
        protect_existing_private_key_file(path)


def normalize_module_subfolder(sub_folder: str) -> str:
    if not sub_folder:
        return ""

    parts = []
    for part in str(sub_folder).replace("\\", "/").split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            continue
        if part.lower() == "neuron_pyd":
            continue
        parts.append(part)
    return "/".join(parts)


def sign_manifest(manifest_bytes: bytes, privkey_path: str) -> bytes:
    private_key = serialization.load_pem_private_key(
        read_private_key_bytes(privkey_path), password=None)
    return private_key.sign(manifest_bytes)


def verify_manifest(manifest_bytes: bytes, signature: bytes, pubkey_path: str) -> bool:
    try:
        with open(pubkey_path, "rb") as handle:
            public_key = serialization.load_pem_public_key(handle.read())
        if not isinstance(public_key, Ed25519PublicKey):
            return False
        public_key.verify(signature, manifest_bytes)
        return True
    except Exception:
        return False


def verify_trusted_signature(signature: str) -> bool:
    # Deprecated digit-pattern check. Real auth is Ed25519 admin_sig on the packet.
    # Always True so legacy AdminConsole digit fields do not block signed deploys.
    return True


def generate_trusted_signature() -> str:
    # Kept for AdminConsole compatibility; value is ignored by nodes (see verify_trusted_signature).
    return "0" * 20


# ---------------------------------------------------------------------------
# Remote AdminConsole authentication (Ed25519). Only used for REMOTE_ADMIN_COMMANDS.
# Normal peer traffic never enters these functions.
# ---------------------------------------------------------------------------

_admin_nonce_lock = None
_admin_seen_nonces = {}


def _get_admin_nonce_state():
    global _admin_nonce_lock, _admin_seen_nonces
    if _admin_nonce_lock is None:
        import threading
        _admin_nonce_lock = threading.Lock()
    return _admin_nonce_lock, _admin_seen_nonces


def ensure_admin_keypair(privkey_path: str, pubkey_path: str) -> bool:
    """Create admin Ed25519 keypair if missing. Returns True if privkey is usable."""
    try:
        if os.path.exists(privkey_path) and os.path.exists(pubkey_path):
            protect_existing_private_key_file(privkey_path)
            return True
        if os.path.exists(privkey_path) and not os.path.exists(pubkey_path):
            private_key = serialization.load_pem_private_key(
                read_private_key_bytes(privkey_path), password=None)
            if not isinstance(private_key, Ed25519PrivateKey):
                return False
            os.makedirs(os.path.dirname(pubkey_path), exist_ok=True)
            with open(pubkey_path, "wb") as handle:
                handle.write(
                    private_key.public_key().public_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PublicFormat.SubjectPublicKeyInfo,
                    )
                )
            protect_existing_private_key_file(privkey_path)
            return True
        ensure_release_keypair(privkey_path, pubkey_path)
        return os.path.exists(privkey_path) and os.path.exists(pubkey_path)
    except Exception:
        return False


def _canonicalize_json_number(item):
    """PHP json_encode turns 1.0 into 1; keep sign/verify hashes aligned."""
    if isinstance(item, bool):
        return item
    if isinstance(item, float) and item.is_integer():
        return int(item)
    return item


def _canonicalize_metadata_value(item):
    """Normalize data1/data2 to the JSON wire form used after park/HTTP/poll."""
    import base64
    if isinstance(item, dict) and '__base64__' in item and len(item) == 1:
        try:
            return base64.b64encode(base64.b64decode(item['__base64__'])).decode('ascii')
        except Exception:
            return {k: _canonicalize_metadata_value(v) for k, v in item.items()}
    if isinstance(item, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(item)).decode('ascii')
    if isinstance(item, tuple):
        return [_canonicalize_metadata_value(sub_item) for sub_item in item]
    if isinstance(item, list):
        return [_canonicalize_metadata_value(sub_item) for sub_item in item]
    if isinstance(item, dict):
        return {k: _canonicalize_metadata_value(v) for k, v in item.items()}
    return _canonicalize_json_number(item)


def _canonicalize_digest_value(item):
    """Normalize data3 so sign and verify hash the same binary payload."""
    import base64
    if isinstance(item, dict) and '__base64__' in item and len(item) == 1:
        try:
            return base64.b64decode(item['__base64__'])
        except Exception:
            return item
    if isinstance(item, (bytes, bytearray, memoryview)):
        return bytes(item)
    if isinstance(item, str):
        if len(item) >= 4:
            try:
                padded = item + ('=' * ((4 - len(item) % 4) % 4))
                decoded = base64.b64decode(padded, validate=True)
                if base64.b64encode(decoded).decode('ascii').rstrip('=') == item.rstrip('='):
                    return decoded
            except Exception:
                pass
        return item
    if isinstance(item, tuple):
        return [_canonicalize_digest_value(sub_item) for sub_item in item]
    if isinstance(item, list):
        return [_canonicalize_digest_value(sub_item) for sub_item in item]
    if isinstance(item, dict):
        return {k: _canonicalize_digest_value(v) for k, v in item.items()}
    return _canonicalize_json_number(item)


def canonicalize_file_section_for_digest(file_section: Dict[str, Any]) -> Dict[str, Any]:
    section = file_section or {}
    return {
        'data1': _canonicalize_metadata_value(section.get('data1')),
        'data2': _canonicalize_metadata_value(section.get('data2')),
        'data3': _canonicalize_digest_value(section.get('data3')),
    }


def admin_payload_digest(file_section: Dict[str, Any]) -> str:
    """Stable digest of packet file payloads after JSON park/HTTP/poll round-trips."""
    normalized = canonicalize_file_section_for_digest(file_section or {})
    hasher = hashlib.sha256()
    data1 = normalized.get("data1")
    data2 = normalized.get("data2")
    data3 = normalized.get("data3")
    hasher.update(json.dumps(
        data1 if data1 is not None else [], default=str,
        separators=(",", ":"), ensure_ascii=True, sort_keys=True).encode("utf-8"))
    hasher.update(b"|")
    hasher.update(json.dumps(
        data2 if data2 is not None else [], default=str,
        separators=(",", ":"), ensure_ascii=True, sort_keys=True).encode("utf-8"))
    hasher.update(b"|")
    items = data3 if data3 is not None else []
    if not isinstance(items, list):
        items = [items]
    for item in items:
        if isinstance(item, (bytes, bytearray, memoryview)):
            hasher.update(hashlib.sha256(bytes(item)).digest())
        else:
            hasher.update(hashlib.sha256(str(item).encode("utf-8", errors="replace")).digest())
    return hasher.hexdigest()


def build_admin_sign_message(command: str, admin_ts, admin_nonce: str, file_section: Dict[str, Any], src_peer_id: str = '') -> bytes:
    digest = admin_payload_digest(file_section or {})
    return f"{command}|{src_peer_id}|{admin_ts}|{admin_nonce}|{digest}".encode("utf-8")


def _load_admin_trusted_peer_id() -> str:
    import Support.Header as init
    pinned = str(getattr(init, 'ADMIN_TRUSTED_PEER_ID', '') or '').strip()
    if pinned:
        return pinned
    path = getattr(init, 'PATH_ADMIN_TRUSTED_PEER', '') or ''
    if not path or not os.path.isfile(path):
        return ''
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return str((data or {}).get('peer_id') or '').strip()
    except Exception:
        return ''


def _save_admin_trusted_peer_id(peer_id: str) -> None:
    import Support.Header as init
    path = getattr(init, 'PATH_ADMIN_TRUSTED_PEER', '') or ''
    peer_key = str(peer_id or '').strip()
    if not path or not peer_key:
        return
    if str(getattr(init, 'ADMIN_TRUSTED_PEER_ID', '') or '').strip():
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump({'peer_id': peer_key}, handle)
    except Exception:
        pass


def _seal_admin_value(value):
    from Support.EncDec import encrypt
    if isinstance(value, str) and value and not value.startswith(('v2:', 'e1:')):
        try:
            return encrypt(value)
        except Exception:
            return value
    if isinstance(value, list):
        return [_seal_admin_value(item) for item in value]
    if isinstance(value, dict):
        return {k: _seal_admin_value(v) for k, v in value.items()}
    return value


def _looks_admin_sealed(value: str) -> bool:
    if not value or not isinstance(value, str):
        return False
    # EncDec MESH_V3_PREFIX is v3:; older seals used v2:/e1:/legacy digit:
    if value.startswith(('v3:', 'v2:', 'e1:')):
        return True
    if ':' not in value:
        return False
    left, right = value.split(':', 1)
    return left.lstrip('-').isdigit() and len(right) > 16


def _unseal_admin_value(value):
    from Support.EncDec import decrypt
    if isinstance(value, str) and _looks_admin_sealed(value):
        try:
            return decrypt(value)
        except Exception:
            return value
    if isinstance(value, list):
        return [_unseal_admin_value(item) for item in value]
    if isinstance(value, dict):
        return {k: _unseal_admin_value(v) for k, v in value.items()}
    return value


def seal_admin_file_section(packet_data: Dict[str, Any]) -> None:
    """Encrypt admin file payload strings after signing (wire confidentiality)."""
    try:
        if not packet_data or 'file' not in packet_data:
            return
        file_section = packet_data.get('file') or {}
        for key in ('data1', 'data2', 'data3', 'name'):
            if key in file_section and file_section[key] is not None:
                file_section[key] = _seal_admin_value(file_section[key])
        packet_data['file'] = file_section
        packet_data.setdefault('header', {})['admin_enc'] = 1
    except Exception:
        pass


def unseal_admin_file_section(queue_item) -> None:
    """Decrypt admin file payload before signature verify when admin_enc is set."""
    try:
        header = getattr(queue_item, 'header', None) or {}
        if not header.get('admin_enc'):
            return
        file_section = getattr(queue_item, 'file', None)
        if not isinstance(file_section, dict):
            return
        for key in ('data1', 'data2', 'data3', 'name'):
            if key in file_section and file_section[key] is not None:
                file_section[key] = _unseal_admin_value(file_section[key])
    except Exception:
        pass


def _canonical_admin_ts(value) -> int:
    """Admin sign/verify must use the same integer epoch seconds (not float time.time())."""
    if value is None:
        return int(time.time())
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(time.time())


def sign_admin_packet(packet_data: Dict[str, Any], privkey_path: str) -> bool:
    """Attach admin_sig / admin_ts / admin_nonce to packet_data['header']."""
    try:
        if not packet_data or "header" not in packet_data:
            return False
        if not os.path.exists(privkey_path):
            return False
        header = packet_data["header"]
        command = header.get("command", "")
        src_peer_id = str(header.get("src_peer_id") or "").strip()
        # Header timestamp is often time.time() float; verify always uses int().
        # Signing the float string made every REMOTE_ADMIN_COMMAND fail peer verify.
        admin_ts = _canonical_admin_ts(header.get("timestamp"))
        admin_nonce = os.urandom(16).hex()
        message = build_admin_sign_message(
            command, admin_ts, admin_nonce, packet_data.get("file") or {}, src_peer_id)
        signature = sign_manifest(message, privkey_path)
        header["admin_ts"] = admin_ts
        header["admin_nonce"] = admin_nonce
        header["admin_sig"] = signature.hex()
        return True
    except Exception:
        return False


def verify_admin_packet(queue_item, pubkey_path: str, max_skew_seconds: int = 600) -> bool:
    """Verify Ed25519 admin signature on an inbound queue item. Fast-fail on missing fields."""
    try:
        if not os.path.exists(pubkey_path):
            return False
        unseal_admin_file_section(queue_item)
        header = getattr(queue_item, "header", None) or {}
        file_section = getattr(queue_item, "file", None) or {}
        command = header.get("command", "")
        src_peer_id = str(header.get("src_peer_id") or "").strip()
        admin_sig = header.get("admin_sig")
        admin_ts = header.get("admin_ts", header.get("timestamp"))
        admin_nonce = header.get("admin_nonce")
        if not admin_sig or admin_ts is None or not admin_nonce:
            return False
        try:
            admin_ts_int = _canonical_admin_ts(admin_ts)
        except (TypeError, ValueError):
            return False
        if abs(int(time.time()) - admin_ts_int) > int(max_skew_seconds):
            return False
        trusted = _load_admin_trusted_peer_id()
        if trusted and src_peer_id and src_peer_id != trusted:
            return False
        if trusted and not src_peer_id:
            return False
        lock, seen = _get_admin_nonce_state()
        with lock:
            now = time.time()
            stale = [k for k, exp in seen.items() if exp < now]
            for k in stale:
                del seen[k]
            if admin_nonce in seen:
                return False
        message = build_admin_sign_message(
            command, admin_ts_int, str(admin_nonce), file_section, src_peer_id)
        try:
            signature = bytes.fromhex(str(admin_sig))
        except (TypeError, ValueError):
            return False
        if not verify_manifest(message, signature, pubkey_path):
            return False
        with lock:
            seen[admin_nonce] = now + float(max_skew_seconds)
            if len(seen) > 4000:
                for k in list(seen.keys())[:1000]:
                    del seen[k]
        if src_peer_id and not trusted:
            _save_admin_trusted_peer_id(src_peer_id)
        return True
    except Exception:
        return False


def admin_auth_from_header(header: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract passthrough kwargs for re-forwarding a signed admin command."""
    if not header:
        return {}
    sig = header.get("admin_sig")
    ts = header.get("admin_ts", header.get("timestamp"))
    nonce = header.get("admin_nonce")
    if sig and ts is not None and nonce:
        return {"admin_sig": sig, "admin_ts": ts, "admin_nonce": nonce}
    return {}


# ---------------------------------------------------------------------------
# Soft peer identity (Ed25519). Unsigned packets remain accepted for legacy peers.
# When a peer_id has a stored pubkey, inbound peer_sig must verify.
# ---------------------------------------------------------------------------
_peer_private_key = None
_peer_pubkey_hex = None
_peer_pubkeys_cache = None
_peer_pubkeys_lock = threading.Lock()
_peer_nonce_lock = threading.Lock()
_peer_seen_nonces = {}
_peer_ecdh_private_key = None
_peer_ecdh_pubkey_hex = None
_peer_ecdh_secrets_cache = None
_peer_ecdh_secrets_lock = threading.Lock()


def _local_machine_id() -> str:
    """Stable per-PC id so a folder copy onto another machine regenerates keys."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value or "").strip()
    except Exception:
        return ""


def _node_local_crypto_paths() -> list:
    """Per-node secrets/keys that must never be reused across installs or machines."""
    import Support.Header as init
    paths = [
        getattr(init, "PATH_PEER_PRIVKEY", ""),
        getattr(init, "PATH_PEER_PUBKEY", ""),
        getattr(init, "PATH_PEER_ECDH_PRIVKEY", ""),
        getattr(init, "PATH_PEER_ECDH_PUBKEY", ""),
        getattr(init, "PATH_PEER_PUBKEYS", ""),
        getattr(init, "PATH_PEER_ECDH_SECRETS", ""),
        getattr(init, "PATH_MESH_SECRET", ""),
        getattr(init, "PATH_ENCDEC_LOCAL_SECRET", ""),
        getattr(init, "PATH_CHUNK_STREAM_SECRET", ""),
        getattr(init, "PATH_WEBRTC_ROOM_SECRET", ""),
        getattr(init, "PATH_PROJECTOR_SECRET", ""),
        getattr(init, "PATH_TLS_PINS", ""),
        getattr(init, "PATH_PEER_CRYPTO_OWNER", ""),
    ]
    return [p for p in paths if p]


def _clear_peer_crypto_runtime_caches() -> None:
    global _peer_private_key, _peer_pubkey_hex, _peer_pubkeys_cache
    global _peer_ecdh_private_key, _peer_ecdh_pubkey_hex, _peer_ecdh_secrets_cache
    _peer_private_key = None
    _peer_pubkey_hex = None
    _peer_pubkeys_cache = None
    _peer_ecdh_private_key = None
    _peer_ecdh_pubkey_hex = None
    _peer_ecdh_secrets_cache = None
    try:
        from Support.EncDec import clear_secret_caches
        clear_secret_caches()
    except Exception:
        pass


def wipe_node_local_crypto() -> None:
    """Delete this node's private keys and local secrets so fresh ones are generated."""
    for path in _node_local_crypto_paths():
        try:
            if path and os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass
    _clear_peer_crypto_runtime_caches()


def _read_peer_crypto_owner() -> dict:
    import Support.Header as init
    path = getattr(init, "PATH_PEER_CRYPTO_OWNER", "") or ""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_peer_crypto_owner(peer_id: str, machine_id: str) -> None:
    import Support.Header as init
    path = getattr(init, "PATH_PEER_CRYPTO_OWNER", "") or ""
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"peer_id": str(peer_id or "").strip(), "machine_id": str(machine_id or "").strip()}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def _resolve_me_peer_id() -> str:
    """Read Me.id from memory or DB. Empty until SetupWizard assigns identity."""
    try:
        import Support.Header as init
        me = getattr(init, "ME", None)
        if me is not None:
            try:
                value = me["id"]
            except Exception:
                value = None
            if value:
                return str(value).strip()
    except Exception:
        pass
    try:
        import Support.Header as init
        from Support.Utils import query_database
        rows = query_database(init.pool_main_db, "SELECT id FROM Me LIMIT 1", ())
        if rows and rows[0] and rows[0][0]:
            return str(rows[0][0]).strip()
    except Exception:
        pass
    return ""


def ensure_node_local_crypto_for_peer_id(peer_id: str = None, force_reset: bool = False) -> bool:
    """
    Guarantee distinct self-generated node-local crypto.

    Developer never issues peer private keys. Each install generates its own
    Ed25519/X25519 keys and secrets. Neuron.rar must not ship them; SetupWizard
    and machine binding regenerate if a viral/folder copy reuses another node's files.
    """
    resolved = str(peer_id or "").strip() or _resolve_me_peer_id()
    machine_id = _local_machine_id()
    owner = _read_peer_crypto_owner()
    owner_peer = str(owner.get("peer_id") or "").strip()
    owner_machine = str(owner.get("machine_id") or "").strip()

    must_reset = bool(force_reset)
    if owner_peer and resolved and owner_peer != resolved:
        must_reset = True
    if owner_machine and machine_id and owner_machine != machine_id:
        must_reset = True

    if must_reset:
        wipe_node_local_crypto()

    ok_identity = ensure_peer_identity_keypair()
    ok_ecdh = ensure_peer_ecdh_keypair()
    if resolved:
        _write_peer_crypto_owner(resolved, machine_id)
    return bool(ok_identity and ok_ecdh)


def ensure_peer_identity_keypair(privkey_path: str = None, pubkey_path: str = None) -> bool:
    import Support.Header as init
    priv = privkey_path or getattr(init, 'PATH_PEER_PRIVKEY', '')
    pub = pubkey_path or getattr(init, 'PATH_PEER_PUBKEY', '')
    if not priv or not pub:
        return False
    if os.path.exists(priv):
        try:
            private_key = serialization.load_pem_private_key(
                read_private_key_bytes(priv), password=None)
            if not isinstance(private_key, Ed25519PrivateKey):
                raise ValueError("not ed25519")
        except Exception:
            try:
                os.remove(priv)
            except Exception:
                pass
            try:
                if pub and os.path.isfile(pub):
                    os.remove(pub)
            except Exception:
                pass
            _clear_peer_crypto_runtime_caches()
    return ensure_admin_keypair(priv, pub)


def ensure_peer_ecdh_keypair(privkey_path: str = None, pubkey_path: str = None) -> bool:
    import Support.Header as init
    priv = privkey_path or getattr(init, 'PATH_PEER_ECDH_PRIVKEY', '')
    pub = pubkey_path or getattr(init, 'PATH_PEER_ECDH_PUBKEY', '')
    if not priv or not pub:
        return False
    if os.path.exists(priv) and os.path.exists(pub):
        try:
            private_key = serialization.load_pem_private_key(
                read_private_key_bytes(priv), password=None)
            if not isinstance(private_key, X25519PrivateKey):
                raise ValueError("not x25519")
            protect_existing_private_key_file(priv)
            return True
        except Exception:
            try:
                os.remove(priv)
            except Exception:
                pass
            try:
                os.remove(pub)
            except Exception:
                pass
            _clear_peer_crypto_runtime_caches()
    try:
        private_key = X25519PrivateKey.generate()
        public_key = private_key.public_key()
        os.makedirs(os.path.dirname(priv), exist_ok=True)
        os.makedirs(os.path.dirname(pub), exist_ok=True)
        write_private_key_bytes(
            priv,
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ),
        )
        with open(pub, "wb") as handle:
            handle.write(
                public_key.public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            )
        protect_existing_private_key_file(priv)
        return True
    except Exception:
        return False


def _load_peer_ecdh_private_key():
    global _peer_ecdh_private_key, _peer_ecdh_pubkey_hex
    if _peer_ecdh_private_key is not None and _peer_ecdh_pubkey_hex:
        return _peer_ecdh_private_key
    import Support.Header as init
    if not ensure_peer_ecdh_keypair():
        return None
    try:
        private_key = serialization.load_pem_private_key(
            read_private_key_bytes(init.PATH_PEER_ECDH_PRIVKEY), password=None)
        if not isinstance(private_key, X25519PrivateKey):
            return None
        _peer_ecdh_private_key = private_key
        _peer_ecdh_pubkey_hex = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()
        return _peer_ecdh_private_key
    except Exception:
        return None


def get_local_peer_ecdh_pubkey_hex() -> str:
    global _peer_ecdh_pubkey_hex
    if _peer_ecdh_pubkey_hex:
        return _peer_ecdh_pubkey_hex
    _load_peer_ecdh_private_key()
    return _peer_ecdh_pubkey_hex or ''


def _load_peer_ecdh_secrets() -> dict:
    global _peer_ecdh_secrets_cache
    import Support.Header as init
    with _peer_ecdh_secrets_lock:
        if _peer_ecdh_secrets_cache is not None:
            return _peer_ecdh_secrets_cache
        data = {}
        path = getattr(init, 'PATH_PEER_ECDH_SECRETS', '') or ''
        if path and os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8') as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    data = {str(k): str(v).lower() for k, v in loaded.items() if k and v}
            except Exception:
                data = {}
        _peer_ecdh_secrets_cache = data
        return data


def _save_peer_ecdh_secrets(data: dict) -> None:
    global _peer_ecdh_secrets_cache
    import Support.Header as init
    path = getattr(init, 'PATH_PEER_ECDH_SECRETS', '') or ''
    if not path:
        return
    with _peer_ecdh_secrets_lock:
        _peer_ecdh_secrets_cache = dict(data)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump(_peer_ecdh_secrets_cache, handle)
        except Exception:
            pass


def store_peer_ecdh_secret(peer_id: str, secret_hex: str) -> None:
    peer_key = str(peer_id or '').strip()
    secret = str(secret_hex or '').strip().lower()
    if not peer_key or len(secret) != 64:
        return
    try:
        bytes.fromhex(secret)
    except ValueError:
        return
    secrets = _load_peer_ecdh_secrets()
    if secrets.get(peer_key) == secret:
        return
    secrets[peer_key] = secret
    _save_peer_ecdh_secrets(secrets)


def get_peer_ecdh_secret(peer_id: str) -> bytes:
    peer_key = str(peer_id or '').strip()
    if not peer_key:
        return b''
    secret_hex = _load_peer_ecdh_secrets().get(peer_key)
    if not secret_hex:
        return b''
    try:
        return bytes.fromhex(secret_hex)
    except ValueError:
        return b''


def iter_peer_ecdh_secrets() -> List[bytes]:
    out = []
    for secret_hex in _load_peer_ecdh_secrets().values():
        try:
            out.append(bytes.fromhex(secret_hex))
        except ValueError:
            continue
    return out


def derive_peer_ecdh_shared_secret(remote_pubkey_hex: str) -> bytes:
    private_key = _load_peer_ecdh_private_key()
    if private_key is None:
        return b''
    pk = str(remote_pubkey_hex or '').strip().lower()
    if len(pk) != 64:
        return b''
    try:
        remote = X25519PublicKey.from_public_bytes(bytes.fromhex(pk))
        shared = private_key.exchange(remote)
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b'neuron-peer-ecdh-v1',
            info=b'neuron-peer-payload',
        ).derive(shared)
    except Exception:
        return b''


def learn_peer_ecdh_secret(peer_id: str, remote_pubkey_hex: str) -> None:
    secret = derive_peer_ecdh_shared_secret(remote_pubkey_hex)
    if secret:
        store_peer_ecdh_secret(peer_id, secret.hex())


def _load_peer_private_key():
    global _peer_private_key, _peer_pubkey_hex
    if _peer_private_key is not None and _peer_pubkey_hex:
        return _peer_private_key
    import Support.Header as init
    if not ensure_peer_identity_keypair():
        return None
    try:
        private_key = serialization.load_pem_private_key(
            read_private_key_bytes(init.PATH_PEER_PRIVKEY), password=None)
        if not isinstance(private_key, Ed25519PrivateKey):
            return None
        _peer_private_key = private_key
        _peer_pubkey_hex = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()
        return _peer_private_key
    except Exception:
        return None


def get_local_peer_pubkey_hex() -> str:
    global _peer_pubkey_hex
    if _peer_pubkey_hex:
        return _peer_pubkey_hex
    _load_peer_private_key()
    return _peer_pubkey_hex or ''


def _load_peer_pubkeys() -> dict:
    global _peer_pubkeys_cache
    import Support.Header as init
    with _peer_pubkeys_lock:
        if _peer_pubkeys_cache is not None:
            return _peer_pubkeys_cache
        data = {}
        path = getattr(init, 'PATH_PEER_PUBKEYS', '') or ''
        if path and os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8') as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    data = {str(k): str(v).lower() for k, v in loaded.items() if k and v}
            except Exception:
                data = {}
        _peer_pubkeys_cache = data
        return data


def _save_peer_pubkeys(data: dict) -> None:
    global _peer_pubkeys_cache
    import Support.Header as init
    path = getattr(init, 'PATH_PEER_PUBKEYS', '') or ''
    if not path:
        return
    with _peer_pubkeys_lock:
        _peer_pubkeys_cache = dict(data)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump(_peer_pubkeys_cache, handle)
        except Exception:
            pass


def _peer_row_identity(peer) -> tuple:
    """Return (peer_id, ip, ip_local) from a Peers row stored as dict or list/tuple."""
    if isinstance(peer, dict):
        return (
            str(peer.get('peer_id') or peer.get('id') or '').strip(),
            str(peer.get('ip') or '').strip(),
            str(peer.get('ip_local') or '').strip(),
        )
    if isinstance(peer, (list, tuple)) and len(peer) > 6:
        return (
            str(peer[1] or '').strip(),
            str(peer[5] or '').strip(),
            str(peer[6] or '').strip(),
        )
    return ('', '', '')


def _me_peer_id() -> str:
    try:
        import Support.Header as init
        me = getattr(init, 'ME', None) or {}
        if isinstance(me, dict):
            return str(me.get('id') or me.get('peer_id') or '').strip()
        if isinstance(me, (list, tuple)) and len(me) > 0:
            return str(me[0] or '').strip()
        if hasattr(me, 'get'):
            return str(me.get('id') or me.get('peer_id') or '').strip()
    except Exception:
        pass
    return ''


def _peer_id_known(peer_id: str) -> bool:
    """True if peer_id is this node or appears in the Peers list (any IP)."""
    peer_key = str(peer_id or '').strip()
    if not peer_key:
        return False
    if _me_peer_id() == peer_key:
        return True
    try:
        import Support.Header as init
        for peer in getattr(init, 'PEERS', None) or []:
            row_peer_id, _, _ = _peer_row_identity(peer)
            if row_peer_id == peer_key:
                return True
    except Exception:
        return False
    return False


def _peer_endpoint_known(peer_id: str, src_ip: str = '', src_ip_local: str = '') -> bool:
    """True if peer_id is Me or a Peers row whose ip/ip_local matches the packet source."""
    peer_key = str(peer_id or '').strip()
    if not peer_key:
        return False
    try:
        if _me_peer_id() == peer_key:
            return True
        import Support.Header as init
        peers = getattr(init, 'PEERS', None) or []
        sip = str(src_ip or '').strip()
        slip = str(src_ip_local or '').strip()
        for peer in peers:
            row_peer_id, pip, plip = _peer_row_identity(peer)
            if row_peer_id != peer_key:
                continue
            if not sip and not slip:
                return True
            if sip and sip in (pip, plip):
                return True
            if slip and slip in (pip, plip):
                return True
            return False
    except Exception:
        return False
    return False


def _unique_peer_id_for_public_ip(public_ip: str) -> str:
    """Return peer_id when exactly one Peers row has this public IP."""
    sip = str(public_ip or '').strip()
    if not sip or sip in ('127.0.0.1', '::1'):
        return ''
    try:
        import Support.Header as init
        found = []
        for peer in getattr(init, 'PEERS', None) or []:
            row_peer_id, pip, _plip = _peer_row_identity(peer)
            if not row_peer_id:
                continue
            # Match public IP only — local IPs collide across unrelated home LANs.
            if sip == str(pip or '').strip():
                if row_peer_id not in found:
                    found.append(row_peer_id)
            if len(found) > 1:
                return ''
        return found[0] if len(found) == 1 else ''
    except Exception:
        return ''


def _peer_id_for_image_basename(basename: str) -> str:
    """If poll/id equals a peer avatar basename (id.png), return that peer's peer_id."""
    key = str(basename or '').strip()
    if not key or '.' in key:
        return ''
    try:
        import Support.Header as init
        image_names = {key, key + '.png', key + '.PNG', key + '.jpg', key + '.webp'}
        found = []
        for peer in getattr(init, 'PEERS', None) or []:
            if isinstance(peer, dict):
                row_peer_id = str(peer.get('peer_id') or '').strip()
                image = str(peer.get('image') or '').strip()
            elif isinstance(peer, (list, tuple)) and len(peer) > 13:
                row_peer_id = str(peer[1] or '').strip()
                image = str(peer[13] or '').strip()
            else:
                continue
            if row_peer_id and image in image_names:
                if row_peer_id not in found:
                    found.append(row_peer_id)
            if len(found) > 1:
                return ''
        return found[0] if len(found) == 1 else ''
    except Exception:
        return ''


def resolve_poll_mailbox_peer_id(peer_id: str, remote_addr: str = '',
                                 token: str = '', expires: str = '',
                                 claim_ip: str = '', claim_ip_local: str = '') -> str:
    """Map a poll peer_id to the parked-folder peer_id.

    Pollers sometimes send a stale/renamed id (or an avatar basename). Prefer the
    Peers row for this public IP, then image-basename match, then proof-key match,
    else the given id.

    Never remap a known peer_id to another peer just because Netskope egress
    REMOTE_ADDR equals that other peer's Peers.ip (common for multiple office peers).
    """
    pid = str(peer_id or '').strip()
    addr = str(remote_addr or '').strip()
    cip = str(claim_ip or '').strip()
    clip = str(claim_ip_local or '').strip()
    if pid and _peer_id_known(pid):
        return pid
    # Advertised Me addresses beat shared egress IP remapping.
    if pid and (cip or clip) and _peer_endpoint_known(pid, cip, clip):
        return pid
    if pid and _peer_observed_ip_known(pid, addr):
        return pid
    by_ip = _unique_peer_id_for_public_ip(addr)
    if by_ip:
        return by_ip
    by_image = _peer_id_for_image_basename(pid)
    if by_image:
        return by_image
    if token:
        by_proof = _peer_id_matching_poll_proof(pid, token, expires)
        if by_proof:
            return by_proof
    return pid


def _peer_id_matching_poll_proof(message_peer_id: str, token: str, expires: str) -> str:
    """Return pinned peer_id whose key verifies poll|<message_peer_id>|expires."""
    pid = str(message_peer_id or '').strip()
    if not pid or not token:
        return ''
    if verify_poll_proof(pid, token, expires):
        return pid
    for key_id in list(_load_peer_pubkeys().keys()):
        if key_id == pid:
            continue
        if verify_poll_proof(pid, token, expires, key_peer_id=key_id):
            return key_id
    return ''


_peer_observed_ips_cache = None
_peer_observed_ips_lock = threading.Lock()
_PEER_OBSERVED_IP_TTL = 7 * 24 * 3600

_peer_poll_heartbeats_lock = threading.Lock()
_PEER_POLL_HEARTBEAT_TTL = 600
_PEER_POLL_FLUSH_INTERVAL = 2.0
_peer_poll_heartbeats_mem = {}
_peer_poll_last_flush = 0.0
_peer_poll_disk_loaded = False


def _peer_poll_heartbeats_path() -> str:
    try:
        import Support.Header as init
        base = getattr(init, 'PATH_PEER_PUBKEYS', '') or ''
        if base:
            return os.path.join(os.path.dirname(base), 'peer_poll_heartbeats.json')
    except Exception:
        pass
    return ''


def _prune_peer_poll_heartbeats(data: dict, now: float) -> dict:
    cutoff = now - _PEER_POLL_HEARTBEAT_TTL
    out = {}
    for key, val in (data or {}).items():
        try:
            ts = float(val)
        except (TypeError, ValueError):
            continue
        if ts >= cutoff:
            out[str(key)] = ts
    return out


def _ensure_peer_poll_heartbeats_loaded_unlocked(path: str) -> None:
    global _peer_poll_disk_loaded, _peer_poll_heartbeats_mem
    if _peer_poll_disk_loaded:
        return
    _peer_poll_disk_loaded = True
    if not path or not os.path.isfile(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            _peer_poll_heartbeats_mem = _prune_peer_poll_heartbeats(loaded, time.time())
    except Exception:
        pass


def _flush_peer_poll_heartbeats_unlocked(path: str, force: bool = False) -> None:
    """Write in-memory heartbeats to disk (caller holds lock)."""
    global _peer_poll_last_flush, _peer_poll_heartbeats_mem
    if not path:
        return
    now = time.time()
    if not force and (now - _peer_poll_last_flush) < _PEER_POLL_FLUSH_INTERVAL:
        return
    _peer_poll_heartbeats_mem = _prune_peer_poll_heartbeats(_peer_poll_heartbeats_mem, now)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(_peer_poll_heartbeats_mem, handle)
        _peer_poll_last_flush = now
    except Exception:
        pass


def remember_peer_poll_heartbeat(peer_id: str, when: float = None) -> None:
    """Record a successful firewall_poll so UI/alive checks see reverse peers online.

    Memory-first with coalesced disk flush. Full rewrite on every poll was locking
    gevent request handlers and starving NeuronProjector page serving.
    """
    global _peer_poll_heartbeats_mem
    pid = str(peer_id or '').strip()
    if not pid:
        return
    try:
        from Support.Utils import get_timestamp_in_seconds
        ts = float(when if when is not None else get_timestamp_in_seconds())
    except Exception:
        ts = float(time.time())
    path = _peer_poll_heartbeats_path()
    with _peer_poll_heartbeats_lock:
        _ensure_peer_poll_heartbeats_loaded_unlocked(path)
        _peer_poll_heartbeats_mem[pid] = ts
        _flush_peer_poll_heartbeats_unlocked(path, force=False)


def load_peer_poll_heartbeats() -> dict:
    """Return {peer_id: last_poll_ts} from memory (and disk once)."""
    path = _peer_poll_heartbeats_path()
    with _peer_poll_heartbeats_lock:
        _ensure_peer_poll_heartbeats_loaded_unlocked(path)
        return dict(_prune_peer_poll_heartbeats(_peer_poll_heartbeats_mem, time.time()))


def _peer_observed_ips_path() -> str:
    import Support.Header as init
    base = getattr(init, 'PATH_PEER_PUBKEYS', '') or ''
    if base:
        return os.path.join(os.path.dirname(base), 'peer_observed_ips.json')
    return ''


def _load_peer_observed_ips() -> dict:
    global _peer_observed_ips_cache
    with _peer_observed_ips_lock:
        if _peer_observed_ips_cache is not None:
            return _peer_observed_ips_cache
        data = {}
        path = _peer_observed_ips_path()
        if path and os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8') as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = {}
        _peer_observed_ips_cache = data
        return data


def _save_peer_observed_ips(data: dict) -> None:
    global _peer_observed_ips_cache
    path = _peer_observed_ips_path()
    if not path:
        return
    with _peer_observed_ips_lock:
        _peer_observed_ips_cache = dict(data)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump(_peer_observed_ips_cache, handle)
        except Exception:
            pass


def remember_peer_observed_ip(peer_id: str, remote_addr: str) -> None:
    """Remember CGNAT/proxy egress IP used by a known peer (poll/post), not Me.ip."""
    pid = str(peer_id or '').strip()
    addr = str(remote_addr or '').strip()
    if not pid or not addr or addr in ('127.0.0.1', '::1'):
        return
    if not _peer_id_known(pid):
        return
    now = int(time.time())
    data = dict(_load_peer_observed_ips())
    entries = data.get(pid) if isinstance(data.get(pid), list) else []
    cleaned = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        ip = str(item.get('ip') or '').strip()
        seen = int(item.get('seen') or 0)
        if not ip or now - seen > _PEER_OBSERVED_IP_TTL:
            continue
        if ip != addr:
            cleaned.append({'ip': ip, 'seen': seen})
    cleaned.append({'ip': addr, 'seen': now})
    data[pid] = cleaned[-8:]
    _save_peer_observed_ips(data)


def _peer_observed_ip_known(peer_id: str, remote_addr: str) -> bool:
    pid = str(peer_id or '').strip()
    addr = str(remote_addr or '').strip()
    if not pid or not addr:
        return False
    now = int(time.time())
    entries = _load_peer_observed_ips().get(pid) or []
    if not isinstance(entries, list):
        return False
    for item in entries:
        if not isinstance(item, dict):
            continue
        if str(item.get('ip') or '').strip() != addr:
            continue
        seen = int(item.get('seen') or 0)
        if now - seen <= _PEER_OBSERVED_IP_TTL:
            return True
    return False


def learn_peer_poll_contact(peer_id: str, remote_addr: str = '',
                            claim_ip: str = '', claim_ip_local: str = '') -> None:
    """After a successful poll: remember egress and refresh Peers.ip from claim.

    Netskope/CGNAT peers advertise Me.ip that churns and differs from REMOTE_ADDR.
    Mailbox is always peer_id; stored IPs are updated so the next claim matches.
    """
    pid = str(peer_id or '').strip()
    if not pid or not _peer_id_known(pid):
        return
    addr = str(remote_addr or '').strip()
    cip = str(claim_ip or '').strip()
    clip = str(claim_ip_local or '').strip()
    if addr and addr not in ('127.0.0.1', '::1'):
        remember_peer_observed_ip(pid, addr)
    if not cip and not clip:
        return
    try:
        import Support.Header as init
        from Support.Utils import query_database
        rows = None
        if getattr(init, 'pool_main_db', None):
            try:
                rows = query_database(
                    init.pool_main_db,
                    "SELECT ip, ip_local FROM Peers WHERE peer_id = ?",
                    (pid,))
            except Exception:
                rows = None
        old_ip = str((rows[0][0] if rows and rows[0] else '') or '').strip()
        old_local = str((rows[0][1] if rows and rows[0] else '') or '').strip()
        new_ip = cip or old_ip
        new_local = clip or old_local
        if new_ip == old_ip and new_local == old_local:
            return
        if getattr(init, 'pool_main_db', None):
            query_database(
                init.pool_main_db,
                "UPDATE Peers SET ip = ?, ip_local = ? WHERE peer_id = ?",
                (new_ip, new_local, pid))
        try:
            from Support.Dictionaries import set_dictionary
            if init.PEERS:
                set_dictionary(
                    init.PEERS,
                    search_conditions={'peer_id': pid},
                    updates={'ip': new_ip, 'ip_local': new_local})
        except Exception:
            pass
    except Exception:
        pass


def authorize_poll_request(peer_id: str, token: str, expires: str, remote_addr: str = '',
                           claim_ip: str = '', claim_ip_local: str = '',
                           claim_pk: str = '') -> bool:
    """Authorize parked-mail poll for every release topology.

    Cases (mailbox is always peer_id; IPs never select the folder):
      1. Valid Ed25519 proof vs pinned key — always OK (Direct/Firewall/Netskope).
      2. Valid proof vs offered pk= on a known peer_id — pin then OK (first contact).
      3. Pin exists but proof bad/missing — refuse (no IP bypass once pinned).
      4. Token or pk= offered but unverified — refuse (no forge-then-bootstrap).
      5. No pin, no proof, known peer_id — OK (Netskope first-contact / IP churn).
      6. Stale/alias poll id — resolve mailbox then same rules.
    On success: remember egress and refresh Peers.ip from claim.
    """
    pid = str(peer_id or '').strip()
    if not pid:
        return False
    cip = str(claim_ip or '').strip()
    clip = str(claim_ip_local or '').strip()
    cpk = str(claim_pk or '').strip().lower()
    addr = str(remote_addr or '').strip()

    def _accept(target: str) -> bool:
        learn_peer_poll_contact(target, addr, cip, clip)
        return True

    if verify_poll_proof(pid, token, expires):
        return _accept(pid)

    mailbox = resolve_poll_mailbox_peer_id(
        pid, addr, token, expires, claim_ip=cip, claim_ip_local=clip)

    if mailbox and mailbox != pid:
        if verify_poll_proof(pid, token, expires, key_peer_id=mailbox):
            return _accept(mailbox)
        if verify_poll_proof(mailbox, token, expires):
            return _accept(mailbox)

    matched = _peer_id_matching_poll_proof(pid, token, expires)
    if matched:
        return _accept(matched)

    # Bootstrap pin: poller sends pk= + proof; peer_id must already be in Peers/Me.
    target_for_pk = pid if _peer_id_known(pid) else (mailbox if mailbox and _peer_id_known(mailbox) else '')
    if target_for_pk and cpk and len(cpk) == 64 and token:
        if verify_poll_proof(pid, token, expires, pubkey_hex=cpk):
            _apply_verified_peer_pubkey(target_for_pk, cpk, cip or addr, clip or addr)
            return _accept(target_for_pk)
        if mailbox and mailbox != pid and verify_poll_proof(mailbox, token, expires, pubkey_hex=cpk):
            _apply_verified_peer_pubkey(mailbox, cpk, cip or addr, clip or addr)
            return _accept(mailbox)
        # Key was offered but proof failed — do not soft-bootstrap.
        return False

    stored = _load_peer_pubkeys().get(pid)
    if stored:
        return False
    if mailbox and mailbox != pid and _load_peer_pubkeys().get(mailbox):
        # Canonical id is pinned; unsigned/unpinned alias must not bypass.
        return False

    # Proof token present but nothing verified it — refuse (no forge-then-bootstrap).
    if token:
        return False

    target = pid if _peer_id_known(pid) else ''
    if not target and mailbox and _peer_id_known(mailbox):
        target = mailbox
    if not target:
        if mailbox and addr and _unique_peer_id_for_public_ip(addr) == mailbox:
            return _accept(mailbox)
        uniq = _unique_peer_id_for_public_ip(addr) if addr else ''
        if uniq and uniq == pid:
            return _accept(pid)
        return False

    # No pin yet, no proof yet: known peer_id is enough (Netskope first contact /
    # claim/egress churn). Next successful poll with pk=+t pins and tightens.
    if addr and addr not in ('127.0.0.1', '::1'):
        return _accept(target)
    if cip or clip:
        return _accept(target)
    return False


def store_peer_pubkey(peer_id: str, pubkey_hex: str, src_ip: str = '', src_ip_local: str = '') -> None:
    peer_key = str(peer_id or '').strip()
    pk = str(pubkey_hex or '').strip().lower()
    if not peer_key or len(pk) != 64:
        return
    try:
        bytes.fromhex(pk)
    except ValueError:
        return
    import Support.Header as init
    pins = _load_peer_pubkeys()
    existing = pins.get(peer_key)
    if existing:
        if existing == pk:
            return
        if getattr(init, 'PEER_PUBKEY_LOCK', True):
            return
    if getattr(init, 'PEER_PIN_REQUIRE_KNOWN_ENDPOINT', True):
        # Known peer_id is enough: Netskope src.ip often != Peers.ip at pin time.
        if not (_peer_endpoint_known(peer_key, src_ip, src_ip_local)
                or _peer_id_known(peer_key)
                or _peer_observed_ip_known(peer_key, src_ip)):
            return
    pins[peer_key] = pk
    _save_peer_pubkeys(pins)


def build_peer_sign_message(command: str, src_peer_id, peer_ts, peer_nonce: str, file_section: Dict[str, Any]) -> bytes:
    try:
        peer_ts = int(peer_ts)
    except (TypeError, ValueError):
        peer_ts = 0
    digest = admin_payload_digest(file_section or {})
    return f"{command}|{src_peer_id}|{peer_ts}|{peer_nonce}|{digest}".encode("utf-8")


def sign_peer_packet(packet_data: Dict[str, Any]) -> bool:
    """Attach peer_sig / peer_ts / peer_nonce / peer_pk / peer_ek using this node's identity keys.

    Always sign when keys exist — including PEER_UNSIGNED_OK_COMMANDS (ping, etc.).
    Unsigned-OK only means receivers may accept a missing signature; it must not
    strip peer_pk from the common path or pins never form and poll proofs break.
    """
    try:
        import Support.Header as init
        if not getattr(init, 'PEER_PACKET_SIGNING_ENABLED', True):
            return False
        if not packet_data or 'header' not in packet_data:
            return False
        private_key = _load_peer_private_key()
        if private_key is None:
            return False
        header = packet_data['header']
        command = header.get('command', '')
        src_peer_id = str(header.get('src_peer_id') or '').strip()
        peer_ts = header.get('timestamp')
        if peer_ts is None:
            peer_ts = int(time.time())
        try:
            peer_ts = int(peer_ts)
        except (TypeError, ValueError):
            peer_ts = int(time.time())
        peer_nonce = os.urandom(16).hex()
        message = build_peer_sign_message(
            command, src_peer_id, peer_ts, peer_nonce,
            canonicalize_file_section_for_digest(packet_data.get('file') or {}))
        signature = private_key.sign(message)
        header['peer_ts'] = peer_ts
        header['peer_nonce'] = peer_nonce
        header['peer_sig'] = signature.hex()
        header['peer_pk'] = get_local_peer_pubkey_hex()
        ecdh_pub = get_local_peer_ecdh_pubkey_hex()
        if ecdh_pub:
            header['peer_ek'] = ecdh_pub
        return True
    except Exception:
        return False


def peer_command_allows_unsigned(command) -> bool:
    try:
        import Support.Header as init
        allowed = getattr(init, 'PEER_UNSIGNED_OK_COMMANDS', None) or ()
        return str(command or '') in allowed
    except Exception:
        return False


def peer_command_requires_signature(command) -> bool:
    try:
        import Support.Header as init
        required = getattr(init, 'PEER_MONEY_COMMANDS', None) or ()
        return str(command or '') in required
    except Exception:
        return False


def peer_packet_header_expired(header, max_skew_seconds: int = None) -> bool:
    """True when peer_ts/timestamp is too old to verify (stale parked packet)."""
    try:
        import Support.Header as init
        if max_skew_seconds is None:
            max_skew_seconds = int(getattr(init, 'PEER_SIG_MAX_SKEW_SECONDS', 600))
        if not isinstance(header, dict):
            return False
        peer_ts = header.get('peer_ts', header.get('timestamp'))
        if peer_ts is None:
            return False
        try:
            peer_ts_int = int(float(peer_ts))
        except (TypeError, ValueError):
            return False
        return abs(int(time.time()) - peer_ts_int) > int(max_skew_seconds)
    except Exception:
        return False


def _queue_section_dict(section, fields):
    if isinstance(section, dict):
        return section
    if section is None:
        return {}
    out = {}
    for field in fields:
        if isinstance(section, dict):
            value = section.get(field)
        else:
            value = getattr(section, field, None)
        if value is not None:
            out[field] = value
    return out


def _queue_header_dict(queue_item):
    return _queue_section_dict(
        getattr(queue_item, 'header', None),
        ('command', 'priority', 'timestamp', 'src_peer_id', 'dst_peer_id',
         'peer_sig', 'peer_ts', 'peer_nonce', 'peer_pk', 'peer_ek'),
    )


def _queue_file_dict(queue_item):
    section = getattr(queue_item, 'file', None)
    out = _queue_section_dict(
        section,
        ('data1', 'data2', 'data3', 'crc', 'chk_id', 'chk_num', 'name'),
    )
    if not out.get('chk_id') and section is not None and not isinstance(section, dict):
        chunk_id = getattr(section, 'chunk_id', None)
        if chunk_id is not None:
            out['chk_id'] = chunk_id
    if not out.get('chk_num') and section is not None and not isinstance(section, dict):
        chunk_num = getattr(section, 'chunk_num', None)
        if chunk_num is not None:
            out['chk_num'] = chunk_num
    return out


def sanitize_chunk_id(chunk_id) -> str:
    value = str(chunk_id or '').strip()
    if not value or len(value) > 128:
        return ''
    if '..' in value or '/' in value or '\\' in value:
        return ''
    for ch in value:
        if not (ch.isalnum() or ch in '-_'):
            return ''
    return value


def path_is_inside(child_path: str, parent_path: str) -> bool:
    child = os.path.abspath(child_path)
    parent = os.path.abspath(parent_path)
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False


def url_host_is_known_peer(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(str(url or '')).hostname or '').strip().lower()
    if not host:
        return False
    if host in ('127.0.0.1', 'localhost', '::1'):
        return True
    allowed = set()
    try:
        import Support.Header as init
        me = getattr(init, 'ME', None) or {}
        if isinstance(me, dict):
            for key in ('ip', 'ip_local'):
                val = str(me.get(key) or '').strip().lower()
                if val:
                    allowed.add(val)
        peers = getattr(init, 'PEERS', None) or []
        for peer in peers:
            if not isinstance(peer, dict):
                continue
            for key in ('ip', 'ip_local'):
                val = str(peer.get(key) or '').strip().lower()
                if val:
                    allowed.add(val)
    except Exception:
        return False
    return host in allowed


def sign_device_cookie(device_id: str) -> str:
    import Support.Header as init
    secret = ensure_secret_file(init.PATH_PROJECTOR_SECRET, 32)
    did = str(device_id or '').strip()
    return hmac.new(secret.encode('utf-8'), did.encode('utf-8'), hashlib.sha256).hexdigest()


def verify_device_cookie(device_id: str, token: str) -> bool:
    expected = sign_device_cookie(device_id)
    got = str(token or '').strip()
    if not expected or not got:
        return False
    return hmac.compare_digest(expected, got)


def _apply_verified_peer_pubkey(peer_id: str, pubkey_hex: str, src_ip: str = '', src_ip_local: str = '') -> None:
    """Pin a peer pubkey after its signature verified and the peer is known."""
    peer_key = str(peer_id or '').strip()
    pk = str(pubkey_hex or '').strip().lower()
    if not peer_key or len(pk) != 64:
        return
    try:
        bytes.fromhex(pk)
    except ValueError:
        return
    if not _peer_endpoint_known(peer_key, src_ip, src_ip_local) and not _peer_id_known(peer_key):
        return
    pins = _load_peer_pubkeys()
    if pins.get(peer_key) == pk:
        return
    pins[peer_key] = pk
    _save_peer_pubkeys(pins)


def _verify_peer_signature(pubkey_hex: str, signature: bytes, message: bytes) -> bool:
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(str(pubkey_hex)))
        public_key.verify(signature, message)
        return True
    except Exception:
        return False


def _peer_known_soft_ok(src_peer_id: str, src_ip: str = '', src_ip_local: str = '') -> bool:
    """True when this source is already in Me/Peers (soft identity; non-money only)."""
    if _peer_endpoint_known(src_peer_id, src_ip, src_ip_local):
        return True
    if src_peer_id and _peer_id_known(src_peer_id):
        return True
    if not src_peer_id and src_ip:
        return bool(_unique_peer_id_for_public_ip(src_ip))
    return False


def verify_peer_packet(queue_item, max_skew_seconds: int = None) -> bool:
    """Soft peer identity: money cmds fail-closed; known Me/Peers soft-ok when sig mismatches."""
    command = ''
    try:
        import Support.Header as init
        if max_skew_seconds is None:
            max_skew_seconds = int(getattr(init, 'PEER_SIG_MAX_SKEW_SECONDS', 600))
        require_known_sigs = bool(getattr(init, 'PEER_REQUIRE_KNOWN_SIGNATURES', True))
        header = _queue_header_dict(queue_item)
        file_section = _queue_file_dict(queue_item)
        src = getattr(queue_item, 'src', None) or {}
        if not isinstance(src, dict):
            src = _queue_section_dict(src, ('ip', 'ip_local', 'URL'))
        src_peer_id = str(header.get('src_peer_id') or '').strip()
        src_ip = str(src.get('ip') or '')
        src_ip_local = str(src.get('ip_local') or '')
        stored = _load_peer_pubkeys().get(src_peer_id) if src_peer_id else None
        command = str(header.get('command', '') or '')
        unsigned_ok = peer_command_allows_unsigned(command)
        money_cmd = peer_command_requires_signature(command)
        soft_ok = (not money_cmd) and (
            unsigned_ok or (
                not require_known_sigs and _peer_known_soft_ok(src_peer_id, src_ip, src_ip_local)
            )
        )
        peer_sig = header.get('peer_sig')
        if not peer_sig:
            if money_cmd:
                return False
            if soft_ok:
                return True
            if stored:
                return False
            if src_peer_id and _peer_endpoint_known(src_peer_id, src_ip, src_ip_local):
                return True
            return False
        peer_ts = header.get('peer_ts', header.get('timestamp'))
        peer_nonce = header.get('peer_nonce')
        peer_pk = str(header.get('peer_pk') or '').strip().lower()
        if peer_ts is None or not peer_nonce:
            return False if money_cmd else soft_ok
        if peer_packet_header_expired(header, max_skew_seconds):
            return False if money_cmd else soft_ok
        try:
            peer_ts_int = int(peer_ts)
        except (TypeError, ValueError):
            return False if money_cmd else soft_ok
        try:
            signature = bytes.fromhex(str(peer_sig))
        except Exception:
            return False if money_cmd else soft_ok
        message = build_peer_sign_message(
            command, src_peer_id, peer_ts_int, str(peer_nonce),
            canonicalize_file_section_for_digest(file_section))
        verified_pk = None
        if peer_pk and len(peer_pk) == 64:
            if _verify_peer_signature(peer_pk, signature, message):
                verified_pk = peer_pk
        if not verified_pk and stored and len(stored) == 64:
            if _verify_peer_signature(stored, signature, message):
                verified_pk = stored
        if not verified_pk:
            return False if money_cmd else soft_ok
        pubkey_hex = verified_pk
        with _peer_nonce_lock:
            now = time.time()
            stale = [k for k, exp in _peer_seen_nonces.items() if exp < now]
            for k in stale:
                del _peer_seen_nonces[k]
            nonce_key = f"{src_peer_id}:{peer_nonce}"
            if nonce_key in _peer_seen_nonces:
                return False if money_cmd else soft_ok
            _peer_seen_nonces[nonce_key] = now + float(max_skew_seconds)
            if len(_peer_seen_nonces) > 8000:
                for k in list(_peer_seen_nonces.keys())[:2000]:
                    del _peer_seen_nonces[k]
        if src_peer_id and (not stored or stored != pubkey_hex):
            _apply_verified_peer_pubkey(
                src_peer_id,
                pubkey_hex,
                src_ip=src_ip,
                src_ip_local=src_ip_local,
            )
        peer_ek = str(header.get('peer_ek') or '').strip().lower()
        if src_peer_id and peer_ek:
            learn_peer_ecdh_secret(src_peer_id, peer_ek)
        return True
    except Exception:
        if peer_command_requires_signature(command):
            return False
        if peer_command_allows_unsigned(command):
            return True
        try:
            import Support.Header as init
            if bool(getattr(init, 'PEER_REQUIRE_KNOWN_SIGNATURES', True)):
                return False
            header = _queue_header_dict(queue_item)
            src = getattr(queue_item, 'src', None) or {}
            if not isinstance(src, dict):
                src = _queue_section_dict(src, ('ip', 'ip_local', 'URL'))
            return _peer_known_soft_ok(
                str(header.get('src_peer_id') or '').strip(),
                str(src.get('ip') or ''),
                str(src.get('ip_local') or ''),
            )
        except Exception:
            return False


def ensure_secret_file(path: str, nbytes: int = 32) -> str:
    """Load or create a per-install secret as hex text. Cached callers should store the return."""
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                value = handle.read().strip()
            if value:
                return value
        os.makedirs(os.path.dirname(path), exist_ok=True)
        value = os.urandom(nbytes).hex()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(value)
        return value
    except Exception:
        return os.urandom(nbytes).hex()


def ensure_json_secrets(path: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Load or create a JSON secrets file; missing keys are filled from defaults."""
    data = {}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                data = loaded
    except Exception:
        data = {}
    changed = False
    for key, value in defaults.items():
        if key not in data or data[key] in (None, ""):
            data[key] = value() if callable(value) else value
            changed = True
    if changed or not os.path.exists(path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
        except Exception:
            pass
    return data


def make_stream_token(secret: str, resource_id: str, expires: int) -> str:
    import hmac as _hmac
    msg = f"{resource_id}|{expires}".encode("utf-8")
    return _hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_stream_token(secret: str, resource_id: str, token: str, expires: str, max_skew: int = 0) -> bool:
    import hmac as _hmac
    try:
        exp = int(expires)
    except (TypeError, ValueError):
        return False
    now = int(time.time())
    if exp < now - max_skew:
        return False
    expected = make_stream_token(secret, resource_id, exp)
    return _hmac.compare_digest(expected, str(token or ""))


def make_poll_proof(peer_id: str = '') -> tuple:
    """Ed25519 proof for CGNAT parked-mail poll. Returns (sig_hex, expires, peer_id)."""
    import Support.Header as init
    pid = str(peer_id or _me_peer_id() or '').strip()
    if not pid:
        return '', 0, ''
    private_key = _load_peer_private_key()
    if private_key is None:
        return '', 0, ''
    ttl = int(getattr(init, 'POLL_PROOF_TTL_SECONDS', 300))
    expires = int(time.time()) + max(60, ttl)
    message = f"poll|{pid}|{expires}".encode('utf-8')
    sig = private_key.sign(message).hex()
    return sig, expires, pid


def verify_poll_proof(peer_id: str, token: str, expires: str, key_peer_id: str = '',
                      pubkey_hex: str = '') -> bool:
    """Verify parked-mail poll proof against pinned peer_pubkeys or an offered key.

    key_peer_id: optional pin lookup id when the poller signed with a stale/alias
    peer_id string but the pubkey is stored under the canonical peer_id.
    pubkey_hex: optional one-shot key from poll ?pk= (bootstrap before pin exists).
    """
    pid = str(peer_id or '').strip()
    tok = str(token or '').strip()
    if not pid or not tok:
        return False
    try:
        exp = int(expires)
    except (TypeError, ValueError):
        return False
    if exp < int(time.time()) - 30:
        return False
    key_id = str(key_peer_id or pid).strip() or pid
    stored = str(pubkey_hex or '').strip().lower() or _load_peer_pubkeys().get(key_id)
    if not stored:
        return False
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(str(stored)))
        public_key.verify(bytes.fromhex(tok), f"poll|{pid}|{exp}".encode('utf-8'))
        return True
    except Exception:
        return False


def append_poll_proof_qs(url: str, peer_id: str = '') -> str:
    sig, expires, pid = make_poll_proof(peer_id)
    if not sig or not pid:
        return url
    sep = '&' if ('?' in (url or '')) else '?'
    pk = ''
    try:
        pk = str(get_local_peer_pubkey_hex() or '').strip().lower()
    except Exception:
        pk = ''
    extra = f"e={expires}&t={sig}"
    if pk and len(pk) == 64:
        extra = f"{extra}&pk={pk}"
    if 'peer_id=' in (url or ''):
        return f"{url}{sep}{extra}"
    return f"{url}{sep}peer_id={pid}&{extra}"


def make_peer_resource_proof(resource_id: str) -> tuple:
    """Proof that a known peer is requesting a stream resource. Returns (sig, expires, peer_id)."""
    import Support.Header as init
    rid = str(resource_id or '').strip()
    pid = _me_peer_id()
    if not rid or not pid:
        return '', 0, ''
    private_key = _load_peer_private_key()
    if private_key is None:
        return '', 0, ''
    ttl = int(getattr(init, 'POLL_PROOF_TTL_SECONDS', 300))
    expires = int(time.time()) + max(60, ttl)
    message = f"stream|{pid}|{rid}|{expires}".encode('utf-8')
    return private_key.sign(message).hex(), expires, pid


def verify_peer_resource_proof(resource_id: str, peer_id: str, token: str, expires: str) -> bool:
    pid = str(peer_id or '').strip()
    tok = str(token or '').strip()
    rid = str(resource_id or '').strip()
    if not pid or not tok or not rid:
        return False
    if not _peer_id_known(pid):
        return False
    try:
        exp = int(expires)
    except (TypeError, ValueError):
        return False
    if exp < int(time.time()) - 30:
        return False
    stored = _load_peer_pubkeys().get(pid)
    if not stored:
        return False
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(str(stored)))
        public_key.verify(bytes.fromhex(tok), f"stream|{pid}|{rid}|{exp}".encode('utf-8'))
        return True
    except Exception:
        return False


def make_room_invite_token(room_id: str) -> tuple:
    """Return (token, expires) HMAC for a WebRTC room join URL."""
    import Support.Header as init
    secret = ensure_secret_file(init.PATH_WEBRTC_ROOM_SECRET, 32)
    ttl = int(getattr(init, 'WEBRTC_ROOM_TOKEN_TTL_SECONDS', 7200))
    expires = int(time.time()) + ttl
    return make_stream_token(secret, str(room_id), expires), expires


def verify_room_invite_token(room_id: str, token: str, expires: str) -> bool:
    import Support.Header as init
    secret = ensure_secret_file(init.PATH_WEBRTC_ROOM_SECRET, 32)
    return verify_stream_token(secret, str(room_id), token, expires)


def create_peer_ssl_context():
    """Prefer Neuron CA when present; otherwise CERT_NONE for self-signed mesh TLS."""
    import ssl
    import Support.Header as init
    ctx = ssl.create_default_context()
    cert = os.path.join(getattr(init, 'PATH_SYSTEM', ''), 'Neuron.crt')
    if os.path.isfile(cert):
        try:
            ctx.load_verify_locations(cafile=cert)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_REQUIRED
            return ctx
        except Exception:
            pass
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_tls_pins_cache = None
_tls_pins_lock = threading.Lock()


def _tls_pin_host_key(host: str) -> str:
    return (host or '').strip().lower().split('%')[0]


def _tls_pin_exempt(host: str) -> bool:
    key = _tls_pin_host_key(host)
    return key in ('', '127.0.0.1', 'localhost', '::1')


def _load_tls_pins() -> dict:
    global _tls_pins_cache
    import Support.Header as init
    with _tls_pins_lock:
        if _tls_pins_cache is not None:
            return _tls_pins_cache
        path = getattr(init, 'PATH_TLS_PINS', '') or ''
        data = {}
        if path and os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8') as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    data = {str(k).lower(): str(v) for k, v in loaded.items() if k and v}
            except Exception:
                data = {}
        _tls_pins_cache = data
        return data


def _save_tls_pins(pins: dict) -> None:
    global _tls_pins_cache
    import Support.Header as init
    path = getattr(init, 'PATH_TLS_PINS', '') or ''
    if not path:
        return
    with _tls_pins_lock:
        _tls_pins_cache = dict(pins)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump(_tls_pins_cache, handle)
        except Exception:
            pass


def _peer_cert_der_from_response(response) -> bytes:
    try:
        raw = getattr(response, 'raw', None)
        conn = getattr(raw, 'connection', None) if raw is not None else None
        sock = getattr(conn, 'sock', None) if conn is not None else None
        if sock is None and conn is not None:
            sock = getattr(conn, 'socket', None)
        if sock is not None and hasattr(sock, 'getpeercert'):
            der = sock.getpeercert(binary_form=True)
            if der:
                return der
    except Exception:
        pass
    return b''


def enforce_tls_tofu(url: str, response) -> bool:
    """Learn or enforce HTTPS cert pin for peer hosts. False = discard response (pin mismatch)."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url or '')
        if (parsed.scheme or '').lower() != 'https':
            return True
        host = _tls_pin_host_key(parsed.hostname or '')
        if _tls_pin_exempt(host):
            return True
        der = _peer_cert_der_from_response(response)
        if not der:
            return True
        fp = hashlib.sha256(der).hexdigest()
        pins = _load_tls_pins()
        known = pins.get(host)
        if not known:
            pins[host] = fp
            _save_tls_pins(pins)
            return True
        if hmac.compare_digest(str(known), fp):
            return True
        import Support.Header as init
        # Peer IPs change often and certs are regenerated with them — re-learn automatically.
        if getattr(init, 'TLS_TOFU_RELEARN_ON_MISMATCH', True):
            pins[host] = fp
            _save_tls_pins(pins)
            return True
        return False
    except Exception:
        return True


def peer_https_request(method: str, url: str, **kwargs):
    """requests wrapper: verify=False + TOFU pin learn/enforce for HTTPS peer URLs."""
    import requests as _requests
    kwargs.setdefault('verify', False)
    response = _requests.request(method, url, **kwargs)
    if not enforce_tls_tofu(url, response):
        raise _requests.exceptions.SSLError(f'TLS pin mismatch for {url}')
    return response


# Keep underscore aliases used by the rest of this module.
_calculate_crc = calculate_crc
_calculate_release_hash = calculate_release_hash
_ensure_release_keypair = ensure_release_keypair
_normalize_module_subfolder = normalize_module_subfolder
_sign_manifest = sign_manifest
_verify_manifest = verify_manifest
_verify_trusted_signature = verify_trusted_signature
_generate_trusted_signature = generate_trusted_signature


def _load_release_zip_bytes(sidecar: Dict[str, Any]) -> Optional[bytes]:
    """Plaintext .zip if present, otherwise decrypt the .nrel."""
    from Support.Header import PATH_DISTRIBUTABLE_RELEASES, PATH_RELEASE_PUBKEY

    release_id = sidecar.get('release_id', '')
    if not release_id:
        return None
    zip_path = os.path.join(PATH_DISTRIBUTABLE_RELEASES, release_id + '.zip')
    if os.path.isfile(zip_path):
        with open(zip_path, 'rb') as f:
            return f.read()
    nrel_path = sidecar.get('_nrel_path') or os.path.join(
        PATH_DISTRIBUTABLE_RELEASES, sidecar.get('enc_file') or (release_id + '.nrel')
    )
    if not os.path.isfile(nrel_path):
        return None
    with open(nrel_path, 'rb') as f:
        enc_bytes = f.read()
    ok, _manifest, zip_bytes, _msg = unlock_and_verify_release(enc_bytes, PATH_RELEASE_PUBKEY)
    if ok:
        return zip_bytes
    return None


def merge_cumulative_release(release: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add the latest previously shipped PYD of every module not in this wizard
    selection, and prior SQL/file/script actions tagged with original_release_id.
    Each original_release_id is included once (oldest package first).
    """
    import io
    import zipfile

    current_id = release.get('release_id', '')
    current_modules = list(release.get('modules') or [])
    current_names = {m.get('name') for m in current_modules if m.get('name')}
    current_actions = list(release.get('actions') or [])

    best_modules = {}
    inherited_actions = []
    seen_origins = set()

    previous = list(list_local_releases())
    previous.sort(key=lambda s: _release_version_tuple(s.get('version', '0.0.0')))

    for sc in previous:
        rid = sc.get('release_id', '')
        if not rid or rid == current_id:
            continue
        zip_bytes = _load_release_zip_bytes(sc)
        if not zip_bytes:
            continue
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes), 'r')
        except Exception:
            continue
        try:
            if 'manifest.json' not in zf.namelist():
                continue
            manifest = json.loads(zf.read('manifest.json').decode('utf-8'))

            for mod in manifest.get('modules') or []:
                name = mod.get('name')
                if not name or name in current_names:
                    continue
                ver = str(mod.get('version', '0.0.0'))
                prev = best_modules.get(name)
                if prev and _release_version_tuple(ver) <= _release_version_tuple(prev.get('version', '0')):
                    continue
                rel_path = mod.get('path')
                if not rel_path or rel_path not in zf.namelist():
                    continue
                best_modules[name] = {
                    'name': name,
                    'sub_folder': _normalize_module_subfolder(mod.get('sub_folder') or ''),
                    'version': ver,
                    'src_bytes': zf.read(rel_path),
                    'inherited': True
                }

            new_origins = set()
            for action in manifest.get('actions') or []:
                action_copy = dict(action)
                origin = action_copy.get('original_release_id') or rid
                action_copy['original_release_id'] = origin
                if origin in seen_origins:
                    continue
                blobs = []
                for ref in action_copy.get('payloads') or []:
                    rel = ref.get('payload')
                    data = zf.read(rel) if rel and rel in zf.namelist() else None
                    if data:
                        blobs.append({
                            'name': ref.get('name', ''),
                            'sha512': ref.get('sha512', ''),
                            'data': data
                        })
                if blobs:
                    action_copy['_payload_blobs'] = blobs
                inherited_actions.append(action_copy)
                new_origins.add(origin)
            seen_origins.update(new_origins)
        finally:
            zf.close()

    tagged_current = []
    for action in current_actions:
        action_copy = dict(action)
        origin = action_copy.get('original_release_id') or current_id
        action_copy['original_release_id'] = origin
        tagged_current.append(action_copy)
        seen_origins.add(origin)

    release['modules'] = current_modules + list(best_modules.values())
    release['actions'] = inherited_actions + tagged_current
    return release


def _rewrite_actions_for_bundle(actions: List[Dict[str, Any]], files_dir: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Copy referenced file payloads into files/<sha512> and rewrite actions
    so no absolute Admin paths remain in the manifest.
    """
    rewritten = []
    payloads = []

    for action in actions or []:
        action_copy = dict(action)
        action_type = action_copy.get('type', '')
        blobs = action_copy.pop('_payload_blobs', None)

        if blobs:
            payload_refs = []
            for blob in blobs:
                data = blob.get('data')
                if not data:
                    continue
                digest = blob.get('sha512') or _calculate_release_hash(data)
                out_path = os.path.join(files_dir, digest)
                if not os.path.exists(out_path):
                    with open(out_path, 'wb') as f:
                        f.write(data)
                rel = 'files/' + digest
                payload_refs.append({
                    'name': blob.get('name', ''),
                    'payload': rel,
                    'sha512': digest,
                    'size': len(data)
                })
                payloads.append({'path': rel, 'sha512': digest, 'size': len(data)})
            action_copy['send_files'] = []
            action_copy.pop('dest_folder', None)
            action_copy['payloads'] = payload_refs
        elif action_type == 'Send Files' and (action_copy.get('send_files') or []):
            send_files = action_copy.get('send_files', []) or []
            payload_refs = []
            for file_path in send_files:
                if not file_path or not os.path.exists(file_path):
                    continue
                with open(file_path, 'rb') as f:
                    data = f.read()
                digest = _calculate_release_hash(data)
                out_name = digest
                out_path = os.path.join(files_dir, out_name)
                if not os.path.exists(out_path):
                    with open(out_path, 'wb') as f:
                        f.write(data)
                rel = 'files/' + out_name
                try:
                    from Support.Header import MAIN_PATH
                    rel_dest = os.path.relpath(os.path.abspath(file_path), os.path.abspath(MAIN_PATH))
                    if rel_dest.startswith('..'):
                        rel_dest = os.path.basename(file_path)
                    rel_dest = rel_dest.replace('\\', '/')
                except Exception:
                    rel_dest = os.path.basename(file_path)
                payload_refs.append({
                    'name': rel_dest,
                    'payload': rel,
                    'sha512': digest,
                    'size': len(data)
                })
                payloads.append({'path': rel, 'sha512': digest, 'size': len(data)})
            action_copy['send_files'] = []
            action_copy.pop('dest_folder', None)
            action_copy['payloads'] = payload_refs

        rewritten.append(action_copy)

    return rewritten, payloads


def build_release_package(release: Dict[str, Any],
                          privkey_path: str,
                          pubkey_path: str,
                          releases_dir: str) -> Dict[str, Any]:
    """
    Build ZIP (manifest + modules + files), Ed25519-sign manifest,
    then AES-GCM encrypt the ZIP so only the app can unlock it.

    release keys expected:
      release_id, version, notes, requires_restart, admin_ip, created_utc,
      modules: [{name, sub_folder, version, src_path}],
      actions: [ActionSequenceEditor dicts]

    Returns dict with enc_path, zip_path, release_id, version, manifest, enc_bytes
    """
    from Support.EncDec import encrypt_bytes

    _ensure_release_keypair(privkey_path, pubkey_path)
    os.makedirs(releases_dir, exist_ok=True)

    release_id = release['release_id']
    staging = tempfile.mkdtemp(prefix='neuron_release_')
    try:
        modules_root = os.path.join(staging, 'modules')
        files_root = os.path.join(staging, 'files')
        migrations_root = os.path.join(staging, 'migrations')
        os.makedirs(modules_root, exist_ok=True)
        os.makedirs(files_root, exist_ok=True)
        os.makedirs(migrations_root, exist_ok=True)

        modules_meta = []
        for mod in release.get('modules', []):
            data = mod.get('src_bytes')
            if data is None:
                src_path = mod['src_path']
                with open(src_path, 'rb') as f:
                    data = f.read()
            sub_folder = _normalize_module_subfolder(mod.get('sub_folder') or '')
            name = mod['name']
            if sub_folder:
                dest_dir = os.path.join(modules_root, *sub_folder.split('/'))
                os.makedirs(dest_dir, exist_ok=True)
                rel_path = f"modules/{sub_folder}/{name}.pyd"
            else:
                dest_dir = modules_root
                rel_path = f"modules/{name}.pyd"
            dest_path = os.path.join(dest_dir, name + '.pyd')
            with open(dest_path, 'wb') as f:
                f.write(data)
            modules_meta.append({
                'name': name,
                'sub_folder': sub_folder,
                'version': mod.get('version', '1.0'),
                'path': rel_path,
                'size': len(data),
                'sha512': _calculate_release_hash(data)
            })

        actions, payloads = _rewrite_actions_for_bundle(release.get('actions', []), files_root)
        for action in actions:
            action.pop('_payload_blobs', None)
            if not action.get('original_release_id'):
                action['original_release_id'] = release_id

        manifest = {
            'format_version': '5.0',
            'release_id': release_id,
            'version': release.get('version', '0.0.0'),
            'created_utc': release.get('created_utc', ''),
            'admin_ip': release.get('admin_ip', ''),
            'requires_restart': bool(release.get('requires_restart', True)),
            'notes': release.get('notes', ''),
            'hash_alg': 'sha512',
            'sig_alg': 'ed25519',
            'enc_alg': 'aes-256-gcm',
            'modules': modules_meta,
            'payloads': payloads,
            'actions': actions,
            'channel': release.get('channel', 'prod'),
        }

        manifest_bytes = json.dumps(manifest, separators=(',', ':'), sort_keys=True).encode('utf-8')
        signature = _sign_manifest(manifest_bytes, privkey_path)

        with open(os.path.join(staging, 'manifest.json'), 'wb') as f:
            f.write(manifest_bytes)
        with open(os.path.join(staging, 'manifest.sig'), 'wb') as f:
            f.write(signature)

        zip_base = os.path.join(releases_dir, release_id)
        zip_path = zip_base + '.zip'
        if os.path.exists(zip_path):
            os.remove(zip_path)
        shutil.make_archive(zip_base, 'zip', root_dir=staging)

        with open(zip_path, 'rb') as f:
            zip_bytes = f.read()

        enc_bytes = encrypt_bytes(zip_bytes)
        enc_path = os.path.join(releases_dir, release_id + '.nrel')
        with open(enc_path, 'wb') as f:
            f.write(enc_bytes)

        sidecar = {
            'release_id': release_id,
            'version': manifest['version'],
            'created_utc': manifest['created_utc'],
            'requires_restart': manifest['requires_restart'],
            'notes': manifest['notes'],
            'channel': manifest.get('channel', 'prod'),
            'enc_file': release_id + '.nrel',
            'zip_sha512': _calculate_release_hash(zip_bytes),
            'enc_sha512': _calculate_release_hash(enc_bytes)
        }
        with open(os.path.join(releases_dir, release_id + '.json'), 'w', encoding='utf-8') as f:
            json.dump(sidecar, f, indent=2)

        return {
            'release_id': release_id,
            'version': manifest['version'],
            'zip_path': zip_path,
            'enc_path': enc_path,
            'enc_bytes': enc_bytes,
            'manifest': manifest,
            'signature_hex': signature.hex()
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _release_sidecar_path(release_id: str) -> str:
    from Support.Header import PATH_DISTRIBUTABLE_RELEASES
    return os.path.join(PATH_DISTRIBUTABLE_RELEASES, release_id + '.json')


def load_release_sidecar(release_id: str) -> Dict[str, Any]:
    path = _release_sidecar_path(release_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_release_sidecar(release_id: str, sidecar: Dict[str, Any]) -> None:
    from Support.Header import PATH_DISTRIBUTABLE_RELEASES
    os.makedirs(PATH_DISTRIBUTABLE_RELEASES, exist_ok=True)
    with open(_release_sidecar_path(release_id), 'w', encoding='utf-8') as f:
        json.dump(sidecar, f, indent=2)


def delete_local_release(release_id: str) -> Tuple[bool, str]:
    """Remove a local release package (.nrel/.zip/.json) from PATH_DISTRIBUTABLE_RELEASES."""
    from Support.Header import PATH_DISTRIBUTABLE_RELEASES
    if not release_id or not str(release_id).strip():
        return False, "Missing release_id"
    release_id = str(release_id).strip()
    removed = []
    missing = []
    for ext in ('.nrel', '.zip', '.json'):
        path = os.path.join(PATH_DISTRIBUTABLE_RELEASES, release_id + ext)
        if os.path.isfile(path):
            try:
                os.remove(path)
                removed.append(ext)
            except Exception as e:
                return False, f"Failed to remove {path}: {e}"
        else:
            missing.append(ext)
    if not removed:
        return False, f"No package files found for {release_id}"
    return True, f"Removed {release_id} ({', '.join(removed)})"


def list_local_releases() -> List[Dict[str, Any]]:
    """Return release sidecars that have a matching .nrel package, newest first."""
    from Support.Header import PATH_DISTRIBUTABLE_RELEASES
    results = []
    if not os.path.isdir(PATH_DISTRIBUTABLE_RELEASES):
        return results
    for fname in os.listdir(PATH_DISTRIBUTABLE_RELEASES):
        if not fname.endswith('.json'):
            continue
        release_id = fname[:-5]
        nrel = os.path.join(PATH_DISTRIBUTABLE_RELEASES, release_id + '.nrel')
        if not os.path.isfile(nrel):
            continue
        sc = load_release_sidecar(release_id)
        if not sc:
            continue
        sc['release_id'] = sc.get('release_id') or release_id
        sc['_nrel_path'] = nrel
        try:
            sc['package_size'] = os.path.getsize(nrel)
        except Exception:
            sc['package_size'] = int(sc.get('package_size') or 0)
        results.append(sc)

    def sort_key(item):
        return str(item.get('created_utc') or '')

    results.sort(key=sort_key, reverse=True)
    return results


def _format_package_size(size_bytes: int) -> str:
    try:
        size_bytes = int(size_bytes or 0)
    except (TypeError, ValueError):
        size_bytes = 0
    if size_bytes >= 1073741824:
        return str(round(size_bytes / 1073741824, 2)) + " GB"
    if size_bytes >= 1048576:
        return str(round(size_bytes / 1048576, 2)) + " MB"
    if size_bytes >= 1024:
        return str(round(size_bytes / 1024, 2)) + " KB"
    return str(size_bytes) + " bytes"


def list_update_packages() -> List[Dict[str, Any]]:
    """
    Installed + pending local releases for the News Updates dialog.
    Newest first. Pending = verified and not yet marked installed.
    """
    try:
        finalize_applied_releases()
    except Exception:
        pass

    packages = []
    for sc in list_local_releases():
        installed = bool(sc.get('installed'))
        status = 'installed' if installed else 'pending'
        if not installed and not sc.get('verified') and not sc.get('staged'):
            continue
        date_value = sc.get('installed_utc') if installed else (
            sc.get('staged_utc') or sc.get('created_utc') or ''
        )
        packages.append({
            'release_id': sc.get('release_id', ''),
            'version': sc.get('version', '0.0.0'),
            'package_size': int(sc.get('package_size') or 0),
            'package_size_display': _format_package_size(sc.get('package_size') or 0),
            'date': date_value,
            'status': status,
            'notes': sc.get('notes') or '',
            'staged': bool(sc.get('staged')),
            'verified': bool(sc.get('verified')),
            'installed': installed,
            '_nrel_path': sc.get('_nrel_path', ''),
        })

    packages.sort(key=lambda p: _release_version_tuple(p.get('version', '0.0.0')), reverse=True)
    return packages


def get_pending_update_packages() -> List[Dict[str, Any]]:
    """Pending packages oldest-first (install order)."""
    pending = [p for p in list_update_packages() if p.get('status') == 'pending']
    pending.sort(key=lambda p: _release_version_tuple(p.get('version', '0.0.0')))
    return pending


def update_systeminfo_version(version_str: str) -> bool:
    """Write major/minor/patch into SystemInfo from a version string."""
    parts = _release_version_tuple(version_str)
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    try:
        from Support.Header import pool_main_db
        from Support.Utils import query_database
        query_database(
            pool_main_db,
            "UPDATE SystemInfo SET major = ?, minor = ?, patch = ?",
            (major, minor, patch)
        )
        print(f"[RELEASE] SystemInfo version set to {major}.{minor}.{patch}")
        return True
    except Exception as e:
        print(f"[RELEASE] Failed to update SystemInfo version: {e}")
        return False


def mark_release_installed(release_id: str, version: str = '', package_size: int = 0) -> None:
    """Persist install completion on the release sidecar."""
    from datetime import datetime, timezone
    from Support.Header import PATH_DISTRIBUTABLE_RELEASES
    if not release_id:
        return
    sidecar = load_release_sidecar(release_id) or {'release_id': release_id}
    sidecar['installed'] = True
    sidecar['installed_utc'] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if version:
        sidecar['version'] = version
    if package_size:
        sidecar['package_size'] = int(package_size)
    elif not sidecar.get('package_size'):
        nrel = os.path.join(PATH_DISTRIBUTABLE_RELEASES, sidecar.get('enc_file') or (release_id + '.nrel'))
        if os.path.isfile(nrel):
            try:
                sidecar['package_size'] = os.path.getsize(nrel)
            except Exception:
                pass
    sidecar['staged'] = True
    save_release_sidecar(release_id, sidecar)


def modules_have_pending_updates() -> bool:
    try:
        from Support.Header import pool_main_db
        from Support.Utils import query_database
        rows = query_database(pool_main_db, "SELECT COUNT(*) FROM Modules WHERE pending = '1'", '')
        return bool(rows and int(rows[0][0] or 0) > 0)
    except Exception:
        return False


def finalize_applied_releases() -> None:
    """
    When nothing remains in Modules.pending, mark staged releases as installed
    and bump SystemInfo to the highest staged version.
    Safe to call on every startup / dialog refresh.
    """
    if modules_have_pending_updates():
        return

    to_finalize = []
    for sc in list_local_releases():
        if sc.get('installed'):
            continue
        if not sc.get('staged'):
            continue
        to_finalize.append(sc)

    if not to_finalize:
        return

    to_finalize.sort(key=lambda s: _release_version_tuple(s.get('version', '0.0.0')))
    highest = to_finalize[-1].get('version', '0.0.0')
    for sc in to_finalize:
        mark_release_installed(
            sc.get('release_id', ''),
            version=sc.get('version', ''),
            package_size=int(sc.get('package_size') or 0)
        )
    update_systeminfo_version(highest)
    print(f"[RELEASE] Finalized {len(to_finalize)} release(s); SystemInfo -> {highest}")


def prepare_pending_releases_for_install() -> Tuple[bool, str, List[str], bool]:
    """
    Ensure every pending verified package is staged in version order so one
    restart applies the full chain.

    Returns (ok, message, staged_versions, needs_restart).
    """
    from datetime import datetime, timezone
    from Support.Header import PATH_DISTRIBUTABLE_RELEASES, PATH_RELEASE_PUBKEY

    pending = get_pending_update_packages()
    if not pending:
        return False, "No pending packages to install.", [], False

    staged_versions = []
    for pkg in pending:
        release_id = pkg.get('release_id', '')
        version = pkg.get('version', '0.0.0')
        sidecar = load_release_sidecar(release_id) or {}

        if sidecar.get('staged'):
            staged_versions.append(version)
            continue

        enc_name = sidecar.get('enc_file') or (release_id + '.nrel')
        enc_path = os.path.join(PATH_DISTRIBUTABLE_RELEASES, enc_name)
        if not os.path.isfile(enc_path):
            return False, f"Package file missing for version {version}.", staged_versions, False

        with open(enc_path, 'rb') as f:
            enc_bytes = f.read()

        ok, manifest, zip_bytes, message = unlock_and_verify_release(enc_bytes, PATH_RELEASE_PUBKEY)
        if not ok:
            return False, f"Verify failed for {version}: {message}", staged_versions, False

        staged_ok, stage_msg = stage_release_from_package(manifest, zip_bytes)
        if not staged_ok:
            if 'not newer' in str(stage_msg).lower():
                mark_release_installed(
                    release_id,
                    version=version,
                    package_size=int(pkg.get('package_size') or 0)
                )
                continue
            return False, f"Could not stage {version}: {stage_msg}", staged_versions, False

        sidecar = load_release_sidecar(release_id) or sidecar
        sidecar['verified'] = True
        sidecar['staged'] = True
        sidecar['staged_utc'] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        sidecar['package_size'] = int(pkg.get('package_size') or os.path.getsize(enc_path))
        save_release_sidecar(release_id, sidecar)
        staged_versions.append(version)

    if not staged_versions:
        if not get_pending_update_packages():
            return False, "No pending packages to install.", [], False
        return False, "No packages were staged.", [], False

    needs_restart = modules_have_pending_updates()
    if not needs_restart:
        finalize_applied_releases()
        return True, f"Applied {len(staged_versions)} package(s). No module files needed a restart.", staged_versions, False

    return True, f"Ready to apply {len(staged_versions)} package(s) on restart.", staged_versions, True


def init_release_seed_status(release_id: str, peers: List[Dict[str, Any]]) -> None:
    """Mark listed peers as pending for this release seed."""
    from Support.Utils import get_timestamp
    sidecar = load_release_sidecar(release_id)
    if not sidecar:
        sidecar = {'release_id': release_id}
    seed_status = sidecar.get('seed_status') or {}
    stamp = get_timestamp()
    for peer in peers or []:
        peer_id = str(peer.get('id') or peer.get('peer_id') or '')
        ip = str(peer.get('ip') or '')
        ip_local = str(peer.get('ip_local') or '')
        key = peer_id or (ip + ':' + ip_local)
        if not key:
            continue
        seed_status[key] = {
            'peer_id': peer_id,
            'name': str(peer.get('name') or key),
            'ip': ip,
            'ip_local': ip_local,
            'status': 'pending',
            'message': '',
            'timestamp': stamp
        }
    sidecar['seed_status'] = seed_status
    save_release_sidecar(release_id, sidecar)


def record_release_seed_reply(release_id: str, ip: str, ip_local: str,
                              status: str, message: str = '',
                              peer_id: str = '', peer_name: str = '') -> None:
    """Update one peer's ACK/NAK entry on the release sidecar."""
    from Support.Utils import get_timestamp
    if not release_id:
        return
    sidecar = load_release_sidecar(release_id)
    if not sidecar:
        sidecar = {'release_id': release_id}
    seed_status = sidecar.get('seed_status') or {}
    matched_key = None
    for key, entry in seed_status.items():
        if peer_id and str(entry.get('peer_id') or '') == str(peer_id):
            matched_key = key
            break
        if str(entry.get('ip') or '') == str(ip) and str(entry.get('ip_local') or '') == str(ip_local):
            matched_key = key
            break
    if matched_key is None:
        matched_key = str(peer_id) if peer_id else (str(ip) + ':' + str(ip_local))
        seed_status[matched_key] = {
            'peer_id': str(peer_id or ''),
            'name': peer_name or matched_key,
            'ip': str(ip or ''),
            'ip_local': str(ip_local or ''),
            'status': status,
            'message': message or '',
            'timestamp': get_timestamp()
        }
    else:
        entry = seed_status[matched_key]
        entry['status'] = status
        entry['message'] = message or ''
        entry['timestamp'] = get_timestamp()
        if peer_name:
            entry['name'] = peer_name
        if peer_id:
            entry['peer_id'] = str(peer_id)
        if ip:
            entry['ip'] = str(ip)
        if ip_local:
            entry['ip_local'] = str(ip_local)
    sidecar['seed_status'] = seed_status
    save_release_sidecar(release_id, sidecar)


def get_release_seed_peers(release_id: str, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    sidecar = load_release_sidecar(release_id)
    peers = []
    for entry in (sidecar.get('seed_status') or {}).values():
        if status_filter and str(entry.get('status') or '') != status_filter:
            continue
        peers.append(dict(entry))
    return peers


def unlock_and_verify_release(enc_bytes: bytes, pubkey_path: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[bytes], str]:
    """
    Decrypt release package with app key, verify Ed25519 manifest signature,
    and verify every module/payload sha512 listed in the manifest.

    Returns (ok, manifest_dict, zip_bytes, message)
    """
    import io
    import zipfile
    from Support.EncDec import decrypt_bytes

    try:
        zip_bytes = decrypt_bytes(enc_bytes)
    except Exception as e:
        return False, None, None, f"decrypt failed: {e}"

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
            if 'manifest.json' not in zf.namelist() or 'manifest.sig' not in zf.namelist():
                return False, None, None, "manifest missing from package"
            manifest_bytes = zf.read('manifest.json')
            signature = zf.read('manifest.sig')

            if not _verify_manifest(manifest_bytes, signature, pubkey_path):
                return False, None, None, "Ed25519 signature invalid"

            manifest = json.loads(manifest_bytes.decode('utf-8'))

            for entry in (manifest.get('modules') or []) + (manifest.get('payloads') or []):
                rel = entry.get('path')
                expected = entry.get('sha512')
                if not rel or not expected:
                    return False, None, None, "manifest entry incomplete"
                if rel not in zf.namelist():
                    return False, None, None, f"missing payload {rel}"
                actual = _calculate_release_hash(zf.read(rel))
                if actual != expected:
                    return False, None, None, f"hash mismatch for {rel}"

            return True, manifest, zip_bytes, "ok"
    except Exception as e:
        return False, None, None, f"verify failed: {e}"


def _release_version_tuple(version_str: str) -> Tuple[int, ...]:
    parts = []
    for piece in str(version_str).split('.'):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def get_installed_release_version() -> str:
    """Read SystemInfo major.minor.patch as the installed release train."""
    try:
        from Support.Header import pool_main_db
        from Support.Utils import query_database
        row = query_database(pool_main_db, "SELECT major, minor, patch FROM SystemInfo", '')
        if row:
            r = row[0]
            return f"{r[0]}.{r[1]}.{r[2]}"
    except Exception:
        pass
    return "0.0.0"


def is_release_newer_than_installed(manifest_version: str) -> bool:
    return _release_version_tuple(manifest_version) > _release_version_tuple(get_installed_release_version())


def stage_release_from_package(manifest: Dict[str, Any], zip_bytes: bytes) -> Tuple[bool, str]:
    """
    Stage a verified release for apply on restart:
    - write module .pending files under correct sub_folder
    - update Modules table
    - persist actions + zip blob in Deployment table
  """
    import io
    import zipfile
    from Support.Header import MAIN_PATH, pool_main_db
    from Support.Utils import query_database, get_timestamp
    from Support.DeploymentActionExecutor import save_deployment_data

    release_id = manifest.get('release_id', '')
    release_version = manifest.get('version', '0.0.0')
    if not release_id:
        return False, "missing release_id"

    try:
        from Support.ChangeManagement import channel_matches, local_channel
        if not channel_matches(manifest.get('channel', 'prod'), MAIN_PATH):
            return False, (
                f"channel mismatch: package={manifest.get('channel', 'prod')} "
                f"local={local_channel(MAIN_PATH)}"
            )
    except Exception:
        pass

    if not is_release_newer_than_installed(release_version):
        return False, f"version {release_version} not newer than installed {get_installed_release_version()}"

    backup_folder = os.path.join(MAIN_PATH, "System", "Bak")
    old_folder = os.path.join(MAIN_PATH, "System", "Old")
    os.makedirs(backup_folder, exist_ok=True)
    os.makedirs(old_folder, exist_ok=True)

    staged_modules = []
    deploy_stamp = get_timestamp()

    with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
        for mod in manifest.get('modules') or []:
            rel_path = mod.get('path')
            name = mod.get('name')
            if not rel_path or not name or rel_path not in zf.namelist():
                return False, f"missing module payload for {name}"

            module_data = zf.read(rel_path)
            expected_sha = mod.get('sha512', '')
            if expected_sha and _calculate_release_hash(module_data) != expected_sha:
                return False, f"hash mismatch for {name}"

            module_version = str(mod.get('version', '1.0'))
            module_folder = _normalize_module_subfolder(mod.get('sub_folder') or '')
            module_checksum = _calculate_crc(module_data)
            module_size = len(module_data)

            existing = query_database(
                pool_main_db,
                "SELECT name, sub_folder, version, pending FROM Modules WHERE name = ?",
                (name,)
            )

            should_update = True
            if existing:
                existing_version = existing[0][2] if len(existing[0]) > 2 and existing[0][2] else "0.0.0"
                try:
                    if _release_version_tuple(module_version) <= _release_version_tuple(existing_version):
                        print(f"[RELEASE] Skipping {name} - module version not newer")
                        should_update = False
                except Exception:
                    should_update = True

            if not should_update:
                continue

            if module_folder:
                module_dir = os.path.join(MAIN_PATH, *module_folder.split('/'))
            else:
                module_dir = MAIN_PATH
            os.makedirs(module_dir, exist_ok=True)

            current_module_path = os.path.join(module_dir, name + '.pyd')
            if os.path.exists(current_module_path):
                old_name = f"{name}_{deploy_stamp}.pyd"
                try:
                    shutil.copy2(current_module_path, os.path.join(old_folder, old_name))
                except Exception as e:
                    print(f"[RELEASE] Warning: could not backup {name}: {e}")

            pending_path = os.path.join(module_dir, name + '.pending')
            with open(pending_path, 'wb') as f:
                f.write(module_data)

            try:
                backup_module(pending_path, backup_folder)
            except Exception:
                pass

            if not existing:
                query_database(
                    pool_main_db,
                    """INSERT INTO Modules (name, sub_folder, deploy_date, version, pending,
                       folder, deployment_id, install_date, checksum, size)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (name, module_folder, deploy_stamp, module_version, '1',
                     '', release_id, '', module_checksum, str(module_size))
                )
            else:
                query_database(
                    pool_main_db,
                    """UPDATE Modules SET version = ?, pending = '1', deploy_date = ?,
                       deployment_id = ?, checksum = ?, size = ?, sub_folder = ? WHERE name = ?""",
                    (module_version, deploy_stamp, release_id, module_checksum,
                     str(module_size), module_folder, name)
                )

            staged_modules.append(name)
            print(f"[RELEASE] Staged pending module {name} v{module_version}")

    actions = manifest.get('actions') or []
    save_deployment_data(release_id, manifest.get('modules') or [], actions, zip_bytes)

    print(f"[RELEASE] Staged release {release_id} v{release_version} modules={len(staged_modules)} actions={len(actions)}")
    return True, "staged"


def process_stored_releases() -> None:
    """Stage any verified .nrel sidecars not yet applied (e.g. after overnight store-only)."""
    from datetime import datetime, timezone
    from Support.Header import PATH_DISTRIBUTABLE_RELEASES, PATH_RELEASE_PUBKEY
    import json

    if not os.path.isdir(PATH_DISTRIBUTABLE_RELEASES):
        return

    for fname in os.listdir(PATH_DISTRIBUTABLE_RELEASES):
        if not fname.endswith('.json'):
            continue
        sidecar_path = os.path.join(PATH_DISTRIBUTABLE_RELEASES, fname)
        try:
            with open(sidecar_path, 'r', encoding='utf-8') as f:
                sidecar = json.load(f)
        except Exception:
            continue

        if not sidecar.get('verified'):
            continue
        if sidecar.get('staged'):
            continue

        enc_name = sidecar.get('enc_file') or (sidecar.get('release_id', '') + '.nrel')
        enc_path = os.path.join(PATH_DISTRIBUTABLE_RELEASES, enc_name)
        if not os.path.isfile(enc_path):
            continue

        with open(enc_path, 'rb') as f:
            enc_bytes = f.read()

        ok, manifest, zip_bytes, message = unlock_and_verify_release(enc_bytes, PATH_RELEASE_PUBKEY)
        if not ok:
            print(f"[RELEASE] Stored release verify failed: {message}")
            continue

        staged_ok, stage_msg = stage_release_from_package(manifest, zip_bytes)
        if staged_ok:
            sidecar['staged'] = True
            sidecar['staged_utc'] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            try:
                sidecar['package_size'] = os.path.getsize(enc_path)
            except Exception:
                pass
            with open(sidecar_path, 'w', encoding='utf-8') as f:
                json.dump(sidecar, f, indent=2)
            _notify_release_restart()


def _notify_release_restart():
    """Show green restart prompt on the taskbar tickertape (UI thread safe)."""
    try:
        import Support.Header as init
        from PySide6.QtCore import QTimer, QCoreApplication
        message = "     Neuron was updated. Please restart the app."
        text = (init.taskbar_scrolling_text or '') + (
            "<span style='color: green; font-weight: bold;'>" + message + "</span><br>")

        def _apply():
            tb = getattr(init, 'taskbar', None)
            if tb is not None and hasattr(tb, 'set_tickertape_text'):
                tb.set_tickertape_text(text)

        app = QCoreApplication.instance()
        if app is not None:
            QTimer.singleShot(0, _apply)
        else:
            _apply()
    except Exception:
        pass


def verify_module_integrity(module_path: str, expected_checksum: str) -> bool:
    """
    Verify that a module's checksum matches the expected value.
    
    Args:
        module_path: Path to the module file
        expected_checksum: Expected SHA256 checksum
        
    Returns:
        True if checksums match, False otherwise
    """
    if not os.path.exists(module_path):
        return False
    
    try:
        with open(module_path, 'rb') as f:
            data = f.read()
        
        actual_checksum = _calculate_crc(data)
        return actual_checksum == expected_checksum
    except Exception as e:
        print(f"[MODULE_SECURITY] Error verifying module integrity: {e}")
        return False


def backup_module(module_path: str, backup_folder: str) -> Tuple[bool, str]:
    """
    Create an encrypted backup of a module.
    
    Args:
        module_path: Path to the module to backup
        backup_folder: Folder where backup should be stored
        
    Returns:
        Tuple of (success, encrypted_filename)
    """
    try:
        from Support.EncDec import encrypt
        import base64
        
        if not os.path.exists(module_path):
            return False, ""
        
        # Ensure backup folder exists
        os.makedirs(backup_folder, exist_ok=True)
        
        # Read module data
        with open(module_path, 'rb') as f:
            module_data = f.read()
        
        # Get module filename
        module_filename = os.path.basename(module_path)
        
        # Encrypt the filename
        encrypted_filename = encrypt(module_filename, key_index=42)  # Use specific key index for filenames
        
        # Make it filesystem-safe by base64 encoding and replacing problematic characters
        # Convert to bytes, base64 encode, then make safe for filesystem
        safe_filename = base64.urlsafe_b64encode(encrypted_filename.encode('utf-8')).decode('ascii')
        # Remove padding characters that might cause issues
        safe_filename = safe_filename.replace('=', '_')
        
        # Add .bak extension to make it clear it's a backup
        safe_filename = safe_filename + '.bak'
        
        # Save encrypted module
        backup_path = os.path.join(backup_folder, safe_filename)
        with open(backup_path, 'wb') as f:
            f.write(module_data)
        
        return True, safe_filename
        
    except Exception as e:
        print(f"[MODULE_SECURITY] Error backing up module: {e}")
        return False, ""


def restore_module_from_backup(encrypted_filename: str, backup_folder: str, 
                               destination_path: str) -> bool:
    """
    Restore a module from encrypted backup.
    
    Args:
        encrypted_filename: Encrypted filename in backup folder (base64-encoded)
        backup_folder: Folder where backup is stored
        destination_path: Where to restore the module
        
    Returns:
        True if successful, False otherwise
    """
    try:
        backup_path = os.path.join(backup_folder, encrypted_filename)
        
        if not os.path.exists(backup_path):
            print(f"[MODULE_SECURITY] Backup file not found: {backup_path}")
            return False
        
        # Read backup data
        with open(backup_path, 'rb') as f:
            module_data = f.read()
        
        # Ensure destination directory exists
        dest_dir = os.path.dirname(destination_path)
        if dest_dir:
            os.makedirs(dest_dir, exist_ok=True)
        
        # Restore module
        with open(destination_path, 'wb') as f:
            f.write(module_data)
        
        return True
        
    except Exception as e:
        print(f"[MODULE_SECURITY] Error restoring module: {e}")
        return False


def validate_all_modules(modules_db_data: List[Tuple], main_path: str) -> Dict[str, bool]:
    """
    Validate all installed modules against their stored checksums.
    Only validates .pyd files (compiled modules).
    Handles .pending files by renaming them to .pyd if they're valid.
    
    Args:
        modules_db_data: List of tuples from Modules table
        main_path: Main application path
        
    Returns:
        Dictionary mapping module names to validation status (True = valid, False = invalid)
    """
    validation_results = {}
    
    for module_record in modules_db_data:
        try:
            # Parse module record
            # Schema: name, sub_folder, deploy_date, version, pending, folder, 
            #         deployment_id, install_date, checksum, size
            module_name = module_record[0]
            sub_folder = module_record[1] if len(module_record) > 1 else ''
            checksum = module_record[8] if len(module_record) > 8 else ''
            
            if not checksum:
                validation_results[module_name] = True  # Assume valid if no checksum
                continue
            
            # Determine module paths
            if sub_folder:
                module_path = os.path.join(main_path, sub_folder, f"{module_name}.pyd")
                pending_path = os.path.join(main_path, sub_folder, f"{module_name}.pending")
            else:
                module_path = os.path.join(main_path, f"{module_name}.pyd")
                pending_path = os.path.join(main_path, f"{module_name}.pending")
            
            # Check if .pending file exists and needs to be finalized
            if os.path.exists(pending_path) and not os.path.exists(module_path):
                if verify_module_integrity(pending_path, checksum):
                    try:
                        os.rename(pending_path, module_path)
                        validation_results[module_name] = True
                        continue
                    except Exception as e:
                        validation_results[module_name] = False
                        continue
                else:
                    validation_results[module_name] = False
                    continue
            
            # Check for .pyd file
            if not os.path.exists(module_path):
                validation_results[module_name] = False
                continue
            
            # Verify integrity
            is_valid = verify_module_integrity(module_path, checksum)
            validation_results[module_name] = is_valid
            
            if not is_valid:
                print(f"[MODULE_SECURITY] Module integrity check FAILED for {module_name}")
            else:
                print(f"[MODULE_SECURITY] Module integrity check passed for {module_name}")
                
        except Exception as e:
            print(f"[MODULE_SECURITY] Error validating module {module_record[0]}: {e}")
            validation_results[module_record[0]] = False
    
    return validation_results


def restore_corrupted_modules(validation_results: Dict[str, bool], 
                              modules_db_data: List[Tuple],
                              main_path: str,
                              backup_folder: str) -> List[str]:
    """
    Restore corrupted modules from backup.
    
    Args:
        validation_results: Dictionary of module validation results
        modules_db_data: List of tuples from Modules table
        main_path: Main application path
        backup_folder: Folder containing encrypted backups
        
    Returns:
        List of successfully restored module names
    """
    restored_modules = []
    
    # Get list of corrupted modules
    corrupted_modules = [name for name, is_valid in validation_results.items() if not is_valid]
    
    if not corrupted_modules:
        print("[MODULE_SECURITY] No corrupted modules found")
        return restored_modules
    
    print(f"[MODULE_SECURITY] Found {len(corrupted_modules)} corrupted modules, attempting restoration...")
    
    # Get list of backup files
    if not os.path.exists(backup_folder):
        print(f"[MODULE_SECURITY] Backup folder not found: {backup_folder}")
        return restored_modules
    
    backup_files = os.listdir(backup_folder)
    
    for module_name in corrupted_modules:
        try:
            # Find module record
            module_record = None
            for record in modules_db_data:
                if record[0] == module_name:
                    module_record = record
                    break
            
            if not module_record:
                print(f"[MODULE_SECURITY] No database record found for {module_name}")
                continue
            
            sub_folder = module_record[1] if len(module_record) > 1 else ''
            
            # Determine destination path - only .pyd files
            if sub_folder:
                dest_path = os.path.join(main_path, sub_folder, f"{module_name}.pyd")
            else:
                dest_path = os.path.join(main_path, f"{module_name}.pyd")
            
            # Try to find matching backup file
            # Since filenames are encrypted and base64-encoded, we need to try each one
            restored = False
            for backup_file in backup_files:
                try:
                    from Support.EncDec import decrypt
                    import base64
                    
                    # Skip if not a .bak file
                    if not backup_file.endswith('.bak'):
                        continue
                    
                    # Remove .bak extension and decode from base64
                    safe_filename = backup_file[:-4]  # Remove .bak
                    safe_filename = safe_filename.replace('_', '=')  # Restore padding
                    
                    try:
                        # Decode from base64
                        encrypted_filename = base64.urlsafe_b64decode(safe_filename.encode('ascii')).decode('utf-8')
                        
                        # Try to decrypt filename
                        decrypted_name = decrypt(encrypted_filename)
                        
                        if module_name in decrypted_name:
                            # Found matching backup
                            success = restore_module_from_backup(backup_file, backup_folder, dest_path)
                            if success:
                                restored_modules.append(module_name)
                                restored = True
                                break
                    except:
                        # If decryption fails, try next file
                        continue
                except:
                    continue
            
            if not restored:
                pass
                #print(f"[MODULE_SECURITY] Could not find backup for {module_name}")
                
        except Exception as e:
            print(f"[MODULE_SECURITY] Error restoring {module_name}: {e}")
    
    return restored_modules