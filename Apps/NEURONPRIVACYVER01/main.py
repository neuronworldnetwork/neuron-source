#!/usr/bin/env python3
# Neuron Privacy Verifier — separate process; never imports Support.

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_ROOT)
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)


def _detach_console():
    """Drop any attached console so Apps launches do not leave a shell window."""
    if os.name != 'nt':
        return
    try:
        import ctypes
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass


def _acquire_single_instance():
    """One Privacy Verifier window at a time."""
    if os.name != 'nt':
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, 'Global\\NeuronPrivacyVerifier_SingleInstance_v1')
        if not handle:
            return True
        if kernel32.GetLastError() == 183:
            kernel32.CloseHandle(handle)
            return False
        return handle
    except Exception:
        return True


def setup_qt_environment():
    import PySide6
    pyside6_path = os.path.dirname(PySide6.__file__)
    for path in (
        os.path.join(pyside6_path, 'Qt6', 'plugins'),
        os.path.join(pyside6_path, 'Qt', 'plugins'),
        os.path.join(pyside6_path, 'plugins'),
    ):
        if os.path.exists(path):
            os.environ['QT_PLUGIN_PATH'] = path
            os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = path
            break
    os.environ['QT_QPA_PLATFORM'] = 'windows'
    os.environ['QT_API'] = 'pyside6'


setup_qt_environment()

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QColor, QPalette, QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QFrame, QFileDialog, QMessageBox, QGridLayout,
)

from redact_core import RedactEngine, safe_log_line
from peer_digests import load_peer_endpoint_texts
from collector import Collector
from report_send import send_report

try:
    from NeuronAPI import connect as neuron_connect
except Exception:
    neuron_connect = None


class PrivacyWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Neuron Privacy Verifier')
        self.setMinimumSize(560, 620)
        self.engine = RedactEngine()
        self.collector = None
        self._api = None
        self._inbox_stop = threading.Event()
        self._inbox_thread = None
        self._build_ui()
        self._load_roster()
        self._start_collector()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1000)
        self._refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        title = QLabel('Neuron Privacy Verifier')
        title.setFont(QFont('Segoe UI Semibold', 18))
        root.addWidget(title)

        self.subtitle = QLabel(
            'Monitors this machine only. Never shows addresses, ports, or message content. '
            'Watches outbound privacy and inbound access attempts separately.'
        )
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet('color: #5a6570;')
        root.addWidget(self.subtitle)

        # Privacy status
        self.status_frame = QFrame()
        self.status_frame.setFrameShape(QFrame.StyledPanel)
        sf = QVBoxLayout(self.status_frame)
        priv_cap = QLabel('Outbound privacy')
        priv_cap.setAlignment(Qt.AlignCenter)
        priv_cap.setStyleSheet('color: #5a6570;')
        sf.addWidget(priv_cap)
        self.status_label = QLabel('Starting…')
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setFont(QFont('Segoe UI Semibold', 20))
        sf.addWidget(self.status_label)
        self.seal_label = QLabel('')
        self.seal_label.setAlignment(Qt.AlignCenter)
        self.seal_label.setStyleSheet('color: #5a6570;')
        sf.addWidget(self.seal_label)
        root.addWidget(self.status_frame)

        # Access shield
        self.shield_frame = QFrame()
        self.shield_frame.setFrameShape(QFrame.StyledPanel)
        sh = QVBoxLayout(self.shield_frame)
        sh_cap = QLabel('Access shield (inbound)')
        sh_cap.setAlignment(Qt.AlignCenter)
        sh_cap.setStyleSheet('color: #5a6570;')
        sh.addWidget(sh_cap)
        self.shield_label = QLabel('…')
        self.shield_label.setAlignment(Qt.AlignCenter)
        self.shield_label.setFont(QFont('Segoe UI Semibold', 16))
        sh.addWidget(self.shield_label)
        self.shield_detail = QLabel('')
        self.shield_detail.setAlignment(Qt.AlignCenter)
        self.shield_detail.setWordWrap(True)
        self.shield_detail.setStyleSheet('color: #5a6570;')
        sh.addWidget(self.shield_detail)
        root.addWidget(self.shield_frame)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        self.count_labels = {}
        rows = (
            ('peer', 'Peer traffic'),
            ('local', 'Local services'),
            ('home_network', 'Home network'),
            ('infra', 'Declared infrastructure'),
            ('user_web', 'User / crawl web'),
            ('unknown', 'Still unexplained'),
        )
        for i, (key, label) in enumerate(rows):
            name = QLabel(label)
            name.setStyleSheet('color: #2c333a;')
            val = QLabel('0')
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            val.setFont(QFont('Consolas', 12))
            grid.addWidget(name, i, 0)
            grid.addWidget(val, i, 1)
            self.count_labels[key] = val
        root.addLayout(grid)

        self.infra_label = QLabel('')
        self.infra_label.setWordWrap(True)
        self.infra_label.setStyleSheet('color: #5a6570;')
        root.addWidget(self.infra_label)

        self.meta_label = QLabel('')
        self.meta_label.setStyleSheet('color: #5a6570;')
        root.addWidget(self.meta_label)

        self.chk_web = QCheckBox('I allowed extra live web this session (marks remaining HTTPS as user web)')
        self.chk_web.toggled.connect(self._on_web_toggled)
        root.addWidget(self.chk_web)

        buttons = QHBoxLayout()
        self.btn_export = QPushButton('Export redacted proof')
        self.btn_export.clicked.connect(self._export)
        self.btn_reseal = QPushButton('Re-check seal')
        self.btn_reseal.setToolTip(
            'Re-check seal verifies that the Official Privacy Verifier files on disk still match '
            'their published cryptographic fingerprints (integrity.json). '
            'If someone altered the classifier, allowlist, or UI to leak addresses or fake a CLEAR '
            'result, the seal breaks and this build is marked untrusted. '
            'It does not re-scan the network by itself — use it after updates or if you suspect tampering.'
        )
        self.btn_reseal.clicked.connect(self._reseal)
        self.btn_report = QPushButton('Send anomaly report')
        self.btn_report.setToolTip(
            'Sends an anomaly report to Neuron helpdesk (neuron-helpdesk@proton.me). '
            'On-screen UI stays free of addresses; the helpdesk copy includes source IP/port '
            'forensics for investigation. Mailto only opens a draft — you must click Send.'
        )
        self.btn_report.clicked.connect(self._send_anomaly_report)
        self.btn_report.setEnabled(False)
        buttons.addWidget(self.btn_export)
        buttons.addWidget(self.btn_reseal)
        buttons.addWidget(self.btn_report)
        buttons.addStretch(1)
        root.addLayout(buttons)

        self.seal_help = QLabel(
            'Re-check seal: confirms these Official verifier files were not modified '
            '(tamper check). A broken seal means do not trust CLEAR/REVIEW from this copy.'
        )
        self.seal_help.setWordWrap(True)
        self.seal_help.setStyleSheet('color: #5a6570; font-size: 11px;')
        root.addWidget(self.seal_help)

        note = QLabel(
            'Open source for self-audit. A modified build cannot claim an Official seal. '
            'This app does not read messages, files, or Neuron algorithms. '
            'Unauthorized inbound contacts are shown here to reinforce that Neuron is watched — not to expose who contacted you. '
            'Anomaly reports use mesh peer handle routing or email — never a hardcoded IP.'
        )
        note.setWordWrap(True)
        note.setStyleSheet('color: #7a8690; font-size: 11px;')
        root.addWidget(note)

        self._apply_palette()
        icon_path = os.path.join(APP_ROOT, 'PrivacyVerifier.png')
        if os.path.isfile(icon_path):
            self.setWindowIcon(QIcon(icon_path))

    def _apply_palette(self):
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor('#f3f1ec'))
        pal.setColor(QPalette.WindowText, QColor('#1c2328'))
        pal.setColor(QPalette.Base, QColor('#faf8f4'))
        pal.setColor(QPalette.Button, QColor('#e4e0d8'))
        self.setPalette(pal)
        self.setStyleSheet('QWidget { background: #f3f1ec; } QPushButton { padding: 8px 14px; }')

    def _load_roster(self):
        try:
            ips = load_peer_endpoint_texts()
            self.engine.ingest_peer_endpoints(ips)
            del ips
        except Exception as e:
            self.engine.set_error(safe_log_line('roster:%s' % e))

    def _start_collector(self):
        self.collector = Collector(self.engine, on_tick=None)
        self.collector.start()
        try:
            if neuron_connect:
                self._api = neuron_connect()
                self._api.log('privacy_verifier_start seal=%s' % (
                    'ok' if self.engine.snapshot()['seal_ok'] else 'tampered',))
                self._start_inbox_poller()
        except Exception:
            self._api = None

    def _start_inbox_poller(self):
        """Drain mesh reports into QueuesApp (writes USER_ERRORS.log on helpdesk)."""
        if not self._api or (self._inbox_thread and self._inbox_thread.is_alive()):
            return
        self._inbox_stop.clear()

        def _loop():
            try:
                module = self._api._load_queues_module()
            except Exception:
                return
            while not self._inbox_stop.is_set():
                try:
                    for envelope in self._api.poll(timeout=0.3):
                        try:
                            module.manage_queue(envelope)
                        except Exception as e:
                            try:
                                self._api.log('inbox:%s' % safe_log_line(str(e)))
                            except Exception:
                                pass
                except Exception:
                    pass
                self._inbox_stop.wait(0.5)

        self._inbox_thread = threading.Thread(target=_loop, name='PrivacyVerifierInbox', daemon=True)
        self._inbox_thread.start()

    def _on_web_toggled(self, checked):
        self.engine.set_user_web_allowed(bool(checked))

    def _reseal(self):
        self.engine.verify_integrity()
        self._refresh()

    def _refresh(self):
        snap = self.engine.snapshot()
        privacy = snap.get('privacy_status') or snap.get('status')
        if privacy == 'clear':
            self.status_label.setText('CLEAR')
            self.status_frame.setStyleSheet('background: #d9ead3; border-radius: 8px; padding: 12px;')
        elif privacy == 'review':
            self.status_label.setText('REVIEW')
            self.status_frame.setStyleSheet('background: #efe6c8; border-radius: 8px; padding: 12px;')
        else:
            self.status_label.setText('UNTRUSTED BUILD')
            self.status_frame.setStyleSheet('background: #f5d5d0; border-radius: 8px; padding: 12px;')

        shield = snap.get('shield_status') or 'quiet'
        unauth = int(snap.get('unauthorized_events') or 0)
        sources = int(snap.get('unauthorized_sources') or 0)
        if shield == 'quiet':
            self.shield_label.setText('Quiet — no unauthorized contacts')
            self.shield_frame.setStyleSheet('background: #dce8f5; border-radius: 8px; padding: 12px;')
            self.shield_detail.setText('Only peers, home network, and local services have reached Neuron ports.')
        elif shield == 'noticed':
            self.shield_label.setText('Unauthorized contact noticed')
            self.shield_frame.setStyleSheet('background: #efe6c8; border-radius: 8px; padding: 12px;')
            self.shield_detail.setText(
                'Events: %s  |  Distinct sources: %s  — identities are not shown. '
                'Includes brief external port checks.' % (unauth, sources)
            )
        else:
            self.shield_label.setText('Elevated probing activity')
            self.shield_frame.setStyleSheet('background: #f5d5d0; border-radius: 8px; padding: 12px;')
            self.shield_detail.setText(
                'Events: %s  |  Distinct sources: %s  |  Burst (last minute): %s. '
                'Neuron is being contacted by outsiders; no addresses are displayed.'
                % (unauth, sources, snap.get('burst_last_minute') or 0)
            )

        if snap['seal_ok']:
            self.seal_label.setText('Official seal: OK')
            self.btn_export.setEnabled(True)
        else:
            self.seal_label.setText('Seal broken or missing — do not trust this report as Official')
            self.btn_export.setEnabled(False)

        unknown = int(snap['counts'].get('unknown') or 0)
        unauth = int(snap.get('unauthorized_events') or 0)
        # Report when unexplained egress or inbound probing — still requires Official seal
        self.btn_report.setEnabled(bool(snap['seal_ok'] and (unknown > 0 or unauth > 0)))

        for key, lab in self.count_labels.items():
            lab.setText(str(snap['counts'].get(key, 0)))

        breakdown = snap.get('infra_breakdown') or {}
        if breakdown:
            parts = ['%s: %s' % (k, v) for k, v in sorted(breakdown.items())]
            self.infra_label.setText('Infrastructure categories — ' + '; '.join(parts))
        else:
            self.infra_label.setText('Infrastructure categories — none observed yet')

        inbound = snap.get('inbound') or {}
        roster = 'ready' if snap['roster_ready'] else 'unavailable'
        self.meta_label.setText(
            'Out samples: %s  |  In samples: %s  |  Elapsed: %ss  |  Peer roster: %s  |  '
            'Inbound peers/home/local: %s/%s/%s'
            % (
                snap['samples'],
                snap.get('inbound_samples') or 0,
                snap['elapsed_sec'],
                roster,
                inbound.get('inbound_peer', 0),
                inbound.get('inbound_home', 0),
                inbound.get('inbound_local', 0),
            )
        )

    def _export(self):
        snap = self.engine.snapshot()
        if not snap['seal_ok']:
            QMessageBox.warning(self, 'Seal', 'Cannot export Official proof from a tampered build.')
            return
        proof = self.engine.export_proof()
        path, _ = QFileDialog.getSaveFileName(
            self, 'Export redacted proof', 'privacy_proof.json', 'JSON (*.json)')
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(proof, f, indent=2)
            QMessageBox.information(self, 'Exported', 'Redacted proof saved.')
        except Exception as e:
            QMessageBox.warning(self, 'Export failed', safe_log_line(str(e)))

    def _send_anomaly_report(self):
        snap = self.engine.snapshot()
        if not snap['seal_ok']:
            QMessageBox.warning(self, 'Seal', 'Cannot send Official reports from a tampered build.')
            return
        unknown = int(snap['counts'].get('unknown') or 0)
        unauth = int(snap.get('unauthorized_events') or 0)
        if unknown <= 0 and unauth <= 0:
            QMessageBox.information(self, 'Nothing to report', 'No unexplained traffic or unauthorized contacts right now.')
            return
        reply = QMessageBox.question(
            self,
            'Send anomaly report',
            'Send an anomaly report to Neuron helpdesk?\n\n'
            'Preferred: peer mesh to the helpdesk node.\n'
            'Fallback: email draft to neuron-helpdesk@proton.me '
            '(your mail app opens a draft — you must click Send; nothing is emailed automatically).\n\n'
            'This screen never shows addresses. The helpdesk copy includes source IP/port '
            'forensics so the breach can be investigated.',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return
        proof = self.engine.export_helpdesk_report()
        try:
            ok, method, detail = send_report(self._api, proof)
        except Exception as e:
            QMessageBox.warning(self, 'Report failed', safe_log_line(str(e)))
            return
        if ok:
            if method in ('mailto', 'mailto_fallback'):
                msg = (
                    'A mail draft was opened for neuron-helpdesk@proton.me.\n\n'
                    'This does NOT send by itself — click Send in your mail app.\n'
                    'Full forensic text (with source IPs/ports) was saved under the app data folder '
                    'and copied to the clipboard when possible — paste/attach it if the draft body is short.\n\n'
                    'Detail: %s' % detail
                )
            else:
                msg = 'Report dispatched via %s (%s).' % (method, detail)
            QMessageBox.information(self, 'Report', msg)
            try:
                if self._api:
                    self._api.log('anomaly_report method=%s detail=%s' % (method, detail))
            except Exception:
                pass
        else:
            QMessageBox.warning(self, 'Report failed', 'Could not send (%s).' % safe_log_line(detail))

    def closeEvent(self, event):
        try:
            self._inbox_stop.set()
        except Exception:
            pass
        try:
            if self.collector:
                self.collector.stop()
        except Exception:
            pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Neuron Privacy Verifier')
    win = PrivacyWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    try:
        _detach_console()
        mutex = _acquire_single_instance()
        if mutex is False:
            sys.exit(0)
        main()
    except Exception:
        try:
            sys.stderr.write('Privacy Verifier failed to start\n')
            sys.stderr.write(safe_log_line(traceback.format_exc()) + '\n')
        except Exception:
            pass
        raise
