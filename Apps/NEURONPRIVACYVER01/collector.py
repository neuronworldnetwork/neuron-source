# collector - OS-level connection sampling for local Neuron processes only.
# Runs in the verifier process. Never touches Neuron UI thread. Never logs endpoints.

from __future__ import annotations

import os
import sys
import threading
import time

from redact_core import SERVICE_PORTS, neuron_root, safe_log_line

try:
    import psutil
except Exception:
    psutil = None

_POLL_SEC = 0.5  # short interval so brief port-scans (canyouseeme etc.) are not missed
_NAME_HINTS = (
    'neuron.py',
    'neuronresident',
    'neuronprojector',
    'neuroncrawler',
    'neuronindexer',
    'neuronclicker',
    'neuronbrowser',
    'webrtc_server',
    'chunk_streaming',
    'hiveservice',
    'calendars',
    'website_server',
    'mainneuron',
    'httpd',
    'apache',
)

# Inbound probe leftovers often sit in these states after a quick external check
_INBOUND_STATES = None


def _inbound_states():
    global _INBOUND_STATES
    if _INBOUND_STATES is not None:
        return _INBOUND_STATES
    if psutil is None:
        _INBOUND_STATES = frozenset()
        return _INBOUND_STATES
    names = (
        'CONN_ESTABLISHED',
        'CONN_SYN_RECV',
        'CONN_CLOSE_WAIT',
        'CONN_FIN_WAIT1',
        'CONN_FIN_WAIT2',
        'CONN_TIME_WAIT',
        'CONN_CLOSING',
        'CONN_LAST_ACK',
        'CONN_CLOSE',
    )
    states = []
    for n in names:
        v = getattr(psutil, n, None)
        if v:
            states.append(v)
    # Also accept plain strings some platforms return
    states.extend([
        'ESTABLISHED', 'SYN_RECV', 'CLOSE_WAIT', 'FIN_WAIT1', 'FIN_WAIT2',
        'TIME_WAIT', 'CLOSING', 'LAST_ACK', 'CLOSE',
    ])
    _INBOUND_STATES = frozenset(states)
    return _INBOUND_STATES


class Collector(object):
    def __init__(self, engine, on_tick=None):
        self.engine = engine
        self.on_tick = on_tick
        self._stop = threading.Event()
        self._thread = None
        self._root = os.path.normcase(os.path.abspath(neuron_root()))
        self._seen_out = set()
        self._seen_in = set()
        self._window_started = time.time()
        self._pid_hints = {}

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='PrivacyVerifierCollector', daemon=True)
        self._thread.start()
        try:
            if sys.platform == 'win32':
                import ctypes
                BELOW_NORMAL = 0x00004000
                ctypes.windll.kernel32.SetPriorityClass(
                    ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL)
        except Exception:
            pass

    def stop(self):
        self._stop.set()
        t = self._thread
        if t:
            t.join(timeout=3.0)

    def _loop(self):
        if psutil is None:
            self.engine.set_error('psutil_unavailable')
            return
        while not self._stop.is_set():
            try:
                self._sample()
            except Exception as e:
                self.engine.set_error(safe_log_line('sample:%s' % e))
            if self.on_tick:
                try:
                    self.on_tick()
                except Exception:
                    pass
            self._stop.wait(_POLL_SEC)

    def _sample(self):
        now = time.time()
        if now - self._window_started > 30.0:
            self._seen_out.clear()
            self._seen_in.clear()
            self._window_started = now

        # 1) System-wide inbound watch on Neuron service ports (catches apache/portproxy too)
        self._sample_inbound_systemwide()

        # 2) Outbound from Neuron-related processes
        pids = self._neuron_pids()
        for pid in pids:
            try:
                proc = psutil.Process(pid)
                hint = self._pid_hints.get(pid, '')
                conns = proc.net_connections(kind='inet')
            except Exception:
                continue
            for c in conns:
                try:
                    status = getattr(c, 'status', '') or ''
                    if status == psutil.CONN_LISTEN:
                        continue
                    raddr = getattr(c, 'raddr', None)
                    if not raddr:
                        continue
                    rip = getattr(raddr, 'ip', None) or (raddr[0] if len(raddr) > 0 else '')
                    rport = getattr(raddr, 'port', None) or (raddr[1] if len(raddr) > 1 else 0)
                    if not rip:
                        continue
                    # Skip counting inbound-shaped rows again here (handled system-wide)
                    laddr = getattr(c, 'laddr', None)
                    lport = 0
                    if laddr:
                        lport = int(getattr(laddr, 'port', None) or (laddr[1] if len(laddr) > 1 else 0) or 0)
                    if lport in SERVICE_PORTS and status in _inbound_states():
                        continue
                    okey = 'out|%s|%s|%s' % (pid, self.engine.digest_endpoint(rip), int(rport or 0))
                    if okey in self._seen_out:
                        continue
                    self._seen_out.add(okey)
                    self.engine.classify_remote(rip, rport, process_hint=hint)
                except Exception:
                    continue

    def _sample_inbound_systemwide(self):
        """
        Watch every process for remotes touching Neuron listen ports.
        External port checkers (e.g. canyouseeme) often leave only a brief
        ESTABLISHED/TIME_WAIT — system-wide + short poll catches them.
        """
        try:
            conns = psutil.net_connections(kind='inet')
        except Exception:
            return
        states = _inbound_states()
        for c in conns:
            try:
                status = getattr(c, 'status', '') or ''
                if status == psutil.CONN_LISTEN:
                    continue
                if status not in states:
                    continue
                laddr = getattr(c, 'laddr', None)
                raddr = getattr(c, 'raddr', None)
                if not laddr or not raddr:
                    continue
                lport = int(getattr(laddr, 'port', None) or (laddr[1] if len(laddr) > 1 else 0) or 0)
                if lport not in SERVICE_PORTS:
                    continue
                rip = getattr(raddr, 'ip', None) or (raddr[0] if len(raddr) > 0 else '')
                if not rip:
                    continue
                # Dedup by remote digest + local port (not pid) so short probes count once
                rport = int(getattr(raddr, 'port', None) or (raddr[1] if len(raddr) > 1 else 0) or 0)
                ikey = 'in|%s|%s' % (self.engine.digest_endpoint(rip), lport)
                if ikey in self._seen_in:
                    continue
                self._seen_in.add(ikey)
                self.engine.classify_inbound(rip, lport, remote_port=rport, conn_state=status)
            except Exception:
                continue

    def _neuron_pids(self):
        found = set()
        my_pid = os.getpid()
        for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
            try:
                pid = proc.info.get('pid')
                if not pid or pid == my_pid:
                    continue
                if self._looks_like_neuron(proc.info):
                    found.add(int(pid))
                    cmd = proc.info.get('cmdline') or []
                    name = proc.info.get('name') or ''
                    self._pid_hints[int(pid)] = ('%s %s' % (name, ' '.join(str(x) for x in cmd))).lower()
            except Exception:
                continue
        # Also include any PID currently LISTENing on a Neuron service port
        try:
            for c in psutil.net_connections(kind='inet'):
                if getattr(c, 'status', None) != psutil.CONN_LISTEN:
                    continue
                laddr = getattr(c, 'laddr', None)
                if not laddr:
                    continue
                lport = int(getattr(laddr, 'port', None) or (laddr[1] if len(laddr) > 1 else 0) or 0)
                if lport in SERVICE_PORTS and getattr(c, 'pid', None):
                    found.add(int(c.pid))
        except Exception:
            pass
        return found

    def _looks_like_neuron(self, info):
        exe = (info.get('exe') or '')
        name = (info.get('name') or '').lower()
        cmd = info.get('cmdline') or []
        cmd_l = ' '.join(str(x) for x in cmd).lower()

        exe_n = os.path.normcase(os.path.abspath(exe)) if exe else ''
        if exe_n and exe_n.startswith(self._root + os.sep):
            return True

        blob = ('%s %s' % (name, cmd_l)).lower()
        for hint in _NAME_HINTS:
            if hint in blob:
                if self._root.lower() in cmd_l.replace('/', '\\') or 'neuron' in blob or hint in ('httpd', 'apache'):
                    return True
        for part in cmd:
            try:
                p = os.path.normcase(os.path.abspath(str(part)))
            except Exception:
                continue
            if p.startswith(self._root + os.sep) and p.endswith('.py'):
                return True
        return False
