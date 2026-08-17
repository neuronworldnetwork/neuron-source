# Neuron Peer Reliability Certificate (NPRC-1)

Version 1.0 - open specification for signed P2P node reliability.

Neuron publishes this standard so third parties can verify certificates, implement
compatible verifiers, and audit scoring logic without access to the Neuron application.

## Documents

| File | Purpose |
|------|---------|
| [SPEC.md](SPEC.md) | Normative protocol: wire format, verification rules, threat model |
| [reference.py](reference.py) | Portable Python reference (scoring + Ed25519 verify) |
| [test_vectors.json](test_vectors.json) | Fixed test keys and expected verify outcomes |
| [test_reference.py](test_reference.py) | Runnable self-test against vectors |

## Quick verify (Python)

```python
import json
from reference import verify_certificate, DEFAULT_PARAMS

cert = json.loads('...')  # from ping payload
ok, reason = verify_certificate(cert, expected_subject_id=cert['id'], params=DEFAULT_PARAMS)
```

## Transparency

Neuron makes every possible effort to release non-sensitive algorithms for public
scrutiny. This directory contains **no private keys, no peer data, and no app code**.

Suggestions and comments are welcome via GitHub Issues on
[neuron-source](https://github.com/neuronworldnetwork/neuron-source).

## License

Same terms as the parent repository. Reference code is intended for reuse in other
decentralized systems implementing NPRC-1 compatible attestations.
