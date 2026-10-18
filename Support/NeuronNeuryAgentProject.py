# NeuronNeuryAgentProject.py — extracted from NeuronNeuryAgent.py (satellite module).
# Prefer function-scoped imports of other AgentXxx modules to avoid cycles.

import json
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional, Tuple

import Support.Header as init
import Support.NeuronNeuryAgentHelpers as _neury_helpers
from Support.NeuronNeuryAgentHelpers import (  # noqa: F401
    CTRL_ASK,
    CTRL_FULL,
    CTRL_OFF,
    NEURY_MARK,
    NEURY_PROTOCOL,
    PEER_ASK,
    PEER_DENY,
    PEER_FULL,
    RISK_DESTRUCTIVE,
    RISK_MONEY,
    RISK_READ,
    RISK_SEND,
    RISK_UI,
    RISK_WRITE,
    SCOPE_DYNAMIC,
    SCOPE_LOCAL,
    SCOPE_OTHER,
    shellflash_dbg,
    _AGENT_ROUND_TIMEOUT_SEC,
    _AUDIT_NAME,
    _MAX_TOOL_ROUNDS,
    _close_to,
    _has_any_word,
    _has_phrase,
    _ratio,
    _token_matches,
)

# --- cython cross-satellite stubs (bound by NeuronNeuryAgent sync) ---
_allowed = None
_answer_from_tools = None
_exec_tool = None
_extract_json_object = None
_format_tool_result = None
_ollama_generate = None
_pick_model = None
_redact_args = None
_sanitize_web_query = None
_short = None
_tool_specs = None
_week_range_ts = None
load_assistant_policy = None
neury_guard_outgoing = None
tool_runs_on_ui = None


def _run_project(project_id: str='', ask_fn: Optional[Callable]=None, status_fn: Optional[Callable]=None, control: str='', should_abort: Optional[Callable]=None, ui_fn: Optional[Callable]=None) -> Dict[str, Any]:
    prev_turn_status = _neury_helpers._turn_status_fn
    if status_fn is not None:
        _neury_helpers._turn_status_fn = status_fn
    try:
        return _run_project_body(project_id, ask_fn=ask_fn, status_fn=status_fn, control=control, should_abort=should_abort, ui_fn=ui_fn)
    finally:
        _neury_helpers._turn_status_fn = prev_turn_status


def _peer_badge_fields(step):
    return {
        'peer_help': bool(step.get('peer_help')),
        'peer_count': int(step.get('peer_count') or 0),
        'peer_names': list(step.get('peer_names') or []),
        'peer_colors': list(step.get('peer_colors') or []),
        'paid_amount': float(step.get('paid_amount') or 0),
    }


def _save_project_preserving_peers(project):
    """Reload-merge before save so parallel workers cannot wipe peer pills."""
    from Support.NeuronOrchestrator import save_project, _load_project
    pid = str((project or {}).get('id') or '')
    if not pid:
        save_project(project)
        return project
    live = _load_project(pid) or project
    live_steps = live.get('steps') or []
    mem_steps = (project or {}).get('steps') or []
    by_id = {str(s.get('id') or ''): s for s in live_steps if str(s.get('id') or '')}
    # Disk peer badges win when memory lost them (parallel save race).
    for mem in mem_steps:
        sid = str(mem.get('id') or '')
        disk = by_id.get(sid)
        if not disk:
            continue
        if disk.get('peer_help') and not mem.get('peer_help'):
            mem.update(_peer_badge_fields(disk))
        elif mem.get('peer_help') and not disk.get('peer_help'):
            disk.update(_peer_badge_fields(mem))
        elif mem.get('peer_help') and disk.get('peer_help'):
            # Keep the richer name list / higher count.
            if int(mem.get('peer_count') or 0) >= int(disk.get('peer_count') or 0):
                disk.update(_peer_badge_fields(mem))
            else:
                mem.update(_peer_badge_fields(disk))
        # Apply non-peer fields from memory for this step onto disk.
        for key, val in mem.items():
            if key.startswith('peer_') or key == 'paid_amount':
                continue
            disk[key] = val
    # Copy project-level fields from memory.
    for key, val in (project or {}).items():
        if key == 'steps':
            continue
        live[key] = val
    # Ensure every memory step exists on disk.
    known = {str(s.get('id') or '') for s in live_steps}
    for mem in mem_steps:
        sid = str(mem.get('id') or '')
        if sid and sid not in known:
            live_steps.append(mem)
            known.add(sid)
    live['steps'] = live_steps
    save_project(live)
    # Keep caller dict in sync without orphaning the shared object.
    for key, val in live.items():
        project[key] = val
    return live


def _apply_step_result(project, step, result, used, idx, total, tool, label, args, status_fn, actor='local'):
    from Support.NeuronOrchestrator import update_project_context
    used.append({'name': tool, 'ok': bool((result or {}).get('ok')), 'result': result})
    if result.get('peer_help'):
        step['peer_help'] = True
        step['peer_count'] = int(result.get('peer_count') or 0)
        step['peer_names'] = list(result.get('peer_names') or [])
        step['paid_amount'] = float(result.get('paid_amount') or 0)
        try:
            colors = __import__(
                'Support.NeuronNeuryAskParallel', fromlist=['peer_colors']
            ).peer_colors(step['peer_count'] or 1)
            step['peer_colors'] = colors
        except Exception:
            step['peer_colors'] = []
    if result.get('ok'):
        step['status'] = 'done'
        step['progress'] = 100
        step['result'] = _format_tool_result(tool, result) or 'done'
        step['error'] = ''
        # Keep outputs on the step so Conductor can offer Preview file
        # (include peer basenames even before the local copy lands).
        outs = []
        for key in ('produced', 'files', 'paths'):
            for p in list(result.get(key) or []):
                if p and str(p) not in outs:
                    outs.append(str(p))
        if result.get('path') and str(result.get('path')) not in outs:
            outs.append(str(result.get('path')))
        if result.get('out_dir'):
            step['out_dir'] = str(result.get('out_dir'))
        notes = [str(n).strip() for n in list(result.get('notes') or []) if str(n).strip()]
        text_blob = str(
            result.get('text') or result.get('preview') or result.get('summary') or ''
        ).strip()
        if text_blob and text_blob not in notes:
            notes.append(text_blob)
        if notes:
            step['notes'] = notes
        # OCR / translate: ensure a local .txt exists for PREVIEW FILE.
        jid = str((args or {}).get('job_id') or '').strip().lower()
        if jid in ('ocr', 'translate', 'documents') and notes:
            has_txt = any(
                str(p).lower().endswith(('.txt', '.md', '.rtf'))
                for p in outs if p
            )
            if not has_txt:
                blob = ''
                for n in notes:
                    t = str(n or '').strip()
                    if len(t) < 40:
                        continue
                    if t.startswith('{') or t.startswith('['):
                        continue
                    if 'boarding_pass' in t.lower() and len(t) < 200:
                        continue
                    blob = t
                    break
                if blob:
                    try:
                        import os
                        dest = str(step.get('out_dir') or '') or str(
                            result.get('out_dir') or ''
                        )
                        if not dest:
                            from Support.ConductorPower import _default_out_dir
                            dest = _default_out_dir()
                        os.makedirs(dest, exist_ok=True)
                        out_txt = os.path.join(
                            dest, '%s_%s.txt' % (jid, step.get('id') or 'step'),
                        )
                        with open(out_txt, 'w', encoding='utf-8') as fh:
                            fh.write(blob)
                        outs.append(out_txt)
                        step['out_dir'] = dest
                    except Exception:
                        pass
        if outs:
            step['produced'] = outs
            step['files'] = list(outs)
        update_project_context(project, tool, result)
        project['current_step'] = idx
        _append_project_receipt(project, tool, True, args, actor=actor, note=label)
        _save_project_preserving_peers(project)
        if status_fn:
            try:
                sid = str(step.get('id') or '')
                pct = int(round(100.0 * idx / max(1, total)))
                if sid:
                    status_fn('[[step:%s]] Finished: %s (%s%%)' % (sid, label, pct))
                else:
                    from Support.NeuronNeuryAskComposer import progress_status as _ps
                    _ps(status_fn, idx, total, 'Finished: %s' % label, pct)
            except Exception:
                pass
        return 'ok'
    if tool in ('web.lookup', 'web.fetch', 'search.web'):
        topic = str(args.get('query') or args.get('topic') or args.get('q') or '').strip()
        topic = _sanitize_web_query(topic) or topic or 'this topic'
        warn = str(result.get('error') or 'no results')
        fallback = (
            'Research notes for %s (live web returned no usable hits: %s). '
            'Draft a short announcement grounded in the user goal and mark trend '
            'claims as provisional pending fresh sources.'
        ) % (topic, warn)
        soft = {
            'ok': True, 'kind': 'search', 'query': topic, 'text': fallback,
            'research': fallback, 'hits': [], 'soft_fail': True, 'warning': warn,
        }
        step['status'] = 'done'
        step['progress'] = 100
        step['result'] = 'Research soft-failed (%s); continuing with notes.' % warn
        step['error'] = ''
        update_project_context(project, tool, soft)
        project['current_step'] = idx
        _append_project_receipt(project, tool, True, args, actor=actor, note='soft-fail research: %s' % warn)
        used[-1] = {'name': tool, 'ok': True, 'result': soft}
        _save_project_preserving_peers(project)
        return 'ok'
    enable = str(result.get('enable_hint') or result.get('message') or '')
    soft_block = result.get('error') in (
        'tools_missing', 'deferred', 'neury_required', 'need_files',
        'unknown_job', 'sandbox_denied',
    )
    if soft_block or tool == 'files.classify_batch':
        step['status'] = 'blocked'
        step['error'] = enable or str(result.get('error') or 'blocked')
        _append_project_receipt(project, tool, False, args, actor=actor, note=step['error'])
        project['current_step'] = idx
        used[-1] = {
            'name': tool, 'ok': False, 'result': result,
            'blocked': True, 'enable_hint': step['error'],
        }
        _save_project_preserving_peers(project)
        if status_fn:
            try:
                status_fn('Skipped (needs setup): %s' % label)
            except Exception:
                pass
        return 'soft'
    # Public/peer outputs must not kill the whole trip — mark FAILED, keep going
    # so diary/chat/reminders/files still finish for the WOW demo.
    _SOFT_FAIL_TOOLS = frozenset({
        'websites.publish', 'article.publish', 'pedia.publish',
        'chat.send', 'mail.send', 'diary.post', 'public_files.publish',
    })
    if tool in _SOFT_FAIL_TOOLS:
        step['status'] = 'failed'
        step['progress'] = 100
        step['error'] = str(result.get('error') or result.get('message') or 'failed')
        step['result'] = 'failed — continuing with the rest'
        project['current_step'] = idx
        # Never leave the trip halted on a soft public/peer output failure.
        project['status'] = 'active'
        _append_project_receipt(project, tool, False, args, actor=actor, note=step['error'])
        used[-1] = {
            'name': tool, 'ok': False, 'result': result, 'soft_fail': True,
        }
        _save_project_preserving_peers(project)
        if status_fn:
            try:
                status_fn('Failed (continuing): %s — %s' % (label, step['error'][:80]))
            except Exception:
                pass
        return 'soft'
    step['status'] = 'failed'
    step['error'] = str(result.get('error') or 'failed')
    project['status'] = 'failed'
    _append_project_receipt(project, tool, False, args, actor=actor, note=step['error'])
    _save_project_preserving_peers(project)
    return 'fail'


def _execute_project_step_core(project, step, ask_fn, status_fn, should_abort, ui_fn, ctrl, tool_map):
    """Run one step tool call (may be invoked from a worker thread for hive jobs)."""
    from Support.NeuronOrchestrator import apply_step_context, save_project
    from Support.NeuronNeuryAskParallel import run_hive_with_mesh_fallback

    steps = project.get('steps') or []
    total = len(steps)
    try:
        idx = steps.index(step) + 1
    except ValueError:
        idx = int(project.get('current_step') or 0) + 1
    tool = str(step.get('tool') or '')
    label = str(step.get('label') or tool)
    risk = str(step.get('risk') or tool_map.get(tool, {}).get('risk') or RISK_WRITE)
    activity = str(step.get('activity') or label)
    sid = str(step.get('id') or '')
    step_args = apply_step_context(step.get('args') or {}, project.get('context') or {})
    summary = 'Project: %s (%s)' % (label, tool)
    if not _allowed(ctrl, risk, ask_fn, tool, summary, args=step_args, spec=tool_map.get(tool)):
        return {
            'ok': False, 'error': 'permission denied', 'declined': True,
            'tool': tool, 'label': label, 'args': step_args, 'idx': idx,
        }
    if should_abort and should_abort():
        return {'ok': False, 'error': 'aborted', 'tool': tool, 'label': label, 'args': step_args, 'idx': idx}
    args = step_args
    if tool == 'calendar.list' and (not args.get('start_ts')):
        start_ts, end_ts = _week_range_ts()
        args['start_ts'] = start_ts
        args['end_ts'] = end_ts
    if tool == 'website.build':
        # Prefer converted/produced trip media over the original dump paths.
        ctx = project.get('context') or {}
        atts = []
        for p in list(args.get('attachments') or []):
            if isinstance(p, str) and p and not p.startswith('$') and p not in atts:
                atts.append(p)
        for key in ('produced', 'photo_paths', 'video_paths', 'photos', 'videos'):
            for p in list(ctx.get(key) or []):
                if not p:
                    continue
                sp = str(p)
                if sp in atts:
                    continue
                if os.path.isfile(sp):
                    atts.append(sp)
                    continue
                # Peer basename — look under hive_results / conductor_out.
                base = os.path.basename(sp)
                for root_key in ('hive_results', 'conductor_out'):
                    try:
                        import Support.Header as _init
                        if root_key == 'hive_results':
                            root = os.path.join(
                                getattr(_init, 'MAIN_PATH', '') or '', 'System', 'hive_results',
                            )
                            if not os.path.isdir(root):
                                continue
                            for name in os.listdir(root):
                                cand = os.path.join(root, name, base)
                                if os.path.isfile(cand) and cand not in atts:
                                    atts.append(cand)
                                    break
                        else:
                            from Support.ConductorPower import _default_out_dir
                            cand = os.path.join(_default_out_dir(), base)
                            if os.path.isfile(cand) and cand not in atts:
                                atts.append(cand)
                    except Exception:
                        pass
        # Drop originals that are clearly dump PDFs / receipts — keep media only.
        media_ext = (
            '.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp',
            '.mp4', '.webm', '.mov', '.m4v',
        )
        atts = [
            p for p in atts
            if os.path.splitext(p)[1].lower() in media_ext
        ]
        if atts:
            args['attachments'] = atts[:24]
            args['embed_media'] = True
    if tool == 'websites.publish':
        ctx = project.get('context') or {}
        name = str(args.get('name') or args.get('website') or '').strip()
        if (not name) or name.startswith('$'):
            name = str(ctx.get('website_name') or ctx.get('name') or '').strip()
            if name:
                args['name'] = name
    try:
        if tool == 'hive.run_job':
            jid = str(args.get('job_id') or '')
            paths = args.get('paths') or args.get('files') or []
            if isinstance(paths, str):
                paths = [paths]
            result = run_hive_with_mesh_fallback(
                jid,
                paths=list(paths or []),
                instruction=str(args.get('instruction') or ''),
                status_fn=status_fn,
                should_abort=should_abort,
                step_id=sid,
            )
        elif tool == 'files.classify_batch':
            from Support.NeuronNeuryAskComposer import files_classify_batch
            def _cls_status(msg=None):
                if not status_fn:
                    return
                try:
                    text = str(msg or '').strip()
                    if sid and not text.startswith('[[step:'):
                        status_fn('[[step:%s]] %s' % (sid, text))
                    else:
                        status_fn(text)
                except Exception:
                    pass
            result = files_classify_batch(args, status_fn=_cls_status)
        elif tool == 'files.commit_outputs':
            from Support.NeuronNeuryAskComposer import files_commit_outputs
            if not args.get('paths') and (project.get('context') or {}).get('produced'):
                args['paths'] = list((project.get('context') or {}).get('produced') or [])
            result = files_commit_outputs(args, ask_fn=ask_fn, status_fn=status_fn)
        elif tool_runs_on_ui(tool, risk) and ui_fn is not None:
            result = ui_fn(tool, args)
        else:
            result = _exec_tool(tool, args)
    except Exception as exc:
        result = {'ok': False, 'error': str(exc)}
    if not isinstance(result, dict):
        result = {'ok': False, 'error': 'bad result'}
    result['tool'] = tool
    result['label'] = label
    result['args'] = args
    result['idx'] = idx
    result['activity'] = activity
    return result


def _run_project_body(project_id: str='', ask_fn: Optional[Callable]=None, status_fn: Optional[Callable]=None, control: str='', should_abort: Optional[Callable]=None, ui_fn: Optional[Callable]=None) -> Dict[str, Any]:
    from Support.NeuronOrchestrator import project_status, save_project, _load_project
    from Support.NeuronNeuryAskParallel import (
        annotate_step_dependencies, next_parallel_wave, split_wave_for_execution,
        mesh_parallel_worker_cap, run_steps_scan_forward, step_is_parallel_safe,
    )

    project = _load_project(project_id)
    if not project:
        return {'ok': False, 'error': 'no active project'}
    tool_map = {t['name']: t for t in _tool_specs()}
    steps = project.get('steps') or []
    annotate_step_dependencies(steps)
    save_project(project)
    total = len(steps)
    used = []
    ctrl = control or load_assistant_policy().get('assistant_control') or CTRL_ASK
    proj_lock = threading.Lock()
    hard_fail = {'value': False}

    def _handle_step_result(step, result):
        """Apply one finished step under lock; return False on hard fail."""
        nonlocal project, steps, total
        tool = str(result.get('tool') or step.get('tool') or '')
        label = str(result.get('label') or step.get('label') or tool)
        args = result.get('args') if isinstance(result.get('args'), dict) else (step.get('args') or {})
        idx = int(result.get('idx') or 1)
        with proj_lock:
            if result.get('declined'):
                if tool in ('websites.publish', 'article.publish'):
                    step['status'] = 'skipped'
                    step['result'] = (
                        'draft kept in My Articles' if tool == 'article.publish'
                        else 'skipped - publish declined'
                    )
                    step['error'] = ''
                    project['current_step'] = idx
                    used.append({'name': tool, 'ok': True, 'result': {'ok': True, 'skipped': True}})
                    _append_project_receipt(project, tool, True, args, note='publish declined')
                    _save_project_preserving_peers(project)
                    project = _load_project(project_id) or project
                    steps = project.get('steps') or []
                    total = len(steps)
                    return True
                step['status'] = 'blocked'
                step['error'] = 'permission denied'
                _save_project_preserving_peers(project)
                hard_fail['value'] = True
                return False
            actor = 'mesh' if result.get('peer_help') else 'local'
            outcome = _apply_step_result(
                project, step, result, used, idx, total, tool, label, args, status_fn, actor=actor,
            )
            project = _load_project(project_id) or project
            steps = project.get('steps') or []
            total = len(steps)
            if outcome == 'fail':
                hard_fail['value'] = True
                return False
            return project.get('status') == 'active'

    def _worker(step):
        sid = str(step.get('id') or '')
        label = str(step.get('label') or step.get('tool') or 'step')
        tool_name = str(step.get('tool') or '')
        with proj_lock:
            # Reload before save — parallel workers were overwriting each other's
            # peer_help/peer_names on disk (every bar fell back to "This PC").
            live = _load_project(project_id) or project
            live_steps = live.get('steps') or project.get('steps') or []
            target = None
            for s in live_steps:
                if sid and str(s.get('id') or '') == sid:
                    target = s
                    break
            if target is None:
                target = step
            target['status'] = 'running'
            target['progress'] = max(1, int(target.get('progress') or 0) or 1)
            # Do not clear peer badges if status_fn already attached a hire.
            if not target.get('peer_help'):
                target['peer_help'] = False
                target['peer_count'] = 0
                target['peer_names'] = []
            try:
                # Classify must stay instant — skip heavy merge-save on start.
                if tool_name == 'files.classify_batch':
                    save_project(live)
                else:
                    _save_project_preserving_peers(live)
                for key, val in live.items():
                    project[key] = val
            except Exception:
                pass
            # Keep shared dict in sync for this step.
            step.update({
                'status': target.get('status'),
                'progress': target.get('progress'),
                'peer_help': target.get('peer_help'),
                'peer_count': target.get('peer_count'),
                'peer_names': list(target.get('peer_names') or []),
            })
        if status_fn and tool_name != 'files.classify_batch':
            try:
                # Never say "This PC" before mesh hire — that cleared peer pills.
                if sid:
                    status_fn('[[step:%s]] Starting… (5%%)' % sid)
                else:
                    status_fn('Starting: %s…' % label)
            except Exception:
                pass
        return _execute_project_step_core(
            project, step, ask_fn, status_fn, should_abort, ui_fn, ctrl, tool_map,
        )

    while project.get('status') == 'active':
        if should_abort and should_abort():
            break
        wave = next_parallel_wave(project)
        if not wave:
            # Wait while any step is still running — never exit and strand Sort at 6%.
            running = [s for s in steps if str(s.get('status') or '') == 'running']
            if running:
                time.sleep(0.15)
                project = _load_project(project_id) or project
                steps = project.get('steps') or []
                continue
            pending = [s for s in steps if str(s.get('status') or '') == 'pending']
            if not pending:
                project['status'] = 'completed'
                save_project(project)
            break

        parallel, serial = split_wave_for_execution(wave)

        # Classify alone so typed paths exist before the mesh fans out.
        if (
            len(parallel) == 1
            and str(parallel[0].get('tool') or '') == 'files.classify_batch'
        ):
            cls_step = parallel[0]
            # Fast path: seed already classified attachments — mark done, fan out.
            ctx = project.get('context') or {}
            if ctx.get('video_paths') or ctx.get('photo_paths') or ctx.get('paths'):
                fake = {
                    'ok': True,
                    'videos': list(ctx.get('video_paths') or ctx.get('videos') or []),
                    'photos': list(ctx.get('photo_paths') or ctx.get('photos') or []),
                    'receipts': list(ctx.get('receipt_paths') or ctx.get('receipts') or []),
                    'documents': list(ctx.get('document_paths') or ctx.get('documents') or []),
                    'video_paths': list(ctx.get('video_paths') or []),
                    'photo_paths': list(ctx.get('photo_paths') or []),
                    'receipt_paths': list(ctx.get('receipt_paths') or []),
                    'document_paths': list(ctx.get('document_paths') or []),
                    'paths': list(ctx.get('paths') or ctx.get('attachments') or []),
                    'produced': list(ctx.get('produced') or ctx.get('paths') or []),
                    'counts': {
                        'videos': len(ctx.get('video_paths') or []),
                        'photos': len(ctx.get('photo_paths') or []),
                        'receipts': len(ctx.get('receipt_paths') or []),
                        'documents': len(ctx.get('document_paths') or []),
                    },
                    'tool': 'files.classify_batch',
                    'label': str(cls_step.get('label') or 'Sort'),
                    'args': cls_step.get('args') or {},
                    'idx': 1,
                }
                cls_step['status'] = 'running'
                if not _handle_step_result(cls_step, fake) or hard_fail['value']:
                    break
            else:
                result = _worker(cls_step)
                if not _handle_step_result(cls_step, result) or hard_fail['value']:
                    break
            # Guard: never leave sort stuck at WORKING (blocks the whole mesh).
            with proj_lock:
                if str(cls_step.get('status') or '') == 'running':
                    cls_step['status'] = 'done'
                    cls_step['progress'] = 100
                    try:
                        save_project(project)
                    except Exception:
                        pass
            project = _load_project(project_id) or project
            steps = project.get('steps') or []
            continue

        # Fan out every independent hive/file job; scan forward as Neurys free up.
        if parallel:
            def _more_ready():
                with proj_lock:
                    live = _load_project(project_id) or project
                    ready = next_parallel_wave(live)
                    return [s for s in ready if step_is_parallel_safe(s)]

            def _on_complete(step, result):
                _handle_step_result(step, result)

            # Never side-run posts/publishes during mesh — those need approval after.
            _NO_SIDE = frozenset({
                'user.confirm', 'diary.post', 'chat.send', 'mail.send',
                'article.publish', 'websites.publish', 'pedia.publish',
            })
            par_ids = {str(s.get('id') or '') for s in parallel}
            side_serial = []
            for step in list(serial):
                tool = str(step.get('tool') or '')
                if tool in _NO_SIDE:
                    continue
                deps = {str(d) for d in (step.get('depends_on') or [])}
                if deps & par_ids:
                    continue
                side_serial.append(step)

            mesh_box = {'error': None}

            def _mesh_burst():
                try:
                    run_steps_scan_forward(
                        parallel,
                        _worker,
                        more_ready=_more_ready,
                        should_abort=should_abort,
                        max_workers=mesh_parallel_worker_cap(max(2, len(parallel))),
                        on_complete=_on_complete,
                    )
                except Exception as exc:
                    mesh_box['error'] = exc

            mesh_thread = threading.Thread(
                target=_mesh_burst, name='project-mesh-wave', daemon=True,
            )
            mesh_thread.start()
            for step in side_serial:
                if should_abort and should_abort():
                    break
                if hard_fail['value'] or project.get('status') != 'active':
                    break
                step['status'] = 'running'
                save_project(project)
                result = _execute_project_step_core(
                    project, step, ask_fn, status_fn, should_abort, ui_fn, ctrl, tool_map,
                )
                if not _handle_step_result(step, result) or hard_fail['value']:
                    break
            mesh_thread.join()
            if mesh_box['error']:
                hard_fail['value'] = True
            project = _load_project(project_id) or project
            steps = project.get('steps') or []
            if hard_fail['value'] or project.get('status') != 'active':
                break
            # After a parallel burst, loop so newly unlocked serial work (website,
            # confirms) or more mesh jobs are picked up immediately.
            continue

        # Serial-only wave: confirms pause; diary/website/chat run one-by-one.
        if not serial:
            break
        step = serial[0]
        tool = str(step.get('tool') or '')
        label = str(step.get('label') or tool)
        activity = str(step.get('activity') or label)
        if tool == 'user.confirm':
            step['status'] = 'awaiting'
            project['status'] = 'awaiting_confirm'
            save_project(project)
            if status_fn:
                try:
                    status_fn('Waiting for your OK: %s' % activity)
                except Exception:
                    pass
            used.append({
                'name': 'user.confirm', 'ok': True,
                'result': {
                    'ok': True, 'await_user': True,
                    'message': str((step.get('args') or {}).get('message') or 'Say OK to continue.'),
                },
            })
            break
        step['status'] = 'running'
        save_project(project)
        result = _execute_project_step_core(
            project, step, ask_fn, status_fn, should_abort, ui_fn, ctrl, tool_map,
        )
        if not _handle_step_result(step, result) or hard_fail['value']:
            break
        project = _load_project(project_id) or project
        steps = project.get('steps') or []

    out = project_status(project.get('id') or '')
    out['used_tools'] = used
    out['receipts'] = list((project or {}).get('receipts') or [])
    out['plan_text'] = str((project or {}).get('plan_text') or '')
    paid_peers = 0
    paid_amount = 0.0
    for step in (project.get('steps') or []):
        if step.get('peer_help'):
            paid_peers += max(1, int(step.get('peer_count') or 1))
            paid_amount += float(step.get('paid_amount') or 0)
    out['paid_peers'] = paid_peers
    out['paid_amount'] = paid_amount
    return out

def _website_manager_action_part(path: str='', website_name: str='') -> Optional[Dict[str, Any]]:
    """Card action that opens a finished site in Website Manager."""
    p = (path or '').strip()
    name = (website_name or '').strip()
    if not p and name:
        try:
            works = os.path.join(getattr(init, 'PATH_WEBSITES_WORKS', '') or '', name)
            pub = os.path.join(getattr(init, 'PATH_WEBSITES_PUBLISHED', '') or '', name)
            if works and os.path.isdir(works):
                p = works
            elif pub and os.path.isdir(pub):
                p = pub
        except Exception:
            p = ''
    if not p or not os.path.isdir(p):
        return None
    return {'type': 'action', 'action_id': 'open_website_manager', 'title': 'Website ready', 'text': 'Open this site in Website Manager to review pages and replace any temporary placeholder images with real ones.', 'path': p, 'website_name': name or os.path.basename(p.rstrip('\\/')), 'action_label': 'Open in Website Manager'}

def _build_project_parts(status_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Plain-text status card, plus Open in Website Manager when a site finished."""
    parts: List[Dict[str, Any]] = [{'type': 'title', 'title': (status_result.get('summary') or {}).get('title') or 'Project', 'text': _format_project_reply(status_result)}]
    summary = status_result.get('summary') or {}
    project = status_result.get('project') or {}
    ctx = project.get('context') if isinstance(project, dict) else {}
    if not isinstance(ctx, dict):
        ctx = {}
    if summary.get('status') == 'completed':
        site_path = str(ctx.get('path') or '').strip()
        site_name = str(ctx.get('website_name') or ctx.get('name') or '').strip()
        if site_path and (not os.path.isdir(site_path)):
            site_path = ''
        action = _website_manager_action_part(site_path, site_name)
        if action is None and site_name:
            action = _website_manager_action_part('', site_name)
        if action is not None:
            note_bits = []
            for item in status_result.get('used_tools') or []:
                res = item.get('result') if isinstance(item, dict) else None
                if isinstance(res, dict) and res.get('image_note'):
                    note_bits.append(str(res.get('image_note')))
                if isinstance(res, dict) and res.get('used_placeholders'):
                    note_bits.append('Some images are temporary web placeholders that must be substituted with real images.')
            if note_bits:
                action['text'] = note_bits[0] + ' ' + action['text']
            parts.append(action)
    return parts

def _strip_project_html_noise(text: str) -> str:
    """Project cards are plain text; strip leftover HTML break tags from older runs."""
    raw = str(text or '')
    raw = re.sub('(?i)<br\\s*/?>', '\n', raw)
    raw = re.sub('(?i)&lt;br\\s*/?&gt;', '\n', raw)
    raw = re.sub('\\n{3,}', '\n\n', raw)
    return raw.strip()

def _format_project_reply(status_result: Dict[str, Any]) -> str:
    summary = status_result.get('summary') or {}
    done = int(summary.get('done') or 0)
    total = int(summary.get('total') or 0)
    state = summary.get('status') or 'active'
    title = _strip_project_html_noise(summary.get('title') or 'Project update')
    lines = [title, 'Progress: %s of %s steps (%s).' % (done, total, state), '']
    plan_text = str(status_result.get('plan_text') or (status_result.get('project') or {}).get('plan_text') or '').strip()
    if plan_text:
        lines.append(plan_text)
        lines.append('')
    blocked_hints = []
    for step in status_result.get('steps') or []:
        label = _strip_project_html_noise(step.get('label') or step.get('tool') or 'Step')
        st = step.get('status') or 'pending'
        mark = {'done': 'Done', 'failed': 'Failed', 'blocked': 'Blocked', 'running': 'Working', 'awaiting': 'Waiting', 'pending': 'Pending', 'skipped': 'Skipped'}.get(st, st.title())
        bit = '%s — %s' % (mark, label)
        if st == 'done' and step.get('result'):
            bit += ' (%s)' % _short(_strip_project_html_noise(step.get('result')), 80)
        if st in ('failed', 'blocked') and step.get('error'):
            bit += ' (%s)' % _short(_strip_project_html_noise(step.get('error')), 80)
            if st == 'blocked':
                blocked_hints.append(str(step.get('error') or ''))
        lines.append(bit)
    if blocked_hints:
        lines.append('')
        lines.append('Some steps need setup:')
        for hint in blocked_hints[:6]:
            if hint:
                lines.append('- %s' % _short(hint, 160))
    if summary.get('status') == 'awaiting_confirm':
        lines.append('')
        lines.append('Say OK when this looks right and I will continue.')
    elif summary.get('status') == 'failed':
        lines.append('')
        need_img = False
        for item in status_result.get('used_tools') or []:
            res = item.get('result') if isinstance(item, dict) else None
            if isinstance(res, dict) and res.get('need_image_install'):
                need_img = True
                break
        if need_img:
            lines.append('ComfyUI is required for the images on this site. Install it when prompted, then say retry to continue.')
        else:
            lines.append('I stopped on a failed step. Tell me how to fix it, or say retry.')
    elif summary.get('status') == 'completed':
        lines.append('')
        lines.append('All steps finished.')
        paid_peers = int(status_result.get('paid_peers') or 0)
        paid_amount = float(status_result.get('paid_amount') or 0)
        if paid_peers > 0:
            if paid_amount > 0:
                lines.append(
                    'Mesh help: %s Neury%s · paid %.4g Neurons.' % (
                        paid_peers, 's' if paid_peers != 1 else '', paid_amount,
                    )
                )
            else:
                lines.append(
                    'Mesh help: %s Neury%s.' % (paid_peers, 's' if paid_peers != 1 else '')
                )
        for item in status_result.get('used_tools') or []:
            res = item.get('result') if isinstance(item, dict) else None
            if isinstance(res, dict) and res.get('image_note'):
                lines.append(str(res.get('image_note')))
                break
            if isinstance(res, dict) and res.get('used_placeholders'):
                lines.append('Some images are temporary web placeholders that must be substituted with real images.')
                break
            if isinstance(res, dict) and res.get('local_dir'):
                lines.append('Files saved to: %s' % res.get('local_dir'))
                break
            if isinstance(res, dict) and res.get('destination') == 'room' and res.get('room'):
                lines.append('Files saved to room: %s' % res.get('room'))
                break
    return '\n'.join(lines)

def _pack_project_turn(out: Dict[str, Any], result: Dict[str, Any], reason: str) -> Dict[str, Any]:
    used = result.get('used_tools') or []
    out['used_tools'] = list(out.get('used_tools') or []) + used
    out['ok'] = True
    summary = result.get('summary') or {}
    formatted_tools = _answer_from_tools(used)
    status_text = _format_project_reply(result)
    if summary.get('status') == 'awaiting_confirm':
        out['text'] = neury_guard_outgoing((formatted_tools + '\n\n' if formatted_tools else '') + 'I have a draft ready. Say OK when you want me to continue.')
    else:
        out['text'] = neury_guard_outgoing(formatted_tools or status_text or 'Done.')
    out['reason'] = reason
    return out

def _plan_job_steps(goal: str, should_abort: Optional[Callable[[], bool]]=None) -> List[Dict[str, Any]]:
    from Support.NeuronOrchestrator import normalize_project_steps, wants_project_mode
    if not wants_project_mode(goal):
        return []
    tools = _tool_specs()
    allowed = [t['name'] for t in tools] + [
        'user.confirm', 'files.classify_batch', 'files.commit_outputs',
    ]
    # Deterministic capability composer first (any combination of asks).
    try:
        from Support.NeuronNeuryAskComposer import compose_project_steps
        composed = compose_project_steps(goal, list(_neury_helpers._turn_attachments or []))
        if composed and composed.get('steps'):
            return normalize_project_steps(composed.get('steps') or [], allowed)
    except Exception:
        pass
    names = ', '.join((t['name'] for t in tools))
    attach_names = ', '.join((os.path.basename(p) for p in _neury_helpers._turn_attachments or []))
    prompt = 'Plan Neuron tool steps for this user request. Reply with JSON only: {"steps":[{"tool":"...","label":"...","args":{...}}]} OR {"ask":"short clarifying question"} if a required peer/file/topic/station is missing.\nUse only these tools: %s\nAlso allowed: files.classify_batch, files.commit_outputs.\nAttached files: %s\nheading_image and table_path are already filled from attachments when present; use $heading_image $table_path $pdf_path $email $room_id $sendee_name $research $article_id $produced $video_paths $photo_paths $receipt_paths in later args.\nNever invent peers, files, stations, channels, article bodies, or facts. For news articles: web.lookup then article.draft_from_news with research=$research, then user.confirm, then article.publish.\nInsert user.confirm before every step that contacts other nodes (mail.send, chat.send, article.publish, peer tools, publish/share).\nOwn-node steps need no confirm. Keep steps in order.\nRequest: %s' % (names, attach_names or 'none', goal)
    try:
        raw = _ollama_generate(_pick_model(), prompt + '\nJSON:', should_abort=should_abort, timeout_sec=90, num_predict=900, num_ctx=4096)
    except Exception:
        return []
    obj = _extract_json_object(raw) or {}
    ask = (obj.get('ask') or '').strip()
    if ask and (not (isinstance(obj.get('steps'), list) and obj.get('steps'))):
        return normalize_project_steps([{'tool': 'user.confirm', 'label': 'Clarification needed', 'args': {'message': ask}}], allowed + ['user.confirm'])
    steps = obj.get('steps') if isinstance(obj.get('steps'), list) else []
    return normalize_project_steps(steps, allowed)

def _execute_planned_project(goal: str, steps: List[Dict[str, Any]], ask_fn, ui_fn, status_fn, should_abort, control: str) -> Dict[str, Any]:
    from Support.NeuronOrchestrator import create_project, seed_project_attachments, save_project, _load_project
    attachments = list(_neury_helpers._turn_attachments or [])
    created = create_project(goal, 'custom', steps, attachments=attachments)
    if not created.get('ok'):
        return created
    project = created.get('project') or _load_project('')
    if project:
        seed_project_attachments(project, attachments)
        try:
            from Support.NeuronNeuryAskComposer import format_task_list
            if not project.get('plan_text') and steps:
                project['plan_text'] = format_task_list(steps)
        except Exception:
            pass
        save_project(project)
    pid = (project or {}).get('id') or ''
    return _run_project(pid, ask_fn=ask_fn, status_fn=status_fn, control=control, should_abort=should_abort, ui_fn=ui_fn)

def _append_project_receipt(project: Optional[Dict[str, Any]], tool: str, ok: bool, args: Optional[Dict[str, Any]]=None, actor: str='local', note: str='') -> None:
    if not isinstance(project, dict) or not project.get('id'):
        return
    receipts = project.setdefault('receipts', [])
    receipts.append({'ts': datetime.now().isoformat(timespec='seconds'), 'actor': actor or 'local', 'tool': tool or '', 'ok': bool(ok), 'args_summary': _short(json.dumps(_redact_args(args or {}), ensure_ascii=True), 160), 'note': _short(note or '', 120)})
    if len(receipts) > 200:
        del receipts[:-200]

def list_recent_receipts(limit: int=30) -> List[Dict[str, Any]]:
    """Recent provenance receipts from the active project (for overlay / Help)."""
    try:
        from Support.NeuronOrchestrator import project_status
        st = project_status()
        project = st.get('project') or {}
        receipts = list(project.get('receipts') or [])
        return receipts[-max(1, int(limit or 30)):]
    except Exception:
        return []

def format_receipts_for_user(limit: int=20) -> str:
    rows = list_recent_receipts(limit=limit)
    if not rows:
        return 'No project activity receipts yet. Start a multi-step job and I will track who did what.'
    lines = ['Recent activity (who did what):']
    for r in rows:
        lines.append('- %s | %s | %s | %s' % (r.get('ts') or '', r.get('actor') or 'local', r.get('tool') or '', 'ok' if r.get('ok') else 'failed'))
    return '\n'.join(lines)

def proactive_suggestions(limit: int=3) -> List[str]:
    """Human-like next-step ideas from open work (suggestions only; never auto-execute)."""
    tips: List[str] = []
    try:
        from Support.NeuronOrchestrator import project_status
        st = project_status()
        if st.get('ok'):
            summary = st.get('summary') or {}
            state = summary.get('status') or ''
            title = summary.get('title') or 'your project'
            if state == 'awaiting_confirm':
                tips.append('Your project "%s" is waiting for OK to continue.' % title)
            elif state == 'active' and int(summary.get('pending') or 0) > 0:
                tips.append('I can continue "%s" (%s steps left).' % (title, summary.get('pending')))
            elif state == 'failed':
                tips.append('"%s" hit a failed step — say "retry" or ask what went wrong.' % title)
    except Exception:
        pass
    try:
        from Support.NeuronCognitiveMesh import discover_skill_peers
        peers = discover_skill_peers('website_build', limit=2, min_score=0.5)
        online = [p for p in peers if not p.get('is_local')]
        if online and len(tips) < limit:
            tips.append('Peer %s looks strong at website building if you want mesh help.' % (online[0].get('name') or 'nearby'))
    except Exception:
        pass
    try:
        from Support.NeuronCapabilityRegistry import build_registry
        missing = [e for e in build_registry().get('entries') or [] if e.get('status') == 'missing' and e.get('id') != 'cloud_ai']
        if missing and len(tips) < limit:
            tips.append('I still lack "%s" — we can look for a Hive/open-source worker later.' % (missing[0].get('label') or missing[0].get('id')))
    except Exception:
        pass
    return tips[:max(1, int(limit or 3))]


__all__ = [
    '_run_project',
    '_run_project_body',
    '_website_manager_action_part',
    '_build_project_parts',
    '_strip_project_html_noise',
    '_format_project_reply',
    '_pack_project_turn',
    '_plan_job_steps',
    '_execute_planned_project',
    '_append_project_receipt',
    'list_recent_receipts',
    'format_receipts_for_user',
    'proactive_suggestions',
]
