# redact_core - in-memory classification; never emit IPs, ports, hosts, or payloads.
# Auditable source. Release builds may ship as redact_core*.pyd with matching integrity.json.

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import threading
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Outbound privacy categories (PASS ignores unknown==0 only among these)
_OUT_KEYS = (
    'peer',
    'local',
    'home_network',
    'infra',
    'user_web',
    'unknown',
)

# Inbound shield categories (separate from privacy PASS/FAIL)
_IN_KEYS = (
    'inbound_peer',
    'inbound_local',
    'inbound_home',
    'inbound_unauthorized',
)

# Neuron service ports — used only in-memory for inbound shield heuristics (never shown in UI)
SERVICE_PORTS = frozenset({12356, 12357, 12358, 12359, 12360, 12361, 12371, 3478, 3479})
_SERVICE_PORTS = SERVICE_PORTS

_WEB_PORTS = frozenset({80, 443, 8080, 8443})
_CRAWL_HINTS = ('neuroncrawler', 'neuronbrowser', 'neuronindexer', 'neuronclicker', 'crawler')

_SAFE_LOG_RE = re.compile(r'(?i)(\d{1,3}\.){3}\d{1,3}|([0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}|:\d{2,5}\b')


def neuron_root():
    return os.path.dirname(os.path.dirname(APP_DIR))


def _session_salt():
    return hashlib.sha256(os.urandom(32)).digest()


class RedactEngine(object):
    """Classify destinations and inbound contacts; expose only counters and seal status."""

    def __init__(self):
        self._lock = threading.RLock()
        self._salt = _session_salt()
        self._peer_digests = set()
        self._infra_digests = {}  # digest -> category_key
        self._category_labels = {}
        self._counts = {k: 0 for k in _OUT_KEYS}
        self._inbound = {k: 0 for k in _IN_KEYS}
        self._infra_breakdown = {}
        self._unknown_events = 0
        self._samples = 0
        self._inbound_samples = 0
        self._unauthorized_sources = set()
        self._burst_window = []  # timestamps of unauthorized events
        # Helpdesk-only forensics (never returned by snapshot/export_proof / never shown in UI)
        self._forensics = []
        self._started = time.time()
        self._user_web_allowed = False
        self._roster_ready = False
        self._infra_ready = False
        self._seal_ok = False
        self._seal_detail = 'unchecked'
        self._last_error = ''
        self._load_allowlist()
        self.verify_integrity()

    def digest_endpoint(self, ip_text):
        raw = (ip_text or '').strip().lower()
        if not raw:
            return None
        if raw.startswith('[') and raw.endswith(']'):
            raw = raw[1:-1]
        if raw.startswith('::ffff:'):
            raw = raw[7:]
        return hashlib.sha256(self._salt + b'|' + raw.encode('utf-8', errors='ignore')).hexdigest()

    def ingest_peer_endpoints(self, ip_list):
        digests = set()
        for item in ip_list or []:
            try:
                text = str(item or '').strip()
            except Exception:
                text = ''
            d = self.digest_endpoint(text)
            if d:
                digests.add(d)
            del text
        with self._lock:
            self._peer_digests |= digests
            self._roster_ready = len(self._peer_digests) > 0
        digests.clear()

    def _load_allowlist(self):
        path = os.path.join(APP_DIR, 'infra_allowlist.json')
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            self._last_error = 'allowlist_missing'
            return
        labels = data.get('categories') or {}
        self._category_labels = {str(k): str(v) for k, v in labels.items()}
        digests = {}
        for entry in data.get('entries') or []:
            host = str(entry.get('host') or '').strip().lower()
            cat = str(entry.get('category') or 'infra').strip()
            if not host:
                continue
            d = self.digest_endpoint(host)
            if d:
                digests[d] = cat
            for resolved in _resolve_host_quiet(host):
                rd = self.digest_endpoint(resolved)
                if rd:
                    digests[rd] = cat
        with self._lock:
            self._infra_digests = digests
            self._infra_ready = True

    def set_user_web_allowed(self, allowed):
        with self._lock:
            self._user_web_allowed = bool(allowed)

    def user_web_allowed(self):
        with self._lock:
            return self._user_web_allowed

    def classify_remote(self, remote_ip, remote_port, process_hint=''):
        """Outbound classification. Identifiers never returned."""
        ip = str(remote_ip or '').strip()
        try:
            port = int(remote_port or 0)
        except Exception:
            port = 0
        hint = (process_hint or '').lower()

        if not ip or ip in ('0.0.0.0', '::', '*'):
            return None

        cat = 'unknown'
        infra_key = None

        if _is_loopback(ip):
            cat = 'local'
        elif _is_home_network(ip):
            cat = 'home_network'
        else:
            d = self.digest_endpoint(ip)
            with self._lock:
                if d and d in self._peer_digests:
                    cat = 'peer'
                elif d and d in self._infra_digests:
                    cat = 'infra'
                    infra_key = self._infra_digests.get(d)
                elif self._user_web_allowed and port in _WEB_PORTS:
                    cat = 'user_web'
                elif _looks_like_crawl_process(hint) and port in _WEB_PORTS:
                    cat = 'user_web'
                else:
                    cat = 'unknown'

        with self._lock:
            if cat in self._counts:
                self._counts[cat] += 1
            if cat == 'infra' and infra_key:
                label = self._category_labels.get(infra_key, infra_key)
                self._infra_breakdown[label] = self._infra_breakdown.get(label, 0) + 1
            if cat == 'unknown':
                self._unknown_events += 1
                self._record_forensic({
                    'kind': 'outbound_unexplained',
                    'remote_ip': ip,
                    'remote_port': port,
                    'process_hint': (process_hint or '')[:80],
                    'time_utc': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()),
                })
            self._samples += 1
        return cat

    def classify_inbound(self, remote_ip, local_port, remote_port=0, conn_state=''):
        """
        Inbound contact to a Neuron service port.
        Unauthorized sources are counted for the access shield (not privacy FAIL).
        Forensic endpoint details are kept only for helpdesk reports.
        """
        ip = str(remote_ip or '').strip()
        try:
            lport = int(local_port or 0)
        except Exception:
            lport = 0
        try:
            rport = int(remote_port or 0)
        except Exception:
            rport = 0
        state = str(conn_state or '')[:32]
        if not ip or ip in ('0.0.0.0', '::', '*'):
            return None
        if lport not in _SERVICE_PORTS:
            return None

        if _is_loopback(ip):
            cat = 'inbound_local'
        elif _is_home_network(ip):
            cat = 'inbound_home'
        else:
            d = self.digest_endpoint(ip)
            with self._lock:
                if d and d in self._peer_digests:
                    cat = 'inbound_peer'
                elif d and d in self._infra_digests:
                    cat = 'inbound_peer'  # rare; treat as expected assist
                else:
                    cat = 'inbound_unauthorized'
                    if d:
                        self._unauthorized_sources.add(d)
                    self._burst_window.append(time.time())
                    self._record_forensic({
                        'kind': 'inbound_unauthorized',
                        'remote_ip': ip,
                        'remote_port': rport,
                        'local_port': lport,
                        'conn_state': state,
                        'time_utc': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()),
                    })

        with self._lock:
            if cat in self._inbound:
                self._inbound[cat] += 1
            self._inbound_samples += 1
            # trim burst window to 60s
            cutoff = time.time() - 60.0
            self._burst_window = [t for t in self._burst_window if t >= cutoff]
        return cat

    def _record_forensic(self, item):
        """Caller must hold lock OR we take it — safe either way."""
        # If already holding RLock, nested acquire is fine
        with self._lock:
            self._forensics.append(item)
            if len(self._forensics) > 80:
                self._forensics = self._forensics[-80:]

    def snapshot(self):
        with self._lock:
            unknown = int(self._counts.get('unknown') or 0)
            # Privacy status: soft wording; unknown still fails clear status
            if not self._seal_ok:
                privacy = 'untrusted'
            elif unknown > 0:
                privacy = 'review'
            else:
                privacy = 'clear'

            unauth = int(self._inbound.get('inbound_unauthorized') or 0)
            sources = len(self._unauthorized_sources)
            burst = len(self._burst_window)
            if unauth <= 0:
                shield = 'quiet'
            elif burst >= 20 or sources >= 5:
                shield = 'elevated'
            else:
                shield = 'noticed'

            return {
                'status': privacy,  # clear | review | untrusted
                'privacy_status': privacy,
                'shield_status': shield,
                'seal_ok': bool(self._seal_ok),
                'seal_detail': str(self._seal_detail),
                'roster_ready': bool(self._roster_ready),
                'infra_ready': bool(self._infra_ready),
                'user_web_allowed': bool(self._user_web_allowed),
                'counts': dict(self._counts),
                'inbound': dict(self._inbound),
                'unauthorized_events': unauth,
                'unauthorized_sources': sources,
                'burst_last_minute': burst,
                'infra_breakdown': dict(self._infra_breakdown),
                'unknown_events': int(self._unknown_events),
                'samples': int(self._samples),
                'inbound_samples': int(self._inbound_samples),
                'elapsed_sec': int(max(0, time.time() - self._started)),
                'last_error': _sanitize_text(self._last_error),
            }

    def export_proof(self):
        """Public / on-disk proof — never includes endpoint identifiers."""
        snap = self.snapshot()
        return {
            'app': 'Neuron Privacy Verifier',
            'version': _app_version(),
            'privacy_status': snap['privacy_status'],
            'shield_status': snap['shield_status'],
            'build_seal': 'ok' if snap['seal_ok'] else 'tampered',
            'counts': snap['counts'],
            'inbound': snap['inbound'],
            'unauthorized_events': snap['unauthorized_events'],
            'unauthorized_sources': snap['unauthorized_sources'],
            'infra_breakdown': snap['infra_breakdown'],
            'unknown_events': snap['unknown_events'],
            'samples': snap['samples'],
            'inbound_samples': snap['inbound_samples'],
            'elapsed_sec': snap['elapsed_sec'],
            'user_web_allowed': snap['user_web_allowed'],
            'roster_ready': snap['roster_ready'],
        }

    def export_helpdesk_report(self):
        """
        Helpdesk-only payload: redacted counters plus forensic endpoint rows
        so breaches can be investigated. Not used by the on-screen UI.
        """
        proof = self.export_proof()
        with self._lock:
            forensics = [dict(x) for x in self._forensics]
        proof['forensics'] = forensics
        proof['forensics_note'] = (
            'Helpdesk investigation fields only. On-screen UI never displays these endpoints.'
        )
        return proof

    def verify_integrity(self):
        path = os.path.join(APP_DIR, 'integrity.json')
        try:
            with open(path, 'r', encoding='utf-8') as f:
                expected = json.load(f)
        except Exception:
            with self._lock:
                self._seal_ok = False
                self._seal_detail = 'integrity_missing'
            return False
        files = expected.get('files') or {}
        for rel, want in files.items():
            full = os.path.join(APP_DIR, rel.replace('/', os.sep))
            got = _sha256_file(full)
            if not got or got != str(want).lower():
                with self._lock:
                    self._seal_ok = False
                    self._seal_detail = 'mismatch'
                return False
        with self._lock:
            self._seal_ok = True
            self._seal_detail = 'ok'
        return True

    def set_error(self, msg):
        with self._lock:
            self._last_error = _sanitize_text(msg)


def _sha256_file(path):
    if not os.path.isfile(path):
        return ''
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _app_version():
    try:
        with open(os.path.join(APP_DIR, 'manifest.json'), 'r', encoding='utf-8') as f:
            return str(json.load(f).get('version') or '0')
    except Exception:
        return '0'


def _is_loopback(ip):
    s = (ip or '').lower()
    if s.startswith('127.') or s == '::1' or s.startswith('localhost'):
        return True
    try:
        return ipaddress.ip_address(s).is_loopback
    except Exception:
        return False


def _is_home_network(ip):
    """Private LAN, link-local, and multicast — router/UPnP/printers, not the public internet."""
    s = (ip or '').strip()
    try:
        obj = ipaddress.ip_address(s)
    except Exception:
        return False
    if obj.is_private or obj.is_link_local or obj.is_multicast:
        return True
    # Carrier-grade NAT space often used on home WAN sides of CGNAT CPE
    if isinstance(obj, ipaddress.IPv4Address):
        if ipaddress.IPv4Address('100.64.0.0') <= obj <= ipaddress.IPv4Address('100.127.255.255'):
            return True
    return False


def _looks_like_crawl_process(hint):
    h = hint or ''
    for part in _CRAWL_HINTS:
        if part in h:
            return True
    return False


def _resolve_host_quiet(host):
    out = []
    if re.match(r'^\d{1,3}(\.\d{1,3}){3}$', host):
        return [host]
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for info in infos:
            addr = info[4][0]
            if addr:
                out.append(str(addr))
    except Exception:
        pass
    seen = set()
    uniq = []
    for a in out:
        if a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq


def _sanitize_text(text):
    s = str(text or '')
    s = _SAFE_LOG_RE.sub('[redacted]', s)
    if len(s) > 160:
        s = s[:160]
    return s


def safe_log_line(text):
    return _sanitize_text(text)
