# neuron-source

Public transparency exports from [Neuron World Network](https://github.com/neuronworldnetwork).

This repository contains **algorithms, specifications, and the open Privacy Verifier**.
It does not contain private keys, user databases, or proprietary mesh modules.

## Why this exists

Neuron keeps proprietary product strategy private, but publishes the **cryptography**,
**peer-trust math**, and a **self-only privacy monitor** that outsiders can verify.
Anyone can inspect and run the tests below without access to install secrets.

We welcome **any and all suggestions and comments** via [GitHub Issues](../../issues).

## Published (safe to review)

| Path | Description |
|------|-------------|
| [Support/EncDec.py](Support/EncDec.py) | Mesh encryption construction (AES-256-GCM + HKDF-SHA512): legacy / v2 / v3 / ECDH wire formats, local-at-rest, chunk DEK wrapping, enc-json helpers. **Placeholder secrets only.** |
| [Support/test_encdec.py](Support/test_encdec.py) | Runnable self-test with fake in-memory secrets |
| [standards/nprc-v1/](standards/nprc-v1/) | **NPRC-1** — Neuron Peer Reliability Certificate (signed P2P trust scoring + Ed25519 verify) |
| [Apps/NEURONPRIVACYVER01/](Apps/NEURONPRIVACYVER01/) | **Neuron Privacy Verifier** — open self-only egress/ingress monitor (no IPs in UI) |

## Quick verify

Requires Python 3 and `cryptography` (EncDec tests). Privacy Verifier also needs `psutil` and PySide6 when run against a live Neuron install.

```bash
# Encryption construction
python Support/test_encdec.py

# Peer reliability certificate standard
cd standards/nprc-v1
python test_reference.py
```

Privacy Verifier docs: [Apps/NEURONPRIVACYVER01/README.md](Apps/NEURONPRIVACYVER01/README.md).

## What we never publish here

- Private keys, recovery codes, or per-install secrets
- Peer lists, live IP addresses, or operational certificates from the mesh
- SQLite databases, user content, or neuron balances
- Proprietary UI, hive, payments, or closed application modules
- Operational strategy, reward economics, or unpublished protocol internals

## Contributing

Open an issue to propose metric changes, report verification bugs, or suggest
interoperability improvements. Implementation pull requests against the published
reference files are welcome when they preserve backward compatibility or bump version.

## License

See [LICENSE](LICENSE).
