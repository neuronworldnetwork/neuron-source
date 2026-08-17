# NPRC-1 Specification

**Neuron Peer Reliability Certificate** - version 1 (`nprc/cert`, `nprc/att`)

Status: Published reference (2026). Implementations SHOULD follow this document for
interoperability.

---

## 1. Purpose

NPRC-1 provides a **verifiable, non-transferable reputation credential** for peers in
decentralized networks. It combines:

1. **Local observation** (what I measured myself)
2. **Third-party attestations** (signed statements after real exchanges)
3. **Self certificate** (signed summary the subject advertises - never authoritative alone)

This mirrors Web3 patterns (Verifiable Credentials, soulbound identity binding) without
requiring a blockchain: Ed25519 keys bind credentials to `peer_id`, and verifiers recompute
trust locally.

---

## 2. Design principles

| Principle | Rule |
|-----------|------|
| Never trust self-score | A subject's `sc` in their own certificate is a **claim**. Verifiers weight it at most 10% and only if signature valid, not expired, and not inflated vs local observation. |
| Observations beat claims | Local measured behavior (50% weight) dominates the verifier's decision score. |
| Web-of-trust attestations | Attestations from issuers the verifier already trusts (local score >= 3.0) contribute 25% (median of valid sigs). |
| Wilson intervals | Storage and exchange ratios use Wilson score lower bound (z=1.64) so small sample sizes cannot masquerade as perfect. |
| Minimum samples | Behavioral metrics require `n >= 3` exchanges before affecting blended score. |
| Revocation | Certificates with `rev: 1` or failed integrity MUST be rejected. |
| Identity binding | `id` in certificate MUST match ping `peer_id`; `pk` MUST match pinned Ed25519 key when known. |

---

## 3. Score model (0.0 - 5.0)

### 3.1 Local components (verifier-side state)

| Key | Meaning | Neuron source |
|-----|---------|---------------|
| `presence` | Uptime / ping recency | Rolling online window |
| `storage` | Wilson(ok, fail) for chunk hold/ACK | Chunk replication |
| `exchange` | Wilson(ok, fail) for jobs/relay | Hive, mesh jobs |
| `identity` | Age since first seen | Sybil resistance |
| `contrib` | Give/take storage balance | Balance sheet ratio |

**Local blend** (renormalize over available components):

```
presence  25%
storage   35%  (only if samples >= min_samples)
exchange  20%  (only if samples >= min_samples)
identity  10%
contrib   10%
```

### 3.2 Decision blend (what verifiers store as `reliability`)

```
decision = renorm(
  local_blend     @ 50%,
  median_attest   @ 25%  (if any valid attestation from trusted issuers),
  claimed_sc      @ 10%  (only if signed cert valid AND claimed <= local + 1.5)
)
```

If `integrity_fail` or `revoked`: decision = 0.

---

## 4. Wire objects

### 4.1 Certificate (`typ: nprc/cert`)

Carried in ping metadata (JSON string). Fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `typ` | string | yes | `nprc/cert` |
| `alg` | string | yes | `ed25519` |
| `v` | int | yes | `1` |
| `id` | string | yes | Subject peer_id |
| `sc` | float | yes | Self-reported decision score (claim) |
| `ts` | int | yes | Unix seconds issued |
| `exp` | int | yes | Unix seconds expiry |
| `rev` | int | no | `1` if subject revoked (integrity failure) |
| `m` | object | yes | Metric breakdown `{p,s,e,i,c}` (nullable sub-scores) |
| `n` | int | yes | Total exchange samples backing metrics |
| `att` | array | no | Up to 3 embedded attestations (vouching for subject) |
| `pk` | hex | yes | Ed25519 public key (32 bytes, 64 hex chars) |
| `sig` | hex | yes | Signature over canonical JSON without `sig` |

**Canonical signing:** JSON-serialize the object **excluding** `sig` and `pk` fields:
`json.dumps(body, separators=(',', ':'), sort_keys=True)` UTF-8.
The public key is carried alongside the signature for verification but is not part of the signed bytes.

**Max size:** 1500 bytes recommended (truncate `att` first).

### 4.2 Attestation (`typ: nprc/att`)

Issued by peer A about peer B after successful exchange:

| Field | Type | Required |
|-------|------|----------|
| `typ` | string | `nprc/att` |
| `alg` | string | `ed25519` |
| `iss` | string | Issuer peer_id |
| `sub` | string | Subject peer_id |
| `sc` | float | Issuer's decision score for subject at issue time |
| `ts` | int | Issue time |
| `exp` | int | Expiry (default 30 days) |
| `n` | int | Sample count issuer used |
| `kind` | string | optional: `storage` or `exchange` |
| `pk`, `sig` | hex | Issuer key + signature |

Sent point-to-point (not broadcast in cert `att` array unless subject includes copies).

---

## 5. Verification algorithm

```
VERIFY_CERT(cert, expected_id, now, pinned_pk):
  if cert.id != expected_id: REJECT
  if cert.rev == 1: REJECT
  if cert.exp < now: REJECT
  if abs(now - cert.ts) > 2 * cert_ttl: REJECT
  if not ED25519_VERIFY(cert.pk, cert, cert.sig): REJECT
  if pinned_pk and cert.pk != pinned_pk: REJECT
  ACCEPT as valid CLAIM (not as truth)

VERIFY_ATT(att, expected_sub, now, issuer_local_score):
  if att.sub != expected_sub: REJECT
  if att.exp < now: REJECT
  if not ED25519_VERIFY(att.pk, att, att.sig): REJECT
  if issuer_local_score < 3.0: IGNORE
  ACCEPT score for median pool
```

---

## 6. Threat model

| Attack | Mitigation |
|--------|------------|
| Self-inflate score | 10% cap; must match signature; delta clamp vs local |
| Sybil identities | New peers start 0; identity age metric; attestations need trusted issuers |
| Collusion ring | Median not mean; issuers below 3.0 ignored; diverse issuers needed |
| Replay | `exp`, `ts` skew limits |
| Key swap | Pin `pk` on first signed packet; cert `id` must match |
| Uptime farming | Storage weight 35% vs presence 25% |

---

## 7. Neuron mapping

| Neuron field | NPRC |
|--------------|------|
| `Me.node_reliability` | Self cert `sc` (display) |
| `Peers.reliability` | Verifier `decision` |
| Ping `data1[10]` | JSON certificate |
| Ping `data1[11]` | JSON attestation for recipient |
| `Peers.stats.observed.rel` | Local state snapshot |

---

## 8. Extensibility

Future versions MUST bump `v` and `typ`. Verifiers MUST ignore unknown fields.
Metrics in `m` may gain keys in v2; old verifiers ignore unknown metric keys.

---

## 9. Transparency statement

**Neuron makes every possible effort to release as open source the non-sensitive
algorithms used in its network calculations**, so that independent researchers can
scrutinize, test, and suggest improvements. We welcome any and all suggestions and
comments via the public repository issue tracker.

This specification intentionally excludes: private keys, live network topology,
user content, and proprietary application code.
