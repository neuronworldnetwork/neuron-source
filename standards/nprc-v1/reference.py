"""
NPRC-1 reference implementation (Neuron Peer Reliability Certificate, version 1).

Standalone, dependency-light scoring and verification logic. No network, database,
or private keys. Safe to publish and reuse in other P2P systems.

Cryptography: Ed25519 signatures over canonical JSON (UTF-8, sorted keys, no spaces).
"""

from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, List, Optional, Tuple

NPRC_VERSION = 1
NPRC_TYP_CERT = 'nprc/cert'
NPRC_TYP_ATT = 'nprc/att'
NPRC_ALG = 'ed25519'

# Default parameters (Neuron production uses the same values in Support/Header.py)
DEFAULT_PARAMS = {
    'score_min': 0.0,
    'score_max': 5.0,
    'min_samples': 3,
    'wilson_z': 1.64,
    'weight_presence': 0.25,
    'weight_storage': 0.35,
    'weight_exchange': 0.20,
    'weight_identity': 0.10,
    'weight_contrib': 0.10,
    'decision_local': 0.50,
    'decision_attest': 0.25,
    'decision_claimed': 0.10,
    'claimed_max_delta': 1.5,
    'attest_issuer_min': 3.0,
    'integrity_floor': 0.0,
    'cert_ttl_sec': 3600,
    'attestation_ttl_sec': 2592000,
    'attestation_max_wire': 3,
}


def clamp(value: Any, params: Dict[str, float]) -> float:
    lo = float(params['score_min'])
    hi = float(params['score_max'])
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = lo
    return max(lo, min(hi, n))


def round_score(value: Any, params: Dict[str, float]) -> float:
    return round(clamp(value, params), 1)


def wilson_lower_bound(ok: int, fail: int, params: Dict[str, float]) -> Optional[float]:
    n = float(ok) + float(fail)
    if n < float(params['min_samples']):
        return None
    p = float(ok) / n
    z = float(params['wilson_z'])
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    lo = max(0.0, (center - margin) / denom)
    return clamp(lo * float(params['score_max']), params)


def renorm(parts: List[Tuple[Optional[float], float, bool]], params: Dict[str, float]) -> float:
    enabled = [(s, w) for s, w, on in parts if on and s is not None and w > 0]
    if not enabled:
        return float(params['score_min'])
    total_w = sum(w for _s, w in enabled)
    if total_w <= 0:
        return clamp(enabled[0][0], params)
    return clamp(sum(s * w for s, w in enabled) / total_w, params)


def blend_local(state: Dict[str, Any], params: Dict[str, float]) -> float:
    min_n = int(params['min_samples'])
    storage_n = int(state.get('ok_storage', 0)) + int(state.get('fail_storage', 0))
    exchange_n = int(state.get('ok_exchange', 0)) + int(state.get('fail_exchange', 0))
    parts = [
        (state.get('presence'), float(params['weight_presence']), state.get('presence') is not None),
        (state.get('storage'), float(params['weight_storage']),
         state.get('storage') is not None and storage_n >= min_n),
        (state.get('exchange'), float(params['weight_exchange']),
         state.get('exchange') is not None and exchange_n >= min_n),
        (state.get('identity'), float(params['weight_identity']), state.get('identity') is not None),
        (state.get('contrib'), float(params['weight_contrib']), state.get('contrib') is not None),
    ]
    return renorm(parts, params)


def claimed_acceptable(state: Dict[str, Any], local: float, params: Dict[str, float]) -> bool:
    claimed = state.get('claimed')
    if claimed is None:
        return False
    if int(state.get('claimed_n', 0)) < int(params['min_samples']):
        return False
    return float(claimed) <= float(local) + float(params['claimed_max_delta'])


def blend_decision(state: Dict[str, Any], params: Dict[str, float]) -> float:
    if state.get('integrity_fail') or state.get('revoked'):
        return float(params['integrity_floor'])
    local = blend_local(state, params)
    parts = [
        (local, float(params['decision_local']), True),
        (state.get('attest'), float(params['decision_attest']),
         state.get('attest') is not None and int(state.get('n_attest', 0)) > 0),
        (state.get('claimed'), float(params['decision_claimed']),
         claimed_acceptable(state, local, params)),
    ]
    return renorm(parts, params)


def canonical_bytes(obj: Dict[str, Any]) -> bytes:
    body = dict(obj)
    body.pop('sig', None)
    body.pop('pk', None)
    return json.dumps(body, separators=(',', ':'), sort_keys=True).encode('utf-8')


def verify_ed25519(pk_hex: str, obj: Dict[str, Any], sig_hex: str) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pk = str(pk_hex or '').strip().lower()
        sig = str(sig_hex or '').strip()
        if len(pk) != 64 or not sig:
            return False
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pk))
        public_key.verify(bytes.fromhex(sig), canonical_bytes(obj))
        return True
    except Exception:
        return False


def sign_ed25519(private_key, obj: Dict[str, Any]) -> Tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    sig = private_key.sign(canonical_bytes(obj)).hex()
    pk = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    return sig, pk


def verify_certificate(
    cert: Dict[str, Any],
    expected_subject_id: str,
    now: Optional[int] = None,
    params: Optional[Dict[str, float]] = None,
) -> Tuple[bool, str]:
    """Return (ok, reason). Does not trust self-score; only structural/crypto validity."""
    p = params or DEFAULT_PARAMS
    now = int(now if now is not None else time.time())
    if cert.get('typ') not in (NPRC_TYP_CERT, None):
        if cert.get('v') != NPRC_VERSION:
            return False, 'bad_version'
    if str(cert.get('id') or '') != str(expected_subject_id):
        return False, 'subject_mismatch'
    exp = int(cert.get('exp') or 0)
    if exp and exp < now:
        return False, 'expired'
    ts = int(cert.get('ts') or 0)
    if ts and abs(now - ts) > int(p['cert_ttl_sec']) * 2:
        return False, 'stale_timestamp'
    if int(cert.get('rev') or 0):
        return False, 'revoked'
    if not cert.get('sig') or not cert.get('pk'):
        return False, 'unsigned'
    if cert.get('alg') not in (NPRC_ALG, None):
        return False, 'bad_alg'
    if not verify_ed25519(str(cert.get('pk')), cert, str(cert.get('sig'))):
        return False, 'bad_signature'
    return True, 'ok'


def verify_attestation(
    att: Dict[str, Any],
    expected_subject_id: str,
    now: Optional[int] = None,
    params: Optional[Dict[str, float]] = None,
) -> Tuple[bool, str]:
    p = params or DEFAULT_PARAMS
    now = int(now if now is not None else time.time())
    if att.get('typ') not in (NPRC_TYP_ATT, None):
        return False, 'bad_typ'
    if str(att.get('sub') or '') != str(expected_subject_id):
        return False, 'subject_mismatch'
    exp = int(att.get('exp') or 0)
    if exp and exp < now:
        return False, 'expired'
    if not att.get('sig') or not att.get('pk'):
        return False, 'unsigned'
    if att.get('alg') not in (NPRC_ALG, None):
        return False, 'bad_alg'
    if not verify_ed25519(str(att.get('pk')), att, str(att.get('sig'))):
        return False, 'bad_signature'
    return True, 'ok'


def median_attestation_scores(
    attestations: List[Dict[str, Any]],
    issuer_trust: Dict[str, float],
    params: Optional[Dict[str, float]] = None,
) -> Tuple[Optional[float], int]:
    """Issuer trust map: peer_id -> local decision score (0..5). Self-issued ignored."""
    p = params or DEFAULT_PARAMS
    min_issuer = float(p['attest_issuer_min'])
    scores = []
    for att in attestations:
        iss = str(att.get('iss') or '')
        if not iss:
            continue
        trust = issuer_trust.get(iss)
        if trust is None or float(trust) < min_issuer:
            continue
        ok, _reason = verify_attestation(att, str(att.get('sub') or ''), params=p)
        if not ok:
            continue
        scores.append(clamp(att.get('sc'), p))
    if not scores:
        return None, 0
    scores.sort()
    mid = len(scores) // 2
    if len(scores) % 2:
        med = scores[mid]
    else:
        med = (scores[mid - 1] + scores[mid]) / 2.0
    return clamp(med, p), len(scores)


def build_certificate_payload(
    subject_id: str,
    state: Dict[str, Any],
    held_attestations: List[Dict[str, Any]],
    now: Optional[int] = None,
    params: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    p = params or DEFAULT_PARAMS
    now = int(now if now is not None else time.time())
    score = round_score(blend_decision(state, p), p)
    n = (int(state.get('ok_storage', 0)) + int(state.get('fail_storage', 0))
         + int(state.get('ok_exchange', 0)) + int(state.get('fail_exchange', 0)))
    att_wire = []
    for item in held_attestations[: int(p['attestation_max_wire'])]:
        if isinstance(item, dict) and verify_ed25519(str(item.get('pk')), item, str(item.get('sig'))):
            att_wire.append(item)
    revoked = 1 if (state.get('integrity_fail') or state.get('revoked')) else 0
    if revoked:
        score = float(p['integrity_floor'])
    return {
        'typ': NPRC_TYP_CERT,
        'alg': NPRC_ALG,
        'v': NPRC_VERSION,
        'id': subject_id,
        'sc': score,
        'ts': now,
        'exp': now + int(p['cert_ttl_sec']),
        'rev': revoked,
        'm': {
            'p': round_score(state.get('presence') or 0, p),
            's': round_score(state.get('storage'), p) if state.get('storage') is not None else None,
            'e': round_score(state.get('exchange'), p) if state.get('exchange') is not None else None,
            'i': round_score(state.get('identity'), p) if state.get('identity') is not None else None,
            'c': round_score(state.get('contrib'), p) if state.get('contrib') is not None else None,
        },
        'n': n,
        'att': att_wire,
    }
