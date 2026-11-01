# peer_digests - read only ip/ip_local from local Me/Peers, hand to RedactEngine, forget plaintext.
# Never imports Support. Never writes IPs to disk.

from __future__ import annotations

import os
import sqlite3
import glob

from redact_core import neuron_root


def load_peer_endpoint_texts():
    """
    Return a list of endpoint strings for hashing. Caller must digest and discard.
    Empty list if DB unavailable.
    """
    db_path = _find_main_sqlite()
    if not db_path:
        return []
    ips = []
    try:
        conn = sqlite3.connect('file:%s?mode=ro' % db_path.replace('\\', '/'), uri=True, timeout=2.0)
        try:
            cur = conn.cursor()
            for table, cols in (
                ('Me', ('ip', 'ip_local')),
                ('Peers', ('ip', 'ip_local')),
            ):
                try:
                    cur.execute('SELECT %s, %s FROM %s' % (cols[0], cols[1], table))
                except Exception:
                    continue
                for row in cur.fetchall():
                    for cell in row:
                        if cell is None:
                            continue
                        text = str(cell).strip()
                        if text:
                            ips.append(text)
        finally:
            conn.close()
    except Exception:
        return []
    return ips


def _find_main_sqlite():
    data_dir = os.path.join(neuron_root(), 'System', 'neuron', 'data')
    if not os.path.isdir(data_dir):
        return ''
    # Prefer extensionless long-hash DB that contains Me + Peers
    candidates = []
    try:
        for name in os.listdir(data_dir):
            if name.endswith('-wal') or name.endswith('-shm') or name.endswith('§'):
                continue
            path = os.path.join(data_dir, name)
            if not os.path.isfile(path):
                continue
            # Skip small named side DBs by heuristic: main DB is large extensionless
            base, ext = os.path.splitext(name)
            if ext.lower() == '.db':
                continue
            if len(name) < 16:
                continue
            candidates.append((os.path.getsize(path), path))
    except Exception:
        return ''
    candidates.sort(reverse=True)
    for _size, path in candidates:
        if _sqlite_has_me_peers(path):
            return path
    # Fallback: any .db with Me/Peers (should not be needed)
    for path in glob.glob(os.path.join(data_dir, '*.db')):
        if _sqlite_has_me_peers(path):
            return path
    return ''


def _sqlite_has_me_peers(path):
    try:
        conn = sqlite3.connect('file:%s?mode=ro' % path.replace('\\', '/'), uri=True, timeout=1.0)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('Me','Peers')")
            names = {r[0] for r in cur.fetchall()}
            return 'Me' in names and 'Peers' in names
        finally:
            conn.close()
    except Exception:
        return False
