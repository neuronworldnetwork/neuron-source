import shutil
import sys
import os
import json
import subprocess
import random
import sqlite3 as sql
from datetime import datetime, timezone
from functools import partial

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
if os.getcwd() != PROJECT_ROOT:
    os.chdir(PROJECT_ROOT)

from PySide6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QHBoxLayout,
                             QWidget, QPushButton, QListWidget, QTextEdit, QLabel,
                             QCheckBox, QLineEdit, QProgressBar, QMessageBox, QScrollArea,
                             QFileDialog, QComboBox, QGroupBox, QGridLayout, QFormLayout,
                             QListWidgetItem, QTabWidget, QTableWidget, QTableWidgetItem,
                             QSpinBox, QDoubleSpinBox, QRadioButton, QFrame, QSplitter,
                             QDialog, QAbstractItemView, QSizePolicy, QCalendarWidget)
from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtGui import QFont, QIcon, QPixmap, QTextCursor

from Support.Utils import query_database


def _is_module_pyd_name(filename, module_name):
    """True only for this module's .pyd, not NeuronResident when module is Neuron."""
    if filename == module_name + '.pyd':
        return True
    if filename.startswith(module_name + '.cp') and filename.endswith('.pyd'):
        return True
    return False


def _newest_pyd_for_module(module_dir, module_name):
    """Return the newest matching .pyd in module_dir, or None."""
    newest = None
    newest_mtime = -1
    if not os.path.isdir(module_dir):
        return None
    for name in os.listdir(module_dir):
        if not _is_module_pyd_name(name, module_name):
            continue
        path = os.path.join(module_dir, name)
        if not os.path.isfile(path):
            continue
        mtime = os.path.getmtime(path)
        if mtime > newest_mtime:
            newest_mtime = mtime
            newest = path
    return newest


# Import necessary modules from the main application
import Support.Header as init
from Support.SendData import send_packet
from Support import Lottery
from Support.GlobalStyle import main_dialog_style
from Support.Misc import generate_random_hash
from Support.Utils import load_pools, load_database_name
from ADMIN.version_manager import get_next_module_version, batch_increment_module_versions

# Standalone AdminConsole must resolve compile output via CM (Neuron_PROD),
# same as Neuron.py -- Header alone still defaults until NEURON_PYD_PATH is set.
if 'NEURON_PYD_PATH' not in os.environ:
    try:
        from Support.ChangeManagement import pyd_compile_output
        os.environ['NEURON_PYD_PATH'] = pyd_compile_output()
    except Exception:
        os.environ['NEURON_PYD_PATH'] = os.path.join(
            os.path.dirname(init.MAIN_PATH.rstrip('\\/')), 'Neuron_PROD'
        )
_pyd_out = os.environ['NEURON_PYD_PATH']
if not _pyd_out.endswith('\\') and not _pyd_out.endswith('/'):
    _pyd_out += '\\'
init.PATH_INSTALLATION_FOLDER = _pyd_out

load_database_name()
load_pools()

APP_IMAGE_KEYS = ["logo", "image1", "image2", "image3", "image4", "image5"]
REQUIRED_IMAGE_KEYS = ["logo", "image1"]

def get_timestamp():
    """Get current timestamp"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

class CompilationWorker(QThread):
    """Worker thread for module compilation"""
    progress = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, module_path):
        super().__init__()
        self.module_path = module_path

    def run(self):
        try:
            self.progress.emit(f"Compiling {os.path.basename(self.module_path)}...")

            if not os.path.exists(self.module_path):
                self.finished.emit(False, f"Module path not found: {self.module_path}")
                return

            # Handle single .py files - compile with Cython to .pyd
            if os.path.isfile(self.module_path) and self.module_path.endswith('.py'):
                try:
                    self.compile_py_to_pyd(self.module_path)
                    self.progress.emit(f"Successfully compiled {os.path.basename(self.module_path)} to .pyd")
                    self.finished.emit(True, "Compilation successful")
                    return
                except Exception as e:
                    self.finished.emit(False, f"Compilation failed: {str(e)}")
                    return

            # Handle package directories with setup.py or pyproject.toml
            if os.path.isdir(self.module_path):
                if not os.path.exists(os.path.join(self.module_path, 'setup.py')) and \
                   not os.path.exists(os.path.join(self.module_path, 'pyproject.toml')):
                    self.finished.emit(False, f"No setup.py or pyproject.toml found in {self.module_path}")
                    return

                os.environ['MODULE_TO_COMPILE'] = self.module_path
                command = [sys.executable, "-m", "pip", "install", "-e", self.module_path]
                result = subprocess.run(command, capture_output=True, text=True, check=True)

                # Cleanup .c files (but keep .pyd files for callback to handle)
                self.cleanup_c_files(self.module_path)

                self.progress.emit(f"Successfully compiled {os.path.basename(self.module_path)}")
                self.finished.emit(True, "Compilation successful")
                return

            self.finished.emit(False, f"Invalid module path: {self.module_path}")

        except subprocess.CalledProcessError as e:
            error_msg = f"Compilation failed: {e.stderr if e.stderr else str(e)}"
            self.finished.emit(False, error_msg)
        except Exception as e:
            self.finished.emit(False, f"Unexpected error: {str(e)}")

    def compile_py_to_pyd(self, py_file):
        """Compile a .py file to .pyd using Cython"""
        module_name = os.path.splitext(os.path.basename(py_file))[0]
        module_dir = os.path.dirname(py_file)
        py_filename = os.path.basename(py_file)
        # Use absolute path for validation
        absolute_py_file = os.path.abspath(py_file)
        
        print(f"[COMPILE DEBUG] Starting compilation for: {py_file}")
        print(f"[COMPILE DEBUG] Absolute py file: {absolute_py_file}")
        print(f"[COMPILE DEBUG] Module name: {module_name}")
        print(f"[COMPILE DEBUG] Module dir: {module_dir}")
        
        try:
            import Cython.Build
        except ImportError:
            raise Exception("Cython is not installed. Install with: pip install Cython")

        # Cython in this toolchain rejects: from __future__ import annotations
        try:
            with open(absolute_py_file, 'r', encoding='utf-8') as f:
                src_lines = f.readlines()
            cleaned = [ln for ln in src_lines if ln.strip() != 'from __future__ import annotations']
            if len(cleaned) != len(src_lines):
                with open(absolute_py_file, 'w', encoding='utf-8') as f:
                    f.writelines(cleaned)
                print(f"[COMPILE DEBUG] Stripped future annotations from {py_filename}")
        except Exception as e:
            print(f"[COMPILE DEBUG] Could not strip future annotations: {e}")

        # Clean up any leftover files from previous compilation attempts
        for cleanup_dir in ['build', 'build_temp_cython']:
            cleanup_path = os.path.join(module_dir, cleanup_dir)
            if os.path.exists(cleanup_path):
                try:
                    shutil.rmtree(cleanup_path)
                except:
                    pass

        # Try to rename/remove old .pyd files that might be locked
        for file in os.listdir(module_dir):
            if not _is_module_pyd_name(file, module_name):
                continue
            pyd_path = os.path.join(module_dir, file)
            try:
                os.remove(pyd_path)
            except PermissionError:
                # File is locked, try renaming it instead
                try:
                    backup_path = pyd_path + '.old'
                    if os.path.exists(backup_path):
                        os.remove(backup_path)
                    os.rename(pyd_path, backup_path)
                    print(f"[COMPILE DEBUG] Renamed locked .pyd to: {backup_path}")
                except Exception as e:
                    print(f"[COMPILE DEBUG] Could not rename locked .pyd {file}: {e}")
                    # Continue anyway, the new build might still work
            except Exception as e:
                print(f"[COMPILE DEBUG] Could not remove .pyd {file}: {e}")

        for cleanup_file in os.listdir(module_dir):
            if cleanup_file.startswith(module_name) and cleanup_file.endswith('.c'):
                try:
                    os.remove(os.path.join(module_dir, cleanup_file))
                except:
                    pass

        # Run build from the module directory itself
        # This ensures .pyd files are created in the correct location regardless of directory depth
        build_dir = module_dir

        print(f"[COMPILE DEBUG] Build dir: {build_dir}")

        # Create setup.py to compile the module with Cython
        # IMPORTANT: Explicitly set extension name to prevent setuptools from auto-detecting package namespace
        # When building from a subdirectory, setuptools tries to create "Support.Taskbar" but we want just "Taskbar"
        setup_content = f"""from setuptools import setup, Extension
from Cython.Build import cythonize
import os

setup(
    name='{module_name}',
    ext_modules=cythonize(
        [Extension(name='{module_name}', sources=['{py_filename}'])],
        language_level=3,
        # Flask/SocketIO call views with kwargs; without these, one-arg Cython
        # defs become METH_O and raise "takes no keyword arguments".
        compiler_directives={{
            'boundscheck': False,
            'binding': True,
            'always_allow_keywords': True,
        }},
    )
)
"""
        setup_file = os.path.join(module_dir, 'setup_cython_temp.py')
        temp_build_dir = os.path.join(module_dir, 'build_temp_cython')
        
        try:
            # Ensure the temporary build directory exists
            os.makedirs(temp_build_dir, exist_ok=True)
            
            with open(setup_file, 'w') as f:
                f.write(setup_content)
            
            # Build the extension with explicit build directories
            # Run from the module directory for proper path resolution
            # NOTE: --inplace creates .pyd in source directory, --build-temp puts temp files elsewhere
            build_cmd = [
                sys.executable, 
                setup_file, 
                "build_ext", 
                "--inplace",
                f"--build-temp={temp_build_dir}"
            ]
            print(f"[COMPILE DEBUG] Running build command from dir: {build_dir}")
            print(f"[COMPILE DEBUG] Build command: {' '.join(build_cmd)}")
            
            result = subprocess.run(build_cmd, capture_output=True, text=True, cwd=build_dir)
            
            print(f"[COMPILE DEBUG] Build return code: {result.returncode}")
            if result.stdout:
                print(f"[COMPILE DEBUG] Build stdout: {result.stdout[:500]}")
            if result.stderr:
                print(f"[COMPILE DEBUG] Build stderr: {result.stderr[:500]}")
            
            if result.returncode != 0:
                error_output = result.stderr if result.stderr else result.stdout

                # Check for file access denied error (locked .pyd file)
                if "access is denied" in error_output.lower() or "permission" in error_output.lower():
                    raise Exception(
                        f"Cython build failed - file is locked (likely still loaded in memory):\n"
                        f"{error_output}\n\n"
                        f"SOLUTION: The old .pyd file is still in use. Try:\n"
                        f"1. Restart the application or Python interpreter\n"
                        f"2. Delete .pyd.old backup files in {module_dir}\n"
                        f"3. Try compiling again"
                    )
                else:
                    raise Exception(f"Cython build failed:\n{error_output}")

            # Use the newest matching .pyd. A locked leftover module.pyd must not
            # beat a freshly built module.cp312-win_amd64.pyd.
            py_mtime = os.path.getmtime(absolute_py_file)
            found_pyd = _newest_pyd_for_module(module_dir, module_name)
            if found_pyd:
                print(f"[COMPILE DEBUG] Found .pyd file at: {found_pyd}")

            if not found_pyd or os.path.getmtime(found_pyd) < py_mtime:
                print(f"[COMPILE DEBUG] Files in {module_dir}:")
                for f in os.listdir(module_dir):
                    print(f"[COMPILE DEBUG]   - {f}")
                raise Exception(
                    f"Compilation did not produce a .pyd newer than {py_filename} in {module_dir}. "
                    f"Close anything locking {module_name}.pyd and compile again."
                )
        finally:
            # Cleanup temporary files (but NOT the .pyd files we just created)
            if os.path.exists(setup_file):
                try:
                    os.remove(setup_file)
                except:
                    pass
            # Also cleanup build directories
            for build_dir_name in ['build', 'build_temp_cython']:
                cleanup_build_dir = os.path.join(module_dir, build_dir_name)
                if os.path.exists(cleanup_build_dir):
                    try:
                        shutil.rmtree(cleanup_build_dir)
                    except:
                        pass
            # Cleanup only .c files from source folder (not .pyd files!)
            for file in os.listdir(module_dir):
                if file.endswith('.c'):
                    try:
                        os.remove(os.path.join(module_dir, file))
                    except:
                        pass

    def cleanup_c_files(self, source_dir):
        """Delete all .c files from source_dir"""
        for root, dirs, files in os.walk(source_dir):
            for file in files:
                if file.endswith('.c'):
                    try:
                        os.remove(os.path.join(root, file))
                    except:
                        pass


class PackageCreateWorker(QThread):
    """Build Neuron.rar distribution package from Neuron_PROD."""
    progress = Signal(str)
    step = Signal(int, int)
    finished = Signal(bool, str)

    PACKAGE_STEPS = 8
    KEEP_TABLES = {
        'Apps', 'Audio', 'Functions', 'Me', 'Modules', 'NodeType',
        'Peers', 'Rooms', 'RoomTypes', 'Subscriptions', 'SystemInfo'
    }

    def __init__(self):
        super().__init__()
        self._abort = False

    def request_abort(self):
        self._abort = True

    def _check_abort(self):
        if self._abort:
            raise InterruptedError("Package creation aborted by user")

    def _emit(self, message, current_step=None):
        self.progress.emit(message)
        if current_step is not None:
            self.step.emit(current_step, self.PACKAGE_STEPS)

    def run(self):
        try:
            self._emit("Starting package creation...", 0)
            self._step_remove_htdocs_template()
            self._check_abort()
            self._step_prepare_template_db()
            self._check_abort()
            self._step_update_outdated_pyds()
            self._check_abort()
            self._step_sync_user_and_json()
            self._check_abort()
            self._step_remove_path_list_files()
            self._check_abort()
            self._step_sync_site_packages()
            self._check_abort()
            self._step_build_frontend_vault()
            self._check_abort()
            # Re-sync after prune/vault so home sites + _lang cannot be left stale/missing.
            self._sync_package_home_sites()
            self._check_abort()
            self._step_create_neuron_rar()
            self._emit("Package creation completed.", self.PACKAGE_STEPS)
            self.finished.emit(True, "Package created successfully")
        except InterruptedError as e:
            self.finished.emit(False, str(e))
        except Exception as e:
            self.finished.emit(False, f"Package creation failed: {e}")

    def _install_root(self):
        return os.path.normpath(init.PATH_INSTALLATION_FOLDER.rstrip('\\/'))

    def _main_root(self):
        return os.path.normpath(init.MAIN_PATH)

    def _neuron_rar_path(self):
        """Always write Neuron.rar to SOURCE System\\neuron\\htdocs, never inside Neuron_PROD."""
        # Prefer the tree that contains this AdminConsole.py (source), not cwd/PROD MAIN_PATH.
        source_root = os.path.normpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        install = self._install_root()
        if os.path.normcase(source_root) == os.path.normcase(install):
            source_root = self._main_root()
        htdocs = os.path.join(source_root, "System", "neuron", "htdocs")
        dest = os.path.abspath(os.path.join(htdocs, "Neuron.rar"))
        install_abs = os.path.abspath(install)
        if dest.lower().startswith(install_abs.lower() + os.sep) or dest.lower() == install_abs.lower():
            raise RuntimeError(
                "Refusing to write Neuron.rar inside Neuron_PROD; source htdocs unresolved"
            )
        os.makedirs(htdocs, exist_ok=True)
        return dest

    def _step_remove_htdocs_template(self):
        self._emit("Step 1/8: Removing data Template.db...", 1)
        for path in (
            os.path.join(init.PATH_SQLITE, "Template.db"),
            os.path.join(self._install_root(), "System", "neuron", "data", "Template.db"),
        ):
            if os.path.isfile(path):
                os.remove(path)
                self._emit(f"Removed {path}")
            else:
                self._emit(f"No Template.db to remove at {path}")

    def _step_prepare_template_db(self):
        self._emit("Step 2/8: Preparing Template.db...", 2)
        src_db = os.path.join(init.PATH_SQLITE, init.DATABASE_NAME)
        if not os.path.isfile(src_db):
            raise FileNotFoundError(f"Source database not found: {src_db}")

        # 1) Copy live main DB -> Neuron data Template.db
        main_data_dir = init.PATH_SQLITE
        os.makedirs(main_data_dir, exist_ok=True)
        template_db = os.path.join(main_data_dir, "Template.db")
        if os.path.isfile(template_db):
            os.remove(template_db)

        self._emit(f"Copying main DB to {template_db}")
        src_conn = sql.connect(src_db)
        try:
            dst_conn = sql.connect(template_db)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()

        # 2) Empty non-KEEP tables on that Template.db
        conn = sql.connect(template_db)
        try:
            cur = conn.cursor()
            tables = [
                r[0] for r in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            for table in tables:
                self._check_abort()
                if table not in self.KEEP_TABLES:
                    cur.execute(f'DELETE FROM "{table}"')

            cur.execute(
                'DELETE FROM Rooms WHERE rowid NOT IN '
                '(SELECT rowid FROM Rooms ORDER BY rowid LIMIT 1)'
            )

            # Keep all Peers from the main DB; ensure ME is present as a peer row.
            me = cur.execute(
                "SELECT id, node_name, node_type, ip, ip_local, user_image, comm_type "
                "FROM Me LIMIT 1"
            ).fetchone()
            peer_stats = (
                '{"schema_ver":1,"private":{},"ping":{},"ai":{},"observed":{"rel":{'
                '"presence":0.0,"storage":null,"exchange":null,"identity":1.2456944444444444,'
                '"contrib":null,"ethics":null,"steward":null,"community":null,"claimed":null,'
                '"attest":null,"ok_storage":0,"fail_storage":0,"ok_exchange":0,"fail_exchange":0,'
                '"ok_hold":0,"fail_hold":0,"n_attest":0,"n_imp":0,"first_seen":1786949863,'
                '"last_tick":0.0,"integrity_fail":0,"online_sec":0.0,"claimed_n":0,"claimed_ts":0,'
                '"revoked":0,"alloc_gb":0.0,"alloc_baseline_gb":0.0,"hw_ram":0.0,"hw_net":0.0,'
                '"hw_cpu":0.0,"hw_ram0":0.0,"hw_net0":0.0,"hw_cpu0":0.0,"neuron_mismatch_n":0,'
                '"milestones":0,"badges":0,"held_att":[],"held_imp":[]}}}'
            )
            cur.execute(
                "DELETE FROM Peers WHERE id = ? OR peer_id = ?",
                (me[0], me[0]),
            )
            cur.execute(
                "INSERT INTO Peers ("
                "id, peer_id, room_id, name, url, ip, ip_local, city, country, type, "
                "reliability, online, last_ping_in, image, x, y, blocked, peering, "
                "shared_folder, shared_printer, comm_type, stats"
                ") VALUES (?,?,0,?,'',?,?,'','',?,0.4,0,0,?,0.5,0.5,0,0,'','',?,?)",
                (
                    me[0], me[0], me[1], me[3], me[4], me[2],
                    me[5], me[6], peer_stats,
                ),
            )
            peer_count = cur.execute("SELECT COUNT(*) FROM Peers").fetchone()[0]
            self._emit(f"Peers kept for Template.db: {peer_count} (including ME)")
            cur.execute("UPDATE Me SET user_neurons = 0, comm_type = 1, node_reliability = 0")
            row = cur.execute("SELECT stats FROM Me LIMIT 1").fetchone()
            if row:
                from Support.ReliabilityCert import reset_new_node_reliability
                cur.execute("UPDATE Me SET stats = ?", (reset_new_node_reliability(row[0]),))
            cur.execute("UPDATE SystemInfo SET show_help = 0, start = 1")
            conn.commit()
            conn.execute("VACUUM")
        finally:
            conn.close()
        self._sync_db_schema(src_db, template_db, label="Template.db")
        self._emit(f"Template.db ready at {template_db}")

        # 3) Copy emptied Template.db into Neuron_PROD package data
        data_dir = os.path.join(self._install_root(), "System", "neuron", "data")
        os.makedirs(data_dir, exist_ok=True)
        pyd_template = os.path.join(data_dir, "Template.db")
        if os.path.isfile(pyd_template):
            os.remove(pyd_template)
        shutil.copy2(template_db, pyd_template)
        self._emit(f"Copied Template.db to {pyd_template}")
        # Ensure Neuron_PROD Template.db tables/columns match the live main DB.
        self._sync_db_schema(src_db, pyd_template, label="Neuron_PROD Template.db")

        self._copy_runtime_databases(data_dir)
        self._empty_copied_runtime_databases(data_dir)
        self._prepare_package_dns(data_dir)
        self._sync_package_home_sites()
        self._delete_hash_databases(data_dir)

    def _norm_sql(self, sql):
        if not sql:
            return ''
        return ' '.join(sql.replace('"', '').replace("'", '').split()).upper()

    def _table_col_sig(self, cur, table):
        rows = cur.execute(f'PRAGMA table_info("{table}")').fetchall()
        sig = []
        for _cid, name, ctype, notnull, dflt, pk in rows:
            default = None if dflt is None else str(dflt)
            sig.append((name, (ctype or '').upper(), int(notnull), default, int(pk)))
        return sig

    def _rebuild_dst_table(self, dst_cur, src_cur, table, create_sql, copy_data=True):
        if not copy_data:
            dst_cur.execute(f'DROP TABLE IF EXISTS "{table}"')
            dst_cur.execute(create_sql)
            return
        tmp = table + "__old_schema"
        dst_cur.execute(f'DROP TABLE IF EXISTS "{tmp}"')
        dst_cur.execute(f'ALTER TABLE "{table}" RENAME TO "{tmp}"')
        dst_cur.execute(create_sql)
        src_names = [r[1] for r in src_cur.execute(f'PRAGMA table_info("{table}")')]
        old_names = {
            r[1] for r in dst_cur.execute(f'PRAGMA table_info("{tmp}")')
        }
        common = [c for c in src_names if c in old_names]
        if common:
            cols = ",".join(f'"{c}"' for c in common)
            dst_cur.execute(
                f'INSERT INTO "{table}" ({cols}) SELECT {cols} FROM "{tmp}"'
            )
        dst_cur.execute(f'DROP TABLE "{tmp}"')

    def _schema_objects(self, cur, obj_type):
        return {
            r[0]: r[1] for r in cur.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type=? AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL",
                (obj_type,),
            ).fetchall()
        }

    def _drop_schema_object(self, cur, obj_type, name):
        if obj_type == 'index':
            cur.execute(f'DROP INDEX IF EXISTS "{name}"')
        elif obj_type == 'trigger':
            cur.execute(f'DROP TRIGGER IF EXISTS "{name}"')
        elif obj_type == 'view':
            cur.execute(f'DROP VIEW IF EXISTS "{name}"')

    def _sync_schema_named_objects(self, src_cur, dst_cur, obj_type, label):
        src_objs = self._schema_objects(src_cur, obj_type)
        dst_objs = self._schema_objects(dst_cur, obj_type)
        changed = False
        for name in sorted(set(dst_objs) - set(src_objs)):
            self._check_abort()
            self._drop_schema_object(dst_cur, obj_type, name)
            self._emit(f"{label}: dropped extra {obj_type} {name}")
            changed = True
        for name, sql in src_objs.items():
            self._check_abort()
            if name in dst_objs and self._norm_sql(dst_objs[name]) == self._norm_sql(sql):
                continue
            if name in dst_objs:
                self._drop_schema_object(dst_cur, obj_type, name)
            dst_cur.execute(sql)
            self._emit(f"{label}: synced {obj_type} {name}")
            changed = True
        return changed

    def _sync_db_schema(self, src_path, dst_path, label=None, copy_data=True):
        """Make dst tables, columns, keys, indexes, views, and triggers match src."""
        label = label or os.path.basename(dst_path)
        src = sql.connect(src_path)
        try:
            dst = sql.connect(dst_path)
            try:
                src_cur = src.cursor()
                dst_cur = dst.cursor()
                dst_cur.execute("PRAGMA foreign_keys=OFF")
                src_tables = {
                    r[0]: r[1] for r in src_cur.execute(
                        "SELECT name, sql FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                }
                dst_table_names = {
                    r[0] for r in dst_cur.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                }

                changed = False
                for table in sorted(dst_table_names - set(src_tables)):
                    self._check_abort()
                    dst_cur.execute(f'DROP TABLE IF EXISTS "{table}"')
                    self._emit(f"{label}: dropped extra table {table}")
                    changed = True

                for table, create_sql in src_tables.items():
                    self._check_abort()
                    if not create_sql:
                        continue
                    exists = table in dst_table_names
                    if not exists:
                        dst_cur.execute(create_sql)
                        self._emit(f"{label}: created missing table {table}")
                        changed = True
                        continue
                    src_sig = self._table_col_sig(src_cur, table)
                    dst_sig = self._table_col_sig(dst_cur, table)
                    dst_sql_row = dst_cur.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    ).fetchone()
                    dst_sql = dst_sql_row[0] if dst_sql_row else ''
                    if src_sig == dst_sig and self._norm_sql(create_sql) == self._norm_sql(dst_sql):
                        continue
                    self._rebuild_dst_table(dst_cur, src_cur, table, create_sql, copy_data=copy_data)
                    self._emit(f"{label}: rebuilt table {table} to match source")
                    changed = True

                if self._sync_schema_named_objects(src_cur, dst_cur, 'index', label):
                    changed = True
                if self._sync_schema_named_objects(src_cur, dst_cur, 'view', label):
                    changed = True
                if self._sync_schema_named_objects(src_cur, dst_cur, 'trigger', label):
                    changed = True

                if changed:
                    dst.commit()
                    dst.execute("VACUUM")
                else:
                    self._emit(f"{label}: schema already matches source")
            finally:
                dst.close()
        finally:
            src.close()

    def _is_hash_db_name(self, name):
        """True for the per-install hash-named main DB (and its wal/shm/backup)."""
        if not name:
            return False
        stem = name
        for suffix in ('-wal', '-shm'):
            if stem.endswith(suffix):
                stem = stem[:-len(suffix)]
        stem = stem.rstrip('§')
        db_name = getattr(init, 'DATABASE_NAME', '') or ''
        if db_name and (stem == db_name or name == db_name):
            return True
        if '.' in stem:
            return False
        return len(stem) >= 20 and stem.isalnum()

    def _sqlite_backup_copy(self, src_path, dst_path):
        """Copy a SQLite DB safely (handles live WAL) via the backup API."""
        if os.path.isfile(dst_path):
            os.remove(dst_path)
        for suffix in ('-wal', '-shm'):
            side = dst_path + suffix
            if os.path.isfile(side):
                try:
                    os.remove(side)
                except OSError:
                    pass
        src_conn = sql.connect(src_path)
        try:
            dst_conn = sql.connect(dst_path)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()

    def _copy_runtime_databases(self, data_dir):
        src_dir = init.PATH_SQLITE
        os.makedirs(data_dir, exist_ok=True)
        for name in os.listdir(src_dir):
            self._check_abort()
            if name.endswith('-wal') or name.endswith('-shm'):
                continue
            if name in ('Template.db', 'Admin.db') or self._is_hash_db_name(name):
                continue
            src = os.path.join(src_dir, name)
            if not os.path.isfile(src):
                continue
            if not name.lower().endswith('.db'):
                continue
            dst = os.path.join(data_dir, name)
            self._sqlite_backup_copy(src, dst)
            self._emit(f"Copied database {name}")

    # Sidecar tables that must keep their seed rows in the package (schema + data).
    RUNTIME_DB_KEEP_TABLES = {
        'Languages.db': frozenset({'Language_codes'}),
    }

    def _empty_one_runtime_database(self, path, name):
        if name == 'Neuron - DNS.db':
            self._ensure_dns_table(path)
        conn = sql.connect(path)
        try:
            cur = conn.cursor()
            tables = [
                r[0] for r in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            is_dns_db = (name == 'Neuron - DNS.db')
            keep_dns = tuple(s.lower() for s in self.PACKAGE_HOME_SITES)
            keep_tables = self.RUNTIME_DB_KEEP_TABLES.get(name, frozenset())
            for table in tables:
                self._check_abort()
                if table in keep_tables:
                    continue
                if is_dns_db and table == 'DNS':
                    placeholders = ", ".join("?" for _ in keep_dns)
                    cur.execute(
                        f"DELETE FROM DNS WHERE LOWER(name) NOT IN ({placeholders})",
                        keep_dns,
                    )
                    continue
                if is_dns_db and table == 'DNS_mig':
                    continue
                cur.execute(f'DELETE FROM "{table}"')
            conn.commit()
            conn.execute("VACUUM")
        finally:
            conn.close()

    def _restore_runtime_keep_tables(self, src_path, dst_path, tables):
        """Re-seed keep tables from the live sidecar after schema sync."""
        if not tables:
            return
        src = sql.connect(src_path)
        try:
            dst = sql.connect(dst_path)
            try:
                for table in tables:
                    self._check_abort()
                    src_cols = [r[1] for r in src.execute(f'PRAGMA table_info("{table}")')]
                    dst_cols = {
                        r[1] for r in dst.execute(f'PRAGMA table_info("{table}")')
                    }
                    common = [c for c in src_cols if c in dst_cols]
                    if not common:
                        continue
                    cols = ",".join(f'"{c}"' for c in common)
                    dst.execute(f'DELETE FROM "{table}"')
                    rows = src.execute(f'SELECT {cols} FROM "{table}"').fetchall()
                    if rows:
                        placeholders = ",".join("?" for _ in common)
                        dst.executemany(
                            f'INSERT INTO "{table}" ({cols}) VALUES ({placeholders})',
                            rows,
                        )
                    self._emit(f"Restored seed rows for {os.path.basename(dst_path)}.{table}")
                dst.commit()
            finally:
                dst.close()
        finally:
            src.close()

    def _empty_copied_runtime_databases(self, data_dir):
        """Empty sidecar DBs and make their schema match the live Neuron copies."""
        if not os.path.isdir(data_dir):
            return
        src_dir = init.PATH_SQLITE
        for name in os.listdir(data_dir):
            self._check_abort()
            if name.endswith('-wal') or name.endswith('-shm'):
                continue
            if name in ('Template.db', 'Admin.db') or self._is_hash_db_name(name):
                continue
            if not name.lower().endswith('.db'):
                continue
            path = os.path.join(data_dir, name)
            if not os.path.isfile(path):
                continue
            src = os.path.join(src_dir, name)
            if not os.path.isfile(src):
                os.remove(path)
                self._emit(f"Removed orphan package database {name}")
                continue
            try:
                self._empty_one_runtime_database(path, name)
            except sql.DatabaseError as e:
                self._emit(f"Recovering malformed {name}: {e}")
                self._sqlite_backup_copy(src, path)
                self._empty_one_runtime_database(path, name)
            self._sync_db_schema(src, path, label=name, copy_data=False)
            keep = self.RUNTIME_DB_KEEP_TABLES.get(name)
            if keep:
                self._restore_runtime_keep_tables(src, path, keep)
            self._emit(f"Synced schema / emptied tables in {name}")

    def _source_dns_create_sql(self):
        src_dns = os.path.join(init.PATH_SQLITE, "Neuron - DNS.db")
        if not os.path.isfile(src_dns):
            return None
        conn = sql.connect(src_dns)
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='DNS'"
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def _ensure_dns_table(self, dns_path):
        """Repair interrupted DNS migration (DNS_mig left behind) and ensure DNS exists."""
        conn = sql.connect(dns_path)
        try:
            cur = conn.cursor()
            tables = {
                r[0] for r in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if 'DNS' not in tables and 'DNS_mig' in tables:
                cur.execute("ALTER TABLE DNS_mig RENAME TO DNS")
                conn.commit()
                self._emit("Repaired DNS table (renamed DNS_mig -> DNS)")
                tables.add('DNS')
            if 'DNS' not in tables:
                create_sql = self._source_dns_create_sql()
                if create_sql:
                    cur.execute(create_sql)
                    conn.commit()
                    self._emit("Created missing DNS table")
        finally:
            conn.close()

    def _prepare_package_dns(self, data_dir):
        """Keep only NeuronWorldNetwork and Welcome in DNS, owned by the template admin id."""
        dns_path = os.path.join(data_dir, "Neuron - DNS.db")
        if not os.path.isfile(dns_path):
            return
        self._ensure_dns_table(dns_path)
        owner_id = getattr(init, 'ADMIN_TRUSTED_PEER_ID', None)
        keep_lower = tuple(name.lower() for name in self.PACKAGE_HOME_SITES)
        conn = sql.connect(dns_path)
        try:
            cur = conn.cursor()
            placeholders = ", ".join("?" for _ in keep_lower)
            cur.execute(
                f"DELETE FROM DNS WHERE LOWER(name) NOT IN ({placeholders})",
                keep_lower,
            )
            for site_name in self.PACKAGE_HOME_SITES:
                cur.execute(
                    "SELECT name FROM DNS WHERE LOWER(name) = LOWER(?)",
                    (site_name,)
                )
                if cur.fetchone():
                    cur.execute(
                        "UPDATE DNS SET name = ?, peer_id = ?, published = 1 WHERE LOWER(name) = LOWER(?)",
                        (site_name, owner_id, site_name)
                    )
                else:
                    cur.execute(
                        "INSERT INTO DNS (name, peer_id, published) VALUES (?, ?, 1)",
                        (site_name, owner_id)
                    )
            conn.commit()
        finally:
            conn.close()
        self._emit("Kept NeuronWorldNetwork and Welcome DNS records")

    def _replace_tree(self, src, dst):
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        elif os.path.isfile(dst):
            os.remove(dst)
        shutil.copytree(src, dst)

    def _sync_package_home_sites(self):
        """Ship NeuronWorldNetwork, Welcome, and shared _lang site translation assets."""
        src_home = os.path.join(self._main_root(), "System", "neuron", "htdocs", "home")
        dst_home = os.path.join(self._install_root(), "System", "neuron", "htdocs", "home")
        os.makedirs(dst_home, exist_ok=True)

        nwn_src = os.path.join(src_home, "NeuronWorldNetwork")
        if not os.path.isdir(nwn_src):
            raise FileNotFoundError(f"Missing home site: {nwn_src}")
        self._check_abort()
        self._replace_tree(nwn_src, os.path.join(dst_home, "NeuronWorldNetwork"))
        self._emit("Copied home site NeuronWorldNetwork")

        # Welcome folder was retired in source; mirror NWN so DNS/setup URLs still work.
        welcome_src = os.path.join(src_home, "Welcome")
        welcome_dst = os.path.join(dst_home, "Welcome")
        self._check_abort()
        if os.path.isdir(welcome_src):
            self._replace_tree(welcome_src, welcome_dst)
            self._emit("Copied home site Welcome")
        else:
            self._replace_tree(nwn_src, welcome_dst)
            self._emit("Mirrored NeuronWorldNetwork to Welcome (source Welcome missing)")

        kept_assets = set()
        for asset_name in self.PACKAGE_HOME_ASSET_DIRS:
            self._check_abort()
            src = os.path.join(src_home, asset_name)
            dst = os.path.join(dst_home, asset_name)
            # Required shared assets (e.g. _lang). Never delete a prior PROD copy when
            # source is missing — that left home sites broken (../_lang/*.js 404).
            if not os.path.isdir(src):
                raise FileNotFoundError(
                    f"Missing required home asset for package: {src}"
                )
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            def _lang_ignore(directory, names, _skip=self.PACKAGE_HOME_LANG_SKIP):
                ignored = []
                for name in names:
                    if name in _skip or name.endswith('.tmp'):
                        ignored.append(name)
                return ignored
            shutil.copytree(src, dst, ignore=_lang_ignore)
            kept_assets.add(asset_name)
            self._emit(f"Copied home asset {asset_name}")

        pref_dst = os.path.join(dst_home, "preferred_lang.json")
        with open(pref_dst, "w", encoding="utf-8") as handle:
            json.dump({"lang": "en", "enabled": True}, handle, indent=2)
        self._emit("Wrote default preferred_lang.json (en)")

        keep = set(self.PACKAGE_HOME_SITES) | kept_assets | {
            "preferred_lang.json",
        }
        for name in list(os.listdir(dst_home)):
            self._check_abort()
            if name in keep:
                continue
            path = os.path.join(dst_home, name)
            if os.path.isdir(path) or os.path.isfile(path):
                self._move_to_old(path)

    def _delete_hash_databases(self, data_dir):
        if not os.path.isdir(data_dir):
            return
        for name in os.listdir(data_dir):
            self._check_abort()
            if name.endswith('-wal') or name.endswith('-shm') or self._is_hash_db_name(name):
                path = os.path.join(data_dir, name)
                if os.path.isfile(path):
                    os.remove(path)
                    self._emit(f"Removed {name} from package data folder")

    def _find_dest_pyd(self, pyd_dir, module):
        """Return path to existing dest .pyd for module, or expected simple path if missing."""
        expected = os.path.join(pyd_dir, module + '.pyd')
        if os.path.isfile(expected):
            return expected
        if os.path.isdir(pyd_dir):
            for name in os.listdir(pyd_dir):
                if name.endswith('.pyd') and (name == module + '.pyd' or name.startswith(module + '.cp')):
                    return os.path.join(pyd_dir, name)
        return expected

    def _pyd_pairs(self):
        """Pair source .py modules (Neuron root + Support + Apps) with dest .pyd paths."""
        pairs = []
        install = self._install_root()
        main = self._main_root()
        skip_names = {
            '__init__.py', 'setup.py', 'setup_cython_temp.py',
            'setup_frontend_vault.py', 'build_frontend_vault.py',
            # CM bridge loads external NeuronChangeManagement; never Cython-compile it
            'ChangeManagement.py',
        }
        for rel_dir in ('', 'Support'):
            py_dir = os.path.join(main, rel_dir) if rel_dir else main
            pyd_dir = os.path.join(install, rel_dir) if rel_dir else install
            if not os.path.isdir(py_dir):
                continue
            for name in os.listdir(py_dir):
                if not name.endswith('.py') or name in skip_names:
                    continue
                module = name[:-3]
                if module == 'FrontendVault':
                    continue
                py_path = os.path.join(py_dir, name)
                if not os.path.isfile(py_path):
                    continue
                pyd_path = self._find_dest_pyd(pyd_dir, module)
                pairs.append((py_path, pyd_path, rel_dir, module))

        apps_src = os.path.join(main, 'Apps')
        if os.path.isdir(apps_src):
            for root, dirs, files in os.walk(apps_src):
                dirs[:] = [
                    d for d in dirs
                    if d not in self.APPS_SKIP_DIRS and not d.startswith('.')
                ]
                rel_dir = os.path.relpath(root, main)
                pyd_dir = os.path.join(install, rel_dir)
                for name in files:
                    if not name.endswith('.py') or name in skip_names:
                        continue
                    lower = name.lower()
                    if lower.startswith('test_'):
                        continue
                    module = name[:-3]
                    py_path = os.path.join(root, name)
                    if not os.path.isfile(py_path):
                        continue
                    pyd_path = self._find_dest_pyd(pyd_dir, module)
                    pairs.append((py_path, pyd_path, rel_dir, module))
        return pairs

    def _deposit_compiled_pyd(self, py_path, module_name, rel_dir):
        module_dir = os.path.dirname(os.path.abspath(py_path))
        py_mtime = os.path.getmtime(py_path)
        pyd_file = _newest_pyd_for_module(module_dir, module_name)
        if not pyd_file or os.path.getmtime(pyd_file) < py_mtime:
            raise FileNotFoundError(
                f"Compiled .pyd not found or older than source for {module_name}"
            )

        dest_dir = self._install_root() if not rel_dir else os.path.join(self._install_root(), rel_dir)
        os.makedirs(dest_dir, exist_ok=True)
        actual_name = os.path.basename(pyd_file)
        if '.cp' in actual_name:
            simple = os.path.join(os.path.dirname(pyd_file), module_name + '.pyd')
            try:
                os.replace(pyd_file, simple)
                pyd_file = simple
            except OSError:
                pass
        dest_file = os.path.join(dest_dir, module_name + '.pyd')
        shutil.copy2(pyd_file, dest_file)
        os.utime(dest_file, None)
        try:
            os.remove(pyd_file)
        except OSError:
            pass
        for file in os.listdir(module_dir):
            if file.startswith(module_name) and file.endswith('.c'):
                try:
                    os.remove(os.path.join(module_dir, file))
                except OSError:
                    pass
        return dest_file

    def _step_update_outdated_pyds(self):
        self._emit("Step 3/8: Checking/updating .pyd files (Support + Apps)...", 3)
        compiler = CompilationWorker("")
        outdated = []
        for py_path, pyd_path, rel_dir, module in self._pyd_pairs():
            missing = not os.path.isfile(pyd_path)
            older = (not missing) and (os.path.getmtime(py_path) > os.path.getmtime(pyd_path))
            if missing or older:
                outdated.append((py_path, rel_dir, module, missing))
        if not outdated:
            self._emit("All .pyd files are up to date")
            return
        self._emit(f"Found {len(outdated)} module(s) to compile (missing or outdated)")
        for i, (py_path, rel_dir, module, missing) in enumerate(outdated, 1):
            self._check_abort()
            reason = "missing" if missing else "outdated"
            self._emit(f"Compiling {module} ({i}/{len(outdated)}, {reason})...")
            compiler.compile_py_to_pyd(py_path)
            dest = self._deposit_compiled_pyd(py_path, module, rel_dir)
            self._emit(f"Updated {dest}")

    LOCAL_ONLY_EXACT = frozenset({
        'Admin.db', 'db.enc', 'chunk_inventory.enc', 'admin_trusted_peer.json',
        'projector_secret.txt', 'turn_credentials.json', 'tls_pins.json',
        'ai_config.enc', 'config.enc', 'capability_passport.enc',
        'cognitive_jobs.json', 'cognitive_mesh_features.json',
        'mesh.secret', 'chunk_stream.secret', 'encdec_local.secret',
        'webrtc_room.secret', 'peer_crypto_owner.json',
        'subprocess_pids.json', 'hive_sessions.db', 'startup_debug.log',
        'coverage.json', 'first_run_journey_v1.json',
        'goals_progress.json', 'goals_progress.enc',
        'hive_invitation_bridge.json', 'hive_toast_queue.json',
        'reputation.json', 'app_updates.json',
    })
    LOCAL_ONLY_PREFIXES = ('peer_', 'cm_', 'collabora_', 'bill_')
    LOCAL_ONLY_SUBSTRINGS = (
        '_pids', '_history', '_status', '_metrics', '_sessions',
        '_credits', '_notifications', '_secrets', '_ledger',
    )
    LOCAL_ONLY_SUFFIXES = ('.secret', '-wal', '-shm')
    # Home sites shipped to peers (Welcome mirrors NWN when source Welcome is gone).
    PACKAGE_HOME_SITES = ('NeuronWorldNetwork', 'Welcome')
    PACKAGE_HOME_ASSET_DIRS = ('_lang',)
    PACKAGE_HOME_LANG_SKIP = frozenset({'pending_i18n', 'mt_cache.json'})
    USER_SKIP_DIRS = {
        'Diary', 'Searches', 'Chunks', 'Attachments', 'Chat',
        'DisasterRecovery', 'CollaboraWork', 'EditTemp', 'RoomAddonWork',
        'Archives', 'Communities', 'Professional', '__pycache__',
        # Media runtime / personal content (Images product assets still ship)
        'Audio', 'Video',
    }
    USER_IMAGE_SKIP_DIRS = {
        'Users',  # personal avatars
        'Communities', 'GoodsServices', 'Groups',  # local community content
    }
    AI_SKIP_DIRS = {
        'models', 'ComfyUI', 'Ollama', 'knowledge_base', '__pycache__',
    }
    APPS_SKIP_DIRS = {
        '__pycache__', 'build', 'build_temp_cython', 'works', 'logs', 'ipc',
        'tmp', 'incoming', 'outgoing', '.git',
    }
    APPS_KEEP_PY_NAMES = frozenset({'__init__.py'})
    SUPPORT_ASSET_REL_DIRS = (
        os.path.join('Support', 'Web'),
        os.path.join('Support', 'nltk_data'),
    )
    TEXT_SCAN_EXTENSIONS = frozenset({
        '.json', '.txt', '.html', '.htm', '.js', '.css', '.sql', '.ui', '.xml',
        '.md', '.csv', '.log', '.py', '.ini', '.cfg', '.yml', '.yaml', '.toml',
        '.bat', '.ps1', '.env', '.pem', '.conf', '.properties',
    })
    DISTRIBUTION_SYNC_ROOTS = (
        ('User', 'user'),
        (os.path.join('System', 'json'), 'json'),
        (os.path.join('System', 'AI'), 'ai'),
        (os.path.join('System', 'Apps'), 'general'),
        (os.path.join('System', 'Releases'), 'general'),
        (os.path.join('System', 'THIRD_PARTY_LICENSES'), 'general'),
        ('Apps', 'apps'),
    )
    # Force-mirrored User asset trees (copy all + purge removals in Neuron_PROD).
    # Docs is handled by _sync_documentation_and_translations.
    USER_FORCE_MIRROR_REL = (
        os.path.join('User', 'ui'),
        os.path.join('User', 'Media', 'Images'),
    )
    PRUNE_REL_PATHS = (
        'json', 'rar_files', 'search_index', 'uploads',
        os.path.join('System', 'python310.dll'),
        os.path.join('System', '__init__.c'),
        os.path.join('System', 'libtorrent.exp'),
        os.path.join('System', 'libtorrent.lib'),
        os.path.join('System', 'Old'),
        os.path.join('System', 'Bak'),
        os.path.join('System', '.neuron_progress'),
        os.path.join('System', 'neuron', 'php', 'php_error.log'),
        os.path.join('System', 'neuron', 'php', 'php5embed.lib'),
        os.path.join('System', 'neuron', 'php', 'pear'),
        os.path.join('System', 'neuron', 'php', 'man'),
        os.path.join('System', 'neuron', 'php', 'tmp'),
        os.path.join('System', 'neuron', 'php', 'www'),
        os.path.join('System', 'neuron', 'apache', 'manual'),
        os.path.join('System', 'neuron', 'apache', 'include'),
        os.path.join('System', 'neuron', 'firewall_poll_called.txt'),
        os.path.join('System', 'neuron', 'htdocs', 'works', 'temp'),
        os.path.join('User', 'Diary'),
        os.path.join('User', 'Searches'),
        os.path.join('User', 'Communities'),
        os.path.join('User', 'Professional'),
        'templates',  # web pages ship inside frontend.vault, not loose
        'static',
    )

    def _old_unused_root(self):
        return os.path.join(init.PATH_DESKTOP, 'OLD', 'Neuron_PROD_unused')

    def _is_private_key_filename(self, name):
        lower = name.lower()
        if not lower.endswith('.pem'):
            return False
        if 'pubkey' in lower or 'public' in lower:
            return False
        if lower.endswith('_key.pem') or 'privkey' in lower or 'signing_key' in lower:
            return True
        if 'private' in lower:
            return True
        return False

    def _is_local_only_file(self, rel_path, name):
        if name in self.LOCAL_ONLY_EXACT:
            return True
        lower = name.lower()
        if lower.endswith('.done'):
            return True
        for suffix in self.LOCAL_ONLY_SUFFIXES:
            if lower.endswith(suffix):
                return True
        for prefix in self.LOCAL_ONLY_PREFIXES:
            if lower.startswith(prefix):
                return True
        for part in self.LOCAL_ONLY_SUBSTRINGS:
            if part in lower:
                return True
        if self._is_private_key_filename(name):
            return True
        if lower.endswith('.secret'):
            return True
        if self._is_hash_db_name(name):
            return True
        return False

    def _should_ship_json_file(self, rel_path, name):
        return not self._is_local_only_file(rel_path, name)

    def _should_ship_user_file(self, rel_path, name):
        """Ship Docs/translations/media UI assets; skip i18n tooling and personal media."""
        lower = name.lower()
        if lower.endswith('.tmp') or lower.endswith('.bak'):
            return False
        parts = rel_path.replace('\\', '/').lower().split('/')
        # Local image/thumbnail caches
        for part in parts:
            if part.startswith('_chkcache') or part.startswith('_pycache') or part.startswith('__pycache'):
                return False
        # Personal / local-only image buckets under Media/Images
        if 'images' in parts:
            for skip in self.USER_IMAGE_SKIP_DIRS:
                if skip.lower() in parts:
                    return False
        if 'i18n' in parts:
            if lower in ('pending.json', 'coverage.json', 'xx.json'):
                return False
        return not self._is_local_only_file(rel_path, name)

    def _should_ship_ai_file(self, rel_path, name):
        lower = name.lower()
        if lower.endswith('.7z') or lower == '7zr.exe':
            return False
        if lower.startswith('url_') and lower.endswith('.txt'):
            return False
        if lower.startswith('_build'):
            return False
        return not self._is_local_only_file(rel_path, name)

    def _should_ship_apps_file(self, rel_path, name):
        """Ship app assets and __init__.py only; .py sources are compiled to .pyd."""
        lower = name.lower()
        parts = rel_path.replace('\\', '/').lower().split('/')
        for skip in self.APPS_SKIP_DIRS:
            if skip.lower() in parts:
                return False
        if name in self.APPS_KEEP_PY_NAMES:
            return not self._is_local_only_file(rel_path, name)
        if lower.endswith('.py') or lower.endswith('.pyc') or lower.endswith('.pyd'):
            return False
        if lower.endswith('.c') or lower.endswith('.obj') or lower.endswith('.exp'):
            return False
        if lower.endswith('.lib') or lower.endswith('.old'):
            return False
        if lower.startswith('test_'):
            return False
        if lower.endswith('.db') or lower.endswith('.log'):
            return False
        return not self._is_local_only_file(rel_path, name)

    def _distribution_ship_filter(self, context):
        if context == 'json':
            return self._should_ship_json_file
        if context == 'ai':
            return self._should_ship_ai_file
        if context == 'apps':
            return self._should_ship_apps_file
        if context == 'user':
            return self._should_ship_user_file
        return lambda rel_path, name: not self._is_local_only_file(rel_path, name)

    def _personal_content_markers(self):
        """Values that must never appear in shipped support-file contents."""
        if getattr(self, '_cached_personal_markers', None) is not None:
            return self._cached_personal_markers
        markers = []
        admin_id = str(getattr(init, 'ADMIN_TRUSTED_PEER_ID', '') or '').strip()
        me = getattr(init, 'ME', None)
        if me:
            for key in ('ip', 'ip_local', 'id', 'node_name', 'name', 'peer_id'):
                try:
                    val = me[key] if key in me else None
                except Exception:
                    val = None
                if val is None:
                    continue
                text = str(val).strip()
                if len(text) < 4:
                    continue
                if key in ('id', 'peer_id') and admin_id and text == admin_id:
                    continue
                if key in ('ip', 'ip_local') and len(text) < 7:
                    continue
                markers.append(text)
        home = os.path.expanduser('~')
        if home:
            markers.append(home)
            markers.append(home.replace('\\', '/'))
            markers.append(os.path.normcase(home))
        desktop = getattr(init, 'PATH_DESKTOP', '') or ''
        if desktop:
            markers.append(desktop.rstrip('\\/'))
            markers.append(desktop.replace('\\', '/').rstrip('/'))
        markers.extend((
            'BEGIN PRIVATE KEY',
            'BEGIN RSA PRIVATE KEY',
            'BEGIN EC PRIVATE KEY',
            'BEGIN OPENSSH PRIVATE KEY',
        ))
        seen = set()
        cleaned = []
        for item in markers:
            if not item or len(item) < 4:
                continue
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(item)
        self._cached_personal_markers = cleaned
        return cleaned

    def _file_contains_personal_data(self, path):
        """Return matching marker if text file content includes personal/secret data."""
        ext = os.path.splitext(path)[1].lower()
        if ext not in self.TEXT_SCAN_EXTENSIONS:
            return None
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        if size <= 0 or size > 2 * 1024 * 1024:
            return None
        try:
            with open(path, 'rb') as handle:
                raw = handle.read()
        except OSError:
            return None
        if b'\x00' in raw[:4096]:
            return None
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            try:
                text = raw.decode('latin-1')
            except Exception:
                return None
        lower = text.lower()
        for marker in self._personal_content_markers():
            if marker.lower() in lower:
                return marker
        return None

    def _assert_safe_to_ship(self, src_path, rel_file, *, hard_fail=False):
        hit = self._file_contains_personal_data(src_path)
        if not hit:
            return True
        message = (
            f"Skipping {rel_file}: content contains personal/secret data "
            f"(matched: {hit[:80]})"
        )
        if hard_fail:
            raise RuntimeError(message.replace('Skipping', 'Refusing to ship', 1))
        self._emit(message)
        return False

    def _move_to_old(self, src_path):
        if not os.path.exists(src_path):
            return False
        install = self._install_root()
        rel = os.path.relpath(src_path, install)
        dest = os.path.join(self._old_unused_root(), rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            dest = dest + '_' + stamp
        shutil.move(src_path, dest)
        self._emit(f"Moved unused {rel} to OLD")
        return True

    def _copy_newer_tree(self, src_root, dst_root, skip_names=None, skip_dirs=None, ship_filter=None, force=False):
        if not os.path.isdir(src_root):
            self._emit(f"Source missing, skip: {src_root}")
            return
        skip_names = skip_names or set()
        skip_dirs = skip_dirs or set()
        os.makedirs(dst_root, exist_ok=True)
        for root, dirs, files in os.walk(src_root):
            self._check_abort()
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            rel = os.path.relpath(root, src_root)
            dest_dir = dst_root if rel == '.' else os.path.join(dst_root, rel)
            os.makedirs(dest_dir, exist_ok=True)
            for name in files:
                if name in skip_names:
                    continue
                rel_file = name if rel == '.' else os.path.join(rel, name)
                if ship_filter and not ship_filter(rel_file, name):
                    continue
                src = os.path.join(root, name)
                dst = os.path.join(dest_dir, name)
                if not self._assert_safe_to_ship(src, rel_file):
                    if os.path.isfile(dst):
                        self._move_to_old(dst)
                    continue
                if force or (not os.path.isfile(dst)) or (os.path.getmtime(src) > os.path.getmtime(dst)):
                    shutil.copy2(src, dst)
                    self._emit(f"Copied {rel_file}")

    def _mirror_purge_tree(
        self, src_root, dst_root, skip_dirs=None, ship_filter=None, label=None,
        preserve_ext=None,
    ):
        """Remove Neuron_PROD files/dirs that are gone or no longer shippable from source."""
        if not os.path.isdir(dst_root):
            return
        label = label or os.path.basename(dst_root)
        skip_dirs = skip_dirs or set()
        skip_dirs_lower = {d.lower() for d in skip_dirs}
        preserve_ext = {e.lower() for e in (preserve_ext or ())}

        # Collect every shippable relative file path from source.
        src_files = set()
        if os.path.isdir(src_root):
            for root, dirs, files in os.walk(src_root):
                self._check_abort()
                dirs[:] = [d for d in dirs if d not in skip_dirs]
                rel = os.path.relpath(root, src_root)
                for name in files:
                    rel_file = name if rel == '.' else os.path.join(rel, name)
                    if ship_filter and not ship_filter(rel_file, name):
                        continue
                    src_files.add(os.path.normcase(rel_file))

        for root, dirs, files in os.walk(dst_root, topdown=False):
            self._check_abort()
            rel = os.path.relpath(root, dst_root)
            rel_parts = [] if rel == '.' else rel.replace('\\', '/').split('/')
            # Drop skip-dir trees entirely from the package.
            if any(part.lower() in skip_dirs_lower for part in rel_parts):
                self._move_to_old(root)
                continue
            for name in files:
                rel_file = name if rel == '.' else os.path.join(rel, name)
                lower = name.lower()
                ext = os.path.splitext(lower)[1]
                # Keep compiled modules only while the matching .py still exists.
                if ext in preserve_ext:
                    if lower.startswith('test_') and ext == '.pyd':
                        self._move_to_old(os.path.join(root, name))
                        continue
                    py_rel = os.path.splitext(rel_file)[0] + '.py'
                    if os.path.isfile(os.path.join(src_root, py_rel)):
                        continue
                    self._move_to_old(os.path.join(root, name))
                    continue
                if ship_filter and not ship_filter(rel_file, name):
                    self._move_to_old(os.path.join(root, name))
                    continue
                if os.path.normcase(rel_file) not in src_files:
                    self._move_to_old(os.path.join(root, name))
            for name in dirs:
                dir_path = os.path.join(root, name)
                try:
                    if os.path.isdir(dir_path) and not os.listdir(dir_path):
                        os.rmdir(dir_path)
                except OSError:
                    pass
        self._emit(f"Mirror-purged stale files under {label}")

    def _write_distributable_conductor_config(self, json_dst):
        """Ship a clean Conductor front-door config (no local completion state)."""
        os.makedirs(json_dst, exist_ok=True)
        path = os.path.join(json_dst, 'conductor.json')
        payload = {
            'enabled': True,
            'completed_once': False,
            'last_soft_shown_on': '',
            'startup_mode': 'auto',
        }
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2)
        self._emit("Wrote distributable System\\json\\conductor.json")

    def _sync_conductor_assets(self):
        """Ensure Conductor background (and logo fallbacks) are in the package."""
        rel = os.path.join('User', 'Media', 'Images', 'Support')
        src_dir = os.path.join(self._main_root(), rel)
        dst_dir = os.path.join(self._install_root(), rel)
        os.makedirs(dst_dir, exist_ok=True)
        for name in ('ConductorBackground.png', 'Neuron.png', 'Welcome.webp'):
            self._check_abort()
            src = os.path.join(src_dir, name)
            if not os.path.isfile(src):
                self._emit(f"WARNING: missing Conductor asset {src}")
                continue
            dst = os.path.join(dst_dir, name)
            shutil.copy2(src, dst)
            self._emit(f"Copied Conductor asset {name}")

    def _sync_documentation_and_translations(self):
        """Force-refresh User/Docs (help, legal PDFs, i18n packs) into the package."""
        src = os.path.join(self._main_root(), 'User', 'Docs')
        dst = os.path.join(self._install_root(), 'User', 'Docs')
        if not os.path.isdir(src):
            self._emit(f"WARNING: missing documentation tree {src}")
            return
        self._emit("Force-syncing User\\Docs (documentation + translations)...")
        self._copy_newer_tree(
            src,
            dst,
            skip_dirs={'__pycache__'},
            ship_filter=self._should_ship_user_file,
            force=True,
        )
        self._mirror_purge_tree(
            src,
            dst,
            skip_dirs={'__pycache__'},
            ship_filter=self._should_ship_user_file,
            label='User\\Docs',
        )
        # Drop stale tooling leftovers already sitting in the package Docs tree.
        i18n_dst = os.path.join(dst, 'i18n')
        if os.path.isdir(i18n_dst):
            for root, _dirs, files in os.walk(i18n_dst):
                self._check_abort()
                for name in files:
                    lower = name.lower()
                    if lower in ('pending.json', 'coverage.json', 'xx.json') or lower.endswith('.tmp'):
                        self._move_to_old(os.path.join(root, name))

    def _sync_user_force_mirrors(self):
        """Force-copy + purge User ui / Images / Docs so Neuron_PROD matches source assets."""
        main = self._main_root()
        install = self._install_root()
        image_skip = set(self.USER_IMAGE_SKIP_DIRS) | {
            d for d in self.USER_SKIP_DIRS if d in ('Audio', 'Video', '__pycache__')
        }
        for rel in self.USER_FORCE_MIRROR_REL:
            self._check_abort()
            src = os.path.join(main, rel)
            dst = os.path.join(install, rel)
            if not os.path.isdir(src):
                self._emit(f"WARNING: missing force-mirror source {src}")
                continue
            skip_dirs = {'__pycache__'}
            if rel.endswith(os.path.join('Media', 'Images')) or rel.replace('/', '\\').endswith('Media\\Images'):
                skip_dirs |= image_skip
            self._emit(f"Force-mirroring {rel}...")
            self._copy_newer_tree(
                src,
                dst,
                skip_dirs=skip_dirs,
                ship_filter=self._should_ship_user_file,
                force=True,
            )
            self._mirror_purge_tree(
                src,
                dst,
                skip_dirs=skip_dirs,
                ship_filter=self._should_ship_user_file,
                label=rel,
            )

    def _purge_apps_tree(self):
        """Remove stale app folders and runtime/private app content from package."""
        main_apps = os.path.join(self._main_root(), 'Apps')
        install_apps = os.path.join(self._install_root(), 'Apps')
        if not os.path.isdir(install_apps):
            return
        src_names = set()
        if os.path.isdir(main_apps):
            src_names = {
                name for name in os.listdir(main_apps)
                if os.path.isdir(os.path.join(main_apps, name))
            }
        for name in list(os.listdir(install_apps)):
            self._check_abort()
            path = os.path.join(install_apps, name)
            if not os.path.isdir(path):
                continue
            if name not in src_names:
                self._move_to_old(path)
                continue
            for skip_dir in self.APPS_SKIP_DIRS:
                skip_path = os.path.join(path, skip_dir)
                if os.path.isdir(skip_path):
                    self._move_to_old(skip_path)
            for root, dirs, files in os.walk(path, topdown=False):
                self._check_abort()
                rel = os.path.relpath(root, path)
                for fname in files:
                    rel_file = fname if rel == '.' else os.path.join(rel, fname)
                    lower = fname.lower()
                    # Drop test binaries and non-shippable leftovers; keep real .pyd modules.
                    if lower.startswith('test_') and lower.endswith('.pyd'):
                        self._move_to_old(os.path.join(root, fname))
                        continue
                    if self._should_ship_apps_file(rel_file, fname):
                        hit = self._file_contains_personal_data(os.path.join(root, fname))
                        if hit:
                            self._emit(
                                f"Removing unsafe app file {name}\\{rel_file} "
                                f"(matched: {hit[:60]})"
                            )
                            self._move_to_old(os.path.join(root, fname))
                        continue
                    if fname.endswith('.pyd'):
                        continue
                    if fname in self.APPS_KEEP_PY_NAMES:
                        continue
                    self._move_to_old(os.path.join(root, fname))

    def _sync_support_asset_dirs(self):
        main = self._main_root()
        install = self._install_root()
        for rel in self.SUPPORT_ASSET_REL_DIRS:
            self._check_abort()
            src = os.path.join(main, rel)
            dst = os.path.join(install, rel)
            if not os.path.isdir(src):
                self._emit(f"WARNING: missing support asset tree {src}")
                continue
            self._emit(f"Scanning support assets {rel}...")
            self._copy_newer_tree(
                src,
                dst,
                skip_dirs={'__pycache__'},
                ship_filter=lambda rel_path, name: not self._is_local_only_file(rel_path, name),
                force=True,
            )
            self._mirror_purge_tree(
                src,
                dst,
                skip_dirs={'__pycache__'},
                ship_filter=lambda rel_path, name: not self._is_local_only_file(rel_path, name),
                label=rel,
            )

    def _purge_non_distributable_files(self):
        install = self._install_root()
        data_dir = os.path.join(install, 'System', 'neuron', 'data')
        if os.path.isdir(data_dir):
            for name in os.listdir(data_dir):
                if name in ('Admin.db', 'Admin.db-wal', 'Admin.db-shm') or self._is_local_only_file('', name):
                    path = os.path.join(data_dir, name)
                    if os.path.isfile(path):
                        os.remove(path)
                        self._emit(f"Removed local-only {name} from package data")

        user_dst = os.path.join(install, 'User')
        if os.path.isdir(user_dst):
            for skip_dir in self.USER_SKIP_DIRS:
                path = os.path.join(user_dst, skip_dir)
                if os.path.isdir(path):
                    self._move_to_old(path)

        json_dst = os.path.join(install, 'System', 'json')
        if os.path.isdir(json_dst):
            self._purge_package_json_forbidden(json_dst)
            for name in os.listdir(json_dst):
                if name == 'costs.enc':
                    continue
                path = os.path.join(json_dst, name)
                if not os.path.isfile(path):
                    continue
                if self._should_ship_json_file('', name):
                    hit = self._file_contains_personal_data(path)
                    if hit:
                        self._emit(
                            f"Removing unsafe json {name} (matched: {hit[:60]})"
                        )
                        self._move_to_old(path)
                    continue
                self._move_to_old(path)

        ai_dst = os.path.join(install, 'System', 'AI')
        if os.path.isdir(ai_dst):
            for skip_dir in self.AI_SKIP_DIRS:
                path = os.path.join(ai_dst, skip_dir)
                if os.path.isdir(path):
                    self._move_to_old(path)
            for root, dirs, files in os.walk(ai_dst, topdown=False):
                self._check_abort()
                rel = os.path.relpath(root, ai_dst)
                rel_prefix = '' if rel == '.' else rel
                for name in files:
                    rel_file = name if rel_prefix == '' else os.path.join(rel_prefix, name)
                    if self._should_ship_ai_file(rel_file, name):
                        continue
                    self._move_to_old(os.path.join(root, name))
                for name in dirs:
                    dir_path = os.path.join(root, name)
                    try:
                        if not os.listdir(dir_path):
                            os.rmdir(dir_path)
                    except OSError:
                        pass

        self._purge_apps_tree()
    def _step_sync_user_and_json(self):
        self._emit("Step 4/8: Scanning and syncing distributable content...", 4)
        main = self._main_root()
        install = self._install_root()
        for rel_root, context in self.DISTRIBUTION_SYNC_ROOTS:
            self._check_abort()
            src = os.path.join(main, rel_root)
            dst = os.path.join(install, rel_root)
            if not os.path.isdir(src):
                continue
            skip_dirs = set()
            if rel_root == 'User':
                skip_dirs = self.USER_SKIP_DIRS
            elif rel_root == os.path.join('System', 'AI'):
                skip_dirs = self.AI_SKIP_DIRS
            elif rel_root == 'Apps':
                skip_dirs = self.APPS_SKIP_DIRS
            ship_filter = self._distribution_ship_filter(context)
            force = rel_root in ('Apps', os.path.join('System', 'THIRD_PARTY_LICENSES'))
            self._emit(f"Scanning {rel_root} for new or modified distributable files...")
            self._copy_newer_tree(
                src, dst, skip_dirs=skip_dirs, ship_filter=ship_filter, force=force,
            )
            if rel_root == 'Apps':
                self._mirror_purge_tree(
                    src, dst, skip_dirs=skip_dirs, ship_filter=ship_filter,
                    label='Apps', preserve_ext={'.pyd'},
                )
            elif rel_root == os.path.join('System', 'THIRD_PARTY_LICENSES'):
                self._mirror_purge_tree(
                    src, dst, skip_dirs=skip_dirs, ship_filter=ship_filter,
                    label='System\\THIRD_PARTY_LICENSES',
                )

        self._sync_support_asset_dirs()
        self._sync_documentation_and_translations()
        self._sync_user_force_mirrors()
        self._sync_conductor_assets()

        json_src = os.path.join(main, 'System', 'json')
        json_dst = os.path.join(install, 'System', 'json')
        for key_name in ('admin_pubkey.pem', 'release_pubkey.pem'):
            src = os.path.join(json_src, key_name)
            if os.path.isfile(src):
                if not self._assert_safe_to_ship(src, os.path.join('System', 'json', key_name)):
                    raise RuntimeError(f"Distributable key failed content check: {key_name}")
                os.makedirs(json_dst, exist_ok=True)
                shutil.copy2(src, os.path.join(json_dst, key_name))
                self._emit(f"Copied distributable key {key_name} to System\\json")
            else:
                self._emit(f"WARNING: missing distributable key {src}")
        # Reference catalogs that new installs must have.
        for ref_name in ('goals.json', 'help_tree.json', 'Languages.txt'):
            src = os.path.join(json_src, ref_name)
            if os.path.isfile(src):
                os.makedirs(json_dst, exist_ok=True)
                shutil.copy2(src, os.path.join(json_dst, ref_name))
                self._emit(f"Copied reference {ref_name}")
            else:
                self._emit(f"WARNING: missing reference {src}")
        self._write_distributable_costs(json_src, json_dst)
        self._write_distributable_conductor_config(json_dst)
        self._purge_non_distributable_files()
        # After purge so personal-data scrub cannot remove the shipped pricing file.
        self._sync_regional_pricing()

    def _sync_regional_pricing(self):
        """Ship RegionalPricing.json into Neuron_PROD htdocs (+ json fallback)."""
        main = self._main_root()
        install = self._install_root()
        src = os.path.join(main, 'System', 'neuron', 'htdocs', 'RegionalPricing.json')
        if not os.path.isfile(src):
            # Allow regenerating into System\\json first, then promote to htdocs.
            alt = os.path.join(main, 'System', 'json', 'RegionalPricing.json')
            if os.path.isfile(alt):
                src = alt
            else:
                self._emit("WARNING: missing RegionalPricing.json (purchase dialog will fail on peers)")
                return
        htdocs_dst_dir = os.path.join(install, 'System', 'neuron', 'htdocs')
        json_dst_dir = os.path.join(install, 'System', 'json')
        os.makedirs(htdocs_dst_dir, exist_ok=True)
        os.makedirs(json_dst_dir, exist_ok=True)
        htdocs_dst = os.path.join(htdocs_dst_dir, 'RegionalPricing.json')
        json_dst = os.path.join(json_dst_dir, 'RegionalPricing.json')
        shutil.copy2(src, htdocs_dst)
        shutil.copy2(src, json_dst)
        # Keep source htdocs + json in sync so both package paths stay valid.
        src_htdocs = os.path.join(main, 'System', 'neuron', 'htdocs', 'RegionalPricing.json')
        src_json = os.path.join(main, 'System', 'json', 'RegionalPricing.json')
        if os.path.abspath(src) != os.path.abspath(src_htdocs):
            os.makedirs(os.path.dirname(src_htdocs), exist_ok=True)
            shutil.copy2(src, src_htdocs)
        if os.path.abspath(src) != os.path.abspath(src_json):
            os.makedirs(os.path.dirname(src_json), exist_ok=True)
            shutil.copy2(src, src_json)
        self._emit("Copied RegionalPricing.json to System\\neuron\\htdocs and System\\json")

    def _write_distributable_costs(self, json_src, json_dst):
        src = os.path.join(json_src, 'costs.enc')
        dst = os.path.join(json_dst, 'costs.enc')
        if not os.path.isfile(src):
            raise FileNotFoundError(f"Missing {src}")
        from Support.EncDec import decrypt, encrypt_v2
        with open(src, 'r', encoding='utf-8') as handle:
            plaintext = decrypt(handle.read())
        os.makedirs(json_dst, exist_ok=True)
        with open(dst, 'w', encoding='utf-8') as handle:
            handle.write(encrypt_v2(plaintext))
        self._emit("Wrote distributable System\\json\\costs.enc")

    def _purge_package_json_forbidden(self, json_dst):
        """Remove package-forbidden files from Neuron_PROD System\\json."""
        if not os.path.isdir(json_dst):
            return
        for name in list(os.listdir(json_dst)):
            self._check_abort()
            lower = name.lower()
            if lower != 'db.enc' and not lower.endswith('.done'):
                continue
            path = os.path.join(json_dst, name)
            if os.path.isfile(path):
                self._move_to_old(path)

    def _remove_install_path_group(self, group, main, install):
        for path in group:
            self._check_abort()
            mapped = os.path.normpath(str(path).replace(main, install))
            if os.path.normcase(mapped) != os.path.normcase(install) and \
               not os.path.normcase(mapped).startswith(os.path.normcase(install) + os.sep):
                continue
            if os.path.isfile(mapped) or os.path.isdir(mapped):
                self._move_to_old(mapped)

    def _step_remove_path_list_files(self):
        self._emit("Step 5/8: Removing PATH lists and unused package files...", 5)
        main = self._main_root()
        install = self._install_root()
        self._remove_install_path_group(init.PATH_NEURONS, main, install)
        self._remove_install_path_group(init.PATH_BILLS, main, install)
        self._remove_install_path_group(init.PATH_CHUNK_INVENTORY, main, install)
        json_dst = os.path.join(install, 'System', 'json')
        self._purge_package_json_forbidden(json_dst)
        self._delete_hash_databases(os.path.join(install, "System", "neuron", "data"))
        self._purge_non_distributable_files()
        for rel in self.PRUNE_REL_PATHS:
            self._check_abort()
            self._move_to_old(os.path.join(install, rel))
        logs_dir = os.path.join(install, "System", "logs")
        os.makedirs(logs_dir, exist_ok=True)
        for name in os.listdir(logs_dir):
            self._check_abort()
            self._move_to_old(os.path.join(logs_dir, name))
        os.makedirs(logs_dir, exist_ok=True)

    def _step_sync_site_packages(self):
        self._emit("Step 6/8: Syncing Lib/site-packages...", 6)
        src_root = os.path.join(self._main_root(), ".venv1", "Lib", "site-packages")
        dst_root = os.path.join(self._install_root(), "Lib", "site-packages")
        if not os.path.isdir(src_root):
            raise FileNotFoundError(f"Missing source site-packages: {src_root}")
        os.makedirs(dst_root, exist_ok=True)

        src_files = set()
        for root, _dirs, files in os.walk(src_root):
            self._check_abort()
            rel_dir = os.path.relpath(root, src_root)
            for name in files:
                rel = name if rel_dir == '.' else os.path.join(rel_dir, name)
                src_files.add(rel)
                src = os.path.join(root, name)
                dst = os.path.join(dst_root, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if (not os.path.isfile(dst)) or os.path.getsize(src) != os.path.getsize(dst) or \
                   os.path.getmtime(src) > os.path.getmtime(dst):
                    shutil.copy2(src, dst)

        for root, dirs, files in os.walk(dst_root, topdown=False):
            self._check_abort()
            rel_dir = os.path.relpath(root, dst_root)
            for name in files:
                rel = name if rel_dir == '.' else os.path.join(rel_dir, name)
                if rel not in src_files:
                    try:
                        os.remove(os.path.join(root, name))
                    except OSError:
                        pass
            for name in dirs:
                dir_path = os.path.join(root, name)
                try:
                    if not os.listdir(dir_path):
                        os.rmdir(dir_path)
                except OSError:
                    pass
        self._emit("site-packages sync complete")

    def _clean_frontend_vault_build(self, root):
        """Remove stale Cython/MSVC outputs that cause intermittent cl/link failures."""
        build_dir = os.path.join(root, "build")
        if os.path.isdir(build_dir):
            shutil.rmtree(build_dir, ignore_errors=True)
        support = os.path.join(root, "Support")
        for name in ("FrontendVault.c", "FrontendVault.cpp"):
            path = os.path.join(support, name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _run_frontend_vault_cmd(self, args, root, log_path):
        env = os.environ.copy()
        env["PYTHON_EXE"] = sys.executable
        result = subprocess.run(
            args,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        chunk = ""
        if result.stdout:
            chunk += result.stdout
        if result.stderr:
            chunk += ("\n" if chunk else "") + result.stderr
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n$ {' '.join(args)}\n")
                f.write(chunk)
                f.write(f"\n[exit {result.returncode}]\n")
        except OSError:
            pass
        return result.returncode, chunk

    def _step_localize_web_templates(self):
        """Apply Translation editor packs into templates/ as data-l clones before vaulting."""
        script = os.path.join(self._main_root(), "ADMIN", "i18n_site_localize.py")
        if not os.path.isfile(script):
            self._emit(f"WARNING: missing {script}; skipping web app localization")
            return
        self._emit("Localizing web app templates from translation packs...")
        log_dir = os.path.join(self._main_root(), "System", "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "I18N_SITE_LOCALIZE.log")
        cmd = [
            sys.executable, script,
            "--scope", "templates",
            "--packs-only",
        ]
        result = subprocess.run(
            cmd,
            cwd=self._main_root(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        try:
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write(combined)
        except OSError:
            pass
        for line in combined.splitlines():
            line = line.strip()
            if line.startswith("[SITE]"):
                self._emit(line)
        if result.returncode != 0:
            raise RuntimeError(
                f"Web app localization failed (see System\\logs\\I18N_SITE_LOCALIZE.log): "
                f"exit {result.returncode}"
            )
        self._emit("Web app template localization complete")

    def _step_build_frontend_vault(self):
        self._emit("Step 7/8: Building FrontendVault...", 7)
        self._step_localize_web_templates()
        self._check_abort()
        root = self._main_root()
        bat = os.path.join(root, "Build_Distribution_Vault.bat")
        if not os.path.isfile(bat):
            raise FileNotFoundError(f"Missing {bat}")

        log_dir = os.path.join(root, "System", "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "FRONTEND_VAULT_BUILD.log")
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"FrontendVault build {datetime.now().isoformat()}\n")
                f.write(f"python={sys.executable}\n")
        except OSError:
            pass

        # Prefer direct Python steps with this interpreter (avoids PATH/pause issues).
        # Fall back to the bat with PYTHON_EXE set if scripts are missing.
        build_py = os.path.join(root, "build_frontend_vault.py")
        setup_py = os.path.join(root, "setup_frontend_vault.py")
        combined = ""
        rc = 1

        if os.path.isfile(build_py) and os.path.isfile(setup_py):
            self._emit("Building encrypted frontend vault...")
            self._clean_frontend_vault_build(root)
            rc, out = self._run_frontend_vault_cmd(
                [sys.executable, build_py, "--root", root], root, log_path
            )
            combined += out
            self._check_abort()
            if rc == 0:
                self._emit("Compiling FrontendVault.pyd...")
                for attempt in (1, 2):
                    self._check_abort()
                    if attempt == 2:
                        self._emit("Retrying FrontendVault compile after clean...")
                        self._clean_frontend_vault_build(root)
                    rc, out = self._run_frontend_vault_cmd(
                        [sys.executable, setup_py, "build_ext", "--inplace", "--force"],
                        root,
                        log_path,
                    )
                    combined += out
                    if rc == 0:
                        break
            for name in ("FrontendVault.pyx", "FrontendVault.c", "FrontendVault.cpp"):
                path = os.path.join(root, "Support", name)
                if os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        else:
            self._clean_frontend_vault_build(root)
            env = os.environ.copy()
            env["PYTHON_EXE"] = sys.executable
            result = subprocess.run(
                ["cmd", "/c", bat],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                input="\n\n",
                env=env,
            )
            combined = (result.stdout or "") + "\n" + (result.stderr or "")
            rc = result.returncode
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(combined)
            except OSError:
                pass

        self._check_abort()
        if rc != 0:
            raise RuntimeError(
                f"Build_Distribution_Vault failed (see System\\logs\\FRONTEND_VAULT_BUILD.log): "
                f"{combined.strip()[-1500:]}"
            )

        support_src = os.path.join(root, "Support")
        support_dst = os.path.join(self._install_root(), "Support")
        system_src = os.path.join(root, "System")
        system_dst = os.path.join(self._install_root(), "System")
        os.makedirs(support_dst, exist_ok=True)
        os.makedirs(system_dst, exist_ok=True)

        # frontend.vault + FrontendVault*.pyd are a matched pair (build embeds the
        # AES key). Always ship BOTH into Neuron_PROD or packaged peers get a dead UI.
        vault_src = os.path.join(system_src, "frontend.vault")
        if not os.path.isfile(vault_src):
            raise FileNotFoundError(f"frontend.vault not found after vault build: {vault_src}")
        vault_dst = os.path.join(system_dst, "frontend.vault")
        shutil.copy2(vault_src, vault_dst)
        self._emit(
            f"Copied System\\frontend.vault to Neuron_PROD\\System "
            f"({os.path.getsize(vault_dst)} bytes)"
        )

        preferred = os.path.join(support_src, "FrontendVault.cp312-win_amd64.pyd")
        pyd_src = preferred if os.path.isfile(preferred) else None
        if not pyd_src:
            matches = sorted(
                f for f in os.listdir(support_src)
                if f.startswith("FrontendVault") and f.endswith(".pyd")
            )
            if matches:
                pyd_src = os.path.join(support_src, matches[0])
        if not pyd_src or not os.path.isfile(pyd_src):
            raise FileNotFoundError("FrontendVault*.pyd not found after vault build")

        # Drop every stale FrontendVault*.pyd so an older keyed binary cannot win import.
        for name in list(os.listdir(support_dst)):
            if name.startswith("FrontendVault") and name.endswith(".pyd"):
                try:
                    os.remove(os.path.join(support_dst, name))
                except OSError:
                    pass

        tagged_name = os.path.basename(pyd_src)
        tagged_dst = os.path.join(support_dst, tagged_name)
        shutil.copy2(pyd_src, tagged_dst)
        # Also deposit untagged name — some extract/import layouts only look for this.
        simple_dst = os.path.join(support_dst, "FrontendVault.pyd")
        shutil.copy2(pyd_src, simple_dst)
        self._emit(
            f"Copied {tagged_name} (+ FrontendVault.pyd) to Neuron_PROD\\Support "
            f"({os.path.getsize(tagged_dst)} bytes)"
        )

        self._require_frontend_vault_pair(self._install_root(), label="Neuron_PROD")

    def _require_frontend_vault_pair(self, root, label="package"):
        """Fail fast if the matched FrontendVault pair is missing from root."""
        vault = os.path.join(root, "System", "frontend.vault")
        support = os.path.join(root, "Support")
        if not os.path.isfile(vault) or os.path.getsize(vault) < 1024:
            raise FileNotFoundError(
                f"{label} missing System\\frontend.vault (required for packaged UI)"
            )
        pyds = []
        if os.path.isdir(support):
            pyds = [
                n for n in os.listdir(support)
                if n.startswith("FrontendVault") and n.endswith(".pyd")
            ]
        if not pyds:
            raise FileNotFoundError(
                f"{label} missing Support\\FrontendVault*.pyd "
                f"(must ship with frontend.vault from the same build)"
            )
        self._emit(
            f"Verified {label} FrontendVault pair: "
            f"frontend.vault={os.path.getsize(vault)} bytes, pyds={', '.join(sorted(pyds))}"
        )

    def _find_rar_exe(self):
        for env_key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env_key)
            if not base:
                continue
            candidate = os.path.join(base, "WinRAR", "Rar.exe")
            if os.path.isfile(candidate):
                return candidate
        return shutil.which("Rar") or shutil.which("rar")

    def _step_create_neuron_rar(self):
        self._emit("Step 8/8: Creating Neuron.rar...", 8)
        rar_exe = self._find_rar_exe()
        if not rar_exe:
            raise FileNotFoundError("WinRAR Rar.exe not found")
        dest_rar = self._neuron_rar_path()
        if os.path.isfile(dest_rar):
            os.remove(dest_rar)
        install = self._install_root()
        # Last chance: refuse to archive a PROD tree without the vault pair.
        self._require_frontend_vault_pair(install, label="Neuron_PROD before RAR")
        # -ep1 strips the install path; -apNeuron\ stores everything under a single
        # Neuron\ root so desktop extract yields one folder, not loose files.
        cmd = [
            rar_exe, "a", "-r", "-ep1", "-apNeuron\\", "-o+",
            dest_rar, os.path.join(install, "*")
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=install)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"RAR creation failed: {err[-1000:]}")
        if not os.path.isfile(dest_rar):
            raise RuntimeError(f"RAR reported success but file missing: {dest_rar}")
        self._verify_rar_frontend_vault(rar_exe, dest_rar)
        self._emit(f"Created {dest_rar} (contents under Neuron\\)")

    def _verify_rar_frontend_vault(self, rar_exe, dest_rar):
        """Confirm Neuron.rar actually contains frontend.vault + FrontendVault pyd."""
        list_cmd = [rar_exe, "lb", dest_rar]
        listed = subprocess.run(list_cmd, capture_output=True, text=True)
        if listed.returncode != 0:
            err = (listed.stderr or listed.stdout or "").strip()
            raise RuntimeError(f"RAR list failed while verifying FrontendVault: {err[-500:]}")
        names = [
            line.strip().replace('/', '\\')
            for line in (listed.stdout or "").splitlines()
            if line.strip()
        ]
        lower_names = [n.lower() for n in names]
        vault_ok = any(n.endswith("system\\frontend.vault") for n in lower_names)
        pyd_ok = any(
            ("\\support\\frontendvault" in n or n.startswith("neuron\\support\\frontendvault"))
            and n.endswith(".pyd")
            for n in lower_names
        )
        if not vault_ok or not pyd_ok:
            raise RuntimeError(
                "Neuron.rar is missing the FrontendVault pair "
                f"(vault={vault_ok}, pyd={pyd_ok}). Refusing to publish a broken package."
            )
        self._emit("Verified Neuron.rar contains System\\frontend.vault and Support\\FrontendVault*.pyd")


class DeploymentWorker(QThread):
    """Worker thread for module deployment"""
    progress = Signal(str)
    finished = Signal(bool, str, list)

    def __init__(self, modules_data, target_peers):
        super().__init__()
        self.modules_data = modules_data
        self.target_peers = target_peers

        # Use the connection pool instead of direct connection
        try:
            self.ME = query_database(init.pool_main_db, "SELECT * FROM Me", '')[0]
        except (IndexError, AttributeError):
            # Fallback to direct connection if pool is not available
            import sqlite3 as sql
            self.ME = query_database(init.pool_main_db, "SELECT * FROM Me", '')[0]

    def run(self):
        try:
            failed_deployments = []
            total_peers = len(self.target_peers)

            for i, peer in enumerate(self.target_peers):
                self.progress.emit(f"Deploying to {peer['name']} ({i+1}/{total_peers})...")

                try:
                    success = self.send_modules_to_peer(self.modules_data, peer)
                    if not success:
                        failed_deployments.append(peer['name'])
                except Exception as e:
                    self.progress.emit(f"Failed to deploy to {peer['name']}: {str(e)}")
                    failed_deployments.append(peer['name'])

            if not failed_deployments:
                self.finished.emit(True, "All deployments successful", [])
            else:
                self.finished.emit(False, f"Some deployments failed", failed_deployments)

        except Exception as e:
            self.finished.emit(False, f"Deployment error: {str(e)}", [])

    def ip_to_send_packet_to(self, ip, local_ip):
        """
        Determine which IP to send packet to - global or local.
        If I'm pinging a peer that is in my same local network, the principal IP address will be the same.
        In this case, I ping the local IP address.
        """
        if ip == self.ME[3]:
            return local_ip
        else:
            return ip

    def send_modules_to_peer(self, modules_data, peer):
        """Send module data to a specific peer"""
        try:
            # modules_data is now a tuple: (deployment_info, module_info, binary_data_list)
            deployment_info, module_info, binary_data_list = modules_data

            my_id = ''
            try:
                if isinstance(self.ME, dict):
                    my_id = str(self.ME.get('id') or self.ME.get('peer_id') or '')
                elif isinstance(self.ME, (list, tuple)) and len(self.ME) > 0:
                    my_id = str(self.ME[0] or '')
            except Exception:
                my_id = ''
            dst_peer_id = str(peer.get('peer_id') or peer.get('id') or '').strip()
            src_ip = self.ME[3] if not isinstance(self.ME, dict) else self.ME.get('ip')
            src_ip_local = self.ME[4] if not isinstance(self.ME, dict) else self.ME.get('ip_local')

            # Send deployment info through data1, module info through data2, binary data through data3
            # Must pass peer_ids: admin verify pins src_peer_id, reverse park shards by dst_peer_id.
            return bool(send_packet(
                src_ip, src_ip_local, '',
                self.ip_to_send_packet_to(peer['ip'], peer['ip_local']), peer['ip_local'], '',
                init.CMD_SRV_MOD,
                init.PRT_LOW,
                '',
                0,
                0,
                get_timestamp(),
                deployment_info,  # Send deployment info as string list through data1
                module_info,      # Send module info as string list through data2
                binary_data_list, # Send binary data as byte list through data3
                '',
                '',
                [],
                [],
                [],
                'critical',
                False,
                src_peer_id=my_id,
                dst_peer_id=dst_peer_id,
            ))

        except Exception as e:
            print(f"Error sending to {peer['name']}: {e}")
            return False


class ServerDistributionChart(QWidget):
    """Widget to display server type distribution as a bar chart"""
    
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(180)
        self.data = {}
        
    def set_data(self, data_dict):
        """Set the data for the chart
        data_dict should have keys like 'nodes', 'brd_servers', 'db_servers', etc.
        """
        self.data = data_dict
        self.update()
        
    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor, QFont
        
        if not self.data:
            painter = QPainter(self)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No data available")
            return
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        server_types = {
            'nodes': ('Nodes', '#4a90e2'),
            'brd_servers': ('BRD Servers', '#ff6b6b'),
            'db_servers': ('DB Servers', '#51cf66'),
            'app_servers': ('App Servers', '#ffd93d'),
            'sfu_servers': ('SFU Servers', '#a78bfa'),
            'super_db_servers': ('Super DB', '#ff922b'),
            'kickstarters': ('Kickstarters', '#00d9ff'),
            'administrators': ('Administrators', '#ff006e')
        }
        
        total = sum(int(str(self.data.get(key, 0))) for key in server_types.keys())
        
        if total == 0:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No servers online")
            return
        
        margin = 40
        chart_width = self.width() - 2 * margin
        chart_height = self.height() - 2 * margin
        bar_width = chart_width / len(server_types)
        
        painter.setFont(QFont("Arial", 10))
        
        for idx, (key, (label, color)) in enumerate(server_types.items()):
            count = int(str(self.data.get(key, 0)))
            percentage = (count / total * 100) if total > 0 else 0
            
            bar_height = (percentage / 100) * chart_height
            x = margin + idx * bar_width
            y = self.height() - margin - bar_height
            
            painter.fillRect(int(x + 5), int(y), int(bar_width - 10), int(bar_height), QColor(color))
            painter.drawRect(int(x + 5), int(y), int(bar_width - 10), int(bar_height))
            
            painter.drawText(int(x), self.height() - margin + 20, int(bar_width), 30, 
                           Qt.AlignmentFlag.AlignCenter, label)
            
            painter.drawText(int(x), int(y - 20), int(bar_width), 20, 
                           Qt.AlignmentFlag.AlignCenter, f"{percentage:.1f}%")


class PeerSelectionDialog(QDialog):
    """Advanced peer selection dialog with filtering and sorting"""

    HEADERS = ['Select', 'Name', 'IP', 'Local IP', 'City', 'Country', 'Online', 'Reliability', 'Type', 'Comm Type']

    def __init__(self, peers_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Peers")
        self.setModal(True)
        self.resize(1000, 700)
        self.original_peers_data = list(peers_data or [])
        self.filtered_peers = list(self.original_peers_data)
        self.selected_peer_ids = {}
        self.setup_ui()
        self.populate_filter_options()
        self.refresh_table()
        self.connect_signals()
        self.setStyleSheet("""
            QDialog {
                background-color: #252a3a;
                color: #e0e0e0;
            }
            QTableWidget {
                background-color: #2c2c2c;
                alternate-background-color: #3a4366;
                selection-background-color: #4a90e2;
                gridline-color: #3a4366;
                color: #e0e0e0;
            }
            QTableWidget::item {
                padding: 8px;
                border: none;
            }
            QTableWidget::item:selected {
                background-color: #4a90e2;
            }
            QHeaderView::section {
                background-color: #3a4366;
                color: #e0e0e0;
                padding: 8px;
                border: 1px solid #252a3a;
                font-weight: bold;
            }
        """)

    def setup_ui(self):
        layout = QVBoxLayout(self)

        header_layout = QHBoxLayout()
        title_label = QLabel("Peer Selection")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #4a90e2; padding: 10px;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        self.peer_count_label = QLabel("Loading peers...")
        self.peer_count_label.setStyleSheet("color: #e0e0e0; padding: 10px;")
        header_layout.addWidget(self.peer_count_label)
        layout.addLayout(header_layout)

        filters_group = QGroupBox("Filters & Search")
        filters_layout = QGridLayout(filters_group)

        filters_layout.addWidget(QLabel("Search:"), 0, 0)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search by name, IP, city, country...")
        filters_layout.addWidget(self.search_input, 0, 1, 1, 2)

        filters_layout.addWidget(QLabel("City:"), 1, 0)
        self.city_filter = QComboBox()
        self.city_filter.addItem("All Cities")
        filters_layout.addWidget(self.city_filter, 1, 1)

        filters_layout.addWidget(QLabel("Country:"), 1, 2)
        self.country_filter = QComboBox()
        self.country_filter.addItem("All Countries")
        filters_layout.addWidget(self.country_filter, 1, 3)

        filters_layout.addWidget(QLabel("Status:"), 2, 0)
        self.status_filter = QComboBox()
        self.status_filter.addItems(["All", "Online Only", "Offline Only"])
        filters_layout.addWidget(self.status_filter, 2, 1)

        filters_layout.addWidget(QLabel("Type:"), 2, 2)
        self.type_filter = QComboBox()
        self.type_filter.addItem("All Types")
        filters_layout.addWidget(self.type_filter, 2, 3)

        filter_buttons_layout = QHBoxLayout()
        self.btn_apply_filters = QPushButton("Apply Filters")
        self.btn_clear_filters = QPushButton("Clear Filters")
        filter_buttons_layout.addWidget(self.btn_apply_filters)
        filter_buttons_layout.addWidget(self.btn_clear_filters)
        filter_buttons_layout.addStretch()
        filters_layout.addLayout(filter_buttons_layout, 3, 0, 1, 4)
        layout.addWidget(filters_group)

        selection_layout = QHBoxLayout()
        self.btn_select_all = QPushButton("Select All Visible")
        self.btn_select_none = QPushButton("Select None")
        selection_layout.addWidget(self.btn_select_all)
        selection_layout.addWidget(self.btn_select_none)
        selection_layout.addStretch()
        layout.addLayout(selection_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 120)
        self.table.setColumnWidth(4, 100)
        self.table.setColumnWidth(5, 100)
        self.table.setColumnWidth(6, 80)
        self.table.setColumnWidth(7, 100)
        self.table.setColumnWidth(8, 100)
        self.table.setColumnWidth(9, 100)
        layout.addWidget(self.table)

        status_layout = QHBoxLayout()
        self.selected_count_label = QLabel("Selected: 0")
        self.selected_count_label.setStyleSheet("color: #4a90e2; font-weight: bold;")
        status_layout.addWidget(self.selected_count_label)
        status_layout.addStretch()
        self.load_status_label = QLabel("Ready")
        status_layout.addWidget(self.load_status_label)
        layout.addLayout(status_layout)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.btn_ok = QPushButton("OK")
        self.btn_ok.setDefault(True)
        self.btn_cancel = QPushButton("Cancel")
        button_layout.addWidget(self.btn_ok)
        button_layout.addWidget(self.btn_cancel)
        layout.addLayout(button_layout)

    def connect_signals(self):
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_select_all.clicked.connect(self.select_all_visible)
        self.btn_select_none.clicked.connect(self.select_none)
        self.btn_apply_filters.clicked.connect(self.apply_filters)
        self.btn_clear_filters.clicked.connect(self.clear_filters)
        self.search_input.textChanged.connect(self.apply_filters)
        self.table.itemChanged.connect(self.on_item_changed)

    def _peer_field(self, peer, key, default=""):
        value = peer.get(key, default)
        return default if value is None else value

    def _reliability_text(self, peer):
        value = peer.get("reliability", 0)
        try:
            return f"{float(value):.1f}%"
        except (TypeError, ValueError):
            return "0.0%"

    def populate_filter_options(self):
        cities = sorted({str(p["city"]) for p in self.original_peers_data if p.get("city")})
        countries = sorted({str(p["country"]) for p in self.original_peers_data if p.get("country")})
        types = sorted({
            str(p["type"]) for p in self.original_peers_data
            if p.get("type") is not None and p.get("type") != ""
        })

        self.city_filter.blockSignals(True)
        self.country_filter.blockSignals(True)
        self.type_filter.blockSignals(True)

        self.city_filter.clear()
        self.city_filter.addItem("All Cities")
        self.city_filter.addItems(cities)

        self.country_filter.clear()
        self.country_filter.addItem("All Countries")
        self.country_filter.addItems(countries)

        self.type_filter.clear()
        self.type_filter.addItem("All Types")
        self.type_filter.addItems(types)

        self.city_filter.blockSignals(False)
        self.country_filter.blockSignals(False)
        self.type_filter.blockSignals(False)

    def apply_filters(self):
        filtered = list(self.original_peers_data)
        search_text = self.search_input.text().strip().lower()
        if search_text:
            filtered = [
                peer for peer in filtered
                if search_text in str(peer.get("name", "")).lower()
                or search_text in str(peer.get("ip", "")).lower()
                or search_text in str(peer.get("city", "")).lower()
                or search_text in str(peer.get("country", "")).lower()
            ]

        city = self.city_filter.currentText()
        if city != "All Cities":
            filtered = [peer for peer in filtered if str(peer.get("city", "")) == city]

        country = self.country_filter.currentText()
        if country != "All Countries":
            filtered = [peer for peer in filtered if str(peer.get("country", "")) == country]

        status = self.status_filter.currentText()
        if status == "Online Only":
            filtered = [peer for peer in filtered if peer.get("online", False)]
        elif status == "Offline Only":
            filtered = [peer for peer in filtered if not peer.get("online", False)]

        peer_type = self.type_filter.currentText()
        if peer_type != "All Types":
            filtered = [peer for peer in filtered if str(peer.get("type", "")) == peer_type]

        self.filtered_peers = filtered
        self.refresh_table()

    def clear_filters(self):
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self.city_filter.setCurrentIndex(0)
        self.country_filter.setCurrentIndex(0)
        self.status_filter.setCurrentIndex(0)
        self.type_filter.setCurrentIndex(0)
        self.filtered_peers = list(self.original_peers_data)
        self.refresh_table()

    def refresh_table(self):
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.filtered_peers))

        for row, peer in enumerate(self.filtered_peers):
            peer_id = peer.get("id", "")
            check_item = QTableWidgetItem()
            check_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            check_item.setCheckState(
                Qt.CheckState.Checked if peer_id in self.selected_peer_ids else Qt.CheckState.Unchecked
            )
            check_item.setData(Qt.ItemDataRole.UserRole, peer)
            self.table.setItem(row, 0, check_item)

            values = [
                str(self._peer_field(peer, "name", "Unknown")),
                str(self._peer_field(peer, "ip", "")),
                str(self._peer_field(peer, "ip_local", "")),
                str(self._peer_field(peer, "city", "Unknown")),
                str(self._peer_field(peer, "country", "Unknown")),
                "Yes" if peer.get("online", False) else "No",
                self._reliability_text(peer),
                str(self._peer_field(peer, "type", "Unknown")),
                str(self._peer_field(peer, "comm_type", "Unknown")),
            ]
            for col, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, col, item)

        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self.update_peer_count()
        self.update_selection_count()

    def on_item_changed(self, item):
        if item.column() != 0:
            return
        peer = item.data(Qt.ItemDataRole.UserRole)
        if not peer:
            return
        peer_id = peer.get("id", "")
        if not peer_id:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self.selected_peer_ids[peer_id] = peer
        else:
            self.selected_peer_ids.pop(peer_id, None)
        self.update_selection_count()

    def select_all_visible(self):
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            peer = item.data(Qt.ItemDataRole.UserRole) if item else None
            if not peer:
                continue
            peer_id = peer.get("id", "")
            if not peer_id:
                continue
            self.selected_peer_ids[peer_id] = peer
            item.setCheckState(Qt.CheckState.Checked)
        self.table.blockSignals(False)
        self.update_selection_count()

    def select_none(self):
        self.selected_peer_ids.clear()
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self.update_selection_count()

    def update_peer_count(self):
        self.peer_count_label.setText(f"Showing {len(self.filtered_peers)} peers")

    def update_selection_count(self):
        self.selected_count_label.setText(f"Selected: {len(self.selected_peer_ids)}")

    def get_selected_peers(self):
        return list(self.selected_peer_ids.values())



class ActionSequenceEditor(QWidget):
    """Widget for creating and managing installation action sequences"""
    
    ACTION_TYPES = [
        "Create Database",
        "Remove Database",
        "Create Table",
        "Modify Table",
        "Remove Table",
        "Add Record",
        "Modify Record",
        "Remove Record",
        "Create File",
        "Modify File",
        "Rename File",
        "Remove File",
        "Create Folder",
        "Remove Folder",
        "Send Files",
        "Execute SQL Script",
        "Execute Python Script",
        "Execute Shell Script"
    ]
    
    def __init__(self):
        super().__init__()
        self.actions = []
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        action_group = QGroupBox("📋 Installation Action Sequence")
        action_layout = QVBoxLayout(action_group)
        
        button_layout = QHBoxLayout()
        
        self.btn_add_action = QPushButton("➕ Add Action")
        self.btn_add_action.clicked.connect(self.add_action)
        button_layout.addWidget(self.btn_add_action)
        
        self.btn_edit_action = QPushButton("✏️ Edit Selected")
        self.btn_edit_action.clicked.connect(self.edit_action)
        button_layout.addWidget(self.btn_edit_action)
        
        self.btn_delete_action = QPushButton("🗑️ Delete Selected")
        self.btn_delete_action.clicked.connect(self.delete_action)
        button_layout.addWidget(self.btn_delete_action)
        
        self.btn_move_up = QPushButton("⬆️ Move Up")
        self.btn_move_up.clicked.connect(self.move_action_up)
        button_layout.addWidget(self.btn_move_up)
        
        self.btn_move_down = QPushButton("⬇️ Move Down")
        self.btn_move_down.clicked.connect(self.move_action_down)
        button_layout.addWidget(self.btn_move_down)
        
        button_layout.addStretch()
        
        action_layout.addLayout(button_layout)
        
        self.action_table = QTableWidget()
        self.action_table.setColumnCount(3)
        self.action_table.setHorizontalHeaderLabels(["#", "Action Type", "Details"])
        self.action_table.horizontalHeader().setStretchLastSection(True)
        self.action_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.action_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        action_layout.addWidget(self.action_table)
        
        layout.addWidget(action_group)
    
    def add_action(self):
        dialog = ActionEditorDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            action_data = dialog.get_action_data()
            self.actions.append(action_data)
            self.refresh_table()
    
    def edit_action(self):
        current_row = self.action_table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Warning", "Please select an action to edit")
            return
        
        action_data = self.actions[current_row]
        dialog = ActionEditorDialog(action_data=action_data, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.actions[current_row] = dialog.get_action_data()
            self.refresh_table()
    
    def delete_action(self):
        current_row = self.action_table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Warning", "Please select an action to delete")
            return
        
        if QMessageBox.question(self, "Confirm Delete", "Delete this action?") == QMessageBox.StandardButton.Yes:
            self.actions.pop(current_row)
            self.refresh_table()
    
    def move_action_up(self):
        current_row = self.action_table.currentRow()
        if current_row <= 0:
            QMessageBox.warning(self, "Warning", "Cannot move up - already at top or no selection")
            return
        
        self.actions[current_row], self.actions[current_row - 1] = self.actions[current_row - 1], self.actions[current_row]
        self.refresh_table()
        self.action_table.setCurrentCell(current_row - 1, 0)
    
    def move_action_down(self):
        current_row = self.action_table.currentRow()
        if current_row < 0 or current_row >= len(self.actions) - 1:
            QMessageBox.warning(self, "Warning", "Cannot move down - already at bottom or no selection")
            return
        
        self.actions[current_row], self.actions[current_row + 1] = self.actions[current_row + 1], self.actions[current_row]
        self.refresh_table()
        self.action_table.setCurrentCell(current_row + 1, 0)
    
    def refresh_table(self):
        self.action_table.setRowCount(len(self.actions))
        self.action_table.setColumnWidth(0, 30)
        self.action_table.setColumnWidth(1, 150)
        self.action_table.setColumnWidth(2, 450)
        
        for i, action in enumerate(self.actions):
            self.action_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            
            type_item = QTableWidgetItem(action.get('type', ''))
            self.action_table.setItem(i, 1, type_item)
            
            details = self.get_action_details(action)
            details_item = QTableWidgetItem(details)
            details_item.setToolTip(details)
            self.action_table.setItem(i, 2, details_item)
            
            self.action_table.setRowHeight(i, 40)
    
    def get_action_details(self, action):
        action_type = action.get('type', '')
        details = []
        
        if 'Database' in action_type:
            if action_type == 'Create Database':
                path = action.get('db_name', 'N/A')
                name = action.get('db_file_name', 'N/A')
                details.append(f"Path: {path}, Name: {name}")
            else:
                db_label = "Main database" if action.get('db_is_main') else action.get('db_name', 'N/A')
                details.append(f"DB: {db_label}")
        elif 'Table' in action_type:
            db_label = "Main database" if action.get('db_is_main') else action.get('db_name', 'N/A')
            details.append(f"DB: {db_label}")
            details.append(f"Table: {action.get('table_name', 'N/A')}")
            schema = action.get('table_schema', '')
            if schema:
                details.append(f"Schema: {schema[:40]}...")
        elif 'Record' in action_type:
            db_label = "Main database" if action.get('db_is_main') else action.get('db_name', 'N/A')
            details.append(f"DB: {db_label}")
            details.append(f"Table: {action.get('table_name', 'N/A')}")
            record = action.get('record_data', '')
            if record:
                details.append(f"Data: {record[:40]}...")
        elif 'File' in action_type:
            details.append(f"Path: {action.get('file_path', 'N/A')}")
            content = action.get('file_content', '')
            if content:
                details.append(f"Content: {content[:30]}...")
        elif 'Folder' in action_type:
            details.append(f"Path: {action.get('file_path', 'N/A')}")
            if action.get('file_content'):
                details.append(f"Name: {action.get('file_content')}")
        elif 'Script' in action_type:
            if action.get('db_is_main'):
                details.append("DB: Main database")
            elif action.get('db_name'):
                details.append(f"DB: {action.get('db_name')}")
            script = action.get('script_content', '')
            if script:
                lines = script.split('\n')[:2]
                details.append(' '.join(lines))
        elif action_type == 'Send Files':
            send_files = action.get('send_files', [])
            if send_files:
                rel_names = []
                for f in send_files[:3]:
                    try:
                        rel = os.path.relpath(f, init.MAIN_PATH).replace('\\', '/')
                    except Exception:
                        rel = os.path.basename(f)
                    rel_names.append(rel)
                files_text = ', '.join(rel_names)
                if len(send_files) > 3:
                    files_text += f" (and {len(send_files) - 3} more)"
                details.append(f"Files: {files_text}")
            else:
                details.append("No files selected")
        
        return ' | '.join(details) if details else "N/A"
    
    def get_actions_data(self):
        return self.actions
    
    def set_actions_data(self, actions):
        self.actions = actions
        self.refresh_table()


class RecordViewerDialog(QDialog):
    """Dialog to view and select records from a table"""
    
    def __init__(self, columns, records, parent=None):
        super().__init__(parent)
        self.columns = columns
        self.records = records
        self.selected_record = None
        self.setWindowTitle("Select Record")
        self.setModal(True)
        self.resize(700, 500)
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        self.table = QTableWidget()
        self.table.setColumnCount(len(self.columns))
        self.table.setHorizontalHeaderLabels(self.columns)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        
        for i, record in enumerate(self.records):
            self.table.insertRow(i)
            for j, value in enumerate(record):
                self.table.setItem(i, j, QTableWidgetItem(str(value)))
        
        layout.addWidget(self.table)
        
        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()
        
        select_btn = QPushButton("Select")
        select_btn.clicked.connect(self.select_record)
        buttons_layout.addWidget(select_btn)
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(close_btn)
        
        layout.addLayout(buttons_layout)
    
    def select_record(self):
        current_row = self.table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Warning", "Please select a record")
            return
        
        import json
        record_dict = {}
        for j, column in enumerate(self.columns):
            item = self.table.item(current_row, j)
            record_dict[column] = item.text() if item else ""
        
        self.selected_record = json.dumps(record_dict)
        self.accept()
    
    def get_selected_record(self):
        return self.selected_record


class ScriptWizardDialog(QDialog):
    """Wizard for creating scripts"""
    
    PYTHON_TEMPLATES = {
        "File Operations": "import os\nimport shutil\n\n# Create file\nwith open('/path/to/file', 'w') as f:\n    f.write('content')\n\n# Copy file\nshutil.copy('/src', '/dst')\n\n# Remove file\nos.remove('/path/to/file')",
        "Database Operations": "import sqlite3\n\nconn = sqlite3.connect('path/to/db.db')\ncursor = conn.cursor()\n\n# Execute query\ncursor.execute('SELECT * FROM table')\nresults = cursor.fetchall()\n\nconn.close()",
        "System Commands": "import subprocess\n\n# Run command\nresult = subprocess.run(['command', 'args'], capture_output=True)\nprint(result.stdout.decode())",
    }
    
    SHELL_TEMPLATES = {
        "File Operations": "#!/bin/bash\n# Copy file\ncp /src /dst\n\n# Remove file\nrm /path/to/file\n\n# Create directory\nmkdir -p /path/to/dir",
        "System Commands": "#!/bin/bash\n# Run command\ncommand arg1 arg2\n\n# Check exit code\nif [ $? -eq 0 ]; then\n  echo 'Success'\nfi",
        "Environment": "#!/bin/bash\n# Get environment variable\necho $VARIABLE_NAME\n\n# Set environment variable\nexport VARIABLE_NAME=value",
    }
    
    SQL_TEMPLATES = {
        "Insert Data": "INSERT INTO table_name (column1, column2) VALUES (value1, value2);",
        "Update Data": "UPDATE table_name SET column1 = value1 WHERE condition;",
        "Delete Data": "DELETE FROM table_name WHERE condition;",
        "Create Index": "CREATE INDEX index_name ON table_name(column_name);",
        "Alter Table": "ALTER TABLE table_name ADD COLUMN new_column datatype;",
    }
    
    def __init__(self, script_type='Python', parent=None):
        super().__init__(parent)
        self.script_type = script_type
        self.script_content = ""
        self.setWindowTitle(f"{script_type} Script Wizard")
        self.setModal(True)
        self.resize(800, 600)
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel(f"Create {self.script_type} Script"))
        
        if self.script_type == 'Python':
            templates = self.PYTHON_TEMPLATES
        elif self.script_type == 'Shell':
            templates = self.SHELL_TEMPLATES
        else:
            templates = self.SQL_TEMPLATES
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(QLabel("Templates:"))
        
        template_list = QListWidget()
        for template_name in templates.keys():
            template_list.addItem(template_name)
        template_list.itemClicked.connect(lambda item: self.load_template(item.text(), templates))
        left_layout.addWidget(template_list)
        
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(QLabel("Script Content:"))
        
        self.script_editor = QTextEdit()
        self.script_editor.setPlaceholderText("Write your script here or select a template...")
        right_layout.addWidget(self.script_editor)
        
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        
        layout.addWidget(splitter)
        
        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()
        
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        buttons_layout.addWidget(ok_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(cancel_btn)
        
        layout.addLayout(buttons_layout)
    
    def load_template(self, template_name, templates):
        if template_name in templates:
            self.script_editor.setPlainText(templates[template_name])
    
    def get_script(self):
        return self.script_editor.toPlainText()


class ActionEditorDialog(QDialog):
    """Dialog for editing individual actions with context-aware UI"""
    
    def __init__(self, action_data=None, parent=None):
        super().__init__(parent)
        self.action_data = action_data or {}
        self.setWindowTitle("Edit Action" if action_data else "Add Action")
        self.setModal(True)
        self.resize(700, 520)
        self.setMinimumSize(560, 420)
        self.form_labels = {}
        self.form_widgets = {}
        self.form_buttons = {}
        self.form_layout = None
        try:
            from Support.GlobalStyle import main_dialog_style
            self.setStyleSheet(main_dialog_style)
        except Exception:
            pass
        self.setup_ui()
        
        if action_data:
            self.load_action_data()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Action Type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(ActionSequenceEditor.ACTION_TYPES)
        self.type_combo.currentTextChanged.connect(self.on_action_type_changed)
        type_layout.addWidget(self.type_combo)
        type_layout.addStretch()
        layout.addLayout(type_layout)
        
        form_container = QWidget()
        self.form_layout = QGridLayout(form_container)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        self.form_layout.setHorizontalSpacing(8)
        self.form_layout.setVerticalSpacing(8)
        self.form_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        scroll = QScrollArea()
        scroll.setWidget(form_container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(scroll, 1)
        
        dialog_buttons = QHBoxLayout()
        dialog_buttons.addStretch()
        
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        dialog_buttons.addWidget(ok_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        dialog_buttons.addWidget(cancel_btn)
        
        layout.addLayout(dialog_buttons)
        
        self.on_action_type_changed()
    
    def clear_form(self):
        while self.form_layout.count() > 0:
            item = self.form_layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.hide()
                    widget.setParent(None)
                    widget.deleteLater()
                else:
                    layout = item.layout()
                    if layout is not None:
                        while layout.count() > 0:
                            child = layout.takeAt(0)
                            if child.widget():
                                child.widget().hide()
                                child.widget().setParent(None)
                                child.widget().deleteLater()
        # Clear stretch factors so empty rows do not push controls apart
        for r in range(self.form_layout.rowCount()):
            self.form_layout.setRowStretch(r, 0)
        self.form_labels.clear()
        self.form_widgets.clear()
        self.form_buttons.clear()
    
    def add_database_field(self, row_ref, show_browse=True):
        db_label = QLabel("Database:")
        self.form_labels['db_name'] = db_label
        self.form_layout.addWidget(db_label, row_ref[0], 0)
        db_layout = QHBoxLayout()
        self.form_widgets['db_name'] = QComboBox()
        self.form_widgets['db_name'].currentTextChanged.connect(self.on_db_selected)
        db_layout.addWidget(self.form_widgets['db_name'])
        if show_browse:
            db_browse_btn = QPushButton("Browse DB")
            db_browse_btn.clicked.connect(self.browse_database)
            db_layout.addWidget(db_browse_btn)
            self.form_buttons['db_browse'] = db_browse_btn
        self.form_layout.addLayout(db_layout, row_ref[0], 1, 1, 2)
        row_ref[0] += 1
    
    def add_database_path_field(self, row_ref):
        db_label = QLabel("Database Path:")
        self.form_labels['db_name'] = db_label
        self.form_layout.addWidget(db_label, row_ref[0], 0)
        db_layout = QHBoxLayout()
        self.form_widgets['db_name'] = QLineEdit()
        db_layout.addWidget(self.form_widgets['db_name'])
        db_browse_btn = QPushButton("Select Folder")
        db_browse_btn.clicked.connect(self.browse_database_folder)
        db_layout.addWidget(db_browse_btn)
        self.form_buttons['db_browse'] = db_browse_btn
        self.form_layout.addLayout(db_layout, row_ref[0], 1, 1, 2)
        row_ref[0] += 1
    
    def add_database_name_field(self, row_ref):
        name_label = QLabel("Database Name:")
        self.form_labels['db_file_name'] = name_label
        self.form_layout.addWidget(name_label, row_ref[0], 0)
        self.form_widgets['db_file_name'] = QLineEdit()
        self.form_widgets['db_file_name'].setPlaceholderText("e.g., mydatabase.db")
        self.form_layout.addWidget(self.form_widgets['db_file_name'], row_ref[0], 1, 1, 2)
        row_ref[0] += 1
    
    def add_database_file_field(self, row_ref):
        db_label = QLabel("Database File:")
        self.form_labels['db_name'] = db_label
        self.form_layout.addWidget(db_label, row_ref[0], 0)
        db_layout = QHBoxLayout()
        self.form_widgets['db_name'] = QLineEdit()
        db_layout.addWidget(self.form_widgets['db_name'])
        db_browse_btn = QPushButton("Select Database File")
        db_browse_btn.clicked.connect(self.browse_database_file)
        db_layout.addWidget(db_browse_btn)
        self.form_buttons['db_browse'] = db_browse_btn
        self.form_layout.addLayout(db_layout, row_ref[0], 1, 1, 2)
        row_ref[0] += 1
    
    def add_table_name_field(self, row_ref, is_new_table=False):
        table_label = QLabel("Table:")
        self.form_labels['table_name'] = table_label
        self.form_layout.addWidget(table_label, row_ref[0], 0)
        table_layout = QHBoxLayout()
        
        if is_new_table:
            self.form_widgets['table_name'] = QLineEdit()
            self.form_widgets['table_name'].setPlaceholderText("Enter table name...")
        else:
            self.form_widgets['table_name'] = QComboBox()
            self.form_widgets['table_name'].currentTextChanged.connect(self.on_table_selected)
        
        table_layout.addWidget(self.form_widgets['table_name'])
        self.form_layout.addLayout(table_layout, row_ref[0], 1, 1, 2)
        row_ref[0] += 1
    
    def add_file_path_field(self, row_ref, button_text="Browse File", button_callback=None):
        file_label = QLabel("File Path:")
        self.form_labels['file_path'] = file_label
        self.form_layout.addWidget(file_label, row_ref[0], 0)
        file_layout = QHBoxLayout()
        self.form_widgets['file_path'] = QLineEdit()
        file_layout.addWidget(self.form_widgets['file_path'])
        file_btn = QPushButton(button_text)
        if button_callback:
            file_btn.clicked.connect(button_callback)
        file_layout.addWidget(file_btn)
        self.form_buttons['file_browse'] = file_btn
        self.form_layout.addLayout(file_layout, row_ref[0], 1, 1, 2)
        row_ref[0] += 1
    
    def add_table_schema_field(self, row_ref):
        schema_label = QLabel("Table Schema (JSON):")
        self.form_labels['table_schema'] = schema_label
        self.form_layout.addWidget(schema_label, row_ref[0], 0)
        self.form_widgets['table_schema'] = QTextEdit()
        self.form_widgets['table_schema'].setPlaceholderText('{"column_name": "datatype", ...}')
        self.form_layout.addWidget(self.form_widgets['table_schema'], row_ref[0], 1, 1, 2)
        row_ref[0] += 1
    
    def add_record_data_field(self, row_ref, show_view_btn=False):
        record_label = QLabel("Record Data (JSON):")
        self.form_labels['record_data'] = record_label
        self.form_layout.addWidget(record_label, row_ref[0], 0)
        record_layout = QHBoxLayout()
        self.form_widgets['record_data'] = QTextEdit()
        self.form_widgets['record_data'].setPlaceholderText('{"column_name": "value", ...}')
        record_layout.addWidget(self.form_widgets['record_data'])
        if show_view_btn:
            btn_view = QPushButton("View Records")
            btn_view.clicked.connect(self.view_table_records)
            record_layout.addWidget(btn_view)
            self.form_buttons['view_records'] = btn_view
        self.form_layout.addLayout(record_layout, row_ref[0], 1, 1, 2)
        row_ref[0] += 1
    
    def add_file_content_field(self, row_ref, is_line_edit=False, label_text="File Content:"):
        content_label = QLabel(label_text)
        self.form_labels['file_content'] = content_label
        self.form_layout.addWidget(content_label, row_ref[0], 0)
        if is_line_edit:
            self.form_widgets['file_content'] = QLineEdit()
            self.form_widgets['file_content'].setPlaceholderText("Enter new file name...")
        else:
            self.form_widgets['file_content'] = QTextEdit()
        self.form_layout.addWidget(self.form_widgets['file_content'], row_ref[0], 1, 1, 2)
        row_ref[0] += 1
    
    def add_script_field(self, row_ref, show_wizard=True):
        script_label = QLabel("Script:")
        self.form_labels['script_content'] = script_label
        self.form_layout.addWidget(script_label, row_ref[0], 0)
        script_layout = QHBoxLayout()
        self.form_widgets['script_content'] = QTextEdit()
        self.form_widgets['script_content'].setPlaceholderText("Enter your script here...")
        script_layout.addWidget(self.form_widgets['script_content'])
        if show_wizard:
            btn_wizard = QPushButton("Wizard")
            btn_wizard.clicked.connect(self.show_script_wizard)
            script_layout.addWidget(btn_wizard)
            self.form_buttons['wizard'] = btn_wizard
        self.form_layout.addLayout(script_layout, row_ref[0], 1, 1, 2)
        row_ref[0] += 1
    
    def add_sql_preview_field(self, row_ref):
        preview_label = QLabel("Preview:")
        self.form_labels['sql_preview'] = preview_label
        self.form_layout.addWidget(preview_label, row_ref[0], 0)
        self.form_widgets['sql_preview'] = QTextEdit()
        self.form_widgets['sql_preview'].setReadOnly(True)
        self.form_widgets['sql_preview'].setPlaceholderText("Preview of table changes will appear here...")
        self.form_layout.addWidget(self.form_widgets['sql_preview'], row_ref[0], 1, 1, 2)
        row_ref[0] += 1
    
    def add_files_selection_field(self, row_ref):
        files_label = QLabel("Files to Send:")
        self.form_labels['send_files'] = files_label
        self.form_layout.addWidget(files_label, row_ref[0], 0)
        
        files_layout = QVBoxLayout()
        
        # File list widget
        self.form_widgets['send_files'] = QListWidget()
        self.form_widgets['send_files'].setMaximumHeight(150)
        files_layout.addWidget(self.form_widgets['send_files'])
        
        # Buttons layout
        buttons_layout = QHBoxLayout()
        
        add_files_btn = QPushButton("Add Files")
        add_files_btn.clicked.connect(self.add_files_for_sending)
        buttons_layout.addWidget(add_files_btn)
        self.form_buttons['add_files'] = add_files_btn
        
        remove_file_btn = QPushButton("Remove Selected")
        remove_file_btn.clicked.connect(self.remove_selected_send_file)
        buttons_layout.addWidget(remove_file_btn)
        self.form_buttons['remove_file'] = remove_file_btn
        
        clear_files_btn = QPushButton("Clear All")
        clear_files_btn.clicked.connect(self.clear_send_files)
        buttons_layout.addWidget(clear_files_btn)
        self.form_buttons['clear_files'] = clear_files_btn
        
        files_layout.addLayout(buttons_layout)
        
        self.form_layout.addLayout(files_layout, row_ref[0], 1, 1, 2)
        row_ref[0] += 1
    
    def _relative_send_path(self, file_path):
        """Return path relative to MAIN_PATH, or None if outside the app folder."""
        try:
            abs_path = os.path.abspath(file_path)
            main_path = os.path.abspath(init.MAIN_PATH)
            rel_path = os.path.relpath(abs_path, main_path)
            if rel_path.startswith('..'):
                return None
            return rel_path.replace('\\', '/')
        except Exception:
            return None

    def add_files_for_sending(self):
        """Add files to the send files list (must be under the main app folder)."""
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Files to Send", init.MAIN_PATH, "All Files (*.*)"
        )
        
        if files and 'send_files' in self.form_widgets:
            skipped = []
            for file_path in files:
                rel_path = self._relative_send_path(file_path)
                if not rel_path:
                    skipped.append(os.path.basename(file_path))
                    continue

                existing_items = []
                for i in range(self.form_widgets['send_files'].count()):
                    item = self.form_widgets['send_files'].item(i)
                    existing_items.append(item.data(Qt.ItemDataRole.UserRole))
                
                if file_path not in existing_items:
                    item = QListWidgetItem(rel_path)
                    item.setData(Qt.ItemDataRole.UserRole, file_path)
                    item.setToolTip(file_path)
                    self.form_widgets['send_files'].addItem(item)

            if skipped:
                QMessageBox.warning(
                    self,
                    "Invalid Files",
                    "Files must be inside the main app folder:\n" + "\n".join(skipped)
                )
    
    def remove_selected_send_file(self):
        """Remove selected file from send files list"""
        if 'send_files' in self.form_widgets:
            current_row = self.form_widgets['send_files'].currentRow()
            if current_row >= 0:
                self.form_widgets['send_files'].takeItem(current_row)
    
    def clear_send_files(self):
        """Clear all files from send files list"""
        if 'send_files' in self.form_widgets:
            self.form_widgets['send_files'].clear()
    
    def build_form_for_action(self, action_type):
        self.clear_form()
        row = [0]
        
        if action_type == "Create Database":
            self.add_database_path_field(row)
            self.add_database_name_field(row)
        elif action_type == "Remove Database":
            self.add_database_file_field(row)
        elif action_type == "Create Table":
            self.add_database_field(row)
            self.add_table_name_field(row, is_new_table=True)
            self.add_table_schema_field(row)
        elif action_type == "Modify Table":
            self.add_database_field(row)
            self.add_table_name_field(row, is_new_table=False)
            self.add_table_schema_field(row)
        elif action_type == "Remove Table":
            self.add_database_field(row)
            self.add_table_name_field(row, is_new_table=False)
        elif action_type == "Add Record":
            self.add_database_field(row)
            self.add_table_name_field(row, is_new_table=False)
            self.add_record_data_field(row, show_view_btn=True)
        elif action_type == "Modify Record":
            self.add_database_field(row)
            self.add_table_name_field(row, is_new_table=False)
            self.add_record_data_field(row, show_view_btn=True)
        elif action_type == "Remove Record":
            self.add_database_field(row)
            self.add_table_name_field(row, is_new_table=False)
            self.add_record_data_field(row, show_view_btn=False)
        elif action_type == "Create File":
            self.add_file_path_field(row, button_text="Select Folder", button_callback=self.browse_folder)
            self.add_file_content_field(row, label_text="File Name:")
        elif action_type == "Modify File":
            self.add_file_path_field(row, button_text="Select File", button_callback=self.browse_and_load_file)
            self.add_file_content_field(row, label_text="File Content:")
        elif action_type == "Rename File":
            self.add_file_path_field(row, button_text="Select File", button_callback=self.browse_and_load_file)
            self.add_file_content_field(row, is_line_edit=True, label_text="New File Name:")
        elif action_type == "Remove File":
            self.add_file_path_field(row, button_text="Select File", button_callback=self.browse_file)
        elif action_type == "Create Folder":
            self.add_file_path_field(row, button_text="Select Parent Folder", button_callback=self.browse_folder)
            self.add_file_content_field(row, is_line_edit=True, label_text="Folder Name:")
        elif action_type == "Remove Folder":
            self.add_file_path_field(row, button_text="Select Folder", button_callback=self.browse_folder)
        elif action_type == "Send Files":
            self.add_files_selection_field(row)
        elif action_type == "Execute SQL Script":
            self.add_database_field(row, show_browse=False)
            self.add_script_field(row, show_wizard=True)
            self.add_sql_preview_field(row)
        elif action_type == "Execute Python Script":
            self.add_script_field(row, show_wizard=True)
        elif action_type == "Execute Shell Script":
            self.add_script_field(row, show_wizard=True)
        
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        self.form_layout.setHorizontalSpacing(8)
        self.form_layout.setVerticalSpacing(8)
        # Do not stretch empty rows - keeps controls packed at the top
    
    def load_databases(self):
        if 'db_name' in self.form_widgets and isinstance(self.form_widgets['db_name'], QComboBox):
            self.form_widgets['db_name'].clear()
            try:
                self.form_widgets['db_name'].addItem("Loading databases...")

                app_root = init.MAIN_PATH
                htdocs = os.path.join(app_root, "System", "neuron", "htdocs")
                db_dirs = [htdocs, app_root]
                databases_found = []

                for db_dir in db_dirs:
                    if not os.path.exists(db_dir):
                        continue
                    for file in os.listdir(db_dir):
                        full = os.path.join(db_dir, file)
                        if not os.path.isfile(full):
                            continue
                        # Standard .db files
                        if file.endswith('.db'):
                            rel_path = os.path.relpath(full, app_root)
                            databases_found.append(rel_path)
                            continue
                        # Skip sqlite sidecars / section-marked files / non-db clutter
                        if file.endswith('-shm') or file.endswith('-wal') or file.endswith('.php') or file.endswith('.txt') or file.endswith('.html') or file.endswith('.rar'):
                            continue
                        if file.endswith('\u00a7') or file.endswith('§'):
                            continue

                # Always include the main app database (hash name, no extension)
                main_name = getattr(init, 'DATABASE_NAME', '') or ''
                if main_name:
                    main_full = os.path.join(htdocs, main_name)
                    if os.path.isfile(main_full):
                        databases_found.append(os.path.relpath(main_full, app_root))

                self.form_widgets['db_name'].clear()
                for db_path in sorted(set(databases_found), key=lambda p: p.lower()):
                    self.form_widgets['db_name'].addItem(db_path)

                if not databases_found:
                    self.form_widgets['db_name'].addItem("No databases found")

            except Exception as e:
                self.form_widgets['db_name'].clear()
                self.form_widgets['db_name'].addItem("Error loading databases")
                print(f"Error loading databases: {e}")
    
    def browse_database(self):
        try:
            app_root = init.MAIN_PATH
            file_path = QFileDialog.getOpenFileName(
                self, "Select Database File", app_root, "Database Files (*.db)"
            )[0]
            if file_path:
                rel_path = os.path.relpath(file_path, app_root)
                if 'db_name' in self.form_widgets and isinstance(self.form_widgets['db_name'], QComboBox):
                    idx = self.form_widgets['db_name'].findText(rel_path)
                    if idx < 0:
                        self.form_widgets['db_name'].addItem(rel_path)
                        idx = self.form_widgets['db_name'].count() - 1
                    self.form_widgets['db_name'].setCurrentIndex(idx)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to browse database: {e}")
    
    def browse_database_folder(self):
        try:
            app_root = init.MAIN_PATH
            folder_path = QFileDialog.getExistingDirectory(
                self, "Select Folder for Database", app_root
            )
            if folder_path:
                rel_path = os.path.relpath(folder_path, app_root)
                self.form_widgets['db_name'].setText(rel_path)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to select folder: {e}")
    
    def browse_database_file(self):
        try:
            app_root = init.MAIN_PATH
            file_path = QFileDialog.getOpenFileName(
                self, "Select Database File to Remove", app_root, "Database Files (*.db)"
            )[0]
            if file_path:
                rel_path = os.path.relpath(file_path, app_root)
                self.form_widgets['db_name'].setText(rel_path)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to select database file: {e}")
    
    def browse_folder(self):
        try:
            app_root = init.MAIN_PATH
            folder_path = QFileDialog.getExistingDirectory(
                self, "Select Folder", app_root
            )
            if folder_path:
                rel_path = os.path.relpath(folder_path, app_root)
                self.form_widgets['file_path'].setText(rel_path)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to select folder: {e}")
    
    def browse_file(self):
        try:
            app_root = init.MAIN_PATH
            file_path = QFileDialog.getOpenFileName(
                self, "Select File", app_root
            )[0]
            if file_path:
                rel_path = os.path.relpath(file_path, app_root)
                self.form_widgets['file_path'].setText(rel_path)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to browse file: {e}")
    
    def browse_and_load_file(self):
        try:
            app_root = init.MAIN_PATH
            file_path = QFileDialog.getOpenFileName(
                self, "Select File", app_root
            )[0]
            if file_path:
                rel_path = os.path.relpath(file_path, app_root)
                self.form_widgets['file_path'].setText(rel_path)
                
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    if isinstance(self.form_widgets['file_content'], QLineEdit):
                        self.form_widgets['file_content'].setText(os.path.basename(file_path))
                    else:
                        self.form_widgets['file_content'].setPlainText(content)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load file: {e}")
    
    def on_db_selected(self):
        self.load_tables()
    
    def load_tables(self):
        if 'table_name' not in self.form_widgets or not isinstance(self.form_widgets['table_name'], QComboBox):
            return
            
        self.form_widgets['table_name'].clear()
        db_name = self.form_widgets['db_name'].currentText()
        if not db_name:
            return
        
        try:
            app_root = init.MAIN_PATH
            db_path = os.path.join(app_root, db_name)
            conn = sql.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            self.form_widgets['table_name'].addItems(tables)
            conn.close()
        except Exception as e:
            print(f"Error loading tables: {e}")
    
    def on_table_selected(self):
        pass
    
    def view_table_records(self):
        db_name = self.form_widgets['db_name'].currentText()
        
        table_widget = self.form_widgets['table_name']
        if isinstance(table_widget, QLineEdit):
            table_name = table_widget.text()
        else:
            table_name = table_widget.currentText()
        
        if not db_name or not table_name:
            QMessageBox.warning(self, "Warning", "Please select a database and table")
            return
        
        try:
            app_root = init.MAIN_PATH
            db_path = os.path.join(app_root, db_name)
            conn = sql.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM {table_name}")
            columns = [description[0] for description in cursor.description]
            records = cursor.fetchall()
            conn.close()
            
            dialog = RecordViewerDialog(columns, records, parent=self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                selected_record = dialog.get_selected_record()
                if selected_record:
                    self.form_widgets['record_data'].setPlainText(selected_record)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to view records: {e}")
    
    def show_script_wizard(self):
        action_type = self.type_combo.currentText()
        
        if 'Python' in action_type:
            script_type = 'Python'
        elif 'Shell' in action_type:
            script_type = 'Shell'
        else:
            script_type = 'SQL'
        
        wizard = ScriptWizardDialog(script_type, parent=self)
        if wizard.exec() == QDialog.DialogCode.Accepted:
            script_content = wizard.get_script()
            self.form_widgets['script_content'].setPlainText(script_content)
            
            if action_type == "Execute SQL Script":
                self.update_sql_preview()
    
    def update_sql_preview(self):
        if 'sql_preview' not in self.form_widgets:
            return
        
        try:
            db_name = self.form_widgets['db_name'].currentText()
            script_content = self.form_widgets['script_content'].toPlainText()
            
            if not db_name or not script_content.strip():
                self.form_widgets['sql_preview'].setPlainText("Select a database and enter a SQL script to see preview")
                return
            
            app_root = init.MAIN_PATH
            db_path = os.path.join(app_root, db_name)
            conn = sql.connect(db_path)
            cursor = conn.cursor()
            
            preview_text = f"Database: {db_name}\n\n"
            preview_text += f"SQL Command:\n{script_content}\n\n"
            preview_text += "Note: This is a preview. The actual changes will be applied when the action executes."
            
            self.form_widgets['sql_preview'].setPlainText(preview_text)
            conn.close()
        except Exception as e:
            self.form_widgets['sql_preview'].setPlainText(f"Error generating preview: {str(e)}")
    
    def on_action_type_changed(self):
        action_type = self.type_combo.currentText()
        self.build_form_for_action(action_type)
        if 'db_name' in self.form_widgets and isinstance(self.form_widgets['db_name'], QComboBox):
            self.load_databases()
    
    def load_action_data(self):
        if 'type' in self.action_data:
            action_type = self.action_data['type']
            idx = self.type_combo.findText(action_type)
            if idx >= 0:
                self.type_combo.setCurrentIndex(idx)
                self.build_form_for_action(action_type)
        
        for key, widget in self.form_widgets.items():
            if key in self.action_data and key != 'sql_preview':
                value = self.action_data[key]
                if key == 'send_files' and isinstance(widget, QListWidget) and isinstance(value, list):
                    # Handle file list widget specially
                    widget.clear()
                    for file_path in value:
                        if file_path:
                            try:
                                rel = os.path.relpath(file_path, init.MAIN_PATH).replace('\\', '/')
                                if rel.startswith('..'):
                                    rel = os.path.basename(file_path)
                            except Exception:
                                rel = os.path.basename(file_path)
                            item = QListWidgetItem(rel)
                            item.setData(Qt.ItemDataRole.UserRole, file_path)
                            item.setToolTip(file_path)
                            widget.addItem(item)
                elif isinstance(widget, QLineEdit):
                    widget.setText(str(value))
                elif isinstance(widget, QTextEdit):
                    widget.setPlainText(str(value))
                elif isinstance(widget, QComboBox):
                    idx = widget.findText(str(value))
                    if idx >= 0:
                        widget.setCurrentIndex(idx)
                    else:
                        widget.addItem(str(value))
                        widget.setCurrentText(str(value))
    
    def get_action_data(self):
        data = {'type': self.type_combo.currentText()}
        
        for key, widget in self.form_widgets.items():
            if key == 'sql_preview':
                continue
            elif key == 'send_files' and isinstance(widget, QListWidget):
                # Handle the file list widget specially
                files_data = []
                for i in range(widget.count()):
                    item = widget.item(i)
                    file_path = item.data(Qt.ItemDataRole.UserRole)
                    if file_path:
                        files_data.append(file_path)
                data[key] = files_data
            elif isinstance(widget, QLineEdit):
                data[key] = widget.text()
            elif isinstance(widget, QTextEdit):
                data[key] = widget.toPlainText()
            elif isinstance(widget, QComboBox):
                data[key] = widget.currentText()

        # Main app DB has a per-install hash name; mark it so peers use their own DATABASE_NAME
        if 'db_name' in data:
            main_name = getattr(init, 'DATABASE_NAME', '') or ''
            selected = str(data.get('db_name', ''))
            data['db_is_main'] = bool(
                main_name and os.path.basename(selected.replace('/', os.sep)) == main_name
            )
        
        return data


class ModuleDeployerApp(QMainWindow):
    """Enhanced Module Deployer with Full Admin Functionality"""

    def __init__(self):
        super().__init__()
        self.tab_widget = None
        self.setWindowTitle("🚀 Neuron Advanced Admin & Module Deployer v3.0")
        self.setGeometry(100, 100, 1400, 900)
        init.sql_result_consumer = self.handle_peer_sql_result

        # Initialize data
        self.selected_modules = []
        self.available_peers = []
        self.log_display = None
        self.banner_file = None
        self.app_deployment_folder_path = ""
        self.app_image_paths = {}
        self.app_image_labels = {}
        self.app_selected_date = ""
        self.last_sql_result_id = 0  # Track the last processed SQL result ID
        self.retrieve_logs_selected_peer = None
        self._last_retrieved_logs_mtime = None
        self.retrieved_logs_temp_dir = os.path.join(init.PATH_ADMIN, "temp_retrieved_logs")
        self.package_worker = None

        # Admin constants
        self.NODE_TYPE_ADMINISTRATOR = 7677770

        # Initialize ME data
        try:
            me_data = query_database(init.pool_main_db, "SELECT * FROM Me", '')[0]
            # Convert tuple to dictionary for easier access
            self.ME = {
                'id': me_data[0] if len(me_data) > 0 and me_data[0] else (init.ME.get('id', '') if init.ME else ''),
                'ip': me_data[3] if len(me_data) > 3 and me_data[3] else init.ME.get('ip', ''),
                'ip_local': me_data[4] if len(me_data) > 4 and me_data[4] else init.ME.get('ip_local', '')
            }
        except (IndexError, AttributeError, TypeError):
            # Use init.ME as fallback - NEVER hardcode!
            self.ME = {
                'id': init.ME.get('id', '') if init.ME else '',
                'ip': init.ME.get('ip', '') if init.ME else '',
                'ip_local': init.ME.get('ip_local', '') if init.ME else ''
            }

        # Standalone AdminConsole never loads dictionaries. send_packet auto-fills
        # src_peer_id from init.ME; peers drop REMOTE_ADMIN_COMMANDS when that is
        # empty because ADMIN_TRUSTED_PEER_ID is pinned.
        try:
            if self.ME.get('id') and (not init.ME or not init.ME.get('id')):
                init.ME = self.ME
        except Exception:
            pass

        # Ensure Ed25519 admin keypair: private key under ADMIN/keys/, pubkey in System/json/
        self.admin_signing_ready = False
        try:
            from Support.ModuleSecurity import ensure_admin_keypair, migrate_admin_private_keys
            migrate_admin_private_keys()
            self.admin_signing_ready = ensure_admin_keypair(
                init.PATH_ADMIN_PRIVKEY, init.PATH_ADMIN_PUBKEY)
            if self.admin_signing_ready:
                print("[ADMIN_AUTH] Admin signing key ready; remote commands will be Ed25519-signed")
            else:
                print("[ADMIN_AUTH] WARNING: Admin signing key missing; remote admin commands will be rejected by nodes")
        except Exception as e:
            print(f"[ADMIN_AUTH] Key setup failed: {e}")

        self.init_ui()
        self.load_peers()
        
        # Set up SQL results polling timer
        self.sql_results_timer = QTimer()
        self.sql_results_timer.timeout.connect(self.check_for_new_sql_results)
        self.sql_results_timer.timeout.connect(self.check_for_retrieved_logs)
        self.sql_results_timer.timeout.connect(self.refresh_releases_list)
        self.sql_results_timer.start(1000)  # Check every 1 second

        # Apply the main dialog dark theme
        self.setStyleSheet(main_dialog_style + """
            QMainWindow {
                background-color: #252a3a;
            }
            QStatusBar {
                color: #e0e0e0;
                background-color: #2c2c2c;
                border-top: 1px solid #3a4366;
            }
        """)

    def init_ui(self):
        """Initialize the comprehensive admin interface"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Create main layout with splitter for better organization
        main_layout = QVBoxLayout(central_widget)

        # Header with title and status
        header = self.create_header()
        main_layout.addWidget(header)

        # Create tab widget with enhanced styling
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabPosition(QTabWidget.TabPosition.North)
        main_layout.addWidget(self.tab_widget)

        # Add all tabs with icons and enhanced functionality
        self.add_all_tabs()

        # Status bar
        if getattr(self, 'admin_signing_ready', False):
            self.statusBar().showMessage("Ready - Admin Ed25519 signing active (emergency remote admin)")
        else:
            self.statusBar().showMessage("WARNING - Admin signing key missing; remote commands will fail")

    def create_header(self):
        """Create a fancy header with system info"""
        header_frame = QFrame()
        header_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        header_frame.setMaximumHeight(80)

        header_layout = QHBoxLayout(header_frame)

        # Title and version
        title_label = QLabel("🚀 Neuron Advanced Admin & Module Deployer")
        title_label.setStyleSheet("""
            QLabel {
                font-size: 18px;
                font-weight: bold;
                color: #4a90e2;
                padding: 10px;
                background-color: transparent;
            }
        """)
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        # System status
        status_label = QLabel(f"🌐 Admin IP: {self.ME.get('ip', 'Unknown')}")
        status_label.setStyleSheet("""
            QLabel {
                font-size: 12px;
                color: #e0e0e0;
                padding: 10px;
                background-color: transparent;
            }
        """)
        header_layout.addWidget(status_label)

        return header_frame

    def add_all_tabs(self):
        """Add all admin tabs with enhanced functionality"""
        # 1. Network Statistics Tab
        network_tab = self.create_network_stats_tab()
        self.tab_widget.addTab(network_tab, "📊 Network Stats")

        # 2. Neuron Management Tab
        neuron_tab = self.create_neuron_management_tab()
        self.tab_widget.addTab(neuron_tab, "💰 Neuron Management")

        # 3. SQL Commands Tab
        sql_tab = self.create_sql_commands_tab()
        self.tab_widget.addTab(sql_tab, "🗄️ SQL Commands")

        # 4. Lottery Tab
        lottery_tab = self.create_lottery_tab()
        self.tab_widget.addTab(lottery_tab, "🎰 Lottery System")

        # 6. Module Deployment Tab (Enhanced)
        module_tab = self.create_enhanced_module_tab()
        self.tab_widget.addTab(module_tab, "📦 Module Deployment")

        # 7. File Deployment Tab
        file_tab = self.create_file_deployment_tab()
        self.tab_widget.addTab(file_tab, "📁 File Deployment")

        # 8. App Deployment Tab
        app_deployment_tab = self.create_app_deployment_tab()
        self.tab_widget.addTab(app_deployment_tab, "🚀 App Deployment")

        # 9. Banner/Ads Tab
        banner_tab = self.create_banner_tab()
        self.tab_widget.addTab(banner_tab, "🎨 Banner Management")

        # 10. Logs Tab (Enhanced)
        logs_tab = self.create_enhanced_logs_tab()
        self.tab_widget.addTab(logs_tab, "📋 System Logs")

        # 11. Retrieve logs from peer
        retrieve_logs_tab = self.create_retrieve_logs_tab()
        self.tab_widget.addTab(retrieve_logs_tab, "Retrieve logs")

        # 12. Create Package
        create_package_tab = self.create_package_tab()
        self.tab_widget.addTab(create_package_tab, "CREATE PACKAGE")

        # 13. Change Management (DEV/TEST/PROD) -- logic in NeuronChangeManagement folder
        try:
            cm_tab = self.create_change_management_tab()
            self.tab_widget.addTab(cm_tab, "Change Management")
        except Exception as e:
            print(f"[CM] Change Management tab unavailable: {e}")

        self.tab_widget.currentChanged.connect(self.on_tab_changed)

    def on_tab_changed(self, index):
        """Refresh neuron peer result columns when Neuron Management is shown"""
        if index == 1:
            self.refresh_neuron_peer_results()

    def create_network_stats_tab(self):
        """Create network statistics tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        btn_advanced_selection = QPushButton("🌐 Advanced Peer Selection")
        btn_advanced_selection.clicked.connect(self.show_peer_selection_dialog)
        btn_advanced_selection.setStyleSheet("QPushButton { background-color: #4a90e2; color: white; font-weight: bold; padding: 8px; }")
        layout.addWidget(btn_advanced_selection)

        control_group = QGroupBox("🌐 Network Statistics Control")
        control_layout = QGridLayout(control_group)

        control_layout.addWidget(QLabel("Target IP:"), 0, 0)
        self.dst_ip_input = QLineEdit("...")
        control_layout.addWidget(self.dst_ip_input, 0, 1)

        control_layout.addWidget(QLabel("Local IP:"), 0, 2)
        self.dst_local_ip_input = QLineEdit("...")
        control_layout.addWidget(self.dst_local_ip_input, 0, 3)

        self.all_peers_checkbox = QCheckBox("Query All Peers")
        control_layout.addWidget(self.all_peers_checkbox, 1, 0, 1, 2)

        btn_layout = QHBoxLayout()
        self.btn_get_stats = QPushButton("📊 Get Network Stats")
        self.btn_get_stats.clicked.connect(self.get_network_stats)
        btn_layout.addWidget(self.btn_get_stats)

        self.btn_reset_stats = QPushButton("🔄 Reset Fields")
        self.btn_reset_stats.clicked.connect(self.reset_network_stats_fields)
        btn_layout.addWidget(self.btn_reset_stats)

        self.btn_refresh_display = QPushButton("🔄 Refresh Table")
        self.btn_refresh_display.clicked.connect(self.update_network_stats_display)
        btn_layout.addWidget(self.btn_refresh_display)

        control_layout.addLayout(btn_layout, 2, 0, 1, 4)
        layout.addWidget(control_group)

        mid_row = QHBoxLayout()

        stats_group = QGroupBox("📈 Network Statistics")
        stats_layout = QGridLayout(stats_group)
        stats_layout.setContentsMargins(10, 20, 10, 10)

        self.stats_fields = {}
        stats_labels = [
            ("Total Users:", "total_users"),
            ("Nodes:", "nodes"),
            ("BRD Servers:", "brd_servers"),
            ("DB Servers:", "db_servers"),
            ("App Servers:", "app_servers"),
            ("SFU Servers:", "sfu_servers"),
            ("Super DB Servers:", "super_db_servers"),
            ("Kickstarters:", "kickstarters"),
            ("Administrators:", "administrators"),
            ("Incompatible:", "incompatible"),
            ("Total Neurons:", "neurons"),
            ("Total Space:", "total_space")
        ]

        row = 0
        col = 0
        for label_text, field_name in stats_labels:
            stats_layout.addWidget(QLabel(label_text), row, col * 2)
            field = QLineEdit()
            field.setReadOnly(True)
            self.stats_fields[field_name] = field
            stats_layout.addWidget(field, row, col * 2 + 1)
            row += 1
            if row >= 6:
                row = 0
                col = 1

        mid_row.addWidget(stats_group, 1)

        server_chart_group = QGroupBox("📊 Server Type Distribution")
        server_chart_layout = QVBoxLayout(server_chart_group)
        self.server_chart_canvas = ServerDistributionChart()
        self.server_chart_canvas.setMinimumHeight(180)
        server_chart_layout.addWidget(self.server_chart_canvas)
        mid_row.addWidget(server_chart_group, 2)

        layout.addLayout(mid_row, 1)

        bottom_row = QHBoxLayout()

        node_type_group = QGroupBox("📊 Node Type Statistics")
        node_type_layout = QVBoxLayout(node_type_group)
        self.node_type_table = QTableWidget()
        self.node_type_table.setColumnCount(5)
        self.node_type_table.setHorizontalHeaderLabels(["Node Type", "Count", "Percentage", "Total Neurons", "Total Space"])
        self.node_type_table.horizontalHeader().setStretchLastSection(True)
        node_type_layout.addWidget(self.node_type_table)
        bottom_row.addWidget(node_type_group, 2)

        country_group = QGroupBox("🌍 Country Distribution")
        country_layout = QVBoxLayout(country_group)
        self.country_table = QTableWidget()
        self.country_table.setColumnCount(2)
        self.country_table.setHorizontalHeaderLabels(["Country", "Count"])
        self.country_table.horizontalHeader().setStretchLastSection(True)
        country_layout.addWidget(self.country_table)
        bottom_row.addWidget(country_group, 1)

        layout.addLayout(bottom_row, 1)

        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.update_network_stats_display)
        self.refresh_timer.start(1000)

        return widget

    def create_neuron_management_tab(self):
        """Create neuron management tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        btn_advanced_selection = QPushButton("🌐 Advanced Peer Selection")
        btn_advanced_selection.clicked.connect(self.show_neuron_peer_selection_dialog)
        btn_advanced_selection.setStyleSheet("QPushButton { background-color: #4a90e2; color: white; font-weight: bold; padding: 8px; }")
        layout.addWidget(btn_advanced_selection)

        selected_group = QGroupBox("📋 Selected Peers")
        selected_layout = QVBoxLayout(selected_group)

        query_controls = QHBoxLayout()
        self.btn_query_neurons = QPushButton("🔍 Query Neurons")
        self.btn_query_neurons.clicked.connect(self.query_neurons)
        self.btn_query_neurons.setEnabled(False)
        query_controls.addWidget(self.btn_query_neurons)
        query_controls.addStretch()
        selected_layout.addLayout(query_controls)

        self.neuron_table = QTableWidget()
        self.neuron_table.setColumnCount(5)
        self.neuron_table.setHorizontalHeaderLabels(["Name", "IP", "Local IP", "Country", "Neurons"])
        self.neuron_table.horizontalHeader().setStretchLastSection(True)
        self.neuron_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        selected_layout.addWidget(self.neuron_table, 1)

        layout.addWidget(selected_group, 1)

        action_group = QGroupBox("⚡ Give/Take Neurons")
        action_layout = QVBoxLayout(action_group)

        radio_layout = QHBoxLayout()
        self.give_radio = QRadioButton("Give Neurons")
        self.take_radio = QRadioButton("Take Neurons")
        self.give_radio.setChecked(True)
        radio_layout.addWidget(self.give_radio)
        radio_layout.addWidget(self.take_radio)
        action_layout.addLayout(radio_layout)

        amount_layout = QHBoxLayout()
        amount_layout.addWidget(QLabel("Amount:"))
        self.neuron_amount_input = QLineEdit("0")
        amount_layout.addWidget(self.neuron_amount_input)
        action_layout.addLayout(amount_layout)

        action_layout.addWidget(QLabel("Message:"))
        self.neuron_message_input = QTextEdit()
        self.neuron_message_input.setMaximumHeight(100)
        action_layout.addWidget(self.neuron_message_input)

        self.btn_execute_neurons = QPushButton("⚡ Execute Neuron Transaction")
        self.btn_execute_neurons.clicked.connect(self.give_take_neurons)
        self.btn_execute_neurons.setEnabled(False)
        action_layout.addWidget(self.btn_execute_neurons)

        layout.addWidget(action_group)

        self.neuron_management_widget = widget
        self.neuron_selected_peers = []
        self.sql_selected_peers = []
        return widget

    def create_sql_commands_tab(self):
        """Create SQL commands tab with results display area"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Advanced peer selection button at the top
        btn_advanced_selection = QPushButton("🌐 Advanced Peer Selection")
        btn_advanced_selection.clicked.connect(self.show_sql_peer_selection_dialog)
        btn_advanced_selection.setStyleSheet("QPushButton { background-color: #4a90e2; color: white; font-weight: bold; padding: 8px; }")
        layout.addWidget(btn_advanced_selection)

        # Selected peers display section
        selected_group = QGroupBox("📋 Selected Peers")
        selected_layout = QVBoxLayout(selected_group)
        self.sql_selected_peers_table = QTableWidget()
        self.sql_selected_peers_table.setColumnCount(3)
        self.sql_selected_peers_table.setHorizontalHeaderLabels(["Name", "IP", "Local IP"])
        self.sql_selected_peers_table.setMaximumHeight(120)
        selected_layout.addWidget(self.sql_selected_peers_table)
        layout.addWidget(selected_group)

        # SQL command execution group
        sql_group = QGroupBox("🗄️ SQL Command Execution")
        sql_layout = QVBoxLayout(sql_group)

        # SQL input
        sql_layout.addWidget(QLabel("SQL Command:"))
        self.sql_input = QTextEdit()
        self.sql_input.setMaximumHeight(150)
        self.sql_input.setPlaceholderText("Enter SQL command here...")
        sql_layout.addWidget(self.sql_input)

        # Execute button
        self.btn_execute_sql = QPushButton("🚀 Execute SQL Command")
        self.btn_execute_sql.clicked.connect(self.send_sql_command)
        sql_layout.addWidget(self.btn_execute_sql)

        layout.addWidget(sql_group)

        # SQL results display group
        results_group = QGroupBox("📊 SQL Results from Peers")
        results_layout = QVBoxLayout(results_group)

        # Clear results button
        self.btn_clear_sql_results = QPushButton("🧹 Clear Results")
        self.btn_clear_sql_results.clicked.connect(self.clear_sql_results)
        self.btn_clear_sql_results.setStyleSheet("QPushButton { background-color: #e74c3c; color: white; padding: 4px; }")
        results_layout.addWidget(self.btn_clear_sql_results)

        # Scrollable area for results
        self.sql_results_scroll = QScrollArea()
        self.sql_results_scroll.setWidgetResizable(True)
        self.sql_results_scroll.setMinimumHeight(400)

        # Container for all SQL result tables
        self.sql_results_container = QWidget()
        self.sql_results_layout = QVBoxLayout(self.sql_results_container)
        self.sql_results_layout.setContentsMargins(5, 5, 5, 5)
        self.sql_results_layout.addStretch()

        self.sql_results_scroll.setWidget(self.sql_results_container)
        results_layout.addWidget(self.sql_results_scroll)

        layout.addWidget(results_group)

        return widget

    def create_lottery_tab(self):
        """Create lottery management tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Advanced peer selection button at the top
        btn_advanced_selection = QPushButton("🌐 Advanced Peer Selection")
        btn_advanced_selection.clicked.connect(self.show_peer_selection_dialog)
        btn_advanced_selection.setStyleSheet("QPushButton { background-color: #4a90e2; color: white; font-weight: bold; padding: 8px; }")
        layout.addWidget(btn_advanced_selection)

        # Peer selection section
        layout.addWidget(self.create_peer_selection_section())

        lottery_group = QGroupBox("🎰 Lottery System")
        lottery_layout = QVBoxLayout(lottery_group)

        # Lottery controls
        controls_layout = QHBoxLayout()

        controls_layout.addWidget(QLabel("Jackpot Amount:"))
        self.lottery_amount_input = QLineEdit("1000")
        controls_layout.addWidget(self.lottery_amount_input)

        self.lottery_running_checkbox = QCheckBox("Lottery Running")
        self.lottery_running_checkbox.setEnabled(False)
        controls_layout.addWidget(self.lottery_running_checkbox)

        lottery_layout.addLayout(controls_layout)

        # Start/Stop button
        self.btn_lottery = QPushButton("🎰 Start/Stop Lottery")
        self.btn_lottery.clicked.connect(self.start_stop_lottery)
        lottery_layout.addWidget(self.btn_lottery)

        layout.addWidget(lottery_group)

        return widget

    def create_enhanced_module_tab(self):
        """Module Deployment tab - releases list, ACK/NAK status, and Release Wizard"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        info = QLabel(
            "Build a full app release (modules, files/folders, database changes, update script),\n"
            "Ed25519-sign and AES-256-GCM encrypt it, then auto-seed supernodes only."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.btn_open_release_wizard = QPushButton("Open Release Wizard")
        self.btn_open_release_wizard.clicked.connect(self.open_release_wizard)
        self.btn_open_release_wizard.setStyleSheet(
            "QPushButton { background-color: #4a90e2; color: white; font-weight: bold; padding: 12px; font-size: 14px; }"
        )
        layout.addWidget(self.btn_open_release_wizard)

        releases_group = QGroupBox("Releases")
        releases_layout = QVBoxLayout(releases_group)

        releases_btns = QHBoxLayout()
        self.btn_refresh_releases = QPushButton("Refresh")
        self.btn_refresh_releases.clicked.connect(self.refresh_releases_list)
        releases_btns.addWidget(self.btn_refresh_releases)
        self.btn_seed_selected_release = QPushButton("Seed selected")
        self.btn_seed_selected_release.clicked.connect(self.seed_selected_release)
        releases_btns.addWidget(self.btn_seed_selected_release)
        self.btn_resend_release_naks = QPushButton("Resend to NAK'd peers")
        self.btn_resend_release_naks.clicked.connect(self.resend_release_to_naks)
        releases_btns.addWidget(self.btn_resend_release_naks)
        self.btn_delete_selected_release = QPushButton("Delete selected")
        self.btn_delete_selected_release.clicked.connect(self.delete_selected_release)
        releases_btns.addWidget(self.btn_delete_selected_release)
        releases_btns.addStretch()
        releases_layout.addLayout(releases_btns)

        self.releases_hint = QLabel(
            "Built packages stay local until Seed. Pending counts peers still waiting for ACK/NAK."
        )
        self.releases_hint.setWordWrap(True)
        releases_layout.addWidget(self.releases_hint)

        self.releases_table = QTableWidget()
        self.releases_table.setColumnCount(7)
        self.releases_table.setHorizontalHeaderLabels([
            "Release ID", "Version", "Created", "Status", "ACK", "NAK", "Pending"
        ])
        self.releases_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.releases_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.releases_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.releases_table.horizontalHeader().setStretchLastSection(True)
        self.releases_table.itemSelectionChanged.connect(self.on_release_selection_changed)
        self.releases_table.setMaximumHeight(180)
        releases_layout.addWidget(self.releases_table)

        status_split = QHBoxLayout()
        ack_group = QGroupBox("ACK")
        ack_layout = QVBoxLayout(ack_group)
        self.release_ack_list = QListWidget()
        ack_layout.addWidget(self.release_ack_list)
        status_split.addWidget(ack_group)

        nak_group = QGroupBox("NAK")
        nak_layout = QVBoxLayout(nak_group)
        self.release_nak_list = QListWidget()
        nak_layout.addWidget(self.release_nak_list)
        status_split.addWidget(nak_group)

        pending_group = QGroupBox("Pending")
        pending_layout = QVBoxLayout(pending_group)
        self.release_pending_list = QListWidget()
        pending_layout.addWidget(self.release_pending_list)
        status_split.addWidget(pending_group)

        releases_layout.addLayout(status_split)
        layout.addWidget(releases_group)

        legacy_group = QGroupBox("Legacy single-module deploy (optional)")
        legacy_layout = QVBoxLayout(legacy_group)
        legacy_hint = QLabel("Prefer the Release Wizard for production updates.")
        legacy_layout.addWidget(legacy_hint)

        self.btn_legacy_module_ui = QPushButton("Show Legacy Module Controls")
        self.btn_legacy_module_ui.setCheckable(True)
        self.btn_legacy_module_ui.toggled.connect(self.toggle_legacy_module_ui)
        legacy_layout.addWidget(self.btn_legacy_module_ui)

        self.legacy_module_container = QWidget()
        self.legacy_module_container.setVisible(False)
        legacy_inner = QVBoxLayout(self.legacy_module_container)
        legacy_inner.setContentsMargins(0, 0, 0, 0)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        module_group = QGroupBox("Module Selection & Compilation")
        module_layout = QVBoxLayout(module_group)
        button_layout = QHBoxLayout()

        self.btn_add_modules = QPushButton("Add Python Files")
        self.btn_add_modules.clicked.connect(self.add_modules)
        button_layout.addWidget(self.btn_add_modules)

        self.btn_add_compiled = QPushButton("Add Compiled Modules")
        self.btn_add_compiled.clicked.connect(self.add_compiled_modules)
        button_layout.addWidget(self.btn_add_compiled)

        self.btn_compile_selected = QPushButton("Compile Selected")
        self.btn_compile_selected.clicked.connect(self.compile_selected_modules)
        button_layout.addWidget(self.btn_compile_selected)

        self.btn_clear_modules = QPushButton("Clear All")
        self.btn_clear_modules.clicked.connect(self.clear_modules)
        button_layout.addWidget(self.btn_clear_modules)

        module_layout.addLayout(button_layout)

        self.module_list = QListWidget()
        self.module_list.itemSelectionChanged.connect(self.update_version_display)
        module_layout.addWidget(self.module_list)

        self.compilation_progress = QProgressBar()
        self.compilation_progress.setVisible(False)
        module_layout.addWidget(self.compilation_progress)
        left_layout.addWidget(module_group)

        info_group = QGroupBox("Module Information")
        info_layout = QGridLayout(info_group)
        info_layout.addWidget(QLabel("Versioning:"), 0, 0)
        self.version_display = QLabel("Auto-generated when compiling")
        info_layout.addWidget(self.version_display, 0, 1)
        info_layout.addWidget(QLabel("Description:"), 1, 0)
        self.description_input = QLineEdit()
        info_layout.addWidget(self.description_input, 1, 1)
        left_layout.addWidget(info_group)

        deploy_group = QGroupBox("Deployment Settings")
        deploy_layout = QVBoxLayout(deploy_group)
        self.force_update_checkbox = QCheckBox("Force update (ignore version checks)")
        deploy_layout.addWidget(self.force_update_checkbox)
        self.backup_modules_checkbox = QCheckBox("Create backups before deployment")
        self.backup_modules_checkbox.setChecked(True)
        deploy_layout.addWidget(self.backup_modules_checkbox)
        self.btn_deploy = QPushButton("Deploy Modules (legacy CMD_SRV_MOD)")
        self.btn_deploy.clicked.connect(self.deploy_modules)
        deploy_layout.addWidget(self.btn_deploy)
        self.deployment_progress = QProgressBar()
        self.deployment_progress.setVisible(False)
        deploy_layout.addWidget(self.deployment_progress)
        left_layout.addWidget(deploy_group)
        left_layout.addStretch()

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.action_sequence_editor = ActionSequenceEditor()
        right_layout.addWidget(self.action_sequence_editor)

        main_splitter.addWidget(left_widget)
        main_splitter.addWidget(right_widget)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([500, 500])
        legacy_inner.addWidget(main_splitter)
        legacy_layout.addWidget(self.legacy_module_container)
        layout.addWidget(legacy_group)

        self._selected_release_id = None
        self._release_seed_worker = None
        self.refresh_releases_list()
        return widget

    def refresh_releases_list(self):
        if not hasattr(self, 'releases_table'):
            return
        from Support.ModuleSecurity import list_local_releases

        releases = list_local_releases()
        fingerprint = []
        for sc in releases:
            seed_status = sc.get('seed_status') or {}
            fingerprint.append((
                sc.get('release_id', ''),
                sc.get('version', ''),
                tuple(sorted(
                    (k, e.get('status'), e.get('message'), e.get('timestamp'))
                    for k, e in seed_status.items()
                ))
            ))
        if getattr(self, '_releases_fingerprint', None) == fingerprint:
            return
        self._releases_fingerprint = fingerprint

        selected_id = self._selected_release_id
        self.releases_table.setRowCount(0)
        restore_row = -1
        for sc in releases:
            release_id = sc.get('release_id', '')
            seed_status = sc.get('seed_status') or {}
            ack_n = sum(1 for e in seed_status.values() if e.get('status') == 'ack')
            nak_n = sum(1 for e in seed_status.values() if e.get('status') == 'nak')
            pending_n = sum(1 for e in seed_status.values() if e.get('status') == 'pending')
            if not seed_status:
                status_text = "Built (not seeded)"
            elif pending_n > 0:
                status_text = "Seeding"
            elif nak_n > 0 and ack_n == 0:
                status_text = "Failed"
            elif nak_n > 0:
                status_text = "Partial"
            else:
                status_text = "Seeded"
            row = self.releases_table.rowCount()
            self.releases_table.insertRow(row)
            self.releases_table.setItem(row, 0, QTableWidgetItem(release_id))
            self.releases_table.setItem(row, 1, QTableWidgetItem(str(sc.get('version', ''))))
            self.releases_table.setItem(row, 2, QTableWidgetItem(str(sc.get('created_utc', ''))))
            self.releases_table.setItem(row, 3, QTableWidgetItem(status_text))
            self.releases_table.setItem(row, 4, QTableWidgetItem(str(ack_n)))
            self.releases_table.setItem(row, 5, QTableWidgetItem(str(nak_n)))
            self.releases_table.setItem(row, 6, QTableWidgetItem(str(pending_n)))
            self.releases_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, release_id)
            if selected_id and release_id == selected_id:
                restore_row = row
        self.releases_table.resizeColumnsToContents()
        if restore_row >= 0:
            self.releases_table.selectRow(restore_row)
        elif self.releases_table.rowCount() > 0 and not selected_id:
            self.releases_table.selectRow(0)
        self.on_release_selection_changed()

    def on_release_selection_changed(self):
        if not hasattr(self, 'release_ack_list'):
            return
        from Support.ModuleSecurity import get_release_seed_peers

        self.release_ack_list.clear()
        self.release_nak_list.clear()
        self.release_pending_list.clear()
        rows = self.releases_table.selectionModel().selectedRows() if self.releases_table.selectionModel() else []
        if not rows:
            self._selected_release_id = None
            return
        item = self.releases_table.item(rows[0].row(), 0)
        release_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        self._selected_release_id = release_id
        if not release_id:
            return

        for entry in get_release_seed_peers(release_id, 'ack'):
            msg = entry.get('message') or ''
            label = f"{entry.get('name', '?')} ({entry.get('ip', '')})"
            if msg:
                label += f" - {msg}"
            self.release_ack_list.addItem(label)

        for entry in get_release_seed_peers(release_id, 'nak'):
            msg = entry.get('message') or ''
            label = f"{entry.get('name', '?')} ({entry.get('ip', '')})"
            if msg:
                label += f" - {msg}"
            self.release_nak_list.addItem(label)

        for entry in get_release_seed_peers(release_id, 'pending'):
            label = f"{entry.get('name', '?')} ({entry.get('ip', '')})"
            self.release_pending_list.addItem(label)

    def _supernode_seed_targets(self):
        """Supernodes as dicts for ReleaseWorker (APP/DB/SUPER_DB)."""
        rows = query_database(
            init.pool_main_db,
            "SELECT id, name, ip, ip_local, type, online FROM Peers "
            "WHERE type = ? OR type = ? OR type = ?",
            (init.NODE_TYPE_APP_SERVER, init.NODE_TYPE_DB_SERVER, init.NODE_TYPE_SUPER_DB_SERVER)
        )
        peers = []
        for row in rows or []:
            if isinstance(row, dict):
                p = row
            else:
                p = {
                    'id': row[0] if len(row) > 0 else '',
                    'name': row[1] if len(row) > 1 else '',
                    'ip': row[2] if len(row) > 2 else '',
                    'ip_local': row[3] if len(row) > 3 else '',
                    'type': row[4] if len(row) > 4 else '',
                    'online': row[5] if len(row) > 5 else False,
                }
            if p.get('ip') and p.get('ip_local'):
                peers.append(p)
        return peers

    def _start_release_seed(self, release_id, targets, title="Seed"):
        from Support.ModuleSecurity import load_release_sidecar, init_release_seed_status
        from ADMIN.ReleaseWizard import ReleaseWorker

        if getattr(self, '_release_seed_worker', None) and self._release_seed_worker.isRunning():
            QMessageBox.warning(self, title, "A seed is already in progress.")
            return False

        sidecar = load_release_sidecar(release_id)
        nrel_name = sidecar.get('enc_file') or (release_id + '.nrel')
        nrel_path = os.path.join(init.PATH_DISTRIBUTABLE_RELEASES, nrel_name)
        if not os.path.isfile(nrel_path):
            QMessageBox.warning(self, title, f"Package not found: {nrel_path}")
            return False
        if not targets:
            QMessageBox.warning(self, title, "No target peers.")
            return False

        try:
            with open(nrel_path, 'rb') as f:
                enc_bytes = f.read()
        except Exception as e:
            QMessageBox.critical(self, title, f"Failed to read package: {e}")
            return False

        init_release_seed_status(release_id, targets)
        self._releases_fingerprint = None
        self.refresh_releases_list()

        self._release_seed_worker = ReleaseWorker(
            enc_bytes,
            release_id,
            sidecar.get('version', ''),
            self.ME.get('ip', ''),
            sidecar.get('created_utc', ''),
            targets
        )
        self._release_seed_worker.progress.connect(self.log)
        self._release_seed_worker.finished.connect(self._on_release_resend_finished)
        self._release_seed_worker.start()
        self.log(f"{title}: {release_id} -> {len(targets)} peer(s)")
        return True

    def seed_selected_release(self):
        release_id = self._selected_release_id
        if not release_id:
            QMessageBox.warning(self, "Seed", "Select a release first.")
            return
        targets = self._supernode_seed_targets()
        if not targets:
            QMessageBox.warning(
                self,
                "Seed",
                "No supernodes found (APP_SERVER / DB_SERVER / SUPER_DB_SERVER)."
            )
            return
        names = ", ".join(p.get('name', '?') for p in targets)
        reply = QMessageBox.question(
            self,
            "Seed Release",
            f"Seed {release_id} to {len(targets)} supernode(s)?\n{names}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._start_release_seed(release_id, targets, title="Seed")

    def delete_selected_release(self):
        from Support.ModuleSecurity import delete_local_release

        release_id = self._selected_release_id
        if not release_id:
            QMessageBox.warning(self, "Delete", "Select a release first.")
            return
        reply = QMessageBox.question(
            self,
            "Delete Release",
            f"Delete local package {release_id}?\n(.nrel / .zip / .json)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, message = delete_local_release(release_id)
        self.log(message)
        self._selected_release_id = None
        self._releases_fingerprint = None
        self.refresh_releases_list()
        if ok:
            QMessageBox.information(self, "Delete", message)
        else:
            QMessageBox.warning(self, "Delete", message)

    def resend_release_to_naks(self):
        from Support.ModuleSecurity import get_release_seed_peers

        release_id = self._selected_release_id
        if not release_id:
            QMessageBox.warning(self, "Resend", "Select a release first.")
            return

        nak_peers = get_release_seed_peers(release_id, 'nak')
        if not nak_peers:
            QMessageBox.information(self, "Resend", "No NAK'd peers for this release.")
            return

        targets = []
        for entry in nak_peers:
            targets.append({
                'id': entry.get('peer_id', ''),
                'name': entry.get('name', ''),
                'ip': entry.get('ip', ''),
                'ip_local': entry.get('ip_local', '')
            })

        reply = QMessageBox.question(
            self,
            "Resend Release",
            f"Resend release {release_id} to {len(targets)} NAK'd peer(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._start_release_seed(release_id, targets, title="Resend")

    def _on_release_resend_finished(self, ok, message, failed):
        self.log(message)
        if failed:
            self.log("Failed: " + ", ".join(failed))
        self._releases_fingerprint = None
        self.refresh_releases_list()
        if ok:
            QMessageBox.information(self, "Seed", message)
        else:
            QMessageBox.warning(self, "Seed incomplete", message)

    def open_release_wizard(self):
        from ADMIN.ReleaseWizard import ReleaseWizard
        editor = ActionSequenceEditor()
        wizard = ReleaseWizard(self, action_editor=editor, parent=self)
        wizard.exec()
        self.refresh_releases_list()

    def toggle_legacy_module_ui(self, checked):
        self.legacy_module_container.setVisible(checked)

    def get_supernodes(self):
        rows = query_database(
            init.pool_main_db,
            "SELECT * FROM Peers WHERE type = ? OR type = ? OR type = ?",
            (init.NODE_TYPE_APP_SERVER, init.NODE_TYPE_DB_SERVER, init.NODE_TYPE_SUPER_DB_SERVER)
        )
        return rows or []

    def create_file_deployment_tab(self):
        """Create file deployment tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        file_group = QGroupBox("📁 File Deployment")
        file_layout = QVBoxLayout(file_group)

        file_buttons = QHBoxLayout()

        self.btn_select_files = QPushButton("📁 Select Files")
        self.btn_select_files.clicked.connect(self.select_files)
        file_buttons.addWidget(self.btn_select_files)

        self.btn_reset_files = QPushButton("🗑️ Reset Files")
        self.btn_reset_files.clicked.connect(self.reset_files)
        file_buttons.addWidget(self.btn_reset_files)

        self.btn_deploy_files = QPushButton("🚀 Deploy Files")
        self.btn_deploy_files.clicked.connect(self.deploy_files)
        file_buttons.addWidget(self.btn_deploy_files)

        file_layout.addLayout(file_buttons)

        self.file_table = QTableWidget()
        self.file_table.setColumnCount(6)
        self.file_table.setHorizontalHeaderLabels(["X", "File Name", "Path", "Copy to path", "COPY TO", "REMOVE FROM"])
        self.file_table.horizontalHeader().setStretchLastSection(False)
        file_layout.addWidget(self.file_table)

        layout.addWidget(file_group)

        self.file_deployment_names = []
        self.file_deployment_data = []
        self.file_button_states = {}

        return widget

    def create_app_deployment_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        app_group = QGroupBox("🚀 App Deployment")
        app_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        group_layout = QVBoxLayout(app_group)
        group_layout.setSpacing(12)

        select_layout = QHBoxLayout()
        select_layout.setSpacing(8)
        self.app_deployment_path_input = QLineEdit()
        self.app_deployment_path_input.setReadOnly(True)
        self.app_deployment_path_input.setPlaceholderText("No folder selected")
        self.app_deployment_path_input.setMaximumWidth(420)
        select_layout.addWidget(self.app_deployment_path_input)

        self.btn_select_app_zip = QPushButton("📁 Select App Folder")
        self.btn_select_app_zip.setFixedSize(160, 28)
        self.btn_select_app_zip.clicked.connect(self.select_app_zip)
        select_layout.addWidget(self.btn_select_app_zip)
        select_layout.addStretch()

        group_layout.addLayout(select_layout)

        form_layout = QFormLayout()
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form_layout.setHorizontalSpacing(12)
        form_layout.setVerticalSpacing(6)

        self.app_name_input = QLineEdit()
        self.app_name_input.setMaximumWidth(360)
        form_layout.addRow("Name:", self.app_name_input)

        self.app_subtitle_input = QLineEdit()
        self.app_subtitle_input.setMaximumWidth(360)
        form_layout.addRow("Subtitle:", self.app_subtitle_input)

        self.app_description_input = QLineEdit()
        self.app_description_input.setMaximumWidth(360)
        self.app_description_input.textChanged.connect(lambda: self.enforce_text_limit(self.app_description_input, 150))
        form_layout.addRow("Description:", self.app_description_input)

        self.app_extended_description_input = QTextEdit()
        self.app_extended_description_input.setMaximumWidth(500)
        self.app_extended_description_input.setFixedHeight(110)
        self.app_extended_description_input.textChanged.connect(lambda: self.enforce_text_limit(self.app_extended_description_input, 2000))
        form_layout.addRow("Extended Description:", self.app_extended_description_input)

        self.app_price_input = QDoubleSpinBox()
        self.app_price_input.setDecimals(7)
        self.app_price_input.setMaximum(99999.9999999)
        self.app_price_input.setSingleStep(0.01)
        self.app_price_input.setMaximumWidth(160)
        form_layout.addRow("Price:", self.app_price_input)

        self.app_developer_input = QLineEdit()
        self.app_developer_input.setMaximumWidth(360)
        form_layout.addRow("Developer:", self.app_developer_input)

        date_layout = QHBoxLayout()
        date_layout.setSpacing(10)
        self.app_date_button = QPushButton("Pick Date")
        self.app_date_button.setFixedSize(110, 28)
        self.app_date_button.clicked.connect(self.open_date_selector)
        date_layout.addWidget(self.app_date_button)

        self.app_date_display = QLabel("No date selected")
        self.app_date_display.setStyleSheet("color: #a1b0c8;")
        date_layout.addWidget(self.app_date_display)

        date_container = QWidget()
        date_container.setLayout(date_layout)
        form_layout.addRow("Date Available:", date_container)

        self.app_version_input = QLineEdit()
        self.app_version_input.setMaximumWidth(200)
        form_layout.addRow("Version:", self.app_version_input)

        self.app_main_input = QLineEdit()
        self.app_main_input.setMaximumWidth(360)
        form_layout.addRow("Main:", self.app_main_input)

        self.app_category_combo = QComboBox()
        self.app_category_combo.addItems(["Games", "Productivity", "Entertainment", "Education", "Social", "Tools"])
        self.app_category_combo.setMaximumWidth(220)
        form_layout.addRow("Category:", self.app_category_combo)

        form_container = QWidget()
        form_container.setLayout(form_layout)
        form_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        image_container = QWidget()
        image_layout = QVBoxLayout(image_container)
        image_layout.setSpacing(10)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_container.setMaximumWidth(260)
        image_container.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        image_keys = [
            ("logo", "Logo"),
            ("image1", "Image 1"),
            ("image2", "Image 2"),
            ("image3", "Image 3"),
            ("image4", "Image 4"),
            ("image5", "Image 5")
        ]

        for key, label_text in image_keys:
            row_layout = QHBoxLayout()
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            select_btn = QPushButton(f"Select {label_text}")
            select_btn.setFixedSize(110, 28)
            select_btn.clicked.connect(partial(self.select_app_image, key))
            row_layout.addWidget(select_btn)

            preview_label = QLabel("No image selected")
            preview_label.setFixedSize(150, 100)
            preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview_label.setStyleSheet("border: 1px dashed #888888;")
            row_layout.addWidget(preview_label)
            row_layout.addStretch()

            image_layout.addLayout(row_layout)

            self.app_image_labels[key] = preview_label
            self.app_image_paths[key] = ""

        image_layout.addStretch()

        content_layout = QHBoxLayout()
        content_layout.setSpacing(24)
        content_layout.addWidget(form_container)
        content_layout.addStretch()
        content_layout.addWidget(image_container)

        group_layout.addLayout(content_layout)

        self.app_form_status_label = QLabel("Complete the required fields to enable deployment")
        self.app_form_status_label.setStyleSheet("color: #f0ad4e; font-size: 12px;")
        group_layout.addWidget(self.app_form_status_label)

        self.btn_deploy_app = QPushButton("🚀 DEPLOY TO APP SERVERS")
        self.btn_deploy_app.clicked.connect(self.deploy_app_to_servers)
        self.btn_deploy_app.setFixedHeight(36)
        self.btn_deploy_app.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_deploy_app.setStyleSheet("""QPushButton { background-color: #4a90e2; color: white; font-weight: bold; padding: 10px; font-size: 14px; }
        QPushButton:disabled { background-color: #5a6b84; color: #d4d8e2; border: 1px solid #4d5c70; }""")
        self.btn_deploy_app.setEnabled(False)
        group_layout.addWidget(self.btn_deploy_app)

        group_layout.addStretch()

        layout.addWidget(app_group)
        layout.addStretch()

        self.update_app_form_state()
        return widget

    def select_app_zip(self):
        folder_path = QFileDialog.getExistingDirectory(self, "Select App Folder", "")
        if folder_path:
            self.app_deployment_folder_path = folder_path
            self.app_deployment_path_input.setText(folder_path)

    def deploy_app_to_servers(self):
        app_id = generate_random_hash()

        form_field_list = [
            app_id,
            self.app_name_input.text(),
            self.app_subtitle_input.text(),
            self.app_description_input.text(),
            self.app_extended_description_input.toPlainText(),
            self.app_price_input.value(),
            self.app_developer_input.text(),
            self.app_selected_date,
            self.app_version_input.text(),
            self.app_main_input.text(),
            self.app_category_combo.currentText()
        ]

        raw_data_list = []
        zip_data = b""
        if self.app_deployment_folder_path and os.path.isdir(self.app_deployment_folder_path):
            # Fix: Point the base path to the DISTRIBUTABLE folder
            zip_base = os.path.join(init.PATH_DISTRIBUTABLE_APPS, app_id)

            # Ensure the directory exists
            os.makedirs(init.PATH_DISTRIBUTABLE_APPS, exist_ok=True)

            zip_path = f"{zip_base}.zip"

            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)

                # This will now create the zip directly in PATH_DISTRIBUTABLE_APPS
                shutil.make_archive(str(zip_base), 'zip', root_dir=self.app_deployment_folder_path)

                with open(zip_path, 'rb') as zip_file:
                    zip_data = zip_file.read()
            except Exception as e:
                # It's usually good practice to at least log the error
                print(f"Zipping failed: {e}")
                zip_data = b""

        raw_data_list.append(zip_data)

        image_keys = ["logo", "image1", "image2", "image3", "image4", "image5"]
        for key in image_keys:
            image_path = self.app_image_paths.get(key, "")
            image_data = b""
            if image_path and os.path.exists(image_path):
                try:
                    with open(image_path, 'rb') as img_file:
                        image_data = img_file.read()
                except:
                    image_data = b""
            raw_data_list.append(image_data)

        app_servers = query_database(init.pool_main_db, "SELECT * FROM Peers WHERE type = ? OR type = ? OR type = ?",
      (init.NODE_TYPE_APP_SERVER,init.NODE_TYPE_DB_SERVER, init.NODE_TYPE_SUPER_DB_SERVER))
        for peer in app_servers:
            send_packet(self.ME['ip'], self.ME['ip_local'], '',
                self.ip_to_send_packet_to(peer[5], peer[6]), peer[6], '',
                init.CMD_SRV_APP, init.PRT_NOR, '', 0, 0, "",
                form_field_list, [], raw_data_list,
                '', '', [], [], [],
                retry_policy='critical')
        self.reset_app_form()

    def handle_text_input_with_limit(self, widget, max_length):
        self.enforce_text_limit(widget, max_length)
        self.update_app_form_state()

    def update_app_form_state(self):
        if not hasattr(self, "btn_deploy_app"):
            return
        missing = []
        if not self.app_deployment_folder_path.strip():
            missing.append("App folder")
        if not self.app_name_input.text().strip():
            missing.append("Name")
        if not self.app_subtitle_input.text().strip():
            missing.append("Subtitle")
        if not self.app_description_input.text().strip():
            missing.append("Description")
        if not self.app_extended_description_input.toPlainText().strip():
            missing.append("Extended description")
        if not self.app_developer_input.text().strip():
            missing.append("Developer")
        if not self.app_selected_date.strip():
            missing.append("Date available")
        if not self.app_version_input.text().strip():
            missing.append("Version")
        if not self.app_main_input.text().strip():
            missing.append("Main")
        if not self.app_category_combo.currentText().strip():
            missing.append("Category")

        for key in REQUIRED_IMAGE_KEYS:
            if not self.app_image_paths.get(key, "").strip():
                label = key.replace("image", "Image ").capitalize()
                missing.append(label)

        ready = len(missing) == 0
        self.btn_deploy_app.setEnabled(ready)

        if hasattr(self, "app_form_status_label"):
            if ready:
                self.app_form_status_label.setText("Ready to deploy")
                self.app_form_status_label.setStyleSheet("color: #7ddc7c; font-size: 12px;")
            else:
                detail = ", ".join(sorted(set(missing)))
                self.app_form_status_label.setText(f"Missing: {detail}")
                self.app_form_status_label.setStyleSheet("color: #f0ad4e; font-size: 12px;")

    def reset_app_form(self):
        self.app_deployment_folder_path = ""
        self.app_deployment_path_input.clear()
        self.app_name_input.clear()
        self.app_subtitle_input.clear()
        self.app_description_input.clear()
        self.app_extended_description_input.clear()
        self.app_price_input.setValue(0)
        self.app_developer_input.clear()
        self.app_selected_date = ""
        self.app_date_display.setText("No date selected")
        self.app_version_input.clear()
        self.app_main_input.clear()
        self.app_category_combo.setCurrentIndex(0)
        for key in APP_IMAGE_KEYS:
            self.app_image_paths[key] = ""
            label = self.app_image_labels.get(key)
            if label:
                label.setPixmap(QPixmap())
                label.setText("No image selected")
        self.update_app_form_state()

    def open_date_selector(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Select Available Date")
        dialog.setModal(True)
        calendar = QCalendarWidget()
        calendar.setGridVisible(True)
        calendar.clicked.connect(lambda date: self.set_app_date(date, dialog))
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.addWidget(calendar)
        dialog.exec()

    def set_app_date(self, qdate, dialog):
        self.app_selected_date = qdate.toString("yyyy-MM-dd")
        self.app_date_display.setText(self.app_selected_date)
        self.update_app_form_state()
        dialog.accept()

    def select_app_image(self, key):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Image", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif)")
        if file_path:
            self.app_image_paths[key] = file_path
            self.update_app_image_preview(key)
            self.update_app_form_state()

    def update_app_image_preview(self, key):
        label = self.app_image_labels.get(key)
        image_path = self.app_image_paths.get(key, "")
        if not label:
            return
        if image_path and os.path.exists(image_path):
            pixmap = QPixmap(image_path)
            pixmap = pixmap.scaled(140, 90, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            label.setPixmap(pixmap)
            label.setText("")
        else:
            label.setPixmap(QPixmap())
            label.setText("No image selected")

    def enforce_text_limit(self, widget, max_length):
        if isinstance(widget, QLineEdit):
            text = widget.text()
            if len(text) > max_length:
                widget.blockSignals(True)
                widget.setText(text[:max_length])
                widget.blockSignals(False)
        elif isinstance(widget, QTextEdit):
            text = widget.toPlainText()
            if len(text) > max_length:
                widget.blockSignals(True)
                cursor = widget.textCursor()
                widget.setPlainText(text[:max_length])
                cursor.movePosition(QTextCursor.MoveOperation.End)
                widget.setTextCursor(cursor)
                widget.blockSignals(False)

    def create_banner_tab(self):
        """Create banner/ads management tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Advanced peer selection button at the top
        btn_advanced_selection = QPushButton("🌐 Advanced Peer Selection")
        btn_advanced_selection.clicked.connect(self.show_peer_selection_dialog)
        btn_advanced_selection.setStyleSheet("QPushButton { background-color: #4a90e2; color: white; font-weight: bold; padding: 8px; }")
        layout.addWidget(btn_advanced_selection)

        banner_group = QGroupBox("🎨 Banner Management")
        banner_layout = QVBoxLayout(banner_group)

        # Banner selection
        banner_controls = QHBoxLayout()

        self.btn_select_banner = QPushButton("🖼️ Select Banner")
        self.btn_select_banner.clicked.connect(self.select_banner)
        banner_controls.addWidget(self.btn_select_banner)

        self.btn_send_banner = QPushButton("📤 Send Banner")
        self.btn_send_banner.clicked.connect(self.send_banner)
        banner_controls.addWidget(self.btn_send_banner)

        banner_layout.addLayout(banner_controls)

        # Banner preview
        self.banner_preview = QLabel("No banner selected")
        self.banner_preview.setMinimumHeight(200)
        self.banner_preview.setStyleSheet("border: 2px dashed #cccccc; text-align: center;")
        self.banner_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        banner_layout.addWidget(self.banner_preview)

        # Banner settings
        settings_layout = QGridLayout()

        settings_layout.addWidget(QLabel("Link URL:"), 0, 0)
        self.banner_link_input = QLineEdit()
        settings_layout.addWidget(self.banner_link_input, 0, 1)

        settings_layout.addWidget(QLabel("Impressions:"), 1, 0)
        self.banner_impressions_input = QSpinBox()
        self.banner_impressions_input.setMaximum(999999)
        self.banner_impressions_input.setValue(1000)
        self.banner_impressions_input.setStyleSheet(main_dialog_style)
        settings_layout.addWidget(self.banner_impressions_input, 1, 1)

        settings_layout.addWidget(QLabel("Neurons:"), 2, 0)
        self.banner_neurons_input = QDoubleSpinBox()
        self.banner_neurons_input.setMaximum(9999999.99999)
        self.banner_neurons_input.setDecimals(5)
        self.banner_neurons_input.setValue(0.00000)
        self.banner_neurons_input.setStyleSheet(main_dialog_style)
        settings_layout.addWidget(self.banner_neurons_input, 2, 1)

        banner_layout.addLayout(settings_layout)

        layout.addWidget(banner_group)

        return widget

    def create_enhanced_logs_tab(self):
        """Create enhanced logs tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Advanced peer selection button at the top
        btn_advanced_selection = QPushButton("🌐 Advanced Peer Selection")
        btn_advanced_selection.clicked.connect(self.show_peer_selection_dialog)
        btn_advanced_selection.setStyleSheet("QPushButton { background-color: #4a90e2; color: white; font-weight: bold; padding: 8px; }")
        layout.addWidget(btn_advanced_selection)

        # Log controls
        log_controls = QHBoxLayout()

        self.btn_clear_logs = QPushButton("🗑️ Clear Logs")
        self.btn_clear_logs.clicked.connect(self.clear_logs)
        log_controls.addWidget(self.btn_clear_logs)

        self.btn_save_logs = QPushButton("💾 Save Logs")
        self.btn_save_logs.clicked.connect(self.save_logs)
        log_controls.addWidget(self.btn_save_logs)

        self.btn_auto_scroll = QPushButton("📜 Auto Scroll")
        self.btn_auto_scroll.setCheckable(True)
        self.btn_auto_scroll.setChecked(True)
        log_controls.addWidget(self.btn_auto_scroll)

        log_controls.addStretch()
        layout.addLayout(log_controls)

        # Log display
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log_display)

        return widget

    def create_retrieve_logs_tab(self):
        """Create tab to retrieve and view log files from a selected peer"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        peer_group = QGroupBox("Peer")
        peer_layout = QHBoxLayout(peer_group)

        peer_layout.addWidget(QLabel("Select peer:"))
        self.retrieve_logs_peer_combo = QComboBox()
        self.retrieve_logs_peer_combo.currentIndexChanged.connect(self.on_retrieve_logs_peer_changed)
        peer_layout.addWidget(self.retrieve_logs_peer_combo, 1)

        self.btn_retrieve_logs = QPushButton("Retrieve logs")
        self.btn_retrieve_logs.clicked.connect(self.send_retrieve_logs_request)
        peer_layout.addWidget(self.btn_retrieve_logs)

        layout.addWidget(peer_group)

        files_group = QGroupBox("Log files")
        files_layout = QVBoxLayout(files_group)
        self.retrieve_logs_status = QLabel("Select a peer and click Retrieve logs.")
        files_layout.addWidget(self.retrieve_logs_status)

        self.retrieve_logs_buttons_widget = QWidget()
        self.retrieve_logs_buttons_layout = QHBoxLayout(self.retrieve_logs_buttons_widget)
        self.retrieve_logs_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.retrieve_logs_buttons_layout.addStretch()
        files_layout.addWidget(self.retrieve_logs_buttons_widget)
        layout.addWidget(files_group)

        content_group = QGroupBox("Log content")
        content_layout = QVBoxLayout(content_group)
        self.retrieve_logs_content_scroll = QScrollArea()
        self.retrieve_logs_content_scroll.setWidgetResizable(True)
        self.retrieve_logs_content_label = QLabel()
        self.retrieve_logs_content_label.setWordWrap(True)
        self.retrieve_logs_content_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.retrieve_logs_content_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.retrieve_logs_content_label.setFont(QFont("Consolas", 9))
        self.retrieve_logs_content_label.setStyleSheet("QLabel { padding: 8px; }")
        self.retrieve_logs_content_scroll.setWidget(self.retrieve_logs_content_label)
        content_layout.addWidget(self.retrieve_logs_content_scroll)
        layout.addWidget(content_group, 1)

        self.populate_retrieve_logs_peer_combo()
        return widget

    def populate_retrieve_logs_peer_combo(self):
        if not hasattr(self, 'retrieve_logs_peer_combo'):
            return
        self.retrieve_logs_peer_combo.blockSignals(True)
        self.retrieve_logs_peer_combo.clear()
        self.retrieve_logs_peer_combo.addItem("Select peer...", None)
        for peer in self.available_peers:
            label = f"{peer.get('name', '')} ({peer.get('ip', '')}:{peer.get('ip_local', '')})"
            self.retrieve_logs_peer_combo.addItem(label, peer)
        self.retrieve_logs_peer_combo.blockSignals(False)
        self.retrieve_logs_selected_peer = None

    def on_retrieve_logs_peer_changed(self, index):
        self.retrieve_logs_selected_peer = self.retrieve_logs_peer_combo.itemData(index)

    def send_retrieve_logs_request(self):
        peer = self.retrieve_logs_selected_peer
        if not peer:
            QMessageBox.warning(self, "No Peer", "Select a peer first.")
            return

        try:
            ready_path = os.path.join(self.retrieved_logs_temp_dir, ".ready")
            if os.path.exists(ready_path):
                os.remove(ready_path)

            self.clear_retrieved_log_buttons()
            self.retrieve_logs_content_label.setText("")
            self.retrieve_logs_status.setText(
                f"Requesting logs from {peer.get('name', '')}...")
            self._last_retrieved_logs_mtime = None

            target_ip = self.ip_to_send_packet_to(peer['ip'], peer['ip_local'])
            self.log_packet(init.CMD_SYS_LOG, target_ip, peer['ip_local'])
            send_packet(
                self.ME['ip'], self.ME['ip_local'], '',
                target_ip, peer['ip_local'], '',
                init.CMD_SYS_LOG, init.PRT_NOR, '', 0, 0, '',
                [], [], [],
                '', '', [], [], [])
            self.log(f"Sent CMD_SYS_LOG to {peer.get('name', '')}")
        except Exception as e:
            self.log(f"Error requesting logs: {e}")
            QMessageBox.critical(self, "Error", f"Failed to request logs: {e}")

    def check_for_retrieved_logs(self):
        if not hasattr(self, 'retrieve_logs_buttons_layout'):
            return

        ready_path = os.path.join(self.retrieved_logs_temp_dir, ".ready")
        if not os.path.exists(ready_path):
            return

        try:
            mtime = os.path.getmtime(ready_path)
            if self._last_retrieved_logs_mtime == mtime:
                return
            self._last_retrieved_logs_mtime = mtime
            self.populate_retrieved_log_buttons(self.retrieved_logs_temp_dir)
        except Exception as e:
            self.log(f"Error checking retrieved logs: {e}")

    def clear_retrieved_log_buttons(self):
        while self.retrieve_logs_buttons_layout.count():
            item = self.retrieve_logs_buttons_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.retrieve_logs_buttons_layout.addStretch()

    def populate_retrieved_log_buttons(self, temp_dir):
        self.clear_retrieved_log_buttons()
        self.retrieve_logs_content_label.setText("")

        log_files = []
        for name in sorted(os.listdir(temp_dir)):
            if name.startswith('.'):
                continue
            full_path = os.path.join(temp_dir, name)
            if os.path.isfile(full_path):
                log_files.append(full_path)

        if not log_files:
            self.retrieve_logs_status.setText("No log files received.")
            return

        self.retrieve_logs_status.setText(f"Received {len(log_files)} log file(s). Click a file to view.")
        for full_path in log_files:
            btn = QPushButton(os.path.basename(full_path))
            btn.clicked.connect(partial(self.display_retrieved_log_file, full_path))
            self.retrieve_logs_buttons_layout.insertWidget(
                self.retrieve_logs_buttons_layout.count() - 1, btn)

    def display_retrieved_log_file(self, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            self.retrieve_logs_content_label.setText(content if content else "(empty file)")
            self.retrieve_logs_status.setText(f"Viewing: {os.path.basename(file_path)}")
        except Exception as e:
            self.retrieve_logs_content_label.setText("")
            self.log(f"Error reading log file: {e}")
            QMessageBox.critical(self, "Error", f"Failed to read log file: {e}")

    def cleanup_retrieved_logs_temp(self):
        try:
            if os.path.exists(self.retrieved_logs_temp_dir):
                shutil.rmtree(self.retrieved_logs_temp_dir, ignore_errors=True)
        except Exception:
            pass

    def closeEvent(self, event):
        self.cleanup_retrieved_logs_temp()
        super().closeEvent(event)



    # ==================== ADMIN FUNCTIONALITY METHODS ====================

    def ip_to_send_packet_to(self, ip, local_ip):
        """
        Determine which IP to send packet to - global or local.
        If I'm pinging a peer that is in my same local network, the principal IP address will be the same.
        In this case, I ping the local IP address.
        """
        if ip == self.ME.get('ip', ''):
            return local_ip
        else:
            return ip

    def log_packet(self, command, target_ip, target_local_ip, data1=None, data2=None, data3=None):
        """Log packet information for debugging"""
        cmd_names = {
            getattr(init, 'CMD_SRV_USR', 'CMD_SRV_USR'): 'Network Stats',
            getattr(init, 'CMD_SRV_QNU', 'CMD_SRV_QNU'): 'Query Neurons',
            getattr(init, 'CMD_SRV_GNE', 'CMD_SRV_GNE'): 'Give Neurons',
            getattr(init, 'CMD_SRV_SQL', 'CMD_SRV_SQL'): 'SQL Command',
            getattr(init, 'CMD_SRV_ENA', 'CMD_SRV_ENA'): 'Enable Peer',
            getattr(init, 'CMD_SRV_DIS', 'CMD_SRV_DIS'): 'Disable Peer',
            getattr(init, 'CMD_LOT_STR', 'CMD_LOT_STR'): 'Start Lottery',
            getattr(init, 'CMD_SRV_MOD', 'CMD_SRV_MOD'): 'Deploy Module',
            getattr(init, 'CMD_SRV_MDF', 'CMD_SRV_MDF'): 'Deploy Files',
            getattr(init, 'CMD_BAN_SND', 'CMD_BAN_SND'): 'Deploy Banner',
            getattr(init, 'CMD_SYS_LOG', 'CMD_SYS_LOG'): 'Retrieve Logs'
        }

        cmd_name = cmd_names.get(command, f'Command_{command}')
        data_info = []
        if data1: data_info.append(f"data1={len(data1) if isinstance(data1, list) else 'text'}")
        if data2: data_info.append(f"data2={len(data2) if isinstance(data2, list) else 'text'}")
        if data3: data_info.append(f"data3={len(data3) if isinstance(data3, list) else 'binary'}")

        data_str = f" [{', '.join(data_info)}]" if data_info else ""
        self.log(f"📤 PACKET: {cmd_name} → {target_ip}:{target_local_ip}{data_str}")

    def get_network_stats(self):
        """Get network statistics from peers"""
        try:
            self.reset_network_stats_fields()

            if self.all_peers_checkbox.isChecked():
                # Ask all peers for stats
                for peer in self.available_peers:
                    if peer:
                        target_ip = self.ip_to_send_packet_to(peer['ip'], peer['ip_local'])
                        self.log_packet(init.CMD_SRV_USR, target_ip, peer['ip_local'])
                        send_packet(
                            self.ME['ip'], self.ME['ip_local'], '',
                            target_ip, peer['ip_local'], '',
                            init.CMD_SRV_USR, init.PRT_NOR, '', 0, 0, "",
                            [], [], [],
                            '', '', [], [], [])
            else:
                # Ask specific peer
                target_ip = self.ip_to_send_packet_to(self.dst_ip_input.text(), self.dst_local_ip_input.text())
                self.log_packet(init.CMD_SRV_USR, target_ip, self.dst_local_ip_input.text())
                send_packet(
                    self.ME['ip'], self.ME['ip_local'], '',
                    target_ip, self.dst_local_ip_input.text(), '',
                    init.CMD_SRV_USR, init.PRT_NOR, '', 0, 0, "",
                    [], [], [],
                    '', '', [], [], [])

            self.log("📊 Network statistics request sent")
        except Exception as e:
            self.log(f"❌ Error getting network stats: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to get network stats: {str(e)}")

    def reset_network_stats_fields(self):
        """Reset all network statistics fields"""
        try:
            for field in self.stats_fields.values():
                field.setText('')
            if hasattr(self, 'country_table'):
                self.country_table.setRowCount(0)
            self.log("🔄 Network statistics fields reset")
        except Exception as e:
            self.log(f"❌ Error resetting fields: {str(e)}")

    def update_network_stats_display(self):
        """Update network stats display from Global table"""
        try:
            type_map = {
                0: 'incompatible',
                1: 'kickstarters',
                2: 'nodes',
                3: 'db_servers',
                4: 'brd_servers',
                5: 'super_db_servers',
                6: 'app_servers',
                7: 'sfu_servers'
            }
            
            global_data = query_database(init.pool_admin_db, "SELECT ip, ip_local, type, neurons, storage, country_code FROM Global", '')
            
            if not global_data:
                return
            
            aggregated_stats = {
                'total_users': 0,
                'nodes': 0,
                'brd_servers': 0,
                'db_servers': 0,
                'app_servers': 0,
                'sfu_servers': 0,
                'super_db_servers': 0,
                'kickstarters': 0,
                'administrators': 0,
                'incompatible': 0,
                'neurons': 0.0,
                'total_space': 0
            }
            
            type_stats = {}
            country_stats = {}
            
            for row in global_data:
                ip, ip_local, peer_type, neurons, storage, country_code = row
                type_name = type_map.get(peer_type, 'incompatible')
                
                if type_name not in type_stats:
                    type_stats[type_name] = {
                        'count': 0,
                        'total_neurons': 0.0,
                        'total_space': 0
                    }
                
                type_stats[type_name]['count'] += 1
                type_stats[type_name]['total_neurons'] += float(neurons) if neurons else 0.0
                type_stats[type_name]['total_space'] += int(storage) if storage else 0
                
                if country_code:
                    if country_code not in country_stats:
                        country_stats[country_code] = 0
                    country_stats[country_code] += 1
                
                if type_name in aggregated_stats:
                    aggregated_stats[type_name] += 1
                aggregated_stats['total_users'] += 1
                aggregated_stats['neurons'] += float(neurons) if neurons else 0.0
                aggregated_stats['total_space'] += int(storage) if storage else 0
            
            for field_name, field_widget in self.stats_fields.items():
                value = aggregated_stats.get(field_name, 0)
                field_widget.setText(str(int(value) if isinstance(value, float) else value))
            
            if hasattr(self, 'server_chart_canvas'):
                self.server_chart_canvas.set_data(aggregated_stats)
            
            if hasattr(self, 'node_type_table') and type_stats:
                self.update_node_type_table_from_global(type_stats, aggregated_stats['total_users'])
            
            if hasattr(self, 'country_table') and country_stats:
                self.update_country_table(country_stats)

            self.refresh_neuron_peer_results()
                
        except Exception as e:
            print(f"[UPDATE_NETWORK_STATS] Exception: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
    
    def update_node_type_table_from_global(self, type_stats, total_nodes):
        """Update node type statistics table from Global table data"""
        try:
            if not hasattr(self, 'node_type_table'):
                return
            
            self.node_type_table.setRowCount(len(type_stats))
            
            row = 0
            for type_name, stats in sorted(type_stats.items()):
                count = stats.get('count', 0)
                total_neurons = stats.get('total_neurons', 0)
                total_space = stats.get('total_space', 0)
                percentage = (count / total_nodes * 100) if total_nodes > 0 else 0
                
                type_item = QTableWidgetItem(type_name.replace('_', ' ').title())
                count_item = QTableWidgetItem(str(count))
                percentage_item = QTableWidgetItem(f"{percentage:.1f}%")
                neurons_item = QTableWidgetItem(f"{total_neurons:.2f}")
                space_item = QTableWidgetItem(f"{total_space:.0f}")
                
                self.node_type_table.setItem(row, 0, type_item)
                self.node_type_table.setItem(row, 1, count_item)
                self.node_type_table.setItem(row, 2, percentage_item)
                self.node_type_table.setItem(row, 3, neurons_item)
                self.node_type_table.setItem(row, 4, space_item)
                
                row += 1
        except Exception as e:
            print(f"[UPDATE_NODE_TYPE_TABLE] Exception: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
    
    def update_country_table(self, country_stats):
        """Update country distribution table"""
        try:
            if not hasattr(self, 'country_table'):
                return
            
            self.country_table.setRowCount(len(country_stats))
            
            row = 0
            for country_code, count in sorted(country_stats.items(), key=lambda x: x[1], reverse=True):
                country_item = QTableWidgetItem(country_code)
                count_item = QTableWidgetItem(str(count))
                
                self.country_table.setItem(row, 0, country_item)
                self.country_table.setItem(row, 1, count_item)
                
                row += 1
        except Exception as e:
            print(f"[UPDATE_COUNTRY_TABLE] Exception: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    def query_neurons(self):
        """Query neuron amounts from selected peers"""
        if not self.neuron_selected_peers:
            QMessageBox.warning(self, "No Peers Selected", "Please select peers first using Advanced Peer Selection.")
            return

        all_ips = []
        for peer in self.neuron_selected_peers:
            all_ips.append(peer['ip'] + ":" + peer['ip_local'])

        for peer in self.neuron_selected_peers:
            self.ask_how_much_they_have(peer['ip'], peer['ip_local'], all_ips)

        self.log(f"Neuron query sent to {len(self.neuron_selected_peers)} peers")
        self.refresh_neuron_peer_results()

    def ask_how_much_they_have(self, ip, ip_local, all_ips):
        """Ask peer how many Neurons they have"""
        send_packet(
            self.ME.get('ip', ''),
            self.ME.get('ip_local', ''), '',
            self.ip_to_send_packet_to(ip, ip_local), ip_local, '',
            init.CMD_SRV_QNU,
            init.PRT_NOR,
            '',
            0, 0, '',
            all_ips,
            [], [],
            '', '',
            [], [], [])

    def give_take_neurons(self):
        """Give or take neurons from selected peers"""
        if not self.neuron_selected_peers:
            QMessageBox.warning(self, "No Peers Selected", "Please select peers first using Advanced Peer Selection.")
            return

        if self.give_radio.isChecked():
            give = 1
            action = "give"
        else:
            give = -1
            action = "take"

        reply = QMessageBox.question(
            self,
            "Neuron Transaction",
            f"Are you sure you want to {action} {self.neuron_amount_input.text()} neurons to {len(self.neuron_selected_peers)} peer(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            for peer in self.neuron_selected_peers:
                self.send_neuron_transaction(peer['ip'], peer['ip_local'], give)

            self.log(f"⚡ Neuron transaction ({action}) executed for {len(self.neuron_selected_peers)} peer(s)")

    def send_neuron_transaction(self, ip, ip_local, give):
        """Send neuron transaction to specific peer"""
        try:
            amount = float(self.neuron_amount_input.text()) * give
            message = self.neuron_message_input.toPlainText()

            send_packet(
                self.ME.get('ip', ''), self.ME.get('ip_local', ''), '',
                self.ip_to_send_packet_to(ip, ip_local), ip_local, '',
                init.CMD_SRV_NEU, init.PRT_NOR, '', 0, 0, '',
                [message, str(amount)], [], [],
                '', '', [], [], [])
        except ValueError:
            QMessageBox.warning(self, "Invalid Amount", "Please enter a valid number for the neuron amount")

    def send_sql_command(self):
        """Send SQL command to peers"""
        sql_command = self.sql_input.toPlainText().strip()

        if not sql_command:
            QMessageBox.warning(self, "Empty Command", "Please enter an SQL command")
            return

        # Confirmation dialog
        reply = QMessageBox.question(
            self,
            "Execute SQL",
            f"Are you sure you want to execute this SQL command?\n\n{sql_command}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            if self.all_peers_checkbox.isChecked():
                # Send to all available peers
                for peer in self.available_peers:
                    self.send_sql_to_peer(peer['ip'], peer['ip_local'], sql_command)
                print(f"🗄️ SQL command executed: {sql_command[:50]}... to ALL peers ({len(self.available_peers)} peers)")
            elif self.sql_selected_peers:
                # Send to selected peers from advanced selection
                for peer in self.sql_selected_peers:
                    self.send_sql_to_peer(peer['ip'], peer['ip_local'], sql_command)
                peer_names = [p['name'] for p in self.sql_selected_peers]
                print(f"🗄️ SQL command executed: {sql_command[:50]}... to selected peers: {', '.join(peer_names)}")
            else:
                # Fallback to manual IP input (if no advanced selection)
                if self.dst_ip_input.text().strip() and self.dst_local_ip_input.text().strip():
                    self.send_sql_to_peer(self.dst_ip_input.text(), self.dst_local_ip_input.text(), sql_command)
                    print(f"🗄️ SQL command executed: {sql_command[:50]}...to {self.dst_ip_input.text()} : {self.dst_local_ip_input.text()}")
                else:
                    QMessageBox.warning(self, "No Target Selected", 
                                      "Please either:\n1. Select peers using Advanced Peer Selection, or\n2. Check 'Query All Peers', or\n3. Enter target IP addresses manually")

    def _admin_src_peer_id(self):
        try:
            if isinstance(self.ME, dict):
                return str(self.ME.get('id') or self.ME.get('peer_id') or '')
        except Exception:
            pass
        return ''

    def _peer_id_for_endpoint(self, ip, ip_local):
        ip = str(ip or '').strip()
        ip_local = str(ip_local or '').strip()
        for peer in (getattr(self, 'available_peers', None) or []):
            if str(peer.get('ip') or '') == ip or str(peer.get('ip_local') or '') == ip_local:
                return str(peer.get('peer_id') or peer.get('id') or '')
        for peer in (getattr(self, 'sql_selected_peers', None) or []):
            if str(peer.get('ip') or '') == ip or str(peer.get('ip_local') or '') == ip_local:
                return str(peer.get('peer_id') or peer.get('id') or '')
        return ''

    def send_sql_to_peer(self, ip, ip_local, sql_command):
        """Send SQL command to specific peer"""
        # Must pass src_peer_id: peers pin ADMIN_TRUSTED_PEER_ID on verify.
        # critical: reverse peers park forever until polled (not aged out at 600s).
        ok = send_packet(self.ME['ip'], self.ME['ip_local'], '',
            self.ip_to_send_packet_to(ip, ip_local), ip_local, '',
            init.CMD_SRV_SQL, init.PRT_NOR, '', 0, 0, '',
            [sql_command], [], [],
            '', '', [], [], [],
            'critical',
            False,
            src_peer_id=self._admin_src_peer_id(),
            dst_peer_id=self._peer_id_for_endpoint(ip, ip_local))
        print(f"[SQL] send to {ip}/{ip_local} dst_peer={self._peer_id_for_endpoint(ip, ip_local)} ok={ok}")

    def handle_peer_sql_result(self, columns, rows, peer_info=None):
        """Callback method to handle SQL results from peers"""
        if not hasattr(self, 'sql_results_layout'):
            print("[SQL] No SQL results UI available")
            return

        try:
            # Get peer info and try to match with known peers
            peer_name = "Unknown Peer"
            peer_ip = "Unknown IP"
            
            if peer_info:
                peer_ip = peer_info.get('ip', 'Unknown')
                peer_id = peer_info.get('peer_id', 'Unknown')
                
                # Try to find peer name from available peers
                for peer in self.available_peers:
                    if peer.get('ip') == peer_ip or peer.get('id') == peer_id:
                        peer_name = peer.get('name', f"Peer-{peer_id}")
                        break
                
                if peer_name == "Unknown Peer" and peer_ip != "Unknown IP":
                    peer_name = f"Peer ({peer_ip})"
            
            timestamp = get_timestamp()
            
            # Create title with peer info and timestamp
            title = f"📡 Results from {peer_name} at {timestamp}"
            
            # Convert to list of tuples for display
            recordset = rows if rows else []
            
            # Use the existing display_sql_result method to create the table
            self.display_sql_result_in_container(recordset, title)
            
            print(f"[SQL] Displayed results from {peer_name} ({peer_ip}): {len(rows)} rows, {len(columns)} columns")
            
        except Exception as e:
            print(f"[SQL] Error handling peer SQL result: {e}")
    
    def clear_sql_results(self):
        """Clear all SQL result displays"""
        if not hasattr(self, 'sql_results_layout'):
            return
            
        # Remove all result widgets except the stretch
        while self.sql_results_layout.count() > 1:
            item = self.sql_results_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        
        # Clear the SqlResults table from Admin.db to remove stale data
        try:
            query_database(init.pool_admin_db, "DELETE FROM SqlResults", (), commit=True)
            # Reset the last processed ID to ensure fresh results
            self.last_sql_result_id = 0
            print("[SQL] Cleared all SQL results from database and UI")
        except Exception as e:
            print(f"[SQL] Error clearing SqlResults table: {e}")
        
        print("[SQL] Cleared all SQL results")

    def display_sql_result_in_container(self, recordset, title="Query Result"):
        """Display SQL results in the SQL results container"""
        if not hasattr(self, 'sql_results_layout'):
            print("[SQL] No SQL results container available")
            return
            
        # Create a container for this specific result
        result_container = QWidget()
        result_layout = QVBoxLayout(result_container)
        result_layout.setContentsMargins(4, 4, 4, 4)
        
        # Add title
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #2c3e50; margin-bottom: 5px;")
        result_layout.addWidget(title_label)
        
        # Handle empty result
        if not recordset:
            empty_label = QLabel("No results returned.")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setStyleSheet("color: gray; font-style: italic;")
            result_layout.addWidget(empty_label)
        else:
            # Create table
            table = QTableWidget()
            columns = len(recordset[0])
            table.setColumnCount(columns)
            table.setRowCount(len(recordset))
            table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            table.setAlternatingRowColors(True)
            table.setSortingEnabled(True)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
            table.setMaximumHeight(300)  # Limit height for multiple results
            
            # Fill table with data
            for row_idx, row in enumerate(recordset):
                for col_idx, value in enumerate(row):
                    item = QTableWidgetItem(str(value))
                    item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
                    table.setItem(row_idx, col_idx, item)
            
            table.resizeColumnsToContents()
            table.resizeRowsToContents()
            result_layout.addWidget(table)
        
        # Insert this result at the top (before the stretch)
        self.sql_results_layout.insertWidget(0, result_container)



    def start_stop_lottery(self):
        """Start or stop lottery system"""
        if not self.lottery_running_checkbox.isChecked() and self.lottery_amount_input.text():
            # START LOTTERY
            self.lottery_running_checkbox.setChecked(True)

            # Get random online peer
            online_peers = [peer for peer in self.available_peers if peer.get('online', True)]
            if not online_peers:
                QMessageBox.warning(self, "No Online Peers", "No online peers available for lottery")
                return

            random_peer = random.choice(online_peers)

            # Set lottery data
            if hasattr(Lottery, 'jackpot'):
                Lottery.jackpot = self.lottery_amount_input.text()
                Lottery.running = True
                Lottery.executed = False
                Lottery.previous_ip = ''
                Lottery.previous_ip_local = ''

            # Tell peer to start lottery
            send_packet(
                '', '', '',
                self.ip_to_send_packet_to(random_peer['ip'], random_peer['ip_local']),
                random_peer['ip_local'], '',
                init.CMD_LOT_STR,
                init.PRT_NOR,
                '',
                0, 0, '',
                [self.lottery_amount_input.text(), '', ''],
                [], [],
                '', '',
                [], [], [])

            self.log(f"🎰 Lottery started with jackpot: {self.lottery_amount_input.text()}")
        else:
            # STOP LOTTERY
            self.lottery_running_checkbox.setChecked(False)
            self.log("🎰 Lottery stopped")

    def select_files(self):
        """Select files for deployment"""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Files for Deployment",
            "",
            "All Files (*.*)"
        )

        self.file_deployment_names = []
        self.file_deployment_data = []
        self.file_button_states = {}

        self.file_table.setRowCount(len(files))
        for i, file_path in enumerate(files):
            file_name = os.path.basename(file_path)
            
            btn_remove = QPushButton("❌")
            btn_remove.setMaximumWidth(40)
            btn_remove.clicked.connect(lambda checked, row=i: self.remove_file_from_table(row))
            self.file_table.setCellWidget(i, 0, btn_remove)
            
            self.file_table.setItem(i, 1, QTableWidgetItem(file_name))
            self.file_table.setItem(i, 2, QTableWidgetItem(os.path.dirname(file_path)))
            
            self.file_table.setItem(i, 3, QTableWidgetItem(""))
            
            btn_copy_to = QPushButton("📋 COPY TO")
            btn_copy_to.clicked.connect(lambda checked, row=i: self.on_copy_to_clicked(row))
            self.file_table.setCellWidget(i, 4, btn_copy_to)
            
            btn_remove_from = QPushButton("🗑️ REMOVE FROM")
            btn_remove_from.clicked.connect(lambda checked, row=i: self.on_remove_from_clicked(row))
            self.file_table.setCellWidget(i, 5, btn_remove_from)
            
            self.file_button_states[i] = {'mode': None, 'copy_to': btn_copy_to, 'remove_from': btn_remove_from}

        self.log(f"📁 Selected {len(files)} files for deployment")

    def reset_files(self):
        """Reset file selection"""
        self.file_table.setRowCount(0)
        self.file_deployment_names = []
        self.file_deployment_data = []
        self.file_button_states = {}
        self.log("🗑️ File selection reset")

    def remove_file_from_table(self, row):
        """Remove a file from the table"""
        self.file_table.removeRow(row)
        self.file_button_states.pop(row, None)
        self.log("🗑️ File removed from table")

    def deploy_files(self):
        """Deploy files to selected peers"""
        if not self.file_deployment_names:
            QMessageBox.warning(self, "No Files", "Please add files to deployment using COPY TO or REMOVE FROM buttons")
            return

        selected_peers = self.show_peer_selection_dialog()
        if not selected_peers:
            return

        reply = QMessageBox.question(
            self,
            "Confirm File Deployment",
            f"Deploy {len(self.file_deployment_names)} file operations to {len(selected_peers)} peers?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            for peer in selected_peers:
                self.send_files_to_peer(peer)
            self.log(f"📁 Files deployed to {len(selected_peers)} peers")

    def send_files_to_peer(self, peer):
        """Send files to specific peer using CMD_SRV_MDF"""
        try:
            target_ip = self.ip_to_send_packet_to(peer['ip'], peer['ip_local'])
            self.log_packet(init.CMD_SRV_MDF, target_ip, peer['ip_local'])
            print(self.file_deployment_names)
            print(self.file_deployment_data)
            send_packet(
                self.ME['ip'], self.ME['ip_local'], '',
                target_ip, peer['ip_local'], '',
                init.CMD_SRV_MDF, init.PRT_NOR, '', 0, 0, '',
                self.file_deployment_names, [], self.file_deployment_data,
                '', '', [], [], [])
        except Exception as e:
            self.log(f"❌ Error sending files to {peer['name']}: {e}")

    def on_copy_to_clicked(self, row):
        """Handle COPY TO button click for a file row"""
        file_name = self.file_table.item(row, 1).text()
        source_path = self.file_table.item(row, 2).text()
        
        target_folder = QFileDialog.getExistingDirectory(
            self,
            f"Select destination folder to copy '{file_name}' to (from app root)",
            init.MAIN_PATH,
            QFileDialog.Option.ShowDirsOnly
        )
        
        if target_folder:
            try:
                source_file = os.path.join(source_path, file_name)
                
                with open(source_file, 'rb') as f:
                    file_data = f.read()
                
                relative_path = os.path.relpath(target_folder, init.MAIN_PATH)
                if relative_path == '.':
                    relative_path = ''
                file_path_with_name = os.path.join(relative_path, file_name) if relative_path else file_name
                
                self.file_deployment_names.append(file_path_with_name)
                self.file_deployment_data.append(file_data)
                
                self.file_table.setItem(row, 3, QTableWidgetItem(relative_path if relative_path else "app root"))
                
                self.file_button_states[row]['mode'] = 'copy'
                self.file_button_states[row]['remove_from'].setEnabled(False)
                
                self.log(f"✅ Added to deployment: '{file_name}' → {relative_path}")
            except Exception as e:
                self.log(f"❌ Error adding file: {e}")
                QMessageBox.critical(self, "Add Error", f"Failed to add file: {e}")
    
    def on_remove_from_clicked(self, row):
        """Handle REMOVE FROM button click for a file row"""
        file_name = self.file_table.item(row, 1).text()
        
        remove_folder = QFileDialog.getExistingDirectory(
            self,
            f"Select folder to remove '{file_name}' from (from app root)",
            init.MAIN_PATH,
            QFileDialog.Option.ShowDirsOnly
        )
        
        if remove_folder:
            try:
                relative_path = os.path.relpath(remove_folder, init.MAIN_PATH)
                if relative_path == '.':
                    relative_path = ''
                file_path_with_name = os.path.join(relative_path, file_name) if relative_path else file_name
                
                self.file_deployment_names.append(file_path_with_name)
                self.file_deployment_data.append(b'')
                
                self.file_table.setItem(row, 3, QTableWidgetItem(""))
                
                self.file_button_states[row]['mode'] = 'remove'
                self.file_button_states[row]['copy_to'].setEnabled(False)
                
                self.log(f"✅ Added to deployment: remove '{file_name}' from {relative_path}")
            except Exception as e:
                self.log(f"❌ Error adding removal: {e}")
                QMessageBox.critical(self, "Remove Error", f"Failed to add removal: {e}")

    def select_banner(self):
        """Select banner image"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Banner Image",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp *.gif)"
        )

        if file_path:
            self.banner_file = file_path

            # Show preview
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                # Scale pixmap to fit preview area
                scaled_pixmap = pixmap.scaled(
                    400, 200,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self.banner_preview.setPixmap(scaled_pixmap)
                self.banner_preview.setToolTip(os.path.basename(file_path))

            self.log(f"🎨 Banner selected: {os.path.basename(file_path)}")

    def send_banner(self):
        """Send banner to peers"""
        if not self.banner_file:
            QMessageBox.warning(self, "No Banner", "Please select a banner first")
            return

        selected_peers = self.show_peer_selection_dialog()
        if not selected_peers:
            return

        reply = QMessageBox.question(
            self,
            "Send Banner",
            f"Send banner to {len(selected_peers)} peers?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.execute_banner_deployment(selected_peers)

    def execute_banner_deployment(self, selected_peers):
        """Execute banner deployment"""
        try:
            with open(self.banner_file, 'rb') as f:
                file_data = f.read()

            file_name = os.path.basename(self.banner_file)
            link = self.banner_link_input.text()
            impressions = str(self.banner_impressions_input.value())
            neurons = round(self.banner_neurons_input.value(), 5)

            for peer in selected_peers:
                self.send_banner_to_peer(
                    file_name, link, impressions, neurons,
                    peer['ip'], peer['ip_local'], file_data
                )

            self.log(f"🎨 Banner deployed to {len(selected_peers)} peers")

        except Exception as e:
            QMessageBox.critical(self, "Banner Error", f"Error deploying banner: {e}")
            self.log(f"❌ Banner deployment error: {e}")

    def send_banner_to_peer(self, file_name, link, impressions, neurons, ip, ip_local, file_data):
        """Send banner to specific peer"""
        import json
        from Support.Utils import generate_random_hash

        ad_id = generate_random_hash()
        peer_id = init.NODE_TYPE_ADMINISTRATOR
        strikes = 0
        cities = json.dumps([])
        countries = json.dumps([])
        date_end = ''
        campaign_name = ''
        new = 0
        propagate = 0

        send_packet(
            '', '', '',
            self.ip_to_send_packet_to(ip, ip_local), ip_local, '',
            init.CMD_BAN_SND,
            init.PRT_NOR,
            '',
            0, 0, '',
            [ad_id, file_name, link, impressions, peer_id, strikes, cities, countries,
             date_end, campaign_name, new, propagate, neurons],
            [],
            [file_data],
            '', '',
            [], [], [])

    # ==================== ENHANCED PEER SELECTION METHODS ====================

    def create_peer_selection_section(self):
        """Create peer selection section widget"""
        peer_group = QGroupBox("👥 Peer Selection")
        peer_layout = QGridLayout(peer_group)

        # Selection mode
        peer_layout.addWidget(QLabel("Selection Mode:"), 0, 0)
        self.peer_selection_mode = QComboBox()
        self.peer_selection_mode.addItems([
            "Single Peer", "Multiple Peers", "All Peers",
            "By Country", "By City", "Online Only", "Offline Only",
            "By Cohort"
        ])
        self.peer_selection_mode.currentTextChanged.connect(self.update_peer_selection_ui)
        peer_layout.addWidget(self.peer_selection_mode, 0, 1)

        # Single peer combo box
        peer_layout.addWidget(QLabel("Select Peer:"), 1, 0)
        self.peer_combo = QComboBox()
        self.peer_combo.currentIndexChanged.connect(self.admin_select_peer)
        peer_layout.addWidget(self.peer_combo, 1, 1)

        # Country/City selection
        peer_layout.addWidget(QLabel("Country/City:"), 2, 0)
        self.location_combo = QComboBox()
        self.location_combo.currentTextChanged.connect(self.refresh_peer_list)
        peer_layout.addWidget(self.location_combo, 2, 1)

        # Peer list widget for multiple selection
        peer_layout.addWidget(QLabel("Available Peers:"), 3, 0)
        self.peer_list_widget = QListWidget()
        self.peer_list_widget.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        peer_layout.addWidget(self.peer_list_widget, 3, 1, 2, 1)

        # Selected peers display
        peer_layout.addWidget(QLabel("Selected:"), 5, 0)
        self.selected_peers_label = QLabel("0 peers selected")
        self.selected_peers_label.setStyleSheet("color: #4a90e2; font-weight: bold;")
        peer_layout.addWidget(self.selected_peers_label, 5, 1)

        return peer_group

    def update_peer_selection_ui(self):
        """Update UI based on peer selection mode"""
        mode = self.peer_selection_mode.currentText()

        # Show/hide controls based on mode
        self.peer_combo.setVisible(mode == "Single Peer")
        self.location_combo.setVisible(mode in ["By Country", "By City", "By Cohort"])
        self.peer_list_widget.setVisible(mode in ["Multiple Peers", "By Country", "By City", "By Cohort"])

        # Update location combo if needed
        if mode in ["By Country", "By City"]:
            self.populate_location_combo(mode)
        elif mode == "By Cohort":
            self.location_combo.blockSignals(True)
            self.location_combo.clear()
            self.location_combo.addItems(["dev", "test", "prod"])
            self.location_combo.blockSignals(False)

        # Update peer list
        self.refresh_peer_list()
        self.update_selected_peers_display()

    def populate_location_combo(self, mode):
        """Populate location combo with countries or cities"""
        self.location_combo.clear()
        locations = set()

        for peer in self.available_peers:
            if mode == "By Country" and peer.get('country'):
                locations.add(peer['country'])
            elif mode == "By City" and peer.get('city'):
                locations.add(peer['city'])

        self.location_combo.addItems(sorted(locations))

    def refresh_peer_list(self):
        """Refresh the peer list widget based on current mode"""
        self.peer_list_widget.clear()
        mode = self.peer_selection_mode.currentText()

        filtered_peers = self.get_filtered_peers()

        for peer in filtered_peers:
            item_text = f"{peer['name']} - {peer['ip']}:{peer['ip_local']}"
            if peer.get('city'): item_text += f" ({peer['city']}"
            if peer.get('country'): item_text += f", {peer['country']}"
            if peer.get('city') or peer.get('country'): item_text += ")"

            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, peer)
            self.peer_list_widget.addItem(item)

    def get_filtered_peers(self):
        """Get peers filtered by current selection mode"""
        mode = self.peer_selection_mode.currentText()

        if mode == "All Peers":
            return self.available_peers
        elif mode == "Online Only":
            return [p for p in self.available_peers if p.get('online', True)]
        elif mode == "Offline Only":
            return [p for p in self.available_peers if not p.get('online', True)]
        elif mode == "By Country":
            country = self.location_combo.currentText()
            return [p for p in self.available_peers if p.get('country') == country]
        elif mode == "By City":
            city = self.location_combo.currentText()
            return [p for p in self.available_peers if p.get('city') == city]
        elif mode == "By Cohort":
            cohort = self.location_combo.currentText().strip().lower()
            if cohort in ('dev', 'test', 'prod'):
                try:
                    from Support.ChangeManagement import filter_peers_by_cohort
                    return filter_peers_by_cohort(self.available_peers, cohort)
                except Exception:
                    return []
            return []
        else:
            return self.available_peers

    def select_all_peers(self):
        """Select all peers in the list"""
        for i in range(self.peer_list_widget.count()):
            self.peer_list_widget.item(i).setSelected(True)
        self.update_selected_peers_display()

    def select_no_peers(self):
        """Deselect all peers"""
        self.peer_list_widget.clearSelection()
        self.update_selected_peers_display()

    def update_selected_peers_display(self):
        """Update the selected peers display label"""
        selected_peers = self.get_selected_peers()
        if not selected_peers:
            self.selected_peers_label.setText("None selected")
        else:
            names = [peer['name'] for peer in selected_peers]
            if len(names) <= 3:
                self.selected_peers_label.setText(", ".join(names))
            else:
                self.selected_peers_label.setText(f"{', '.join(names[:3])} and {len(names)-3} more")

    def get_selected_peers(self):
        """Get currently selected peers based on selection mode"""
        mode = self.peer_selection_mode.currentText()

        if mode == "Single Peer":
            if self.peer_combo.currentText():
                # Find the peer by combo selection
                for peer in self.available_peers:
                    if f"{peer['name']} - {peer['ip']}:{peer['ip_local']}" == self.peer_combo.currentText():
                        return [peer]
            return []
        elif mode == "All Peers":
            return self.available_peers
        elif mode in ["Multiple Peers", "By Country", "By City", "By Cohort"]:
            selected_items = self.peer_list_widget.selectedItems()
            return [item.data(Qt.ItemDataRole.UserRole) for item in selected_items]
        elif mode == "Online Only":
            return [p for p in self.available_peers if p.get('online', True)]
        elif mode == "Offline Only":
            return [p for p in self.available_peers if not p.get('online', True)]
        else:
            return []

    def admin_select_peer(self):
        """Handle peer selection from combo box"""
        if self.peer_combo.currentText():
            try:
                ip_complete = self.peer_combo.currentText().split(" - ")[1]
                ip = ip_complete.split(":")[0]
                ip_local = ip_complete.split(":")[1]
                self.dst_ip_input.setText(ip)
                self.dst_local_ip_input.setText(ip_local)
                self.log(f"Selected peer: {ip}:{ip_local}")
            except (IndexError, AttributeError):
                self.log("Error parsing peer selection")
        self.update_selected_peers_display()

    def get_ips(self):
        """Get all peer IP addresses"""
        all_ips = []
        for peer in self.available_peers:
            all_ips.append(f"{peer['ip']}:{peer['ip_local']}")
        return all_ips

    def compile_cython_extension(self, module_path):
        """Compile Cython extension using setup.py"""
        try:
            setup_py_path = os.path.abspath("setup.py")
            os.environ['MODULE_TO_COMPILE'] = module_path

            command = [sys.executable, setup_py_path, "install"]
            subprocess.run(command, check=True)

            self.log(f"✅ Successfully compiled {os.path.basename(module_path)}")
            return True

        except subprocess.CalledProcessError as e:
            self.log(f"❌ Compilation error for {os.path.basename(module_path)}: {e}")
            return False
        except FileNotFoundError:
            self.log(f"❌ setup.py not found at {setup_py_path}")
            return False
        except Exception as e:
            self.log(f"❌ Unexpected compilation error: {e}")
            return False

    def create_module_tab(self):
        """Create the module selection tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Module selection section
        module_group = QGroupBox("Module Selection")
        module_layout = QVBoxLayout(module_group)

        # Buttons for module operations
        button_layout = QHBoxLayout()

        self.btn_add_modules = QPushButton("Add Python Files")
        self.btn_add_modules.clicked.connect(self.add_modules)
        button_layout.addWidget(self.btn_add_modules)

        self.btn_compile_selected = QPushButton("Compile Selected")
        self.btn_compile_selected.clicked.connect(self.compile_selected_modules)
        button_layout.addWidget(self.btn_compile_selected)

        self.btn_clear_modules = QPushButton("Clear All")
        self.btn_clear_modules.clicked.connect(self.clear_modules)
        button_layout.addWidget(self.btn_clear_modules)

        module_layout.addLayout(button_layout)

        # Module list
        self.module_list = QListWidget()
        module_layout.addWidget(self.module_list)

        # Compilation progress
        self.compilation_progress = QProgressBar()
        self.compilation_progress.setVisible(False)
        module_layout.addWidget(self.compilation_progress)

        layout.addWidget(module_group)

        # Module information section
        info_group = QGroupBox("Module Information")
        info_layout = QGridLayout(info_group)

        info_layout.addWidget(QLabel("Version:"), 0, 0)
        self.version_input = QLineEdit("1.0.0")
        info_layout.addWidget(self.version_input, 0, 1)

        info_layout.addWidget(QLabel("Description:"), 1, 0)
        self.description_input = QLineEdit()
        info_layout.addWidget(self.description_input, 1, 1)

        layout.addWidget(info_group)

        return widget

    def create_deployment_tab(self):
        """Create the deployment tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Main content - Split view with left controls and right peer list
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # LEFT SIDE - Deployment controls
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Peer selection section
        peer_group = QGroupBox("Target Selection")
        peer_layout = QVBoxLayout(peer_group)

        # Main content - Split view with left controls and right peer list
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # LEFT SIDE - Deployment controls
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Peer selection buttons
        peer_button_layout = QHBoxLayout()

        self.btn_select_all_peers = QPushButton("Select All")
        self.btn_select_all_peers.clicked.connect(self.select_all_peers)
        peer_button_layout.addWidget(self.btn_select_all_peers)

        self.btn_select_no_peers = QPushButton("Select None")
        self.btn_select_no_peers.clicked.connect(self.select_no_peers)
        peer_button_layout.addWidget(self.btn_select_no_peers)

        self.btn_refresh_peers = QPushButton("Refresh Peers")
        self.btn_refresh_peers.clicked.connect(self.load_peers)
        peer_button_layout.addWidget(self.btn_refresh_peers)

        peer_layout.addLayout(peer_button_layout)
        left_layout.addWidget(peer_group)

        # Deployment section
        deploy_group = QGroupBox("Deployment Settings")
        deploy_layout = QVBoxLayout(deploy_group)

        # Deployment options
        self.force_update_checkbox = QCheckBox("Force update (ignore version checks)")
        deploy_layout.addWidget(self.force_update_checkbox)

        self.backup_modules_checkbox = QCheckBox("Create backups before deployment")
        self.backup_modules_checkbox.setChecked(True)
        deploy_layout.addWidget(self.backup_modules_checkbox)

        deploy_layout.addStretch()

        # Deploy button
        self.btn_deploy = QPushButton("🚀 Deploy Modules")
        self.btn_deploy.clicked.connect(self.deploy_modules)
        self.btn_deploy.setStyleSheet("QPushButton { background-color: #4a90e2; color: white; font-weight: bold; padding: 8px; font-size: 12px; }")
        deploy_layout.addWidget(self.btn_deploy)

        # Deployment progress
        self.deployment_progress = QProgressBar()
        self.deployment_progress.setVisible(False)
        deploy_layout.addWidget(self.deployment_progress)

        left_layout.addWidget(deploy_group)
        left_layout.addStretch()

        # RIGHT SIDE - Peer list
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        peer_list_group = QGroupBox("Target Peers")
        peer_list_layout = QVBoxLayout(peer_list_group)

        # Peer list
        self.peer_list = QListWidget()
        self.populate_peer_list()
        peer_list_layout.addWidget(self.peer_list)

        right_layout.addWidget(peer_list_group)

        # Add widgets to splitter
        main_splitter.addWidget(left_widget)
        main_splitter.addWidget(right_widget)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([400, 400])

        layout.addWidget(main_splitter)

        left_layout.addStretch()

        # RIGHT SIDE - Peer list
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        peer_list_group = QGroupBox("Target Peers")
        peer_list_layout = QVBoxLayout(peer_list_group)

        left_layout.addWidget(peer_group)

        # Add widgets to splitter
        main_splitter.addWidget(left_widget)
        main_splitter.addWidget(right_widget)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([400, 400])

        layout.addWidget(main_splitter)

        return widget

    def create_logs_tab(self):
        """Create the logs tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Log controls
        log_controls = QHBoxLayout()

        self.btn_clear_logs = QPushButton("Clear Logs")
        self.btn_clear_logs.clicked.connect(self.clear_logs)
        log_controls.addWidget(self.btn_clear_logs)

        self.btn_save_logs = QPushButton("Save Logs")
        self.btn_save_logs.clicked.connect(self.save_logs)
        log_controls.addWidget(self.btn_save_logs)

        log_controls.addStretch()
        layout.addLayout(log_controls)

        # Log display
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log_display)

        return widget

    def load_peers(self):
        """Load available peers from the database"""
        try:
            # Use connection pool instead of direct connection
            try:
                # Check what columns exist in the Peers table
                columns_result = query_database(init.pool_main_db, "PRAGMA table_info(Peers)", '')
                columns = [column[1] for column in columns_result]
                self.log(f"Available Peers table columns: {columns}")

                # Query peers including type/online when available
                if 'peer_id' in columns and 'type' in columns and 'online' in columns:
                    peer_rows = query_database(
                        init.pool_main_db,
                        "SELECT peer_id, name, ip, ip_local, type, online, comm_type FROM Peers", ''
                    )
                elif 'peer_id' in columns:
                    peer_rows = query_database(
                        init.pool_main_db,
                        "SELECT peer_id, name, ip, ip_local FROM Peers", ''
                    )
                elif 'type' in columns and 'online' in columns:
                    peer_rows = query_database(
                        init.pool_main_db,
                        "SELECT id, name, ip, ip_local, type, online FROM Peers", ''
                    )
                else:
                    peer_rows = query_database(init.pool_main_db, "SELECT id, name, ip, ip_local FROM Peers", '')

            except (AttributeError, Exception) as e:
                # Fallback to direct connection if pool is not available
                self.log(f"Connection pool not available, using direct connection: {e}")
                peer_rows = query_database(init.pool_main_db, "SELECT peer_id, name, ip, ip_local from Peers", '')

            self.log(f"Found {len(peer_rows)} peer records in database")

            # Convert to list of dictionaries
            self.available_peers = []
            for row in peer_rows:
                peer_key = row[0] if len(row) > 0 else ''
                peer = {
                    # Deploy/park routing needs neuron peer_id, not numeric row id.
                    'peer_id': peer_key,
                    'id': peer_key,
                    'name': row[1] if len(row) > 1 else '',
                    'ip': row[2] if len(row) > 2 else '',
                    'ip_local': row[3] if len(row) > 3 else '',
                    'type': row[4] if len(row) > 4 else '',
                    'online': row[5] if len(row) > 5 else False,
                    'comm_type': row[6] if len(row) > 6 else 1,
                }

                # Skip peers without essential data
                if not peer.get('peer_id') or not peer.get('name'):
                    self.log(f"Skipping peer with missing data: {peer}")
                    continue

                # Skip peers without network info - NEVER hardcode IPs!
                if not peer.get('ip') or not peer.get('ip_local'):
                    self.log(f"Skipping peer {peer.get('name', 'unknown')} - missing IP information")
                    continue

                self.available_peers.append(peer)
                self.log(f"Added peer: {peer['name']} ({peer['ip']}:{peer['ip_local']})")

            self.populate_peer_list()
            self.populate_peer_combo()
            self.populate_retrieve_logs_peer_combo()
            self.log(f"Loaded {len(self.available_peers)} peers from database")

            # Also try to load from init.PEERS as fallback
            if not self.available_peers and hasattr(init, 'PEERS') and init.PEERS:
                self.available_peers = [peer for peer in init.PEERS if peer.get('id')]
                self.populate_peer_list()
                self.populate_peer_combo()
                self.populate_retrieve_logs_peer_combo()
                self.log(f"Fallback: Loaded {len(self.available_peers)} peers from init.PEERS")

        except Exception as e:
            self.log(f"Error loading peers: {e}")
            # Try fallback to init.PEERS
            try:
                if hasattr(init, 'PEERS') and init.PEERS:
                    self.available_peers = [peer for peer in init.PEERS if peer.get('id')]
                    self.populate_peer_list()
                    self.populate_peer_combo()
                    self.populate_retrieve_logs_peer_combo()
                    self.log(f"Fallback: Loaded {len(self.available_peers)} peers from init.PEERS")
            except Exception as fallback_error:
                self.log(f"Fallback also failed: {fallback_error}")

    def populate_peer_list(self):
        """Populate the peer list widget"""
        if hasattr(self, 'peer_list'):
            self.peer_list.clear()
            for peer in self.available_peers:
                item = QListWidgetItem(f"{peer.get('name', 'Unknown')} - {peer.get('ip', '')}:{peer.get('ip_local', '')}")
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, peer)
                self.peer_list.addItem(item)

    def populate_peer_combo(self):
        """Populate the peer combo box"""
        if hasattr(self, 'peer_combo'):
            self.peer_combo.clear()
            for peer in self.available_peers:
                if peer.get('id'):
                    self.peer_combo.addItem(f"{peer.get('name', 'Unknown')} - {peer.get('ip', '')}:{peer.get('ip_local', '')}")

    def add_modules(self):
        """Add Python modules for compilation"""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Python Files",
            "",
            "Python Files (*.py);;All Files (*)"
        )

        for file_path in files:
            if file_path not in [item.data(Qt.ItemDataRole.UserRole) for item in self.get_all_list_items(self.module_list)]:
                item = QListWidgetItem(os.path.basename(file_path))
                item.setCheckState(Qt.CheckState.Checked)
                item.setData(Qt.ItemDataRole.UserRole, file_path)
                self.module_list.addItem(item)

        self.log(f"Added {len(files)} modules")
        self.update_version_display()

    def update_version_display(self):
        """Update the version display based on selected modules"""
        selected_items = self.module_list.selectedItems()
        if not selected_items:
            checked_items = [item for item in self.get_all_list_items(self.module_list) 
                           if item.checkState() == Qt.CheckState.Checked]
            if checked_items:
                module_names = [os.path.splitext(os.path.basename(item.data(Qt.ItemDataRole.UserRole)))[0] 
                               for item in checked_items]
                if len(module_names) == 1:
                    current_version = get_next_module_version(module_names[0])
                    self.version_display.setText(f"Next: {current_version}")
                else:
                    self.version_display.setText(f"Auto-generated for {len(module_names)} modules")
            else:
                self.version_display.setText("Auto-generated when compiling")
        else:
            module_names = [os.path.splitext(os.path.basename(item.data(Qt.ItemDataRole.UserRole)))[0] 
                           for item in selected_items]
            if len(module_names) == 1:
                current_version = get_next_module_version(module_names[0])
                self.version_display.setText(f"Next: {current_version}")
            else:
                self.version_display.setText(f"Auto-generated for {len(module_names)} modules")

    def add_compiled_modules(self):
        """Add already compiled modules (.pyd files)"""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Compiled Modules",
            "",
            "Compiled Modules (*.pyd);;All Files (*)"
        )

        for file_path in files:
            if file_path not in [item.data(Qt.ItemDataRole.UserRole) for item in self.get_all_list_items(self.module_list)]:
                item = QListWidgetItem(f"[COMPILED] {os.path.basename(file_path)}")
                item.setCheckState(Qt.CheckState.Checked)
                item.setData(Qt.ItemDataRole.UserRole, file_path)
                # Mark as compiled module with a special flag
                item.setData(Qt.ItemDataRole.UserRole + 1, True)  # Flag for compiled module
                self.module_list.addItem(item)

        self.log(f"Added {len(files)} compiled modules")
        self.update_version_display()

    def get_all_list_items(self, list_widget):
        """Get all items from a list widget"""
        items = []
        for i in range(list_widget.count()):
            items.append(list_widget.item(i))
        return items

    def compile_selected_modules(self):
        """Compile selected modules"""
        selected_items = [item for item in self.get_all_list_items(self.module_list)
                         if item.checkState() == Qt.CheckState.Checked]

        if not selected_items:
            QMessageBox.warning(self, "Warning", "No modules selected for compilation")
            return

        self.compilation_progress.setVisible(True)
        self.compilation_progress.setRange(0, len(selected_items))
        self.compilation_progress.setValue(0)

        self.compile_next_module(selected_items, 0)

    def compile_next_module(self, items, index):
        """Compile modules one by one"""
        if index >= len(items):
            self.compilation_progress.setVisible(False)
            self.log("All modules compiled successfully")
            QMessageBox.information(self, "Success", "All modules compiled successfully")
            return

        item = items[index]
        module_path = item.data(Qt.ItemDataRole.UserRole)

        self.worker = CompilationWorker(module_path)
        self.worker.progress.connect(self.log)
        self.worker.finished.connect(lambda success, msg: self.on_compilation_finished(success, msg, items, index))
        self.worker.start()

    def on_compilation_finished(self, success, message, items, index):
        """Handle compilation completion"""
        self.log(message)
        self.compilation_progress.setValue(index + 1)

        if success:

            # Mark item as compiled
            items[index].setText(items[index].text() + " ✅")

            # Copy the file to NeuronPROD the desktop which is my distribution folder that I will ZIP and send to peers
            try:
                module_path = items[index].data(Qt.ItemDataRole.UserRole)
                module_name = os.path.splitext(os.path.basename(module_path))[0]

                # Assumed location of the compiled .pyd file based on setup.py default output
                module_dir = os.path.dirname(os.path.abspath(module_path))
                pyd_file = _newest_pyd_for_module(module_dir, module_name)
                print(f"[COPY DEBUG] Newest .pyd for {module_name}: {pyd_file}")

                if not pyd_file:
                    print(f"[COPY DEBUG] .pyd not found in module_dir, searching subdirectories...")
                    # Search in build directories and subdirectories
                    for root, dirs, files in os.walk(module_dir):
                        print(f"[COPY DEBUG] Searching in: {root}")
                        for file in files:
                            print(f"[COPY DEBUG]   Found file: {file}")
                            if _is_module_pyd_name(file, module_name):
                                pyd_file = os.path.join(root, file)
                                print(f"[COPY DEBUG] ✅ Found .pyd: {pyd_file}")
                                break

                        if pyd_file:
                            break
                
                # If still not found, list all files to help with debugging
                if not pyd_file:
                    print(f"[COPY DEBUG] Still not found. All files in {module_dir}:")
                    for root, dirs, files in os.walk(module_dir):
                        for file in files:
                            full_path = os.path.join(root, file)
                            file_size = os.path.getsize(full_path)
                            print(f"[COPY DEBUG]   {full_path} ({file_size} bytes)")

                if pyd_file:
                    # Calculate relative path from MAIN_PATH to get the directory structure
                    try:
                        # Normalize paths to ensure consistent comparison
                        normalized_module_dir = os.path.normpath(os.path.abspath(module_dir))
                        normalized_main_path = os.path.normpath(os.path.abspath(init.MAIN_PATH))
                        
                        rel_dir = os.path.relpath(normalized_module_dir, normalized_main_path)
                        
                        # Clean up relative path - remove leading './' or '.\\'
                        rel_dir = rel_dir.lstrip('.\\/')
                        
                        print(f"[PATH DEBUG] normalized_module_dir: {normalized_module_dir}")
                        print(f"[PATH DEBUG] normalized_main_path: {normalized_main_path}")
                        print(f"[PATH DEBUG] rel_dir: {rel_dir}")
                    except ValueError as e:
                        print(f"[PATH DEBUG] ValueError during relpath: {e}")
                        rel_dir = ""

                    # Determine destination directory preserving structure
                    if not rel_dir or rel_dir == '.' or rel_dir == '':                        # Top-level module in Neuron root
                        dest_dir = os.path.normpath(init.PATH_INSTALLATION_FOLDER.rstrip('\\'))
                        self.log(f"Destination: Neuron_PROD (root level)")
                    else:
                        # Module in a subdirectory like Support
                        dest_dir = os.path.normpath(os.path.join(init.PATH_INSTALLATION_FOLDER.rstrip('\\'), rel_dir))
                        self.log(f"Destination: Neuron_PROD/{rel_dir}")

                    # Ensure destination directory exists
                    try:
                        os.makedirs(dest_dir, exist_ok=True)
                        print(f"[PATH DEBUG] Created destination dir: {dest_dir}")
                    except Exception as e:
                        self.log(f"Error creating destination directory {dest_dir}: {e}")
                        print(f"[PATH DEBUG] Error: {e}")
                        raise

                    # Rename versioned .pyd file to simple filename if needed
                    # e.g., Taskbar.cp312-win_amd64.pyd -> Taskbar.pyd
                    actual_pyd_filename = os.path.basename(pyd_file)
                    
                    if '.cp' in actual_pyd_filename:
                        # Versioned filename detected, rename to simple .pyd
                        simple_pyd_path = os.path.join(os.path.dirname(pyd_file), module_name + '.pyd')
                        try:
                            os.rename(pyd_file, simple_pyd_path)
                            print(f"[COPY DEBUG] Renamed: {actual_pyd_filename} -> {module_name}.pyd")
                            pyd_file = simple_pyd_path
                            actual_pyd_filename = module_name + '.pyd'
                        except Exception as e:
                            print(f"[COPY DEBUG] Warning: Could not rename file: {e}")
                    
                    destination_file = os.path.join(dest_dir, module_name + '.pyd')
                    
                    print(f"[COPY DEBUG] Source pyd_file: {pyd_file}")
                    print(f"[COPY DEBUG] Source exists: {os.path.exists(pyd_file)}")
                    print(f"[COPY DEBUG] Destination dir: {dest_dir}")
                    print(f"[COPY DEBUG] Destination dir exists: {os.path.exists(dest_dir)}")
                    print(f"[COPY DEBUG] Full destination: {destination_file}")
                    
                    try:
                        # Convert to absolute paths for safety
                        src_abs = os.path.abspath(pyd_file)
                        dst_abs = os.path.abspath(destination_file)
                        
                        print(f"[COPY DEBUG] Absolute source: {src_abs}")
                        print(f"[COPY DEBUG] Absolute destination: {dst_abs}")
                        print(f"[COPY DEBUG] Source exists: {os.path.exists(src_abs)}")
                        
                        shutil.copy(src_abs, dst_abs)
                        self.log(f"Copied {actual_pyd_filename}")
                        self.log(f"   From: {module_dir}")
                        self.log(f"   To:   {dst_abs}")
                        print(f"[COPY DEBUG] Successfully copied: {src_abs} -> {dst_abs}")
                    except Exception as e:
                        self.log(f"Error copying .pyd file: {e}")
                        print(f"[COPY DEBUG] Error copying file: {e}")
                        print(f"[COPY DEBUG] Traceback:")
                        import traceback
                        traceback.print_exc()
                        raise

                    # Delete from source folder to keep only .py files
                    try:
                        os.remove(pyd_file)
                        self.log(f"Deleted {pyd_file} from source folder")
                        print(f"[COPY DEBUG] Deleted source .pyd: {pyd_file}")
                    except Exception as e:
                        self.log(f"Warning: Could not delete {pyd_file} from source: {e}")

                    # Also delete any .c files that were generated during compilation
                    # Look for both simple and versioned .c files
                    for file in os.listdir(module_dir):
                        if file.startswith(module_name) and file.endswith('.c'):
                            c_file_path = os.path.join(module_dir, file)

                            try:
                                os.remove(c_file_path)
                                self.log(f"Deleted {file} from source folder")
                            except Exception as e:
                                self.log(f"Warning: Could not delete {file} from source: {e}")

                else:
                    print(f"[COPY DEBUG] pyd_file is None, PATH_INSTALLATION_FOLDER = {init.PATH_INSTALLATION_FOLDER}")
                    print(f"[COPY DEBUG] module_dir searched: {module_dir}")
                    print(f"[COPY DEBUG] Files in directory:")
                    try:
                        for f in os.listdir(module_dir):
                            print(f"[COPY DEBUG]   - {f}")
                    except Exception as e:
                        print(f"[COPY DEBUG] Error listing directory: {e}")
                    self.log(f"❌ Error: Could not find compiled .pyd file in {module_dir}")
            except Exception as e:
                raise

            # Continue with next module
            self.compile_next_module(items, index + 1)

        else:
            self.compilation_progress.setVisible(False)
            QMessageBox.critical(self, "Compilation Error", message)

    def deploy_modules(self):
        """Deploy compiled modules to selected peers"""
        selected_modules = [item for item in self.get_all_list_items(self.module_list)
                           if item.checkState() == Qt.CheckState.Checked and ("✅" in item.text() or "[COMPILED]" in item.text())]

        if not selected_modules:
            QMessageBox.warning(self, "Warning", "No compiled modules selected for deployment")
            return

        dialog = PeerSelectionDialog(self.available_peers, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected_peers = dialog.get_selected_peers()
        if not selected_peers:
            QMessageBox.warning(self, "Warning", "No peers selected for deployment")
            return

        reply = QMessageBox.question(
            self,
            "Confirm Deployment",
            f"Deploy {len(selected_modules)} modules to {len(selected_peers)} peers?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            action_sequence = self.action_sequence_editor.get_actions_data()
            # Peer CMD_SRV_MOD parser expects module_info length % 12 == 0 and
            # one binary per module. Actions / Send Files break that contract.
            # Use Release Wizard for files, DB changes, or update scripts.
            if action_sequence:
                QMessageBox.warning(
                    self,
                    "Use Release Wizard",
                    "Legacy single-module deploy cannot include update-script "
                    "actions (files/folders/SQL). Clear the action list, or use "
                    "Open Release Wizard for a signed full/patch release."
                )
                return
            force_update = bool(self.force_update_checkbox.isChecked())
            modules_data = self.prepare_module_data(
                selected_modules, action_sequence, force_update=force_update)

            self.deployment_progress.setVisible(True)
            self.deployment_progress.setRange(0, 0)

            self.deployment_worker = DeploymentWorker(modules_data, selected_peers)
            self.deployment_worker.progress.connect(self.log)
            self.deployment_worker.finished.connect(self.on_deployment_finished)
            self.deployment_worker.start()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to prepare module data: {e}")
            self.log(f"Deployment preparation error: {e}")

    def prepare_module_data(self, selected_modules, action_sequence=None, force_update=False):
        """Prepare module data for deployment - separating metadata and binary data"""
        from Support.ModuleSecurity import calculate_crc, generate_trusted_signature
        import json

        trusted_signature = generate_trusted_signature()
        
        # Generate proper deployment_id using the generate_random_hash function
        deployment_id = generate_random_hash()

        # Get module names for automatic versioning
        module_names = [os.path.splitext(os.path.basename(item.data(Qt.ItemDataRole.UserRole)))[0] 
                       for item in selected_modules]
        
        # Get automatic versions for all modules and update database
        module_versions = batch_increment_module_versions(module_names)
        self.log(f"Auto-generated versions: {module_versions}")
        if force_update:
            self.log("Force update enabled - peers will stage even if version is not newer")

        deployment_metadata = {
            "deployment_id": deployment_id,
            "admin_ip": init.ME['ip'] if init.ME else 'unknown',
            "deployment_time": get_timestamp(),
            "total_modules": len(selected_modules),
            "description": self.description_input.text(),
            "signature": trusted_signature,
            "has_action_sequence": "1" if action_sequence else "0",
            "action_sequence_count": str(len(action_sequence)) if action_sequence else "0",
            "force_update": "1" if force_update else "0",
        }

        modules_metadata = []
        modules_binary_data = []

        for item in selected_modules:
            module_path = item.data(Qt.ItemDataRole.UserRole)
            module_name = os.path.splitext(os.path.basename(module_path))[0]

            module_dir = os.path.dirname(os.path.abspath(module_path))

            try:
                rel_dir = os.path.relpath(module_dir, init.MAIN_PATH)
            except ValueError:
                rel_dir = ""

            file_to_deploy = None

            if rel_dir == '.' or rel_dir == '':
                pyd_path = os.path.join(init.PATH_INSTALLATION_FOLDER, module_name + ".pyd")
            else:
                pyd_path = os.path.join(init.PATH_INSTALLATION_FOLDER, rel_dir, module_name + ".pyd")
            if os.path.exists(pyd_path):
                file_to_deploy = pyd_path
                self.log(f"Using compiled module: {pyd_path}")

            if not file_to_deploy:
                if rel_dir == '.' or rel_dir == '':
                    pyd_path = os.path.join(os.path.dirname(init.MAIN_PATH), "Neuron_PROD",
                                                                 module_name + ".pyd")
                else:
                    pyd_path = os.path.join(os.path.dirname(init.MAIN_PATH), "Neuron_PROD", rel_dir, module_name + ".pyd")
                if os.path.exists(pyd_path):
                    file_to_deploy = pyd_path
                    self.log(f"Using compiled module: {pyd_path}")

            # Never ship .py as a compiled module - peer renames payload to .pyd and
            # ImportError: dynamic module does not define module export function (PyInit_X).
            if not file_to_deploy:
                raise Exception(
                    f"Compiled .pyd not found for {module_name}. "
                    f"Compile it first (expected at {pyd_path})."
                )

            with open(file_to_deploy, 'rb') as f:
                data = f.read()

            if len(data) < 2 or data[0:2] != b'MZ':
                raise Exception(
                    f"{file_to_deploy} is not a Windows .pyd (missing MZ header). "
                    f"Refusing to deploy {module_name}."
                )

            expected_init = ("PyInit_" + module_name).encode("ascii")
            if expected_init not in data:
                raise Exception(
                    f"{file_to_deploy} does not export {expected_init.decode()}. "
                    f"Wrong binary staged for {module_name} (e.g. PeersUtils as Peers)."
                )

            checksum = calculate_crc(data)
            file_extension = os.path.splitext(file_to_deploy)[1]
            if file_extension.lower() != '.pyd':
                raise Exception(f"Refusing to deploy non-.pyd file for {module_name}: {file_to_deploy}")

            sub_folder = ""
            if rel_dir and rel_dir != '.':
                sub_folder = rel_dir
            
            # Use automatic version for this module
            module_version = module_versions.get(module_name, "1.0")
            
            module_metadata = {
                "name": module_name,
                "sub_folder": sub_folder,
                "deploy_date": get_timestamp(),
                "version": module_version,
                "pending": "1",
                "folder": "",
                "deployment_id": deployment_metadata["deployment_id"],
                "install_date": "",
                "checksum": checksum,
                "size": str(len(data)),
                "file_extension": file_extension,
                "is_compiled": str(file_extension == ".pyd")
            }
            
            modules_metadata.append(module_metadata)
            modules_binary_data.append(data)
            
            self.log(f"Prepared {module_name}: {len(data)} bytes, CRC: {checksum[:16]}...")
        
        deployment_info = [
            deployment_metadata["deployment_id"],
            deployment_metadata["admin_ip"], 
            deployment_metadata["deployment_time"],
            str(deployment_metadata["total_modules"]),
            "1.0",  # version (placeholder - actual module versions in module_info)
            deployment_metadata["description"],
            "4.0",  # format_version
            deployment_metadata["signature"],
            deployment_metadata["has_action_sequence"],
            deployment_metadata["action_sequence_count"],
            deployment_metadata.get("force_update", "0"),
        ]
        
        module_info = []
        for module_meta in modules_metadata:
            module_info.extend([
                module_meta["name"],
                module_meta["sub_folder"],
                module_meta["deploy_date"],
                module_meta["version"],
                module_meta["pending"],
                module_meta["folder"],
                module_meta["deployment_id"],
                module_meta["install_date"],
                module_meta["checksum"],
                module_meta["size"],
                module_meta["file_extension"],
                module_meta["is_compiled"]
            ])
        
        # Collect additional files from "Send Files" actions
        additional_files_data = []
        additional_files_count = 0
        
        if action_sequence:
            for action in action_sequence:
                if action.get('type') == 'Send Files' and 'send_files' in action:
                    send_files = action.get('send_files', [])
                    for file_path in send_files:
                        if os.path.exists(file_path):
                            try:
                                with open(file_path, 'rb') as f:
                                    file_data = f.read()
                                additional_files_data.append(file_data)
                                additional_files_count += 1
                                self.log(f"Added file to deployment: {os.path.basename(file_path)}")
                            except Exception as e:
                                self.log(f"Warning: Failed to read file {file_path}: {e}")
                        else:
                            self.log(f"Warning: File not found: {file_path}")
            
            action_info = json.dumps(action_sequence)
            module_info.append(action_info)
        
        # Combine module data with additional files
        all_binary_data = modules_binary_data + additional_files_data
        
        self.log(f"✓ Prepared deployment package with signature: {trusted_signature[:10]}...")
        if action_sequence:
            self.log(f"✓ Included {len(action_sequence)} installation actions")
        if additional_files_count > 0:
            self.log(f"✓ Included {additional_files_count} additional files in deployment")
        
        return deployment_info, module_info, all_binary_data

    def on_deployment_finished(self, success, message, failed_deployments):
        """Handle deployment completion"""
        self.deployment_progress.setVisible(False)
        self.log(message)
        
        if success:
            QMessageBox.information(self, "Success", "Deployment completed successfully!")
        else:
            error_msg = message
            if failed_deployments:
                error_msg += f"\nFailed deployments: {', '.join(failed_deployments)}"
            QMessageBox.warning(self, "Deployment Issues", error_msg)
            
    def clear_modules(self):
        """Clear all modules from the list"""
        self.module_list.clear()
        self.log("Cleared all modules")
        
    def log(self, message):
        """Add message to log display with enhanced formatting"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}"
        
        # Print to console for debugging
        print(log_message)
        
        # Add to log display if it exists
        if self.log_display is not None:
            self.log_display.append(log_message)
            
            # Auto-scroll if enabled
            if hasattr(self, 'btn_auto_scroll') and self.btn_auto_scroll.isChecked():
                scrollbar = self.log_display.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())
        
        # Update status bar with latest message
        if hasattr(self, 'statusBar'):
            self.statusBar().showMessage(f"🔄 {message}")
            
            # Clear status message after 3 seconds
            QTimer.singleShot(3000, lambda: self.statusBar().showMessage("🟢 Ready - Neuron Advanced Admin System"))
        
    def clear_logs(self):
        """Clear the log display"""
        self.log_display.clear()
        
    def save_logs(self):
        """Save logs to file"""
        file_path, _ = QFileDialog.getSaveFileName(
            self, 
            "Save Logs", 
            f"deployment_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            "Text Files (*.txt);;All Files (*)"
        )
        
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(self.log_display.toPlainText())
                QMessageBox.information(self, "Success", f"Logs saved to {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save logs: {e}")

    def show_peer_selection_dialog(self):
        """Show the advanced peer selection dialog"""
        if not self.available_peers:
            QMessageBox.warning(self, "No Peers", "No peers available. Please refresh the peer list first.")
            return []
        
        dialog = PeerSelectionDialog(self.available_peers, self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            selected_peers = dialog.get_selected_peers()
            self.log(f"Selected {len(selected_peers)} peers for operation")
            return selected_peers
        return []

    def show_neuron_peer_selection_dialog(self):
        """Show the advanced peer selection dialog for neuron management"""
        if not self.available_peers:
            QMessageBox.warning(self, "No Peers", "No peers available. Please refresh the peer list first.")
            return

        dialog = PeerSelectionDialog(self.available_peers, self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self.neuron_selected_peers = dialog.get_selected_peers()
            self.update_neuron_selected_peers_display()
            self.update_neuron_button_states()
            self.log(f"Selected {len(self.neuron_selected_peers)} peers for neuron management")

    def update_neuron_selected_peers_display(self):
        """Show selected peers in the combined neuron table"""
        self.neuron_table.setRowCount(0)
        for peer in self.neuron_selected_peers:
            row = self.neuron_table.rowCount()
            self.neuron_table.insertRow(row)
            self.neuron_table.setItem(row, 0, QTableWidgetItem(str(peer.get('name', ''))))
            self.neuron_table.setItem(row, 1, QTableWidgetItem(str(peer.get('ip', ''))))
            self.neuron_table.setItem(row, 2, QTableWidgetItem(str(peer.get('ip_local', ''))))
            country = peer.get('country') or peer.get('country_code') or ''
            self.neuron_table.setItem(row, 3, QTableWidgetItem(str(country)))
            self.neuron_table.setItem(row, 4, QTableWidgetItem(''))
        self.neuron_table.resizeColumnsToContents()
        self.refresh_neuron_peer_results()

    def update_neuron_button_states(self):
        """Enable/disable neuron buttons based on peer selection"""
        has_peers = len(self.neuron_selected_peers) > 0
        self.btn_query_neurons.setEnabled(has_peers)
        self.btn_execute_neurons.setEnabled(has_peers)

    def refresh_neuron_peer_results(self):
        """Fill Country/Neurons columns for selected peers from Global responses"""
        if not hasattr(self, 'neuron_table') or self.neuron_table.rowCount() == 0:
            return
        try:
            global_records = query_database(
                init.pool_admin_db,
                "SELECT ip, ip_local, neurons, country_code FROM Global",
                ''
            )
            by_key = {}
            for record in global_records or []:
                by_key[(str(record[0]), str(record[1]))] = record

            for row in range(self.neuron_table.rowCount()):
                ip_item = self.neuron_table.item(row, 1)
                local_item = self.neuron_table.item(row, 2)
                if not ip_item or not local_item:
                    continue
                key = (ip_item.text(), local_item.text())
                if key not in by_key:
                    continue
                _, _, neurons, country_code = by_key[key]
                self.neuron_table.setItem(row, 3, QTableWidgetItem(str(country_code or '')))
                self.neuron_table.setItem(row, 4, QTableWidgetItem(str(neurons if neurons is not None else '')))
        except Exception as e:
            if "no such table" not in str(e).lower():
                self.log(f"Error refreshing neuron peer results: {e}")

    def show_sql_peer_selection_dialog(self):
        """Show the advanced peer selection dialog for SQL commands"""
        if not self.available_peers:
            QMessageBox.warning(self, "No Peers", "No peers available. Please refresh the peer list first.")
            return

        dialog = PeerSelectionDialog(self.available_peers, self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self.sql_selected_peers = dialog.get_selected_peers()
            self.update_sql_selected_peers_display()
            self.log(f"Selected {len(self.sql_selected_peers)} peers for SQL commands")

    def update_sql_selected_peers_display(self):
        """Update the SQL selected peers display table"""
        self.sql_selected_peers_table.setRowCount(0)
        for peer in self.sql_selected_peers:
            row = self.sql_selected_peers_table.rowCount()
            self.sql_selected_peers_table.insertRow(row)
            self.sql_selected_peers_table.setItem(row, 0, QTableWidgetItem(peer['name']))
            self.sql_selected_peers_table.setItem(row, 1, QTableWidgetItem(peer['ip']))
            self.sql_selected_peers_table.setItem(row, 2, QTableWidgetItem(peer['ip_local']))
        self.sql_selected_peers_table.resizeColumnsToContents()

    def check_for_new_sql_results(self):

        """Check for new SQL results in the Admin database and display them"""
        if not hasattr(self, 'sql_results_layout'):
            return  # SQL tab not initialized yet
            
        try:
            results = query_database(init.pool_admin_db, '''SELECT id, peer_ip, peer_ip_local, peer_id, timestamp, rows
                             FROM SqlResults 
                             WHERE id > ? 
                             ORDER BY id ASC''', (self.last_sql_result_id,))
            
            if results:
                print(f"[SQL] Found {len(results)} new SQL results to display")
                
            for result in results:
                result_id, peer_ip, peer_ip_local, peer_id, timestamp, rows_json = result
                
                try:
                    import json
                    rows = json.loads(rows_json)
                    
                    # Generate column names as Col1, Col2, etc. based on data width
                    column_count = len(rows[0]) if rows and len(rows) > 0 else 0
                    columns = [f"Col{i+1}" for i in range(column_count)]
                    
                    # Try to get peer name from available peers
                    peer_name = f"Peer ({peer_ip})"
                    for peer in self.available_peers:
                        if peer.get('ip') == peer_ip:
                            peer_name = peer.get('name', peer_name)
                            break
                    
                    # Create title with peer info and timestamp
                    title = f"📡 Results from {peer_name} at {timestamp}"
                    
                    # Display the result
                    self.display_sql_result_in_container(rows, title)
                    
                    # Update last processed ID
                    self.last_sql_result_id = max(self.last_sql_result_id, result_id)
                    
                    print(f"[SQL] Displayed result from {peer_name} ({peer_ip}): {len(rows)} rows, {column_count} columns")
                    
                except Exception as e:
                    print(f"[SQL] Error processing SQL result {result_id}: {e}")
                    self.last_sql_result_id = max(self.last_sql_result_id, result_id)  # Skip this result
            
        except Exception as e:
            if "no such table" not in str(e).lower():  # Don't spam if table doesn't exist yet
                print(f"[SQL] Error checking for new SQL results: {e}")

    def load_global_table_data(self):
        """Refresh Country/Neurons for currently selected peers"""
        self.refresh_neuron_peer_results()

    def send_lottery_command(self):
        """Send lottery command to selected peers"""
        try:
            selected_peers = self.show_peer_selection_dialog()
            if not selected_peers:
                return
            
            # Get lottery parameters
            lottery_type = getattr(self, 'lottery_type_combo', None)
            if not lottery_type or not lottery_type.currentText():
                QMessageBox.warning(self, "Missing Information", "Please select lottery type")
                return
            
            self.log(f"Sending lottery command to {len(selected_peers)} peers")
            
            for peer in selected_peers:
                self.log(f"Sending lottery command to {peer['name']} ({peer['ip']})")
                
                # Send lottery command
                send_packet(
                    self.ME['ip'], self.ME['ip_local'], '',
                    peer['ip'], peer['ip_local'], '',
                    init.CMD_LOT_STR,
                    init.PRT_LOW,
                    '',
                    0,
                    0,
                    get_timestamp(),
                    [lottery_type.currentText()],  # Send lottery type in data1
                    [],
                    [],
                    '',
                    '',
                    [],
                    [],
                    []
                )
            
            self.log("Lottery commands sent successfully")
            
        except Exception as e:
            self.log(f"Error sending lottery command: {e}")
            QMessageBox.critical(self, "Error", f"Failed to send lottery command: {e}")

    def deploy_file(self):
        """Deploy selected file to peers"""
        try:
            if not hasattr(self, 'file_path_input') or not self.file_path_input.text().strip():
                QMessageBox.warning(self, "No File", "Please select a file to deploy")
                return
            
            file_path = self.file_path_input.text().strip()
            if not os.path.exists(file_path):
                QMessageBox.warning(self, "File Not Found", f"File not found: {file_path}")
                return
            
            selected_peers = self.show_peer_selection_dialog()
            if not selected_peers:
                return
            
            self.log(f"Deploying file {file_path} to {len(selected_peers)} peers")
            
            # Read file data
            with open(file_path, 'rb') as f:
                file_data = f.read()
            
            file_name = os.path.basename(file_path)
            file_size = len(file_data)
            
            for peer in selected_peers:
                self.log(f"Deploying file to {peer['name']} ({peer['ip']})")
                
                # Send file deployment command
                send_packet(
                    self.ME['ip'], self.ME['ip_local'], '',
                    peer['ip'], peer['ip_local'], '',
                    init.CMD_SRV_MDF,
                    init.PRT_LOW,
                    '',
                    0,
                    0,
                    get_timestamp(),
                    [file_name, str(file_size)],  # File info in data1
                    [],
                    [file_data],  # File data in data3
                    '',
                    '',
                    [],
                    [],
                    []
                )
            
            self.log("File deployment commands sent successfully")
            
        except Exception as e:
            self.log(f"Error deploying file: {e}")
            QMessageBox.critical(self, "Error", f"Failed to deploy file: {e}")

    def deploy_banner(self):
        """Deploy banner/ad to selected peers"""
        try:
            if not self.banner_file:
                QMessageBox.warning(self, "No Banner", "Please select a banner file first")
                return
            
            selected_peers = self.show_peer_selection_dialog()
            if not selected_peers:
                return
            
            self.log(f"Deploying banner to {len(selected_peers)} peers")
            
            # Get banner parameters
            banner_text = getattr(self, 'banner_text_input', None)
            banner_duration = getattr(self, 'banner_duration_input', None)
            
            banner_text_value = banner_text.text() if banner_text else ""
            banner_duration_value = str(banner_duration.value()) if banner_duration else "30"
            
            for peer in selected_peers:
                self.log(f"Deploying banner to {peer['name']} ({peer['ip']})")
                
                # Send banner deployment command
                send_packet(
                    self.ME['ip'], self.ME['ip_local'], '',
                    peer['ip'], peer['ip_local'], '',
                    init.CMD_SRV_BAN,
                    init.PRT_LOW,
                    '',
                    0,
                    0,
                    get_timestamp(),
                    [banner_text_value, banner_duration_value],  # Banner info in data1
                    [],
                    [self.banner_file],  # Banner file data in data3
                    '',
                    '',
                    [],
                    [],
                    []
                )
            
            self.log("Banner deployment commands sent successfully")
            
        except Exception as e:
            self.log(f"Error deploying banner: {e}")
            QMessageBox.critical(self, "Error", f"Failed to deploy banner: {e}")

    def create_package_tab(self):
        """Create distribution package (Neuron.rar) from Neuron_PROD."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        info = QLabel(
            "Build the distributable Neuron.rar from the Neuron_PROD folder:\n"
            "Template.db schema synced from the live main DB, emptied sidecar DBs "
            "(Languages.db keeps Language_codes), .pyd refresh (Support + Apps), "
            "force-mirrored User\\ui + User\\Media\\Images + User\\Docs\\i18n/docs "
            "(removed source files purged from Neuron_PROD), Apps assets "
            "(including app data/images/grapesjs; works/logs skipped), "
            "home websites (NeuronWorldNetwork/Welcome, optional _lang), "
            "web-app templates+static localized then sealed into FrontendVault, "
            "Support\\Web + nltk_data, System\\THIRD_PARTY_LICENSES, "
            "site-packages library mirror, Conductor assets, "
            "FrontendVault pair verified inside the RAR.\n"
            "Archive stores everything under a single Neuron\\ folder for clean desktop extract."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        btn_row = QHBoxLayout()
        self.btn_package_start = QPushButton("START")
        self.btn_package_start.clicked.connect(self.start_create_package)
        self.btn_package_start.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; font-weight: bold; padding: 10px; font-size: 13px; }"
        )
        btn_row.addWidget(self.btn_package_start)

        self.btn_package_stop = QPushButton("STOP")
        self.btn_package_stop.clicked.connect(self.stop_create_package)
        self.btn_package_stop.setEnabled(False)
        self.btn_package_stop.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; font-weight: bold; padding: 10px; font-size: 13px; }"
        )
        btn_row.addWidget(self.btn_package_stop)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.package_progress = QProgressBar()
        self.package_progress.setRange(0, PackageCreateWorker.PACKAGE_STEPS)
        self.package_progress.setValue(0)
        layout.addWidget(self.package_progress)

        self.package_status = QLineEdit()
        self.package_status.setReadOnly(True)
        self.package_status.setPlaceholderText("Progress status...")
        layout.addWidget(self.package_status)

        self.package_log = QTextEdit()
        self.package_log.setReadOnly(True)
        self.package_log.setPlaceholderText("Detailed package creation log...")
        layout.addWidget(self.package_log)

        return widget

    def start_create_package(self):
        if self.package_worker and self.package_worker.isRunning():
            QMessageBox.warning(self, "Busy", "Package creation is already running")
            return
        # CREATE PACKAGE runs from the Neuron SOURCE tree and writes into Neuron_PROD.
        try:
            from Support.ChangeManagement import audit_log
            audit_log('package_create', result='ok', extra={'phase': 'start', 'main_path': init.MAIN_PATH})
        except Exception:
            pass
        self.package_log.clear()
        self.package_status.setText("Starting...")
        self.package_progress.setValue(0)
        self.btn_package_start.setEnabled(False)
        self.btn_package_stop.setEnabled(True)

        self.package_worker = PackageCreateWorker()
        self.package_worker.progress.connect(self.on_package_progress)
        self.package_worker.step.connect(self.on_package_step)
        self.package_worker.finished.connect(self.on_package_finished)
        self.package_worker.start()

    def create_change_management_tab(self):
        """Load CM tab from portable NeuronChangeManagement\\ADMIN\\AdminCMTab.py"""
        import importlib.util
        from Support.ChangeManagement import CM_HOME
        path = os.path.join(CM_HOME, 'ADMIN', 'AdminCMTab.py')
        spec = importlib.util.spec_from_file_location('AdminCMTab', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.build_change_management_tab(self)

    def stop_create_package(self):
        if not self.package_worker or not self.package_worker.isRunning():
            return
        reply = QMessageBox.question(
            self,
            "Confirm STOP",
            "Abort package creation?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.package_status.setText("Abort requested...")
            self.package_worker.request_abort()

    def on_package_progress(self, message):
        self.package_status.setText(message)
        self.package_log.append(message)
        self.log(message)

    def on_package_step(self, current, total):
        self.package_progress.setMaximum(total)
        self.package_progress.setValue(current)

    def on_package_finished(self, success, message):
        self.btn_package_start.setEnabled(True)
        self.btn_package_stop.setEnabled(False)
        self.package_status.setText(message)
        self.package_log.append(message)
        try:
            from Support.ChangeManagement import audit_log, pyd_compile_output
            rar_path = ''
            if self.package_worker:
                rar_path = self.package_worker._neuron_rar_path()
            else:
                rar_path = os.path.join(init.MAIN_PATH, 'System', 'neuron', 'htdocs', 'Neuron.rar')
            sha = ''
            if success and rar_path and os.path.isfile(rar_path):
                import hashlib
                h = hashlib.sha512()
                with open(rar_path, 'rb') as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        h.update(chunk)
                sha = h.hexdigest()
            audit_log(
                'package_create',
                result='ok' if success else 'fail',
                error='' if success else message,
                extra={
                    'neuron_rar_path': rar_path,
                    'neuron_rar_sha512': sha,
                    'pyd_compile_output': pyd_compile_output(),
                },
            )
        except Exception:
            pass
        if success:
            self.package_progress.setValue(self.package_progress.maximum())
            rar_note = ''
            try:
                if self.package_worker:
                    rar_note = f"\n{self.package_worker._neuron_rar_path()}"
            except Exception:
                pass
            QMessageBox.information(self, "CREATE PACKAGE", message + rar_note)
        else:
            QMessageBox.warning(self, "CREATE PACKAGE", message)

def display_sql_result(self, columns, rows):
    table = QTableWidget(self)
    table.setColumnCount(len(columns))
    table.setRowCount(len(rows))

    # Headers
    for col, name in enumerate(columns):
        table.setHorizontalHeaderItem(col, QTableWidgetItem(str(name)))

    # Data
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem("" if value is None else str(value))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(r, c, item)

    table.resizeColumnsToContents()
    table.resizeRowsToContents()

    # Clear previous results
    while self.sql_results_layout.count():
        child = self.sql_results_layout.takeAt(0)
        if child.widget():
            child.widget().deleteLater()

    self.sql_results_layout.addWidget(table)


def main():
    """Main entry point for the enhanced admin application"""
    import Support.Header as init
    if not getattr(init, 'REMOTE_ADMIN_ENABLED', False):
        print("AdminConsole killswitch is OFF.")
        print("Set REMOTE_ADMIN_ENABLED = True in Support/Header.py to use AdminConsole.")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("Neuron Advanced Admin & Module Deployer v3.0")

    app.setStyleSheet(main_dialog_style)

    try:
        app.setWindowIcon(QIcon("..\\User\\Media\\Images\\Support\\Neuron.png"))
    except:
        pass

    window = ModuleDeployerApp()
    window.show()

    window.log("Neuron Advanced Admin System started (REMOTE_ADMIN_ENABLED=True).")
    window.log("Deploy killswitch: Support/Header.py -> REMOTE_ADMIN_ENABLED")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()