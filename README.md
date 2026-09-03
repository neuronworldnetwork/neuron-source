# neuron-source

Public transparency exports from [Neuron World Network](https://github.com/neuronworldnetwork).

This repository contains **algorithms and specifications only**. It does not contain
the Neuron application, user data, private keys, databases, or operational secrets.

## Why this exists

Neuron keeps proprietary product strategy private, but publishes the **cryptography and
peer-trust math** that outsiders can verify. Anyone can inspect and run the tests below
without access to the app, mesh, or install secrets.

We welcome **any and all suggestions and comments** via [GitHub Issues](../../issues).

## Published (safe to review)

| Path | Description |
|------|-------------|
| [Support/EncDec.py](Support/EncDec.py) | Mesh encryption construction (AES-256-GCM + HKDF-SHA512): legacy / v2 / v3 / ECDH wire formats, local-at-rest, chunk DEK wrapping. **Placeholder secrets only.** |
| [Support/test_encdec.py](Support/test_encdec.py) | Runnable self-test with fake in-memory secrets |
| [standards/nprc-v1/](standards/nprc-v1/) | **NPRC-1** - Neuron Peer Reliability Certificate (signed P2P trust scoring + Ed25519 verify) |

## Quick verify

Requires Python 3 and `cryptography`.

```bash
# Encryption construction
python Support/test_encdec.py

# Peer reliability certificate standard
cd standards/nprc-v1
python test_reference.py
```

## What we never publish here

- Private keys, recovery codes, or per-install secrets
- Peer lists, IP addresses, or live certificates from the mesh
- SQLite databases, user content, or neuron balances
- Proprietary UI, networking, hive, payments, or application modules
- Operational strategy, reward economics, or unpublished protocol internals

## Contributing

Open an issue to propose metric changes, report verification bugs, or suggest
interoperability improvements. Implementation pull requests against the published
reference files are welcome when they preserve backward compatibility or bump version.

## License

See [LICENSE](LICENSE).
