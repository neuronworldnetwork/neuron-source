"""Self-test for NPRC-1 reference implementation. Generates test keys in memory only."""

import json
import sys
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from reference import (
    NPRC_TYP_ATT,
    build_certificate_payload,
    verify_attestation,
    verify_certificate,
    sign_ed25519,
    blend_decision,
    DEFAULT_PARAMS,
)


def _test_key():
    # Ephemeral deterministic TEST key - never use outside this script
    return Ed25519PrivateKey.from_private_bytes(bytes(range(32)))


def run_tests():
    sk = _test_key()
    now = 1_700_000_000
    state = {
        'presence': 4.2,
        'storage': 4.8,
        'exchange': 3.5,
        'identity': 2.0,
        'contrib': 4.0,
        'ok_storage': 10,
        'fail_storage': 1,
        'ok_exchange': 5,
        'fail_exchange': 0,
    }
    cert = build_certificate_payload('peer-alice-test', state, [], now=now)
    sig, pk = sign_ed25519(sk, cert)
    cert['pk'] = pk
    cert['sig'] = sig

    ok, reason = verify_certificate(cert, 'peer-alice-test', now=now)
    assert ok and reason == 'ok', (ok, reason)

    ok_bad, _ = verify_certificate(cert, 'peer-wrong', now=now)
    assert not ok_bad

    att = {
        'typ': NPRC_TYP_ATT,
        'alg': 'ed25519',
        'iss': 'peer-bob-test',
        'sub': 'peer-alice-test',
        'sc': 4.1,
        'ts': now,
        'exp': now + 86400 * 30,
        'n': 12,
        'kind': 'storage',
    }
    sig2, pk2 = sign_ed25519(sk, att)
    att['pk'] = pk2
    att['sig'] = sig2
    ok_att, reason_att = verify_attestation(att, 'peer-alice-test', now=now)
    assert ok_att and reason_att == 'ok', (ok_att, reason_att)

    rev = dict(cert)
    rev['rev'] = 1
    rev['sc'] = 0.0
    rev['ts'] = now + 1
    sig3, _ = sign_ed25519(sk, rev)
    rev['sig'] = sig3
    ok_rev, reason_rev = verify_certificate(rev, 'peer-alice-test', now=now + 1)
    assert not ok_rev and reason_rev == 'revoked', (ok_rev, reason_rev)

    score = round(blend_decision(state, DEFAULT_PARAMS), 1)
    assert 0.0 <= score <= 5.0
    print('NPRC-1 reference tests passed. decision_score=%s' % score)
    return 0


if __name__ == '__main__':
    sys.exit(run_tests())
