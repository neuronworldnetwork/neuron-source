# Inbound handler — privacy verifier anomaly reports land here on the helpdesk node.
# Writes redacted text into System/logs/USER_ERRORS.log (same sink as exception reports).

import os
from datetime import datetime

CMD_REPORT = 'NEURONPRIVACYVER0100000'


def manage_queue(envelope):
    command = str(envelope.get('command') or envelope.get('sub') or '')
    if command != CMD_REPORT:
        return

    data1 = envelope.get('data1') or []
    sender = str(envelope.get('from') or 'unknown')
    body = '\n'.join(str(x) for x in data1) if data1 else ''
    if not body.strip():
        return

    # Never trust inbound to contain addresses in our log formatting beyond handle id
    _append_user_errors(sender_handle=sender, body=body)


def _append_user_errors(sender_handle, body):
    app_root = os.path.dirname(os.path.abspath(__file__))
    neuron_root = os.path.dirname(os.path.dirname(app_root))
    path = os.path.join(neuron_root, 'System', 'logs', 'USER_ERRORS.log')
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write('USER_ID: %s\n' % str(sender_handle or 'unknown')[:64])
            f.write('USER NAME: privacy_verifier_report\n')
            f.write('USER IP: [redacted by privacy verifier] : [redacted]\n')
            f.write('DATE: %s\n' % datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
            f.write('ERROR: %s\n' % body[:8000])
            f.write('------------------------------------------------------\n\n')
    except Exception:
        pass
