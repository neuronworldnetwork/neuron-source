# Neuron Privacy Verifier

Open, self-only monitor that classifies **this machine’s** Neuron process network
connections into peer / local / home network / declared infrastructure /
user-web / unexplained — and separately watches **inbound** contacts on Neuron
service ports for unauthorized probes.

## Goals

1. Reassure users that Neuron is not silently phoning home with personal data.
2. Never display IP addresses, ports, hostnames, peer names, messages, or files
   in the on-screen UI or public proof export.
3. Run as a separate process so it cannot freeze the main Neuron UI.
4. Integrity-seal Official builds (`integrity.json` + `seal_integrity.py`).

## What CLEAR means

Outbound destinations observed this session were only:

- known peers (digests from local Me/Peers IP columns, hashed in memory),
- localhost,
- private/home network,
- hosts listed in `infra_allowlist.json` (category labels only in the UI),
- or user/crawl web when applicable.

**REVIEW** means something still unexplained — not a claim that Neuron is spying.

## Access shield

Inbound contacts to Neuron service ports are counted as peer / home / local /
unauthorized. Identities are not shown on screen. Helpdesk anomaly reports may
include forensic source IP/port rows for investigation only.

## Anomaly reports

`report_route.json` routes helpdesk reports by **mesh peer handle** (not an IP).
Fallback opens a mail draft to `neuron-helpdesk@proton.me` — you must click Send.

## Trust limits

- Independent OS connection sampling; peer membership digests come from the
  user’s own local peer table.
- A spy IP planted in Peers would look like “peer” (visible as an unexpected peer
  in the normal Peers UI).
- Modified builds break the Official seal and cannot export Official proof.
- Does not decrypt traffic or prove cryptographic correctness of the mesh.

## Run (with Neuron installed)

Prefer launching from Neuron’s Apps list, or:

```bat
wscript launch_silent.vbs
```

Rebuild the Official seal after intentional edits:

```bat
python seal_integrity.py
```

## License

Same terms as this repository’s [LICENSE](../../LICENSE).
