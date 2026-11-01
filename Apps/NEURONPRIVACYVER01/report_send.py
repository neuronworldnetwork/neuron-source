# Anomaly reports — P2P by peer handle (never an IP in routing config), mailto fallback.
# Helpdesk payload may include forensic endpoints for investigation; UI never shows them.

from __future__ import annotations

import json
import os
import urllib.parse
import webbrowser
from datetime import datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CMD_REPORT = 'NEURONPRIVACYVER0100000'
DEFAULT_MAIL = 'neuron-helpdesk@proton.me'


def load_route():
    path = os.path.join(APP_DIR, 'report_route.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {
            'prefer': 'p2p',
            'peer_handle': '',
            'mailto': DEFAULT_MAIL,
        }


def build_report_text(proof, extra_note=''):
    """Helpdesk text: counters + forensic endpoint rows when present."""
    lines = [
        'NEURON PRIVACY VERIFIER ANOMALY REPORT',
        'time_utc=%s' % datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
        'privacy_status=%s' % proof.get('privacy_status'),
        'shield_status=%s' % proof.get('shield_status'),
        'build_seal=%s' % proof.get('build_seal'),
        'version=%s' % proof.get('version'),
        'unknown_events=%s' % proof.get('unknown_events'),
        'unauthorized_events=%s' % proof.get('unauthorized_events'),
        'unauthorized_sources=%s' % proof.get('unauthorized_sources'),
        'samples=%s' % proof.get('samples'),
        'inbound_samples=%s' % proof.get('inbound_samples'),
        'elapsed_sec=%s' % proof.get('elapsed_sec'),
        'roster_ready=%s' % proof.get('roster_ready'),
        'user_web_allowed=%s' % proof.get('user_web_allowed'),
        'counts=%s' % json.dumps(proof.get('counts') or {}, sort_keys=True),
        'inbound=%s' % json.dumps(proof.get('inbound') or {}, sort_keys=True),
        'infra_breakdown=%s' % json.dumps(proof.get('infra_breakdown') or {}, sort_keys=True),
        '',
        '--- HELPDESK FORENSICS (investigation only; not shown in app UI) ---',
    ]
    forensics = proof.get('forensics') or []
    if not forensics:
        lines.append('(no forensic endpoint rows captured in this session)')
    else:
        for i, row in enumerate(forensics, 1):
            kind = row.get('kind') or ''
            if kind == 'inbound_unauthorized':
                lines.append(
                    '%s) inbound_unauthorized time_utc=%s remote_ip=%s remote_port=%s local_port=%s conn_state=%s'
                    % (
                        i,
                        row.get('time_utc') or '',
                        row.get('remote_ip') or '',
                        row.get('remote_port') or '',
                        row.get('local_port') or '',
                        row.get('conn_state') or '',
                    )
                )
            elif kind == 'outbound_unexplained':
                lines.append(
                    '%s) outbound_unexplained time_utc=%s remote_ip=%s remote_port=%s process_hint=%s'
                    % (
                        i,
                        row.get('time_utc') or '',
                        row.get('remote_ip') or '',
                        row.get('remote_port') or '',
                        (row.get('process_hint') or '')[:80],
                    )
                )
            else:
                lines.append('%s) %s' % (i, json.dumps(row, sort_keys=True)))
    note = (extra_note or '').strip()
    if note:
        from redact_core import safe_log_line
        lines.append('user_note=%s' % safe_log_line(note)[:400])
    return '\n'.join(lines)


def _save_report_file(body):
    data_dir = os.path.join(APP_DIR, 'data')
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, 'last_helpdesk_report.txt')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(body)
        f.write('\n')
    return path


def _clipboard_copy(text):
    try:
        import ctypes
        from ctypes import wintypes
        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(None):
            return False
        user32.EmptyClipboard()
        data = text.encode('utf-16-le') + b'\x00\x00'
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            user32.CloseClipboard()
            return False
        ptr = kernel32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
        user32.CloseClipboard()
        return True
    except Exception:
        return False


def send_report(api, proof, extra_note=''):
    """
    Prefer P2P to report_route peer_handle (mesh id, not IP).
    Fall back to mailto:neuron-helpdesk@proton.me (opens draft — user must click Send).
    Full forensic text is always saved under data/last_helpdesk_report.txt and copied
    to the clipboard when possible (mailto URLs are length-limited).
    Returns (ok: bool, method: str, detail: str)
    """
    route = load_route()
    body = build_report_text(proof, extra_note)
    saved = ''
    try:
        saved = _save_report_file(body)
    except Exception:
        saved = ''
    clipped_ok = _clipboard_copy(body)

    prefer = str(route.get('prefer') or 'p2p').lower()
    handle = str(route.get('peer_handle') or '').strip()
    mail = str(route.get('mailto') or DEFAULT_MAIL).strip() or DEFAULT_MAIL

    if prefer == 'p2p' and handle and api is not None:
        try:
            online = False
            peers = api.peers() or []
            for p in peers:
                if str(p.get('handle') or '') == handle and int(p.get('online', 0) or 0) == 1:
                    online = True
                    break
            # Chunk long bodies across data1 strings if needed
            chunks = _chunk_text(body, 3500)
            api.send_command(
                handle,
                CMD_REPORT,
                data1=chunks,
                data2=['privacy_verifier_report', proof.get('version') or '', 'forensics=1'],
            )
            detail = 'queued' if not online else 'sent'
            if saved:
                detail = '%s; saved=%s' % (detail, os.path.basename(saved))
            return True, 'p2p', detail
        except Exception as e:
            from redact_core import safe_log_line
            mail_err = safe_log_line(str(e))
            ok, method, detail = _mailto(mail, body, saved, clipped_ok)
            if ok:
                return True, 'mailto_fallback', detail
            return False, 'failed', mail_err

    ok, method, detail = _mailto(mail, body, saved, clipped_ok)
    return ok, method, detail


def _chunk_text(text, size):
    text = text or ''
    if len(text) <= size:
        return [text]
    return [text[i:i + size] for i in range(0, len(text), size)]


def _mailto(address, body, saved_path='', clipped_ok=False):
    if not address or '@' not in address:
        return False, 'mailto', 'no_address'
    subject = urllib.parse.quote('Neuron Privacy Verifier anomaly report')
    # mailto URLs are tiny — put a short pointer; full text is on clipboard + saved file
    summary_lines = []
    for line in (body or '').splitlines():
        if line.startswith('--- HELPDESK'):
            break
        summary_lines.append(line)
    summary = '\n'.join(summary_lines[:20])
    pointer = (
        '\n\n---\nFull forensic report (includes source IPs/ports) was copied to the clipboard'
        if clipped_ok else
        '\n\n---\nFull forensic report could not be copied to the clipboard'
    )
    if saved_path:
        pointer += '\nand saved to:\n%s\nPlease paste/attach that file before sending.' % saved_path
    else:
        pointer += '\nPlease paste the clipboard contents into this email before sending.'
    pointer += (
        '\n\nIMPORTANT: Opening this draft does NOT send mail by itself — '
        'click Send in your mail app.'
    )
    clipped = (summary + pointer)
    if len(clipped) > 1600:
        clipped = clipped[:1600] + '\n[truncated]'
    body_q = urllib.parse.quote(clipped)
    url = 'mailto:%s?subject=%s&body=%s' % (address, subject, body_q)
    try:
        webbrowser.open(url)
        detail = 'draft_opened_must_click_send'
        if clipped_ok:
            detail += '+clipboard'
        if saved_path:
            detail += '+file'
        return True, 'mailto', detail
    except Exception:
        return False, 'mailto', 'open_failed'
