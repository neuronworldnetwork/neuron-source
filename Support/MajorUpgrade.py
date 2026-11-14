"""
Major OTA helpers: data-property protection, wanted-release state,
piece-swarm pull, and ops-based apply.

Policy:
- Default DENY delete/overwrite of User data, DBs under System/neuron/data,
  System/ai/knowledge_base, and config/work files (.json/.enc/...).
- ALLOW only when the package explicitly instructs (ops.explicit=true, or
  DeploymentActionExecutor actions authored in the release).
- NEVER delete database *files* (Remove Database / wipe data folder).
"""

import hashlib
import json
import os
import shutil
import tempfile
import threading
time = __import__('time')
from typing import Any, Dict, List, Optional, Tuple

# Trees that hold user property — never wipe; file ops need explicit=true
# and still cannot delete .db files. Keep in sync with NeuronSetup PRESERVE_INSTALL_PATHS.
_DATA_PROPERTY_PREFIXES = (
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

_CONFIG_WORK_SUFFIXES = (
    '.json', '.enc', '.pem', '.key', '.secret', '.nsk', '.vault',
    '.db', '.sqlite', '.sqlite3',
)

_WANTED_NAME = 'wanted_release.json'
_PENDING_ADV_NAME = 'pending_adv.json'
_MAJOR_PIECE_SIZE = 1024 * 1024
MAJOR_MESH_PKG_MAX_BYTES = 24 * 1024 * 1024

_swarm_lock = threading.Lock()
_swarm_active = {}
_adv_amplify_lock = threading.Lock()
_adv_amplified = {}  # release_id -> monotonic ts


def _main_path() -> str:
    from Support.Header import MAIN_PATH
    return MAIN_PATH


def _releases_dir() -> str:
    from Support.Header import PATH_DISTRIBUTABLE_RELEASES
    return PATH_DISTRIBUTABLE_RELEASES


def _wanted_path() -> str:
    return os.path.join(_releases_dir(), _WANTED_NAME)


def _pending_adv_path() -> str:
    return os.path.join(_releases_dir(), _PENDING_ADV_NAME)


def _norm_rel(path: str) -> str:
    p = (path or '').replace('\\', '/').lstrip('/')
    while '..' in p.split('/'):
        parts = []
        for bit in p.split('/'):
            if bit == '..':
                if parts:
                    parts.pop()
            elif bit and bit != '.':
                parts.append(bit)
        p = '/'.join(parts)
    return p


def _rel_lower(rel_or_abs: str, main_path: str = '') -> Tuple[str, str]:
    main_path = main_path or _main_path()
    raw = (rel_or_abs or '').strip()
    if not raw:
        return '', ''
    abs_path = raw if os.path.isabs(raw) else os.path.join(main_path, raw.replace('/', os.sep))
    abs_path = os.path.normpath(abs_path)
    try:
        rel = os.path.relpath(abs_path, os.path.normpath(main_path))
    except ValueError:
        return abs_path, ''
    if rel.startswith('..'):
        return abs_path, ''
    return abs_path, rel.replace('\\', '/').lower()


def is_database_file(rel_or_abs: str, main_path: str = '') -> bool:
    abs_path, lower = _rel_lower(rel_or_abs, main_path)
    if not lower:
        return True
    base = os.path.basename(lower)
    if base.endswith(('.db', '.sqlite', '.sqlite3')):
        return True
    try:
        from Support.Header import PATH_SQLITE, PATH_DATABASE, DATABASE_NAME
        for root in (PATH_SQLITE, PATH_DATABASE):
            if not root:
                continue
            root_n = os.path.normpath(root)
            if abs_path == root_n or abs_path.startswith(root_n + os.sep):
                return True
        if DATABASE_NAME and base == str(DATABASE_NAME).lower():
            return True
    except Exception:
        pass
    return False


def is_data_property_path(rel_or_abs: str, main_path: str = '') -> bool:
    """User property trees (User, DBs dir, knowledge_base, keys, bak)."""
    _, lower = _rel_lower(rel_or_abs, main_path)
    if not lower:
        return True
    for prefix in _DATA_PROPERTY_PREFIXES:
        pref = prefix.replace('\\', '/').lower()
        if lower == pref or lower.startswith(pref + '/'):
            return True
    if is_database_file(rel_or_abs, main_path):
        return True
    return False


def is_config_work_file(rel_or_abs: str, main_path: str = '') -> bool:
    """Config/work files that must not be touched unless package is explicit."""
    _, lower = _rel_lower(rel_or_abs, main_path)
    if not lower:
        return True
    base = os.path.basename(lower)
    for suf in _CONFIG_WORK_SUFFIXES:
        if base.endswith(suf):
            return True
    if lower.startswith('system/json/') or lower.startswith('system\\json\\'):
        return True
    return False


def is_protected_path(rel_or_abs: str, main_path: str = '') -> bool:
    """
    Legacy name: True if a blind (non-explicit) file mutation must be refused.
    Database files are always protected. Data trees and config/work files are
    protected unless the caller passes explicit=True to the op helpers.
    """
    return is_data_property_path(rel_or_abs, main_path) or is_config_work_file(rel_or_abs, main_path)


def mutation_allowed(rel_or_abs: str, *, explicit: bool = False, allow_db_file: bool = False,
                     main_path: str = '') -> bool:
    """
    File-level mutation gate for updates.
    - DB files: never (unless allow_db_file, which nothing should set).
    - Data property / config: only if explicit=True (package instructed).
    """
    if is_database_file(rel_or_abs, main_path) and not allow_db_file:
        return False
    if is_data_property_path(rel_or_abs, main_path) or is_config_work_file(rel_or_abs, main_path):
        return bool(explicit)
    return True


def filter_ops_for_safety(ops: Dict[str, Any], main_path: str = '') -> Dict[str, Any]:
    """Keep ops; drop unsafe ones. explicit=true required for data/config paths."""
    main_path = main_path or _main_path()
    out = {
        'add': [],
        'overwrite': [],
        'delete': [],
        'preserve': list(ops.get('preserve') or []) if isinstance(ops, dict) else [],
    }
    if not isinstance(ops, dict):
        return out
    for key in ('add', 'overwrite', 'delete'):
        for item in ops.get(key) or []:
            if isinstance(item, dict):
                rel = item.get('path') or item.get('dest') or item.get('file_path') or ''
                explicit = bool(item.get('explicit'))
            else:
                rel = str(item or '')
                explicit = False
            rel = _norm_rel(rel)
            if not rel:
                continue
            if is_database_file(rel, main_path):
                print(f"[MAJOR_UPGRADE] refused {key} on database file: {rel}")
                continue
            if not mutation_allowed(rel, explicit=explicit, main_path=main_path):
                print(f"[MAJOR_UPGRADE] refused {key} (need explicit:true): {rel}")
                continue
            if isinstance(item, dict):
                cleaned = dict(item)
                cleaned['path'] = rel
                out[key].append(cleaned)
            else:
                out[key].append(rel)
    for must in _DATA_PROPERTY_PREFIXES:
        if must not in out['preserve']:
            out['preserve'].append(must)
    return out


def default_major_ops() -> Dict[str, Any]:
    return {
        'add': [],
        'overwrite': [],
        'delete': [],
        'preserve': list(_DATA_PROPERTY_PREFIXES),
    }


def get_wanted_release() -> Dict[str, Any]:
    path = _wanted_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def set_wanted_release(entry: Dict[str, Any]) -> None:
    os.makedirs(_releases_dir(), exist_ok=True)
    path = _wanted_path()
    payload = dict(entry or {})
    payload['updated_utc'] = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)


def clear_wanted_release() -> None:
    path = _wanted_path()
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


def _local_neuron_rar_paths() -> Tuple[str, str]:
    """Published Neuron.rar is Apache static under htdocs (not SQLite data/)."""
    from Support.Header import PATH_HTDOCS, PATH_DATABASE
    base = str(PATH_HTDOCS or '').strip() or str(PATH_DATABASE or '').strip()
    rar = os.path.join(base, 'Neuron.rar')
    return rar, rar + '.version.json'


def _arm_ready_major_and_notify(
    rid: str,
    version: str,
    artifact_path: str,
    file_hash: str = '',
    package_size: int = 0,
    piece_size: int = 0,
    piece_hashes: Optional[List[str]] = None,
    ops: Optional[Dict[str, Any]] = None,
    train_reset: bool = False,
    post_apply_version: str = '',
    notes: str = '',
) -> None:
    """Same post-download UX as piece-swarm complete: wanted ready + green ticker / countdown."""
    wanted = {
        'release_id': rid or '',
        'version': version or '',
        'kind': 'major',
        'file_hash': (file_hash or '').lower(),
        'package_size': int(package_size or 0),
        'piece_size': int(piece_size or _MAJOR_PIECE_SIZE),
        'piece_hashes': list(piece_hashes or []),
        'status': 'ready_to_apply',
        'ops': filter_ops_for_safety(ops or default_major_ops()),
        'artifact_name': 'Neuron.rar',
        'artifact_path': artifact_path,
        'notes': str(notes or ''),
    }
    if train_reset:
        wanted['train_reset'] = True
        wanted['post_apply_version'] = str(post_apply_version or version or '0.0.0')
    set_wanted_release(wanted)
    try:
        from Support.ModuleSecurity import _notify_release_restart
        _notify_release_restart(
            version=str(version or ''), kind='major', notes=str(notes or '')
        )
    except Exception:
        pass


def try_arm_local_neuron_rar() -> Tuple[bool, str]:
    """
    If htdocs already has a Neuron.rar newer than SystemInfo (e.g. Tower Promote),
    mark ready_to_apply and fire the same restart ticker/dialog as a finished swarm.
    """
    from Support.ModuleSecurity import (
        get_installed_release_version, _release_version_tuple,
    )

    # Ops kill-switch: after a manual unpack / emergency, do not keep re-arming.
    if str(os.environ.get('NEURON_SKIP_LOCAL_RAR_ARM') or '').strip() in ('1', 'true', 'yes'):
        return False, 'skipped by env'
    try:
        from Support.Header import PATH_DISTRIBUTABLE_RELEASES
        _skip_flag = os.path.join(
            PATH_DISTRIBUTABLE_RELEASES or '', 'SKIP_LOCAL_RAR_ARM.flag'
        )
        if _skip_flag and os.path.isfile(_skip_flag):
            return False, 'skipped by flag'
    except Exception:
        pass

    rar, ver_path = _local_neuron_rar_paths()
    if not os.path.isfile(rar) or not os.path.isfile(ver_path):
        print(f"[MAJOR_UPGRADE] local rar miss: {rar}")
        return False, 'no local rar'
    try:
        with open(ver_path, 'r', encoding='utf-8') as f:
            meta = json.load(f) or {}
    except Exception as e:
        return False, f'version json: {e}'

    ver = str(meta.get('version') or '')
    rid = str(meta.get('release_id') or '')
    file_hash = str(meta.get('file_hash') or '').lower()
    try:
        expect_size = int(meta.get('size') or meta.get('package_size') or 0)
    except (TypeError, ValueError):
        expect_size = 0
    actual_size = os.path.getsize(rar)
    if expect_size and actual_size != expect_size:
        return False, f'size mismatch {actual_size}!={expect_size}'
    if not ver:
        return False, 'no version'

    installed = get_installed_release_version()
    train_reset = bool(meta.get('train_reset'))
    if train_reset:
        # Explicit train rewind (e.g. fleet -> 0.0.0): arm even when not newer.
        if (
            _release_version_tuple(ver) == _release_version_tuple(installed)
            and file_hash
        ):
            try:
                import hashlib
                h = hashlib.sha256()
                with open(rar, 'rb') as f:
                    while True:
                        block = f.read(1024 * 1024)
                        if not block:
                            break
                        h.update(block)
                if h.hexdigest().lower() == file_hash:
                    return False, 'train_reset already at matching rar'
            except Exception:
                pass
    elif _release_version_tuple(ver) <= _release_version_tuple(installed):
        return False, 'not newer than installed'

    cur = get_wanted_release() or {}
    cur_status = str(cur.get('status') or '')
    cur_path = str(cur.get('artifact_path') or '')
    if (
        cur_status == 'ready_to_apply'
        and str(cur.get('release_id') or '') == rid
        and cur_path
        and os.path.isfile(cur_path)
    ):
        # Arm often happens before UI exists; re-offer ticker once desktop is up.
        try:
            import Support.Header as _hdr
            if bool(getattr(_hdr, 'conductor_visible', False)):
                from Support.ModuleSecurity import _notify_release_restart
                _notify_release_restart(
                    version=str(ver or ''),
                    kind='major',
                    notes=str(meta.get('notes') or ''),
                )
        except Exception:
            pass
        return False, 'already armed'

    if cur_status == 'downloading':
        cur_ver = str(cur.get('version') or '')
        if _release_version_tuple(cur_ver) >= _release_version_tuple(ver):
            return False, 'download in progress'

    _arm_ready_major_and_notify(
        rid=rid or 'local-neuron-rar',
        version=ver,
        artifact_path=rar,
        file_hash=file_hash,
        package_size=actual_size,
        piece_size=int(meta.get('piece_size') or _MAJOR_PIECE_SIZE),
        piece_hashes=list(meta.get('piece_hashes') or []),
        ops=meta.get('ops') if isinstance(meta.get('ops'), dict) else None,
        train_reset=train_reset,
        post_apply_version=str(meta.get('post_apply_version') or ver),
        notes=str(meta.get('notes') or ''),
    )
    print(f"[MAJOR_UPGRADE] local Neuron.rar armed for apply: {ver} ({rid})")
    return True, f'armed {ver}'


def queue_pending_adv(version: str, release_id: str, peer_ids: List[str]) -> None:
    """Remember ADV for peers that were offline when we published."""
    if not version or not peer_ids:
        return
    os.makedirs(_releases_dir(), exist_ok=True)
    path = _pending_adv_path()
    data = {'items': []}
    try:
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f) or data
    except Exception:
        data = {'items': []}
    items = list(data.get('items') or [])
    items.append({
        'version': str(version),
        'release_id': str(release_id or ''),
        'peer_ids': [str(p) for p in peer_ids if p],
        'created': time.time(),
    })
    # Cap queue
    data['items'] = items[-200:]
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def flush_pending_adv() -> int:
    """Send queued ADVs to peers that are online now. Returns sends."""
    path = _pending_adv_path()
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f) or {}
    except Exception:
        return 0
    items = list(data.get('items') or [])
    if not items:
        return 0
    from Support.Header import ME, PEERS, CMD_APP_ADV, PRT_NOR
    from Support.Utils import ip_to_send_packet_to
    from Support.SendData import send_packet

    online = {}
    for p in PEERS or []:
        if int(p.get('online') or 0) == 1:
            pid = str(p.get('peer_id') or p.get('id') or '')
            if pid:
                online[pid] = p
    sent = 0
    remain = []
    for item in items:
        left = []
        ver = item.get('version') or ''
        rid = item.get('release_id') or ''
        for pid in item.get('peer_ids') or []:
            peer = online.get(str(pid))
            if not peer:
                left.append(pid)
                continue
            try:
                from Support.ModuleSecurity import build_adv_payload
                send_packet(
                    ME['ip'], ME['ip_local'], '',
                    ip_to_send_packet_to(peer['ip'], peer['ip_local']), peer['ip_local'], '',
                    CMD_APP_ADV, PRT_NOR, '', 0, 0, '',
                    build_adv_payload(ver, rid),
                    [], [], '', '', [], [], []
                )
                sent += 1
            except Exception:
                left.append(pid)
        if left and (time.time() - float(item.get('created') or 0)) < 86400 * 7:
            item['peer_ids'] = left
            remain.append(item)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'items': remain}, f, indent=2)
    return sent


def catalog_needs_piece_swarm(entry: Dict[str, Any]) -> bool:
    if not entry:
        return False
    kind = str(entry.get('kind') or '').lower()
    if kind == 'major':
        return True
    try:
        size = int(entry.get('package_size') or 0)
    except Exception:
        size = 0
    if size > MAJOR_MESH_PKG_MAX_BYTES:
        return True
    if entry.get('file_hash') and entry.get('piece_hashes'):
        return True
    return False


def build_major_sidecar_fields(
    artifact_path: str,
    version: str,
    release_id: str,
    ops: Optional[Dict[str, Any]] = None,
    piece_size: int = _MAJOR_PIECE_SIZE,
) -> Dict[str, Any]:
    if not artifact_path or not os.path.isfile(artifact_path):
        raise FileNotFoundError(artifact_path)
    piece_size = int(piece_size or _MAJOR_PIECE_SIZE)
    file_hasher = hashlib.sha256()
    piece_hashes = []
    size = 0
    with open(artifact_path, 'rb') as f:
        while True:
            block = f.read(piece_size)
            if not block:
                break
            file_hasher.update(block)
            piece_hashes.append(hashlib.sha256(block).hexdigest())
            size += len(block)
    safe_ops = filter_ops_for_safety(ops or default_major_ops())
    return {
        'release_id': release_id,
        'version': version,
        'kind': 'major',
        'file_hash': file_hasher.hexdigest(),
        'package_size': size,
        'piece_size': piece_size,
        'piece_hashes': piece_hashes,
        'artifact_name': os.path.basename(artifact_path),
        'ops': safe_ops,
        'transport': 'piece_swarm',
        'verified': True,
    }


def apply_major_ops(ops: Dict[str, Any], package_root: str, main_path: str = '') -> Tuple[bool, str]:
    main_path = main_path or _main_path()
    if not package_root or not os.path.isdir(package_root):
        return False, "missing package_root"
    safe = filter_ops_for_safety(ops or default_major_ops(), main_path)

    def _src(rel: str) -> str:
        return os.path.join(package_root, rel.replace('/', os.sep))

    def _dst(rel: str) -> str:
        return os.path.join(main_path, rel.replace('/', os.sep))

    try:
        for item in safe.get('overwrite') or []:
            rel = item.get('path') if isinstance(item, dict) else str(item)
            rel = _norm_rel(rel)
            src, dst = _src(rel), _dst(rel)
            if not os.path.isfile(src):
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            print(f"[MAJOR_UPGRADE] overwrite {rel}")

        for item in safe.get('add') or []:
            rel = item.get('path') if isinstance(item, dict) else str(item)
            rel = _norm_rel(rel)
            src, dst = _src(rel), _dst(rel)
            if os.path.exists(dst) or not os.path.isfile(src):
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            print(f"[MAJOR_UPGRADE] add {rel}")

        for item in safe.get('delete') or []:
            rel = item.get('path') if isinstance(item, dict) else str(item)
            rel = _norm_rel(rel)
            if is_database_file(rel, main_path):
                continue
            dst = _dst(rel)
            if os.path.isfile(dst):
                os.remove(dst)
                print(f"[MAJOR_UPGRADE] delete file {rel}")
            elif os.path.isdir(dst):
                try:
                    os.rmdir(dst)
                except OSError:
                    print(f"[MAJOR_UPGRADE] refused non-empty dir delete {rel}")
        return True, "applied"
    except Exception as e:
        return False, str(e)


def guard_deployment_path(path: str) -> bool:
    return mutation_allowed(path, explicit=False)


def _http_range(url: str, start: int, end: int, timeout: int = 60) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, method='GET')
    req.add_header('Range', f'bytes={start}-{end}')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _seed_urls_for_entry(entry: Dict[str, Any]) -> List[str]:
    from Support.Header import ME, PEERS, SYSTEM_ADMIN_IP, SYSTEM_ADMIN_IP_LOCAL
    name = entry.get('artifact_name') or 'Neuron.rar'
    urls = []
    for u in entry.get('seed_urls') or []:
        if u and u not in urls:
            urls.append(str(u))
    hosts = []
    if SYSTEM_ADMIN_IP_LOCAL:
        hosts.append(SYSTEM_ADMIN_IP_LOCAL)
    if SYSTEM_ADMIN_IP:
        hosts.append(SYSTEM_ADMIN_IP)
    for peer in PEERS or []:
        if int(peer.get('online') or 0) != 1:
            continue
        for key in ('ip_local', 'ip'):
            h = (peer.get(key) or '').strip()
            if h:
                hosts.append(h)
    my_local = ''
    try:
        if isinstance(ME, dict):
            my_local = (ME.get('ip_local') or '').strip()
    except Exception:
        pass
    for h in hosts:
        if not h or h == my_local:
            continue
        url = f'http://{h}:12357/{name}'
        if url not in urls:
            urls.append(url)
    return urls


def start_major_piece_swarm(entry: Dict[str, Any]) -> bool:
    rid = str(entry.get('release_id') or '')
    file_hash = str(entry.get('file_hash') or '').lower()
    try:
        size = int(entry.get('package_size') or 0)
    except Exception:
        size = 0
    piece_size = int(entry.get('piece_size') or _MAJOR_PIECE_SIZE)
    piece_hashes = list(entry.get('piece_hashes') or [])
    if not rid or not file_hash or size <= 0:
        print(f"[MAJOR_UPGRADE] swarm refuse incomplete entry {rid}")
        return False

    # Prefer a newer local Promote rar over a stuck older piece_failed swarm.
    try:
        armed, _amsg = try_arm_local_neuron_rar()
        if armed:
            return True
    except Exception:
        pass

    # Tower Promote / prior publish: bytes already in htdocs — same UX as swarm complete.
    try:
        local_rar, local_ver = _local_neuron_rar_paths()
        if os.path.isfile(local_rar) and os.path.getsize(local_rar) == size:
            local_hash = ''
            if os.path.isfile(local_ver):
                try:
                    with open(local_ver, 'r', encoding='utf-8') as f:
                        local_hash = str((json.load(f) or {}).get('file_hash') or '').lower()
                except Exception:
                    local_hash = ''
            if local_hash and local_hash == file_hash:
                _arm_ready_major_and_notify(
                    rid=rid,
                    version=str(entry.get('version') or ''),
                    artifact_path=local_rar,
                    file_hash=file_hash,
                    package_size=size,
                    piece_size=piece_size,
                    piece_hashes=piece_hashes,
                    ops=entry.get('ops') if isinstance(entry.get('ops'), dict) else None,
                )
                print(f"[MAJOR_UPGRADE] skip swarm; local rar matches {rid}")
                return True
    except Exception as e:
        print(f"[MAJOR_UPGRADE] local rar short-circuit warn: {e}")

    with _swarm_lock:
        if _swarm_active.get(rid):
            return True
        _swarm_active[rid] = True

    set_wanted_release({
        'release_id': rid,
        'version': entry.get('version', ''),
        'kind': 'major',
        'file_hash': file_hash,
        'package_size': size,
        'piece_size': piece_size,
        'piece_hashes': piece_hashes,
        'status': 'downloading',
        'ops': filter_ops_for_safety(entry.get('ops') or default_major_ops()),
        'artifact_name': entry.get('artifact_name') or 'Neuron.rar',
    })

    def _worker():
        ui = None
        try:
            try:
                from datetime import datetime, timezone
                from Support.MajorInstall import ApplyProgressUI
                from Support.ModuleSecurity import get_installed_release_version
                have = ''
                try:
                    have = str(get_installed_release_version() or '')
                except Exception:
                    have = ''
                ui = ApplyProgressUI(
                    title='Neuron.rar download',
                    version=str(entry.get('version') or ''),
                    old_version=have,
                    new_version=str(entry.get('version') or ''),
                    timestamp=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
                    modules=['Neuron.rar'],
                )
                ui.update(0.01, 'Connecting to seeds...', 0.0, 'Neuron.rar')
            except Exception as e:
                print(f"[MAJOR_UPGRADE] download UI warn: {e}")
                ui = None
            staging = os.path.join(_releases_dir(), 'staging')
            os.makedirs(staging, exist_ok=True)
            dest = os.path.join(staging, rid + '.major.bin')
            part = dest + '.partial'
            urls = _seed_urls_for_entry(entry)
            if not urls:
                print(f"[MAJOR_UPGRADE] no seed URLs for {rid}")
                set_wanted_release({**get_wanted_release(), 'status': 'no_seeds'})
                return
            if not os.path.isfile(part) or os.path.getsize(part) != size:
                with open(part, 'wb') as f:
                    f.truncate(size)
            n_pieces = (size + piece_size - 1) // piece_size
            for i in range(n_pieces):
                start = i * piece_size
                end = min(start + piece_size, size) - 1
                expected = (piece_hashes[i].lower() if i < len(piece_hashes) else '')
                got = None
                for url in urls:
                    try:
                        got = _http_range(url, start, end, timeout=120)
                        if expected and hashlib.sha256(got).hexdigest().lower() != expected:
                            got = None
                            continue
                        break
                    except Exception:
                        got = None
                if not got:
                    print(f"[MAJOR_UPGRADE] piece {i}/{n_pieces} failed for {rid}")
                    set_wanted_release({**get_wanted_release(), 'status': 'piece_failed', 'piece': i})
                    return
                with open(part, 'r+b') as f:
                    f.seek(start)
                    f.write(got)
                if ui is not None and n_pieces:
                    frac = float(i + 1) / float(n_pieces)
                    try:
                        ui.update(
                            frac * 0.95,
                            'Downloading Neuron.rar (%s/%s)...' % (i + 1, n_pieces),
                            frac,
                            'piece %s/%s' % (i + 1, n_pieces),
                        )
                    except Exception:
                        pass
            h = hashlib.sha256()
            with open(part, 'rb') as f:
                while True:
                    block = f.read(1024 * 1024)
                    if not block:
                        break
                    h.update(block)
            if h.hexdigest().lower() != file_hash:
                set_wanted_release({**get_wanted_release(), 'status': 'hash_mismatch'})
                return
            os.replace(part, dest)
            try:
                from Support.Header import PATH_HTDOCS, PATH_DATABASE
                art = entry.get('artifact_name') or 'Neuron.rar'
                htdocs = str(PATH_HTDOCS or '').strip() or str(PATH_DATABASE or '').strip()
                if art == 'Neuron.rar' and htdocs:
                    final = os.path.join(htdocs, 'Neuron.rar')
                    shutil.copy2(dest, final)
                    with open(final + '.version.json', 'w', encoding='utf-8') as f:
                        json.dump({
                            'version': entry.get('version', ''),
                            'file_hash': file_hash,
                            'piece_size': piece_size,
                            'piece_hashes': piece_hashes,
                            'size': size,
                            'ops': filter_ops_for_safety(entry.get('ops') or default_major_ops()),
                            'release_id': rid,
                        }, f, indent=2)
            except Exception as e:
                print(f"[MAJOR_UPGRADE] htdocs publish warn: {e}")
            sc_path = os.path.join(_releases_dir(), rid + '.json')
            try:
                sc = {}
                if os.path.isfile(sc_path):
                    with open(sc_path, 'r', encoding='utf-8') as f:
                        sc = json.load(f) or {}
                sc.update({
                    'release_id': rid,
                    'version': entry.get('version', ''),
                    'kind': 'major',
                    'file_hash': file_hash,
                    'package_size': size,
                    'piece_size': piece_size,
                    'piece_hashes': piece_hashes,
                    'artifact_path': dest,
                    'ops': filter_ops_for_safety(entry.get('ops') or default_major_ops()),
                    'verified': True,
                    'downloaded': True,
                    'transport': 'piece_swarm',
                })
                with open(sc_path, 'w', encoding='utf-8') as f:
                    json.dump(sc, f, indent=2)
            except Exception as e:
                print(f"[MAJOR_UPGRADE] sidecar write warn: {e}")
            try:
                from Support.ModuleSecurity import advertise_release_to_peers
                advertise_release_to_peers(str(entry.get('version') or ''), rid)
            except Exception:
                pass
            _arm_ready_major_and_notify(
                rid=rid,
                version=str(entry.get('version') or ''),
                artifact_path=dest,
                file_hash=file_hash,
                package_size=size,
                piece_size=piece_size,
                piece_hashes=piece_hashes,
                ops=entry.get('ops') if isinstance(entry.get('ops'), dict) else None,
            )
            if ui is not None:
                try:
                    ui.update(1.0, 'Download complete', 1.0, 'Neuron.rar')
                except Exception:
                    pass
            print(f"[MAJOR_UPGRADE] swarm complete {rid} ({size} bytes)")
        finally:
            if ui is not None:
                try:
                    ui.close()
                except Exception:
                    pass
            with _swarm_lock:
                _swarm_active.pop(rid, None)

    threading.Thread(target=_worker, name=f'major-swarm-{rid[:8]}', daemon=True).start()
    return True


def _apply_major_rar_inplace(
    artifact: str, version: str = '', old_version: str = '', notes: str = ''
) -> Tuple[bool, str]:
    """
    Park user data, extract Neuron.rar over MAIN_PATH, restore parked trees.
    Peer-safe: uses Support.MajorInstall (no ADMIN.NeuronSetup dependency).
    """
    from datetime import datetime, timezone
    from Support.Header import MAIN_PATH
    from Support.MajorInstall import ApplyProgressUI, apply_neuron_rar
    from Support.ModuleSecurity import get_installed_release_version

    neuron_root = os.path.normpath(MAIN_PATH or '')
    have = str(old_version or '')
    if not have:
        try:
            have = str(get_installed_release_version() or '')
        except Exception:
            have = ''
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    ui = ApplyProgressUI(
        title='Neuron major update',
        version=version or '',
        old_version=have,
        new_version=version or '',
        timestamp=stamp,
        notes=notes or '',
        neuron_root=neuron_root,
    )
    try:
        def _cb(frac, msg, file_frac=None, current_file=''):
            ui.update(frac, msg, file_fraction=file_frac, current_file=current_file)
        ok, msg = apply_neuron_rar(artifact, neuron_root, progress=_cb)
        # Offline relauncher applies then relaunches — show the 10s restart gate.
        # Startup in-process apply continues into the same session (no countdown).
        offline = str(os.environ.get('NEURON_OFFLINE_APPLY') or '').strip() in (
            '1', 'true', 'yes'
        )
        if ok and offline:
            try:
                ui.show_restart_countdown(version=version or '', seconds=10)
            except Exception:
                pass
        return ok, msg
    finally:
        ui.close()


def apply_ready_major_if_any(force_inplace: bool = False) -> Tuple[bool, str]:
    """
    Complete a downloaded major.
    - Optional ops tree ({rid}_files): apply immediately.
    - Neuron.rar / .major.bin: extract with park/restore when force_inplace,
      NEURON_OFFLINE_APPLY=1 (relauncher), or startup (no conductor UI yet).
      Otherwise defer (keep wanted) until restart/offline apply — never wipe DBs.
    """
    wanted = get_wanted_release()
    if not wanted or wanted.get('status') != 'ready_to_apply':
        return False, "nothing ready"
    if str(wanted.get('kind') or '').lower() != 'major':
        return False, "not major"
    try:
        from Support.ModuleSecurity import (
            get_installed_release_version, _release_version_tuple,
        )
        have = str(get_installed_release_version() or '')
        want = str(wanted.get('version') or '')
        if (
            have and want
            and _release_version_tuple(have) >= _release_version_tuple(want)
            and not bool(wanted.get('train_reset'))
        ):
            clear_wanted_release()
            return False, "already at %s" % have
    except Exception:
        pass
    rid = wanted.get('release_id') or ''
    files_root = os.path.join(_releases_dir(), 'staging', rid + '_files')
    artifact = wanted.get('artifact_path') or os.path.join(
        _releases_dir(), 'staging', rid + '.major.bin'
    )
    if not os.path.isfile(artifact) and not os.path.isdir(files_root):
        print(f"[MAJOR_UPGRADE] ready major missing artifact {artifact}")
        return False, "artifact missing"

    ok, msg = True, "artifact ready"
    if os.path.isdir(files_root):
        ok, msg = apply_major_ops(wanted.get('ops') or default_major_ops(), files_root)
        if not ok:
            return False, msg
    elif os.path.isfile(artifact):
        offline = str(os.environ.get('NEURON_OFFLINE_APPLY') or '').strip() in ('1', 'true', 'yes')
        startup = False
        try:
            import Support.Header as _hdr
            startup = not bool(getattr(_hdr, 'conductor_visible', False))
        except Exception:
            startup = True
        if not (force_inplace or offline or startup):
            # Keep package ready; show green ticker / countdown now that UI exists.
            try:
                from Support.ModuleSecurity import _notify_release_restart
                _notify_release_restart(
                    force_countdown=False,
                    version=str(wanted.get('version') or ''),
                    kind='major',
                )
            except Exception:
                pass
            return False, "deferred_until_restart"
        ok, msg = _apply_major_rar_inplace(
            artifact,
            version=str(wanted.get('version') or ''),
            notes=str(wanted.get('notes') or ''),
        )
        if not ok:
            return False, msg

    clear_wanted_release()
    try:
        from Support.ModuleSecurity import (
            update_systeminfo_version, advertise_release_to_peers, save_release_sidecar,
            load_release_sidecar, mark_release_installed, clear_install_history,
        )
        final_ver = str(wanted.get('version') or '')
        if bool(wanted.get('train_reset')):
            try:
                clear_install_history()
            except Exception as e:
                print(f"[MAJOR_UPGRADE] clear history warn: {e}")
            final_ver = str(wanted.get('post_apply_version') or final_ver or '0.0.0')
        if final_ver:
            update_systeminfo_version(final_ver)
        if rid:
            sc = load_release_sidecar(rid) or {}
            sc.update({
                'release_id': rid,
                'version': final_ver or wanted.get('version', ''),
                'kind': 'major',
                'notes': sc.get('notes') or wanted.get('notes') or 'Major Neuron.rar',
                'downloaded': True,
                'verified': True,
                'transport': 'piece_swarm',
                'artifact_path': artifact,
                'file_hash': wanted.get('file_hash', sc.get('file_hash', '')),
                'package_size': wanted.get('package_size', sc.get('package_size', 0)),
                'piece_hashes': wanted.get('piece_hashes', sc.get('piece_hashes', [])),
                'piece_size': wanted.get('piece_size', sc.get('piece_size', 0)),
                'train_reset': bool(wanted.get('train_reset')),
            })
            save_release_sidecar(rid, sc)
            mark_release_installed(
                rid,
                version=final_ver or wanted.get('version', ''),
                package_size=int(wanted.get('package_size') or sc.get('package_size') or 0),
            )
        if final_ver:
            advertise_release_to_peers(str(final_ver), rid)
    except Exception as e:
        print(f"[MAJOR_UPGRADE] finalize warn: {e}")
    return True, msg


def should_amplify_adv(release_id: str, cooldown_sec: float = 120.0) -> bool:
    """True once per release_id within cooldown — for gossip rebroadcast."""
    rid = str(release_id or '')
    if not rid:
        return False
    now = time.monotonic()
    with _adv_amplify_lock:
        last = float(_adv_amplified.get(rid) or 0)
        if now - last < cooldown_sec:
            return False
        _adv_amplified[rid] = now
        if len(_adv_amplified) > 500:
            cutoff = now - 3600
            for k in list(_adv_amplified.keys()):
                if _adv_amplified[k] < cutoff:
                    _adv_amplified.pop(k, None)
        return True
