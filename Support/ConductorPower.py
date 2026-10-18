"""
ConductorPower — stay-in-Conductor jobs (Hive + Neuron tools + Neury install).

Used only by Support/Conductor.py. Does not open desktop windows.
"""

import json
import os
import time
from datetime import datetime

import Support.Header as init

# Production-ready Hive catalog ids (stubs excluded).
HIVE_READY_IDS = (
    'video_convert',
    'movie_render',
    'captions',
    'voiceover',
    'ocr',
    'documents',
    'photo_batch',
    'enlarge_photos',
    'maps',
    'stems',
    'databases',
    'translate',
    'pictures',
)

HIVE_NEURY_IDS = frozenset(('translate', 'pictures'))

# Neuron stay-inside actions (lanes for Conductor UI).
NEURON_ACTIONS = (
    {
        'id': 'invite_copy',
        'title': 'Invite a friend',
        'blurb': 'Copy a join link you can send by email, WhatsApp, or text.',
        'examples': ['Copy my invite link', 'Share Neuron with a friend'],
        'needs_neury': False,
        'needs_input': False,
        'input_placeholder': '',
        'lane': 'connect',
    },
    {
        'id': 'who_here',
        'title': 'Who is here?',
        'blurb': 'See rooms and people linked on this node.',
        'examples': ['Who is here?', 'Show people on this node'],
        'needs_neury': False,
        'needs_input': False,
        'input_placeholder': '',
        'lane': 'connect',
    },
    {
        'id': 'mesh_status',
        'title': 'Show my mesh',
        'blurb': 'Live peers online and chunks you hold.',
        'examples': ['Show my mesh', 'How many peers are online?'],
        'needs_neury': False,
        'needs_input': False,
        'input_placeholder': '',
        'lane': 'connect',
    },
    {
        'id': 'chat_send',
        'title': 'Send a chat message',
        'blurb': 'Message a peer without opening Chat.',
        'examples': ['Tell Alex I am online', 'Say hello to the first peer'],
        'needs_neury': False,
        'needs_input': True,
        'input_placeholder': 'Peer name or id, then your message (e.g. Alex: hello)',
        'lane': 'connect',
    },
    {
        'id': 'diary_list',
        'title': 'Read my diary',
        'blurb': 'List recent diary entries.',
        'examples': ['Read my diary', 'Show recent diary entries'],
        'needs_neury': False,
        'needs_input': False,
        'input_placeholder': '',
        'lane': 'connect',
    },
    {
        'id': 'diary_post',
        'title': 'Write in my diary',
        'blurb': 'Post a diary entry from here.',
        'examples': ['Today I joined Neuron', 'Note: meeting at 3'],
        'needs_neury': False,
        'needs_input': True,
        'input_placeholder': 'What do you want to remember?',
        'lane': 'connect',
    },
    {
        'id': 'contacts_list',
        'title': 'My contacts',
        'blurb': 'List contacts on this node.',
        'examples': ['Show my contacts', 'List contacts'],
        'needs_neury': False,
        'needs_input': False,
        'input_placeholder': '',
        'lane': 'connect',
    },
    {
        'id': 'synapse_open',
        'title': 'Open Synapse CRM',
        'blurb': 'Launch Neuron Synapse — the living relationship mesh.',
        'examples': ['Open Synapse', 'Open my CRM'],
        'needs_neury': True,
        'needs_input': False,
        'input_placeholder': '',
        'lane': 'connect',
        'neury_prefill': 'Open Neuron Synapse CRM',
    },
    {
        'id': 'synapse_pulse',
        'title': 'Synapse pulse',
        'blurb': 'Who needs attention in your relationship mesh?',
        'examples': ['Show my Synapse pulse', 'Who needs follow-up?'],
        'needs_neury': True,
        'needs_input': False,
        'input_placeholder': '',
        'lane': 'connect',
        'neury_prefill': 'Show my Synapse CRM pulse dashboard and who needs follow-up',
    },
    {
        'id': 'synapse_capture',
        'title': 'Capture a lead in Synapse',
        'blurb': 'Spark a new account and orbit with Neury.',
        'examples': ['Capture Acme as a lead', 'Add a Synapse account'],
        'needs_neury': True,
        'needs_input': True,
        'input_placeholder': 'Company or person name…',
        'lane': 'connect',
        'neury_prefill': 'In Neuron Synapse, create an account named ',
    },
    {
        'id': 'calendar_list',
        'title': 'My calendar',
        'blurb': 'See upcoming events.',
        'examples': ['What is on my calendar?', 'Show upcoming events'],
        'needs_neury': False,
        'needs_input': False,
        'input_placeholder': '',
        'lane': 'connect',
    },
    {
        'id': 'crawl',
        'title': 'Crawl a website',
        'blurb': 'Fetch a page into local knowledge.',
        'examples': ['https://example.com', 'Crawl my blog home page'],
        'needs_neury': False,
        'needs_input': True,
        'input_placeholder': 'https://…',
        'lane': 'discover',
    },
    {
        'id': 'public_search',
        'title': 'Search public files',
        'blurb': 'Find indexed public files on this node.',
        'examples': ['photos', 'manual', 'invoice'],
        'needs_neury': False,
        'needs_input': True,
        'input_placeholder': 'Search words…',
        'lane': 'discover',
    },
    {
        'id': 'market_search',
        'title': 'Browse marketplace',
        'blurb': 'Search listings without opening Market.',
        'examples': ['laptop', 'design help', 'camera'],
        'needs_neury': False,
        'needs_input': True,
        'input_placeholder': 'What are you looking for?',
        'lane': 'discover',
    },
    {
        'id': 'pedia_search',
        'title': 'Search Pedia',
        'blurb': 'Look up encyclopedia entries.',
        'examples': ['mesh network', 'Neuron'],
        'needs_neury': False,
        'needs_input': True,
        'input_placeholder': 'Topic…',
        'lane': 'discover',
    },
    {
        'id': 'article_list',
        'title': 'List articles',
        'blurb': 'See recent articles on this node.',
        'examples': ['List articles', 'Show recent articles'],
        'needs_neury': False,
        'needs_input': False,
        'input_placeholder': '',
        'lane': 'discover',
    },
    {
        'id': 'identity',
        'title': 'Who am I?',
        'blurb': 'Your node name and Neuron balance.',
        'examples': ['Who am I?', 'Show my identity'],
        'needs_neury': False,
        'needs_input': False,
        'input_placeholder': '',
        'lane': 'discover',
    },
    {
        'id': 'retrieve',
        'title': 'Get a file back',
        'blurb': 'Retrieve a neuronized file from the mesh.',
        'examples': ['Retrieve my file', 'Get a file back from the mesh'],
        'needs_neury': False,
        'needs_input': False,
        'input_placeholder': '',
        'lane': 'discover',
    },
    {
        'id': 'article_publish',
        'title': 'Publish an article',
        'blurb': 'Title on first line, body below.',
        'examples': ['My first post\nHello mesh.'],
        'needs_neury': False,
        'needs_input': True,
        'input_placeholder': 'Title on first line, then the article…',
        'lane': 'create',
    },
    {
        'id': 'market_publish',
        'title': 'Publish a listing',
        'blurb': 'Title on first line, description below.',
        'examples': ['Used camera\nGood condition, local pickup.'],
        'needs_neury': False,
        'needs_input': True,
        'input_placeholder': 'Title on first line, body below, then Category: Name',
        'lane': 'create',
    },
    {
        'id': 'channel_create',
        'title': 'Start a channel',
        'blurb': 'Create a Professional channel with a node-hosted website.',
        'examples': ["Nonna's Kitchen", 'My Weekly Show'],
        'needs_neury': False,
        'needs_input': True,
        'input_placeholder': 'Channel name (optional second line: about text)',
        'lane': 'create',
    },
    {
        'id': 'channel_go_live',
        'title': 'Go live (podcast)',
        'blurb': 'Start a live show on your channel now.',
        'examples': ['Go live', 'Start my live podcast'],
        'needs_neury': False,
        'needs_input': False,
        'input_placeholder': '',
        'lane': 'create',
    },
    {
        'id': 'channel_studio',
        'title': 'Open Channel Studio',
        'blurb': 'Manage channel page, videos, and live shows.',
        'examples': ['Open Channel Studio', 'Manage my channel'],
        'needs_neury': False,
        'needs_input': False,
        'input_placeholder': '',
        'lane': 'create',
    },
)


def _hive_mod():
    from Support.MeshAutoOffload import load_hive_app_module
    return load_hive_app_module('HiveJobCatalog.py', '_neuron_conductor_hive_catalog')


def list_hive_jobs(include_neury_gated=False):
    mod = _hive_mod()
    out = []
    for jid in HIVE_READY_IDS:
        needs_ai = jid in HIVE_NEURY_IDS
        if needs_ai and not include_neury_gated:
            continue
        job = mod.job_by_id(jid) or {}
        if not job:
            continue
        out.append({
            'id': jid,
            'title': str(job.get('title') or jid),
            'blurb': str(job.get('blurb') or ''),
            'examples': list(job.get('examples') or []),
            'needs_files': bool(job.get('needs_files')),
            'file_filter': str(job.get('file_filter') or 'All files (*)'),
            'file_label': str(job.get('file_label') or 'Select files'),
            'placeholder': str(job.get('placeholder') or ''),
            'outputs': list(job.get('outputs') or ['txt']),
            'mesh_type': str(job.get('mesh_type') or ''),
            'needs_neury': needs_ai,
            'tool_note': str(job.get('tool_note') or ''),
        })
    return out


def neuron_actions(lane=None, include_neury=True):
    rows = []
    for a in NEURON_ACTIONS:
        if lane and a.get('lane') != lane:
            continue
        if a.get('needs_neury') and not include_neury:
            continue
        rows.append(dict(a))
    return rows


def action_by_id(action_id):
    for a in NEURON_ACTIONS:
        if a['id'] == action_id:
            return dict(a)
    return None


def _default_out_dir():
    base = os.path.join(init.MAIN_PATH, 'System', 'Neuron', 'Neuron_Files', 'conductor_out')
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = os.path.join(base, stamp)
    os.makedirs(path, exist_ok=True)
    return path


def conductor_work_root():
    return os.path.join(init.MAIN_PATH, 'System', 'Neuron', 'Neuron_Files', 'conductor_out')


def cleanup_conductor_work_files(keep_latest: int = 0):
    """
    Remove Neury/Conductor staging folders (converted media, OCR dumps, etc.).
    Does NOT touch Website Builder drafts in PATH_WEBSITES_WORKS.
    """
    import shutil
    removed = 0
    roots = [conductor_work_root()]
    try:
        nf = getattr(init, 'PATH_NEURONFILES', '') or ''
        if nf:
            roots.append(nf)
    except Exception:
        pass
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            names = sorted(os.listdir(root))
        except Exception:
            continue
        # ShareReady_* under Neuron_Files; stamped folders under conductor_out.
        victims = []
        for name in names:
            full = os.path.join(root, name)
            if not os.path.isdir(full):
                continue
            low = name.lower()
            if root.rstrip('\\/').endswith('conductor_out'):
                victims.append(full)
            elif low.startswith('shareready_'):
                victims.append(full)
        if keep_latest > 0 and root.rstrip('\\/').endswith('conductor_out'):
            victims = sorted(victims)[:-int(keep_latest)] if len(victims) > keep_latest else []
        for full in victims:
            try:
                shutil.rmtree(full, ignore_errors=True)
                removed += 1
            except Exception:
                pass
    return removed


def neury_installed():
    try:
        from Support.NeuronAIInstaller import is_ai_installed
        return bool(is_ai_installed(ensure_running=False))
    except Exception:
        return False


def invitation_for_job(job_id):
    """Build the invite-shaped dict HiveToolPrep expects for this catalog job."""
    jid = str(job_id or '').strip()
    mod = _hive_mod()
    job = mod.job_by_id(jid) or {}
    mesh = str(job.get('mesh_type') or jid).strip()
    tools = list(job.get('tools') or [])
    return {
        'job_type': mesh,
        'mesh_job_type': mesh,
        'tools': tools,
        'job_data': {
            'mesh_job_type': mesh,
            'job_type': mesh,
            'tools': tools,
        },
    }


def ensure_job_tools_ready(job_id, parent=None):
    """
    If this hive job needs apps that are not on the PC, prompt to download/install them.
    Returns {'ok': bool, 'message': str, 'deferred': bool}.
    deferred=True means a non-blocking installer was opened — retry after it finishes.
    """
    jid = str(job_id or '').strip()
    if not jid:
        return {'ok': True, 'message': '', 'deferred': False}
    invite = invitation_for_job(jid)
    try:
        from Support.HiveToolPrep import (
            missing_tools, run_prep_wizard, find_whisper_model, find_piper_voice,
            ensure_whisper_model, ensure_piper_voice,
        )
    except Exception:
        return {'ok': True, 'message': '', 'deferred': False}

    missing = missing_tools(invite)
    job = (_hive_mod().job_by_id(jid) or {})
    tool_names = [str(t or '').lower() for t in (job.get('tools') or [])]

    # Binaries present but voice/model packs still missing (common after first tool install)
    need_whisper_model = (
        any(('whisper' in t) or t == 'main' for t in tool_names)
        and not find_whisper_model()
    )
    need_piper_voice = (
        any('piper' in t for t in tool_names)
        and not find_piper_voice()
    )

    if not missing and not need_whisper_model and not need_piper_voice:
        return {'ok': True, 'message': '', 'deferred': False}

    image_only = bool(missing) and all(s.get('image_gen') for _, s in missing)
    if image_only:
        try:
            from Support.HiveInvitationUI import show_hive_tool_install_prompt
            from Support.NeuronAIInstaller import (
                ImageGeneratorInstallerDialog,
                is_image_generator_installed,
                is_image_generator_ready,
            )
            from PySide6.QtCore import Qt
        except Exception as exc:
            return {
                'ok': False,
                'message': 'Could not open the image generator installer: %s' % exc,
                'deferred': False,
            }
        if is_image_generator_installed() and is_image_generator_ready():
            return {'ok': True, 'message': '', 'deferred': False}
        labels = [(s.get('label') or n) for n, s in missing]
        if not show_hive_tool_install_prompt(labels, parent=parent):
            return {
                'ok': False,
                'message': 'Install declined. This job needs %s.' % ', '.join(labels),
                'deferred': False,
            }
        # Modal install — wait until finished, then continue the job
        dlg = ImageGeneratorInstallerDialog(parent=None)
        try:
            dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        except Exception:
            pass
        if getattr(dlg, 'stack', None):
            try:
                dlg._start()
            except Exception:
                pass
        dlg.exec()
        if is_image_generator_installed() and is_image_generator_ready():
            return {'ok': True, 'message': '', 'deferred': False}
        if is_image_generator_installed():
            return {'ok': True, 'message': '', 'deferred': False}
        return {
            'ok': False,
            'message': 'Image generator install did not finish. Try again when it is ready.',
            'deferred': False,
        }

    if missing:
        if not run_prep_wizard(invite, parent=parent):
            labels = [(s.get('label') or n) for n, s in missing]
            return {
                'ok': False,
                'message': (
                    'Required apps are not ready yet.\n'
                    'This job needs: %s'
                ) % ', '.join(labels),
                'deferred': False,
            }

    # Model/voice packs after binary install (or when binary was already present)
    try:
        from Support.HiveInvitationUI import show_hive_tool_install_prompt
    except Exception:
        show_hive_tool_install_prompt = None

    if need_whisper_model:
        if show_hive_tool_install_prompt and not show_hive_tool_install_prompt(
            ['whisper.cpp speech model'], parent=parent,
        ):
            return {
                'ok': False,
                'message': 'Install declined. Captions need a whisper.cpp speech model.',
                'deferred': False,
            }
        ensure_whisper_model()
        if not find_whisper_model():
            return {
                'ok': False,
                'message': 'Could not download the whisper.cpp speech model.',
                'deferred': False,
            }

    if need_piper_voice:
        if show_hive_tool_install_prompt and not show_hive_tool_install_prompt(
            ['Piper voice model'], parent=parent,
        ):
            return {
                'ok': False,
                'message': 'Install declined. Voiceovers need a Piper voice model.',
                'deferred': False,
            }
        ensure_piper_voice()
        if not find_piper_voice():
            return {
                'ok': False,
                'message': 'Could not download a Piper voice model.',
                'deferred': False,
            }

    return {'ok': True, 'message': '', 'deferred': False}


def run_hive_job_local(job_id, files, instruction, export_format='', out_dir='', progress_fn=None):
    jid = str(job_id or '').strip()
    if jid in HIVE_NEURY_IDS and not neury_installed():
        return {
            'ok': False,
            'error': 'neury_required',
            'message': 'Install Neury first — this job needs onboard AI.',
            'produced': [],
            'out_dir': '',
        }
    mod = _hive_mod()
    job = mod.job_by_id(jid) or {}
    if not job:
        return {'ok': False, 'error': 'unknown_job', 'message': 'Unknown job.', 'produced': [], 'out_dir': ''}
    mesh_type = str(job.get('mesh_type') or '').strip()
    outputs = list(job.get('outputs') or ['txt'])
    fmt = (export_format or (outputs[0] if outputs else 'txt')).strip().lstrip('.')
    dest = out_dir or _default_out_dir()
    paths = [p for p in (files or []) if p and os.path.isfile(p)]
    if job.get('needs_files') and not paths:
        return {
            'ok': False,
            'error': 'need_files',
            'message': 'Drop or pick at least one file first.',
            'produced': [],
            'out_dir': dest,
        }

    if jid == 'databases':
        if not paths:
            return {
                'ok': False,
                'error': 'need_files',
                'message': 'Drop or pick at least one spreadsheet or database file first.',
                'produced': [],
                'out_dir': dest,
            }
        sql = (instruction or '').strip()
        if not sql:
            return {
                'ok': False,
                'error': 'need_input',
                'message': 'Describe the query (or paste SQL), then press Go.',
                'produced': [],
                'out_dir': dest,
            }
        try:
            from Support.MeshAutoOffload import run_sql_job
            if progress_fn:
                progress_fn(10, 'Running spreadsheet query…')

            def _status(msg):
                if progress_fn:
                    progress_fn(55, str(msg or 'Working…'))

            res = run_sql_job(sql, paths[0], status_fn=_status)
            if not isinstance(res, dict) or not res.get('ok'):
                return {
                    'ok': False,
                    'error': 'run_failed',
                    'message': str((res or {}).get('error') or 'Query failed.'),
                    'produced': [],
                    'out_dir': dest,
                    'raw': res,
                }
            produced = []
            for key in ('path', 'out_path', 'file'):
                if res.get(key):
                    produced.append(res[key])
            produced.extend(list(res.get('files') or res.get('produced') or []))
            notes = []
            text = str(res.get('text') or res.get('summary') or res.get('preview') or '').strip()
            rows = res.get('rows')
            if rows is not None and not produced:
                if progress_fn:
                    progress_fn(85, 'Writing results…')
                out_path, preview = _rows_to_csv_and_preview(rows, dest)
                produced.append(out_path)
                notes.append(preview)
            elif text:
                notes.append(text)
            elif rows is not None:
                _path, preview = _rows_to_csv_and_preview(rows, dest)
                if _path not in produced:
                    produced.append(_path)
                notes.append(preview)
            if progress_fn:
                progress_fn(100, 'Query complete')
            if not produced and not notes:
                return {
                    'ok': False,
                    'error': 'empty',
                    'message': 'Query finished with no rows to show.',
                    'produced': [],
                    'out_dir': dest,
                    'raw': res,
                }
            return {
                'ok': True,
                'produced': [p for p in produced if p],
                'out_dir': dest,
                'notes': notes,
                'raw': res,
                'status': 'completed',
            }
        except Exception as exc:
            return {
                'ok': False,
                'error': 'run_failed',
                'message': str(exc),
                'produced': [],
                'out_dir': dest,
            }

    try:
        result = mod.run_assigned_job(
            mesh_type,
            paths,
            instruction or '',
            fmt,
            dest,
            progress_fn=progress_fn,
        )
    except Exception as exc:
        return {
            'ok': False,
            'error': 'run_failed',
            'message': str(exc),
            'produced': [],
            'out_dir': dest,
        }
    produced = []
    notes = []
    if isinstance(result, dict):
        produced = list(result.get('produced') or result.get('files') or [])
        notes = list(result.get('notes') or [])
        if result.get('path'):
            produced.append(result['path'])
        status = str(result.get('status') or '').lower()
        text_blob = str(
            result.get('text') or result.get('preview') or result.get('summary') or ''
        ).strip()
        if result.get('ok') is False or status in ('error', 'tool_missing', 'failed'):
            ok = False
        else:
            has_output = bool(produced) or bool(notes) or bool(text_blob)
            ok = has_output and (
                result.get('ok') is True
                or status in ('completed', 'ok', 'done')
                or (not status and has_output)
            )
        msg = str(result.get('error') or result.get('message') or '')
        if not msg and notes and not ok:
            msg = str(notes[0])
        if not ok and not msg:
            msg = 'Job finished with no output files or notes.'
        err_code = str(result.get('error') or '').strip()
        if not ok and status == 'tool_missing':
            err_code = 'tool_missing'
        out_notes = list(notes)
        if text_blob and text_blob not in out_notes:
            out_notes.append(text_blob)
        # OCR / translate often return text only — write a .txt so Preview can open it.
        if ok and jid in ('ocr', 'translate', 'documents') and out_notes:
            has_text_file = any(
                str(p).lower().endswith(('.txt', '.md', '.rtf'))
                for p in produced if p
            )

            def _usable(blob):
                text = str(blob or '').strip()
                if len(text) < 40:
                    return False
                if text.startswith('{') or text.startswith('['):
                    try:
                        data = json.loads(text)
                    except Exception:
                        data = None
                    if isinstance(data, (dict, list)):
                        return False
                if 'boarding_pass' in text.lower() and len(text) < 200:
                    return False
                return True

            if not has_text_file:
                blob = ''
                for n in out_notes:
                    if _usable(n):
                        blob = str(n).strip()
                        break
                if not blob and _usable(text_blob):
                    blob = text_blob
                if blob:
                    try:
                        os.makedirs(dest, exist_ok=True)
                        out_txt = os.path.join(dest, '%s_result.txt' % (jid or 'out'))
                        with open(out_txt, 'w', encoding='utf-8') as fh:
                            fh.write(blob)
                        produced.append(out_txt)
                    except Exception:
                        pass
        return {
            'ok': ok,
            'produced': [p for p in produced if p],
            'out_dir': dest,
            'notes': out_notes,
            'message': msg,
            'status': status,
            'error': err_code if not ok else '',
            'tool': str(result.get('tool') or result.get('missing') or ''),
            'text': text_blob,
            'raw': result,
        }
    if isinstance(result, str) and result:
        return {'ok': True, 'produced': [result], 'out_dir': dest, 'notes': [], 'message': ''}
    if isinstance(result, (list, tuple)):
        return {'ok': True, 'produced': list(result), 'out_dir': dest, 'notes': [], 'message': ''}
    return {
        'ok': False,
        'error': 'empty',
        'message': 'Job finished with no output files.',
        'produced': [],
        'out_dir': dest,
        'raw': result,
    }


def start_neury_install(on_progress=None, on_status=None, on_done=None, on_fail=None):
    """Start AIInstallerWorker; returns the worker (caller must keep a reference)."""
    from Support.NeuronAIInstaller import PCDiagnostic, ModelSelector, ModelFetcher
    from Support.NeuronAIInstallerWorker import AIInstallerWorker

    diagnostic = PCDiagnostic()
    selector = ModelSelector(diagnostic)
    models = ModelFetcher()._get_default_models()
    selector.set_available_models(models or [])
    model, err = selector.select_model()
    if not model:
        if on_fail:
            on_fail(err or 'No suitable model found for this PC.')
        return None
    if on_status:
        on_status('Selected model: %s' % (model.get('name') or model.get('id') or 'AI'))
    worker = AIInstallerWorker(model, diagnostic)
    if on_progress:
        worker.progress_updated.connect(on_progress)
    if on_status:
        worker.status_changed.connect(on_status)

    def _after_install_done():
        try:
            from Support.NeuronNeuryPsychology import ensure_psychology_ingested
            ensure_psychology_ingested(force=False)
        except Exception:
            pass
        if on_done:
            on_done()

    worker.installation_complete.connect(_after_install_done)
    if on_fail:
        worker.installation_failed.connect(on_fail)
    worker.start()
    return worker


def run_neuron_tool(name, args=None):
    from Support.NeuronNeuryAgent import exec_tool_for_ui
    raw = exec_tool_for_ui(name, args or {})
    if not isinstance(raw, dict):
        return {'ok': False, 'text': '', 'raw': raw, 'error': 'bad_result'}
    text = str(raw.get('text') or raw.get('message') or raw.get('summary') or '')
    if not text and raw.get('ok'):
        text = _stringify_result(raw)
    return {
        'ok': bool(raw.get('ok')),
        'text': text,
        'error': '' if raw.get('ok') else str(raw.get('error') or raw.get('reason') or ''),
        'raw': raw,
    }


def _stringify_result(raw):
    parts = []
    for key in (
        'items', 'events', 'rows', 'results', 'entries', 'ads', 'articles',
        'contacts', 'posts', 'hits', 'cards', 'documents',
    ):
        rows = raw.get(key)
        if isinstance(rows, list) and rows:
            for i, row in enumerate(rows[:20]):
                if isinstance(row, dict):
                    label = (
                        row.get('title') or row.get('name') or row.get('subject')
                        or row.get('text') or row.get('preview') or row.get('query')
                        or row.get('path') or row.get('file') or row.get('peer_name')
                        or row.get('peer') or ''
                    )
                    parts.append('• %s' % (label or str(row)[:120]))
                else:
                    parts.append('• %s' % str(row)[:120])
            if len(rows) > 20:
                parts.append('…and %s more' % (len(rows) - 20))
            break
    if not parts:
        for key, val in raw.items():
            if key in ('ok', 'error', 'reason', 'artifacts') or val in (None, '', [], {}):
                continue
            parts.append('%s: %s' % (key, str(val)[:200]))
    return '\n'.join(parts) if parts else 'Done.'


def _need_input(message, prompt=''):
    return {
        'ok': False,
        'error': 'need_input',
        'text': str(message or ''),
        'prompt': str(prompt or message or 'Please provide the missing details, or Abort.'),
    }


def _rows_to_csv_and_preview(rows, dest, basename='query_result.csv'):
    """Write spreadsheet rows to dest and build a short text preview."""
    import csv
    os.makedirs(dest, exist_ok=True)
    path = os.path.join(dest, basename)
    preview_lines = []
    if not rows:
        with open(path, 'w', encoding='utf-8', newline='') as fh:
            fh.write('')
        return path, 'Query returned 0 rows.'
    if isinstance(rows[0], dict):
        cols = list(rows[0].keys())
        with open(path, 'w', encoding='utf-8', newline='') as fh:
            writer = csv.DictWriter(fh, fieldnames=cols, extrasaction='ignore')
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k) for k in cols})
        preview_lines.append('%s row(s), %s column(s).' % (len(rows), len(cols)))
        preview_lines.append('Columns: %s' % ', '.join(str(c) for c in cols[:12]))
        for row in rows[:12]:
            preview_lines.append(
                '• ' + ' | '.join(str(row.get(c, ''))[:40] for c in cols[:6])
            )
        if len(rows) > 12:
            preview_lines.append('…and %s more' % (len(rows) - 12))
    else:
        with open(path, 'w', encoding='utf-8', newline='') as fh:
            writer = csv.writer(fh)
            for row in rows:
                if isinstance(row, (list, tuple)):
                    writer.writerow(list(row))
                else:
                    writer.writerow([row])
        preview_lines.append('%s row(s).' % len(rows))
        for row in rows[:12]:
            preview_lines.append('• %s' % str(row)[:120])
    return path, '\n'.join(preview_lines)


def format_tool_result(result):
    if not isinstance(result, dict):
        return str(result or '')
    if str(result.get('error') or '') == 'need_input':
        return str(result.get('text') or result.get('prompt') or 'More information is needed.')
    if result.get('ok'):
        return str(result.get('text') or _stringify_result(result.get('raw') or result) or 'Done.')
    err = str(result.get('error') or result.get('message') or 'Something went wrong.')
    text = str(result.get('text') or '')
    return (text + '\n\n' + err).strip() if text else err


def format_hive_result(result):
    if not isinstance(result, dict):
        return str(result or '')
    if not result.get('ok'):
        err = str(result.get('message') or result.get('error') or 'Job failed.')
        code = str(result.get('error') or result.get('status') or '').strip()
        if code == 'tool_missing':
            tool = str(result.get('tool') or result.get('missing') or '').strip()
            if tool:
                return (
                    'Missing tool: %s\n%s\n\n'
                    'Neuron can download and set it up if you press Go again.'
                ) % (tool, err)
            return (
                '%s\n\nNeuron can download the required app if you press Go again.'
                % err
            )
        return err
    lines = []
    produced = [p for p in (result.get('produced') or []) if p]
    existing = [p for p in produced if os.path.isfile(p) or os.path.isdir(p)]
    if existing:
        lines.append('Results ready:')
        for p in existing[:30]:
            lines.append('• %s' % p)
        if len(existing) > 30:
            lines.append('…and %s more' % (len(existing) - 30))
    elif produced:
        lines.append('Job reported files, but they were not found on disk.')
        for p in produced[:8]:
            lines.append('• %s' % p)
    notes = [n for n in (result.get('notes') or []) if n]
    if notes:
        if lines:
            lines.append('')
        lines.extend(str(n) for n in notes[:8])
    preview = str(result.get('text') or result.get('preview') or '').strip()
    if preview and preview not in lines:
        if lines:
            lines.append('')
        lines.append(preview)
    out_dir = str(result.get('out_dir') or '')
    if out_dir:
        if lines:
            lines.append('')
        lines.append('Folder: %s' % out_dir)
    if lines:
        return '\n'.join(lines)
    status = str(result.get('status') or '').strip()
    if status:
        return 'Job finished (%s) with no output files or notes.' % status
    return 'Job finished with no output files or notes.'


# ---- Neuron action runners ----

def tool_invite_copy():
    try:
        from Support.PhoneUtil import build_phone_connect_urls
        from PySide6.QtWidgets import QApplication
        urls = build_phone_connect_urls('/') or {}
        lan = str(urls.get('lan_url') or '').strip()
        remote = str(urls.get('remote_url') or '').strip()
        recommended = None
        for c in (urls.get('candidates') or []):
            if c.get('recommended') and c.get('available'):
                recommended = str(c.get('url') or '').strip()
                break
        link = recommended or remote or lan
        if not link:
            return {'ok': False, 'text': '', 'error': 'Could not build an invite link yet.'}
        clip = QApplication.clipboard()
        if clip is not None:
            clip.setText(link)
        body = (
            'Invite link copied to the clipboard.\n\n'
            '%s\n\n'
            'Paste it into email, WhatsApp, or a message.\n'
            'When they open it, they can join your mesh.'
        ) % link
        if lan and lan != link:
            body += '\n\nOn the same Wi-Fi they can also use:\n%s' % lan
        return {'ok': True, 'text': body, 'error': ''}
    except Exception as exc:
        return {'ok': False, 'text': '', 'error': str(exc)}


def tool_who_is_here():
    lines = []
    rooms = list(getattr(init, 'ROOMS', None) or [])
    if not rooms:
        lines.append('No rooms yet — your mesh is still small.')
        lines.append('Invite a friend, then come back here.')
    else:
        lines.append('Rooms on this Neuron:')
        for room in rooms[:12]:
            if not isinstance(room, dict):
                continue
            nm = str(room.get('name') or room.get('label') or room.get('room_id') or 'Room')
            typ = str(room.get('type') or '')
            peers = str(room.get('allowed_peers') or '')
            n = len([p for p in peers.split(';') if p.strip()]) if peers else 0
            extra = (' · %s' % typ) if typ else ''
            if n:
                extra += ' · %s people linked' % n
            lines.append('• %s%s' % (nm, extra))
        if len(rooms) > 12:
            lines.append('…and %s more' % (len(rooms) - 12))
    return {'ok': True, 'text': '\n'.join(lines), 'error': ''}


def tool_mesh_status():
    lines = []
    try:
        from Support.NetworkHubWeb import personal_mesh_status, network_live_counts
        from Support.Utils import query_database
        st = personal_mesh_status() or {}
        peers = int(st.get('peers_online') or 0)
        chunks = int(st.get('peer_chunks_held') or 0)
        files = int(st.get('neuronized_files') or 0)
        complete = int(st.get('files_complete') or 0)
        incomplete = max(0, files - complete)
        bal = float(st.get('balance') or 0)
        hint = str(st.get('earn_hint') or '').strip()
        lines.append('%s peers online' % peers)
        lines.append('%s chunks you hold for peers' % chunks)
        lines.append('%s files you protected' % files)
        if complete:
            lines.append('%s fully synced' % complete)
        if incomplete:
            lines.append('%s still syncing' % incomplete)
        if bal > 0.05:
            lines.append('Balance: %.1f Neurons' % bal)
        if hint:
            lines.append('Just now: %s' % hint)
        live = network_live_counts() or {}
        unread = int(live.get('chat_unread') or 0)
        if unread:
            lines.append('%s unread chat' % unread)
        for btype, label in (
            (getattr(init, 'BALANCE_SHEET_I_STORED_PEER_DATA', ''), 'Stored peer data'),
            (getattr(init, 'BALANCE_SHEET_RELAY_ACTIVITY_I_PERFORM', ''), 'Relayed for peers'),
        ):
            if not btype or not getattr(init, 'pool_main_db', None):
                continue
            rows = query_database(
                init.pool_main_db,
                "SELECT COUNT(*) FROM Balance WHERE type = ?",
                (btype,),
                commit=False,
            )
            n = int(rows[0][0] or 0) if rows and rows[0] else 0
            if n:
                lines.append('%s %s times' % (label, n))
        rooms = len(list(getattr(init, 'ROOMS', None) or []))
        if rooms:
            lines.append('%s rooms on this node' % rooms)
    except Exception as exc:
        return {'ok': False, 'text': '', 'error': str(exc)}
    if not lines:
        lines.append('Mesh waking up…')
    return {'ok': True, 'text': '\n'.join('• %s' % L for L in lines), 'error': ''}


def tool_diary_list():
    return run_neuron_tool('diary.list', {'limit': 20})


def tool_diary_post(text):
    return run_neuron_tool('diary.post', {'text': (text or '').strip()})


def tool_chat_send(raw):
    text = (raw or '').strip()
    if not text:
        return {'ok': False, 'text': '', 'error': 'Type: PeerName: your message'}
    peer = ''
    message = text
    if ':' in text:
        peer, message = text.split(':', 1)
        peer = peer.strip()
        message = message.strip()
    args = {'text': message}
    if peer:
        args['peer'] = peer
        args['to'] = peer
        args['peer_name'] = peer
    return run_neuron_tool('chat.send', args)


def tool_calendar_list():
    now = int(time.time())
    return run_neuron_tool('calendar.list', {
        'start_ts': now - 86400,
        'end_ts': now + 14 * 86400,
    })


def tool_contacts_list():
    return run_neuron_tool('contacts.list', {})


def tool_market_search(query):
    return run_neuron_tool('market.search', {'query': (query or '').strip() or '*'})


def tool_pedia_search(query):
    return run_neuron_tool('pedia.search', {'query': (query or '').strip()})


def tool_public_search(query):
    return run_neuron_tool('public_files.search', {'query': (query or '').strip()})


def tool_crawl(url):
    u = (url or '').strip()
    if u and not u.lower().startswith('http'):
        u = 'https://' + u
    return run_neuron_tool('crawl.start', {'url': u})


def tool_article_list():
    return run_neuron_tool('article.list', {'limit': 20})


def tool_article_publish(raw):
    text = (raw or '').strip()
    if not text:
        return _need_input(
            'Title on the first line, then the article body below.\n'
            'Press Go when ready, or Abort to cancel.',
            'Title on first line, then the article…',
        )
    lines = text.splitlines()
    title = lines[0].strip()
    body = '\n'.join(lines[1:]).strip()
    if not title:
        return _need_input(
            'Add a title on the first line, then the body.',
            'Title on first line, then the article…',
        )
    if not body:
        return _need_input(
            'Add the article body under the title (second line onward).',
            'Title on first line, then the article…',
        )
    saved = run_neuron_tool('article.save', {'title': title, 'body': body, 'text': body, 'article': body})
    if not saved.get('ok'):
        return {
            'ok': False,
            'text': str(saved.get('text') or ''),
            'error': str(saved.get('error') or 'Could not save the article draft.'),
        }
    article_id = ''
    raw_saved = saved.get('raw') if isinstance(saved.get('raw'), dict) else {}
    article_id = str(raw_saved.get('article_id') or saved.get('article_id') or '').strip()
    if not article_id:
        # run_neuron_tool may only put id in raw
        try:
            article_id = str((saved.get('raw') or {}).get('article_id') or '').strip()
        except Exception:
            article_id = ''
    if not article_id:
        return {
            'ok': False,
            'text': format_tool_result(saved),
            'error': 'Draft saved but no article_id was returned.',
        }
    published = run_neuron_tool('article.publish', {'article_id': article_id})
    if published.get('ok'):
        return {
            'ok': True,
            'text': format_tool_result(published) or ('Published: %s' % title),
            'error': '',
            'raw': published.get('raw') or published,
        }
    return {
        'ok': False,
        'text': format_tool_result(published),
        'error': str(published.get('error') or 'Publish failed.'),
    }


def _default_market_category():
    try:
        import json as _json
        path = os.path.join(init.PATH_JSON, 'Categories.json')
        with open(path, 'r', encoding='utf-8') as fh:
            data = _json.load(fh)
        if isinstance(data, dict) and data:
            return str(sorted(data.keys())[0])
    except Exception:
        pass
    return 'Miscellaneous'


def tool_market_publish(raw):
    text = (raw or '').strip()
    if not text:
        return _need_input(
            'Line 1: title\nLine 2+: description\nOptional last line: Category: Name\n'
            'Press Go when ready, or Abort to cancel.',
            'Title on first line, then details…',
        )
    lines = [ln for ln in text.splitlines()]
    title = (lines[0] if lines else '').strip()
    rest = lines[1:] if len(lines) > 1 else []
    category = ''
    body_lines = []
    for ln in rest:
        low = ln.strip().lower()
        if low.startswith('category:'):
            category = ln.split(':', 1)[1].strip()
        else:
            body_lines.append(ln)
    body = '\n'.join(body_lines).strip()
    if not title:
        return _need_input(
            'Add a listing title on the first line.',
            'Title on first line, then details…',
        )
    if not body:
        return _need_input(
            'Add a description under the title.',
            'Title on first line, then details…',
        )
    if not category:
        # Ask rather than silently inventing — show default they can keep
        default_cat = _default_market_category()
        return _need_input(
            'Add a category. Append a line like:\nCategory: %s\n\n'
            'Then press Go, or Abort to cancel.' % default_cat,
            'Title\nDescription\nCategory: %s' % default_cat,
        )
    res = run_neuron_tool(
        'market.publish',
        {
            'title': title,
            'body': body,
            'text': body,
            'description': body,
            'category': category,
        },
    )
    if res.get('ok'):
        return res
    # Community ads fallback — needs a room_id
    try:
        from Support.CommunityAdsNet import publish_ad
        room_id = ''
        for room in list(getattr(init, 'ROOMS', None) or []):
            if isinstance(room, dict) and room.get('room_id'):
                room_id = str(room.get('room_id'))
                break
        if room_id:
            ok, msg = publish_ad(room_id, title, body)
            if ok:
                return {'ok': True, 'text': 'Community listing published.', 'error': '', 'raw': {'ad': msg}}
            return {'ok': False, 'text': '', 'error': str(msg or 'Publish failed')}
    except Exception:
        pass
    return res


def tool_channel_create(raw):
    text = (raw or '').strip()
    if not text:
        return _need_input(
            'Type a channel name on the first line.\n'
            'Optional second line: about text.\n'
            'Press Go when ready, or Abort to cancel.',
            'Channel name (optional second line: about text)',
        )
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    name = lines[0] if lines else ''
    description = '\n'.join(lines[1:]) if len(lines) > 1 else ''
    if not name:
        return _need_input(
            'Add a channel name on the first line.',
            'Channel name (optional second line: about text)',
        )
    args = {'name': name}
    if description:
        args['description'] = description
    return run_neuron_tool('channel.create', args)


def tool_channel_go_live(raw):
    room = (raw or '').strip()
    args = {}
    if room:
        args['room'] = room
    return run_neuron_tool('channel.go_live', args)


def tool_channel_studio(raw):
    room = (raw or '').strip()
    args = {}
    if room:
        args['room'] = room
    return run_neuron_tool('channel.open_studio', args)


def tool_identity():
    try:
        from Support.NeuronOrchestrator import identity_card
        card = identity_card(refresh_passport=False) or {}
        name = str(card.get('name') or '')
        lines = [
            'Your Neuron Identity',
            'Name: %s' % (name or '-'),
            'Trust: %s (%s)' % (card.get('reliability_score'), card.get('reliability_tier') or 'New'),
            'Neurons: %s' % (
                card.get('neurons_balance') if card.get('neurons_balance') is not None else '-'
            ),
        ]
        fp = str(card.get('pubkey_fingerprint') or '').strip()
        if fp:
            lines.append('Key fingerprint: %s' % fp)
        skills = card.get('passport_skills') or []
        if skills:
            lines.append('')
            lines.append('Top skills:')
            for row in skills[:5]:
                if isinstance(row, dict):
                    lines.append('- %s (%s)' % (row.get('name'), row.get('score')))
        note = str(card.get('trust_status') or '').strip()
        if note:
            lines.append('')
            lines.append(note)
        return {'ok': True, 'text': '\n'.join(lines), 'error': '', 'raw': card}
    except Exception as exc:
        name = ''
        if isinstance(init.ME, dict):
            name = str(init.ME.get('node_name') or init.ME.get('nickname') or '').strip()
        bal = run_neuron_tool('neurons.balance', {})
        lines = ['Node: %s' % (name or 'this computer')]
        if bal.get('text'):
            lines.append(bal['text'])
        lines.append('(Full identity unavailable: %s)' % exc)
        return {'ok': True, 'text': '\n'.join(lines), 'error': ''}


def _neuronized_file_choices():
    icons = getattr(init, 'DESKTOPICONS', None) or {}
    iterable = icons.values() if isinstance(icons, dict) else []
    out = []
    for row in iterable:
        if not isinstance(row, dict):
            continue
        try:
            if int(row.get('neuronizing_max') or 0) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        icon_id = str(row.get('icon_id') or row.get('id') or '')
        label = str(row.get('label') or row.get('name') or icon_id)
        if icon_id:
            out.append({'icon_id': icon_id, 'label': label, 'row': row})
    out.sort(key=lambda r: r['label'].lower())
    return out


def tool_retrieve(selection=''):
    from Support.Files import ensure_neuronized_icon_selected, retrieve_neuronized_files
    choices = _neuronized_file_choices()
    if not choices:
        return {
            'ok': False,
            'text': '',
            'error': 'No protected (neuronized) files on this node yet. Keep a file safe first.',
        }
    want = (selection or '').strip()
    chosen = None
    if want:
        if want.isdigit():
            idx = int(want) - 1
            if 0 <= idx < len(choices):
                chosen = choices[idx]
        if chosen is None:
            low = want.lower()
            for c in choices:
                if c['label'].lower() == low or c['icon_id'] == want:
                    chosen = c
                    break
            if chosen is None:
                for c in choices:
                    if low in c['label'].lower():
                        chosen = c
                        break
        if chosen is None:
            listing = '\n'.join('%s. %s' % (i + 1, c['label']) for i, c in enumerate(choices[:30]))
            return _need_input(
                'No match for "%s". Pick a number or file name:\n\n%s\n\n'
                'Type your choice, then Go — or Abort.' % (want, listing),
                'File number or name…',
            )
        init.selected_icon_id = chosen['icon_id']
        ensure_neuronized_icon_selected(chosen['icon_id'])
    else:
        listing = '\n'.join('%s. %s' % (i + 1, c['label']) for i, c in enumerate(choices[:30]))
        extra = '' if len(choices) <= 30 else '\n…and %s more' % (len(choices) - 30)
        return _need_input(
            'Which protected file should I retrieve?\n\n%s%s\n\n'
            'Type the number or file name, then Go — or Abort.' % (listing, extra),
            'File number or name…',
        )
    try:
        retrieve_neuronized_files('file', skip_recompose=False)
        label = chosen['label'] if chosen else str(getattr(init, 'selected_icon_id', '') or 'file')
        return {
            'ok': True,
            'text': 'Retrieve started for: %s\nPieces will reassemble as peers answer.' % label,
            'error': '',
        }
    except Exception as exc:
        return {'ok': False, 'text': '', 'error': str(exc)}


def run_neuron_action(action_id, user_text='', progress_fn=None):
    aid = str(action_id or '').strip()
    text = (user_text or '').strip()

    def _prog(pct, msg=''):
        if progress_fn:
            progress_fn(int(pct or 0), str(msg or ''))

    runners = {
        'invite_copy': lambda: tool_invite_copy(),
        'who_here': lambda: tool_who_is_here(),
        'mesh_status': lambda: tool_mesh_status(),
        'chat_send': lambda: tool_chat_send(text),
        'diary_list': lambda: tool_diary_list(),
        'diary_post': lambda: tool_diary_post(text),
        'contacts_list': lambda: tool_contacts_list(),
        'calendar_list': lambda: tool_calendar_list(),
        'crawl': lambda: tool_crawl(text),
        'public_search': lambda: tool_public_search(text),
        'market_search': lambda: tool_market_search(text),
        'pedia_search': lambda: tool_pedia_search(text),
        'article_list': lambda: tool_article_list(),
        'identity': lambda: tool_identity(),
        'retrieve': lambda: tool_retrieve(text),
        'article_publish': lambda: tool_article_publish(text),
        'market_publish': lambda: tool_market_publish(text),
        'channel_create': lambda: tool_channel_create(text),
        'channel_go_live': lambda: tool_channel_go_live(text),
        'channel_studio': lambda: tool_channel_studio(text),
    }
    fn = runners.get(aid)
    if not fn:
        return {'ok': False, 'text': '', 'error': 'Unknown action.'}
    act = action_by_id(aid) or {}
    if act.get('needs_input') and not text and aid != 'retrieve':
        return _need_input(
            act.get('input_placeholder') or 'Type something first, then Go — or Abort.',
            act.get('input_placeholder') or 'Type here…',
        )
    try:
        _prog(15, 'Starting %s…' % (act.get('title') or aid))
        result = fn()
        _prog(100, 'Completed' if (result or {}).get('ok') else 'Finished')
        return result
    except Exception as exc:
        return {'ok': False, 'text': '', 'error': str(exc)}
