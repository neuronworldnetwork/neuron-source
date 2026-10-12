"""
Release Wizard - build, sign, encrypt, and seed app updates to supernodes.
"""
import os
from datetime import datetime, timezone

from PySide6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTextEdit, QCheckBox, QPushButton, QListWidget, QListWidgetItem,
    QProgressBar, QMessageBox, QFileDialog,
    QAbstractItemView, QWidget
)
from PySide6.QtCore import QThread, Signal, Qt

import Support.Header as init
from Support.Utils import query_database, generate_random_hash
from Support.SendData import send_packet
from Support.ModuleSecurity import build_release_package, ensure_release_keypair, init_release_seed_status
from ADMIN.version_manager import get_next_module_version, batch_increment_module_versions


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_system_info_version():
    try:
        row = query_database(init.pool_main_db, "SELECT major, minor, patch FROM SystemInfo", '')
        if row:
            r = row[0]
            return f"{r[0]}.{r[1]}.{r[2]}"
    except Exception:
        pass
    return "1.0.0"


def bump_patch_version(version_str):
    parts = [int(x) for x in str(version_str).split('.')]
    while len(parts) < 3:
        parts.append(0)
    parts[2] += 1
    return f"{parts[0]}.{parts[1]}.{parts[2]}"


class ReleaseChangeSetPage(QWizardPage):
    """Pick one or more Change Management CS in state prod (optional)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Change Sets")
        self.setSubTitle(
            "Optional: select PROD change sets from NeuronChangeManagement. "
            "Environments are Neuron SOURCE trees; Neuron_PYD is compile-output only."
        )
        self._selected = []
        layout = QVBoxLayout(self)
        self.cs_list = QListWidget()
        self.cs_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.cs_list)
        self.channel_label = QLabel("Release channel: prod")
        layout.addWidget(self.channel_label)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        layout.addWidget(self.btn_refresh)
        self.refresh()

    def refresh(self):
        self.cs_list.clear()
        try:
            from Support.ChangeManagement import list_changesets
            for cs in list_changesets('prod'):
                item = QListWidgetItem(
                    f"{cs.get('title')} [{cs.get('cs_id')[:8]}] ver={cs.get('version_prod')}"
                )
                item.setData(Qt.ItemDataRole.UserRole, cs)
                self.cs_list.addItem(item)
        except Exception as e:
            self.cs_list.addItem(QListWidgetItem(f"(CM unavailable: {e})"))

    def selected_changesets(self):
        out = []
        for item in self.cs_list.selectedItems():
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict):
                out.append(data)
        return out

    def get_channel(self):
        # TEST seeding uses channel test when wizard lock is set; default prod
        wiz = self.wizard()
        if wiz and getattr(wiz, '_force_channel', None):
            return wiz._force_channel
        return 'prod'

    def initializePage(self):
        self.channel_label.setText(f"Release channel: {self.get_channel()}")


class ReleaseWorker(QThread):
    progress = Signal(str)
    finished = Signal(bool, str, list)

    def __init__(self, enc_bytes, release_id, version, admin_ip, created_utc, target_peers):
        super().__init__()
        self.enc_bytes = enc_bytes
        self.release_id = release_id
        self.version = version
        self.admin_ip = admin_ip
        self.created_utc = created_utc
        self.target_peers = target_peers
        try:
            self.ME = query_database(init.pool_main_db, "SELECT * FROM Me", '')[0]
        except Exception:
            self.ME = None

    def ip_to_send_packet_to(self, ip, local_ip):
        if self.ME and ip == self.ME[3]:
            return local_ip
        return ip

    def _me_field(self, idx, key, default=''):
        if self.ME is None:
            return (init.ME or {}).get(key, default) if isinstance(init.ME, dict) else default
        if isinstance(self.ME, dict):
            return self.ME.get(key, default)
        if isinstance(self.ME, (list, tuple)) and len(self.ME) > idx:
            return self.ME[idx] if self.ME[idx] is not None else default
        return default

    def run(self):
        failed = []
        total = len(self.target_peers)
        if total == 0:
            self.finished.emit(False, "No supernodes found", [])
            return

        data1 = [
            self.release_id,
            self.admin_ip,
            self.version,
            "5.0",
            self.created_utc
        ]

        my_id = str(self._me_field(0, 'id', '') or '')
        me_ip = self._me_field(3, 'ip', '')
        me_local = self._me_field(4, 'ip_local', '')

        for i, peer in enumerate(self.target_peers):
            name = peer.get('name', peer.get('ip', '?')) if isinstance(peer, dict) else '?'
            self.progress.emit(f"Seeding {name} ({i + 1}/{total})...")
            try:
                if isinstance(peer, (list, tuple)):
                    peer_ip = peer[5] if len(peer) > 5 else ''
                    peer_local = peer[6] if len(peer) > 6 else ''
                    name = peer[1] if len(peer) > 1 else name
                    dst_peer_id = str(peer[0] if len(peer) > 0 else '')
                else:
                    peer_ip = peer.get('ip', '')
                    peer_local = peer.get('ip_local', '')
                    dst_peer_id = str(peer.get('peer_id') or peer.get('id') or '')

                # Must pass peer_ids: peers reject REMOTE_ADMIN when trusted id is
                # pinned and src_peer_id is empty (same requirement as CMD_SRV_MOD).
                ok = send_packet(
                    me_ip, me_local, '',
                    self.ip_to_send_packet_to(peer_ip, peer_local), peer_local, '',
                    init.CMD_SRV_REL, init.PRT_LOW, '', 0, 0, _utc_now(),
                    data1, [], [self.enc_bytes],
                    '', '', [], [], [],
                    'critical',
                    False,
                    src_peer_id=my_id,
                    dst_peer_id=dst_peer_id,
                )
                if not ok:
                    self.progress.emit(f"Failed {name}: send_packet returned False")
                    failed.append(name)
            except Exception as e:
                self.progress.emit(f"Failed {name}: {e}")
                failed.append(name)

        if not failed:
            self.finished.emit(True, "All supernode seeds successful", [])
        else:
            self.finished.emit(False, "Some seeds failed", failed)


class ReleaseInfoPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Release Info")
        self.setSubTitle("Set the app release version and notes")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        current = get_system_info_version()
        self.current_label = QLabel(f"Current SystemInfo version: {current}")
        layout.addWidget(self.current_label)

        ver_row = QHBoxLayout()
        ver_row.setSpacing(8)
        ver_row.addWidget(QLabel("Release version:"))
        self.version_edit = QLineEdit(bump_patch_version(current))
        self.version_edit.setMaximumWidth(160)
        ver_row.addWidget(self.version_edit)
        ver_row.addStretch()
        layout.addLayout(ver_row)

        layout.addWidget(QLabel("Notes:"))
        self.notes_edit = QTextEdit()
        self.notes_edit.setAcceptRichText(False)
        self.notes_edit.setFixedHeight(100)
        self.notes_edit.setPlaceholderText("Optional release notes...")
        self.notes_edit.setReadOnly(False)
        self.notes_edit.setEnabled(True)
        layout.addWidget(self.notes_edit)

        self.restart_check = QCheckBox("Requires restart after apply")
        self.restart_check.setChecked(True)
        layout.addWidget(self.restart_check)

        self.channel_label = QLabel("Channel: prod")
        layout.addWidget(self.channel_label)

        layout.addStretch(1)

        # Mandatory * fields block Next until "edited"; keep optional and gate via isComplete
        self.version_edit.textChanged.connect(self.completeChanged.emit)

    def initializePage(self):
        wiz = self.wizard()
        channel = 'prod'
        if wiz and getattr(wiz, 'changeset_page', None):
            channel = wiz.changeset_page.get_channel()
            selected = wiz.changeset_page.selected_changesets()
            if selected and selected[0].get('version_prod'):
                self.version_edit.setText(selected[0]['version_prod'])
            if selected and wiz.modules_page:
                try:
                    from Support.ChangeManagement import modules_from_changeset
                    wiz.modules_page.clear_all()
                    for cs in selected:
                        for mod in modules_from_changeset(cs['cs_id'], prefer_pyd=True):
                            wiz.modules_page._add_item(mod['src_path'], compiled=mod['src_path'].endswith('.pyd'))
                except Exception:
                    pass
        self.channel_label.setText(f"Channel: {channel}")

    def isComplete(self):
        ver = self.version_edit.text().strip()
        parts = ver.split('.')
        return len(parts) == 3 and all(p.isdigit() for p in parts)

    def validatePage(self):
        if not self.isComplete():
            QMessageBox.warning(self, "Invalid version", "Use major.minor.patch (e.g. 3.4.12)")
            return False
        return True

    def get_version(self):
        return self.version_edit.text().strip()

    def get_notes(self):
        return self.notes_edit.toPlainText().strip()

    def get_requires_restart(self):
        return self.restart_check.isChecked()


class ReleaseModulesPage(QWizardPage):
    def __init__(self, admin_app, parent=None):
        super().__init__(parent)
        self.admin_app = admin_app
        self.setTitle("Modules")
        self.setSubTitle("Add and compile PYD modules for this release")
        self.compilation_worker = None
        self._modules = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        btn_row = QHBoxLayout()
        self.btn_add_py = QPushButton("Add Python Files")
        self.btn_add_pyd = QPushButton("Add Compiled Modules")
        self.btn_compile = QPushButton("Compile Selected")
        self.btn_clear = QPushButton("Clear")
        self.btn_add_py.clicked.connect(self.add_py)
        self.btn_add_pyd.clicked.connect(self.add_pyd)
        self.btn_compile.clicked.connect(self.compile_selected)
        self.btn_clear.clicked.connect(self.clear_all)
        for b in (self.btn_add_py, self.btn_add_pyd, self.btn_compile, self.btn_clear):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        self.module_list = QListWidget()
        self.module_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.module_list, 1)

        self.version_label = QLabel("Versions: auto at build")
        layout.addWidget(self.version_label)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

    def add_py(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Python Files", init.MAIN_PATH, "Python (*.py)")
        for path in files:
            self._add_item(path, compiled=False)

    def add_pyd(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Compiled Modules", init.PATH_INSTALLATION_FOLDER, "PYD (*.pyd)")
        for path in files:
            self._add_item(path, compiled=True)

    def _add_item(self, path, compiled):
        name = os.path.splitext(os.path.basename(path))[0]
        for i in range(self.module_list.count()):
            if self.module_list.item(i).data(Qt.ItemDataRole.UserRole) == path:
                return
        label = f"{'[COMPILED] ' if compiled else ''}{name}  ({path})"
        item = QListWidgetItem(label)
        item.setCheckState(Qt.CheckState.Checked)
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setData(Qt.ItemDataRole.UserRole + 1, compiled)
        if compiled:
            item.setText(f"{name} [COMPILED] OK")
            item.setData(Qt.ItemDataRole.UserRole + 2, True)
        self.module_list.addItem(item)
        try:
            nxt = get_next_module_version(name)
            self.version_label.setText(f"Next version for {name}: {nxt}")
        except Exception:
            pass

    def clear_all(self):
        self.module_list.clear()

    def compile_selected(self):
        from ADMIN.AdminConsole import CompilationWorker
        to_compile = []
        for i in range(self.module_list.count()):
            item = self.module_list.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            if item.data(Qt.ItemDataRole.UserRole + 1):
                item.setData(Qt.ItemDataRole.UserRole + 2, True)
                continue
            if item.data(Qt.ItemDataRole.UserRole + 2):
                continue
            to_compile.append(item)

        if not to_compile:
            QMessageBox.information(self, "Compile", "Nothing to compile (already compiled or none selected)")
            return

        self._compile_queue = to_compile
        self._compile_index = 0
        self.progress.setVisible(True)
        self.progress.setMaximum(len(to_compile))
        self.progress.setValue(0)
        self._start_next_compile()

    def _start_next_compile(self):
        from ADMIN.AdminConsole import CompilationWorker
        if self._compile_index >= len(self._compile_queue):
            self.progress.setVisible(False)
            return
        item = self._compile_queue[self._compile_index]
        path = item.data(Qt.ItemDataRole.UserRole)
        self.compilation_worker = CompilationWorker(path)
        self.compilation_worker.progress.connect(lambda m: None)
        self.compilation_worker.finished.connect(lambda ok, msg, p=path, it=item: self._on_compiled(ok, msg, p, it))
        self.compilation_worker.start()

    def _on_compiled(self, ok, msg, path, item):
        if ok:
            item.setText(item.text().split('  (')[0] + " OK")
            item.setData(Qt.ItemDataRole.UserRole + 2, True)
            try:
                self._stage_compiled_pyd(path)
            except Exception as e:
                print(f"[RELEASE] stage compiled module failed: {e}")
        else:
            item.setText(item.text() + " FAIL")
            QMessageBox.warning(self, "Compile failed", msg)
        self._compile_index += 1
        self.progress.setValue(self._compile_index)
        self._start_next_compile()

    def _stage_compiled_pyd(self, module_path):
        """Copy compiled .pyd into PATH_INSTALLATION_FOLDER preserving Support/ layout."""
        import shutil
        from ADMIN.AdminConsole import _newest_pyd_for_module
        module_name = os.path.splitext(os.path.basename(module_path))[0]
        module_dir = os.path.dirname(os.path.abspath(module_path))
        # Exact match only (Peers must not pick PeersUtils.pyd via startswith).
        pyd_file = _newest_pyd_for_module(module_dir, module_name)
        if not pyd_file:
            return
        try:
            rel_dir = os.path.relpath(module_dir, init.MAIN_PATH)
            if rel_dir in ('.', ''):
                dest_dir = init.PATH_INSTALLATION_FOLDER
            else:
                dest_dir = os.path.join(init.PATH_INSTALLATION_FOLDER, rel_dir)
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, module_name + '.pyd')
            shutil.copy2(pyd_file, dest)
            try:
                os.remove(pyd_file)
            except Exception:
                pass
        except Exception as e:
            print(f"[RELEASE] Could not stage {module_name}: {e}")

    def _module_subfolder_from_path(self, path):
        """Map a source or Neuron_PYD path to the in-app folder (e.g. Support)."""
        from Support.ModuleSecurity import normalize_module_subfolder
        abs_path = os.path.abspath(path)
        path_dir = abs_path if os.path.isdir(abs_path) else os.path.dirname(abs_path)

        for base in (init.MAIN_PATH, init.PATH_INSTALLATION_FOLDER):
            try:
                rel = os.path.relpath(path_dir, os.path.abspath(base))
            except ValueError:
                continue
            if rel.startswith('..'):
                continue
            return normalize_module_subfolder(rel)
        return ""

    def resolve_pyd_path(self, module_path):
        module_name = os.path.splitext(os.path.basename(module_path))[0]
        module_dir = os.path.dirname(os.path.abspath(module_path))
        # Folder relative to app tree, never ../Neuron_PYD/...
        rel_dir = self._module_subfolder_from_path(module_path)

        candidates = []
        if module_path.lower().endswith('.pyd') and os.path.exists(module_path):
            return module_path, rel_dir

        install_base = init.PATH_INSTALLATION_FOLDER
        if rel_dir:
            candidates.append(os.path.join(install_base, rel_dir.replace('/', os.sep), module_name + ".pyd"))
        else:
            candidates.append(os.path.join(install_base, module_name + ".pyd"))

        for pattern in (
            module_name + ".pyd",
            module_name + ".cp312-win_amd64.pyd",
            module_name + ".cp311-win_amd64.pyd",
            module_name + ".cp310-win_amd64.pyd",
        ):
            candidates.append(os.path.join(module_dir, pattern))

        for c in candidates:
            if os.path.exists(c):
                # Prefer folder derived from the found file's location
                found_rel = self._module_subfolder_from_path(c) or rel_dir
                return c, found_rel

        if os.path.isdir(module_dir):
            from ADMIN.AdminConsole import _newest_pyd_for_module
            found = _newest_pyd_for_module(module_dir, module_name)
            if found:
                return found, self._module_subfolder_from_path(found) or rel_dir
        return None, ""

    def get_modules_for_release(self):
        modules = []
        for i in range(self.module_list.count()):
            item = self.module_list.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            path = item.data(Qt.ItemDataRole.UserRole)
            pyd_path, sub_folder = self.resolve_pyd_path(path)
            if not pyd_path:
                raise ValueError(f"No compiled .pyd for {path}")
            name = os.path.splitext(os.path.basename(path))[0]
            modules.append({
                'name': name,
                'sub_folder': sub_folder,
                'src_path': pyd_path,
                'version': get_next_module_version(name)
            })
        return modules


class ReleaseFilesPage(QWizardPage):
    def __init__(self, action_editor, parent=None):
        super().__init__(parent)
        self.action_editor = action_editor
        self.setTitle("Files & Folders")
        self.setSubTitle("Add file/folder actions (also editable in the Update Script step)")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(QLabel(
            "Use Add Action below for Create/Modify/Remove File, Create/Remove Folder, Send Files."
        ))
        self.btn = QPushButton("Add File / Folder Action")
        self.btn.setFixedWidth(220)
        self.btn.clicked.connect(self.add_file_action)
        layout.addWidget(self.btn)
        self.hint = QLabel("Tip: full ordering is on the Update Script page.")
        layout.addWidget(self.hint)
        layout.addStretch(1)

    def add_file_action(self):
        from ADMIN.AdminConsole import ActionEditorDialog
        dialog = ActionEditorDialog(parent=self)
        idx = dialog.type_combo.findText("Send Files")
        if idx >= 0:
            dialog.type_combo.setCurrentIndex(idx)
        if dialog.exec():
            self.action_editor.actions.append(dialog.get_action_data())
            self.action_editor.refresh_table()


class ReleaseDatabasePage(QWizardPage):
    def __init__(self, action_editor, parent=None):
        super().__init__(parent)
        self.action_editor = action_editor
        self.setTitle("Database Changes")
        self.setSubTitle("Add database actions for the sequential update script")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.btn = QPushButton("Add Database Action")
        self.btn.setFixedWidth(220)
        self.btn.clicked.connect(self.add_db_action)
        layout.addWidget(self.btn)
        layout.addStretch(1)

    def add_db_action(self):
        from ADMIN.AdminConsole import ActionEditorDialog
        dialog = ActionEditorDialog(parent=self)
        idx = dialog.type_combo.findText("Execute SQL Script")
        if idx >= 0:
            dialog.type_combo.setCurrentIndex(idx)
        if dialog.exec():
            self.action_editor.actions.append(dialog.get_action_data())
            self.action_editor.refresh_table()


class ReleaseScriptPage(QWizardPage):
    def __init__(self, action_editor, parent=None):
        super().__init__(parent)
        self.setTitle("Update Script")
        self.setSubTitle("Ordered actions that guide nodes when applying this release")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        layout.addWidget(action_editor)


class ReleaseBuildPage(QWizardPage):
    def __init__(self, wizard, parent=None):
        super().__init__(parent)
        self.release_wizard = wizard
        self.setTitle("Build, Sign, Encrypt & Seed")
        self.setSubTitle("Package is Ed25519-signed and AES-256-GCM encrypted (app-only unlock)")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setFixedHeight(120)
        layout.addWidget(self.summary)

        self.supers_label = QLabel("Supernodes: (refresh on enter)")
        self.supers_label.setWordWrap(True)
        layout.addWidget(self.supers_label)

        btn_row = QHBoxLayout()
        self.btn_build = QPushButton("Build, Sign & Encrypt")
        self.btn_seed = QPushButton("Seed to Supernodes")
        self.btn_seed.setEnabled(False)
        self.btn_build.clicked.connect(self.build_package)
        self.btn_seed.clicked.connect(self.seed_supers)
        btn_row.addWidget(self.btn_build)
        btn_row.addWidget(self.btn_seed)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        layout.addWidget(self.log)
        layout.addStretch(1)

        self._built = None
        self._worker = None

    def _info(self):
        return self.release_wizard.info_page

    def initializePage(self):
        supers = self.release_wizard.get_supernodes()
        lines = [f"- {p.get('name', '?')} ({p.get('ip', '')})" for p in supers]
        self.supers_label.setText(
            "Supernodes (" + str(len(supers)) + "):\n" + ("\n".join(lines) if lines else "(none)")
        )
        mods = []
        try:
            mods = self.release_wizard.modules_page.get_modules_for_release()
        except Exception as e:
            self.summary.setPlainText(f"Module resolve warning: {e}")
            return
        actions = self.release_wizard.action_editor.get_actions_data()
        info = self._info()
        self.summary.setPlainText(
            f"Version: {info.get_version()}\n"
            f"Modules: {len(mods)}\n"
            f"Actions: {len(actions)}\n"
            f"Security: Ed25519 sign + AES-256-GCM encrypt + SHA-512 hashes\n"
            f"Notes: {info.get_notes()}"
        )

    def build_package(self):
        try:
            modules = self.release_wizard.modules_page.get_modules_for_release()
        except Exception as e:
            QMessageBox.critical(self, "Build failed", str(e))
            return

        if not modules and not self.release_wizard.action_editor.get_actions_data():
            QMessageBox.warning(self, "Empty release", "Add at least one module or action")
            return

        names = [m['name'] for m in modules]
        if names:
            versions = batch_increment_module_versions(names)
            for m in modules:
                m['version'] = versions.get(m['name'], m['version'])

        release_id = generate_random_hash()
        admin_ip = ''
        if self.release_wizard.admin_app and getattr(self.release_wizard.admin_app, 'ME', None):
            admin_ip = self.release_wizard.admin_app.ME.get('ip', '')
        elif init.ME:
            admin_ip = init.ME.get('ip', '')

        info = self._info()
        channel = 'prod'
        try:
            channel = self.release_wizard.changeset_page.get_channel()
        except Exception:
            pass
        release = {
            'release_id': release_id,
            'version': info.get_version(),
            'notes': info.get_notes(),
            'requires_restart': info.get_requires_restart(),
            'admin_ip': admin_ip,
            'created_utc': _utc_now(),
            'modules': modules,
            'actions': self.release_wizard.action_editor.get_actions_data(),
            'channel': channel,
        }

        try:
            from Support.ModuleSecurity import migrate_admin_private_keys
            migrate_admin_private_keys()
            ensure_release_keypair(init.PATH_RELEASE_PRIVKEY, init.PATH_RELEASE_PUBKEY)
            result = build_release_package(
                release,
                init.PATH_RELEASE_PRIVKEY,
                init.PATH_RELEASE_PUBKEY,
                init.PATH_DISTRIBUTABLE_RELEASES
            )
            self._built = result
            self.btn_seed.setEnabled(True)
            self.log.append(
                f"Built {result['enc_path']}\n"
                f"Channel: {channel}\n"
                f"Signed: {result['signature_hex'][:32]}...\n"
                f"Encrypted package ready (only Neuron can decrypt)"
            )
            try:
                from Support.ChangeManagement import mark_changeset_released, audit_log
                for cs in self.release_wizard.changeset_page.selected_changesets():
                    # mark released after successful seed instead; record build for now
                    audit_log('release_build', result='ok', cs_id=cs.get('cs_id', ''),
                              release_id=result['release_id'],
                              version_allocated=info.get_version(),
                              extra={'channel': channel})
            except Exception:
                pass
            QMessageBox.information(
                self,
                "Built",
                f"Release {result['release_id']} signed and encrypted.\n\n"
                f"Next: click Seed to Supernodes (Build alone does not send)."
            )
        except Exception as e:
            QMessageBox.critical(self, "Build failed", str(e))
            self.log.append(f"Build error: {e}")

    def seed_supers(self):
        if not self._built:
            QMessageBox.warning(self, "Seed", "Build the package first")
            return
        channel = self._built.get('manifest', {}).get('channel', 'prod')
        if channel == 'test':
            try:
                from Support.ChangeManagement import filter_peers_by_cohort, peers_in_cohort
                roster = peers_in_cohort('test')
                # Prefer live peer rows filtered by cohort; fall back to roster-shaped targets
                supers = self.release_wizard.get_supernodes(cohort='test')
                if not supers and roster:
                    supers = [
                        {
                            'id': p.get('peer_id'),
                            'name': p.get('name'),
                            'ip': p.get('ip', ''),
                            'ip_local': p.get('ip_local', ''),
                        }
                        for p in roster if p.get('ip')
                    ]
            except Exception:
                supers = self.release_wizard.get_supernodes(cohort='test')
        else:
            supers = self.release_wizard.get_supernodes(cohort='prod')
        if not supers:
            QMessageBox.warning(self, "Seed", "No target peers found for this channel/cohort")
            return

        admin_ip = self._built['manifest'].get('admin_ip', '')
        init_release_seed_status(self._built['release_id'], supers)
        try:
            app = self.release_wizard.admin_app
            if app and hasattr(app, '_releases_fingerprint'):
                app._releases_fingerprint = None
            if app and hasattr(app, 'refresh_releases_list'):
                app.refresh_releases_list()
        except Exception:
            pass
        self.progress.setVisible(True)
        self.progress.setMaximum(len(supers))
        self.progress.setValue(0)
        self._worker = ReleaseWorker(
            self._built['enc_bytes'],
            self._built['release_id'],
            self._built['version'],
            admin_ip,
            self._built['manifest'].get('created_utc', ''),
            supers
        )
        self._worker.progress.connect(self._on_seed_progress)
        self._worker.finished.connect(self._on_seed_finished)
        self._worker.start()

    def _on_seed_progress(self, msg):
        self.log.append(msg)
        self.progress.setValue(self.progress.value() + 1)

    def _on_seed_finished(self, ok, message, failed):
        self.progress.setVisible(False)
        self.log.append(message)
        if failed:
            self.log.append("Failed: " + ", ".join(failed))
        try:
            app = self.release_wizard.admin_app
            if app and hasattr(app, '_releases_fingerprint'):
                app._releases_fingerprint = None
            if app and hasattr(app, 'refresh_releases_list'):
                app.refresh_releases_list()
        except Exception:
            pass
        if ok:
            try:
                from Support.ChangeManagement import mark_changeset_released
                for cs in self.release_wizard.changeset_page.selected_changesets():
                    mark_changeset_released(cs['cs_id'], self._built['release_id'])
            except Exception:
                pass
            QMessageBox.information(self, "Seeded", message)
        else:
            QMessageBox.warning(self, "Seed incomplete", message)


class ReleaseWizard(QWizard):
    def __init__(self, admin_app, action_editor=None, parent=None):
        super().__init__(parent)
        self.admin_app = admin_app
        self.setWindowTitle("Neuron Release Wizard")
        self.resize(900, 700)

        # ModernStyle puts Back/Next/Cancel in the footer (Aero hides Back in the title bar)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.WizardOption.NoCancelButtonOnLastPage, False)

        try:
            from Support.GlobalStyle import main_dialog_style
            self.setStyleSheet(main_dialog_style)
        except Exception:
            pass

        if action_editor is not None:
            self.action_editor = action_editor
        else:
            from ADMIN.AdminConsole import ActionSequenceEditor
            self.action_editor = ActionSequenceEditor()

        self.info_page = ReleaseInfoPage()
        self.changeset_page = ReleaseChangeSetPage()
        self.modules_page = ReleaseModulesPage(admin_app)
        self.files_page = ReleaseFilesPage(self.action_editor)
        self.db_page = ReleaseDatabasePage(self.action_editor)
        self.script_page = ReleaseScriptPage(self.action_editor)
        self.build_page = ReleaseBuildPage(self)
        self._force_channel = 'prod'

        self.addPage(self.changeset_page)
        self.addPage(self.info_page)
        self.addPage(self.modules_page)
        self.addPage(self.files_page)
        self.addPage(self.db_page)
        self.addPage(self.script_page)
        self.addPage(self.build_page)

        self.setButtonText(QWizard.WizardButton.BackButton, "Back")
        self.setButtonText(QWizard.WizardButton.NextButton, "Next")
        self.setButtonText(QWizard.WizardButton.CancelButton, "Cancel")
        self.setButtonText(QWizard.WizardButton.FinishButton, "Close")

    def get_supernodes(self, cohort=None):
        rows = query_database(
            init.pool_main_db,
            "SELECT id, name, ip, ip_local, type, online FROM Peers "
            "WHERE type = ? OR type = ? OR type = ?",
            (init.NODE_TYPE_APP_SERVER, init.NODE_TYPE_DB_SERVER, init.NODE_TYPE_SUPER_DB_SERVER)
        )
        peers = []
        for row in rows or []:
            if isinstance(row, dict):
                peers.append(row)
            else:
                peers.append({
                    'id': row[0] if len(row) > 0 else '',
                    'name': row[1] if len(row) > 1 else '',
                    'ip': row[2] if len(row) > 2 else '',
                    'ip_local': row[3] if len(row) > 3 else '',
                    'type': row[4] if len(row) > 4 else '',
                    'online': row[5] if len(row) > 5 else False
                })
        peers = [p for p in peers if p.get('ip') and p.get('ip_local')]
        if cohort in ('dev', 'test', 'prod'):
            try:
                from Support.ChangeManagement import filter_peers_by_cohort, peers_in_cohort
                filtered = filter_peers_by_cohort(peers, cohort)
                if filtered:
                    return filtered
                # For test channel, also allow any Peers that appear in cohort roster
                # even if not supernodes (beta testers)
                if cohort == 'test':
                    from Support.ChangeManagement import load_cohorts
                    roster_ids = {str(p.get('peer_id')) for p in peers_in_cohort('test')}
                    all_peers = query_database(
                        init.pool_main_db,
                        "SELECT id, name, ip, ip_local, type, online FROM Peers",
                        ''
                    ) or []
                    out = []
                    for row in all_peers:
                        if isinstance(row, dict):
                            p = row
                        else:
                            p = {
                                'id': row[0], 'name': row[1], 'ip': row[2],
                                'ip_local': row[3], 'type': row[4], 'online': row[5]
                            }
                        if str(p.get('id')) in roster_ids and p.get('ip') and p.get('ip_local'):
                            out.append(p)
                    return out
            except Exception:
                pass
        return peers
