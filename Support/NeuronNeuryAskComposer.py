# NeuronNeuryAskComposer.py
# General multi-ask analyzer: any combination of user asks -> ordered tasks.
# No branded one-off recipes. Demo scenarios are test fixtures only.


import os
import re
import shutil
import time
from typing import Any, Callable, Dict, List, Optional, Tuple


# Intent -> tool mapping. Patterns are English after routing normalization.
# input_kinds / output_kinds help chain produced files between steps.
CAPABILITIES: Tuple[Dict[str, Any], ...] = (
    {
        'id': 'classify_inputs',
        'tool': 'files.classify_batch',
        'label': 'Sort your files by type',
        'activity': 'Reading your folder and sorting videos, photos, and documents',
        'patterns': (
            r'\b(phone\s+dump|folder|attachments?|these\s+files|my\s+photos|my\s+videos)\b',
            r'\b(convert|shrink|resize|ocr|caption|enlarge)\b',
        ),
        'need_any_file': True,
        'always_first_if_files': True,
        'input_kinds': (),
        'output_kinds': ('paths',),
        'risk': 'read',
    },
    {
        'id': 'video_convert',
        'tool': 'hive.run_job',
        'args': {'job_id': 'video_convert'},
        'label': 'Convert videos so they play everywhere',
        'activity': 'Converting videos',
        'patterns': (
            r'\b(convert|transcode)\b.+\b(video|mp4|mov|mkv|whatsapp)\b',
            r'\b(video|videos|clips?)\b.+\b(whatsapp|play|mp4)\b',
            r'\bmake\s+the\s+videos?\b.+\b(work|play)\b',
        ),
        'path_ctx': 'video_paths',
        'input_kinds': ('video',),
        'output_kinds': ('video',),
        'risk': 'write',
    },
    {
        'id': 'photo_batch',
        'tool': 'hive.run_job',
        'args': {'job_id': 'photo_batch'},
        'label': 'Shrink photos so they send faster',
        'activity': 'Resizing photos',
        'patterns': (
            r'\b(shrink|resize|compress|smaller)\b.+\b(photo|photos|image|images|pics?)\b',
            r'\b(photo|photos|pics?)\b.+\b(shrink|resize|send\s+fast|instagram)\b',
        ),
        'path_ctx': 'photo_paths',
        'input_kinds': ('photo',),
        'output_kinds': ('photo',),
        'risk': 'write',
    },
    {
        'id': 'enlarge_photos',
        'tool': 'hive.run_job',
        'args': {'job_id': 'enlarge_photos'},
        'label': 'Enlarge blurry photos worth saving',
        'activity': 'Enlarging photos',
        'patterns': (
            r'\b(enlarge|upscale|blow\s+up|enhance)\b.+\b(photo|photos|blurry|image)\b',
            r'\bblurry\b.+\b(photo|photos|enlarge|save)\b',
        ),
        'path_ctx': 'photo_paths',
        'input_kinds': ('photo',),
        'output_kinds': ('photo',),
        'risk': 'write',
    },
    {
        'id': 'ocr',
        'tool': 'hive.run_job',
        'args': {'job_id': 'ocr'},
        'label': 'Read text from receipts or scans',
        'activity': 'Scanning photos and reading text',
        'patterns': (
            r'\b(ocr|receipt|receipts|scan|scans|expense|pull\s+the\s+text|read\s+the\s+text)\b',
            r'\b(text\s+off|read)\b.+\b(receipt|photo|scan|pdf)\b',
        ),
        'path_ctx': 'receipt_paths',
        'fallback_path_ctx': 'photo_paths',
        'input_kinds': ('receipt', 'photo', 'document'),
        'output_kinds': ('document', 'text'),
        'risk': 'write',
    },
    {
        'id': 'captions',
        'tool': 'hive.run_job',
        'args': {'job_id': 'captions'},
        'label': 'Caption a video',
        'activity': 'Listening to audio and writing captions',
        'patterns': (
            r'\b(caption|captions|subtitle|subtitles|transcribe|transcript)\b',
        ),
        'path_ctx': 'video_paths',
        'input_kinds': ('video',),
        'output_kinds': ('document', 'text'),
        'risk': 'write',
    },
    {
        'id': 'voiceover',
        'tool': 'hive.run_job',
        'args': {'job_id': 'voiceover'},
        'label': 'Create a voice intro',
        'activity': 'Creating a spoken voiceover',
        'patterns': (
            r'\b(voice\s*over|voiceover|narrat|spoken\s+intro|thank[- ]you\s+voice|audiobook)\b',
            r'\b(read\s+this)\b.+\b(aloud|voice)\b',
        ),
        'input_kinds': ('text',),
        'output_kinds': ('audio',),
        'risk': 'write',
    },
    {
        'id': 'translate_hive',
        'tool': 'hive.run_job',
        'args': {'job_id': 'translate'},
        'label': 'Translate writing',
        'activity': 'Translating text',
        'patterns': (
            r'\b(translate|translation)\b.+\b(file|document|intro|caption|italian|spanish|french|german)\b',
            r'\b(into|to)\s+(italian|spanish|french|german|english)\b',
        ),
        'prefer_when_files': True,
        'input_kinds': ('document', 'text'),
        'output_kinds': ('document', 'text'),
        'risk': 'write',
    },
    {
        'id': 'translate_text',
        'tool': 'text.translate',
        'label': 'Translate short text',
        'activity': 'Translating pasted text',
        'patterns': (
            r'\btranslate\b.+\b(this|text|sentence|message)\b',
        ),
        'input_kinds': ('text',),
        'output_kinds': ('text',),
        'risk': 'read',
    },
    {
        'id': 'website_build',
        'tool': 'website.build',
        'label': 'Build a simple page with the highlights',
        'activity': 'Building a website page',
        'patterns': (
            r'\b(build|create|make)\b.+\b(page|website|site|highlights?\s+page)\b',
            r'\b(simple\s+page|highlights?\s+page)\b',
        ),
        'input_kinds': (),
        'output_kinds': ('website',),
        'risk': 'write',
    },
    {
        'id': 'website_publish',
        'tool': 'websites.publish',
        'label': 'Publish the website',
        'activity': 'Publishing the website',
        'patterns': (
            r'\bpublish\b.+\b(website|site|page|it)\b',
            r'\bpublish\s+it\b',
        ),
        'input_kinds': ('website',),
        'output_kinds': ('website',),
        'risk': 'write',
        'needs_confirm': True,
    },
    {
        'id': 'chat_notify',
        'tool': 'chat.send',
        'label': 'Tell someone or a group it is ready',
        'activity': 'Sending a chat message',
        'patterns': (
            r'\b(tell|notify|message|text)\s+(?:the\s+|my\s+)?[A-Za-z]',
            r'\b(tell\s+the\s+group|message\s+the\s+group)\b',
        ),
        'input_kinds': (),
        'output_kinds': (),
        'risk': 'send',
        'needs_confirm': True,
        'needs_text_approve': True,
        'needs_clarify_keys': ('peer_or_group',),
    },
    {
        'id': 'diary_post',
        'tool': 'diary.post',
        'label': 'Save a diary note',
        'activity': 'Writing a diary note',
        'patterns': (
            r'\b(diary|journal)\b',
            r'\bsave\s+a\s+(diary\s+)?note\b',
        ),
        'input_kinds': (),
        'output_kinds': (),
        'risk': 'write',
        'needs_confirm': True,
        'needs_text_approve': True,
    },
    {
        'id': 'calendar_remind',
        'tool': 'calendar.remind',
        'label': 'Set a reminder',
        'activity': 'Creating a calendar reminder',
        'patterns': (
            r'\b(remind\s+me|reminder)\b',
            r'\bremind\b.+\b(friday|monday|tuesday|wednesday|thursday|saturday|sunday|tomorrow)\b',
        ),
        'input_kinds': (),
        'output_kinds': (),
        'risk': 'write',
    },
    {
        'id': 'commit_outputs',
        'tool': 'files.commit_outputs',
        'label': 'Save the finished files where you want them',
        'activity': 'Saving finished files',
        'patterns': (
            r'\b(save|put|keep|store)\b.+\b(room|folder|desktop|local|results?)\b',
            r'\b(put\s+them\s+in|save\s+(them|it)\s+(to|in))\b',
        ),
        'input_kinds': ('produced',),
        'output_kinds': ('produced',),
        'risk': 'write',
        'append_when_outputs': True,
    },
)

_VIDEO_EXT = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v', '.wmv', '.3gp'}
_PHOTO_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tif', '.tiff', '.heic'}
_DOC_EXT = {'.pdf', '.doc', '.docx', '.txt', '.rtf', '.odt'}
_AUDIO_EXT = {'.mp3', '.wav', '.m4a', '.flac', '.ogg', '.aac'}
_RECEIPT_NAME = re.compile(r'(receipt|invoice|ticket|boarding|expense|bill)', re.I)

_MULTI_JOIN = re.compile(
    r'\b(and then|then|after that|afterwards|once (?:you|that)|and also|also|finally|plus)\b|,|;',
    re.I,
)
_ACTION_HINT = re.compile(
    r'\b(convert|shrink|resize|enlarge|upscale|ocr|caption|subtitle|transcribe|'
    r'translate|voiceover|narrat|publish|build|create|make|tell|notify|message|'
    r'remind|diary|journal|save|put|store|compress|scan|read)\b',
    re.I,
)


def _isfile_quick(path: str, timeout_sec: float = 0.15) -> bool:
    """os.path.isfile can hang on dead UNC — never block ADMIN. Keep timeout tiny."""
    import threading
    box = {'ok': False}

    def _check():
        try:
            box['ok'] = bool(path) and os.path.isfile(path)
        except Exception:
            box['ok'] = False

    thr = threading.Thread(target=_check, name='isfile-quick', daemon=True)
    thr.start()
    thr.join(max(0.05, float(timeout_sec or 0.15)))
    if thr.is_alive():
        return False
    return bool(box['ok'])


def classify_paths(
    paths: Optional[List[str]] = None,
    status_fn: Optional[Callable] = None,
    verify_exists: bool = False,
) -> Dict[str, Any]:
    """
    Sort file paths into video / photo / receipt / document / audio / other.
    Extension-only by default (instant). Existence checks are optional — phone
    dumps with hundreds of paths used to stall Sort for minutes via isfile.
    """
    videos: List[str] = []
    photos: List[str] = []
    receipts: List[str] = []
    documents: List[str] = []
    audio: List[str] = []
    other: List[str] = []
    raw_list = [p for p in (paths or []) if p]
    for raw in raw_list:
        try:
            path = os.path.abspath(str(raw or '').strip())
        except Exception:
            path = str(raw or '').strip()
        if not path:
            continue
        if verify_exists and not _isfile_quick(path, timeout_sec=0.1):
            continue
        ext = os.path.splitext(path)[1].lower()
        base = os.path.basename(path)
        if ext in _VIDEO_EXT:
            videos.append(path)
        elif ext in _AUDIO_EXT:
            audio.append(path)
        elif ext in _DOC_EXT:
            documents.append(path)
        elif ext in _PHOTO_EXT:
            if _RECEIPT_NAME.search(base):
                receipts.append(path)
            else:
                photos.append(path)
        else:
            other.append(path)
    if status_fn:
        try:
            status_fn('Sorted files (100%)')
        except Exception:
            pass
    return {
        'ok': True,
        'videos': videos,
        'photos': photos,
        'receipts': receipts,
        'documents': documents,
        'audio': audio,
        'other': other,
        'video_paths': videos,
        'photo_paths': photos,
        'receipt_paths': receipts or photos,
        'document_paths': documents,
        'paths': videos + photos + receipts + documents + audio + other,
        'produced': videos + photos + receipts + documents + audio + other,
        'counts': {
            'videos': len(videos),
            'photos': len(photos),
            'receipts': len(receipts),
            'documents': len(documents),
            'audio': len(audio),
            'other': len(other),
        },
    }


def _routing_text(goal: str) -> str:
    text = (goal or '').strip()
    if not text:
        return ''
    try:
        from Support.NeuronNeuryAgent import neury_routing_text
        return (neury_routing_text(text) or text).strip()
    except Exception:
        return text


def _extract_room_name(text: str) -> str:
    raw = text or ''
    m = re.search(
        r'\b(?:in|into|to)\s+(?:the\s+)?([A-Za-z0-9][A-Za-z0-9 _-]{0,40}?)\s+room\b',
        raw,
        re.I,
    )
    if m:
        return m.group(1).strip()
    m = re.search(r'\broom\s+(?:called|named)\s+["\']?([^"\']+)["\']?', raw, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'\broom\s+([A-Za-z0-9][A-Za-z0-9_-]{0,40})\b', raw, re.I)
    if m:
        name = m.group(1).strip()
        if name.lower() not in ('called', 'named', 'the', 'a', 'an', 'into', 'to'):
            return name
    # "save/put them in MAIN" (default room everyone has)
    m = re.search(
        r'\b(?:save|put|keep|store)\b.+\b(?:in|into|to)\s+(?:the\s+)?([A-Za-z0-9][A-Za-z0-9_-]{0,40})\b',
        raw,
        re.I,
    )
    if m:
        cand = m.group(1).strip()
        if cand.lower() not in ('the', 'a', 'an', 'my', 'our', 'room'):
            return cand
    return ''


def _extract_peer_or_group(text: str) -> str:
    # Prefer "the group" / "my group" as ambiguous group target
    if re.search(r'\b(?:tell|notify|message|text)\s+(?:the|my)\s+group\b', text or '', re.I):
        return 'group'
    m = re.search(
        r'\b(?:tell|notify|message|text)\s+([A-Za-z][A-Za-z0-9_-]{0,40})\b',
        text or '',
        re.I,
    )
    if not m:
        return ''
    name = m.group(1).strip()
    if name.lower() in (
        'group', 'them', 'him', 'her', 'everyone', 'somebody', 'the', 'my', 'a', 'an',
    ):
        if name.lower() == 'group':
            return 'group'
        return ''
    return name


def _extract_language(text: str) -> str:
    m = re.search(
        r'\b(?:into|to|in)\s+(italian|spanish|french|german|english|portuguese|dutch)\b',
        text or '',
        re.I,
    )
    return m.group(1).title() if m else ''


def _friday_or_weekday_iso(text: str) -> str:
    from datetime import datetime, timedelta
    low = (text or '').lower()
    now = datetime.now()
    names = (
        'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'
    )
    target = None
    for i, name in enumerate(names):
        if name in low:
            target = i
            break
    start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if 'tomorrow' in low:
        start = start + timedelta(days=1)
    elif target is not None:
        delta = (target - start.weekday()) % 7
        if delta == 0:
            delta = 7
        start = start + timedelta(days=delta)
    else:
        start = start + timedelta(days=1)
    return start.strftime('%Y-%m-%dT%H:%M:%S')


def capability_surface() -> List[Dict[str, Any]]:
    """Public inventory for registry / system.capabilities consumers."""
    out = []
    for cap in CAPABILITIES:
        out.append({
            'id': cap['id'],
            'tool': cap['tool'],
            'label': cap['label'],
            'activity': cap['activity'],
            'job_id': (cap.get('args') or {}).get('job_id') or '',
            'input_kinds': list(cap.get('input_kinds') or ()),
            'output_kinds': list(cap.get('output_kinds') or ()),
        })
    return out


def activity_for_step(tool: str, args: Optional[Dict[str, Any]] = None, label: str = '') -> str:
    args = args or {}
    jid = str(args.get('job_id') or '').strip()
    for cap in CAPABILITIES:
        if cap['tool'] == tool:
            if jid and (cap.get('args') or {}).get('job_id') and jid != cap['args']['job_id']:
                continue
            return str(cap.get('activity') or label or tool)
    if label:
        return label
    return 'Working on %s' % tool


def format_task_list(tasks: List[Dict[str, Any]]) -> str:
    lines = ['Here is what I will do:']
    for i, task in enumerate(tasks or [], 1):
        status = str(task.get('status') or 'pending')
        label = str(task.get('label') or task.get('tool') or 'Step')
        if status == 'blocked':
            lines.append('%s. [Needs setup] %s' % (i, label))
            hint = str(task.get('enable_hint') or '').strip()
            if hint:
                lines.append('   -> %s' % hint)
        elif status == 'clarify':
            lines.append('%s. [Need your answer] %s' % (i, label))
        else:
            lines.append('%s. %s' % (i, label))
    return '\n'.join(lines)


def enable_hint_for_capability(cap_id: str, detail: str = '') -> str:
    try:
        from Support.NeuronCapabilityRegistry import gap_reply
        return gap_reply(cap_id, detail)
    except Exception:
        pass
    hints = {
        'video_convert': 'Install FFmpeg from Conductor or Hive tool prep, then ask again.',
        'photo_batch': 'Photo resize uses built-in tools; attach the photos and try again.',
        'enlarge_photos': 'Install Real-ESRGAN or waifu2x from Hive tool prep, then ask again.',
        'ocr': 'Install Tesseract (OCR) from Hive tool prep, then ask again.',
        'captions': 'Install whisper.cpp and a speech model from Hive tool prep, then ask again.',
        'voiceover': 'Install Piper and a voice model from Hive tool prep, then ask again.',
        'translate_hive': 'Install onboard AI (Neury/Ollama) so translation can run, then ask again.',
        'chat_notify': 'Name the peer or group chat to message.',
        'commit_outputs': 'Say whether to save on this PC or in a Neuron room.',
    }
    base = hints.get(cap_id) or 'I cannot do that step yet. Ask what I can do, or open the related screen.'
    if detail:
        return '%s (%s)' % (base, detail)
    return base


def _cap_matches(cap: Dict[str, Any], low: str) -> bool:
    for pat in cap.get('patterns') or ():
        if re.search(pat, low):
            return True
    return False


def _compose_diary_note(goal: str, classified: Optional[Dict[str, Any]] = None) -> str:
    """Short human diary note — never dump the whole multi-job ask."""
    classified = classified or {}
    counts = classified.get('counts') if isinstance(classified.get('counts'), dict) else {}
    bits = []
    nv = int(counts.get('videos') or 0)
    np_ = int(counts.get('photos') or 0)
    nr = int(counts.get('receipts') or 0)
    if nv:
        bits.append('%s video%s' % (nv, 's' if nv != 1 else ''))
    if np_:
        bits.append('%s photo%s' % (np_, 's' if np_ != 1 else ''))
    if nr:
        bits.append('%s receipt%s' % (nr, 's' if nr != 1 else ''))
    low = (goal or '').lower()
    extras = []
    if re.search(r'\b(whatsapp|convert)\b', low):
        extras.append('videos ready to share')
    if re.search(r'\b(shrink|compress|send fast)\b', low):
        extras.append('photos shrunk for sending')
    if re.search(r'\b(caption|subtitle)\b', low):
        extras.append('party video captioned')
    if re.search(r'\b(voice|intro|thank)\b', low):
        extras.append('thank-you voice intro')
    if re.search(r'\b(highlights?|website|page)\b', low):
        extras.append('highlights page')
    body = 'Trip dump from my phone'
    if bits:
        body = 'Trip dump — worked through ' + ', '.join(bits)
    if extras:
        body += '. ' + '; '.join(extras[:4])
    body += '.'
    return body[:500]


def analyze_ask(
    goal: str,
    attachments: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Analyze any multi-step ask into an ordered task list.
    Returns tasks, clarify question (if any), and a plain-language plan.
    """
    text = (goal or '').strip()
    low = _routing_text(text).lower()
    # Trust attached paths — never stall analyze on isfile (phone dumps / UNC).
    files = []
    for p in (attachments or []):
        if not p:
            continue
        try:
            files.append(os.path.abspath(str(p)))
        except Exception:
            files.append(str(p))
    classified = classify_paths(files) if files else classify_paths([])

    matched: List[Dict[str, Any]] = []
    seen = set()
    for cap in CAPABILITIES:
        if cap['id'] in seen:
            continue
        if cap.get('always_first_if_files') and files and _cap_matches(cap, low):
            matched.append(cap)
            seen.add(cap['id'])
            continue
        if cap.get('append_when_outputs'):
            continue
        if _cap_matches(cap, low):
            # Prefer hive translate when files exist; text.translate for short paste.
            if cap['id'] == 'translate_text' and files:
                continue
            if cap['id'] == 'translate_hive' and not files and 'file' not in low and 'document' not in low:
                if not re.search(r'\b(intro|caption|transcript)\b', low):
                    # Still allow when translating "intro" produced later.
                    pass
            matched.append(cap)
            seen.add(cap['id'])

    # If user asked for file work but did not mention sorting, still classify when files present.
    if files and 'classify_inputs' not in seen:
        has_media_job = any(
            c['id'] in (
                'video_convert', 'photo_batch', 'enlarge_photos', 'ocr', 'captions',
            )
            for c in matched
        )
        if has_media_job:
            matched.insert(0, CAPABILITIES[0])
            seen.add('classify_inputs')

    # Auto-append save step when media/file outputs are expected and user did not ask,
    # OR when they did ask (already matched). If outputs expected, ensure commit exists.
    expects_files = any(
        c['id'] in (
            'video_convert', 'photo_batch', 'enlarge_photos', 'ocr', 'captions',
            'voiceover', 'translate_hive',
        )
        for c in matched
    )
    if expects_files and 'commit_outputs' not in seen:
        matched.append([c for c in CAPABILITIES if c['id'] == 'commit_outputs'][0])
        seen.add('commit_outputs')

    room_name = _extract_room_name(text)
    peer_or_group = _extract_peer_or_group(text)
    language = _extract_language(text)

    tasks: List[Dict[str, Any]] = []
    clarify_bits: List[str] = []

    for cap in matched:
        args = dict(cap.get('args') or {})
        tool = cap['tool']
        label = cap['label']
        status = 'pending'
        enable_hint = ''

        if tool == 'files.classify_batch':
            args = {'paths': files}
        elif tool == 'hive.run_job':
            jid = args.get('job_id')
            path_key = cap.get('path_ctx') or ''
            paths = []
            if path_key and classified.get(path_key):
                paths = list(classified.get(path_key) or [])
            elif cap.get('fallback_path_ctx') and classified.get(cap['fallback_path_ctx']):
                paths = list(classified.get(cap['fallback_path_ctx']) or [])
            elif jid == 'voiceover':
                # Instruction-only TTS — never attach trip videos/photos (that made
                # Tower download beach_clip.mp4 and hard-crash mid-transfer).
                paths = [
                    p for p in (files or [])
                    if str(p).lower().endswith(('.txt', '.md', '.docx', '.pdf', '.epub'))
                ]
            elif jid == 'translate':
                paths = list(
                    classified.get('documents')
                    or classified.get('receipts')
                    or []
                )
            elif files and path_key:
                # Paths filled later from classify step via $ctx
                args['paths'] = '$%s' % path_key
            if paths:
                args['paths'] = paths
            elif files and path_key and 'classify_inputs' in seen:
                # Only wire $ctx paths when the user actually attached files.
                args['paths'] = '$%s' % path_key
            args['instruction'] = text[:500]
            if jid == 'translate' and language:
                args['instruction'] = 'Translate to %s. %s' % (language, text[:400])
            if jid == 'voiceover':
                # Never TTS the full multi-job ask — that read the whole prompt aloud.
                args['instruction'] = (
                    'Speak a short warm thank-you voice intro for friends who helped '
                    'with this trip. Keep it under 25 seconds. Do not read task lists, '
                    'file names, or instructions — only a friendly spoken thank-you.'
                )
            # Missing files for jobs that need them
            needs_files = jid not in ('voiceover', 'pictures')
            if needs_files and not files and not str(args.get('paths') or '').startswith('$'):
                status = 'blocked'
                enable_hint = 'Attach the files or drop the folder, then ask again.'
        elif tool == 'text.translate':
            args['language'] = language or 'English'
            args['text'] = text
        elif tool == 'website.build':
            args['goal'] = text
            args['prompt'] = text
            # Trip media gallery — photos + converted videos, not icon placeholders.
            media = []
            for key in ('photos', 'videos', 'photo_paths', 'video_paths'):
                for p in list(classified.get(key) or []):
                    if p and p not in media:
                        media.append(p)
            if media:
                args['attachments'] = media[:24]
                args['embed_media'] = True
        elif tool == 'websites.publish':
            args['name'] = '$website_name'
        elif tool == 'chat.send':
            target = peer_or_group
            if not target:
                status = 'clarify'
                clarify_bits.append(
                    'Who should I message — a peer name or a group name?'
                )
            elif target.lower() == 'group':
                status = 'clarify'
                clarify_bits.append('Which group should I message?')
            else:
                # Prefer group if user said group-ish; else peer
                if 'group' in low:
                    args['group'] = target
                else:
                    args['peer'] = target
            args['message'] = (
                'I finished the work you asked for. The results are ready.'
            )
        elif tool == 'diary.post':
            # Never paste the full multi-job ask into the diary.
            args['message'] = _compose_diary_note(text, classified)
        elif tool == 'calendar.remind':
            args['title'] = 'Reminder from Neury'
            if re.search(r'\bpost\b.+\b(photo|best)\b', low):
                args['title'] = 'Post the best photo'
            args['start_iso'] = _friday_or_weekday_iso(text)
            args['minutes_before'] = 30
        elif tool == 'files.commit_outputs':
            args['paths'] = '$produced'
            if room_name:
                args['destination'] = 'room'
                args['room'] = room_name
            else:
                # Will clarify at runtime if still unknown
                args['destination'] = ''
                if not room_name and 'room' not in low and 'local' not in low and 'desktop' not in low:
                    # Soft clarify only when outputs exist — ask once at commit time
                    args['ask_destination'] = True

        # Tool readiness for hive jobs
        if status == 'pending' and tool == 'hive.run_job':
            jid = str(args.get('job_id') or '')
            hint = _hive_tool_gap(jid)
            if hint:
                status = 'blocked'
                enable_hint = hint

        step = {
            'id': 't%s' % (len(tasks) + 1),
            'tool': tool,
            'label': label,
            'args': args,
            'risk': cap.get('risk') or 'write',
            'status': status,
            'enable_hint': enable_hint,
            'activity': cap.get('activity') or label,
            'cap_id': cap['id'],
            'needs_confirm': bool(cap.get('needs_confirm')),
        }
        tasks.append(step)
        if status == 'clarify' and cap.get('needs_confirm'):
            pass
        if status == 'pending' and cap.get('needs_confirm'):
            # Insert confirm before send/publish (editable draft when posting text).
            draft = ''
            draft_field = 'message'
            if cap.get('needs_text_approve') or tool in (
                'diary.post', 'chat.send', 'mail.send', 'article.publish',
            ):
                draft = str(
                    args.get('message') or args.get('text') or args.get('body') or ''
                ).strip()
                if args.get('message') is not None:
                    draft_field = 'message'
                elif args.get('text') is not None:
                    draft_field = 'text'
                elif args.get('body') is not None:
                    draft_field = 'body'
            preview_kind = ''
            if tool == 'websites.publish':
                confirm_msg = (
                    'Preview the website first, then Yes to publish it publicly '
                    '(or No to keep the draft only).'
                )
                preview_kind = 'website'
            elif tool == 'article.publish':
                confirm_msg = (
                    'Review the article draft below, then Yes to publish '
                    '(or No to keep it in My Articles).'
                )
                if not draft:
                    draft = str(args.get('title') or label or '').strip()
            elif draft:
                confirm_msg = 'Review and edit the text below, then Yes to post.'
            else:
                confirm_msg = 'Say OK to continue with: %s' % label
            conf_args = {
                'message': confirm_msg,
                'draft_text': draft,
                'draft_field': draft_field,
                'apply_to_step': step['id'],
            }
            if preview_kind:
                conf_args['preview_kind'] = preview_kind
                # Hint for Conductor Preview / Builder (resolved at confirm time too).
                conf_args['website_name'] = '$website_name'
            tasks.insert(
                len(tasks) - 1,
                {
                    'id': 'c%s' % len(tasks),
                    'tool': 'user.confirm',
                    'label': 'Confirm before: %s' % label,
                    'args': conf_args,
                    'risk': 'read',
                    'status': 'pending',
                    'activity': 'Waiting for your OK',
                    'cap_id': 'confirm',
                },
            )

    clarify = ''
    if not files and any(
        t.get('cap_id') in (
            'video_convert', 'photo_batch', 'enlarge_photos', 'ocr', 'captions',
        )
        for t in tasks
    ):
        clarify = 'Please attach the folder or files you want me to work on.'
    elif clarify_bits:
        clarify = clarify_bits[0]
    elif not tasks:
        # Not a multi-capability ask — leave empty for other routers
        return {
            'ok': True,
            'tasks': [],
            'clarify': '',
            'plan_text': '',
            'matched': False,
            'classified': classified,
        }

    # If first actionable tasks are all blocked and nothing runnable, still return list
    plan_text = format_task_list(tasks)
    return {
        'ok': True,
        'tasks': tasks,
        'clarify': clarify,
        'plan_text': plan_text,
        'matched': True,
        'classified': classified,
        'room_name': room_name,
        'peer_or_group': peer_or_group,
    }


def _hive_tool_gap(job_id: str) -> str:
    jid = (job_id or '').strip()
    if not jid:
        return ''
    try:
        import Support.ConductorPower as cpower
        if jid not in cpower.HIVE_READY_IDS:
            return 'That job is not available yet.'
        if jid in getattr(cpower, 'HIVE_NEURY_IDS', ()) and not cpower.neury_installed():
            return enable_hint_for_capability('translate_hive', 'onboard AI missing')
        from Support.HiveToolPrep import missing_tools
        invite = cpower.invitation_for_job(jid)
        missing = missing_tools(invite) or []
        if missing:
            labels = [s.get('label') or n for n, s in missing]
            return enable_hint_for_capability(jid, 'need %s' % ', '.join(labels))
    except Exception:
        return ''
    return ''


def compose_project_steps(
    goal: str,
    attachments: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build Orchestrator-compatible steps for any matched multi-ask.
    Returns None when the ask should use other planners.
    """
    analysis = analyze_ask(goal, attachments)
    if not analysis.get('matched') or not analysis.get('tasks'):
        return None

    if analysis.get('clarify') and not any(
        t.get('status') == 'pending' for t in analysis['tasks']
    ):
        return {
            'template_id': 'multi_ask',
            'title': (goal or 'Multi-step ask')[:80],
            'steps': [{
                'id': 'clarify',
                'tool': 'user.confirm',
                'label': 'Clarification needed',
                'args': {'message': analysis['clarify']},
                'risk': 'read',
                'status': 'pending',
            }],
            'plan_text': analysis.get('plan_text') or '',
            'analysis': analysis,
        }

    steps = []
    # Seed classify result into context via first step when present
    for task in analysis['tasks']:
        if task.get('status') == 'blocked':
            # Keep blocked steps visible but use user.confirm to surface the enable hint
            steps.append({
                'id': task.get('id') or 'blocked',
                'tool': 'user.confirm',
                'label': task.get('label') or 'Blocked step',
                'args': {
                    'message': (
                        'I cannot run "%s" yet. %s '
                        'Say OK to skip this step and continue with the rest.'
                    ) % (
                        task.get('label') or 'this step',
                        task.get('enable_hint') or enable_hint_for_capability(
                            str(task.get('cap_id') or '')
                        ),
                    ),
                },
                'risk': 'read',
                'status': 'pending',
                'activity': task.get('activity') or '',
                'cap_id': task.get('cap_id') or '',
                'blocked_original': task,
            })
            continue
        if task.get('status') == 'clarify':
            steps.append({
                'id': task.get('id') or 'clarify',
                'tool': 'user.confirm',
                'label': 'Need a quick answer',
                'args': {'message': analysis.get('clarify') or 'Please clarify.'},
                'risk': 'read',
                'status': 'pending',
            })
            continue
        steps.append({
            'id': task.get('id') or 'step',
            'tool': task['tool'],
            'label': task.get('label') or task['tool'],
            'args': task.get('args') or {},
            'risk': task.get('risk') or 'write',
            'status': 'pending',
            'activity': task.get('activity') or '',
            'cap_id': task.get('cap_id') or '',
        })

    title = (goal or 'Multi-step ask').strip()
    if len(title) > 80:
        title = title[:77] + '...'
    try:
        from Support.NeuronNeuryAskParallel import annotate_step_dependencies
        annotate_step_dependencies(steps)
    except Exception:
        pass
    return {
        'template_id': 'multi_ask',
        'title': title,
        'steps': steps,
        'plan_text': analysis.get('plan_text') or format_task_list(analysis['tasks']),
        'analysis': analysis,
    }


def unique_name(directory: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = filename
    n = 2
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = '%s_%s%s' % (base, n, ext)
        n += 1
        if n > 500:
            candidate = '%s_%s%s' % (base, int(time.time()), ext)
            break
    return candidate


def commit_outputs(
    paths: Optional[List[str]] = None,
    destination: str = '',
    room: str = '',
    local_dir: str = '',
    conflict: str = '',
    apply_to_all: bool = False,
    ask_fn: Optional[Callable] = None,
    status_fn: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Save created/modified files to local storage or a Neuron room.
    conflict: 'replace' | 'rename' | '' (ask once, Apply to all).
    """
    files = [os.path.abspath(p) for p in (paths or []) if p and os.path.isfile(p)]
    if not files:
        return {'ok': False, 'error': 'no_files', 'message': 'No finished files to save yet.'}

    dest = (destination or '').strip().lower()
    room_name = (room or '').strip()
    conflict_policy = (conflict or '').strip().lower()
    apply_all = bool(apply_to_all)

    if dest not in ('local', 'room'):
        if room_name:
            dest = 'room'
        elif ask_fn:
            # Plain-language choice
            choice = ask_fn(
                'files.commit_outputs',
                'Where should I save the finished files?\n'
                '1) This computer (local folder)\n'
                '2) A Neuron room\n'
                'Reply with 1 or 2.',
                {'paths': files},
            )
            raw = str(choice or '').strip().lower()
            if raw.startswith('2') or 'room' in raw:
                dest = 'room'
            else:
                dest = 'local'
        else:
            dest = 'local'

    if dest == 'room' and not room_name and ask_fn:
        room_name = str(ask_fn(
            'files.commit_outputs.room',
            'Which Neuron room should I save them into? Reply with the room name.',
            {},
        ) or '').strip()
        if not room_name:
            return {
                'ok': False,
                'error': 'need_room',
                'message': 'Tell me the room name, or choose local storage.',
            }

    saved: List[str] = []
    replaced: List[str] = []
    renamed: List[str] = []
    skipped: List[str] = []

    if dest == 'local':
        out_dir = (local_dir or '').strip()
        if not out_dir:
            try:
                import Support.Header as init
                base = getattr(init, 'PATH_NEURONFILES', '') or ''
                out_dir = os.path.join(base, 'ShareReady_%s' % time.strftime('%Y%m%d_%H%M%S'))
            except Exception:
                out_dir = os.path.join(os.path.expanduser('~'), 'NeuronShareReady')
        os.makedirs(out_dir, exist_ok=True)
        for src in files:
            if status_fn:
                try:
                    status_fn('Saving %s' % os.path.basename(src))
                except Exception:
                    pass
            name = os.path.basename(src)
            target = os.path.join(out_dir, name)
            if os.path.exists(target):
                policy = conflict_policy
                if not policy and ask_fn and not apply_all:
                    ans = str(ask_fn(
                        'files.commit_outputs.conflict',
                        'A file named %s already exists.\n'
                        '1) Replace it\n'
                        '2) Save with a different name\n'
                        'Reply 1 or 2. (I can apply this to all remaining files.)'
                        % name,
                        {'path': src},
                    ) or '').strip().lower()
                    if ans.startswith('1') or 'replace' in ans:
                        policy = 'replace'
                    else:
                        policy = 'rename'
                    if 'all' in ans:
                        apply_all = True
                    conflict_policy = policy
                    apply_to_all = apply_all
                if not policy:
                    policy = 'rename'
                if policy == 'replace':
                    shutil.copy2(src, target)
                    replaced.append(target)
                else:
                    name2 = unique_name(out_dir, name)
                    target = os.path.join(out_dir, name2)
                    shutil.copy2(src, target)
                    renamed.append(target)
            else:
                shutil.copy2(src, target)
            saved.append(target)
        return {
            'ok': True,
            'destination': 'local',
            'local_dir': out_dir,
            'saved': saved,
            'replaced': replaced,
            'renamed': renamed,
            'produced': saved,
            'files': saved,
            'path': saved[0] if saved else out_dir,
            'message': 'Saved %s file(s) to %s' % (len(saved), out_dir),
        }

    # Room destination
    try:
        import Support.Header as init
        from Support.Dictionaries import filter_dictionary
        from Support.Files import import_files, overwrite_room_file
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}

    def _rooms_list():
        raw = getattr(init, 'ROOMS', None) or []
        # ROOMS is often a dict keyed by room_id — never list(dict) (that yields ids).
        if isinstance(raw, dict):
            return [r for r in raw.values() if isinstance(r, dict)]
        return [r for r in list(raw) if isinstance(r, dict)]

    def _match_room(want):
        want = str(want or '').strip()
        if not want:
            return []
        all_rooms = _rooms_list()
        hit = filter_dictionary(all_rooms, 'name', want, '', '', '', '')
        if hit:
            return hit
        low = want.lower()
        hit = [
            r for r in all_rooms
            if str(r.get('name') or '').lower() == low
            or str(r.get('room_id') or r.get('id') or '').lower() == low
        ]
        return hit

    rooms = _match_room(room_name)
    if not rooms:
        # Reload Rooms once — Conductor jobs can race before dictionaries settle.
        try:
            from Support.Dictionaries import load_dictionaries
            from Support.Header import DICTIONARY_ROOMS
            load_dictionaries(DICTIONARY_ROOMS)
            rooms = _match_room(room_name)
        except Exception:
            pass
    if not rooms and str(room_name or '').upper() == 'MAIN':
        main_path = os.path.join(getattr(init, 'PATH_NEURON_ROOMS', '') or '', 'MAIN')
        if os.path.isdir(main_path):
            try:
                from Support.Utils import query_database
                rows = query_database(
                    getattr(init, 'pool_main_db', None),
                    "SELECT room_id, name, type, allowed_peers FROM Rooms WHERE UPPER(name)='MAIN' OR UPPER(room_id)='MAIN'",
                    (),
                    commit=False,
                ) or []
                if rows:
                    rooms = [{
                        'room_id': rows[0][0],
                        'name': rows[0][1] or 'MAIN',
                        'type': rows[0][2] or '',
                        'allowed_peers': rows[0][3] or '',
                    }]
            except Exception:
                rooms = []
            if not rooms:
                # Folder exists — synthesize a usable MAIN record.
                rooms = [{
                    'room_id': 'MAIN',
                    'name': 'MAIN',
                    'type': 'public',
                    'allowed_peers': '',
                }]
    if not rooms:
        return {
            'ok': False,
            'error': 'room_not_found',
            'message': 'I could not find a room named "%s".' % room_name,
            'enable_hint': 'Open Rooms and check the name, then ask me to save there again.',
        }
    room_rec = rooms[0]
    room_id = room_rec.get('room_id') or room_rec.get('id')
    allowed = room_rec.get('allowed_peers') or ''
    rtype = room_rec.get('type') or ''
    app = getattr(init, 'mainApplication', None)
    if app is None:
        return {
            'ok': False,
            'error': 'ui_required',
            'message': 'Open Neuron on the desktop to save files into a room.',
        }

    for src in files:
        if status_fn:
            try:
                status_fn('Saving %s into room %s' % (os.path.basename(src), room_name))
            except Exception:
                pass
        name = os.path.basename(src)
        existing = filter_dictionary(
            getattr(init, 'DESKTOPICONS', {}) or {},
            'room_id', room_id, 'label', name, '', '',
        )
        if existing:
            policy = conflict_policy
            if not policy and ask_fn and not apply_all:
                ans = str(ask_fn(
                    'files.commit_outputs.conflict',
                    'The file %s already exists in room %s.\n'
                    '1) Replace it\n'
                    '2) Save with a different name\n'
                    'Reply 1 or 2 (add "all" to apply to the rest).'
                    % (name.upper(), room_name.upper()),
                    {'path': src, 'room': room_name},
                ) or '').strip().lower()
                if ans.startswith('1') or 'replace' in ans:
                    policy = 'replace'
                else:
                    policy = 'rename'
                if 'all' in ans:
                    apply_all = True
                conflict_policy = policy
            if not policy:
                policy = 'rename'
            if policy == 'replace':
                try:
                    overwrite_room_file(existing[0].get('icon_id'), room_id, src)
                    replaced.append(name)
                    saved.append(src)
                except Exception as exc:
                    skipped.append('%s (%s)' % (name, exc))
                continue
            # rename: copy to temp unique name then import
            try:
                import Support.Header as init2
                tmp_dir = getattr(init2, 'PATH_NEURONFILES', '') or os.path.dirname(src)
                os.makedirs(tmp_dir, exist_ok=True)
                new_name = unique_name(tmp_dir, name)
                tmp_path = os.path.join(tmp_dir, new_name)
                shutil.copy2(src, tmp_path)
                ok = import_files(app, [tmp_path], 120, 120, room_id, allowed, rtype)
                if ok is False:
                    skipped.append(new_name)
                else:
                    renamed.append(new_name)
                    saved.append(tmp_path)
            except Exception as exc:
                skipped.append('%s (%s)' % (name, exc))
            continue
        ok = import_files(app, [src], 120, 120, room_id, allowed, rtype)
        if ok is False:
            skipped.append(name)
        else:
            saved.append(src)

    return {
        'ok': True if saved else False,
        'destination': 'room',
        'room': room_name,
        'room_id': room_id,
        'saved': saved,
        'replaced': replaced,
        'renamed': renamed,
        'skipped': skipped,
        'produced': saved,
        'files': saved,
        'message': (
            'Saved %s file(s) to room %s'
            % (len(saved), room_name)
            + (('; skipped %s' % len(skipped)) if skipped else '')
        ),
        'error': '' if saved else 'Could not save files into that room.',
    }


def seed_attachments_fast(project: Optional[Dict[str, Any]], paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Seed project context from attachments without os.path.isfile (instant).
    Prefer this over NeuronOrchestrator.seed_project_attachments for phone dumps.
    """
    project = project if isinstance(project, dict) else {}
    ctx = project.setdefault('context', {})
    files = []
    for p in (paths or []):
        if not p:
            continue
        try:
            files.append(os.path.abspath(str(p)))
        except Exception:
            files.append(str(p))
    if not files:
        return project
    ctx['attachments'] = files
    ctx['path'] = files[0]
    classified = classify_paths(files, verify_exists=False)
    for key in (
        'videos', 'photos', 'receipts', 'documents', 'audio', 'other',
        'video_paths', 'photo_paths', 'receipt_paths', 'document_paths', 'paths',
    ):
        if classified.get(key) is not None:
            ctx[key] = classified.get(key)
    ctx['produced'] = list(classified.get('paths') or files)
    images = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'}
    tables = {'.xlsx', '.xls', '.csv'}
    for path in files:
        ext = os.path.splitext(path)[1].lower()
        if ext in images and not ctx.get('heading_image'):
            ctx['heading_image'] = path
        if ext in tables and not ctx.get('table_path'):
            ctx['table_path'] = path
    return project


def files_classify_batch(args: Optional[Dict[str, Any]] = None, status_fn=None) -> Dict[str, Any]:
    args = args or {}
    paths = args.get('paths') or args.get('files') or []
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        try:
            from Support import NeuronNeuryAgentHelpers as helpers
            paths = list(getattr(helpers, '_turn_attachments', None) or [])
        except Exception:
            paths = []
    if not paths:
        # Already classified at project seed — reuse context buckets.
        try:
            from Support.NeuronOrchestrator import _load_project
            ctx = ((_load_project('') or {}).get('context') or {})
            for key in ('attachments', 'paths', 'produced', 'video_paths', 'photo_paths'):
                val = ctx.get(key)
                if isinstance(val, list) and val:
                    paths = list(val)
                    break
        except Exception:
            paths = []
    # Instant — extension sort only (no per-file isfile thrash).
    result = classify_paths(paths, status_fn=None, verify_exists=False)
    result['message'] = (
        'Sorted files: %s videos, %s photos, %s receipts, %s documents.'
        % (
            result['counts']['videos'],
            result['counts']['photos'],
            result['counts']['receipts'],
            result['counts']['documents'],
        )
    )
    if status_fn:
        try:
            status_fn('Finished: Sorted files (100%)')
        except Exception:
            pass
    return result


def files_commit_outputs(args: Optional[Dict[str, Any]] = None, ask_fn=None, status_fn=None) -> Dict[str, Any]:
    args = args or {}
    paths = args.get('paths') or args.get('files') or args.get('produced') or []
    if isinstance(paths, str):
        paths = [paths]
    return commit_outputs(
        paths=paths,
        destination=str(args.get('destination') or ''),
        room=str(args.get('room') or args.get('room_name') or ''),
        local_dir=str(args.get('local_dir') or args.get('folder') or ''),
        conflict=str(args.get('conflict') or args.get('on_conflict') or ''),
        apply_to_all=bool(args.get('apply_to_all')),
        ask_fn=ask_fn,
        status_fn=status_fn,
    )


def peer_help_allowed_for_requester() -> bool:
    """True when this node may ask friends' Neurons for heavy batch work."""
    try:
        from Support.NeuronNeuryAgentPolicy import load_assistant_policy, PEER_DENY
        pol = load_assistant_policy()
        # Requester uses friends when inbound is not deny-all culture;
        # also require at least one online peer.
        if str(pol.get('inbound_peer_jobs') or '').lower() == PEER_DENY:
            # Still allow requesting help; deny only affects accepting.
            pass
    except Exception:
        pass
    try:
        import Support.Header as init
        peers = getattr(init, 'PEERS', None) or {}
        if isinstance(peers, dict):
            return any(bool(p.get('online')) for p in peers.values() if isinstance(p, dict))
        if isinstance(peers, list):
            return any(bool(p.get('online')) for p in peers if isinstance(p, dict))
    except Exception:
        pass
    return False


def progress_status(status_fn, idx: int, total: int, activity: str, pct: Optional[int] = None) -> None:
    if not status_fn:
        return
    try:
        if pct is None:
            pct = int(round(100.0 * max(0, idx - 1) / max(1, total)))
        msg = 'Step %s/%s (%s%%): %s' % (idx, total, max(0, min(100, pct)), activity)
        status_fn(msg)
    except Exception:
        pass


__all__ = [
    'CAPABILITIES',
    'capability_surface',
    'classify_paths',
    'analyze_ask',
    'compose_project_steps',
    'format_task_list',
    'activity_for_step',
    'enable_hint_for_capability',
    'commit_outputs',
    'files_classify_batch',
    'files_commit_outputs',
    'seed_attachments_fast',
    'peer_help_allowed_for_requester',
    'progress_status',
    'unique_name',
]
