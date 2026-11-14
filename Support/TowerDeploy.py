"""
Tower hub deploy helpers for AdminConsole.

Config: System/json/tower_deploy.json
  mapped_root   - UNC/mapped path to Tower Neuron tree (e.g. Z:\\)
  tower_peer_id - optional Peers.id for preferential seed
  tower_lan_ip  - Tower LAN IP (default 192.168.1.110)
  extra_paths   - relative paths under MAIN_PATH always considered for patches
  shipped_hashes - path -> sha256 of last published extra
"""

import hashlib
import json
import os
import shutil
from typing import Any, Dict, List, Optional, Tuple


_REFUSED_PREFIXES = (
    'system/neuron/data',
    'user/',
    'system/json/',
    'system/bak/',
    'system/old/',
    'system/logs/',
    'admin/keys',
)
_REFUSED_SUFFIXES = ('.db', '.sqlite', '.sqlite3', '.secret', '.pem', '.key')


def _config_path() -> str:
    from Support.Header import MAIN_PATH
    return os.path.join(MAIN_PATH, 'System', 'json', 'tower_deploy.json')


def default_config() -> Dict[str, Any]:
    return {
        'mapped_root': 'Z:\\',
        'tower_peer_id': '',
        'tower_lan_ip': '192.168.1.110',
        'tower_wan_ip': '93.149.186.223',
        'extra_paths': [],
        'shipped_hashes': {},
    }


def load_tower_deploy_config() -> Dict[str, Any]:
    cfg = default_config()
    path = _config_path()
    if not os.path.isfile(path):
        return cfg
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f) or {}
        if isinstance(data, dict):
            cfg.update(data)
    except Exception:
        pass
    if not isinstance(cfg.get('extra_paths'), list):
        cfg['extra_paths'] = []
    if not isinstance(cfg.get('shipped_hashes'), dict):
        cfg['shipped_hashes'] = {}
    return cfg


def save_tower_deploy_config(cfg: Dict[str, Any]) -> str:
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)
    return path


def normalize_rel_path(rel: str) -> str:
    return str(rel or '').replace('\\', '/').strip().lstrip('/')


def is_safe_extra_path(rel: str) -> bool:
    rel_n = normalize_rel_path(rel).lower()
    if not rel_n or '..' in rel_n.split('/'):
        return False
    for pref in _REFUSED_PREFIXES:
        if rel_n == pref.rstrip('/') or rel_n.startswith(pref):
            return False
    base = os.path.basename(rel_n)
    for suf in _REFUSED_SUFFIXES:
        if base.endswith(suf):
            return False
    return True


def absolute_extra_path(rel: str) -> str:
    from Support.Header import MAIN_PATH
    rel_n = normalize_rel_path(rel).replace('/', os.sep)
    return os.path.join(MAIN_PATH, rel_n)


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def mapped_root_ready(cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    cfg = cfg or load_tower_deploy_config()
    root = str(cfg.get('mapped_root') or '').strip()
    if not root:
        return False, 'mapped_root not set'
    if not os.path.isdir(root):
        return False, f'mapped_root offline or missing: {root}'
    return True, root


def user_errors_log_candidates(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """Local helpdesk log first (AdminConsole PC); optional Tower mapped path second."""
    from Support.Header import PATH_LOGS
    cfg = cfg or load_tower_deploy_config()
    paths = [os.path.join(PATH_LOGS, 'USER_ERRORS.log')]
    ok, root = mapped_root_ready(cfg)
    if ok:
        paths.append(os.path.join(root, 'System', 'logs', 'USER_ERRORS.log'))
    out = []
    seen = set()
    for p in paths:
        try:
            key = os.path.normcase(os.path.abspath(p))
        except Exception:
            key = p
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def pick_user_errors_log_path(cfg: Optional[Dict[str, Any]] = None) -> str:
    """Prefer an existing non-empty log; else first candidate (usually local)."""
    candidates = user_errors_log_candidates(cfg)
    best = ''
    best_size = -1
    for path in candidates:
        try:
            if os.path.isfile(path):
                size = os.path.getsize(path)
                if size > best_size:
                    best = path
                    best_size = size
        except Exception:
            continue
    if best:
        return best
    return candidates[0] if candidates else ''


def dirty_extra_files(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """Return extras that exist and differ from last shipped hash."""
    cfg = cfg or load_tower_deploy_config()
    shipped = cfg.get('shipped_hashes') or {}
    dirty = []
    for rel in cfg.get('extra_paths') or []:
        rel_n = normalize_rel_path(rel)
        if not is_safe_extra_path(rel_n):
            continue
        abs_path = absolute_extra_path(rel_n)
        if not os.path.isfile(abs_path):
            continue
        digest = file_sha256(abs_path)
        if shipped.get(rel_n) == digest:
            continue
        dirty.append({'rel': rel_n, 'abs': abs_path, 'sha256': digest})
    return dirty


def build_send_files_actions(dirty: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    if not dirty:
        return []
    return [{
        'type': 'Send Files',
        'send_files': [d['abs'] for d in dirty],
        'destination_paths': [d['rel'].replace('/', os.sep) for d in dirty],
        'note': 'Auto extras for Tower patch',
    }]


def mark_extras_shipped(dirty: List[Dict[str, str]], cfg: Optional[Dict[str, Any]] = None) -> None:
    cfg = cfg or load_tower_deploy_config()
    shipped = dict(cfg.get('shipped_hashes') or {})
    for d in dirty:
        shipped[d['rel']] = d['sha256']
    cfg['shipped_hashes'] = shipped
    save_tower_deploy_config(cfg)


def find_tower_peer(available_peers: List[Dict[str, Any]],
                    cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    cfg = cfg or load_tower_deploy_config()
    peer_id = str(cfg.get('tower_peer_id') or '').strip()
    lan = str(cfg.get('tower_lan_ip') or '192.168.1.110').strip()
    wan = str(cfg.get('tower_wan_ip') or '').strip()
    for peer in available_peers or []:
        if peer_id and str(peer.get('id') or '') == peer_id:
            return peer
    for peer in available_peers or []:
        if str(peer.get('ip_local') or '').strip() == lan:
            return peer
        if wan and str(peer.get('ip') or '').strip() == wan:
            name = str(peer.get('node_name') or peer.get('name') or '').lower()
            if 'tower' in name or lan == str(peer.get('ip_local') or '').strip():
                return peer
    for peer in available_peers or []:
        name = str(peer.get('node_name') or peer.get('name') or '').lower()
        if 'tower' in name:
            return peer
    return None


def prefer_tower_in_seed_list(peers: List[Dict[str, Any]],
                             cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    tower = find_tower_peer(peers, cfg)
    if not tower:
        return list(peers or [])
    tid = tower.get('id')
    rest = [p for p in (peers or []) if p.get('id') != tid]
    return [tower] + rest


def peer_versions_path() -> str:
    from Support.Header import MAIN_PATH
    return os.path.join(MAIN_PATH, 'System', 'json', 'peer_release_versions.json')


def load_peer_release_versions() -> Dict[str, Any]:
    path = peer_versions_path()
    if not os.path.isfile(path):
        return {'peers': {}}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f) or {}
        if not isinstance(data, dict):
            return {'peers': {}}
        if not isinstance(data.get('peers'), dict):
            data['peers'] = {}
        return data
    except Exception:
        return {'peers': {}}


def remember_peer_release_version(ip: str, ip_local: str, version: str) -> None:
    """Cache peer SystemInfo release version from CMD_SRV_USA (no DB schema change)."""
    from datetime import datetime, timezone
    ip_s = str(ip or '').strip()
    loc_s = str(ip_local or '').strip()
    ver = str(version or '').strip()
    if not ip_s or not loc_s or not ver:
        return
    data = load_peer_release_versions()
    peers = data.setdefault('peers', {})
    key = f'{ip_s}|{loc_s}'
    peers[key] = {
        'ip': ip_s,
        'ip_local': loc_s,
        'version': ver,
        'updated_utc': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
    }
    path = peer_versions_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def list_peer_release_versions() -> List[Dict[str, str]]:
    data = load_peer_release_versions()
    rows = list((data.get('peers') or {}).values())
    rows.sort(key=lambda r: (str(r.get('version') or ''), str(r.get('ip') or '')))
    return rows


def copy_to_mapped_tower(rel_or_abs_src: str, rel_dest: str,
                         cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    ok, root = mapped_root_ready(cfg)
    if not ok:
        return False, root
    rel_n = normalize_rel_path(rel_dest)
    if not rel_n:
        return False, 'empty dest'
    dest = os.path.join(root, rel_n.replace('/', os.sep))
    src = rel_or_abs_src
    if not os.path.isabs(src):
        src = absolute_extra_path(src)
    if not os.path.isfile(src):
        return False, f'source missing: {src}'
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(src, dest)
    return True, dest


def dispatch_compiled_pyd_to_tower(abs_pyd: str, rel_dir: str, module_name: str,
                                   cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """Copy a compiled module .pyd into mapped Tower at the same relative path."""
    rel_dir_n = normalize_rel_path(rel_dir)
    if rel_dir_n in ('.', ''):
        rel_dest = f'{module_name}.pyd'
    else:
        rel_dest = f'{rel_dir_n}/{module_name}.pyd'
    return copy_to_mapped_tower(abs_pyd, rel_dest, cfg=cfg)


def promote_release_files_to_prod(release_id: str) -> List[str]:
    """Mirror release artifacts into Neuron_PROD\\System\\Releases."""
    from Support.Header import PATH_DISTRIBUTABLE_RELEASES, PATH_INSTALLATION_FOLDER
    messages = []
    prod = str(PATH_INSTALLATION_FOLDER or '').rstrip('\\/')
    if not prod or not os.path.isdir(prod):
        messages.append('Neuron_PROD missing')
        return messages
    dest_dir = os.path.join(prod, 'System', 'Releases')
    os.makedirs(dest_dir, exist_ok=True)
    for name in (release_id + '.nrel', release_id + '.json', release_id + '.zip'):
        src = os.path.join(PATH_DISTRIBUTABLE_RELEASES, name)
        if not os.path.isfile(src):
            continue
        dest = os.path.join(dest_dir, name)
        shutil.copy2(src, dest)
        messages.append(f'PROD copied {name}')
    return messages


def promote_release_files_to_tower(release_id: str, cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    from Support.Header import PATH_DISTRIBUTABLE_RELEASES
    cfg = cfg or load_tower_deploy_config()
    messages = []
    try:
        messages.extend(promote_release_files_to_prod(release_id))
    except Exception as e:
        messages.append(f'PROD warn: {e}')
    ok, root = mapped_root_ready(cfg)
    if not ok:
        messages.append(root)
        return messages
    for name in (release_id + '.nrel', release_id + '.json', release_id + '.zip'):
        src = os.path.join(PATH_DISTRIBUTABLE_RELEASES, name)
        if not os.path.isfile(src):
            continue
        dest = os.path.join(root, 'System', 'Releases', name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest)
        messages.append(f'copied {name}')
    return messages


_FORCE_RAR_ARM_NAME = 'FORCE_LOCAL_RAR_ARM.flag'


def force_local_rar_arm_flag_path(root: str = '') -> str:
    from Support.Header import PATH_DISTRIBUTABLE_RELEASES, MAIN_PATH
    if root:
        return os.path.join(root, 'System', 'Releases', _FORCE_RAR_ARM_NAME)
    base = str(PATH_DISTRIBUTABLE_RELEASES or '').rstrip('\\/')
    if not base:
        base = os.path.join(str(MAIN_PATH or ''), 'System', 'Releases')
    return os.path.join(base, _FORCE_RAR_ARM_NAME)


def request_force_local_rar_arm(also_mapped_tower: bool = True,
                               cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """
    Drop FORCE_LOCAL_RAR_ARM.flag so the next maintenance tick (~25s) arms
    Neuron.rar without waiting for the hourly APP_UPDATE cycle.
    Also writes the flag onto mapped Tower (Z:) when online.
    """
    messages = []
    paths = [force_local_rar_arm_flag_path()]
    if also_mapped_tower:
        ok, root = mapped_root_ready(cfg)
        if ok:
            paths.append(force_local_rar_arm_flag_path(root))
        else:
            messages.append(f'Tower flag skip: {root}')
    for path in paths:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write('1\n')
            messages.append(f'force-arm flag: {path}')
        except Exception as e:
            messages.append(f'force-arm flag fail {path}: {e}')
    return messages


def consume_force_local_rar_arm_flag() -> bool:
    """True if a force-arm flag was present (deleted)."""
    path = force_local_rar_arm_flag_path()
    if not path or not os.path.isfile(path):
        return False
    try:
        os.remove(path)
    except Exception:
        pass
    return True


def promote_neuron_rar_to_tower(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    from Support.Header import MAIN_PATH
    cfg = cfg or load_tower_deploy_config()
    messages = []
    ok, root = mapped_root_ready(cfg)
    if not ok:
        messages.append(root)
        return messages
    rar_src = os.path.join(MAIN_PATH, 'System', 'neuron', 'htdocs', 'Neuron.rar')
    if not os.path.isfile(rar_src):
        messages.append('Neuron.rar not found in local htdocs')
        return messages
    dest_dir = os.path.join(root, 'System', 'neuron', 'htdocs')
    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(rar_src, os.path.join(dest_dir, 'Neuron.rar'))
    messages.append('copied Neuron.rar')
    ver_src = rar_src + '.version.json'
    if os.path.isfile(ver_src):
        shutil.copy2(ver_src, os.path.join(dest_dir, 'Neuron.rar.version.json'))
        messages.append('copied Neuron.rar.version.json')
    # Also copy matching major sidecar(s) if present under Releases
    try:
        with open(ver_src, 'r', encoding='utf-8') as f:
            ver = json.load(f) or {}
        rid = str(ver.get('release_id') or '')
        if rid:
            messages.extend(promote_release_files_to_tower(rid, cfg))
    except Exception as e:
        messages.append(f'version sidecar warn: {e}')
    try:
        messages.extend(request_force_local_rar_arm(also_mapped_tower=True, cfg=cfg))
    except Exception as e:
        messages.append(f'force-arm warn: {e}')
    return messages
