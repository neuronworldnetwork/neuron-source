# Peer-safe helpers for major Neuron.rar apply (no ADMIN dependency).
# Used by Support/MajorUpgrade during OTA major upgrades on Tower/peers.

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Callable, List, Optional, Tuple

# Keep in sync with ADMIN.NeuronSetup PRESERVE_INSTALL_PATHS.
PRESERVE_INSTALL_PATHS = (
    'User',
    'System/neuron/data',
    'System/ai/knowledge_base',
    'System/AI/knowledge_base',
    'System/AI/bundles',
    'System/json',
    'System/Bak',
    'System/Releases',
    'System/logs',
    'System/Apps',
    'System/Old',
    'System/frontend.vault',
    'ADMIN/keys',
    'System/neuron/htdocs/uploads',
    'System/neuron/htdocs/home',
    'Apps',
)

ProgressCb = Optional[Callable[[float, str], None]]


def _run_hidden(cmd, **kwargs):
    """Run a subprocess with no console window on Windows."""
    kwargs.setdefault('capture_output', True)
    kwargs.setdefault('text', True)
    if sys.platform == 'win32':
        kwargs.setdefault('creationflags', getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        kwargs.setdefault('startupinfo', startupinfo)
    return subprocess.run(cmd, **kwargs)


def find_unrar(main_path: str = '') -> Optional[str]:
    """Locate UnRAR.exe next to install or under System/neuron/rar."""
    candidates = []
    if getattr(sys, 'frozen', False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), 'UnRAR.exe'))
    if main_path:
        candidates.append(os.path.join(main_path, 'UnRAR.exe'))
        candidates.append(os.path.join(main_path, 'System', 'neuron', 'rar', 'UnRAR.exe'))
    try:
        import Support.Header as init
        mp = str(getattr(init, 'MAIN_PATH', '') or '')
        if mp:
            candidates.append(os.path.join(mp, 'UnRAR.exe'))
            candidates.append(os.path.join(mp, 'System', 'neuron', 'rar', 'UnRAR.exe'))
        rar_dir = str(getattr(init, 'PATH_RAR', '') or '')
        if rar_dir:
            candidates.append(os.path.join(rar_dir, 'UnRAR.exe'))
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(os.path.dirname(here), 'System', 'neuron', 'rar', 'UnRAR.exe'))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _rmtree_force(path):
    if not path or not os.path.exists(path):
        return

    def onerror(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except Exception:
            pass

    git_dir = os.path.join(path, '.git')
    if os.path.isdir(git_dir):
        trash = os.path.join(
            tempfile.gettempdir(),
            'NeuronMajor_git_%s_%s' % (os.getpid(), int(__import__('time').time()))
        )
        try:
            os.rename(git_dir, trash)
        except OSError:
            shutil.rmtree(git_dir, ignore_errors=True, onerror=onerror)
    shutil.rmtree(path, ignore_errors=True, onerror=onerror)


def _park_path(src, park_root, rel):
    if not src or not os.path.exists(src):
        return False
    dest = os.path.join(park_root, rel.replace('/', os.sep))
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.exists(dest):
        if os.path.isdir(dest) and not os.path.islink(dest):
            _rmtree_force(dest)
        else:
            try:
                os.remove(dest)
            except OSError:
                _rmtree_force(dest)
    shutil.move(src, dest)
    return True


def park_preserved_install_data(neuron_root, park_root):
    parked = []
    if not neuron_root or not os.path.isdir(neuron_root):
        return parked
    for rel in PRESERVE_INSTALL_PATHS:
        src = os.path.join(neuron_root, rel.replace('/', os.sep))
        if _park_path(src, park_root, rel):
            parked.append(rel)
    system_root = os.path.join(neuron_root, 'System')
    if os.path.isdir(system_root):
        for walk_root, _dirs, files in os.walk(system_root):
            for name in files:
                low = name.lower()
                if not (low.endswith('.db') or low.endswith('.sqlite') or low.endswith('.sqlite3')):
                    continue
                abs_path = os.path.join(walk_root, name)
                try:
                    rel = os.path.relpath(abs_path, neuron_root).replace('\\', '/')
                except ValueError:
                    continue
                if any(rel == p or rel.startswith(p + '/') for p in parked):
                    continue
                if _park_path(abs_path, park_root, rel):
                    parked.append(rel)
    return parked


def restore_preserved_install_data(park_root, neuron_root, parked_rels):
    if not park_root or not os.path.isdir(park_root) or not parked_rels:
        return
    os.makedirs(neuron_root, exist_ok=True)
    for rel in sorted(set(parked_rels), key=lambda r: r.count('/'), reverse=True):
        src = os.path.join(park_root, rel.replace('/', os.sep))
        if not os.path.exists(src):
            continue
        dest = os.path.join(neuron_root, rel.replace('/', os.sep))
        if os.path.exists(dest):
            if os.path.isdir(dest) and not os.path.islink(dest):
                _rmtree_force(dest)
            else:
                try:
                    os.remove(dest)
                except OSError:
                    _rmtree_force(dest)
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.move(src, dest)


def _neuron_logo_path(neuron_root: str = '') -> str:
    candidates = []
    try:
        import Support.Header as init
        base = str(getattr(init, 'PATH_SYSTEM_SUPPORT_IMAGES', '') or '')
        if base:
            candidates.append(os.path.join(base, 'Neuron - watermark.png'))
        mp = str(getattr(init, 'MAIN_PATH', '') or '')
        if mp:
            candidates.append(
                os.path.join(mp, 'User', 'Media', 'Images', 'Support', 'Neuron - watermark.png')
            )
    except Exception:
        pass
    if neuron_root:
        candidates.append(
            os.path.join(
                neuron_root, 'User', 'Media', 'Images', 'Support', 'Neuron - watermark.png'
            )
        )
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ''


class ApplyProgressUI:
    """Always-on-top update window: logo, versions, file list, dual progress bars."""

    def __init__(
        self,
        title='Neuron update',
        version='',
        old_version='',
        new_version='',
        timestamp='',
        modules=None,
        notes='',
        logo_path='',
        neuron_root='',
    ):
        self._root = None
        self._status = None
        self._file_lbl = None
        self._modules_lbl = None
        self._notes_lbl = None
        self._file_bar = None
        self._overall_bar = None
        self._ver_lbl = None
        self._done_frm = None
        self._done_lbl = None
        self._countdown_lbl = None
        self._restart_btn = None
        self._photo = None
        self._version = str(new_version or version or '')
        self._old_version = str(old_version or '')
        self._new_version = str(new_version or version or '')
        self._timestamp = str(timestamp or '')
        self._notes = str(notes or '').strip()
        self._modules = [str(m) for m in (modules or []) if str(m)]
        self._title = str(title or 'Neuron update')
        self._closed = False
        self._finish_done = False
        self._lock = threading.Lock()
        try:
            import tkinter as tk
            from tkinter import ttk
            root = tk.Tk()
            root.title(self._title)
            root.attributes('-topmost', True)
            root.resizable(False, False)
            root.geometry('520x460')
            try:
                root.protocol('WM_DELETE_WINDOW', lambda: None)
            except Exception:
                pass
            frm = ttk.Frame(root, padding=16)
            frm.pack(fill='both', expand=True)

            logo = logo_path or _neuron_logo_path(neuron_root)
            if logo:
                try:
                    self._photo = tk.PhotoImage(file=logo)
                    # Cap width ~120 without requiring PIL.
                    try:
                        w = int(self._photo.width())
                        if w > 120:
                            factor = max(1, int(round(w / 120.0)))
                            self._photo = self._photo.subsample(factor, factor)
                    except Exception:
                        pass
                    ttk.Label(frm, image=self._photo).pack(pady=(0, 8))
                except Exception:
                    self._photo = None

            head = self._title
            ttk.Label(frm, text=head, font=('Segoe UI', 12, 'bold')).pack(anchor='w')
            ver_bits = []
            if self._old_version:
                ver_bits.append('From %s' % self._old_version)
            if self._new_version:
                ver_bits.append('to %s' % self._new_version)
            if self._timestamp:
                ver_bits.append('(%s)' % self._timestamp)
            self._ver_lbl = ttk.Label(
                frm,
                text=' '.join(ver_bits) if ver_bits else (
                    ('Version %s' % self._version) if self._version else 'Applying update'
                ),
                wraplength=480,
            )
            self._ver_lbl.pack(anchor='w', pady=(4, 0))

            mods_txt = ', '.join(self._modules[:12]) if self._modules else ''
            if len(self._modules) > 12:
                mods_txt += ' ...'
            self._modules_lbl = ttk.Label(
                frm,
                text=('Updating: %s' % mods_txt) if mods_txt else 'Preparing files...',
                wraplength=480,
            )
            self._modules_lbl.pack(anchor='w', pady=(6, 0))
            if self._notes:
                self._notes_lbl = ttk.Label(
                    frm, text='Notes: %s' % self._notes, wraplength=480
                )
                self._notes_lbl.pack(anchor='w', pady=(4, 0))

            self._status = ttk.Label(frm, text='Starting...', wraplength=480)
            self._status.pack(anchor='w', pady=(8, 0))
            self._file_lbl = ttk.Label(frm, text='Current file: —', wraplength=480)
            self._file_lbl.pack(anchor='w', pady=(4, 0))

            ttk.Label(frm, text='Current file').pack(anchor='w', pady=(10, 0))
            self._file_bar = ttk.Progressbar(frm, mode='determinate', maximum=100, length=480)
            self._file_bar.pack(pady=(2, 0))
            ttk.Label(frm, text='Overall progress').pack(anchor='w', pady=(8, 0))
            self._overall_bar = ttk.Progressbar(frm, mode='determinate', maximum=100, length=480)
            self._overall_bar.pack(pady=(2, 0))

            self._done_frm = ttk.Frame(frm)
            self._done_lbl = ttk.Label(
                self._done_frm, text='', wraplength=480, font=('Segoe UI', 11, 'bold')
            )
            self._done_lbl.pack(anchor='w')
            self._countdown_lbl = ttk.Label(self._done_frm, text='', wraplength=480)
            self._countdown_lbl.pack(anchor='w', pady=(6, 0))
            self._restart_btn = ttk.Button(self._done_frm, text='RESTART NOW')
            self._restart_btn.pack(pady=(10, 0))

            self._root = root
            self._tk = tk
            root.update_idletasks()
            root.update()
        except Exception as e:
            print('[MAJOR_INSTALL] progress UI unavailable: %s' % e)
            self._root = None

    def set_modules(self, modules):
        with self._lock:
            self._modules = [str(m) for m in (modules or []) if str(m)]
            if self._closed or self._modules_lbl is None:
                return
            try:
                mods_txt = ', '.join(self._modules[:12]) if self._modules else ''
                if len(self._modules) > 12:
                    mods_txt += ' ...'
                self._modules_lbl.config(
                    text=('Updating: %s' % mods_txt) if mods_txt else 'Preparing files...'
                )
                self._pump()
            except Exception:
                pass

    def update(
        self,
        fraction: float,
        message: str = '',
        file_fraction: float = None,
        current_file: str = '',
    ):
        with self._lock:
            if self._closed or self._root is None:
                return
            try:
                overall = max(0.0, min(100.0, float(fraction) * 100.0))
                if self._overall_bar is not None:
                    self._overall_bar['value'] = overall
                if file_fraction is None:
                    file_pct = overall
                else:
                    file_pct = max(0.0, min(100.0, float(file_fraction) * 100.0))
                if self._file_bar is not None:
                    self._file_bar['value'] = file_pct
                if self._status is not None and message:
                    self._status.config(text=str(message))
                if self._file_lbl is not None and current_file:
                    self._file_lbl.config(text='Current file: %s' % current_file)
                self._pump()
            except Exception:
                pass

    def show_restart_countdown(self, version: str = '', seconds: int = 10):
        """Block until countdown ends or RESTART NOW; then return."""
        ver = str(version or self._new_version or self._version or '').strip()
        secs = max(1, int(seconds or 10))
        with self._lock:
            if self._closed or self._root is None:
                return
            try:
                if self._overall_bar is not None:
                    self._overall_bar['value'] = 100
                if self._file_bar is not None:
                    self._file_bar['value'] = 100
                if self._status is not None:
                    self._status.config(text='Update complete')
                if self._done_frm is not None:
                    self._done_frm.pack(fill='x', pady=(14, 0))
                if self._done_lbl is not None:
                    self._done_lbl.config(
                        text=(
                            "You've updated to version %s! Restarting now." % ver
                            if ver else
                            "You've updated! Restarting now."
                        )
                    )
                self._pump()
            except Exception:
                return

        state = {'left': secs, 'go': False}

        def _tick():
            if state['go'] or self._closed:
                return
            left = state['left']
            try:
                if self._countdown_lbl is not None:
                    self._countdown_lbl.config(
                        text='Restarting in %s second%s...' % (left, '' if left == 1 else 's')
                    )
                self._pump()
            except Exception:
                pass
            if left <= 0:
                state['go'] = True
                return
            state['left'] = left - 1
            try:
                self._root.after(1000, _tick)
            except Exception:
                state['go'] = True

        def _now():
            state['go'] = True

        try:
            if self._restart_btn is not None:
                self._restart_btn.config(command=_now)
        except Exception:
            pass
        _tick()
        while not state['go'] and not self._closed and self._root is not None:
            try:
                self._root.update()
            except Exception:
                break
            try:
                import time as _time
                _time.sleep(0.05)
            except Exception:
                pass
        self._finish_done = True

    def _pump(self):
        if self._root is None:
            return
        self._root.update_idletasks()
        self._root.update()

    def close(self):
        with self._lock:
            self._closed = True
            root = self._root
            self._root = None
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def apply_neuron_rar(
    artifact: str,
    neuron_root: str,
    progress: ProgressCb = None,
) -> Tuple[bool, str]:
    """Park user data, extract Neuron.rar over neuron_root, restore parked trees."""
    neuron_root = os.path.normpath(neuron_root or '')
    if not neuron_root or not os.path.isdir(neuron_root):
        return False, 'MAIN_PATH missing'
    if not artifact or not os.path.isfile(artifact):
        return False, 'artifact missing'
    unrar = find_unrar(neuron_root)
    if not unrar:
        return False, 'UnRAR.exe not found'

    def _prog(frac, msg, file_frac=None, current_file=''):
        if not progress:
            return
        try:
            progress(frac, msg, file_frac, current_file)
            return
        except TypeError:
            pass
        try:
            progress(frac, msg)
        except Exception:
            pass

    _prog(0.02, 'Preparing update...', 0.0, '')
    # Park under Neuron\System\temp so a crashed apply can still be recovered
    # (system TEMP parks are invisible from the share and easy to lose).
    park_base = os.path.join(neuron_root, 'System', 'temp')
    try:
        os.makedirs(park_base, exist_ok=True)
    except Exception:
        park_base = tempfile.gettempdir()
    park = tempfile.mkdtemp(prefix='NeuronMajor_preserve_', dir=park_base)
    extract_parent = tempfile.mkdtemp(prefix='NeuronMajor_extract_', dir=park_base)
    parked = []
    restored = False
    try:
        _prog(0.08, 'Safeguarding your data...', 0.2, 'User data')
        parked = park_preserved_install_data(neuron_root, park)
        _prog(0.18, 'Extracting Neuron.rar (this can take several minutes)...', 0.0, 'Neuron.rar')
        cmd = [
            unrar, 'x', '-y', '-o+',
            r'-x*\.git', r'-x*\.git\*',
            artifact, extract_parent + os.sep,
        ]
        proc = _run_hidden(cmd)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or '').strip()
            return False, 'extract failed: %s' % (err[:300],)
        _prog(0.55, 'Extract complete', 1.0, 'Neuron.rar')

        _prog(0.58, 'Installing program files...', 0.0, '')
        extracted = os.path.join(extract_parent, 'Neuron')
        if not os.path.isdir(extracted):
            for name in os.listdir(extract_parent):
                cand = os.path.join(extract_parent, name)
                if os.path.isdir(cand) and os.path.isdir(os.path.join(cand, 'Support')):
                    extracted = cand
                    break
        if not os.path.isdir(extracted):
            return False, 'extracted Neuron tree not found'

        file_count = 0
        for _dp, _dn, filenames in os.walk(extracted):
            file_count += len(filenames)
        done = 0
        for dirpath, dirnames, filenames in os.walk(extracted):
            rel = os.path.relpath(dirpath, extracted)
            dest_dir = neuron_root if rel == '.' else os.path.join(neuron_root, rel)
            os.makedirs(dest_dir, exist_ok=True)
            for name in filenames:
                src = os.path.join(dirpath, name)
                dest = os.path.join(dest_dir, name)
                rel_file = name if rel == '.' else ('%s/%s' % (rel.replace('\\', '/'), name))
                try:
                    shutil.copy2(src, dest)
                except Exception as e:
                    print('[MAJOR_INSTALL] copy warn %s: %s' % (dest, e))
                done += 1
                if file_count:
                    file_frac = float(done) / float(file_count)
                    frac = 0.58 + 0.32 * file_frac
                    if done == 1 or done == file_count or (done % 25 == 0):
                        _prog(
                            frac,
                            'Installing files (%s/%s)...' % (done, file_count),
                            file_frac,
                            rel_file,
                        )

        _prog(0.92, 'Restoring your data...', 0.5, 'User data')
        if parked:
            restore_preserved_install_data(park, neuron_root, parked)
            restored = True
        else:
            restored = True
        _prog(1.0, 'Update complete', 1.0, '')
        return True, 'major rar applied (parked %s trees)' % len(parked)
    except Exception:
        # Best effort: put User/DBs back even if extract/copy failed.
        try:
            if park and parked and not restored:
                restore_preserved_install_data(park, neuron_root, parked)
                restored = True
        except Exception as e:
            print('[MAJOR_INSTALL] restore-after-fail warn: %s' % e)
        raise
    finally:
        try:
            _rmtree_force(extract_parent)
        except Exception:
            pass
        # NEVER delete the park until restore succeeded — otherwise a failed
        # or interrupted apply permanently loses User / databases.
        if restored:
            try:
                _rmtree_force(park)
            except Exception:
                pass
        else:
            print('[MAJOR_INSTALL] park retained for recovery: %s' % park)
