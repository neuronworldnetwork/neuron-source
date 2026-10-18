# NeuronNeuryAskParallel.py
# Wave / DAG helpers for multi-ask projects: run independent steps together,
# wait on producers, prefer capable peer Neurys, fall back local.


import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

# Serialize only invite/recruit/distribute — wait_for_results may overlap.
# Parallel waves share peers up to _MESH_MAX_PER_PEER concurrent hires each.
_MESH_START_LOCK = threading.Lock()
_MESH_BUSY_LOCK = threading.Lock()
_MESH_BUSY_PEERS: Set[Tuple[str, str]] = set()
# Concurrent hires per peer from this requester (peer_processor allows 3).
_MESH_PEER_LOAD: Dict[Tuple[str, str], int] = {}
_MESH_MAX_PER_PEER = 2
_PEER_RR_LOCK = threading.Lock()
_PEER_RR = 0
_PEER_HIRE_COUNTS: Dict[Tuple[str, str], int] = {}


# Tools that may run concurrently in a wave (no UI / no user.confirm).
_PARALLEL_SAFE = frozenset({
    'hive.run_job',
    'files.classify_batch',
    'text.translate',
    'calendar.remind',
})

# Soft producers: later steps that consume produced[] wait for these.
_MEDIA_PRODUCER_CAPS = frozenset({
    'video_convert', 'photo_batch', 'enlarge_photos', 'ocr', 'captions',
    'voiceover', 'translate_hive',
})

# Highlights page needs media bits, not the Italian intro translation.
_WEBSITE_MEDIA_CAPS = frozenset({
    'video_convert', 'photo_batch', 'enlarge_photos', 'ocr', 'captions', 'voiceover',
})

_CTX_KEY_RE = re.compile(r'\$([A-Za-z_][A-Za-z0-9_]*)')


def _step_id(step: Dict[str, Any], index: int) -> str:
    return str(step.get('id') or ('s%s' % (index + 1)))


def _cap_id(step: Dict[str, Any]) -> str:
    return str(step.get('cap_id') or '')


def _refs_in_args(args: Any) -> Set[str]:
    found: Set[str] = set()
    if isinstance(args, str):
        found.update(_CTX_KEY_RE.findall(args))
        if args.startswith('$') and len(args) > 1:
            found.add(args[1:])
    elif isinstance(args, list):
        for item in args:
            found.update(_refs_in_args(item))
    elif isinstance(args, dict):
        for val in args.values():
            found.update(_refs_in_args(val))
    return found


def _produces_keys(step: Dict[str, Any]) -> Set[str]:
    """Context keys this step is expected to fill."""
    tool = str(step.get('tool') or '')
    cap = _cap_id(step)
    out: Set[str] = set()
    if tool == 'files.classify_batch' or cap == 'classify_inputs':
        out.update({
            'paths', 'produced', 'videos', 'photos', 'receipts', 'documents',
            'audio', 'video_paths', 'photo_paths', 'receipt_paths', 'document_paths',
        })
    if cap in _MEDIA_PRODUCER_CAPS or tool == 'hive.run_job':
        out.update({'produced', 'paths'})
        jid = str((step.get('args') or {}).get('job_id') or '')
        if jid in ('video_convert', 'captions') or cap in ('video_convert', 'captions'):
            out.add('video_paths')
        if jid in ('photo_batch', 'enlarge_photos') or cap in ('photo_batch', 'enlarge_photos'):
            out.add('photo_paths')
        if jid == 'ocr' or cap == 'ocr':
            out.update({'receipt_paths', 'document_paths'})
        if jid == 'voiceover' or cap == 'voiceover':
            out.add('audio')
        if jid == 'translate' or cap == 'translate_hive':
            out.update({'document_paths', 'produced'})
    if tool in ('website.build', 'websites.publish') or cap in ('website_build', 'website_publish'):
        out.update({'website_name', 'website_path', 'path'})
    if tool == 'files.commit_outputs' or cap == 'commit_outputs':
        out.add('produced')
    return out


def annotate_step_dependencies(steps: Optional[Sequence[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    Attach depends_on (list of step ids) using $refs + capability producer rules.
    Mutates and returns the same step dicts.

    Two-pass: discover classify/media ids first so media never starts before
    sort-files (order-independent). Independent hive jobs share only the
    classify edge so the mesh can fan out after sort.
    """
    steps = list(steps or [])
    producers: Dict[str, str] = {}  # context key -> step id that produces it
    classify_id = ''
    media_ids: List[str] = []
    website_id = ''
    publish_id = ''
    voiceover_id = ''
    captions_id = ''
    # Pass 1 — assign ids + discover producers / classify / media.
    for i, step in enumerate(steps):
        sid = _step_id(step, i)
        step['id'] = sid
        cap = _cap_id(step)
        tool = str(step.get('tool') or '')
        if cap == 'classify_inputs' or tool == 'files.classify_batch':
            classify_id = sid
        if cap in _MEDIA_PRODUCER_CAPS or (
            tool == 'hive.run_job' and cap not in ('website_build',)
        ):
            media_ids.append(sid)
        if cap == 'voiceover':
            voiceover_id = sid
        if cap == 'captions':
            captions_id = sid
        if cap == 'website_build' or tool == 'website.build':
            website_id = sid
        if cap == 'website_publish' or tool == 'websites.publish':
            publish_id = sid
        for key in _produces_keys(step):
            producers.setdefault(key, sid)
    # Pass 2 — wire depends_on with classify known up front.
    for i, step in enumerate(steps):
        sid = _step_id(step, i)
        deps: Set[str] = set()
        cap = _cap_id(step)
        tool = str(step.get('tool') or '')
        refs = _refs_in_args(step.get('args') or {})
        for key in refs:
            prev = producers.get(key)
            if prev and prev != sid:
                deps.add(prev)
        # Every media / hive job waits for classify when present.
        if sid != classify_id and classify_id and (
            cap in _MEDIA_PRODUCER_CAPS or tool == 'hive.run_job'
        ):
            deps.add(classify_id)
        # "Translate that intro" waits on voiceover only; captions is fallback.
        if cap == 'translate_hive':
            if voiceover_id:
                deps.add(voiceover_id)
            elif captions_id:
                deps.add(captions_id)
        if cap in ('website_build', 'website_publish') or tool in ('website.build', 'websites.publish'):
            for mid in media_ids:
                other = next((s for s in steps if str(s.get('id') or '') == mid), None)
                if other and _cap_id(other) in _WEBSITE_MEDIA_CAPS:
                    deps.add(mid)
            if classify_id:
                deps.add(classify_id)
        if cap == 'website_publish' or tool == 'websites.publish':
            if website_id:
                deps.add(website_id)
        # "Tell them it's ready" follows publish when present, else the build.
        if cap == 'chat_notify' or tool == 'chat.send':
            if publish_id:
                deps.add(publish_id)
            elif website_id:
                deps.add(website_id)
        if cap == 'commit_outputs' or tool == 'files.commit_outputs':
            for mid in media_ids:
                deps.add(mid)
            if website_id:
                deps.add(website_id)
        # Trip diary waits for media so the note is about results, not a raw dump mid-mesh.
        if cap == 'diary_post' or tool == 'diary.post':
            for mid in media_ids:
                deps.add(mid)
            if classify_id:
                deps.add(classify_id)
        step['depends_on'] = sorted(deps)
    # Pass 3 — confirms guard the *next* step (composer inserts confirm before action).
    # Without this, diary/chat/publish ran while their Yes/No row stayed PENDING.
    for i, step in enumerate(steps):
        tool = str(step.get('tool') or '')
        if tool == 'user.confirm' and i + 1 < len(steps):
            nxt = steps[i + 1]
            inherited = set(nxt.get('depends_on') or [])
            inherited.discard(_step_id(step, i))
            step['depends_on'] = sorted(set(step.get('depends_on') or []) | inherited)
        if i > 0 and str(steps[i - 1].get('tool') or '') == 'user.confirm':
            conf_id = _step_id(steps[i - 1], i - 1)
            step['depends_on'] = sorted(set(step.get('depends_on') or []) | {conf_id})
    return steps


def _deps_satisfied(step: Dict[str, Any], by_id: Dict[str, Dict[str, Any]]) -> bool:
    for dep in step.get('depends_on') or []:
        other = by_id.get(str(dep))
        if other is None:
            continue
        st = str(other.get('status') or 'pending')
        if st not in ('done', 'skipped', 'blocked'):
            return False
    return True


def next_parallel_wave(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return pending steps whose dependencies are satisfied (wave)."""
    steps = list(project.get('steps') or [])
    if not any(s.get('depends_on') is not None for s in steps):
        annotate_step_dependencies(steps)
    by_id = {_step_id(s, i): s for i, s in enumerate(steps)}
    # Hard gate: while sort-files is still running, start nothing else
    # (diary/reminder used to race ahead and freeze the board at Sort 7%).
    for s in steps:
        tool = str(s.get('tool') or '')
        cap = _cap_id(s)
        if tool == 'files.classify_batch' or cap == 'classify_inputs':
            st = str(s.get('status') or '')
            if st == 'running':
                return []
            if st == 'pending':
                return [s]
    wave = []
    for i, step in enumerate(steps):
        if str(step.get('status') or '') != 'pending':
            continue
        if not _deps_satisfied(step, by_id):
            continue
        wave.append(step)
    return wave


def step_is_parallel_safe(step: Dict[str, Any]) -> bool:
    tool = str(step.get('tool') or '')
    if tool == 'user.confirm':
        return False
    return tool in _PARALLEL_SAFE


def split_wave_for_execution(wave: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Parallel-safe hive/file steps vs must-run-serial (confirm / UI / send).
    Serial items in a wave run one-by-one after the parallel batch (or alone).
    """
    parallel = [s for s in wave if step_is_parallel_safe(s)]
    serial = [s for s in wave if not step_is_parallel_safe(s)]
    return parallel, serial


def peer_colors(count: int) -> List[str]:
    """Distinct neon tracks for remote Neury progress bars."""
    palette = (
        '#7ad7ef', '#9fe0a8', '#ffe08a', '#ff9f6b', '#c9a0ff',
        '#6ee7b7', '#f472b6', '#38bdf8', '#fbbf24', '#a3e635',
    )
    n = max(1, int(count or 1))
    return [palette[i % len(palette)] for i in range(n)]


def export_format_for_hive_job(job_id: str, mesh_type: str = '') -> str:
    """Peer-safe output extension for this catalog / mesh job."""
    jid = (job_id or '').strip().lower()
    mesh = (mesh_type or '').strip().lower()
    by_id = {
        'photo_batch': 'jpg',
        'enlarge_photos': 'png',
        'video_convert': 'mp4',
        'captions': 'srt',
        'voiceover': 'wav',
        'ocr': 'txt',
        'translate': 'txt',
        'documents': 'txt',
    }
    by_mesh = {
        'image_batch': 'jpg',
        'image_upscale': 'png',
        'media_transcode': 'mp4',
        'document_ocr': 'txt',
        'translation': 'txt',
        'voiceover': 'wav',
        'captions': 'srt',
    }
    return by_id.get(jid) or by_mesh.get(mesh) or 'bin'


def _peer_ip_key(peer: Dict[str, Any]) -> Tuple[str, str]:
    return (str(peer.get('ip') or ''), str(peer.get('ip_local') or ''))


def busy_mesh_peer_keys() -> Set[Tuple[str, str]]:
    """Peers at hire capacity (not merely busy with one job)."""
    with _MESH_BUSY_LOCK:
        full = {
            k for k, n in _MESH_PEER_LOAD.items()
            if int(n or 0) >= int(_MESH_MAX_PER_PEER)
        }
        # Legacy set still blocks fully-saturated keys.
        return full | {
            k for k in _MESH_BUSY_PEERS
            if int(_MESH_PEER_LOAD.get(k, 0)) >= int(_MESH_MAX_PER_PEER)
        }


def reserve_mesh_peers(peer_list: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Claim free peers for one hive job; returns the subset successfully reserved."""
    taken: List[Dict[str, Any]] = []
    with _MESH_BUSY_LOCK:
        for peer in peer_list or []:
            key = _peer_ip_key(peer)
            if not key[0]:
                continue
            load = int(_MESH_PEER_LOAD.get(key, 0))
            if load >= int(_MESH_MAX_PER_PEER):
                continue
            _MESH_PEER_LOAD[key] = load + 1
            if _MESH_PEER_LOAD[key] >= int(_MESH_MAX_PER_PEER):
                _MESH_BUSY_PEERS.add(key)
            taken.append(peer)
            _PEER_HIRE_COUNTS[key] = int(_PEER_HIRE_COUNTS.get(key, 0)) + 1
    return taken


def claim_least_hired_peers(
    pool: Sequence[Dict[str, Any]],
    want: int = 1,
) -> List[Dict[str, Any]]:
    """
    Atomically pick + reserve the least-hired peers (up to _MESH_MAX_PER_PEER each).
    Lets Rob1 take a second job while Tower handles another — board no longer
    looks like only one Neury ever works.
    """
    want = max(1, int(want or 1))
    taken: List[Dict[str, Any]] = []
    with _MESH_BUSY_LOCK:
        free = []
        for peer in pool or []:
            key = _peer_ip_key(peer)
            if not key[0]:
                continue
            if int(_MESH_PEER_LOAD.get(key, 0)) >= int(_MESH_MAX_PER_PEER):
                continue
            free.append(peer)
        free.sort(
            key=lambda p: (
                int(_MESH_PEER_LOAD.get(_peer_ip_key(p), 0)),
                int(_PEER_HIRE_COUNTS.get(_peer_ip_key(p), 0)),
                -float(p.get('node_reliability') or 0),
            ),
        )
        for peer in free[:want]:
            key = _peer_ip_key(peer)
            load = int(_MESH_PEER_LOAD.get(key, 0)) + 1
            _MESH_PEER_LOAD[key] = load
            if load >= int(_MESH_MAX_PER_PEER):
                _MESH_BUSY_PEERS.add(key)
            taken.append(peer)
            _PEER_HIRE_COUNTS[key] = int(_PEER_HIRE_COUNTS.get(key, 0)) + 1
    return taken


def release_mesh_peers(peer_list: Sequence[Dict[str, Any]]) -> None:
    with _MESH_BUSY_LOCK:
        for peer in peer_list or []:
            key = _peer_ip_key(peer)
            load = max(0, int(_MESH_PEER_LOAD.get(key, 0)) - 1)
            if load:
                _MESH_PEER_LOAD[key] = load
            else:
                _MESH_PEER_LOAD.pop(key, None)
            if load < int(_MESH_MAX_PER_PEER):
                _MESH_BUSY_PEERS.discard(key)


def select_peers_for_hive_job(
    job_id: str,
    paths: Optional[List[str]] = None,
    want: int = 0,
    exclude_ips: Optional[Set[Tuple[str, str]]] = None,
    spread: bool = True,
) -> Dict[str, Any]:
    """
    Pick online peers with trust/reliability/tech fit for this hive job.
    Returns {peers, cost, estimates, mesh_type, action}.
    spread=True keeps one Neury per job so a wave can hire many peers at once.
    """
    import Support.ConductorPower as cpower
    from Support.MeshAutoOffload import (
        consider_job, estimates_from_job, mesh_is_worthwhile, select_capable_peers,
        requirements_from_estimates, quote_job,
    )
    jid = (job_id or '').strip()
    paths = [p for p in (paths or []) if p]
    meta = cpower.invitation_for_job(jid) or {}
    mesh_type = str(meta.get('job_type') or meta.get('mesh_type') or '').strip()
    if not mesh_type:
        return {'action': 'local', 'peers': [], 'cost': 0, 'mesh_type': '', 'estimates': {}}
    job_data = {'dataset_paths': paths, 'files': paths, 'paths': paths}
    estimates = estimates_from_job(mesh_type, job_data)
    req = requirements_from_estimates(estimates)
    peers_wanted = max(1, int(want or estimates.get('peers_wanted') or 1))
    if spread:
        # Fan out: one capable Neury per job maximizes concurrent mesh work.
        peers_wanted = 1
    elif len(paths) >= 2:
        peers_wanted = max(peers_wanted, min(2, len(paths)))
    blocked = set(exclude_ips or set()) | busy_mesh_peer_keys()
    # Wide capable pool + soft pool of every online friend (Rob Tower included).
    pool = select_capable_peers(req, max(peers_wanted + 8, 12), exclude_ips=blocked)
    soft = select_capable_peers({}, max(12, peers_wanted + 6), exclude_ips=blocked)
    if soft:
        seen = {_peer_ip_key(p) for p in pool}
        for p in soft:
            key = _peer_ip_key(p)
            if key not in seen and key[0]:
                pool.append(p)
                seen.add(key)
    if not pool:
        pool = list(soft or [])
    peers_wanted = max(1, min(peers_wanted, len(pool) or 1))
    peers = []
    if pool:
        # Least-hired first so Rob (Tower) gets turns, not only Rob1 every wave.
        global _PEER_RR
        with _PEER_RR_LOCK:
            ranked = sorted(
                pool,
                key=lambda p: (
                    int(_PEER_HIRE_COUNTS.get(_peer_ip_key(p), 0)),
                    -float(p.get('node_reliability') or 0),
                ),
            )
            start = int(_PEER_RR) % max(1, len(ranked))
            _PEER_RR = int(_PEER_RR) + 1
            rotated = list(ranked[start:]) + list(ranked[:start])
            # Prefer the least-used peer in the rotated window.
            rotated.sort(key=lambda p: int(_PEER_HIRE_COUNTS.get(_peer_ip_key(p), 0)))
            peers = list(rotated[:peers_wanted])
    # Prefer higher trust / reliability already sorted in select_capable_peers.
    decision = consider_job(mesh_type, estimates, job_data)
    action = str((decision or {}).get('action') or 'local')
    if action == 'local' and peers:
        worth, _ = mesh_is_worthwhile(estimates)
        if worth and (len(paths) >= 1):
            action = 'mesh'
    # Demo bias: if capable Neurys exist for a hive job, use the mesh.
    if peers and mesh_type and action == 'local':
        action = 'mesh'
    cost = 0
    try:
        quote = quote_job(
            mesh_type, max(1, len(peers)), estimates.get('duration_sec') or 60, job_data=job_data,
        )
        cost = float((quote or {}).get('total_budget') or (decision or {}).get('cost') or 0)
    except Exception:
        cost = float((decision or {}).get('cost') or 0)
    return {
        'action': action if peers else 'local',
        'peers': peers,
        'cost': cost,
        'mesh_type': mesh_type,
        'estimates': estimates,
        'job_data': job_data,
        'requirements': req,
        'peers_wanted': peers_wanted,
    }


def run_hive_with_mesh_fallback(
    job_id: str,
    paths: Optional[List[str]] = None,
    instruction: str = '',
    status_fn: Optional[Callable] = None,
    should_abort: Optional[Callable] = None,
    step_id: str = '',
    recruit_timeout_sec: float = 32.0,
) -> Dict[str, Any]:
    """
    Run a hive job preferring paid peer Neurys; on drop/stall/no join, try other
    peers then finish locally. Concurrent waves reserve disjoint peers; only
    start/recruit/distribute is serialized so invitations do not race.
    """
    import Support.ConductorPower as cpower
    from Support.MeshAutoOffload import _load_mesh_distribution

    jid = (job_id or '').strip()
    paths = [os.path.abspath(p) for p in (paths or []) if p and os.path.isfile(p)]
    # Voice intro / pictures: instruction only — never ship trip media to peers.
    if jid in ('voiceover', 'pictures'):
        paths = [
            p for p in paths
            if str(p).lower().endswith(('.txt', '.md', '.docx', '.pdf', '.epub'))
        ]
    instruction = str(instruction or '').strip()
    tag = ('[[step:%s]] ' % step_id) if step_id else ''

    def _clean_msg(msg):
        # Strip embedded xx% so Conductor keeps our authoritative trailing pct.
        try:
            import re as _re
            return _re.sub(r'\s*\(?\d{1,3}\s*%\)?', '', str(msg or '')).strip() or str(msg or '')
        except Exception:
            return str(msg or '')

    def _status(msg, pct=None):
        if not status_fn:
            return
        try:
            if pct is not None:
                status_fn('%s%s (%s%%)' % (tag, _clean_msg(msg), int(pct)))
            else:
                status_fn('%s%s' % (tag, msg))
        except Exception:
            pass

    if should_abort and should_abort():
        return {'ok': False, 'error': 'aborted'}

    pick = select_peers_for_hive_job(jid, paths, spread=True)
    peers = list(pick.get('peers') or [])
    mesh_type = str(pick.get('mesh_type') or '')
    cost = float(pick.get('cost') or 0)
    used_peer_help = False
    peer_names = [str(p.get('name') or p.get('peer_id') or 'Neury') for p in peers]
    result = None
    export_fmt = export_format_for_hive_job(jid, mesh_type)
    reserved_peers: List[Dict[str, Any]] = []

    def _attempt_mesh(peer_pool, mesh_cost, attempt_label, want=1):
        nonlocal used_peer_help, reserved_peers
        if not peer_pool or not mesh_type:
            return None
        # Claim under busy-lock so parallel jobs split Rob1 / Rob Tower fairly.
        reserved = claim_least_hired_peers(peer_pool, want=max(1, int(want or 1)))
        if not reserved:
            return None
        reserved_peers = list(reserved)
        # Invite only the claimed Neurys.
        names = [str(p.get('name') or p.get('peer_id') or 'Neury') for p in reserved]
        _status(
            '%sAsking %s Neury%s (%.4g Neurons): %s' % (
                attempt_label,
                len(reserved),
                's' if len(reserved) != 1 else '',
                mesh_cost,
                ', '.join(names[:4]),
            ),
            8,
        )
        hive_id = ''
        try:
            # Light MY yellow hive LED + cyan on each hired Neury (Rob1 / Tower).
            from Support.NeuronAIMesh import (
                signal_neury_hive_lights, announce_hive_icon, clear_neury_hive_lights,
            )
            signal_neury_hive_lights(session_id='hive_%s' % (jid or 'job'))
            for peer in reserved:
                try:
                    announce_hive_icon(
                        role='worker',
                        hive_id='hive_%s' % (jid or 'job'),
                        peer=peer,
                    )
                except Exception:
                    pass
        except Exception:
            pass
        try:
            mgr = _load_mesh_distribution()
            if mgr is None or not hasattr(mgr, 'start_mesh_job'):
                return None
            job_data = dict(pick.get('job_data') or {})
            job_data['instruction'] = instruction
            job_data['catalog_job_id'] = jid
            job_data['mesh_job_type'] = mesh_type
            job_data['job_type'] = mesh_type
            job_data['export_format'] = export_fmt
            # peers_required must match invite list — never ask for more than we invited.
            # auto_offload=True keeps the UI responsive (no focus-stealing invite storm).
            # Peers with inbound=ask still get a popup; FULL peers auto-accept and still get paid.
            with _MESH_START_LOCK:
                started = mgr.start_mesh_job(
                    mesh_type,
                    job_data,
                    max(1, len(reserved)),
                    mesh_cost,
                    export_fmt,
                    peer_list=reserved,
                    auto_offload=True,
                    progress_callback=lambda p, msg: _status(str(msg or 'Mesh...'), p),
                )
                if not (isinstance(started, dict) and started.get('status') != 'error' and started.get('hive_id')):
                    return None
                hive_id = started.get('hive_id')
                wait_for_recruitment = getattr(mgr, 'wait_for_recruitment', None)
                distribute_work = getattr(mgr, 'distribute_work', None)
                wait_for_results = getattr(mgr, 'wait_for_results', None)
                joined = 0
                if callable(wait_for_recruitment):
                    joined = wait_for_recruitment(
                        hive_id, len(reserved), timeout=max(8, int(recruit_timeout_sec)),
                        progress_callback=lambda p, msg: _status(str(msg or 'Recruiting...'), p),
                    ) or 0
                if should_abort and should_abort():
                    return {'ok': False, 'error': 'aborted', 'peer_help': False}
                if joined <= 0 or not callable(distribute_work):
                    return None
                # Confirmed join only — UI badges use this number + peer names.
                live_names = names[: max(1, int(joined))]
                _status(
                    'Mesh live - %s Neury%s working: %s' % (
                        joined,
                        's' if joined != 1 else '',
                        ', '.join(live_names),
                    ),
                    28,
                )
                dist = distribute_work(
                    hive_id, job_data,
                    progress_callback=lambda p, msg: _status(str(msg or 'Sharing...'), p),
                ) or {}
                if not dist.get('success') or not callable(wait_for_results):
                    return None
            # Collect results outside the start lock so other jobs can hire peers.
            # Poll in short windows so a peer stuck at 70%/94% cannot freeze forever
            # (a single 50s wait_for_results often never returns while status ticks).
            last_mesh = {'pct': 28, 't': time.time()}

            def _collect_prog(p, msg):
                try:
                    pv = int(float(p))
                except Exception:
                    pv = int(last_mesh['pct'])
                # Normalize accidental 0-1 fractions from some progress emitters.
                if 0 < pv <= 1:
                    pv = int(round(pv * 100))
                pv = max(0, min(100, pv))
                if pv > int(last_mesh['pct']):
                    last_mesh['pct'] = pv
                    last_mesh['t'] = time.time()
                # Board may show 100 while we still finalize — never clamp to 99.
                _status(str(msg or 'Collecting...'), max(int(last_mesh['pct']), pv))

            results = {}
            soft_take = False
            wait_deadline = time.time() + 95.0
            while time.time() < wait_deadline:
                if should_abort and should_abort():
                    return {'ok': False, 'error': 'aborted', 'peer_help': False}
                chunk = wait_for_results(
                    hive_id,
                    timeout=8,
                    progress_callback=_collect_prog,
                ) or {}
                if chunk:
                    results = chunk
                stalled_sec = time.time() - float(last_mesh['t'])
                pct_now = int(last_mesh['pct'])
                if (
                    results.get('all_complete')
                    or str(results.get('status') or '') == 'complete'
                ):
                    break
                # Classic captions/whisper settle lag — take what we have and move on.
                if pct_now >= 88 and stalled_sec >= 16:
                    soft_take = True
                    _status(
                        'Mesh settle lagged — taking the result and continuing...',
                        max(96, pct_now),
                    )
                    break
                # Peer quiet below finish line — fall local instead of freezing.
                if stalled_sec >= 32 and pct_now < 88:
                    _status('Peer dropped while transferring — finishing here...', 42)
                    return None
            stalled_sec = time.time() - float(last_mesh['t'])
            # wait_for_results returns hive progress payload:
            # {all_complete, status:complete, collated_results:{results:[...]}, ...}
            # NOT a flat {results/peer_results/ok} dict — that bug discarded paid peer work.
            produced_mesh = []
            notes_mesh = []
            # Do not treat API success=True as job-complete (every progress poll has it).
            complete = bool(
                results.get('all_complete')
                or str(results.get('status') or '') == 'complete'
                or results.get('ok')
            )
            collated = results.get('collated_results') or {}
            rows = (
                list(results.get('results') or [])
                or list(results.get('peer_results') or [])
                or list(collated.get('results') or [])
            )
            for row in rows:
                if not isinstance(row, dict):
                    continue
                payload = row.get('result') or row.get('result_data') or row
                if isinstance(payload, dict):
                    for key in (
                        'produced', 'files', 'file_paths', 'local_file_paths',
                        'uploaded_files', 'requester_file_paths', 'saved_files',
                    ):
                        for item in list(payload.get(key) or []):
                            if item:
                                produced_mesh.append(item)
                    for key in ('notes',):
                        for item in list(payload.get(key) or []):
                            if item and str(item).strip() not in notes_mesh:
                                notes_mesh.append(str(item).strip())
                    for key in ('text', 'preview', 'summary'):
                        blob = str(payload.get(key) or '').strip()
                        if blob and len(blob) > 20 and blob not in notes_mesh:
                            notes_mesh.append(blob)
                for key in ('produced', 'files', 'uploaded_files'):
                    for item in list(row.get(key) or []):
                        if item:
                            produced_mesh.append(item)
            # De-dupe while preserving order
            seen = set()
            uniq = []
            for p in produced_mesh:
                sp = str(p)
                if sp in seen:
                    continue
                seen.add(sp)
                uniq.append(p)
            produced_mesh = uniq
            statuses = results.get('job_statuses') or {}
            peer_done = any(
                str((st or {}).get('status') or '').lower()
                in ('completed', 'complete', 'done', 'success')
                for st in statuses.values()
            )
            # Peer hit 100% / finished work but settle lagged — do not leave
            # the board at WORKING 100% forever.
            if not peer_done and int(last_mesh['pct']) >= 99:
                peer_done = True
            if soft_take or (
                not peer_done
                and not complete
                and int(last_mesh['pct']) >= 88
                and stalled_sec >= 16
            ):
                peer_done = True
                if not soft_take:
                    _status('Mesh settle lagged — taking the result and continuing...', 96)
            if not (complete or peer_done or produced_mesh):
                return None
            used_peer_help = True
            _status(
                'Mesh done - %s Neury%s: %s' % (
                    joined, 's' if joined != 1 else '', ', '.join(live_names),
                ),
                100,
            )
            # Materialize OCR/translate text when peers returned notes but no .txt.
            out_dir_mesh = ''
            try:
                from Support.ConductorPower import _default_out_dir
                out_dir_mesh = _default_out_dir()
            except Exception:
                out_dir_mesh = ''
            if notes_mesh and jid in ('ocr', 'translate', 'documents'):
                has_txt = any(
                    str(p).lower().endswith(('.txt', '.md', '.rtf'))
                    for p in produced_mesh if p
                )

                def _usable_note(blob):
                    text = str(blob or '').strip()
                    if len(text) < 40:
                        return False
                    if text.startswith('{') or text.startswith('['):
                        return False
                    if 'boarding_pass' in text.lower() and len(text) < 200:
                        return False
                    return True

                if not has_txt:
                    blob = next((n for n in notes_mesh if _usable_note(n)), '')
                    try:
                        if blob and out_dir_mesh:
                            os.makedirs(out_dir_mesh, exist_ok=True)
                            out_txt = os.path.join(
                                out_dir_mesh, '%s_mesh_%s.txt' % (jid, int(time.time())),
                            )
                            with open(out_txt, 'w', encoding='utf-8') as fh:
                                fh.write(blob)
                            produced_mesh.append(out_txt)
                    except Exception:
                        pass
            return {
                'ok': True,
                'produced': [p for p in produced_mesh if p],
                'files': [p for p in produced_mesh if p],
                'message': 'Finished with help from %s Neury%s' % (
                    joined, 's' if joined != 1 else '',
                ),
                'out_dir': out_dir_mesh,
                'notes': list(notes_mesh),
                'text': (notes_mesh[0] if notes_mesh else ''),
                'peer_help': True,
                'peer_count': int(joined),
                'peer_names': names[:joined] if joined else live_names,
                'paid_amount': mesh_cost,
            }
        except Exception as exc:
            _status('Mesh handoff failed (%s)' % str(exc)[:80], 35)
            return None
        finally:
            try:
                from Support.NeuronAIMesh import clear_neury_hive_lights
                clear_neury_hive_lights(delay_sec=1.2)
            except Exception:
                pass
            release_mesh_peers(reserved_peers)
            reserved_peers = []

    # Full capable pool for atomic claim (not a single pre-picked name).
    pool_pick = select_peers_for_hive_job(jid, paths, want=8, spread=False)
    peer_pool = list(pool_pick.get('peers') or peers or [])
    want_mesh = bool(
        (pick.get('action') == 'mesh' or pool_pick.get('action') == 'mesh')
        and peer_pool
        and mesh_type
    )
    if want_mesh:
        try:
            result = _attempt_mesh(peer_pool, cost, '', want=1)
            if result and result.get('error') == 'aborted':
                return result
            if result is None:
                tried = {_peer_ip_key(p) for p in peer_pool}
                # Walk every other capable Neury (Rob Tower, etc.) before local.
                for attempt in range(4):
                    _status('Peers busy or dropped — searching other Neurys...', 38 + attempt)
                    pick2 = select_peers_for_hive_job(
                        jid, paths, want=8, exclude_ips=tried, spread=False,
                    )
                    peers2 = list(pick2.get('peers') or [])
                    if not peers2:
                        break
                    cost2 = float(pick2.get('cost') or cost)
                    peer_names = [
                        str(p.get('name') or p.get('peer_id') or 'Neury') for p in peers2
                    ]
                    cost = cost2
                    result = _attempt_mesh(peers2, cost2, 'Retry: ', want=1)
                    if result and result.get('error') == 'aborted':
                        return result
                    if result is not None:
                        break
                    tried |= {_peer_ip_key(p) for p in peers2}
            # Peers were busy with other wave jobs — wait for a free Neury instead
            # of dumping every independent task onto This PC.
            if result is None and mesh_type:
                wait_deadline = time.time() + 50.0
                wait_i = 0
                while result is None and time.time() < wait_deadline:
                    if should_abort and should_abort():
                        return {'ok': False, 'error': 'aborted'}
                    free_pick = select_peers_for_hive_job(
                        jid, paths, want=8, spread=False,
                    )
                    free_pool = list(free_pick.get('peers') or [])
                    if free_pool:
                        wait_i += 1
                        _status(
                            'A Neury just freed up — hiring%s...' % (
                                (' again' if wait_i > 1 else '')
                            ),
                            min(40, 18 + wait_i),
                        )
                        result = _attempt_mesh(
                            free_pool,
                            float(free_pick.get('cost') or cost),
                            'Queue: ',
                            want=1,
                        )
                        if result and result.get('error') == 'aborted':
                            return result
                        if result is not None:
                            break
                    else:
                        _status(
                            'Waiting for a free Neury (Rob1 / Tower)...',
                            min(35, 12 + wait_i),
                        )
                        wait_i += 1
                        time.sleep(4.0)
                if result is None:
                    _status('No mesh takers — doing it on this PC...', 42)
        finally:
            if reserved_peers:
                release_mesh_peers(reserved_peers)
                reserved_peers = []

    if result is None:
        if should_abort and should_abort():
            return {'ok': False, 'error': 'aborted'}
        # Start local above any prior mesh-ask % so the board never sticks at 12%.
        _status('Working on this PC...', 45)
        _heartbeat = {'t0': time.time(), 'last': 45, 'stop': False}

        def _local_progress(pct=None, msg=None):
            # Local progress is real work — keep climbing from the local baseline.
            try:
                p = int(pct) if pct is not None else None
            except Exception:
                p = None
            if p is not None:
                p = max(45, min(99, p))
                _heartbeat['last'] = max(_heartbeat['last'], p)
            _status(
                'Working on this PC...' if not msg else str(msg),
                p if p is not None else _heartbeat['last'],
            )

        def _pulse():
            # Soft heartbeat so the board never freezes while a tool is quiet.
            # Climb past 85 (old ceiling made captions look stuck forever).
            while not _heartbeat['stop']:
                time.sleep(3.0)
                if _heartbeat['stop'] or (should_abort and should_abort()):
                    break
                elapsed = time.time() - float(_heartbeat['t0'])
                if elapsed < 160:
                    soft = min(85, 45 + int(elapsed / 4))
                else:
                    # Long tools (whisper captions) — creep past 94 toward 99 so
                    # the board never looks permanently stuck at 94%.
                    soft = min(99, 85 + int((elapsed - 160) / 8))
                # If the tool reported ~94 and then went quiet, keep creeping.
                if int(_heartbeat['last']) >= 90 and soft <= int(_heartbeat['last']):
                    soft = min(99, int(_heartbeat['last']) + 1)
                if soft > int(_heartbeat['last']):
                    _heartbeat['last'] = soft
                    _status('Working on this PC...', soft)
                time.sleep(0)

        pulse_thread = threading.Thread(target=_pulse, name='hive-local-pulse', daemon=True)
        pulse_thread.start()
        try:
            result = cpower.run_hive_job_local(
                jid, paths, instruction, progress_fn=_local_progress,
            ) or {}
        finally:
            _heartbeat['stop'] = True
        result.setdefault('peer_help', False)
        result.setdefault('peer_count', 0)

    peer_helped = bool(result.get('peer_help') or used_peer_help)
    produced = []
    for p in list(result.get('produced') or []) + list(result.get('files') or []):
        if not p:
            continue
        sp = str(p)
        if sp in produced:
            continue
        # Peer uploads may land as basename-only until copy finishes — keep them.
        if os.path.isfile(sp) or peer_helped:
            produced.append(p)
    # Mesh settle already paid the peer; never fail the step for missing local paths.
    ok = bool(result.get('ok'))
    if peer_helped and result.get('ok'):
        ok = True
    out = {
        'ok': ok,
        'job_id': jid,
        'message': str(result.get('message') or result.get('text') or '').strip(),
        'error': '' if ok else str(result.get('error') or result.get('message') or 'Job failed.'),
        'produced': produced,
        'files': produced,
        'out_dir': result.get('out_dir') or '',
        'notes': list(result.get('notes') or []),
        'peer_help': peer_helped,
        'peer_count': int(result.get('peer_count') or (len(peer_names) if peer_helped else 0)),
        'peer_names': list(result.get('peer_names') or (peer_names if peer_helped else [])),
        'paid_amount': float(result.get('paid_amount') or (cost if peer_helped else 0)),
        'enable_hint': '' if ok else str(result.get('message') or result.get('error') or ''),
    }
    return out


def mesh_parallel_worker_cap(ready_count: int = 1) -> int:
    """How many independent hive/file steps may run at once for a WOW mesh demo."""
    n = max(1, int(ready_count or 1))
    # Prefer fan-out across Neurys; remote work is cheap on this PC.
    return max(1, min(6, n))


def run_steps_parallel(
    steps: List[Dict[str, Any]],
    worker: Callable[[Dict[str, Any]], Dict[str, Any]],
    should_abort: Optional[Callable] = None,
    max_workers: int = 4,
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """
    Execute step worker(s) concurrently. Returns list of (step, result).
    Hive jobs mostly run on peer Neurys — allow several in flight.
    """
    if not steps:
        return []
    workers = max(1, min(int(max_workers or 4), mesh_parallel_worker_cap(len(steps)), len(steps)))
    out: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    if workers == 1 or len(steps) == 1:
        for step in steps:
            if should_abort and should_abort():
                out.append((step, {'ok': False, 'error': 'aborted'}))
                break
            out.append((step, worker(step) or {'ok': False, 'error': 'no result'}))
        return out
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, step): step for step in steps}
        for fut in as_completed(futures):
            step = futures[fut]
            if should_abort and should_abort():
                try:
                    fut.cancel()
                except Exception:
                    pass
                out.append((step, {'ok': False, 'error': 'aborted'}))
                continue
            try:
                res = fut.result()
            except Exception as exc:
                res = {'ok': False, 'error': str(exc)}
            out.append((step, res if isinstance(res, dict) else {'ok': False, 'error': 'bad result'}))
    return out


def run_steps_scan_forward(
    initial_steps: List[Dict[str, Any]],
    worker: Callable[[Dict[str, Any]], Dict[str, Any]],
    more_ready: Callable[[], List[Dict[str, Any]]],
    should_abort: Optional[Callable] = None,
    max_workers: int = 4,
    on_complete: Optional[Callable[[Dict[str, Any], Dict[str, Any]], None]] = None,
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """
    Run parallel-safe steps; as each finishes, scan for newly unlocked work and
    assign it (rehire free Neurys). Returns all (step, result) pairs completed.
    """
    out: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    pending: List[Dict[str, Any]] = list(initial_steps or [])
    claimed: Set[str] = set()
    workers = max(1, min(int(max_workers or 4), 6))

    def _sid(step):
        return str(step.get('id') or id(step))

    def _queue_new():
        try:
            for step in list(more_ready() or []):
                sid = _sid(step)
                if sid in claimed:
                    continue
                if str(step.get('status') or '') not in ('pending', ''):
                    continue
                if not step_is_parallel_safe(step):
                    continue
                pending.append(step)
                claimed.add(sid)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures: Dict[Any, Dict[str, Any]] = {}

        def _fill():
            while pending and len(futures) < workers:
                step = pending.pop(0)
                sid = _sid(step)
                claimed.add(sid)
                futures[pool.submit(worker, step)] = step

        for step in pending:
            claimed.add(_sid(step))
        _fill()

        while futures:
            if should_abort and should_abort():
                for fut in list(futures.keys()):
                    try:
                        fut.cancel()
                    except Exception:
                        pass
                    step = futures.pop(fut)
                    out.append((step, {'ok': False, 'error': 'aborted'}))
                break
            done_set, _not_done = wait(
                list(futures.keys()), timeout=0.75, return_when=FIRST_COMPLETED,
            )
            if not done_set:
                _queue_new()
                _fill()
                continue
            for done_fut in done_set:
                step = futures.pop(done_fut, None)
                if step is None:
                    continue
                try:
                    res = done_fut.result()
                except Exception as exc:
                    res = {'ok': False, 'error': str(exc)}
                if not isinstance(res, dict):
                    res = {'ok': False, 'error': 'bad result'}
                out.append((step, res))
                if on_complete:
                    try:
                        on_complete(step, res)
                    except Exception:
                        pass
            _queue_new()
            _fill()
    return out


__all__ = [
    'annotate_step_dependencies',
    'next_parallel_wave',
    'step_is_parallel_safe',
    'split_wave_for_execution',
    'peer_colors',
    'select_peers_for_hive_job',
    'run_hive_with_mesh_fallback',
    'run_steps_parallel',
    'run_steps_scan_forward',
    'mesh_parallel_worker_cap',
    'busy_mesh_peer_keys',
    'reserve_mesh_peers',
    'claim_least_hired_peers',
    'release_mesh_peers',
]
