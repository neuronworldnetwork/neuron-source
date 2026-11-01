# Rebuild integrity.json for sealed modules. Run after intentional source changes.
from __future__ import annotations

import hashlib
import json
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))

SEALED = (
    'redact_core.py',
    'collector.py',
    'peer_digests.py',
    'infra_allowlist.json',
    'main.py',
    'QueuesApp_NEURONPRIVACYVER01.py',
    'manifest.json',
    'commands.json',
    'report_route.json',
    'report_send.py',
)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def main():
    files = {}
    for rel in SEALED:
        full = os.path.join(APP_DIR, rel)
        if not os.path.isfile(full):
            raise SystemExit('missing %s' % rel)
        files[rel] = sha256_file(full)
    payload = {
        'version': 1,
        'files': files,
        'note': 'Official seal. If any listed file changes, the app marks the build untrusted.',
    }
    out = os.path.join(APP_DIR, 'integrity.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
        f.write('\n')
    print('wrote', out)


if __name__ == '__main__':
    main()
