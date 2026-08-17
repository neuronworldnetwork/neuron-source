# neuron-source

Public transparency exports from [Neuron World Network](https://github.com/neuronworldnetwork).

This repository contains **algorithms and specifications only**. It does not contain
the Neuron application, user data, private keys, databases, or operational secrets.

## Open transparency policy

**Neuron makes every possible effort to release as open source the non-sensitive
algorithms used in its network calculations**, so that independent researchers,
developers, and security reviewers can scrutinize, test, and validate how trust
scores and cryptographic operations are computed.

We welcome **any and all suggestions and comments** via [GitHub Issues](../../issues).

## Published standards

| Path | Description |
|------|-------------|
| [Support/EncDec.py](Support/EncDec.py) | Mesh encryption construction (AES-GCM + HKDF); placeholder secrets only |
| [standards/nprc-v1/](standards/nprc-v1/) | **NPRC-1** - Neuron Peer Reliability Certificate (signed P2P trust) |

### NPRC-1 quick start

```bash
cd standards/nprc-v1
python test_reference.py
```

See [standards/nprc-v1/SPEC.md](standards/nprc-v1/SPEC.md) for the full protocol.

## What we never publish here

- Private keys, recovery codes, or per-install secrets
- Peer lists, IP addresses, or live certificates from the mesh
- SQLite databases, user content, or neuron balances
- Proprietary UI and application modules

## Contributing

Open an issue to propose metric changes, report verification bugs, or suggest
interoperability improvements. Implementation pull requests against `reference.py`
and `SPEC.md` are welcome when they preserve backward compatibility or bump `v`.
