"""
Conductor — post-splash front door. Jobs stay here until Open desktop.

Revert without code rollback:
  Set System/json/conductor.json  "enabled": false
  Restart Neuron — prior hub/journey startup resumes.

Backups (pre-Conductor):
  F:\\OLD\\FirstRunExperience_pre_conductor_*.py
  F:\\OLD\\Conductor_before_stay_inside.py
"""

import html as html_lib
import json
import math
import os
import random
import re
import shutil
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import requests

from PySide6.QtCore import Qt, QTimer, QPointF, Signal, QUrl, QSize
from PySide6.QtGui import (
    QColor, QPainter, QPen, QPixmap, QLinearGradient, QRadialGradient, QCursor,
    QTextCursor, QDesktopServices, QIcon,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFrame,
    QApplication, QFileDialog, QTextEdit, QSizePolicy, QGraphicsOpacityEffect,
    QScrollArea, QProgressBar,
)

import Support.Header as init
from Support.Header import PATH_JSON
import Support.ConductorPower as cpower
from Support.Languages import ensure_catalog_entry, resolve_text, save_catalog

_CONFIG_PATH = os.path.join(PATH_JSON, 'conductor.json')
_BG_NAME = 'ConductorBackground.png'
_conductor = None
_PROJECT_STATUS_LOCK = threading.Lock()

CONDUCTOR_FORM = 'Conductor'
_strings_seeded = False

# Front-door chrome / stages / dialogs (English source for catalog + packs).
_CONDUCTOR_STATIC_STRINGS = (
    'NEURON', 'A New Kind of Internet', 'Welcome back', 'Welcome back, %s',
    'What do you want Neuron to do?', 'Tell Neuron what you want to do.',
    'Ask, search, create, or give Neuron a task…', 'Add files', 'How it works →',
    'No files yet', 'No files yet — use + Add file', 'Save results…', 'Try another',
    'Go', 'Open', 'Open desktop', 'Type here…', 'Still on the front door.',
    'Completed', 'Stopped', 'More information needed', 'Waiting for your input',
    'Something is missing.', 'Fill in the details, then Go — or choose Abort.',
    'Missing information', 'Continue', 'Abort', 'Aborted', 'Stop',
    'Stop and return home',
    'Stopped on a failed step — tap Try another to go home.',
    'Progress %s/%s · %s%%',
    'Friends helping…',
    'Still working… %s:%02d',
    'Long job — leave Neuron open. Tap Stop to cancel.',
    'Friends helping…',
    'Mesh · %s jobs · %s Neurys',
    '%s steps running here',
    '%s Neurys',
    'This PC',
    'Planning your project…',
    'Preparing steps…',
    'Working with the mesh…',
    'Working on this machine…',
    'Cancelled — nothing was changed.', 'Aborted by you.',
    'Add at least one file with + Add file, then Go.',
    'Add at least one file with + Add file, then press Go.\nOr Abort to cancel.',
    'Add files…', 'Describe what you want…',
    'Describe what to translate (and into which language).',
    'Describe the picture you want.', 'Describe the question or paste SQL.',
    'Paste or describe the text to speak (or add a text file).',
    'Describe what you want, then Go — or Abort.',
    'Checking required apps…', 'Required apps are not installed yet.',
    'Install declined or failed.', 'Starting…', 'Working…',
    'Missing app — offering install…', 'Still missing a required app.',
    'App ready — continuing…', 'Could not finish this job.',
    'Nothing to save yet.', 'Could not copy results.', 'Completed.',
    '← Back', 'Work on files', 'Pick a tool. Examples appear after you select one.',
    'Connect', 'Invite, chat, diary, calendar — without opening the desktop.',
    'Discover', 'Create & publish',     'Press Go when ready.',
    'Publish it?', 'Save and register DNS?', 'Allow this step?',
    'Add files, describe the result, then Go.',
    'Try saying:', 'Examples:',
    'Press Go, then pick a file.',
    'Tell Neuron what you want, then Go.',
    'Press Go to see file tools (OCR, convert, photos…).',
    'Press Go for invite, chat, diary, calendar.',
    'Press Go to search, crawl, browse, retrieve.',
    'Press Go to translate, image, publish, or go live.',
    'Press Go — install stays on this screen with live progress.',
    'Press Go to return.',
    'Press Go to see who is on your mesh.',
    'Press Go for a live mesh snapshot.',
    'Press Go to copy an invite link.',
    'Press Go for a short tour.',
    'Continue to type the missing details, or Abort this job.',
    'Reply to Neury…', 'Ask again', 'Open desktop',
    'Type a reply and press Enter, or Open desktop for a longer Neury chat.',
    'Keep a file safe', 'Work with files', 'Install Neury', 'Ask Neury',
    '%.0f Neurons on this node', '%.1f Neurons on this node',
    'Just now: %s', 'Stored peer data %s times', 'Relayed for peers %s times',
    '%s files still syncing across the mesh', '%s rooms on this node',
    '%s unread chat message%s', '%s files fully protected',
    'Invite a friend — grow the mesh beyond this node',
    'Mesh is live — tell Neuron what you want to do',
    '%s peers online', '%s chunks you hold', '%s files protected',
    'Not ready', 'Desktop is not ready yet. Try again in a moment.',
    'Choose a file…', 'No room ready',
    'Open desktop once to finish setup, then try again.',
    'Could not neuronize', 'Spreading across the mesh…', 'Prepared · %s',
    'Prepared · %s — waiting for the first peer holder…',
    'Still working… (%ss)', 'Elapsed %s:%02d', 'Elapsed 0:00',
    'Your answer', 'Type Yes, No, or your answer…', 'Send',
    'Press Yes / No, or type an answer and press Enter.',
    'You: %s', 'Could not show answer: %s',
    'Choose mailbox', 'Pick which account should send this.',
    'Email', 'Nothing left to send. Try again.',
    'Sending email', 'Using the mailbox you chose…',
    'Neury is ready', 'Ask anything from the home screen.',
    'After install I can answer: %s', 'Progress: %s%%',
    'Install Neury', 'Install could not finish:\n%s',
    'Who is here?', 'Looking at rooms on your node…',
    'Your mesh', 'Reading live status…', 'Listening…',
    'Invite a friend', 'Preparing your invite link…', 'Growing the mesh',
    'How it works', 'The simple version',
    '%s file(s): %s%s',
    # Rotating home suggestions (also come from Hive examples)
    'Send an email', 'Send a chat message', 'Keep this file safe',
    'What is on my calendar?', 'Who is on my mesh?', 'Ask Neury anything',
    'Create a picture from a description', 'Translate this into Italian',
    'Send an email to ', 'Translate into Italian: ', 'Tell ',
    'Create a voiceover from a text', 'Caption this interview',
    'Convert a video to MP4', 'Render a Blender animation',
    'Make these scans searchable', 'Split a song into stems',
    'Resize photos for the web', 'Enlarge and restore a photo',
    'Convert this document', 'Query this spreadsheet', 'Process map layers',
    'Create a picture', 'Translate writing',
    'Search, crawl, marketplace, pedia, retrieve.',
    'Translate, generate images, publish articles and listings.',
    'Translate, generate images, publish articles, listings, or start a channel.',
    'Start a channel', 'Go live (podcast)', 'Open Channel Studio',
    'Create a channel, go live, or open Channel Studio.',
)


def _t(default_en):
    """Translate Conductor UI copy via catalog/lang-pack (English source text)."""
    en = default_en if default_en is not None else ''
    if not str(en).strip():
        return en
    ensure_catalog_entry(en, form=CONDUCTOR_FORM, ctx='Conductor.ui', persist=False)
    # Never machine-translate on the UI thread (Google sync freezes Conductor).
    return resolve_text(en, form=CONDUCTOR_FORM, record_miss=True, translate_miss=False)


def _tf(template_en, *args):
    """Translate a printf-style template, falling back if placeholders are lost."""
    translated = _t(template_en)
    if translated.count('%') != template_en.count('%'):
        translated = template_en
    try:
        return translated % args
    except Exception:
        try:
            return template_en % args
        except Exception:
            return template_en


def _seed_conductor_strings():
    """Register Conductor / action / hive copy into catalog.json once per process."""
    global _strings_seeded
    if _strings_seeded:
        return
    for pair in ConductorOverlay.CHIPS_BASIC + ConductorOverlay.CHIPS_FULL:
        ensure_catalog_entry(pair[1], form=CONDUCTOR_FORM, ctx='chip', persist=False)
    for a in cpower.NEURON_ACTIONS:
        aid = str(a.get('id') or '')
        for k in ('title', 'blurb', 'input_placeholder'):
            v = str(a.get(k) or '').strip()
            if v:
                ensure_catalog_entry(
                    v, form=CONDUCTOR_FORM, ctx='action_%s_%s' % (aid, k), persist=False
                )
        for ex in a.get('examples') or []:
            ex = str(ex or '').strip()
            if ex:
                ensure_catalog_entry(
                    ex, form=CONDUCTOR_FORM, ctx='action_%s_ex' % aid, persist=False
                )
    try:
        for j in cpower.list_hive_jobs(include_neury_gated=True):
            jid = str(j.get('id') or '')
            for k in ('title', 'blurb', 'placeholder', 'file_label', 'tool_note'):
                v = str(j.get(k) or '').strip()
                if v:
                    ensure_catalog_entry(
                        v, form=CONDUCTOR_FORM, ctx='hive_%s_%s' % (jid, k), persist=False
                    )
            for ex in j.get('examples') or []:
                ex = str(ex or '').strip()
                if ex:
                    ensure_catalog_entry(
                        ex, form=CONDUCTOR_FORM, ctx='hive_%s_ex' % jid, persist=False
                    )
    except Exception:
        pass
    for en in _CONDUCTOR_STATIC_STRINGS:
        ensure_catalog_entry(en, form=CONDUCTOR_FORM, ctx='Conductor.ui', persist=False)
    # Pull every rotating suggestion label (hive examples + creative lines).
    try:
        for ready in (False, True):
            for item in _conductor_suggestion_pool(ai_ready=ready):
                t = str(item.get('text') or '').strip()
                if t:
                    ensure_catalog_entry(t, form=CONDUCTOR_FORM, ctx='Conductor.suggest', persist=False)
                p = str(item.get('prefill') or '').strip()
                if p:
                    ensure_catalog_entry(p, form=CONDUCTOR_FORM, ctx='Conductor.suggest_prefill', persist=False)
    except Exception:
        pass
    save_catalog()
    _strings_seeded = True


def _seed_conductor_strings_async():
    """Catalog seed must never block Conductor paint / fade."""
    if _strings_seeded:
        return

    def _work():
        try:
            _seed_conductor_strings()
        except Exception:
            pass

    threading.Thread(target=_work, name='conductor_seed', daemon=True).start()


_neury_ready_state = {'value': False, 'ts': 0.0, 'probing': False}
_NEURY_READY_TTL_SEC = 4.0
_mesh_blurb_cache = {'primary': '', 'pulse': '', 'sig': None, 'ts': 0.0, 'probing': False}
_MESH_BLURB_TTL_SEC = 1.5


_DEFAULT_CONFIG = {
    'enabled': True,
    'completed_once': False,
    'last_soft_shown_on': '',
    'startup_mode': 'auto',
}


_FADE_IN_MS = 750
_FADE_OUT_MS = 1000
_FADE_STEPS = 20
_CONDUCTOR_NEURY_TIMEOUT_SEC = 90
# Complex asks that may call peer Neurys (Auto escalate) need invite+job headroom.
_CONDUCTOR_NEURY_MESH_TIMEOUT_SEC = 180
_CONDUCTOR_NEURY_LONG_TIMEOUT_SEC = 600
_CONDUCTOR_NEURY_PROJECT_TIMEOUT_SEC = 900
_MESH_POLL_MS = 1000
_MESH_PULSE_EVERY = 4  # rotate secondary stat line every N seconds
_SUGGEST_ROTATE_MS = 5500
_SUGGEST_VISIBLE = 3


# ================================================================
# Conductor web search — exact TEST/URLs.py fetch (requests only).
# Never call NeuronNeuryAgent for URL / image retrieval.
# ================================================================

class _ConductorSearchResult:

    def __init__(
        self,
        url="",
        title="",
        description="",
        image_data=None,
    ):
        self.url = url
        self.title = title
        self.description = description
        self.image_data = image_data


class _ConductorWebsiteFetcher:
    """Pasted from TEST/URLs.py WebsiteWorker — requests.Session only."""

    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.proxies = {'http': None, 'https': None}
        self.session.headers.update({
            "User-Agent":
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/153.0 Safari/537.36"
        })

    def fetch_website(self, url):
        response = self.session.get(
            url,
            timeout=10,
            allow_redirects=True
        )
        response.raise_for_status()
        content_type = response.headers.get(
            "Content-Type",
            ""
        )
        if "text/html" not in content_type.lower():
            return None
        source = response.text

        title = self.extract_meta(source, "og:title")
        if not title:
            match = re.search(
                r"<title[^>]*>(.*?)</title>",
                source,
                re.I | re.S
            )
            if match:
                title = self.clean_text(match.group(1))
        if not title:
            title = response.url

        description = self.extract_meta(source, "og:description")
        if not description:
            description = self.extract_meta(source, "description")
        if not description:
            description = ""
        description = self.clean_text(description)
        if len(description) > 240:
            description = description[:237] + "..."

        image_url = self.extract_meta(source, "og:image")
        if not image_url:
            match = re.search(
                r'<img[^>]+src=["\']([^"\']+)["\']',
                source,
                re.I
            )
            if match:
                image_url = match.group(1)

        image_data = None
        if image_url:
            image_url = urljoin(
                response.url,
                html_lib.unescape(image_url)
            )
            try:
                image_response = self.session.get(
                    image_url,
                    timeout=8
                )
                if image_response.ok:
                    image_data = image_response.content
            except Exception:
                image_data = None

        return _ConductorSearchResult(
            url=response.url,
            title=title,
            description=description,
            image_data=image_data
        )

    @staticmethod
    def extract_meta(source, property_name):
        pattern1 = (
            r'<meta[^>]+'
            r'(?:property|name)=["\']'
            + re.escape(property_name)
            + r'["\'][^>]+'
            r'content=["\']([^"\']*)["\']'
        )
        match = re.search(pattern1, source, re.I)
        if match:
            return html_lib.unescape(match.group(1))
        pattern2 = (
            r'<meta[^>]+'
            r'content=["\']([^"\']*)["\']'
            r'[^>]+'
            r'(?:property|name)=["\']'
            + re.escape(property_name)
            + r'["\']'
        )
        match = re.search(pattern2, source, re.I)
        if match:
            return html_lib.unescape(match.group(1))
        return ""

    @staticmethod
    def clean_text(text):
        text = re.sub(r"\s+", " ", text)
        return html_lib.unescape(text).strip()


def _conductor_ddg_hit_urls(query, limit=6, session=None):
    """Find result URLs via DuckDuckGo HTML — same requests.Session as URLs.py."""
    q = (query or '').strip()
    if not q:
        return []
    if session is None:
        session = requests.Session()
        session.trust_env = False
        session.proxies = {'http': None, 'https': None}
        session.headers.update({
            "User-Agent":
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/153.0 Safari/537.36",
        })
    html = ''
    try:
        # POST — GET often returns DDG anomaly / zero results.
        resp = session.post(
            'https://html.duckduckgo.com/html/',
            data={'q': q},
            timeout=12,
            allow_redirects=True,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
            },
        )
        resp.raise_for_status()
        html = resp.text or ''
        if 'result__a' not in html:
            resp = session.get(
                'https://html.duckduckgo.com/html/?q=%s' % quote(q),
                timeout=12,
                allow_redirects=True,
            )
            resp.raise_for_status()
            html = resp.text or ''
    except Exception:
        return []

    def _unwrap(href):
        h = (href or '').strip()
        if not h:
            return ''
        if h.startswith('//'):
            h = 'https:' + h
        if 'uddg=' in h:
            try:
                real = (parse_qs(urlparse(h).query).get('uddg') or [''])[0]
                if real:
                    return unquote(real)
            except Exception:
                pass
        return h

    urls = []
    seen = set()
    for m in re.finditer(
        r'class="result__a"[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        html,
        re.I | re.S,
    ):
        href = _unwrap(m.group(1))
        title = _ConductorWebsiteFetcher.clean_text(
            re.sub(r'<[^>]+>', ' ', m.group(2))
        )
        if not href or not href.startswith('http'):
            continue
        key = href.lower().rstrip('/')
        if key in seen:
            continue
        seen.add(key)
        urls.append({'url': href, 'title': title})
        if len(urls) >= max(1, int(limit or 6)):
            break
    return urls


def _conductor_web_search(query, limit=6):
    """Conductor-owned web search: DDG hit URLs + exact TEST/URLs.py page/image fetch."""
    fetcher = _ConductorWebsiteFetcher()
    hits = _conductor_ddg_hit_urls(query, limit=limit, session=fetcher.session)
    if not hits:
        return []
    cards = []
    for hit in hits:
        url = str(hit.get('url') or '').strip()
        if not url:
            continue
        try:
            result = fetcher.fetch_website(url)
        except Exception:
            result = None
        if result is None:
            cards.append({
                'title': str(hit.get('title') or url).strip() or 'Link',
                'text': '',
                'url': url,
                'image_data': None,
                'action_label': 'Open',
            })
            continue
        title = (result.title or hit.get('title') or url).strip() or 'Link'
        cards.append({
            'title': title,
            'text': (result.description or '').strip(),
            'url': (result.url or url).strip(),
            'image_data': result.image_data,
            'action_label': 'Open',
        })
    return cards


def _conductor_web_notes(web_cards, limit=6):
    """Fact snippets only (prefer descriptions) — never title lists for the model to expand."""
    notes = []
    for c in list(web_cards or [])[:limit]:
        snip = str(c.get('text') or '').strip()
        tline = str(c.get('title') or '').strip()
        if snip and len(snip) >= 40:
            notes.append(snip[:280])
        elif snip and tline:
            notes.append('%s: %s' % (tline[:80], snip[:200]))
    return notes


def _conductor_finalize_text(text, paid_peers=0, paid_amount=0.0):
    try:
        from Support.NeuronPromptCoach import sanitize_neury_voice, finish_if_cut_off
        text = finish_if_cut_off(sanitize_neury_voice(text or ''))
    except Exception:
        text = (text or '').strip()
    if not text:
        return ''
    if paid_peers > 0:
        if paid_amount > 0:
            text = text.rstrip() + '\n\n— Collaborated with %s peer%s · paid %.4g Neurons' % (
                paid_peers, 's' if paid_peers != 1 else '', paid_amount,
            )
        else:
            text = text.rstrip() + '\n\n— Collaborated with %s peer%s' % (
                paid_peers, 's' if paid_peers != 1 else '',
            )
        try:
            import Support.Initializations as init
            dlg = getattr(init, 'ai_dialog', None)
            if dlg is not None and hasattr(dlg, 'notify_neuron_payment') and paid_amount > 0:
                dlg.notify_neuron_payment(
                    'Paid %.4g Neurons to %s peer%s for mesh help.' % (
                        paid_amount, paid_peers, 's' if paid_peers != 1 else '',
                    ),
                    'peer(s)',
                    paid_amount,
                    'AI mesh work',
                )
        except Exception:
            pass
    return text


def _conductor_synthesize_reply(question, web_cards=None, draft='', stage_cb=None):
    """
    GPT-like Conductor answer: Prompt Coach + local Ollama (+ paid mesh when Auto).
    Direct second-person voice; never dump web titles.
    """
    q = (question or '').strip()
    if not q:
        return ''

    def _stage(msg):
        if stage_cb:
            try:
                stage_cb(msg)
            except Exception:
                pass

    coach = {}
    try:
        from Support.NeuronPromptCoach import (
            coach_user_prompt, enrich_chat_prompt, ensure_outbound_escalate_default,
            escalation_hints_from_coach, sanitize_neury_voice, finish_if_cut_off,
            quality_assess, polish_instruction,
        )
        ensure_outbound_escalate_default()
        coach = coach_user_prompt(q)
    except Exception:
        coach = {}
        enrich_chat_prompt = None
        escalation_hints_from_coach = None
        sanitize_neury_voice = lambda t: t
        finish_if_cut_off = lambda t: t
        quality_assess = None
        polish_instruction = None

    notes = _conductor_web_notes(web_cards)
    draft = (draft or '').strip()
    dlow = draft.lower()
    if (
        draft.startswith('Here is what I found on the web')
        or dlow.startswith('here are sources')
        or 'the user asked' in dlow
        or 'could not verify from your kb' in dlow
    ):
        draft = ''

    # Prefer facts-only context; never feed page-title catalogs as content to continue.
    context_bits = []
    if notes:
        context_bits.append(
            'Optional facts (weave into advice if useful; do NOT list websites or invent Script 1/2/3):\n'
            + '\n'.join('- ' + n for n in notes[:5])
        )

    try:
        prompt = enrich_chat_prompt(q, coach) if enrich_chat_prompt else q
    except Exception:
        prompt = q
    if context_bits:
        prompt = (
            prompt
            + '\n\n'
            + '\n\n'.join(context_bits)
            + '\n\nNeury (answer the human directly now):'
        )

    # Answer locally first — mesh only as optional polish after (same as neury_resolve_ask).
    paid_peers = 0
    paid_amount = 0.0

    _stage('Writing your answer…')
    num_predict = max(512, int((coach or {}).get('num_predict') or 768))
    text = ''
    try:
        from Support.NeuronAIMesh import run_local_ollama
        text = (run_local_ollama(prompt, num_predict=num_predict, timeout_sec=120) or '').strip()
    except Exception:
        text = ''

    try:
        gate = quality_assess(q, text, coach) if quality_assess else {'ok': bool(text)}
    except Exception:
        gate = {'ok': bool(text)}
    if text and gate.get('needs_rewrite') and polish_instruction:
        try:
            from Support.NeuronAIMesh import run_local_ollama as _ollama
            _stage('Improving answer…')
            polish = polish_instruction(q, text, coach)
            improved = (_ollama(polish, num_predict=num_predict, timeout_sec=120) or '').strip()
            if improved and len(improved) > 40:
                text = improved
                if quality_assess:
                    gate = quality_assess(q, text, coach) or gate
        except Exception:
            pass

    if gate.get('needs_mesh') and coach.get('escalate_hint'):
        try:
            from Support.NeuronNeuryAgent import _neury_mesh_polish_answer
            polished, paid = _neury_mesh_polish_answer(
                q, text, coach=coach, stage_cb=stage_cb,
            )
            if polished:
                text = polished
                paid_peers = int((paid or {}).get('peers') or 0)
                paid_amount = float((paid or {}).get('amount') or 0)
        except Exception:
            pass

    text = _conductor_finalize_text(text, paid_peers, paid_amount)
    if text and 'the user asked' not in text.lower() and not text.lower().startswith('here is what i found'):
        return text
    if notes:
        return (
            'Here is the practical take for you: '
            + notes[0][:240]
        )
    try:
        from Support.NeuronNeuryAgentChat import neury_clarify_when_unsure
        clarify = neury_clarify_when_unsure('', kind='advice') or ''
        if clarify:
            return clarify
    except Exception:
        pass
    return 'I want to help, but I need one more detail. What matters most to you here?'


class _ConductorWebResultCard(QFrame):
    """Display one search hit with in-memory image (TEST/URLs.py style)."""

    def __init__(self, title='', snippet='', url='', image_data=None, parent=None):
        super().__init__(parent)
        self._url = (url or '').strip()
        self.setObjectName('conductorWebResult')
        self.setStyleSheet(
            "QFrame#conductorWebResult {"
            " background-color: rgba(6, 12, 20, 150);"
            " border: 1px solid rgba(255, 255, 255, 42); border-radius: 12px; }"
            " QLabel { background: transparent; border: none; }"
            " QPushButton#conductorOpenLink {"
            " background-color: rgba(0, 120, 200, 170); color: #ffffff;"
            " border: 1px solid rgba(120, 200, 255, 160); border-radius: 6px;"
            " padding: 6px 12px; font-weight: bold; }"
            " QPushButton#conductorOpenLink:hover {"
            " background-color: rgba(0, 160, 255, 210); }"
        )
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(12)

        thumb = QLabel('')
        thumb.setFixedSize(148, 98)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setStyleSheet(
            "background-color: rgba(255,255,255,10); border: 1px solid rgba(255,255,255,18); "
            "border-radius: 6px; color:#8aa0b5; font-size:8pt;"
        )
        if image_data:
            pix = QPixmap()
            if pix.loadFromData(image_data):
                pix = pix.scaled(
                    148, 98,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                thumb.setPixmap(pix)
        root.addWidget(thumb, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        safe_title = re.sub(r'https?://\S+', '', (title or 'Link'), flags=re.I).strip() or 'Link'
        safe_title = re.sub(r'\bwww\.\S+', '', safe_title, flags=re.I).strip() or 'Link'
        title_lbl = QLabel(safe_title)
        title_lbl.setStyleSheet("color:#7ec8ff; font-size:13pt; font-weight:bold;")
        title_lbl.setWordWrap(True)
        title_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        col.addWidget(title_lbl)

        snip = re.sub(r'https?://\S+', '', (snippet or '').strip(), flags=re.I)
        snip = re.sub(r'\bwww\.\S+', '', snip, flags=re.I)
        snip = re.sub(r'\s+', ' ', snip).strip()
        if len(snip) > 200:
            snip = snip[:197] + '...'
        snip_lbl = QLabel(snip)
        snip_lbl.setWordWrap(True)
        snip_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        snip_lbl.setStyleSheet("color:#d5e0ea; font-size:10pt;")
        snip_lbl.setVisible(bool(snip))
        col.addWidget(snip_lbl)

        row = QHBoxLayout()
        row.addStretch(1)
        open_btn = QPushButton(_t('Open'))
        open_btn.setObjectName('conductorOpenLink')
        open_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        open_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        open_btn.clicked.connect(self._open_link)
        row.addWidget(open_btn)
        col.addLayout(row)
        root.addLayout(col, 1)

    def _open_link(self):
        target = (self._url or '').strip()
        if not target:
            return

        host = self.parent()
        while host is not None and not hasattr(host, 'enter_desktop'):
            try:
                host = host.parent()
            except Exception:
                host = None

        # Stop Conductor reclaiming Z-order so NeuronBrowser can keep focus.
        if host is not None and hasattr(host, '_allow_external_focus'):
            try:
                host._allow_external_focus(True)
            except Exception:
                pass

        low = target.lower()
        opens_player = low.startswith('neuron://open/vision')
        opens_browser = (
            low.startswith('http://') or low.startswith('https://')
        )
        opens_dialog = low.startswith('neuron://') and not opens_browser and not opens_player

        def _do_open():
            try:
                from Support.NeuronOrchestrator import open_neuron_link
                if open_neuron_link(target):
                    return
            except Exception:
                pass
            # Unwrap DuckDuckGo redirect wrappers if present.
            real = target
            if 'uddg=' in real:
                try:
                    from urllib.parse import parse_qs, unquote, urlparse
                    qs = parse_qs(urlparse(real).query)
                    uddg = (qs.get('uddg') or [''])[0]
                    if uddg:
                        real = unquote(uddg)
                except Exception:
                    pass
            if real.startswith('http://') or real.startswith('https://'):
                try:
                    from Support.Misc import open_in_neuron_browser
                    # Single raise — multi-retry flashes the browser 4–5 times.
                    open_in_neuron_browser(real, '0', focus_retries=1)
                    return
                except Exception:
                    pass
            qurl = QUrl.fromUserInput(real)
            if qurl.isValid():
                QDesktopServices.openUrl(qurl)

        if opens_player:
            # VLC / local file player — yield Conductor, play on UI thread, then leave cover.
            _do_open()
            if host is not None and hasattr(host, 'enter_desktop'):
                try:
                    host.enter_desktop()
                except Exception:
                    pass
            return

        if opens_browser:
            # Open in the click stack (Windows foreground rules), then leave Conductor.
            _do_open()
            if host is not None and hasattr(host, 'enter_desktop'):
                try:
                    host.enter_desktop()
                except Exception:
                    pass
            return

        # Desktop dialogs (Search Public Files, etc.): fade Conductor first.
        if opens_dialog and host is not None and hasattr(host, 'enter_desktop'):
            try:
                host.enter_desktop()
            except Exception:
                pass
            QTimer.singleShot(900, _do_open)
            return
        _do_open()


def _cover_screen_geometry():
    """Stable full-cover rect. Never use host.frameGeometry (that snaps during maximize)."""
    screen = QApplication.primaryScreen()
    if screen is not None:
        geo = screen.availableGeometry()
        if geo is not None and not geo.isNull() and geo.width() > 100:
            return geo
    return None


def _apply_cover_geometry(widget, host=None):
    """Fill host client area when Conductor is a child (Fade.py). Use screen size if host is still tiny."""
    from PySide6.QtCore import QRect

    if widget is None:
        return None
    screen_geo = _cover_screen_geometry()
    if host is not None:
        try:
            if widget.parent() is host:
                # Ensure host is already screen-sized before measuring client rect.
                if screen_geo is not None:
                    try:
                        if host.width() < 100 or host.height() < 100:
                            host.setGeometry(screen_geo)
                    except Exception:
                        pass
                r = host.rect()
                if r.width() < 100 or r.height() < 100:
                    if screen_geo is not None:
                        r = QRect(0, 0, screen_geo.width(), screen_geo.height())
                    else:
                        return None
                if widget.geometry() != r:
                    widget.setGeometry(r)
                return r
        except Exception:
            pass
    geo = screen_geo
    if geo is None and host is not None:
        try:
            geo = host.geometry()
        except Exception:
            geo = None
    if geo is None or geo.isNull() or geo.width() < 100:
        return None
    try:
        if widget.geometry() != geo:
            widget.setGeometry(geo)
    except Exception:
        return None
    return geo


def _hide_main_chrome(win):
    """Hide every Main chrome widget that could paint above Conductor."""
    if win is None:
        return
    for attr in ('canvas', 'watermark', 'name', 'name1', 'taskbar', 'window_controls'):
        w = getattr(win, attr, None)
        if w is None and attr == 'taskbar':
            w = getattr(init, 'taskbar', None)
        if w is not None:
            try:
                w.hide()
            except Exception:
                pass
    try:
        macros = getattr(init, 'macros_bar', None)
        if macros is not None:
            macros.hide()
    except Exception:
        pass
    _set_desktop_icons_visible(False)


def _conductor_suggestion_pool(ai_ready=False):
    """Build clickable suggestions from real Hive + neuron action catalogs."""
    pool = []
    seen = set()

    def _add(text, route, target='', needs_files=False, needs_neury=False, prefill='', auto_run=False):
        label = str(text or '').strip()
        if not label:
            return
        key = label.lower()
        if key in seen:
            return
        if needs_neury and not ai_ready:
            return
        seen.add(key)
        # Register English source so Translation editor / packs see suggestions.
        ensure_catalog_entry(label, form=CONDUCTOR_FORM, ctx='Conductor.suggest', persist=False)
        if prefill and str(prefill).strip() and str(prefill).strip() != label:
            ensure_catalog_entry(
                str(prefill).strip(), form=CONDUCTOR_FORM, ctx='Conductor.suggest_prefill',
                persist=False,
            )
        pool.append({
            'text': label,
            'route': route,
            'target': target,
            'needs_files': bool(needs_files),
            'needs_neury': bool(needs_neury),
            'prefill': str(prefill or label),
            'auto_run': bool(auto_run),
        })

    # Always-on home / ask ideas
    _add('Start my day', 'ask', needs_neury=True, prefill='Start my day', auto_run=True)
    _add('Send an email', 'ask', needs_neury=True, prefill='Send an email to ')
    _add('Send a chat message', 'action', 'chat_send', prefill='Tell ')
    _add('Keep this file safe', 'home', 'keep_safe')
    _add('What is on my calendar?', 'action', 'calendar_list')
    _add('Who is on my mesh?', 'action', 'mesh_status')
    _add('Invite a friend', 'action', 'invite_copy')
    if ai_ready:
        _add('Ask Neury anything', 'home', 'ask_neury', needs_neury=True)
        _add('Create a picture from a description', 'hive', 'pictures', needs_neury=True)
        _add('Translate this into Italian', 'hive', 'translate', needs_neury=True,
             prefill='Translate into Italian: ')

    try:
        for job in cpower.list_hive_jobs(include_neury_gated=bool(ai_ready)):
            jid = str(job.get('id') or '')
            title = str(job.get('title') or jid)
            examples = list(job.get('examples') or [])
            creative = {
                'voiceover': 'Create a voiceover from a text',
                'captions': 'Caption this interview',
                'video_convert': 'Convert a video to MP4',
                'movie_render': 'Render a Blender animation',
                'ocr': 'Make these scans searchable',
                'stems': 'Split a song into stems',
                'photo_batch': 'Resize photos for the web',
                'enlarge_photos': 'Enlarge and restore a photo',
                'documents': 'Convert this document',
                'databases': 'Query this spreadsheet',
                'maps': 'Process map layers',
            }.get(jid, '')
            if creative:
                _add(
                    creative, 'hive', jid,
                    needs_files=bool(job.get('needs_files')),
                    needs_neury=bool(job.get('needs_neury')),
                    prefill=examples[0] if examples else '',
                )
            for ex in examples[:2]:
                _add(
                    ex, 'hive', jid,
                    needs_files=bool(job.get('needs_files')),
                    needs_neury=bool(job.get('needs_neury')),
                    prefill=ex,
                )
            if not examples and not creative:
                _add(
                    title, 'hive', jid,
                    needs_files=bool(job.get('needs_files')),
                    needs_neury=bool(job.get('needs_neury')),
                )
    except Exception:
        pass

    try:
        for act in cpower.neuron_actions(include_neury=bool(ai_ready)):
            aid = str(act.get('id') or '')
            for ex in list(act.get('examples') or [])[:2]:
                _add(
                    ex, 'action', aid,
                    needs_neury=bool(act.get('needs_neury')),
                    prefill=ex if act.get('needs_input') else '',
                )
            if not act.get('examples'):
                _add(str(act.get('title') or aid), 'action', aid,
                     needs_neury=bool(act.get('needs_neury')))
    except Exception:
        pass

    random.shuffle(pool)
    # Keep the day briefing near the front of the rotating pills.
    for i, item in enumerate(list(pool)):
        if str(item.get('text') or '').lower() == 'start my day':
            pool.insert(0, pool.pop(i))
            break
    return pool


def _today():
    return time.strftime('%Y-%m-%d')


def load_config():
    data = dict(_DEFAULT_CONFIG)
    try:
        if os.path.isfile(_CONFIG_PATH):
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as fh:
                loaded = json.load(fh) or {}
            if isinstance(loaded, dict):
                data.update(loaded)
    except Exception:
        pass
    return data


def save_config(data):
    try:
        os.makedirs(PATH_JSON, exist_ok=True)
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2)
    except Exception:
        pass


def is_enabled():
    return bool(load_config().get('enabled', True))


def should_show_on_startup():
    cfg = load_config()
    if not cfg.get('enabled', True):
        return False
    return True


def mark_completed_once():
    cfg = load_config()
    cfg['completed_once'] = True
    save_config(cfg)


def mark_soft_shown():
    cfg = load_config()
    cfg['last_soft_shown_on'] = _today()
    save_config(cfg)


def background_path():
    path = os.path.join(init.PATH_SYSTEM_SUPPORT_IMAGES, _BG_NAME)
    if os.path.isfile(path):
        return path
    return ''


def _conductor_logo_path():
    """Prefer User/Media/Images/neuron.png; fall back to Icons / Support."""
    candidates = (
        os.path.join(init.MAIN_PATH, 'User', 'Media', 'Images', 'neuron.png'),
        os.path.join(init.MAIN_PATH, 'User', 'Media', 'Images', 'Neuron.png'),
        os.path.join(getattr(init, 'PATH_SYSTEM_ICONS', ''), 'Neuron.png'),
        os.path.join(init.PATH_SYSTEM_SUPPORT_IMAGES, 'Neuron.png'),
    )
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ''


def _count_balance_helps(balance_type):
    """How many times this node earned for a helpful mesh action."""
    if not balance_type or not getattr(init, 'pool_main_db', None):
        return 0
    try:
        from Support.Utils import query_database
        rows = query_database(
            init.pool_main_db,
            "SELECT COUNT(*) FROM Balance WHERE type = ?",
            (balance_type,),
            commit=False,
        )
        if rows and rows[0]:
            return int(rows[0][0] or 0)
    except Exception:
        pass
    return 0


def _conductor_live_stats():
    """Live mesh snapshot for Conductor strip — real DB/state only, no mock numbers."""
    stats = {
        'peers_online': 0,
        'peer_chunks_held': 0,
        'neuronized_files': 0,
        'files_complete': 0,
        'files_incomplete': 0,
        'balance': 0.0,
        'earn_hint': '',
        'relay_helps': 0,
        'store_helps': 0,
        'rooms': 0,
        'chat_unread': 0,
    }
    try:
        from Support.NetworkHubWeb import personal_mesh_status, network_live_counts
        st = personal_mesh_status() or {}
        stats['peers_online'] = int(st.get('peers_online') or 0)
        stats['peer_chunks_held'] = int(st.get('peer_chunks_held') or 0)
        stats['neuronized_files'] = int(st.get('neuronized_files') or 0)
        stats['files_complete'] = int(st.get('files_complete') or 0)
        stats['balance'] = float(st.get('balance') or 0)
        stats['earn_hint'] = str(st.get('earn_hint') or '').strip()
        live = network_live_counts() or {}
        stats['chat_unread'] = int(live.get('chat_unread') or 0)
    except Exception:
        try:
            from Support.FirstRunExperience import whisper_snapshot
            snap = whisper_snapshot() or {}
            stats['peers_online'] = int(snap.get('peers_online') or 0)
            stats['peer_chunks_held'] = int(snap.get('peer_chunks_held') or 0)
            stats['balance'] = float(snap.get('balance') or 0)
        except Exception:
            pass
    incomplete = stats['neuronized_files'] - stats['files_complete']
    stats['files_incomplete'] = max(0, incomplete)
    stats['relay_helps'] = _count_balance_helps(
        getattr(init, 'BALANCE_SHEET_RELAY_ACTIVITY_I_PERFORM', '')
    )
    stats['store_helps'] = _count_balance_helps(
        getattr(init, 'BALANCE_SHEET_I_STORED_PEER_DATA', '')
    )
    try:
        stats['rooms'] = len(list(getattr(init, 'ROOMS', None) or []))
    except Exception:
        stats['rooms'] = 0
    return stats


def _mesh_pulse_lines(stats):
    """Secondary rotating facts — only include lines that have something real to say."""
    lines = []
    bal = float(stats.get('balance') or 0)
    if bal > 0.05:
        if bal >= 100:
            lines.append(_tf('%.0f Neurons on this node', bal))
        else:
            lines.append(_tf('%.1f Neurons on this node', bal))
    hint = str(stats.get('earn_hint') or '').strip()
    if hint:
        lines.append(_tf('Just now: %s', hint))
    store = int(stats.get('store_helps') or 0)
    if store > 0:
        lines.append(_tf('Stored peer data %s times', store))
    relay = int(stats.get('relay_helps') or 0)
    if relay > 0:
        lines.append(_tf('Relayed for peers %s times', relay))
    incomplete = int(stats.get('files_incomplete') or 0)
    if incomplete > 0:
        lines.append(_tf('%s files still syncing across the mesh', incomplete))
    rooms = int(stats.get('rooms') or 0)
    if rooms > 0:
        lines.append(_tf('%s rooms on this node', rooms))
    unread = int(stats.get('chat_unread') or 0)
    if unread > 0:
        lines.append(_tf(
            '%s unread chat message%s',
            unread,
            '' if unread == 1 else 's',
        ))
    complete = int(stats.get('files_complete') or 0)
    if complete > 0 and incomplete <= 0:
        lines.append(_tf('%s files fully protected', complete))
    peers = int(stats.get('peers_online') or 0)
    if peers <= 0:
        lines.append(_t('Invite a friend — grow the mesh beyond this node'))
    elif not lines:
        lines.append(_t('Mesh is live — tell Neuron what you want to do'))
    return lines


def _mesh_blurb():
    """One-line primary strip (kept for trust / stay-result callers)."""
    primary, _pulse = _mesh_blurb_pair(0)
    return primary


def _mesh_blurb_pair(pulse_i=0):
    stats = _conductor_live_stats()
    peers = int(stats.get('peers_online') or 0)
    chunks = int(stats.get('peer_chunks_held') or 0)
    files = int(stats.get('neuronized_files') or 0)
    parts = []
    if peers > 0:
        parts.append(_tf('%s peers online', peers))
    parts.append(_tf('%s chunks you hold', chunks))
    parts.append(_tf('%s files protected', files))
    primary = '  ·  '.join(parts)
    pulses = _mesh_pulse_lines(stats)
    pulse = pulses[int(pulse_i) % len(pulses)]
    return primary, pulse


def _neury_ready():
    """Cached readiness — never block the UI thread on Ollama HTTP."""
    now = time.time()
    age = now - float(_neury_ready_state.get('ts') or 0)
    if _neury_ready_state.get('ts') and age < _NEURY_READY_TTL_SEC:
        return bool(_neury_ready_state.get('value'))
    if not _neury_ready_state.get('probing'):
        _neury_ready_state['probing'] = True

        def _probe():
            try:
                from Support.NeuronAIInstaller import is_ai_installed
                val = bool(is_ai_installed(ensure_running=False))
            except Exception:
                val = False
            _neury_ready_state['value'] = val
            _neury_ready_state['ts'] = time.time()
            _neury_ready_state['probing'] = False

        threading.Thread(target=_probe, name='neury_ready_probe', daemon=True).start()
    return bool(_neury_ready_state.get('value'))


def _kick_mesh_blurb_refresh(pulse_i=0):
    """Compute mesh strip text off the UI thread."""
    if _mesh_blurb_cache.get('probing'):
        return
    _mesh_blurb_cache['probing'] = True

    def _work():
        try:
            primary, pulse = _mesh_blurb_pair(pulse_i)
        except Exception:
            try:
                primary = _mesh_blurb()
            except Exception:
                primary = ''
            pulse = ''
        _mesh_blurb_cache['primary'] = primary
        _mesh_blurb_cache['pulse'] = pulse
        _mesh_blurb_cache['sig'] = primary
        _mesh_blurb_cache['ts'] = time.time()
        _mesh_blurb_cache['probing'] = False

    threading.Thread(target=_work, name='conductor_mesh', daemon=True).start()


def _desktop_chrome_visible(win=None):
    """True when the Neuron desktop chrome is already showing (soft reopen)."""
    win = win or getattr(init, 'mainApplication', None)
    tb = None
    if win is not None:
        tb = getattr(win, 'taskbar', None)
    if tb is None:
        tb = getattr(init, 'taskbar', None)
    if tb is None:
        return False
    try:
        return bool(tb.isVisible())
    except Exception:
        return False


def _set_desktop_icons_visible(visible):
    for itm in list(getattr(init, 'icon_list', None) or []):
        w = getattr(itm, 'icon', None) or itm
        if w is None:
            continue
        try:
            if visible:
                w.show()
                w.raise_()
            else:
                w.hide()
        except Exception:
            pass


def restore_desktop_chrome(win=None):
    """Force taskbar, icons, watermark and window controls back onto Main."""
    win = win or getattr(init, 'mainApplication', None)
    try:
        _detach_window_controls_from_conductor(_conductor, win)
    except Exception:
        pass
    try:
        _set_desktop_icons_visible(True)
    except Exception:
        pass
    for src in (win, init):
        if src is None:
            continue
        tb = getattr(src, 'taskbar', None)
        if tb is None:
            continue
        try:
            if win is not None and hasattr(tb, 'dock_to_parent_bottom'):
                tb.dock_to_parent_bottom()
            tb.show()
            tb.raise_()
        except Exception:
            pass
    if win is None:
        return
    for attr in ('watermark', 'name', 'name1', 'window_controls'):
        w = getattr(win, attr, None)
        if w is None:
            continue
        try:
            w.show()
            w.raise_()
        except Exception:
            pass
    try:
        canvas = getattr(win, 'canvas', None)
        if canvas is not None:
            canvas.lower()
    except Exception:
        pass
    try:
        for itm in list(getattr(init, 'icon_list', None) or []):
            w = getattr(itm, 'icon', None) or itm
            if w is not None:
                w.raise_()
    except Exception:
        pass
    try:
        init._conductor_taskbar_revealed = True
    except Exception:
        pass


def conductor_is_open():
    """True while Conductor is covering the desktop."""
    if not bool(getattr(init, 'conductor_visible', False)):
        return False
    ov = _conductor
    if ov is None:
        return False
    try:
        if getattr(ov, '_fading_out', False):
            return False
        if not ov.isVisible():
            return False
        return True
    except Exception:
        return False


def set_conductor_visible(visible):
    """Global gate: when True, no notification bubbles may show."""
    init.conductor_visible = bool(visible)
    if visible:
        try:
            for bubble in list(getattr(init, 'messages', None) or []):
                try:
                    bubble.hide()
                except Exception:
                    pass
                try:
                    if hasattr(bubble, '_dequeue_self'):
                        bubble._dequeue_self()
                except Exception:
                    pass
            if hasattr(init, 'messages'):
                init.messages = []
        except Exception:
            pass


def prepare_main_shell(win=None, map_window=None, keep_opacity_zero=False):
    """Hide chrome under Conductor. Optionally map Main HWND.

    keep_opacity_zero: size/map Main but leave opacity 0 (no black flash).
    Fade.py: children must already be parented before opaque show.
    """
    from PySide6.QtGui import QColor, QPalette

    win = win or getattr(init, 'mainApplication', None)
    if win is None:
        return False
    try:
        if hasattr(win, 'fade_object_timer') and win.fade_object_timer:
            win.fade_object_timer.stop()
    except Exception:
        pass
    try:
        if hasattr(win, '_fade_started'):
            win._fade_started = True
    except Exception:
        pass
    try:
        win.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        win.setAutoFillBackground(True)
        pal = win.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(6, 10, 22))
        win.setPalette(pal)
    except Exception:
        pass
    # Size to screen before any show — avoids the tiny default shell flash.
    try:
        geo = _cover_screen_geometry()
        if geo is not None:
            win.setGeometry(geo)
    except Exception:
        pass
    if map_window is None:
        try:
            map_window = (not win.isVisible()) or (float(win.windowOpacity()) < 0.5)
        except Exception:
            map_window = True
    if map_window:
        try:
            if keep_opacity_zero:
                win.setWindowOpacity(0.0)
                win.showMaximized()
                win.setWindowOpacity(0.0)
            else:
                win.setWindowOpacity(1.0)
                win.showMaximized()
                win.setWindowOpacity(1.0)
        except Exception:
            return False
    _hide_main_chrome(win)
    return True


def prepare_main_under_conductor(win=None, activate=False, show_icons=True):
    """Reveal the Neuron desktop under Conductor - maximized and opaque."""
    from PySide6.QtGui import QColor, QPalette

    win = win or getattr(init, 'mainApplication', None)
    if win is None:
        return False
    try:
        if hasattr(win, 'fade_object_timer') and win.fade_object_timer:
            win.fade_object_timer.stop()
    except Exception:
        pass
    try:
        if hasattr(win, '_fade_started'):
            win._fade_started = True
    except Exception:
        pass
    try:
        win.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        win.setAutoFillBackground(True)
        pal = win.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(6, 10, 22))
        win.setPalette(pal)
    except Exception:
        pass
    try:
        win.setWindowOpacity(1.0)
        if not win.isMaximized():
            win.showMaximized()
        # Only bury chrome while Conductor is actively covering.
        # Never call _push_icons_behind when revealing (show_icons=True) —
        # that undoes the restore because the Conductor object still exists.
        covering = (
            bool(getattr(init, 'conductor_visible', False))
            and _conductor is not None
            and not bool(getattr(_conductor, '_fading_out', False))
            and not bool(show_icons)
        )
        canvas = getattr(win, 'canvas', None)
        if canvas is not None:
            try:
                from Support.Misc import ensure_screen_dimensions, restore_desktop_canvas
                ensure_screen_dimensions(win)
                r = win.rect()
                cw = r.width() if r.width() > 100 else init.Screen.width
                ch = r.height() if r.height() > 100 else init.Screen.height
                canvas.setGeometry(0, 0, cw, ch)
            except Exception:
                pass
            if covering:
                # Solid stand-in while Conductor covers Main (wallpaper restored on reveal).
                try:
                    canvas.setStyleSheet('background-color: #060a16;')
                except Exception:
                    pass
            else:
                # Leaving Conductor: drop solid fill; keep existing pixmap (no reload flash).
                try:
                    from Support.Misc import restore_desktop_canvas
                    restore_desktop_canvas(win)
                except Exception:
                    try:
                        canvas.setStyleSheet('')
                    except Exception:
                        pass
            canvas.show()
            try:
                canvas.lower()
            except Exception:
                pass
        for attr in ('watermark', 'name', 'name1', 'taskbar', 'window_controls'):
            w = getattr(win, attr, None)
            if w is None and attr == 'taskbar':
                w = getattr(init, 'taskbar', None)
            if w is not None:
                try:
                    w.show()
                except Exception:
                    pass
        try:
            macros = getattr(init, 'macros_bar', None)
            if macros is not None:
                macros.hide()
        except Exception:
            pass
        _set_desktop_icons_visible(bool(show_icons))
        try:
            if covering:
                _conductor.raise_()
                _conductor._push_icons_behind()
        except Exception:
            pass
        if activate:
            try:
                win.activateWindow()
            except Exception:
                pass
        return True
    except Exception:
        return False


def reveal_main_desktop(win=None, raise_window=True):
    """Make the main desktop fully visible (Conductor already fading/hidden)."""
    win = win or getattr(init, 'mainApplication', None)
    try:
        from Support.Misc import (
            ensure_screen_dimensions,
            restore_desktop_canvas,
            desktop_icons_collapsed_at_origin,
            relayout_desktop_icons_from_db,
            load_desktop,
            load_icon_of_peers_in_room,
        )
        ensure_screen_dimensions(win)
        restore_desktop_canvas(win)
        # Only rebuild when first layout piled every icon at (0,0).
        if desktop_icons_collapsed_at_origin():
            if not relayout_desktop_icons_from_db(win):
                _set_desktop_icons_visible(False)
                load_desktop(win)
                load_icon_of_peers_in_room(win)
                _set_desktop_icons_visible(False)
    except Exception:
        pass
    prepare_main_under_conductor(win, activate=bool(raise_window), show_icons=True)
    # Explicit restore — prepare_main must not re-bury after leave.
    restore_desktop_chrome(win)
    if not raise_window:
        try:
            if _conductor is not None and bool(getattr(init, 'conductor_visible', False)):
                _conductor.raise_()
        except Exception:
            pass


def _stage_main_under_cover(win):
    """After Conductor is solid: keep shell mapped on first show; leave desktop alone on soft reopen."""
    if not _desktop_chrome_visible(win):
        prepare_main_shell(win)
    try:
        if (
            _conductor is not None
            and bool(getattr(init, 'conductor_visible', False))
            and not bool(getattr(_conductor, '_fading_out', False))
        ):
            _conductor.raise_()
            _conductor._push_icons_behind()
            _ensure_main_owns_taskbar()
    except Exception:
        pass



class _MeshAtmosphere(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._phase = 0.0
        self._nodes = []
        random.seed(42)
        for _ in range(28):
            self._nodes.append({
                'x': random.random(),
                'y': random.random(),
                'r': random.uniform(1.2, 3.2),
                's': random.uniform(0.4, 1.4),
            })
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # 80ms: mesh is translucent over the plate, so each tick also
        # repaints the wallpaper underneath — keep this modest.
        self._timer.start(80)

    def _tick(self):
        if not self.isVisible():
            return
        self._phase += 0.05
        self.update()

    def paintEvent(self, event):
        _ = event
        plate = self.parent()
        host = plate.parent() if plate is not None else None
        veil = 1.0
        if host is not None:
            try:
                veil = float(getattr(host, '_veil_bg', getattr(host, '_veil', 1.0)))
            except Exception:
                veil = 1.0
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setOpacity(max(0.0, min(1.0, veil)))
        w = max(1, self.width())
        h = max(1, self.height())
        pts = []
        for n in self._nodes:
            ox = math.sin(self._phase * n['s'] + n['x'] * 6) * 0.012
            oy = math.cos(self._phase * n['s'] * 0.8 + n['y'] * 5) * 0.012
            pts.append(QPointF((n['x'] + ox) * w, (n['y'] + oy) * h))

        pen = QPen(QColor(80, 200, 220, 28))
        pen.setWidthF(1.0)
        p.setPen(pen)
        for i, a in enumerate(pts):
            for j in range(i + 1, len(pts)):
                b = pts[j]
                dx = a.x() - b.x()
                dy = a.y() - b.y()
                if (dx * dx + dy * dy) < (w * 0.18) ** 2:
                    p.drawLine(a, b)

        for i, n in enumerate(self._nodes):
            pt = pts[i]
            glow = 90 + int(50 * math.sin(self._phase * n['s'] + i))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(120, 230, 255, max(40, min(160, glow))))
            p.drawEllipse(pt, n['r'], n['r'])
        p.end()


class _BackgroundPlate(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAutoFillBackground(False)
        self._pix = QPixmap()
        self._scaled = QPixmap()
        self._scaled_key = None
        path = background_path()
        if path:
            self._pix = QPixmap(path)
        self._mesh = _MeshAtmosphere(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._mesh.setGeometry(self.rect())
        self._scaled_key = None

    def _ensure_scaled(self, w, h):
        key = (int(w), int(h))
        if self._scaled_key == key and not self._scaled.isNull():
            return
        self._scaled_key = key
        if self._pix.isNull() or w < 1 or h < 1:
            self._scaled = QPixmap()
            return
        self._scaled = self._pix.scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )

    def paintEvent(self, event):
        _ = event
        host = self.parent()
        veil = 1.0
        if host is not None:
            try:
                veil = float(getattr(host, '_veil_bg', getattr(host, '_veil', 1.0)))
            except Exception:
                veil = 1.0
        veil = max(0.0, min(1.0, veil))
        p = QPainter(self)
        p.setOpacity(veil)
        w = self.width()
        h = self.height()
        self._ensure_scaled(w, h)
        if not self._scaled.isNull():
            x = (self._scaled.width() - w) // 2
            y = (self._scaled.height() - h) // 2
            p.drawPixmap(0, 0, self._scaled, x, y, w, h)
        else:
            p.fillRect(self.rect(), QColor(6, 10, 22))

        edge = QRadialGradient(w * 0.5, h * 0.42, max(w, h) * 0.72)
        edge.setColorAt(0.0, QColor(0, 0, 0, 30))
        edge.setColorAt(0.45, QColor(0, 0, 0, 90))
        edge.setColorAt(1.0, QColor(0, 0, 0, 210))
        p.fillRect(self.rect(), edge)

        band = QLinearGradient(0, 0, 0, h)
        band.setColorAt(0.0, QColor(0, 8, 20, 160))
        band.setColorAt(0.35, QColor(0, 0, 0, 40))
        band.setColorAt(0.7, QColor(0, 0, 0, 70))
        band.setColorAt(1.0, QColor(0, 4, 14, 200))
        p.fillRect(self.rect(), band)
        p.end()


class _ChipButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setStyleSheet(
            "QPushButton {"
            "  color:#e8f4ff; background:rgba(18,28,48,170);"
            "  border:1px solid rgba(120,190,220,90); border-radius:18px;"
            "  padding:10px 16px; font-size:13px; font-weight:600;"
            "}"
            "QPushButton:hover {"
            "  background:rgba(28,48,72,210); border-color:rgba(160,230,255,160);"
            "}"
            "QPushButton:checked {"
            "  background:rgba(20,90,110,210); border-color:rgba(120,240,255,220);"
            "  color:#ffffff;"
            "}"
        )


class ConductorOverlay(QWidget):
    """Front door. The desktop (Ferrari) stays under the hood until Open desktop."""

    closed = Signal()
    neury_done = Signal(str, str, object)  # title, body, web_cards
    job_progress = Signal(int, str)
    job_finished = Signal(dict)
    install_progress = Signal(int)
    install_status = Signal(str)
    install_finished = Signal()
    install_failed = Signal(str)
    action_finished = Signal(str, str, object)  # title, body, result dict
    # Worker-thread → UI (QueuedConnection): live log + consent prompts.
    progress_line = Signal(str)
    consent_needed = Signal(str, str)  # title, message
    project_board = Signal(object)  # {project_id, steps?, consent?}
    ui_tool_needed = Signal(str, object)  # tool name, args dict

    # Top-level lanes. Sub-menus rebuild chips from ConductorPower catalogs.
    CHIPS_BASIC = (
        ('keep_safe', 'Keep a file safe'),
        ('work_files', 'Work with files'),
        ('connect', 'Connect'),
        ('discover', 'Discover'),
        ('install_neury', 'Install Neury'),
    )
    CHIPS_FULL = (
        ('keep_safe', 'Keep a file safe'),
        ('ask_neury', 'Ask Neury'),
        ('work_files', 'Work with files'),
        ('connect', 'Connect'),
        ('discover', 'Discover'),
        ('create', 'Create & publish'),
    )

    def __init__(self, parent=None, soft=False):
        super().__init__(parent)
        self._soft = soft
        self._ai_ready = _neury_ready()
        self._chip = 'ask_neury' if self._ai_ready else 'keep_safe'
        # Drop leftover staging from earlier trips on every Conductor show.
        try:
            from Support.ConductorPower import cleanup_conductor_work_files
            cleanup_conductor_work_files()
        except Exception:
            pass
        self._phase = 'idle'
        self._stage_i = 0
        self._stages = []
        self._poll = None
        self._mesh_timer = None
        self._track_icon_id = ''
        self._track_file_name = ''
        self._pending_question = ''
        self._raise_timer = None
        self._fading_out = False
        self._fading_in = False
        self._geometry_locked = False
        self._yield_focus = False
        self._veil = 1.0
        self._veil_bg = 1.0
        self._veil_ui = 1.0
        self._fade_gen = 0
        self._neury_cancel = False
        self._neury_run_id = 0
        self._neury_reply_mode = False
        self._neury_thread_question = ''
        self._neury_thread_reply = ''
        self._neury_transcript = ''
        self._btn_neury_home = None
        self._main_lay = None
        self._res_row = None
        self._row_in_home_idx = 1
        self._host = parent
        self._after_fade = None
        self._on_ready = None
        self._pending_mail = None
        self._mail_account_btns = []
        self._menu_mode = 'home'  # home | hive | connect | discover | create | hive_setup
        self._menu_chips = []
        self._pending_files = []
        self._hive_job_id = ''
        self._hive_job_meta = None
        self._hive_tool_retry = False
        self._last_produced = []
        self._install_worker = None
        # Block focus until after promote + full-screen map (startup path).
        self._suppress_focus = True
        self._consent_ev = threading.Event()
        self._consent_answer = None
        self._consent_edited_text = ''
        self._consent_draft_edit = None
        self._consent_draft_meta = {}
        self._pending_consent = {}
        self._awaiting_consent = False
        self._consent_chrome_painted = False
        self._project_board_mode = False
        self._project_board_id = ''
        self._project_article_review = None
        self._job_t0 = 0.0
        self._elapsed_timer = None
        self.neury_done.connect(self._on_neury_done, Qt.ConnectionType.QueuedConnection)
        self.job_progress.connect(self._on_job_progress, Qt.ConnectionType.QueuedConnection)
        self.job_finished.connect(self._on_job_finished, Qt.ConnectionType.QueuedConnection)
        self.install_progress.connect(self._on_install_progress, Qt.ConnectionType.QueuedConnection)
        self.install_status.connect(self._on_install_status, Qt.ConnectionType.QueuedConnection)
        self.install_finished.connect(self._on_install_finished, Qt.ConnectionType.QueuedConnection)
        self.install_failed.connect(self._on_install_failed, Qt.ConnectionType.QueuedConnection)
        self.action_finished.connect(self._on_action_finished, Qt.ConnectionType.QueuedConnection)
        self._ui_tool_ev = threading.Event()
        self._ui_tool_result = None
        self.progress_line.connect(self._on_progress_line, Qt.ConnectionType.QueuedConnection)
        self.consent_needed.connect(self._on_consent_needed, Qt.ConnectionType.QueuedConnection)
        self.project_board.connect(self._on_project_board, Qt.ConnectionType.QueuedConnection)
        self.ui_tool_needed.connect(self._on_ui_tool_needed, Qt.ConnectionType.QueuedConnection)

        _seed_conductor_strings_async()

        self.setObjectName('conductorOverlay')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Child of Main only — never a separate translucent HWND.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        # Prefer building as a child of hidden Main (no top-level HWND). If
        # constructed with parent=None, stay unmapped until promote.
        try:
            self.hide()
            if parent is None:
                self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        except Exception:
            pass

        self._bg = _BackgroundPlate(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._bg)

        self._ui = QFrame(self._bg)
        self._ui.setStyleSheet('QFrame { background:transparent; border:none; }')
        self._ui_fade = QGraphicsOpacityEffect(self._ui)
        self._ui_fade.setOpacity(0.0)
        self._ui.setGraphicsEffect(self._ui_fade)
        lay = QVBoxLayout(self._ui)
        lay.setContentsMargins(48, 36, 48, 32)
        lay.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.lbl_brand_icon = QLabel(self._ui)
        self.lbl_brand_icon.setFixedSize(22, 22)
        self.lbl_brand_icon.setStyleSheet('background:transparent;')
        logo_path = _conductor_logo_path()
        if logo_path:
            pix = QPixmap(logo_path)
            if not pix.isNull():
                self.lbl_brand_icon.setPixmap(
                    pix.scaled(
                        22, 22,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        top.addWidget(self.lbl_brand_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        brand = QLabel(_t('NEURON'), self._ui)
        brand.setStyleSheet(
            'color:rgba(200,230,255,200); font-size:12px; font-weight:800;'
            ' letter-spacing:3px; background:transparent;')
        top.addWidget(brand, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        # Desktop control is on Main (Win_desktop.webp). Keep a hidden hook.
        self.btn_desktop = QPushButton('', self._ui)
        self.btn_desktop.hide()
        self.btn_desktop.clicked.connect(self.enter_desktop)
        lay.addLayout(top)

        lay.addStretch(1)

        name = ''
        if isinstance(init.ME, dict):
            name = str(init.ME.get('node_name') or init.ME.get('nickname') or '').strip()

        self.lbl_q = QLabel(_t('A New Kind of Internet'))
        self.lbl_q.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_q.setStyleSheet(
            'color:#f4f8ff; font-size:32px; font-weight:700; background:transparent; padding:4px;')
        lay.addWidget(self.lbl_q)

        self.lbl_welcome = QLabel('', self._ui)
        self.lbl_welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_welcome.setStyleSheet(
            'color:rgba(180,210,230,150); font-size:13px; background:transparent;')
        if soft:
            self.lbl_welcome.setText(
                _tf('Welcome back, %s', name) if name else _t('Welcome back')
            )
        lay.addWidget(self.lbl_welcome)
        if soft:
            self.lbl_welcome.show()
        else:
            self.lbl_welcome.hide()

        self.lbl_mesh = QLabel('')
        self.lbl_mesh.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_mesh.setStyleSheet(
            'color:rgba(180,220,240,210); font-size:14px; font-weight:600;'
            ' background:transparent; letter-spacing:0.3px;')
        lay.addWidget(self.lbl_mesh)

        self.lbl_mesh_pulse = QLabel('')
        self.lbl_mesh_pulse.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_mesh_pulse.setStyleSheet(
            'color:rgba(150,210,225,160); font-size:12px; background:transparent;'
            ' padding-bottom:2px;')
        lay.addWidget(self.lbl_mesh_pulse)
        self._mesh_tick = 0
        self._mesh_pulse_i = 0
        _kick_mesh_blurb_refresh(0)

        self.lbl_sub = QLabel('')
        self.lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_sub.setWordWrap(True)
        self.lbl_sub.setStyleSheet(
            'color:rgba(210,230,245,190); font-size:14px; background:transparent;')
        self.lbl_sub.hide()
        lay.addWidget(self.lbl_sub)

        self.lbl_prompt = QLabel(_t('What do you want Neuron to do?'))
        self.lbl_prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_prompt.setStyleSheet(
            'color:#9fe9ff; font-size:17px; font-weight:600; background:transparent; padding-top:8px;')
        lay.addWidget(self.lbl_prompt)

        self.input = QTextEdit()
        self.input.setObjectName('conductorInput')
        self.input.setAcceptRichText(False)
        self.input.setPlaceholderText(_t('Ask, search, create, or give Neuron a task…'))
        self.input.setMinimumHeight(48)
        self.input.setMaximumHeight(500)
        self.input.setMaximumWidth(640)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.input.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.input.setStyleSheet(
            "QTextEdit#conductorInput { color:#eef6ff; background:rgba(8,14,28,160);"
            " border:1px solid rgba(120,190,220,120); border-radius:14px;"
            " padding:10px 0px; font-size:15px; selection-background-color:#1a6a80; }"
        )
        try:
            opt = self.input.document().defaultTextOption()
            opt.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.input.document().setDefaultTextOption(opt)
        except Exception:
            pass
        self.input.textChanged.connect(self._on_input_text_changed)
        self.input.keyPressEvent = self._conductor_input_key
        try:
            from Support.NeuronAIInstaller import attach_prompt_char_countdown
            self._prompt_countdown = attach_prompt_char_countdown(
                self.input, corner='br',
            )
        except Exception:
            self._prompt_countdown = None
        # Keep typed blocks centered even after paste/edit.
        try:
            self.input.cursorPositionChanged.connect(self._keep_input_centered)
        except Exception:
            pass
        QTimer.singleShot(0, self._fit_input_height)

        self.btn_add_file = QPushButton('+', self._ui)
        self.btn_add_file.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_file.setFixedSize(48, 48)
        self.btn_add_file.setToolTip(_t('Add files'))
        # Font baseline sits low on '+'; pad bottom to optically center.
        self.btn_add_file.setStyleSheet(
            "QPushButton { color:#041018; background:#9fd0f0; border:none;"
            " border-radius:24px; font-size:26px; font-weight:800;"
            " padding:0px 0px 4px 0px; text-align:center; }"
            "QPushButton:hover { background:#b8e0ff; }"
        )
        self.btn_add_file.clicked.connect(self._browse_job_files)

        self.btn_run = QPushButton('', self._ui)
        self.btn_run.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run.setFixedSize(48, 48)
        self.btn_run.setToolTip(_t('Go'))
        self.btn_run.setStyleSheet(
            "QPushButton { color:#041018; background:#7ad7ef; border:none;"
            " border-radius:24px; padding:0px; }"
            "QPushButton:hover { background:#9aebff; }"
            "QPushButton:disabled { background:#3a5560; }"
        )
        self._run_icon_go = QIcon()
        try:
            _fwd = init.PATH_SYSTEM_SUPPORT_IMAGES + 'Foreward.png'
            if os.path.isfile(_fwd):
                self._run_icon_go = QIcon(_fwd)
        except Exception:
            pass
        self.btn_run.setIcon(self._run_icon_go)
        self.btn_run.setIconSize(QSize(20, 20))
        self.btn_run.clicked.connect(self._run_from_input)

        self._row_in = QHBoxLayout()
        self._row_in.setSpacing(10)
        self._row_in.addStretch(1)
        self._row_in.addWidget(self.btn_add_file, 0, Qt.AlignmentFlag.AlignVCenter)
        self._row_in.addWidget(self.input, 1)
        self._row_in.addWidget(self.btn_run, 0, Qt.AlignmentFlag.AlignVCenter)
        self._row_in.addStretch(1)
        self._main_lay = lay
        lay.addLayout(self._row_in)
        self._row_in_home_idx = lay.count() - 1

        self._suggest_wrap = QWidget(self._ui)
        self._suggest_wrap.setStyleSheet('background:transparent;')
        self._suggest_col = QVBoxLayout(self._suggest_wrap)
        self._suggest_col.setContentsMargins(0, 2, 0, 2)
        self._suggest_col.setSpacing(4)
        self._suggest_btns = []
        self._suggest_pool = []
        self._suggest_i = 0
        self._suggest_paused = False
        self._suggest_timer = None
        sug_row = QHBoxLayout()
        sug_row.addStretch(1)
        sug_row.addWidget(self._suggest_wrap)
        sug_row.addStretch(1)
        lay.addLayout(sug_row)

        self._chip_wrap = QWidget(self._ui)
        self._chip_wrap.setStyleSheet('background:transparent;')
        self._chips_col = QVBoxLayout(self._chip_wrap)
        self._chips_col.setContentsMargins(0, 4, 0, 4)
        self._chips_col.setSpacing(10)
        self._chip_btns = {}
        chip_row = QHBoxLayout()
        chip_row.addStretch(1)
        chip_row.addWidget(self._chip_wrap)
        chip_row.addStretch(1)
        lay.addLayout(chip_row)

        how_row = QHBoxLayout()
        how_row.addStretch(1)
        self.btn_how = QPushButton(_t('How it works →'))
        self.btn_how.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_how.setFlat(True)
        self.btn_how.setStyleSheet(
            "QPushButton { color:rgba(160,210,230,180); background:transparent; border:none;"
            " font-size:12px; text-decoration:underline; padding:4px; }"
            "QPushButton:hover { color:#c8f0ff; }"
        )
        self.btn_how.clicked.connect(self._start_learn)
        how_row.addWidget(self.btn_how)
        how_row.addStretch(1)
        lay.addLayout(how_row)

        self.lbl_stage = QLabel('')
        self.lbl_stage.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_stage.setWordWrap(True)
        self.lbl_stage.setStyleSheet(
            'color:#9fe9ff; font-size:15px; font-weight:600; background:transparent;')
        lay.addWidget(self.lbl_stage)

        self.result = QTextEdit()
        self.result.setReadOnly(True)
        self.result.setMinimumHeight(56)
        self.result.setMaximumHeight(500)
        # (min_floor, max_cap) — actual height hugs content via _fit_result_height
        self._result_height_default = (56, 360)
        self._result_height_triplex = (56, 500)
        self._result_height_neury = (56, 500)
        self.result.setMaximumWidth(720)
        self.result.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.result.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.result.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.result.setStyleSheet(
            "QTextEdit { color:#e8f4ff; background:rgba(8,14,28,150);"
            " border:1px solid rgba(120,190,220,80); border-radius:12px;"
            " padding:12px; font-size:14px; }"
            "QScrollBar:vertical { background:rgba(0,0,0,60); width:10px; margin:4px; border-radius:4px; }"
            "QScrollBar::handle:vertical { background:rgba(120,190,220,140); border-radius:4px; min-height:24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }"
        )
        self.result.hide()
        res_row = QHBoxLayout()
        res_row.addStretch(1)
        res_row.addWidget(self.result, 1)
        res_row.addStretch(1)
        self._res_row = res_row
        lay.addLayout(res_row)

        # Compact numbered project steps (DONE / WORKING / PENDING) with
        # Yes/No sitting on the step that needs consent.
        self._steps_board_host = QWidget(self._ui)
        self._steps_board_host.setStyleSheet('background:transparent;')
        self._steps_board_lay = QVBoxLayout(self._steps_board_host)
        self._steps_board_lay.setContentsMargins(10, 6, 10, 6)
        self._steps_board_lay.setSpacing(0)
        self._steps_board_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._steps_board_scroll = QScrollArea()
        self._steps_board_scroll.setWidgetResizable(True)
        self._steps_board_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._steps_board_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._steps_board_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._steps_board_scroll.setMinimumHeight(72)
        self._steps_board_scroll.setMaximumHeight(500)
        # Wide enough for step title + peer name pills without clipping.
        self._steps_board_scroll.setMaximumWidth(920)
        self._steps_board_scroll.setStyleSheet(
            'QScrollArea { background:rgba(8,14,28,150); border:1px solid rgba(120,190,220,80);'
            ' border-radius:12px; }'
            'QScrollBar:vertical { background:rgba(0,0,0,60); width:10px; margin:4px; border-radius:4px; }'
            'QScrollBar::handle:vertical { background:rgba(120,190,220,140); border-radius:4px; min-height:24px; }'
            'QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }'
        )
        self._steps_board_scroll.setWidget(self._steps_board_host)
        self._steps_board_scroll.hide()
        steps_row = QHBoxLayout()
        steps_row.addStretch(1)
        steps_row.addWidget(self._steps_board_scroll, 1)
        steps_row.addStretch(1)
        lay.addLayout(steps_row)

        self._web_cards_host = QWidget(self._ui)
        self._web_cards_host.setMaximumWidth(720)
        self._web_cards_lay = QVBoxLayout(self._web_cards_host)
        self._web_cards_lay.setContentsMargins(0, 0, 0, 0)
        self._web_cards_lay.setSpacing(8)
        self._web_cards_scroll = QScrollArea()
        self._web_cards_scroll.setWidgetResizable(True)
        self._web_cards_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._web_cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._web_cards_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._web_cards_scroll.setMaximumHeight(280)
        self._web_cards_max_default = 280
        self._web_cards_max_triplex = 420
        self._web_cards_scroll.setMaximumWidth(740)
        self._web_cards_scroll.setStyleSheet(
            'QScrollArea { background: transparent; border: none; }'
            'QScrollBar:vertical { background:rgba(0,0,0,60); width:10px; margin:4px; border-radius:4px; }'
            'QScrollBar::handle:vertical { background:rgba(120,190,220,140); border-radius:4px; min-height:24px; }'
            'QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }'
        )
        self._web_cards_scroll.setWidget(self._web_cards_host)
        self._web_cards_scroll.hide()
        cards_row = QHBoxLayout()
        cards_row.addStretch(1)
        cards_row.addWidget(self._web_cards_scroll, 1)
        cards_row.addStretch(1)
        lay.addLayout(cards_row)

        self.trust = QLabel('')
        self.trust.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.trust.setWordWrap(True)
        self.trust.setStyleSheet(
            'color:rgba(180,210,180,200); font-size:12px; background:rgba(0,20,10,90);'
            ' border:1px solid rgba(80,140,100,80); border-radius:10px; padding:8px 12px;')
        self.trust.hide()
        lay.addWidget(self.trust)

        self.drop_zone = QLabel(_t('No files yet'))
        self.drop_zone.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_zone.setWordWrap(True)
        self.drop_zone.setMinimumHeight(56)
        self.drop_zone.setMaximumWidth(560)
        self.drop_zone.setStyleSheet(
            "QLabel { color:#cfe8ff; background:rgba(8,20,36,140);"
            " border:1px dashed rgba(120,200,230,140); border-radius:14px;"
            " padding:12px; font-size:13px; }"
        )
        self.drop_zone.hide()
        drop_row = QHBoxLayout()
        drop_row.addStretch(1)
        drop_row.addWidget(self.drop_zone, 1)
        drop_row.addStretch(1)
        lay.addLayout(drop_row)

        self.btn_save = QPushButton(_t('Save results…'))
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.setMinimumHeight(40)
        self.btn_save.setStyleSheet(
            "QPushButton { color:#041018; background:#9fe0a8; border:none;"
            " border-radius:20px; padding:8px 18px; font-size:13px; font-weight:700; }"
            "QPushButton:hover { background:#b8f0be; }"
        )
        self.btn_save.clicked.connect(self._save_produced)
        self.btn_save.hide()

        self._showcase_wrap = QWidget(self._ui)
        self._showcase_wrap.setStyleSheet('background:transparent;')
        self._showcase_lay = QHBoxLayout(self._showcase_wrap)
        self._showcase_lay.setContentsMargins(0, 4, 0, 4)
        self._showcase_lay.setSpacing(8)
        self._showcase_wrap.hide()
        showcase_row = QHBoxLayout()
        showcase_row.addStretch(1)
        showcase_row.addWidget(self._showcase_wrap)
        showcase_row.addStretch(1)
        lay.addLayout(showcase_row)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.btn_yes = QPushButton(_t('Yes'))
        self.btn_yes.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_yes.setMinimumHeight(40)
        self.btn_yes.setStyleSheet(
            "QPushButton { color:#041018; background:#9fe0a8; border:none;"
            " border-radius:20px; padding:8px 22px; font-size:13px; font-weight:700; }"
            "QPushButton:hover { background:#b8f0be; }"
        )
        self.btn_yes.clicked.connect(lambda: self._resolve_consent(True))
        self.btn_yes.hide()
        self.btn_no = QPushButton(_t('No'))
        self.btn_no.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_no.setMinimumHeight(40)
        self.btn_no.setStyleSheet(
            "QPushButton { color:#cfe8ff; background:rgba(0,0,0,100);"
            " border:1px solid rgba(160,200,230,120); border-radius:20px;"
            " padding:8px 22px; font-size:13px; font-weight:600; }"
            "QPushButton:hover { background:rgba(20,40,70,180); }"
        )
        self.btn_no.clicked.connect(lambda: self._resolve_consent(False))
        self.btn_no.hide()
        actions.addWidget(self.btn_yes)
        actions.addWidget(self.btn_no)
        self.btn_again = QPushButton(_t('Try another'))
        self.btn_again.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_again.setMinimumHeight(40)
        self.btn_again.setStyleSheet(
            "QPushButton { color:#cfe8ff; background:rgba(0,0,0,100);"
            " border:1px solid rgba(160,200,230,120); border-radius:20px;"
            " padding:8px 18px; font-size:13px; font-weight:600; }"
            "QPushButton:hover { background:rgba(20,40,70,180); }"
        )
        self.btn_again.clicked.connect(self._back_to_chooser)
        self.btn_again.hide()
        self.btn_abort = QPushButton(_t('Stop'))
        self.btn_abort.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_abort.setMinimumHeight(40)
        self.btn_abort.setToolTip(_t('Stop and return home'))
        self.btn_abort.setStyleSheet(
            "QPushButton { color:#041018; background:#ff8a8a; border:none;"
            " border-radius:20px; padding:8px 22px; font-size:13px; font-weight:700; }"
            "QPushButton:hover { background:#ffb0b0; }"
        )
        self.btn_abort.clicked.connect(self._abort_to_home)
        self.btn_abort.hide()
        actions.addWidget(self.btn_again)
        actions.addWidget(self.btn_abort)
        actions.addWidget(self.btn_save)
        actions.addStretch(1)
        lay.addLayout(actions)

        self._mail_pick_wrap = QWidget(self._ui)
        self._mail_pick_wrap.setStyleSheet('background:transparent;')
        self._mail_pick_lay = QHBoxLayout(self._mail_pick_wrap)
        self._mail_pick_lay.setContentsMargins(0, 4, 0, 4)
        self._mail_pick_lay.setSpacing(8)
        self._mail_pick_wrap.hide()
        lay.addWidget(self._mail_pick_wrap)

        lay.addStretch(2)

        self._rebuild_capability_ui(force=True)
        self._rebuild_suggestions(force=True)
        self._sync_add_file_button()

        # Content veil starts at 0. Do not touch windowOpacity here when
        # top-level: setWindowOpacity creates the HWND immediately, and at
        # this point flags are still the default framed Window at a tiny
        # unset size — that flashes a ~30px bordered speck before
        # _make_conductor_toplevel + full-screen geometry run.
        self._veil_bg = 0.0
        self._veil_ui = 0.0
        self._veil = 0.0
        try:
            self._ui_fade.setOpacity(0.0)
        except Exception:
            pass
        if parent is not None:
            try:
                self.setWindowOpacity(1.0)
            except Exception:
                pass
        # Readable board tooltips (Qt defaults clip the last line on Windows).
        try:
            tip_css = (
                ' QToolTip { color:#e8f4ff; background-color:#0b1624;'
                ' border:1px solid #7ad7ef; padding:10px 12px 14px 12px;'
                ' border-radius:8px; font-size:12px; min-width:160px; }'
            )
            self.setStyleSheet((self.styleSheet() or '') + tip_css)
            app = QApplication.instance()
            if app is not None:
                app.setStyleSheet((app.styleSheet() or '') + tip_css)
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._bg.setGeometry(self.rect())
        self._ui.setGeometry(self.rect())
        try:
            if getattr(self, 'input', None) is not None and self.input.isVisible():
                QTimer.singleShot(0, self._fit_input_height)
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_geometry_locked', False):
            _apply_cover_geometry(self, self._host)
            self._geometry_locked = True
        try:
            self._bg.update()
        except Exception:
            pass
        # Defer rebuilds so fade-in is not starved on the UI thread.
        QTimer.singleShot(0, self._on_shown_deferred)

    def _focus_input(self):
        """Focus the prompt only after the top-level HWND is mapped safely."""
        if getattr(self, '_suppress_focus', False):
            return
        try:
            if not self.isVisible():
                return
        except Exception:
            return
        try:
            self.input.setFocus()
        except Exception:
            pass

    def _input_text(self):
        try:
            if hasattr(self.input, 'toPlainText'):
                return self.input.toPlainText() or ''
            return self.input.text() or ''
        except Exception:
            return ''

    def _set_input_text(self, text):
        try:
            if hasattr(self.input, 'setPlainText'):
                self.input.setPlainText(str(text or ''))
            else:
                self.input.setText(str(text or ''))
        except Exception:
            pass
        self._fit_input_height()

    def _on_input_text_changed(self, *args):
        self._on_suggest_pause()
        self._keep_input_centered()
        self._fit_input_height()
        # Second pass after Qt finishes laying out the new blocks.
        QTimer.singleShot(0, self._fit_input_height)

    def _keep_input_centered(self, *args):
        """Keep block alignment centered so the caret stays mid-field."""
        inp = getattr(self, 'input', None)
        if inp is None or not hasattr(inp, 'textCursor'):
            return
        try:
            cursor = inp.textCursor()
            fmt = cursor.blockFormat()
            if int(fmt.alignment()) != int(Qt.AlignmentFlag.AlignHCenter):
                fmt.setAlignment(Qt.AlignmentFlag.AlignHCenter)
                cursor.mergeBlockFormat(fmt)
                inp.setTextCursor(cursor)
        except Exception:
            pass

    def _fit_input_height(self):
        """Grow the prompt box with multiline text (capped at 500px)."""
        inp = getattr(self, 'input', None)
        if inp is None or not hasattr(inp, 'document'):
            return
        try:
            doc = inp.document()
            # Viewport can be 0 during early layout; fall back to widget / max width.
            vw = int(inp.viewport().width() or 0)
            if vw < 40:
                vw = int(inp.width() or 0) - 8
            if vw < 40:
                try:
                    vw = int(inp.maximumWidth() or 640)
                except Exception:
                    vw = 640
            vw = max(40, min(vw, 640))
            doc.setTextWidth(float(vw))
            try:
                layout_h = float(doc.documentLayout().documentSize().height())
            except Exception:
                layout_h = float(doc.size().height())
            # Stylesheet padding (10px top+bottom) is not in contentsMargins.
            pad = 20
            margins = inp.contentsMargins()
            frame = int(inp.frameWidth()) * 2
            need = int(layout_h + 0.99) + margins.top() + margins.bottom() + frame + pad + 8
            # Floor by wrapped-ish line count so short multiline pastes still grow.
            try:
                fm = inp.fontMetrics()
                line_h = max(18, int(fm.lineSpacing()))
                blocks = max(1, int(doc.blockCount()))
                plain = inp.toPlainText() or ''
                # Extra wraps: rough chars-per-line from width.
                cpl = max(20, vw // max(7, int(fm.horizontalAdvance('W') or 8)))
                wrap_lines = 0
                for line in plain.split('\n') or ['']:
                    wrap_lines += max(1, (len(line) + cpl - 1) // cpl)
                need = max(need, 24 + line_h * max(blocks, wrap_lines))
            except Exception:
                pass
            lo = 48
            hi = 500
            h = max(lo, min(need, hi))
            if int(inp.height()) != h:
                inp.setFixedHeight(h)
            if need > hi:
                inp.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            else:
                inp.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        except Exception:
            pass

    def _conductor_input_key(self, event):
        """Enter sends; Shift+Enter inserts a newline."""
        try:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    return QTextEdit.keyPressEvent(self.input, event)
                if getattr(self, '_awaiting_consent', False):
                    self._submit_consent_text()
                    return
                if getattr(self, '_neury_reply_mode', False) and self._phase == 'done':
                    self._neury_followup_from_input()
                    return
                self._run_from_input()
                return
        except Exception:
            pass
        return QTextEdit.keyPressEvent(self.input, event)

    def _on_progress_line(self, line):
        # Project jobs use the step board — never put progress under Neury
        # (IT packs once mapped a status line to a lone "X" there).
        if getattr(self, '_project_board_mode', False):
            try:
                self.lbl_sub.hide()
                self.lbl_sub.setText('')
            except Exception:
                pass
            return
        self._append_job_log(str(line or ''))

    def _append_job_log(self, line):
        text = str(line or '').rstrip()
        if not text:
            return
        try:
            cur = self.result.toPlainText() or ''
            if cur:
                self.result.setPlainText(cur + '\n' + text)
            else:
                self.result.setPlainText(text)
            self.result.show()
            cursor = self.result.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.result.setTextCursor(cursor)
            self._fit_result_height()
        except Exception:
            pass

    def _clear_steps_board(self):
        lay = getattr(self, '_steps_board_lay', None)
        if lay is None:
            return
        # Stop pulse before ripping widgets out — avoids paint on dying labels.
        try:
            t = getattr(self, '_board_wow_timer', None)
            if t is not None and t.isActive():
                t.stop()
        except Exception:
            pass
        # Freeze paints so the board does not flash blank between clear and rebuild.
        host = getattr(self, '_steps_board_host', None)
        try:
            if host is not None:
                host.setUpdatesEnabled(False)
        except Exception:
            pass
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                try:
                    w.hide()
                    w.setParent(None)
                except Exception:
                    pass
                w.deleteLater()
        # Do NOT processEvents here — that painted an empty board (blank flash).
        try:
            # Release any prior height clamp or new rows stack/overlap in a tiny box.
            if host is not None:
                host.setMinimumHeight(0)
                host.setMaximumHeight(16777215)
        except Exception:
            pass
        self._board_live_widgets = None
        self._board_structure_sig = None

    def _hide_project_board(self):
        self._project_board_mode = False
        self._project_board_id = ''
        self._project_article_review = None
        self._board_live_widgets = None
        self._board_structure_sig = None
        self._clear_status_proj_cache()
        try:
            self._clear_steps_board()
            self._steps_board_scroll.hide()
        except Exception:
            pass

    def _show_project_planning_shell(self, project_id=''):
        """Keep the step panel visible while Neury plans — never wipe a live step board."""
        live = getattr(self, '_board_live_widgets', None) or {}
        if live.get('steps'):
            return
        self._project_board_mode = True
        if project_id:
            self._project_board_id = str(project_id)
        try:
            self.result.hide()
        except Exception:
            pass
        try:
            # Never put status under the Neury title (IT "X" bug).
            self.lbl_sub.hide()
            self.lbl_sub.setText('')
        except Exception:
            pass
        self._clear_steps_board()
        self._board_live_widgets = None
        self._board_structure_sig = None
        try:
            row = QLabel('Preparing steps…')
            row.setWordWrap(True)
            row.setStyleSheet(
                'color:#9fe9ff; font-size:13px; font-weight:600; background:transparent;'
                ' padding:10px 6px;'
            )
            self._steps_board_lay.addWidget(row)
            self._steps_board_scroll.show()
            QTimer.singleShot(0, self._fit_steps_board_height)
        except Exception:
            pass

    def _sync_project_board_from_disk(self):
        """UI-thread recovery: load active project steps and paint (escapes planning shell)."""
        if not getattr(self, '_project_board_mode', False):
            return False
        try:
            from Support.NeuronOrchestrator import project_status
            want = str(getattr(self, '_project_board_id', '') or '')
            st = project_status(want)
            if not (st or {}).get('ok'):
                return False
            summary = (st or {}).get('summary') or {}
            status = str(summary.get('status') or '')
            # Without a known id, only adopt an in-flight project (not an old completed one).
            if not want and status not in ('active', 'awaiting_confirm'):
                return False
            steps = list((st or {}).get('steps') or [])
            if not steps:
                return False
            pid = str(summary.get('id') or '') or want
            if pid:
                self._project_board_id = pid
            consent = None
            if getattr(self, '_awaiting_consent', False):
                consent = dict(getattr(self, '_pending_consent', None) or {}) or None
            if not consent:
                consent = self._consent_from_awaiting_steps(steps)
                if consent:
                    self._pending_consent = dict(consent)
                    self._awaiting_consent = True
            # While Yes/No / draft editor is up — do NOT rebuild (flash + kills editing).
            if consent or getattr(self, '_awaiting_consent', False):
                return True
            sig = self._project_board_signature(steps, None)
            live = getattr(self, '_board_live_widgets', None)
            if (
                live
                and live.get('steps')
                and sig == getattr(self, '_board_structure_sig', None)
                and self._steps_board_scroll.isVisible()
            ):
                self._refresh_project_board_live(steps)
            else:
                self._paint_project_steps(steps, None)
            # Keep subtitle clear during the board — IT pack once mapped the mesh
            # status line to a lone "X" under Neury.
            try:
                self.lbl_sub.hide()
                self.lbl_sub.setText('')
            except Exception:
                pass
            return True
        except Exception:
            return False

    @staticmethod
    def _step_status_mark(step):
        st = str((step or {}).get('status') or 'pending')
        tool = str((step or {}).get('tool') or '')
        result = str((step or {}).get('result') or '')
        if st == 'done' and tool in ('websites.publish', 'article.publish'):
            return 'PUBLISHED'
        if st == 'done' and 'without DNS' in result:
            return 'SAVED'
        if st == 'skipped' and tool == 'article.publish':
            return 'DRAFT KEPT'
        if st == 'skipped' and 'draft kept' in result.lower():
            return 'DRAFT KEPT'
        if st == 'skipped':
            return 'SKIPPED'
        return {
            'done': 'DONE',
            'running': 'WORKING',
            'pending': 'PENDING',
            'awaiting': 'WAITING',
            'failed': 'FAILED',
            'blocked': 'BLOCKED',
            'skipped': 'SKIPPED',
        }.get(st, st.upper())

    @staticmethod
    def _short_consent_question(title, message, step=None):
        tool = str((step or {}).get('tool') or '')
        label = str((step or {}).get('label') or '')
        args_msg = str(((step or {}).get('args') or {}).get('message') or '').strip()
        if tool == 'article.publish':
            return _t('Publish this article?')
        if tool == 'user.confirm' and args_msg:
            first = args_msg.split('\n')[0].strip()
            return first[:90] + ('…' if len(first) > 90 else '')
        if tool in ('websites.publish', 'article.publish') or 'publish' in label.lower():
            return _t('Publish it?')
        if tool == 'website.save':
            return _t('Save and register DNS?')
        body = str(message or '').strip()
        first = body.split('\n')[0].strip() if body else ''
        if first and len(first) <= 72 and 'Neury is waiting' not in first:
            return first
        if title and str(title).strip() and str(title).strip() != 'Continue this project?':
            return str(title).strip()[:72]
        return _t('Allow this step?')

    @staticmethod
    def _consent_target_index(steps, consent):
        steps = list(steps or [])
        for i, s in enumerate(steps):
            if str(s.get('status') or '') == 'awaiting':
                return i
        for i, s in enumerate(steps):
            if str(s.get('status') or '') == 'running':
                return i
        msg = str((consent or {}).get('message') or '')
        title = str((consent or {}).get('title') or '')
        blob = (msg + ' ' + title).lower()
        for i, s in enumerate(steps):
            tool = str(s.get('tool') or '')
            if str(s.get('status') or '') == 'pending' and tool and tool.lower() in blob:
                return i
        for i, s in enumerate(steps):
            if str(s.get('status') or '') == 'pending':
                return i
        return -1

    @staticmethod
    def _consent_from_awaiting_steps(steps):
        """Build consent chrome from an awaiting user.confirm step (keeps Yes/No alive)."""
        for step in list(steps or []):
            if str(step.get('status') or '') != 'awaiting':
                continue
            args = step.get('args') if isinstance(step.get('args'), dict) else {}
            consent = {
                'title': str(step.get('label') or 'Continue this project?'),
                'message': str(
                    args.get('message') or step.get('label') or 'Allow this step?'
                ),
            }
            draft = str(args.get('draft_text') or '').strip()
            if draft:
                consent['draft_text'] = draft
            if str(args.get('draft_field') or '').strip():
                consent['draft_field'] = str(args.get('draft_field')).strip()
            if str(args.get('apply_to_step') or '').strip():
                consent['apply_to_step'] = str(args.get('apply_to_step')).strip()
            pk = str(args.get('preview_kind') or '').strip()
            if pk:
                consent['preview_kind'] = pk
            wn = str(args.get('website_name') or '').strip()
            if wn and not wn.startswith('$'):
                consent['website_name'] = wn
            return consent
        return None

    @staticmethod
    def _project_board_signature(steps, consent=None):
        """
        Fingerprint for when a full rebuild is required.
        Rebuild when bar chrome appears, a step awaits Yes/No, or DONE needs Preview.
        Percent and peer names update in place via live refresh.
        """
        bits = []
        for s in steps or []:
            st = str(s.get('status') or 'pending')
            # Distinct chrome keys so live refresh cannot skip Yes/No or Preview.
            if st == 'awaiting':
                bar = 'A'
            elif st == 'done':
                bar = 'D'
            elif st == 'running':
                bar = 'B'
            else:
                bar = 'N'
            bits.append('%s:%s' % (s.get('id') or '', bar))
        if consent:
            bits.append('c:%s' % ((consent.get('message') or '')[:40],))
            bits.append('pk:%s' % str((consent or {}).get('preview_kind') or ''))
        return '|'.join(bits)

    def _on_project_board(self, payload):
        data = payload if isinstance(payload, dict) else {}
        # Ignore paints from a cancelled / superseded Neury run.
        try:
            rid = data.get('run_id')
            if rid is not None and int(rid) != int(getattr(self, '_neury_run_id', 0) or 0):
                return
        except Exception:
            pass
        if self._neury_cancel and not data.get('consent'):
            return
        pid = str(data.get('project_id') or self._project_board_id or '')
        self._project_board_mode = True
        if pid:
            self._project_board_id = pid
        consent = data.get('consent')
        # Never wipe Yes/No while a confirm is open — status ticks were clearing buttons.
        if getattr(self, '_awaiting_consent', False):
            # Preserve in-progress draft edits across any late board emits.
            try:
                edit = getattr(self, '_consent_draft_edit', None)
                if edit is not None:
                    pending = dict(getattr(self, '_pending_consent', None) or {})
                    pending['draft_text'] = str(edit.toPlainText() or '')
                    self._pending_consent = pending
            except Exception:
                pass
            if not consent:
                consent = dict(getattr(self, '_pending_consent', None) or {}) or None
            # Freeze the board while Yes/No / draft editor is up — rebuilds
            # kill focus (feels "not editable") and flash the results list.
            if (
                self._steps_board_scroll.isVisible()
                and getattr(self, '_board_live_widgets', None)
            ):
                return
        # Prefer disk; fall back to boot payload so the board is never blank for seconds.
        steps = None
        raw_boot = data.get('steps')
        try:
            from Support.NeuronOrchestrator import project_status
            st = project_status(pid)
            summary = (st or {}).get('summary') or {}
            # Never resurrect a cancelled project's board over the live one.
            if str(summary.get('status') or '') == 'cancelled':
                if isinstance(raw_boot, list) and raw_boot:
                    steps = list(raw_boot)
                else:
                    return
            elif (st or {}).get('ok'):
                loaded = list((st or {}).get('steps') or [])
                if loaded:
                    steps = loaded
                sid = str(summary.get('id') or '')
                if sid:
                    self._project_board_id = sid
                    pid = sid
        except Exception:
            steps = None
        if not steps and isinstance(raw_boot, list) and raw_boot:
            steps = list(raw_boot)
        if steps is None:
            steps = []
        # If a step is WAITING, always carry consent chrome (Yes/No / Preview / Tweak).
        if steps and not consent:
            consent = self._consent_from_awaiting_steps(steps)
            if consent:
                self._pending_consent = dict(consent)
                self._awaiting_consent = True
        review = data.get('article_review')
        if isinstance(review, dict) and (review.get('title') or review.get('preview_text')):
            self._project_article_review = review
        if not getattr(self, '_project_article_review', None):
            try:
                from Support.NeuronOrchestrator import project_status
                st = project_status(pid)
                proj = (st or {}).get('project') or {}
                ctx = proj.get('context') or {}
                if ctx.get('article_id') or ctx.get('article_title'):
                    self._project_article_review = {
                        'title': str(ctx.get('article_title') or ''),
                        'subtitle': str(ctx.get('article_subtitle') or ''),
                        'preview_text': str(ctx.get('article_preview') or ''),
                        'article_id': str(ctx.get('article_id') or ''),
                        'draft_kept': bool(ctx.get('article_draft_kept')),
                    }
            except Exception:
                pass
        try:
            self.result.hide()
        except Exception:
            pass
        if not steps:
            # Prefer a planning shell over a hollow framed box while steps catch up.
            self._show_project_planning_shell(pid)
            return
        try:
            # Never show a subtitle under Neury during the board (IT "X" bug).
            self.lbl_sub.hide()
            self.lbl_sub.setText('')
        except Exception:
            pass
        # Hide the centered Go/prompt chrome under Neury — Stop is enough.
        self._hide_prompt_chrome()
        try:
            self.btn_abort.show()
        except Exception:
            pass
        sig = self._project_board_signature(steps, consent)
        live = getattr(self, '_board_live_widgets', None)
        if (
            live
            and live.get('steps')
            and sig == getattr(self, '_board_structure_sig', None)
            and not consent
            and not getattr(self, '_awaiting_consent', False)
            and self._steps_board_scroll.isVisible()
        ):
            self._refresh_project_board_live(steps)
            return
        self._paint_project_steps(steps, consent)

    def _paint_project_steps(self, steps, consent=None):
        steps = list(steps or [])
        if not steps:
            if not self._sync_project_board_from_disk():
                self._show_project_planning_shell(getattr(self, '_project_board_id', '') or '')
            return
        if not consent:
            consent = self._consent_from_awaiting_steps(steps)
        # Serialize paints — overlapping rebuilds freeze ADMIN and garble labels.
        if getattr(self, '_board_paint_busy', False):
            prev = getattr(self, '_board_paint_pending', None)
            # Never let a non-consent paint overwrite a queued Yes/No paint.
            if (
                prev is not None
                and prev[1]
                and not consent
                and getattr(self, '_awaiting_consent', False)
            ):
                self._board_paint_pending = (steps, prev[1])
            else:
                self._board_paint_pending = (steps, consent)
            return
        self._board_paint_busy = True
        try:
            self._paint_project_steps_body(steps, consent)
        except Exception:
            # Never leave a cleared hollow panel — recover from disk once.
            try:
                if self._sync_project_board_from_disk():
                    return
                self._show_project_planning_shell(getattr(self, '_project_board_id', '') or '')
            except Exception:
                pass
        finally:
            self._board_paint_busy = False
            pending = getattr(self, '_board_paint_pending', None)
            self._board_paint_pending = None
            if pending is not None:
                try:
                    QTimer.singleShot(
                        0,
                        lambda s=pending[0], c=pending[1]: self._paint_project_steps(s, c),
                    )
                except Exception:
                    pass

    def _paint_project_steps_body(self, steps, consent=None):
        self._clear_steps_board()
        self._board_live_widgets = {
            'overall_bar': None,
            'overall_lbl': None,
            'mesh_lbl': None,
            'steps': {},
        }
        steps = list(steps or [])
        self._board_structure_sig = self._project_board_signature(steps, consent)
        host = getattr(self, '_steps_board_host', None)
        consent_i = self._consent_target_index(steps, consent) if consent else -1
        question = ''
        if consent and consent_i >= 0:
            question = self._short_consent_question(
                consent.get('title'), consent.get('message'), steps[consent_i],
            )
        elif consent:
            question = self._short_consent_question(
                consent.get('title'), consent.get('message'), None,
            )

        overall_pct, finished_n, total_n = self._project_overall_progress(steps)
        running_n = sum(1 for s in steps if str(s.get('status') or '') == 'running')
        mesh_n = self._project_mesh_peer_total(steps)
        peer_active = mesh_n > 0

        # Overall progress header (thin bar + percentage + honest mesh count).
        if total_n > 0:
            head = QWidget(self._steps_board_host)
            head.setStyleSheet('background:transparent;')
            head.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            hv = QVBoxLayout(head)
            hv.setContentsMargins(4, 2, 4, 6)
            hv.setSpacing(4)
            top = QHBoxLayout()
            top.setContentsMargins(0, 0, 8, 0)
            top.setSpacing(8)
            pct_lbl = QLabel(_tf('Progress %s/%s · %s%%', finished_n, total_n, overall_pct))
            pct_lbl.setStyleSheet(
                'color:#9fe9ff; font-size:12px; font-weight:700; background:transparent;'
            )
            pct_lbl.setWordWrap(False)
            top.addWidget(pct_lbl, 0)
            top.addStretch(1)
            mesh_lbl = QLabel('')
            mesh_lbl.setStyleSheet(
                'color:#9fe0a8; font-size:11px; font-weight:700; background:transparent;'
            )
            mesh_lbl.setWordWrap(False)
            if peer_active:
                mesh_lbl.setText('Mesh · %s jobs · %s Neurys' % (running_n, mesh_n))
            elif running_n > 1:
                mesh_lbl.setText('%s running' % running_n)
            elif running_n == 1:
                mesh_lbl.setText('Working…')
            top.addWidget(mesh_lbl, 0)
            hv.addLayout(top)
            overall_bar = self._thin_progress_bar(head, overall_pct, '#7ad7ef', height=4)
            hv.addWidget(overall_bar)
            # Neon mesh ribbon — one thin colored track per live Neury.
            if peer_active or running_n > 1:
                try:
                    from Support.NeuronNeuryAskParallel import peer_colors
                    ribbon_n = max(2, min(8, mesh_n or running_n or 2))
                    strip = QWidget(head)
                    strip.setFixedHeight(12)
                    strip.setStyleSheet(
                        'background:rgba(0,0,0,80); border-radius:6px;'
                        ' border:1px solid rgba(159,224,168,80);'
                    )
                    sl = QHBoxLayout(strip)
                    sl.setContentsMargins(3, 3, 3, 3)
                    sl.setSpacing(2)
                    for col in peer_colors(ribbon_n):
                        sl.addWidget(
                            self._thin_progress_bar(
                                strip, max(22, overall_pct), col, height=4,
                            ),
                            1,
                        )
                    hv.addWidget(strip)
                    self._board_live_widgets['mesh_ribbon'] = strip
                except Exception:
                    pass
            self._steps_board_lay.addWidget(head)
            self._board_live_widgets['overall_bar'] = overall_bar
            self._board_live_widgets['overall_lbl'] = pct_lbl
            self._board_live_widgets['mesh_lbl'] = mesh_lbl

        review = getattr(self, '_project_article_review', None) or {}
        if review.get('title') or review.get('preview_text'):
            rev = QWidget(self._steps_board_host)
            rev.setStyleSheet('background:transparent;')
            rev.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            vl = QVBoxLayout(rev)
            vl.setContentsMargins(4, 2, 4, 4)
            vl.setSpacing(2)
            title = str(review.get('title') or '').strip() or _t('Untitled draft')
            try:
                from Support.NeuronNeuryAgent import strip_neury_mark
                title = strip_neury_mark(title) or title
            except Exception:
                pass
            head = QLabel(_tf('Article draft: %s', title))
            head.setWordWrap(True)
            head.setStyleSheet(
                'color:#9fe9ff; font-size:14px; font-weight:700; background:transparent;'
            )
            vl.addWidget(head)
            sub = str(review.get('subtitle') or '').strip()
            if sub:
                sub_lbl = QLabel(sub)
                sub_lbl.setWordWrap(True)
                sub_lbl.setStyleSheet(
                    'color:rgba(200,220,240,180); font-size:12px; background:transparent;'
                )
                vl.addWidget(sub_lbl)
            preview = str(review.get('preview_text') or '').strip()
            if preview:
                body = QLabel(preview[:420] + ('…' if len(preview) > 420 else ''))
                body.setWordWrap(True)
                body.setStyleSheet(
                    'color:#e8f4ff; font-size:12px; background:transparent;'
                )
                vl.addWidget(body)
            note = QLabel(
                _t('Draft is in My Articles. Yes publishes; No keeps it there only.')
            )
            note.setWordWrap(True)
            note.setStyleSheet(
                'color:#ffe08a; font-size:12px; font-weight:600; background:transparent;'
            )
            vl.addWidget(note)
            self._steps_board_lay.addWidget(rev)

        for i, step in enumerate(steps):
            block = QWidget(self._steps_board_host)
            block.setStyleSheet('background:transparent;')
            block.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            bv = QVBoxLayout(block)
            bv.setContentsMargins(4, 0, 4, 0)
            bv.setSpacing(1)
            row = QWidget(block)
            row.setStyleSheet('background:transparent;')
            row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 1, 0, 1)
            hl.setSpacing(8)
            label = str(step.get('label') or step.get('tool') or 'Step').strip() or 'Step'
            mark = self._step_status_mark(step)
            # Peer finished (100%) but settle lagged — show DONE, not WORKING 100%.
            if mark == 'WORKING':
                try:
                    if int(step.get('progress') or 0) >= 100:
                        mark = 'DONE'
                except Exception:
                    pass
            if consent and i == consent_i:
                text = '%s. %s … %s' % (i + 1, label, question or _t('Allow this step?'))
                color = '#ffe08a'
            else:
                text = '%s. %s … %s' % (i + 1, label, mark)
                if mark == 'WORKING':
                    try:
                        sp = int(step.get('progress') or 0)
                    except Exception:
                        sp = 0
                    if sp > 0:
                        text = '%s. %s … %s %s%%' % (i + 1, label, mark, sp)
                if mark in ('BLOCKED', 'FAILED'):
                    err = str(step.get('error') or '').strip()
                    if err:
                        short = err.split('\n')[0].strip()
                        if len(short) > 72:
                            short = short[:69] + '…'
                        text = '%s. %s … %s — %s' % (i + 1, label, mark, short)
                color = {
                    'DONE': '#9fe0a8',
                    'PUBLISHED': '#9fe0a8',
                    'SAVED': '#9fe0a8',
                    'DRAFT KEPT': '#9fe9ff',
                    'WORKING': '#9fe9ff',
                    'WAITING': '#ffe08a',
                    'PENDING': 'rgba(200,220,240,160)',
                    'SKIPPED': 'rgba(180,190,200,140)',
                    'FAILED': '#ff8a8a',
                    'BLOCKED': '#ff8a8a',
                }.get(mark, '#e8f4ff')
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            self._attach_board_tip(lbl, self._board_step_tooltip(step, mark, text))
            lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            lbl.setMinimumWidth(160)
            lbl.setStyleSheet(
                'color:%s; font-size:12px; font-weight:600; background:transparent;'
                ' padding:0; margin:0;' % color
            )
            hl.addWidget(lbl, 1)
            # Peer identity lives ONLY on the bar-row name pill. Never create
            # parentless QLabel badges here — show() turns them into yellow
            # top-level "python" windows (MESH / Rob1 / LIVE).
            peer_badge = None
            live_chip = None
            pc = int(step.get('peer_count') or 0)
            ask_names = [
                str(n).strip() for n in (step.get('peer_names') or []) if str(n).strip()
            ]
            show_name = (ask_names[0][:18] if ask_names else '')
            if consent and i == consent_i:
                if str((consent or {}).get('preview_kind') or '') == 'website':
                    btn_prev = QPushButton(_t('Preview'))
                    btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
                    btn_prev.setMinimumHeight(26)
                    btn_prev.setStyleSheet(
                        "QPushButton { color:#041018; background:#9fe9ff; border:none;"
                        " border-radius:13px; padding:2px 12px; font-size:11px; font-weight:700; }"
                        "QPushButton:hover { background:#c5f2ff; }"
                    )
                    btn_prev.clicked.connect(lambda _=False: self._preview_consent_website())
                    hl.addWidget(btn_prev)
                    btn_tweak = QPushButton(_t('Tweak in Builder'))
                    btn_tweak.setCursor(Qt.CursorShape.PointingHandCursor)
                    btn_tweak.setMinimumHeight(26)
                    btn_tweak.setStyleSheet(
                        "QPushButton { color:#041018; background:#ffe08a; border:none;"
                        " border-radius:13px; padding:2px 12px; font-size:11px; font-weight:700; }"
                        "QPushButton:hover { background:#fff0b0; }"
                    )
                    btn_tweak.clicked.connect(lambda _=False: self._open_website_builder_for_consent())
                    hl.addWidget(btn_tweak)
                btn_yes = QPushButton(_t('Yes'))
                btn_yes.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_yes.setMinimumHeight(26)
                btn_yes.setStyleSheet(
                    "QPushButton { color:#041018; background:#9fe0a8; border:none;"
                    " border-radius:13px; padding:2px 12px; font-size:11px; font-weight:700; }"
                    "QPushButton:hover { background:#b8f0be; }"
                )
                btn_yes.clicked.connect(lambda _=False: self._resolve_consent(True))
                btn_no = QPushButton(_t('No'))
                btn_no.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_no.setMinimumHeight(26)
                btn_no.setStyleSheet(
                    "QPushButton { color:#cfe8ff; background:rgba(0,0,0,100);"
                    " border:1px solid rgba(160,200,230,120); border-radius:13px;"
                    " padding:2px 12px; font-size:11px; font-weight:600; }"
                    "QPushButton:hover { background:rgba(20,40,70,180); }"
                )
                btn_no.clicked.connect(lambda _=False: self._resolve_consent(False))
                hl.addWidget(btn_yes)
                hl.addWidget(btn_no)
                self._consent_chrome_painted = True
            bv.addWidget(row)
            # Editable draft for diary/chat/etc. — tweak before Yes.
            if consent and i == consent_i:
                draft = str((consent or {}).get('draft_text') or '').strip()
                if draft:
                    edit = QTextEdit(block)
                    edit.setObjectName('consentDraftEdit')
                    edit.setReadOnly(False)
                    edit.setAcceptRichText(False)
                    edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                    edit.setUndoRedoEnabled(True)
                    edit.setTextInteractionFlags(
                        Qt.TextInteractionFlag.TextEditorInteraction
                    )
                    edit.setPlainText(draft)
                    edit.setMinimumHeight(96)
                    edit.setMaximumHeight(180)
                    edit.setStyleSheet(
                        'QTextEdit#consentDraftEdit { color:#e8f4ff;'
                        ' background:rgba(8,14,28,240);'
                        ' border:2px solid rgba(158,233,255,200); border-radius:8px;'
                        ' padding:8px; font-size:13px;'
                        ' selection-background-color:#1a6a80; }'
                        'QTextEdit#consentDraftEdit:focus {'
                        ' border:2px solid #9fe9ff; }'
                    )
                    bv.addWidget(edit)
                    self._consent_draft_edit = edit
                    tip = QLabel('Click the box, edit the text, then Yes to post — or No to skip.')
                    tip.setWordWrap(True)
                    tip.setStyleSheet(
                        'color:#ffe08a; font-size:11px; font-weight:600; background:transparent;'
                    )
                    bv.addWidget(tip)
                    self._consent_chrome_painted = True
                    QTimer.singleShot(0, edit.setFocus)
                    QTimer.singleShot(80, edit.setFocus)
            bars_host = QWidget(block)
            bars_host.setStyleSheet('background:transparent;')
            bars_lay = QVBoxLayout(bars_host)
            # Right pad so the chunk never looks clipped by the frame edge.
            bars_lay.setContentsMargins(2, 2, 14, 4)
            bars_lay.setSpacing(3)
            live_bars = []
            live_names = []
            live_tags = []
            live_done_icons = []

            def _addpeers_pixmap():
                base = getattr(init, 'PATH_SYSTEM_SUPPORT_IMAGES', '') or ''
                for name in ('AddPeers.png', 'AddPeer.png'):
                    try:
                        pix = QPixmap(base + name)
                        if not pix.isNull():
                            return pix.scaled(
                                22, 22,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation,
                            )
                    except Exception:
                        continue
                return QPixmap()

            def _add_named_bar(who, value, col, done=False, tag='', preview_btn=None):
                # No inner chrome — the outer job block carries the only border.
                track = QWidget(bars_host)
                track.setStyleSheet('background:transparent; border:none;')
                th = QHBoxLayout(track)
                th.setContentsMargins(2, 2, 2, 2)
                th.setSpacing(8)
                nm = QLabel(track)
                nm.setText(str(who or _t('Neury'))[:18])
                nm.setMinimumWidth(88)
                nm.setMaximumWidth(140)
                nm.setAlignment(Qt.AlignmentFlag.AlignCenter)
                nm.setStyleSheet(
                    'color:#041018; background:%s; border:none; border-radius:8px;'
                    ' padding:2px 8px; font-size:10px; font-weight:800;' % col
                )
                th.addWidget(nm, 0)
                # LIVE/MESH chip — always parented to the track (never a top-level window).
                chip = QLabel(track)
                chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
                chip.setMinimumWidth(36)
                if (not done) and tag == 'LIVE':
                    chip.setText('LIVE')
                    chip.setStyleSheet(
                        'color:#041018; background:#ff6b8a; border:none; border-radius:7px;'
                        ' padding:1px 6px; font-size:9px; font-weight:900;'
                    )
                    chip.show()
                elif (not done) and tag == 'MESH':
                    chip.setText('MESH')
                    chip.setStyleSheet(
                        'color:#041018; background:#ffe08a; border:none; border-radius:7px;'
                        ' padding:1px 6px; font-size:9px; font-weight:900;'
                    )
                    chip.show()
                else:
                    chip.setText('')
                    chip.hide()
                th.addWidget(chip, 0)
                # PREVIEW FILE sits between peer name and the checkmark (saves a row).
                if preview_btn is not None:
                    th.addWidget(preview_btn, 0)
                # Done: hide the bar, show AddPeer.png. Working: live % bar.
                bar = self._thin_progress_bar(
                    track, max(0, min(100, int(value or 0))), col, height=3,
                )
                done_icon = QLabel(track)
                done_icon.setFixedSize(24, 24)
                done_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
                done_icon.setStyleSheet('background:transparent; border:none;')
                if done:
                    bar.hide()
                    pix = _addpeers_pixmap()
                    if not pix.isNull():
                        done_icon.setPixmap(pix)
                    done_icon.show()
                else:
                    done_icon.hide()
                    bar.show()
                th.addWidget(bar, 1)
                th.addWidget(done_icon, 0)
                bars_lay.addWidget(track)
                live_bars.append(bar)
                live_names.append(nm)
                live_tags.append(chip)
                live_done_icons.append(done_icon)

            if mark in ('WORKING', 'DONE'):
                # One soft frame around the whole job (title + peer row), not nested boxes.
                block.setStyleSheet(
                    'background:rgba(10,22,40,70); border-radius:12px;'
                    ' border:1px solid rgba(122,215,239,50);'
                )
            if mark == 'WORKING':
                try:
                    sp = int(step.get('progress') or 0)
                except Exception:
                    sp = 0
                sp = max(0, min(100, sp))
                remote = bool(step.get('peer_help') and (pc > 0 or ask_names))
                names = [str(n).strip() for n in (step.get('peer_names') or []) if str(n).strip()]
                asking = bool(names) and not remote
                if remote or asking:
                    try:
                        from Support.NeuronNeuryAskParallel import peer_colors
                        colors = list(step.get('peer_colors') or []) or peer_colors(
                            max(pc, len(names) or 1)
                        )
                    except Exception:
                        colors = ['#9fe0a8', '#ffe08a', '#ff9f6b', '#c9a0ff', '#f472b6', '#38bdf8']
                    track_n = max(1, min(6, (pc if remote else len(names)) or 1))
                    while len(names) < track_n:
                        names.append(_t('Neury'))
                    tag = 'LIVE' if remote else 'MESH'
                    for ci in range(track_n):
                        _add_named_bar(
                            names[ci], sp, colors[ci % len(colors)], tag=tag,
                        )
                else:
                    _add_named_bar(_t('This PC'), sp, '#38bdf8')
            elif mark == 'DONE':
                # Peer name + PREVIEW FILE + checkmark on one row — no LIVE, no progress bar.
                names = [str(n).strip() for n in (step.get('peer_names') or []) if str(n).strip()]
                preview_paths = self._step_preview_paths(step)
                btn_prev = None
                # Show for media/hive DONE steps even before local paths resolve
                # (peer uploads) — click re-resolves; never for confirm/diary/remind.
                if self._step_wants_preview(step):
                    btn_prev = QPushButton(_t('PREVIEW FILE'))
                    btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
                    btn_prev.setMinimumHeight(24)
                    btn_prev.setToolTip(
                        _t('Open the finished file(s) for this step')
                    )
                    btn_prev.setStyleSheet(
                        "QPushButton { color:#041018; background:#9fe9ff; border:none;"
                        " border-radius:12px; padding:2px 12px; font-size:11px; font-weight:700; }"
                        "QPushButton:hover { background:#c5f2ff; }"
                    )
                    btn_prev.clicked.connect(
                        lambda _=False, paths=list(preview_paths),
                        tool=str(step.get('tool') or ''),
                        sid=str(step.get('id') or ''),
                        kind=self._step_preview_kind(step):
                        self._preview_step_outputs(
                            paths, tool, step_id=sid, kind=kind,
                        )
                    )
                if step.get('peer_help') and (names or pc > 0):
                    try:
                        from Support.NeuronNeuryAskParallel import peer_colors
                        colors = list(step.get('peer_colors') or []) or peer_colors(
                            max(1, len(names) or pc or 1)
                        )
                    except Exception:
                        colors = ['#7ad7ef']
                    who = names[0] if names else _t('Mesh')
                    _add_named_bar(
                        who, 100, colors[0], done=True, preview_btn=btn_prev,
                    )
                else:
                    _add_named_bar(
                        _t('This PC'), 100, '#7ad7ef', done=True, preview_btn=btn_prev,
                    )
            bv.addWidget(bars_host)
            self._steps_board_lay.addWidget(block)
            sid = str(step.get('id') or ('s%s' % (i + 1)))
            self._board_live_widgets['steps'][sid] = {
                'label': lbl,
                'badge': peer_badge,
                'live_chip': live_chip,
                'bars': live_bars,
                'names': live_names,
                'tags': live_tags,
                'done_icons': live_done_icons,
                'index': i + 1,
                'name': label,
                'block': block,
            }

        if consent and consent_i < 0 and question:
            # Fallback row when we could not match a step.
            row = QWidget(self._steps_board_host)
            row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            hl = QHBoxLayout(row)
            hl.setContentsMargins(4, 1, 4, 1)
            lbl = QLabel(question)
            lbl.setStyleSheet(
                'color:#ffe08a; font-size:13px; font-weight:600; background:transparent;'
            )
            hl.addWidget(lbl, 1)
            btn_yes = QPushButton(_t('Yes'))
            btn_yes.setMinimumHeight(28)
            btn_yes.setStyleSheet(
                "QPushButton { color:#041018; background:#9fe0a8; border:none;"
                " border-radius:14px; padding:2px 14px; font-size:12px; font-weight:700; }"
            )
            btn_yes.clicked.connect(lambda _=False: self._resolve_consent(True))
            btn_no = QPushButton(_t('No'))
            btn_no.setMinimumHeight(28)
            btn_no.setStyleSheet(
                "QPushButton { color:#cfe8ff; background:rgba(0,0,0,100);"
                " border:1px solid rgba(160,200,230,120); border-radius:14px;"
                " padding:2px 14px; font-size:12px; font-weight:600; }"
            )
            btn_no.clicked.connect(lambda _=False: self._resolve_consent(False))
            hl.addWidget(btn_yes)
            hl.addWidget(btn_no)
            self._steps_board_lay.addWidget(row)

        self._steps_board_scroll.show()
        # Bottom Yes/No stay hidden — answers live on the step row.
        try:
            self.btn_yes.hide()
            self.btn_no.hide()
        except Exception:
            pass
        try:
            if self._phase == 'running':
                self.btn_abort.show()
        except Exception:
            pass
        try:
            if host is not None:
                host.setUpdatesEnabled(True)
                host.update()
        except Exception:
            pass
        # Fit after widgets exist; a second pass after layout settles.
        QTimer.singleShot(0, self._fit_steps_board_height)
        QTimer.singleShot(50, self._fit_steps_board_height)
        self._ensure_board_wow_timer()

    def _refresh_project_board_live(self, steps):
        """Update % / badges / bars in place — never rebuild (avoids snap glitch)."""
        if getattr(self, '_awaiting_consent', False):
            return
        live = getattr(self, '_board_live_widgets', None) or {}
        steps = list(steps or [])
        overall_pct, finished_n, total_n = self._project_overall_progress(steps)
        running_n = sum(1 for s in steps if str(s.get('status') or '') == 'running')
        mesh_n = self._project_mesh_peer_total(steps)
        host = getattr(self, '_steps_board_host', None)
        try:
            if host is not None:
                host.setUpdatesEnabled(False)
        except Exception:
            pass
        try:
            try:
                ol = live.get('overall_lbl')
                if ol is not None:
                    nxt = _tf('Progress %s/%s · %s%%', finished_n, total_n, overall_pct)
                    if ol.text() != nxt:
                        ol.setText(nxt)
                ob = live.get('overall_bar')
                if ob is not None:
                    ob.setValue(max(0, min(100, overall_pct)))
                ml = live.get('mesh_lbl')
                if ml is not None:
                    if mesh_n > 0:
                        nxt = 'Mesh · %s jobs · %s Neurys' % (running_n, mesh_n)
                        if ml.text() != nxt:
                            ml.setText(nxt)
                        ml.show()
                    elif running_n > 1:
                        nxt = '%s running' % running_n
                        if ml.text() != nxt:
                            ml.setText(nxt)
                        ml.show()
                    elif running_n == 1:
                        if ml.text() != 'Working…':
                            ml.setText('Working…')
                        ml.show()
                    else:
                        if ml.text():
                            ml.setText('')
            except Exception:
                pass
            by_id = {}
            for i, s in enumerate(steps):
                by_id[str(s.get('id') or ('s%s' % (i + 1)))] = s
            for sid, widgets in (live.get('steps') or {}).items():
                step = by_id.get(sid)
                if not step or not widgets:
                    continue
                mark = self._step_status_mark(step)
                label = str(widgets.get('name') or step.get('label') or 'Step')
                idx = int(widgets.get('index') or 1)
                try:
                    sp = int(step.get('progress') or 0)
                except Exception:
                    sp = 0
                sp = max(0, min(100, sp))
                if mark == 'WORKING' and sp >= 100:
                    mark = 'DONE'
                text = '%s. %s … %s' % (idx, label, mark)
                if mark == 'WORKING' and sp > 0:
                    text = '%s. %s … %s %s%%' % (idx, label, mark, sp)
                if mark in ('BLOCKED', 'FAILED'):
                    err = str(step.get('error') or '').strip()
                    if err:
                        short = err.split('\n')[0].strip()
                        if len(short) > 72:
                            short = short[:69] + '…'
                        text = '%s. %s … %s — %s' % (idx, label, mark, short)
                color = {
                    'DONE': '#9fe0a8', 'WORKING': '#9fe9ff', 'PENDING': 'rgba(200,220,240,160)',
                    'FAILED': '#ff8a8a', 'BLOCKED': '#ff8a8a', 'WAITING': '#ffe08a',
                    'SKIPPED': 'rgba(180,190,200,140)',
                }.get(mark, '#e8f4ff')
                try:
                    lbl = widgets.get('label')
                    if lbl is not None:
                        if lbl.text() != text:
                            lbl.setText(text)
                        lbl.setStyleSheet(
                            'color:%s; font-size:12px; font-weight:600; background:transparent;'
                            ' padding:0; margin:0;' % color
                        )
                    pc = int(step.get('peer_count') or 0)
                    remote = bool(step.get('peer_help') and (pc > 0 or step.get('peer_names')))
                    ask_names = [
                        str(n).strip() for n in (step.get('peer_names') or []) if str(n).strip()
                    ]
                    # Name pills on the bar row are the only peer tags — no floating chips.
                    # Exact same number as the "WORKING N%" label — never invent %.
                    if mark == 'DONE':
                        val = 100
                    elif mark == 'PENDING':
                        val = 0
                    else:
                        val = sp
                    for bar in (widgets.get('bars') or []):
                        if bar is None:
                            continue
                        bar.setValue(val)
                        if mark == 'DONE':
                            bar.hide()
                        else:
                            bar.show()
                    for icon in (widgets.get('done_icons') or []):
                        if icon is None:
                            continue
                        if mark == 'DONE':
                            try:
                                base = getattr(init, 'PATH_SYSTEM_SUPPORT_IMAGES', '') or ''
                                pix = QPixmap()
                                for name in ('AddPeers.png', 'AddPeer.png'):
                                    pix = QPixmap(base + name)
                                    if not pix.isNull():
                                        break
                                if not pix.isNull():
                                    icon.setPixmap(pix.scaled(
                                        22, 22,
                                        Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation,
                                    ))
                                icon.show()
                            except Exception:
                                icon.hide()
                        else:
                            icon.hide()
                    # Keep name pills + LIVE/MESH chips in sync without rebuilding the board.
                    name_widgets = list(widgets.get('names') or [])
                    tag_widgets = list(widgets.get('tags') or [])
                    if name_widgets:
                        who = 'This PC'
                        col = '#38bdf8'
                        tag = ''
                        if mark == 'DONE':
                            if step.get('peer_help') and ask_names:
                                who = ask_names[0][:18]
                                col = '#7ad7ef'
                            else:
                                who = 'This PC'
                                col = '#7ad7ef'
                            tag = ''  # never LIVE after done
                        elif mark == 'WORKING':
                            if remote and ask_names:
                                who = ask_names[0][:18]
                                col = '#9fe0a8'
                                tag = 'LIVE'
                            elif ask_names and not remote:
                                who = ask_names[0][:18]
                                col = '#ffe08a'
                                tag = 'MESH'
                            else:
                                who = 'This PC'
                                col = '#38bdf8'
                        nm0 = name_widgets[0]
                        if nm0 is not None:
                            nm0.setText(who)
                            nm0.setStyleSheet(
                                'color:#041018; background:%s; border-radius:7px;'
                                ' padding:1px 7px; font-size:10px; font-weight:800;' % col
                            )
                        if tag_widgets:
                            chip0 = tag_widgets[0]
                            if chip0 is not None:
                                if tag == 'LIVE':
                                    chip0.setText('LIVE')
                                    chip0.setStyleSheet(
                                        'color:#041018; background:#ff6b8a; border-radius:7px;'
                                        ' padding:1px 6px; font-size:9px; font-weight:900;'
                                    )
                                    chip0.show()
                                elif tag == 'MESH':
                                    chip0.setText('MESH')
                                    chip0.setStyleSheet(
                                        'color:#041018; background:#ffe08a; border-radius:7px;'
                                        ' padding:1px 6px; font-size:9px; font-weight:900;'
                                    )
                                    chip0.show()
                                else:
                                    chip0.setText('')
                                    chip0.hide()
                except Exception:
                    pass
        finally:
            try:
                if host is not None:
                    host.setUpdatesEnabled(True)
            except Exception:
                pass
        self._ensure_board_wow_timer()

    def _ensure_board_wow_timer(self):
        """No fake bar motion — progress must match real step % only."""
        try:
            t = getattr(self, '_board_wow_timer', None)
            if t is not None and t.isActive():
                t.stop()
        except Exception:
            pass

    def _tick_board_wow(self):
        # Intentionally empty: crawling bars fought live % and looped forward/back.
        return

    def _tick_elapsed(self):
        if self._phase != 'running' or self._fading_out:
            return
        try:
            elapsed = max(0, int(time.time() - float(self._job_t0 or 0)))
            mins, secs = divmod(elapsed, 60)
            self.lbl_stage.setText(_tf('Elapsed %s:%02d', mins, secs))
            self.lbl_stage.show()
        except Exception:
            pass
        if getattr(self, '_project_board_mode', False):
            try:
                self.btn_abort.show()
            except Exception:
                pass
            # Never refresh while Yes/No / draft editor is up (flash + kills edit).
            if getattr(self, '_awaiting_consent', False):
                return
            now = time.time()
            last = float(getattr(self, '_board_refresh_t0', 0) or 0)
            if now - last < 0.85:
                return
            self._board_refresh_t0 = now
            # Empty project_id still resolves via active-project pointer — escapes planning shell.
            # Also recovers Yes/No when a step is WAITING without chrome.
            self._sync_project_board_from_disk()
            self._ensure_board_wow_timer()

    def _start_elapsed_timer(self):
        self._job_t0 = time.time()
        self._tick_elapsed()
        try:
            if self._elapsed_timer is None:
                self._elapsed_timer = QTimer(self)
                self._elapsed_timer.timeout.connect(self._tick_elapsed)
            self._elapsed_timer.start(1000)
        except Exception:
            pass

    def _clear_status_proj_cache(self):
        self._status_proj_cache = None
        self._board_status_save_t0 = 0.0

    def _stop_elapsed_timer(self):
        try:
            if self._elapsed_timer is not None:
                self._elapsed_timer.stop()
        except Exception:
            pass

    def _on_consent_needed(self, title, message):
        """Show Yes/No — on the project step row when a job board is active."""
        self._awaiting_consent = True
        if getattr(self, '_project_board_mode', False) and self._project_board_id:
            # Already painted Yes/No / draft once — never rebuild (kills editing).
            if getattr(self, '_consent_chrome_painted', False):
                return
            consent = dict(getattr(self, '_pending_consent', None) or {})
            consent['title'] = str(title or consent.get('title') or 'Neury needs permission')
            consent['message'] = str(message or consent.get('message') or 'Allow this step?')
            self._pending_consent = dict(consent)
            try:
                from Support.NeuronOrchestrator import project_status
                st = project_status(self._project_board_id)
                self._paint_project_steps(
                    (st or {}).get('steps') or [],
                    consent,
                )
            except Exception:
                self._paint_project_steps([], consent)
            self._consent_chrome_painted = True
            self._hide_prompt_chrome()
            try:
                self.btn_yes.hide()
                self.btn_no.hide()
                self.btn_abort.show()
            except Exception:
                pass
            return
        self._append_job_log('')
        self._append_job_log('— %s —' % (str(title or 'Neury needs your answer').strip() or 'Neury needs your answer'))
        body = str(message or '').strip()
        if body:
            self._append_job_log(body)
        self._append_job_log(_t('Press Yes / No, or type an answer and press Enter.'))
        try:
            self.btn_yes.show()
            self.btn_no.show()
        except Exception:
            pass
        try:
            self._restore_prompt_chrome()
            self.input.show()
            self._set_input_text('')
            self.input.setPlaceholderText(_t('Type Yes, No, or your answer…'))
            self.lbl_prompt.setText(_t('Your answer'))
            self.lbl_prompt.show()
            self.btn_run.setEnabled(True)
            self.btn_run.show()
            self._set_run_label(_t('Send'))
            try:
                self.btn_run.clicked.disconnect()
            except Exception:
                pass
            self.btn_run.clicked.connect(self._submit_consent_text)
            self._focus_input()
        except Exception:
            pass

    def _submit_consent_text(self):
        if not getattr(self, '_awaiting_consent', False):
            return
        raw = (self._input_text() or '').strip()
        if not raw:
            return
        self._resolve_consent(raw)

    def _consent_website_name(self):
        consent = dict(getattr(self, '_pending_consent', None) or {})
        name = str(consent.get('website_name') or '').strip()
        if name and not name.startswith('$'):
            return name
        if not getattr(self, '_project_board_id', ''):
            return ''
        try:
            from Support.NeuronOrchestrator import project_status
            st = project_status(self._project_board_id) or {}
            proj = st.get('project') or {}
            ctx = proj.get('context') if isinstance(proj.get('context'), dict) else {}
            name = str(ctx.get('website_name') or ctx.get('name') or '').strip()
            if name and not name.startswith('$'):
                return name
            sid = str(consent.get('apply_to_step') or '').strip()
            for step in (st.get('steps') or proj.get('steps') or []):
                if sid and str(step.get('id') or '') != sid:
                    continue
                args = step.get('args') if isinstance(step.get('args'), dict) else {}
                cand = str(args.get('name') or args.get('website') or '').strip()
                if cand and not cand.startswith('$'):
                    return cand
        except Exception:
            pass
        return ''

    def _preview_consent_website(self):
        """Open the draft website in a local browser before Yes/No publish."""
        name = self._consent_website_name()
        if not name:
            try:
                self.trust.setText(_t('No website draft found to preview yet.'))
                self.trust.show()
            except Exception:
                pass
            return
        try:
            from pathlib import Path
            from Support.WebsitesUtils import WebsiteManager
            works = Path(getattr(init, 'PATH_WEBSITES_WORKS', '') or '') / name
            pub = Path(getattr(init, 'PATH_WEBSITES_PUBLISHED', '') or '') / name
            path = works if works.is_dir() else pub
            ok, msg = WebsiteManager.preview_website(path)
            if not ok:
                self.trust.setText(_t('Preview failed: %s') % (msg or 'unknown'))
                self.trust.show()
            else:
                self.trust.setText(
                    _t('Preview opened — Tweak in Builder to edit, Yes publishes, No keeps the draft.')
                )
                self.trust.show()
        except Exception as exc:
            try:
                self.trust.setText(_t('Preview failed: %s') % exc)
                self.trust.show()
            except Exception:
                pass

    def _open_website_builder_for_consent(self):
        """Open Website Builder on the Works draft so the user can tweak before publish."""
        name = self._consent_website_name()
        if not name:
            try:
                self.trust.setText(_t('No website draft found to edit yet.'))
                self.trust.show()
            except Exception:
                pass
            return
        try:
            from pathlib import Path
            from Support.ProfessionalChannel import open_website_in_manager
            works = Path(getattr(init, 'PATH_WEBSITES_WORKS', '') or '') / name
            if not works.is_dir():
                # Case-insensitive match under Works.
                root = Path(getattr(init, 'PATH_WEBSITES_WORKS', '') or '')
                low = name.lower()
                for child in root.iterdir() if root.is_dir() else []:
                    if child.is_dir() and child.name.lower() == low:
                        works = child
                        break
            if not works.is_dir():
                self.trust.setText(_t('Draft folder missing: %s') % name)
                self.trust.show()
                return
            # Queue edit request + launch (or nudge) Website Builder — no Menu early-return.
            ok = bool(open_website_in_manager(str(works)))
            if ok:
                self.trust.setText(
                    _t('Website Builder opening your Works draft — tweak it, then Yes to publish or No to keep it.')
                )
            else:
                self.trust.setText(_t('Could not open Website Builder for %s') % name)
            self.trust.show()
        except Exception as exc:
            try:
                self.trust.setText(_t('Could not open Website Builder: %s') % exc)
                self.trust.show()
            except Exception:
                pass

    @staticmethod
    def _board_step_tooltip(step, mark, line_text):
        """Plain multi-line tip text for the custom board tip popup."""
        label = str((step or {}).get('label') or (step or {}).get('tool') or 'Step').strip()
        err = str((step or {}).get('error') or '').strip()
        activity = str((step or {}).get('activity') or '').strip()
        tool = str((step or {}).get('tool') or '').strip()
        peers = [
            str(n).strip()
            for n in ((step or {}).get('peer_names') or [])
            if str(n).strip()
        ]
        lines = [label, str(mark or '')]
        if tool and tool not in label:
            lines.append(tool)
        if activity and activity != label:
            lines.append(activity)
        if peers:
            lines.append('Neurys: %s' % ', '.join(peers[:6]))
        if err:
            lines.append(err[:240])
        return '\n'.join(lines)

    def _attach_board_tip(self, widget, text):
        """Custom tip popup — Windows QToolTip clips long/wrapped board text."""
        tip = str(text or '').strip()
        if not tip or widget is None:
            return
        try:
            widget.setToolTip('')  # disable native tip (clips on Windows)
            widget.setProperty('boardTip', tip)
            widget.installEventFilter(self)
        except Exception:
            pass

    def _show_board_tip(self, widget):
        tip = ''
        try:
            tip = str(widget.property('boardTip') or getattr(widget, '_board_tip_text', '') or '')
        except Exception:
            tip = str(getattr(widget, '_board_tip_text', '') or '')
        if not tip:
            return
        pop = getattr(self, '_board_tip_pop', None)
        if pop is None:
            pop = QLabel(None, Qt.WindowType.ToolTip)
            pop.setWordWrap(True)
            pop.setTextFormat(Qt.TextFormat.PlainText)
            pop.setStyleSheet(
                'QLabel { color:#e8f4ff; background-color:#0b1624;'
                ' border:1px solid #7ad7ef; border-radius:8px;'
                ' padding:10px 12px; font-size:12px; }'
            )
            self._board_tip_pop = pop
        pop.setText(tip)
        pop.setFixedWidth(320)
        pop.adjustSize()
        try:
            pos = widget.mapToGlobal(widget.rect().bottomLeft())
            pop.move(pos.x(), pos.y() + 6)
        except Exception:
            pass
        pop.show()
        pop.raise_()

    def _hide_board_tip(self):
        pop = getattr(self, '_board_tip_pop', None)
        if pop is not None:
            try:
                pop.hide()
            except Exception:
                pass

    def eventFilter(self, obj, event):
        try:
            from PySide6.QtCore import QEvent
            if obj is not None and obj.property('boardTip'):
                et = event.type()
                if et == QEvent.Type.Enter:
                    self._show_board_tip(obj)
                elif et in (QEvent.Type.Leave, QEvent.Type.Hide, QEvent.Type.Destroy):
                    self._hide_board_tip()
        except Exception:
            pass
        try:
            return super().eventFilter(obj, event)
        except Exception:
            return False

    @staticmethod
    def _step_preview_kind(step):
        """photo | video | ocr | audio | docs | none | any — drives Preview opens."""
        step = step if isinstance(step, dict) else {}
        args = step.get('args') if isinstance(step.get('args'), dict) else {}
        jid = str(args.get('job_id') or '').strip().lower()
        tool = str(step.get('tool') or '').strip().lower()
        label = str(step.get('label') or '').lower()
        blob = '%s %s %s' % (jid, label, tool)
        if tool in (
            'user.confirm', 'calendar.remind', 'chat.send', 'mail.send',
            'diary.post', 'websites.publish', 'article.publish',
            'files.classify_batch', 'files.commit_outputs',
            'website.build', 'websites.build',
        ):
            return 'none'
        if jid in ('photo_batch', 'enlarge_photos'):
            return 'photo'
        if jid == 'ocr':
            return 'ocr'
        if jid == 'voiceover':
            return 'audio'
        if jid == 'captions' or 'caption' in blob or 'subtitle' in blob or 'transcrib' in blob:
            return 'captions'
        if jid == 'video_convert' or (
            'convert' in blob and 'video' in blob
        ) or ('video' in blob and 'caption' not in blob and 'voice' not in blob):
            return 'video'
        if jid == 'translate':
            return 'docs'
        if 'receipt' in blob or 'ocr' in blob or 'scan' in blob:
            return 'ocr'
        if 'shrink' in blob or 'enlarge' in blob or (
            'photo' in blob and 'video' not in blob and 'caption' not in blob
        ):
            return 'photo'
        if 'voice' in blob or 'intro' in blob:
            return 'audio'
        if 'translate' in blob:
            return 'docs'
        return 'any'

    @staticmethod
    def _step_wants_preview(step):
        """True for DONE media / hive steps that actually produce a file."""
        tool = str((step or {}).get('tool') or '')
        # No file artifact — skip PREVIEW FILE entirely.
        if tool in (
            'user.confirm', 'calendar.remind', 'chat.send', 'mail.send',
            'diary.post', 'websites.publish', 'article.publish',
            'files.classify_batch', 'files.commit_outputs',
            'website.build', 'websites.build',
        ):
            return False
        if tool == 'hive.run_job' or tool.startswith('hive.'):
            return True
        label = str((step or {}).get('label') or '').lower()
        blob = tool + ' ' + label
        for key in (
            'video', 'photo', 'image', 'caption', 'voice', 'ocr', 'receipt',
            'shrink', 'convert', 'translate', 'intro',
        ):
            if key in blob:
                return True
        return False

    @staticmethod
    def _path_matches_kind(path, kind):
        if kind == 'none':
            return False
        ext = os.path.splitext(str(path or ''))[1].lower()
        photos = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tif', '.tiff')
        videos = ('.mp4', '.webm', '.mov', '.m4v', '.avi', '.mkv')
        audio = ('.wav', '.mp3', '.m4a', '.aac', '.ogg', '.flac')
        # OCR/translate: text only — never PDF/JSON path dumps.
        text_out = ('.txt', '.md', '.rtf')
        captions = ('.srt', '.vtt', '.txt', '.sbv')
        if kind == 'photo':
            return ext in photos
        if kind == 'video':
            return ext in videos
        if kind == 'audio':
            return ext in audio
        if kind == 'captions':
            # Prefer subtitle files; allow captioned video as fallback.
            return ext in captions or ext in videos
        if kind == 'ocr':
            return ext in text_out
        if kind == 'docs':
            return ext in text_out
        return ext in photos + videos + audio + text_out + captions

    @staticmethod
    def _usable_text_note(blob):
        """Reject path-dump JSON / status crumbs that are not real OCR/translate text."""
        text = str(blob or '').strip()
        if len(text) < 40:
            return False
        low = text.lower()
        if low in ('done', 'ok', 'completed', 'failed'):
            return False
        if text.startswith('{') or text.startswith('['):
            try:
                data = json.loads(text)
            except Exception:
                data = None
            if isinstance(data, dict) and (
                'files' in data or 'paths' in data or 'produced' in data
            ):
                return False
            if isinstance(data, list):
                return False
        if 'boarding_pass' in low and len(text) < 200:
            return False
        if text.count('\\') >= 2 and '.pdf' in low and len(text) < 300:
            return False
        return True

    def _step_input_paths(self, step):
        """Original inputs for this step — never open these as 'preview'."""
        step = step if isinstance(step, dict) else {}
        args = step.get('args') if isinstance(step.get('args'), dict) else {}
        out = set()
        for key in ('paths', 'files', 'path', 'source', 'attachments'):
            raw = args.get(key)
            if isinstance(raw, (list, tuple)):
                for p in raw:
                    if p and not str(p).startswith('$'):
                        out.add(os.path.normcase(os.path.abspath(str(p))) if os.path.exists(str(p)) else str(p))
            elif raw and not str(raw).startswith('$'):
                sp = str(raw)
                out.add(os.path.normcase(os.path.abspath(sp)) if os.path.exists(sp) else sp)
        # Also treat project dump attachments as inputs (boarding_pass.pdf etc.).
        try:
            from Support.NeuronOrchestrator import project_status
            st = project_status(getattr(self, '_project_board_id', '') or '') or {}
            ctx = ((st.get('project') or {}).get('context') or {})
            for key in (
                'attachments', 'paths', 'receipt_paths', 'document_paths',
                'receipts', 'documents',
            ):
                for p in list(ctx.get(key) or []):
                    if p and not str(p).startswith('$'):
                        sp = str(p)
                        out.add(os.path.normcase(os.path.abspath(sp)) if os.path.exists(sp) else sp)
        except Exception:
            pass
        return out

    def _step_preview_paths(self, step):
        """Files this step produced — never the original PDF/inputs."""
        step = step if isinstance(step, dict) else {}
        kind = self._step_preview_kind(step)
        if kind == 'none' or not self._step_wants_preview(step):
            return []
        inputs = self._step_input_paths(step)
        paths = []

        def _is_input(p):
            sp = str(p or '')
            if not sp:
                return True
            if sp in inputs or os.path.normcase(sp) in inputs:
                return True
            try:
                if os.path.exists(sp) and os.path.normcase(os.path.abspath(sp)) in inputs:
                    return True
            except Exception:
                pass
            base = os.path.basename(sp).lower()
            if base in ('boarding_pass.pdf',):
                return True
            for inp in inputs:
                if os.path.basename(str(inp)).lower() == base:
                    return True
            return False

        def _add(p):
            if not p:
                return
            sp = str(p)
            if sp in paths or _is_input(sp):
                return
            if not self._path_matches_kind(sp, kind):
                return
            if os.path.exists(sp) or step.get('peer_help'):
                paths.append(sp)

        for p in list(step.get('produced') or []) + list(step.get('files') or []):
            _add(p)
        out_dir = str(step.get('out_dir') or '').strip()
        if out_dir and os.path.isdir(out_dir):
            try:
                for name in os.listdir(out_dir)[:80]:
                    _add(os.path.join(out_dir, name))
            except Exception:
                pass
        # Materialize real OCR/translate text only — never path-dump JSON.
        if kind in ('ocr', 'docs') and not any(
            os.path.splitext(p)[1].lower() in ('.txt', '.md', '.rtf')
            for p in paths if os.path.exists(p)
        ):
            blob = ''
            for note in list(step.get('notes') or []):
                if self._usable_text_note(note):
                    blob = str(note).strip()
                    break
            if not blob and self._usable_text_note(step.get('result')):
                blob = str(step.get('result') or '').strip()
            if blob:
                try:
                    dest = out_dir or os.path.join(
                        getattr(init, 'MAIN_PATH', '') or '', 'System', 'conductor_out',
                    )
                    os.makedirs(dest, exist_ok=True)
                    jid = str(((step.get('args') or {}).get('job_id')) or kind or 'out')
                    out_txt = os.path.join(dest, '%s_%s.txt' % (jid, step.get('id') or 'step'))
                    with open(out_txt, 'w', encoding='utf-8') as f:
                        f.write(blob)
                    paths.insert(0, out_txt)
                    step['produced'] = list(step.get('produced') or []) + [out_txt]
                except Exception:
                    pass
        if not paths:
            try:
                from Support.NeuronOrchestrator import project_status
                st = project_status(getattr(self, '_project_board_id', '') or '') or {}
                ctx = ((st.get('project') or {}).get('context') or {})
                if kind == 'photo':
                    keys = ('photo_paths', 'photos')
                elif kind == 'video':
                    keys = ('video_paths', 'videos')
                elif kind == 'audio':
                    keys = ('audio_paths', 'audio')
                else:
                    keys = ()
                for key in keys:
                    for p in list(ctx.get(key) or []):
                        _add(p)
            except Exception:
                pass
        # Resolve basenames against this step's out_dir / default out — no global dump scan.
        search_dirs = []
        for folder in (
            out_dir,
            str(getattr(self, '_last_out_dir', '') or ''),
            str(getattr(cpower, '_default_out_dir', lambda: '')() or ''),
        ):
            if folder and os.path.isdir(folder) and folder not in search_dirs:
                search_dirs.append(folder)
        resolved = []
        for p in paths:
            if os.path.exists(p) and not _is_input(p) and self._path_matches_kind(p, kind):
                resolved.append(p)
                continue
            if os.path.exists(p):
                continue
            base = os.path.basename(p)
            for folder in search_dirs:
                cand = os.path.join(folder, base)
                if (
                    os.path.exists(cand)
                    and self._path_matches_kind(cand, kind)
                    and not _is_input(cand)
                ):
                    resolved.append(cand)
                    break
        # Peer uploads: only resolve listed basenames inside newest hive_results folders.
        if not resolved and step.get('peer_help'):
            try:
                hive_root = os.path.join(
                    getattr(init, 'MAIN_PATH', '') or '', 'System', 'hive_results',
                )
                if os.path.isdir(hive_root):
                    subs = []
                    for name in os.listdir(hive_root):
                        p = os.path.join(hive_root, name)
                        if os.path.isdir(p) and name != 'export_cache':
                            try:
                                subs.append((os.path.getmtime(p), p))
                            except Exception:
                                subs.append((0, p))
                    subs.sort(reverse=True)
                    bases = [
                        os.path.basename(str(x))
                        for x in list(step.get('produced') or []) + list(step.get('files') or [])
                        if x
                    ]
                    for _mt, folder in subs[:6]:
                        for base in bases:
                            cand = os.path.join(folder, base)
                            if (
                                os.path.isfile(cand)
                                and self._path_matches_kind(cand, kind)
                                and not _is_input(cand)
                            ):
                                resolved.append(cand)
                        if resolved:
                            break
            except Exception:
                pass
        if kind in ('ocr', 'docs'):
            textish = [
                p for p in resolved
                if os.path.splitext(p)[1].lower() in ('.txt', '.md', '.rtf')
            ]
            return textish[:24]
        if kind == 'captions':
            subs = [
                p for p in resolved
                if os.path.splitext(p)[1].lower() in ('.srt', '.vtt', '.sbv', '.txt')
            ]
            if subs:
                return subs[:24]
        return resolved[:24]

    def _preview_step_outputs(self, paths, tool='', step_id='', kind=''):
        """Open produced media for THIS step kind — never open a sibling step's video."""
        kind = str(kind or '').strip() or 'any'
        # Always re-resolve from the live step so PREVIEW opens this step's
        # outputs (not a stale PDF captured when the row was first painted).
        if step_id:
            try:
                from Support.NeuronOrchestrator import project_status
                st = project_status(getattr(self, '_project_board_id', '') or '') or {}
                for step in (st.get('steps') or []):
                    if str(step.get('id') or '') == str(step_id):
                        kind = self._step_preview_kind(step) or kind
                        fresh = list(self._step_preview_paths(step))
                        if fresh:
                            paths = fresh
                        break
            except Exception:
                pass
        paths = [
            p for p in (paths or [])
            if p and os.path.exists(p) and self._path_matches_kind(p, kind)
        ]
        if not paths:
            try:
                self.trust.setText(_t('No preview file for this step yet.'))
                self.trust.show()
            except Exception:
                pass
            return
        photos = []
        videos = []
        docs = []
        audio = []
        captions = []
        other = []
        for p in paths:
            ext = os.path.splitext(p)[1].lower()
            if ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tif', '.tiff'):
                photos.append(p)
            elif ext in ('.mp4', '.webm', '.mov', '.m4v', '.avi', '.mkv'):
                videos.append(p)
            elif ext in ('.wav', '.mp3', '.m4a', '.aac', '.ogg', '.flac'):
                audio.append(p)
            elif ext in ('.srt', '.vtt', '.sbv'):
                captions.append(p)
            elif ext in ('.txt', '.md', '.csv', '.json', '.pdf', '.docx', '.rtf'):
                docs.append(p)
            else:
                other.append(p)
        try:
            if kind == 'photo' or (kind == 'any' and photos and not videos):
                if len(photos) >= 2:
                    self._open_photo_gallery_html(photos)
                    return
                if photos:
                    os.startfile(photos[0])
                    return
            if kind == 'captions':
                if captions:
                    os.startfile(captions[0])
                    return
                if docs:
                    os.startfile(docs[0])
                    return
                if videos:
                    os.startfile(videos[0])
                    return
            if kind == 'ocr':
                if docs:
                    os.startfile(docs[0])
                    return
                if len(photos) >= 2:
                    self._open_photo_gallery_html(photos)
                    return
                if photos:
                    os.startfile(photos[0])
                    return
            if kind == 'video' and videos:
                os.startfile(videos[0])
                return
            if kind == 'audio' and audio:
                os.startfile(audio[0])
                return
            if kind == 'docs' and docs:
                os.startfile(docs[0])
                return
            if captions:
                os.startfile(captions[0])
                return
            # Generic fallback — still prefer kind-matched lists over raw videos.
            if docs:
                os.startfile(docs[0])
                return
            if photos:
                if len(photos) >= 2:
                    self._open_photo_gallery_html(photos)
                else:
                    os.startfile(photos[0])
                return
            if audio:
                os.startfile(audio[0])
                return
            if videos and kind in ('video', 'any'):
                os.startfile(videos[0])
                return
            if other:
                os.startfile(other[0])
                return
            folder = os.path.dirname(paths[0])
            if folder and os.path.isdir(folder):
                os.startfile(folder)
        except Exception as exc:
            try:
                self.trust.setText(_t('Preview failed: %s') % exc)
                self.trust.show()
            except Exception:
                pass

    def _open_photo_gallery_html(self, photos):
        """Write a tiny local gallery and open it in the default browser."""
        import tempfile
        import webbrowser
        from pathlib import Path
        photos = [p for p in (photos or []) if p and os.path.isfile(p)][:40]
        if not photos:
            return
        tiles = []
        for i, src in enumerate(photos):
            uri = Path(src).resolve().as_uri()
            tiles.append(
                '<a class="tile" href="%s" target="_blank">'
                '<img src="%s" alt="Photo %s"/></a>'
                % (uri, uri, i + 1)
            )
        html = (
            '<!DOCTYPE html><html><head><meta charset="utf-8"/>'
            '<title>Neuron preview</title>'
            '<style>'
            'body{margin:0;background:#0b1220;color:#e8f4ff;font-family:Segoe UI,sans-serif}'
            'h1{font-size:18px;padding:16px 20px;margin:0}'
            '.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));'
            'gap:10px;padding:12px 20px 28px}'
            '.tile{display:block;border-radius:10px;overflow:hidden;background:#132033}'
            'img{display:block;width:100%;height:160px;object-fit:cover}'
            '</style></head><body>'
            '<h1>Preview — %s photos</h1>'
            '<div class="grid">%s</div></body></html>'
        ) % (len(photos), ''.join(tiles))
        path = os.path.join(tempfile.gettempdir(), 'neuron_preview_gallery.html')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html)
        webbrowser.open(Path(path).resolve().as_uri())

    def _resolve_consent(self, answer):
        if not getattr(self, '_awaiting_consent', False):
            return
        self._awaiting_consent = False
        self._consent_chrome_painted = False
        # Capture edited draft before the board repaint destroys the editor.
        edited = ''
        try:
            edit = getattr(self, '_consent_draft_edit', None)
            if edit is not None:
                edited = str(edit.toPlainText() or '').strip()
        except Exception:
            edited = ''
        self._consent_edited_text = edited
        self._consent_draft_edit = None
        try:
            self.btn_yes.hide()
            self.btn_no.hide()
        except Exception:
            pass
        if getattr(self, '_project_board_mode', False) and self._project_board_id:
            # Refresh board without the Yes/No row; worker continues the job.
            try:
                from Support.NeuronOrchestrator import project_status
                st = project_status(self._project_board_id)
                self._paint_project_steps((st or {}).get('steps') or [], None)
            except Exception:
                pass
        else:
            shown = 'Yes' if answer is True else ('No' if answer is False else str(answer))
            self._append_job_log(_tf('You: %s', shown))
        try:
            self.input.hide()
            self.lbl_prompt.hide()
            self.btn_run.hide()
            self.btn_add_file.hide()
        except Exception:
            pass
        self._consent_answer = answer
        try:
            self._consent_ev.set()
        except Exception:
            pass

    def _apply_consent_draft_to_project(self, project_id, meta, edited_text):
        """Write the approved/edited text into the target step args."""
        pid = str(project_id or '')
        text = str(edited_text or '').strip()
        meta = meta if isinstance(meta, dict) else {}
        sid = str(meta.get('apply_to_step') or '').strip()
        field = str(meta.get('draft_field') or 'message').strip() or 'message'
        if not pid or not sid or not text:
            return
        try:
            from Support.NeuronOrchestrator import _load_project, save_project
            proj = _load_project(pid)
            if not proj:
                return
            for step in proj.get('steps') or []:
                if str(step.get('id') or '') != sid:
                    continue
                args = dict(step.get('args') or {})
                args[field] = text
                step['args'] = args
                break
            save_project(proj)
        except Exception:
            pass

    @staticmethod
    def _consent_to_bool(answer):
        if isinstance(answer, bool):
            return answer
        low = str(answer or '').strip().lower()
        if low in ('y', 'yes', 'ok', 'okay', 'proceed', 'allow', 'true', '1', 'continue'):
            return True
        if low in ('n', 'no', 'cancel', 'abort', 'deny', 'stop', 'false', '0'):
            return False
        return False

    def _on_shown_deferred(self):
        if self._fading_out or self._fading_in:
            # Wait until fade finishes — loading during fade hangs it.
            QTimer.singleShot(50, self._on_shown_deferred)
            return
        self._focus_input()
        if self._raise_timer is None:
            self._raise_timer = QTimer(self)
            self._raise_timer.timeout.connect(self._keep_above_desktop)
            self._raise_timer.start(400)
        elif not self._raise_timer.isActive():
            self._raise_timer.start(400)
        if self._mesh_timer is None:
            self._mesh_timer = QTimer(self)
            self._mesh_timer.timeout.connect(self._refresh_mesh_live)
            self._mesh_timer.start(_MESH_POLL_MS)
        elif not self._mesh_timer.isActive():
            self._mesh_timer.start(_MESH_POLL_MS)
        if self._suggest_timer is None:
            self._suggest_timer = QTimer(self)
            self._suggest_timer.timeout.connect(self._rotate_suggestions)
            self._suggest_timer.start(_SUGGEST_ROTATE_MS)
        elif not self._suggest_timer.isActive():
            self._suggest_timer.start(_SUGGEST_ROTATE_MS)
        self._push_icons_behind()
        self._rebuild_capability_ui()
        self._rebuild_suggestions()
        self._refresh_mesh_live()
        self._sync_add_file_button()


    def _hide_prompt_chrome(self):
        """Remove every widget between the Neury title and the elapsed timer."""
        for attr in (
            'lbl_welcome', 'lbl_mesh', 'lbl_mesh_pulse', 'lbl_sub', 'lbl_prompt',
            'btn_add_file', 'btn_run', 'input', '_suggest_wrap', '_chip_wrap',
            'btn_how',
        ):
            w = getattr(self, attr, None)
            if w is None:
                continue
            try:
                w.hide()
            except Exception:
                pass
        try:
            self.btn_run.setText('')
            self.btn_run.setIcon(QIcon())
            self.btn_run.setFixedSize(0, 0)
            self.btn_run.setMaximumSize(0, 0)
            self.btn_add_file.setFixedSize(0, 0)
            self.btn_add_file.setMaximumSize(0, 0)
        except Exception:
            pass
        # Char-countdown label is a child of the input — force-hide so it cannot
        # float as a tiny glyph under the Neury title when the input collapses.
        try:
            cd = getattr(self, '_prompt_countdown', None)
            lab = getattr(cd, 'label', None) if cd is not None else None
            if lab is not None:
                lab.hide()
                lab.setText('')
        except Exception:
            pass
        try:
            self.lbl_sub.setText('')
        except Exception:
            pass

    def _restore_prompt_chrome(self):
        """Bring Go/+ back after a job ends."""
        try:
            self.btn_run.setMaximumSize(16777215, 16777215)
            self.btn_run.setFixedSize(48, 48)
            self.btn_add_file.setMaximumSize(16777215, 16777215)
            self.btn_add_file.setFixedSize(48, 48)
        except Exception:
            pass

    def _set_run_label(self, text):
        """Go stays a circular Foreward icon; other labels use a compact pill."""
        label = str(text or '').strip()
        go = _t('Go')
        if label == '…':
            self._hide_prompt_chrome()
            return
        self._restore_prompt_chrome()
        if not label or label == go or label == 'Go':
            self.btn_run.setFixedSize(48, 48)
            self.btn_run.setText('')
            self.btn_run.setIcon(getattr(self, '_run_icon_go', None) or QIcon())
            self.btn_run.setIconSize(QSize(20, 20))
            self.btn_run.setToolTip(go)
            self.btn_run.setStyleSheet(
                "QPushButton { color:#041018; background:#7ad7ef; border:none;"
                " border-radius:24px; padding:0px; }"
                "QPushButton:hover { background:#9aebff; }"
                "QPushButton:disabled { background:#3a5560; }"
            )
            return
        self.btn_run.setIcon(QIcon())
        self.btn_run.setMinimumSize(140, 44)
        self.btn_run.setMaximumSize(16777215, 16777215)
        self.btn_run.setText(label)
        self.btn_run.setToolTip(label)
        self.btn_run.setStyleSheet(
            "QPushButton { color:#041018; background:#7ad7ef; border:none;"
            " border-radius:22px; font-size:15px; font-weight:800; padding:10px 28px; }"
            "QPushButton:hover { background:#9aebff; }"
            "QPushButton:disabled { background:#3a5560; color:#9ab; }"
        )

    def _active_chips(self):
        if self._menu_chips:
            return tuple(self._menu_chips)
        return self.CHIPS_FULL if self._ai_ready else self.CHIPS_BASIC

    def _home_chips(self):
        return self.CHIPS_FULL if self._ai_ready else self.CHIPS_BASIC

    def _enter_menu(self, mode, chips, title, prompt, show_input=False, placeholder=''):
        self._menu_mode = mode
        self._menu_chips = list(chips)
        self._phase = 'idle'
        self.lbl_q.setText(_t(title) if title else '')
        self.lbl_prompt.setText(_t(prompt) if prompt else '')
        self.lbl_prompt.show()
        self.lbl_sub.hide()
        self.lbl_stage.setText('')
        self.result.hide()
        self.trust.hide()
        self._clear_web_cards()
        self.btn_again.show()
        self.btn_save.hide()
        self._set_drop_visible(False)
        self._set_run_label(_t('Go'))
        self.btn_run.setEnabled(True)
        try:
            self.btn_run.clicked.disconnect()
        except Exception:
            pass
        self.btn_run.clicked.connect(self._run_from_input)
        if show_input:
            self.input.show()
            self.input.setPlaceholderText(_t(placeholder or 'Type here…'))
            self.input.clear()
        else:
            self.input.hide()
        self._chip_wrap.show()
        try:
            self._suggest_wrap.hide()
        except Exception:
            pass
        self._rebuild_capability_ui(force=True)
        if chips:
            self._select_chip(chips[0][0])

    def _open_work_files(self):
        jobs = cpower.list_hive_jobs(include_neury_gated=bool(self._ai_ready))
        chips = [('hive:' + j['id'], j['title']) for j in jobs]
        chips.append(('back_home', '← Back'))
        self._enter_menu(
            'hive',
            chips,
            'Work on files',
            'Pick a tool. Examples appear after you select one.',
            show_input=False,
        )

    def _open_connect(self):
        acts = cpower.neuron_actions(lane='connect', include_neury=bool(self._ai_ready))
        chips = [('act:' + a['id'], a['title']) for a in acts]
        chips.append(('back_home', '← Back'))
        self._enter_menu(
            'connect',
            chips,
            'Connect',
            'Invite, chat, diary, calendar — without opening the desktop.',
            show_input=False,
        )

    def _open_discover(self):
        acts = cpower.neuron_actions(lane='discover', include_neury=bool(self._ai_ready))
        chips = [('act:' + a['id'], a['title']) for a in acts]
        chips.append(('back_home', '← Back'))
        self._enter_menu(
            'discover',
            chips,
            'Discover',
            'Search, crawl, marketplace, pedia, retrieve.',
            show_input=False,
        )

    def _open_create(self):
        if not self._ai_ready:
            self._start_install_neury()
            return
        hive_gated = [
            ('hive:translate', 'Translate writing'),
            ('hive:pictures', 'Create a picture'),
        ]
        acts = cpower.neuron_actions(lane='create', include_neury=True)
        chips = hive_gated + [('act:' + a['id'], a['title']) for a in acts]
        chips.append(('back_home', '← Back'))
        self._enter_menu(
            'create',
            chips,
            'Create & publish',
            'Translate, generate images, publish articles, listings, or start a channel.',
            show_input=False,
        )

    def _clear_chip_layout(self):
        while self._chips_col.count():
            item = self._chips_col.takeAt(0)
            lay = item.layout()
            if lay is not None:
                while lay.count():
                    child = lay.takeAt(0)
                    w = child.widget()
                    if w is not None:
                        w.setParent(None)
                        w.deleteLater()
            else:
                w = item.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
        self._chip_btns = {}

    def _rebuild_capability_ui(self, force=False):
        ready = _neury_ready()
        if (
            not force
            and ready == getattr(self, '_ai_ready', None)
            and self._chip_btns
            and self._menu_mode == 'home'
        ):
            return
        was_ready = bool(getattr(self, '_ai_ready', False))
        self._ai_ready = ready
        if self._menu_mode == 'home':
            self._menu_chips = []
        self._clear_chip_layout()
        chips = self._active_chips()
        # Wrap chips into rows of up to 3
        rows = []
        buf = []
        for item in chips:
            buf.append(item)
            if len(buf) >= 3:
                rows.append(buf)
                buf = []
        if buf:
            rows.append(buf)
        for row_chips in rows:
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addStretch(1)
            for cid, label in row_chips:
                btn = _ChipButton(_t(label) if label else '')
                btn.clicked.connect(lambda checked=False, c=cid: self._select_chip(c))
                self._chip_btns[cid] = btn
                row.addWidget(btn)
            row.addStretch(1)
            self._chips_col.addLayout(row)

        if self._menu_mode == 'home':
            self.input.show()
            self.input.setPlaceholderText(_t('Ask, search, create, or give Neuron a task…'))
            default = 'ask_neury' if self._ai_ready else 'keep_safe'
            if self._phase == 'idle':
                self.lbl_prompt.setText(_t('Tell Neuron what you want to do.'))
            if self._chip not in self._chip_btns:
                self._chip = default
            if self._phase == 'idle':
                self._select_chip(self._chip)
                if (not was_ready) and ready:
                    self.lbl_prompt.setText(_t('What do you want Neuron to do?'))
                    self._focus_input()
                    self._rebuild_suggestions(force=True)
        elif self._phase == 'idle' and self._chip_btns:
            if self._chip not in self._chip_btns:
                self._chip = next(iter(self._chip_btns))
            self._select_chip(self._chip)
    def _refresh_mesh_live(self):
        if self._fading_out or self._fading_in:
            return
        self._mesh_tick = int(getattr(self, '_mesh_tick', 0) or 0) + 1
        if self._mesh_tick % _MESH_PULSE_EVERY == 0:
            self._mesh_pulse_i = int(getattr(self, '_mesh_pulse_i', 0) or 0) + 1
        pulse_i = getattr(self, '_mesh_pulse_i', 0)
        now = time.time()
        cache_age = now - float(_mesh_blurb_cache.get('ts') or 0)
        if cache_age >= _MESH_BLURB_TTL_SEC or not _mesh_blurb_cache.get('ts'):
            _kick_mesh_blurb_refresh(pulse_i)
        prev = getattr(self, '_mesh_stats_sig', None)
        try:
            primary = str(_mesh_blurb_cache.get('primary') or '')
            pulse = str(_mesh_blurb_cache.get('pulse') or '')
            sig = primary
            if sig != prev:
                self._mesh_stats_sig = sig
                # Brief pulse when live mesh numbers change.
                self.lbl_mesh.setStyleSheet(
                    'color:#b8fff0; font-size:14px; font-weight:700; background:transparent;'
                    ' letter-spacing:0.3px;'
                )
                QTimer.singleShot(450, self._reset_mesh_style)
            if primary:
                self.lbl_mesh.setText(primary)
            self.lbl_mesh_pulse.setText(pulse)
        except Exception:
            pass
        if self._phase == 'idle':
            try:
                self.lbl_mesh.show()
                self.lbl_mesh_pulse.show()
            except Exception:
                pass
            # Rebuild chips only when Neury readiness flips — not every second.
            ready = _neury_ready()
            if bool(ready) != bool(self._ai_ready):
                self._rebuild_capability_ui()
                self._rebuild_suggestions(force=True)

    def _reset_mesh_style(self):
        try:
            self.lbl_mesh.setStyleSheet(
                'color:rgba(180,220,240,210); font-size:14px; font-weight:600;'
                ' background:transparent; letter-spacing:0.3px;'
            )
        except Exception:
            pass

    def _apply_veil(self, ui_value, bg_value=None):
        ui = max(0.0, min(1.0, float(ui_value)))
        bg = ui if bg_value is None else max(0.0, min(1.0, float(bg_value)))
        self._veil_ui = ui
        self._veil_bg = bg
        self._veil = bg
        try:
            self._ui_fade.setOpacity(ui)
        except Exception:
            pass
        try:
            self._bg.update()
        except Exception:
            pass

    def _fade_in(self):
        if self._fading_out:
            return
        if not getattr(self, '_geometry_locked', False):
            _apply_cover_geometry(self, self._host)
            self._geometry_locked = True
        set_conductor_visible(True)
        self._fading_in = True
        self._fade_gen = int(getattr(self, '_fade_gen', 0) or 0) + 1
        gen = self._fade_gen
        self._apply_veil(0.0, 0.0)
        try:
            self.setWindowOpacity(1.0)
        except Exception:
            pass
        try:
            if not self.isVisible():
                self.show()
            self.raise_()
        except Exception:
            pass
        ready = getattr(self, '_on_ready', None)
        self._on_ready = None
        ready_fired = {'done': False}
        step = {'i': 0}
        interval = max(16, int(_FADE_IN_MS / _FADE_STEPS))

        def tick():
            if self._fading_out or _conductor is not self or gen != getattr(self, '_fade_gen', 0):
                self._fading_in = False
                return
            step['i'] += 1
            v = step['i'] / float(_FADE_STEPS)
            self._apply_veil(v, v)
            try:
                self.raise_()
            except Exception:
                pass
            if not ready_fired['done'] and v >= (1.0 / float(_FADE_STEPS)):
                ready_fired['done'] = True
                if callable(ready):
                    try:
                        ready()
                    except Exception:
                        pass
            if step['i'] >= _FADE_STEPS:
                self._fading_in = False
                self._apply_veil(1.0, 1.0)
                try:
                    self.raise_()
                except Exception:
                    pass
                after = getattr(self, '_after_fade_in', None)
                self._after_fade_in = None
                if callable(after):
                    QTimer.singleShot(0, after)
                return
            QTimer.singleShot(interval, tick)

        QTimer.singleShot(interval, tick)


    def _foreign_toplevel_present(self):
        """True when any other top-level Neuron UI is up (dialogs, message boxes, etc.).

        Conductor must not raise/activate over those windows — ever.
        """
        app = QApplication.instance()
        if app is None:
            return False
        main = getattr(init, 'mainApplication', None) or self._host
        skip_cls = {
            'Taskbar', 'Menu', 'Splash', 'SplashScreen', 'Banner', 'MacrosBar',
            'TVPlayer', 'NotificationsPopup', 'NotificationBubble',
            'ConductorOverlay',
        }
        try:
            # Header-tracked function windows (Devices etc.) — do not require
            # parentage checks that can miss independent BaseDialog HWNDs.
            for attr in (
                'devices_dialog', 'peers_dialog', 'rooms_dialog', 'chat_dialog',
                'system_dialog', 'profile_dialog', 'contacts_dialog',
            ):
                w = getattr(init, attr, None)
                if w is None or w is self:
                    continue
                try:
                    if w.isVisible() and not getattr(w, '_is_closing', False):
                        return True
                except RuntimeError:
                    continue
            modal = app.activeModalWidget()
            if modal is not None and modal is not self and not self.isAncestorOf(modal):
                return True
            popup = app.activePopupWidget()
            if popup is not None and popup is not self and not self.isAncestorOf(popup):
                return True
            active = app.activeWindow()
            if (
                active is not None
                and active is not self
                and not self.isAncestorOf(active)
                and (main is None or (active is not main and not main.isAncestorOf(active)))
            ):
                return True
            for w in app.topLevelWidgets():
                if w is self or w is main:
                    continue
                try:
                    if not w.isVisible() or not w.isWindow():
                        continue
                    if self.isAncestorOf(w) or (main is not None and main.isAncestorOf(w)):
                        continue
                    flags = w.windowFlags()
                    if flags & (
                        Qt.WindowType.Popup
                        | Qt.WindowType.ToolTip
                        | Qt.WindowType.SplashScreen
                    ):
                        continue
                    if w.__class__.__name__ in skip_cls:
                        continue
                    if getattr(w, '_is_closing', False) or getattr(w, 'is_closing', False):
                        continue
                    return True
                except RuntimeError:
                    continue
        except Exception:
            pass
        return False

    def _app_focus_elsewhere(self):
        """True when the user switched to another app, the taskbar, or session UI.

        In that case we must not raise_() — reclaiming Z-order blocks Alt+Tab,
        the Windows taskbar, and remote session controls (RustDesk, etc.).
        """
        app = QApplication.instance()
        if app is None:
            return False
        try:
            active = app.activeWindow()
        except Exception:
            return False
        if active is None:
            return True
        if active is self or self.isAncestorOf(active):
            return False
        main = getattr(init, 'mainApplication', None) or self._host
        if main is not None and (active is main or main.isAncestorOf(active)):
            return False
        return True

    def _keep_above_desktop(self):
        # Never touch Main chrome after leave — a queued timer tick can fire post-dismiss.
        # Never raise/activate over any other top-level UI (dialogs, message boxes, etc.).
        if self._fading_out or getattr(self, '_yield_focus', False):
            return
        if not bool(getattr(init, 'conductor_visible', False)):
            return
        try:
            if not self.isVisible():
                return
        except Exception:
            return
        if self._foreign_toplevel_present():
            return
        # Stay maximized, but yield the desktop when focus left this process.
        if self._app_focus_elsewhere():
            return
        try:
            # Z-order only among our chrome — never activateWindow/setFocus.
            self.raise_()
            _keep_taskbar_buried()
            _attach_window_controls_to_conductor(self, self._host)
        except Exception:
            pass

    def _allow_external_focus(self, allow):
        """Stop reclaiming stacking so quit dialogs and other windows can take focus."""
        self._yield_focus = bool(allow)
        if self._yield_focus:
            t = getattr(self, '_raise_timer', None)
            if t is not None:
                try:
                    t.stop()
                except Exception:
                    pass
            try:
                self.clearFocus()
            except Exception:
                pass
        elif self._raise_timer is not None and not self._fading_out:
            # Defer resume so a dialog opened in the same call stack becomes visible first.
            QTimer.singleShot(0, self._resume_raise_if_idle)

    def _resume_raise_if_idle(self):
        if self._fading_out or getattr(self, '_yield_focus', False):
            return
        if not bool(getattr(init, 'conductor_visible', False)):
            return
        if self._foreign_toplevel_present():
            return
        t = getattr(self, '_raise_timer', None)
        if t is not None:
            try:
                if not t.isActive():
                    t.start(400)
            except Exception:
                pass

    def _open_files_dialog(self, title, start_dir='', file_filter='All files (*.*)'):
        return QFileDialog.getOpenFileNames(
            self,
            title,
            start_dir or '',
            file_filter or 'All files (*.*)',
        )

    def _open_dir_dialog(self, title):
        return QFileDialog.getExistingDirectory(self, title)

    def _push_icons_behind(self):
        # Only while Conductor is covering — never after leave/dismiss.
        if not bool(getattr(init, 'conductor_visible', False)):
            return
        if getattr(self, '_fading_out', False):
            return
        try:
            if not self.isVisible():
                return
        except Exception:
            return
        try:
            for itm in list(getattr(init, 'icon_list', None) or []):
                w = getattr(itm, 'icon', None) or itm
                if w is None:
                    continue
                try:
                    w.hide()
                except Exception:
                    pass
        except Exception:
            pass
        _keep_taskbar_buried()
        try:
            _attach_window_controls_to_conductor(self, self._host)
        except Exception:
            pass

    def _chip_label_set(self):
        return set(label for _, label in self._active_chips())

    def _sync_add_file_button(self):
        """+ Add file stays on home and menus; hide only during busy/result."""
        busy = self._phase in ('running', 'done')
        try:
            self.btn_add_file.setVisible(not busy)
        except Exception:
            pass
        show_list = (not busy) and (
            bool(self._pending_files)
            or (
                self._menu_mode in ('hive', 'create', 'hive_setup')
                and str(self._chip).startswith('hive:')
            )
        )
        try:
            self.drop_zone.setVisible(show_list)
        except Exception:
            pass
        if show_list:
            self._refresh_drop_label()

    def _on_suggest_pause(self, *_args):
        self._suggest_paused = True
        QTimer.singleShot(8000, self._on_suggest_resume)

    def _on_suggest_resume(self):
        if self._phase == 'idle' and not (self._input_text() or '').strip():
            self._suggest_paused = False

    def _rebuild_suggestions(self, force=False):
        if self._phase != 'idle' and not force:
            return
        try:
            while self._suggest_col.count():
                item = self._suggest_col.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
        except Exception:
            pass
        self._suggest_btns = []
        self._suggest_pool = _conductor_suggestion_pool(ai_ready=bool(self._ai_ready))
        self._suggest_i = 0
        for _ in range(_SUGGEST_VISIBLE):
            btn = QPushButton('')
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFlat(True)
            btn.setStyleSheet(
                "QPushButton { color:rgba(170,220,240,200); background:transparent; border:none;"
                " font-size:12px; text-align:center; padding:2px 8px; }"
                "QPushButton:hover { color:#d8f6ff; text-decoration:underline; }"
            )
            btn.clicked.connect(lambda _c=False, b=btn: self._on_suggestion_clicked(b))
            self._suggest_col.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
            self._suggest_btns.append(btn)
        self._paint_suggestions()
        try:
            self._suggest_wrap.setVisible(self._phase == 'idle' and self._menu_mode == 'home')
        except Exception:
            pass

    def _paint_suggestions(self):
        pool = self._suggest_pool or []
        if not pool or not self._suggest_btns:
            return
        n = len(pool)
        start = int(self._suggest_i) % n
        for i, btn in enumerate(self._suggest_btns):
            item = pool[(start + i) % n]
            btn.setText(u'\u2022  %s' % _t(item['text']))
            btn.setProperty('suggest', item)
            btn.show()

    def _rotate_suggestions(self):
        if self._suggest_paused or self._fading_in or self._fading_out:
            return
        if self._phase != 'idle' or self._menu_mode != 'home':
            return
        if not self._suggest_pool:
            return
        self._suggest_i = (int(self._suggest_i) + 1) % len(self._suggest_pool)
        self._paint_suggestions()

    def _on_suggestion_clicked(self, btn):
        item = btn.property('suggest') if btn is not None else None
        if not isinstance(item, dict):
            return
        self._suggest_paused = True
        route = str(item.get('route') or '')
        target = str(item.get('target') or '')
        prefill = str(item.get('prefill') or item.get('text') or '')
        if prefill:
            prefill = _t(prefill)
        if route == 'hive' and target:
            self._open_work_files()
            chip = 'hive:' + target
            if chip in self._chip_btns:
                self._select_chip(chip)
            if prefill:
                self._set_input_text(prefill)
                self.input.show()
                self._focus_input()
            if item.get('needs_files'):
                self.btn_add_file.setFocus()
            return
        if route == 'action' and target:
            meta = cpower.action_by_id(target) or {}
            lane = str(meta.get('lane') or 'connect')
            if lane == 'connect':
                self._open_connect()
            elif lane == 'discover':
                self._open_discover()
            elif lane == 'create':
                self._open_create()
            chip = 'act:' + target
            if chip in self._chip_btns:
                self._select_chip(chip)
            if meta.get('needs_input') and prefill:
                self._set_input_text(prefill)
                self.input.show()
                self._focus_input()
            return
        if route == 'home' and target:
            self._back_to_chooser()
            if target in self._chip_btns:
                self._select_chip(target)
            if target == 'ask_neury' and prefill:
                self._set_input_text(prefill)
                self._focus_input()
            return
        if route == 'ask' or prefill:
            self._back_to_chooser()
            if 'ask_neury' in self._chip_btns:
                self._select_chip('ask_neury')
                self._set_input_text(prefill)
                self.input.show()
                self._focus_input()
                if item.get('auto_run') and prefill:
                    QTimer.singleShot(0, self._start_ask_neury)
                return
            # Neury not ready — offer install, then ask
            self._pending_question = prefill or 'Send an email'
            self._start_install_neury(pending_question=self._pending_question)
            return

    def _set_drop_visible(self, visible):
        """Show or hide the file list; + Add file is owned by _sync_add_file_button."""
        visible = bool(visible)
        try:
            if visible:
                self.drop_zone.show()
                self._refresh_drop_label()
            elif not self._pending_files:
                self.drop_zone.hide()
        except Exception:
            pass
        self._sync_add_file_button()

    def _select_chip(self, chip_id):
        if chip_id not in self._chip_btns:
            return
        self._chip = chip_id
        for cid, btn in self._chip_btns.items():
            btn.setChecked(cid == chip_id)

        if chip_id.startswith('hive:') and self._menu_mode in ('hive', 'create', 'hive_setup'):
            jid = chip_id.split(':', 1)[1]
            jobs = {j['id']: j for j in cpower.list_hive_jobs(include_neury_gated=True)}
            meta = jobs.get(jid) or {}
            self._hive_job_id = jid
            self._hive_job_meta = meta
            examples = meta.get('examples') or []
            blurb = _t(meta.get('blurb') or '') if meta.get('blurb') else ''
            lines = [blurb] if blurb else []
            if examples:
                lines.append('')
                lines.append(_t('Try saying:'))
                for ex in examples[:4]:
                    lines.append('• %s' % _t(str(ex)))
            if meta.get('tool_note'):
                lines.append('')
                lines.append(_t(str(meta.get('tool_note'))))
            self.result.setPlainText('\n'.join(lines))
            self.result.show()
            self._fit_result_height()
            self.input.show()
            self.input.setPlaceholderText(
                _t(meta.get('placeholder') or 'Describe what you want…')
            )
            self._set_drop_visible(True)
            self.lbl_prompt.setText(
                _t(meta.get('file_label') or 'Add files, describe the result, then Go.')
            )
            return

        if chip_id.startswith('act:'):
            aid = chip_id.split(':', 1)[1]
            act = cpower.action_by_id(aid) or {}
            self._set_drop_visible(False)
            examples = act.get('examples') or []
            body = _t(str(act.get('blurb') or '')) if act.get('blurb') else ''
            if examples:
                body += '\n\n' + _t('Examples:') + '\n' + '\n'.join(
                    '• %s' % _t(str(e)) for e in examples[:4]
                )
            if body:
                self.result.setPlainText(body)
                self.result.show()
                self._fit_result_height()
            else:
                self.result.hide()
            if act.get('needs_input'):
                self.input.show()
                self.input.setPlaceholderText(
                    _t(act.get('input_placeholder') or 'Type here…')
                )
                self._focus_input()
            else:
                if self._menu_mode != 'home' or not self._ai_ready:
                    self.input.hide()
            self.lbl_prompt.setText(_t('Press Go when ready.'))
            return

        self._set_drop_visible(False)
        if self._menu_mode == 'home' and not chip_id.startswith(('hive:', 'act:')):
            self.result.hide()
        hints = {
            'keep_safe': 'Press Go, then pick a file.',
            'ask_neury': 'Tell Neuron what you want, then Go.',
            'work_files': 'Press Go to see file tools (OCR, convert, photos…).',
            'connect': 'Press Go for invite, chat, diary, calendar.',
            'discover': 'Press Go to search, crawl, browse, retrieve.',
            'create': 'Press Go to translate, image, publish, or go live.',
            'install_neury': 'Press Go — install stays on this screen with live progress.',
            'back_home': 'Press Go to return.',
            'find_people': 'Press Go to see who is on your mesh.',
            'mesh_status': 'Press Go for a live mesh snapshot.',
            'invite': 'Press Go to copy an invite link.',
            'learn': 'Press Go for a short tour.',
        }
        hint = hints.get(chip_id, 'Tell Neuron what you want to do.')
        self.lbl_prompt.setText(_t(hint))
        if self._menu_mode == 'home':
            self.input.show()
            self._focus_input()
        self._sync_add_file_button()

    def _run_from_input(self):
        if self._menu_mode in ('hive', 'hive_setup', 'connect', 'discover', 'create'):
            self.run_selected()
            return
        raw = self._input_text().strip()
        text = raw.lower()
        labels = {label.lower(): cid for cid, label in self._active_chips()}
        if text in labels:
            self._select_chip(labels[text])
            self.run_selected()
            return
        if raw and raw not in self._chip_label_set():
            if not self._ai_ready:
                self._pending_question = raw
                self._start_install_neury(pending_question=raw)
                return
            self._chip = 'ask_neury'
            for cid, btn in self._chip_btns.items():
                btn.setChecked(cid == 'ask_neury')
            self._pending_question = raw
            self.run_selected()
            return
        self.run_selected()

    def run_selected(self):
        chip = self._chip
        if chip == 'back_home':
            self._back_to_chooser()
            return
        if chip == 'work_files':
            self._open_work_files()
            return
        if chip == 'connect':
            self._open_connect()
            return
        if chip == 'discover':
            self._open_discover()
            return
        if chip == 'create':
            self._open_create()
            return
        if chip.startswith('hive:'):
            self._start_hive_job(chip.split(':', 1)[1])
            return
        if chip.startswith('act:'):
            self._start_neuron_action(chip.split(':', 1)[1])
            return
        runners = {
            'keep_safe': self._start_keep_safe,
            'ask_neury': self._start_ask_neury,
            'find_people': self._start_find_people,
            'mesh_status': self._start_mesh_status,
            'invite': self._start_invite,
            'install_neury': self._start_install_neury,
            'learn': self._start_learn,
        }
        if chip == 'ask_neury' and not self._ai_ready:
            self._start_install_neury()
            return
        (runners.get(chip) or self._start_keep_safe)()

    def _clear_web_cards(self):
        lay = getattr(self, '_web_cards_lay', None)
        if lay is None:
            return
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        try:
            self._web_cards_scroll.hide()
        except Exception:
            pass
        try:
            self._web_cards_scroll.setMaximumHeight(
                int(getattr(self, '_web_cards_max_default', 280) or 280)
            )
        except Exception:
            pass

    def _add_section_label(self, title):
        lbl = QLabel(_t(title) if title else '')
        lbl.setStyleSheet(
            'color:#9fe9ff; font-size:12pt; font-weight:700; background:transparent;'
            ' padding:6px 2px 2px 2px;'
        )
        lbl.setWordWrap(True)
        self._web_cards_lay.addWidget(lbl)

    def _resolve_card_image_data(self, item):
        """Prefer in-memory still bytes; else load a cached image_path file."""
        if not isinstance(item, dict):
            return None
        data = item.get('image_data')
        if isinstance(data, (bytes, bytearray)) and len(data) >= 32:
            return bytes(data)
        path = str(item.get('image_path') or item.get('path') or '').strip()
        if path and os.path.isfile(path):
            try:
                with open(path, 'rb') as fh:
                    blob = fh.read()
                if blob and len(blob) >= 32:
                    return blob
            except Exception:
                pass
        # data:image/...;base64,... stills from peer public-file answers
        still = item.get('still')
        if isinstance(still, list) and still:
            still = still[0]
        if isinstance(still, str) and still.startswith('data:') and ',' in still:
            try:
                import base64
                return base64.b64decode(still.split(',', 1)[1])
            except Exception:
                return None
        return None

    def _add_result_card(self, item):
        card = _ConductorWebResultCard(
            title=str(item.get('title') or 'Link').strip() or 'Link',
            snippet=str(
                item.get('text') or item.get('description') or item.get('snippet') or ''
            ).strip(),
            url=str(item.get('url') or '').strip(),
            image_data=self._resolve_card_image_data(item),
            parent=self._web_cards_host,
        )
        self._web_cards_lay.addWidget(card)

    def _show_web_cards(self, cards):
        self._clear_web_cards()
        items = [
            c for c in (cards or [])
            if isinstance(c, dict) and (c.get('url') or c.get('title') or c.get('image_data'))
        ]
        if not items:
            return
        for item in items[:6]:
            self._add_result_card(item)
        self._web_cards_lay.addStretch(1)
        self._web_cards_scroll.show()

    def _show_triplex_cards(self, cloud_hits=None, web_hits=None, web_cards=None):
        """Always show NEURON CLOUD + WEB under the NEURY text answer."""
        self._clear_web_cards()
        cloud = [c for c in (cloud_hits or []) if isinstance(c, dict) and c.get('title')]
        # Prefer structured web_hits; fall back to live preview cards.
        web = [c for c in (web_hits or []) if isinstance(c, dict) and (c.get('title') or c.get('url'))]
        if not web:
            web = [
                c for c in (web_cards or [])
                if isinstance(c, dict) and (c.get('url') or c.get('title') or c.get('image_data'))
            ]
        try:
            self._web_cards_scroll.setMaximumHeight(
                int(getattr(self, '_web_cards_max_triplex', 420) or 420)
            )
        except Exception:
            pass
        self._add_section_label('NEURON CLOUD')
        if cloud:
            for item in cloud[:8]:
                peer = str(item.get('peer') or '').strip()
                snip = str(item.get('snippet') or item.get('text') or '').strip()
                if peer and peer not in snip:
                    snip = ('%s — %s' % (peer, snip)).strip(' —')
                self._add_result_card({
                    'title': item.get('title') or 'File',
                    'text': snip,
                    'url': item.get('url') or '',
                    'image_data': item.get('image_data'),
                    'image_path': item.get('image_path') or item.get('path') or '',
                    'still': item.get('still'),
                })
        else:
            empty = QLabel(_t('No public files matched on this node or the mesh.'))
            empty.setWordWrap(True)
            empty.setStyleSheet('color:#a8b8c8; font-size:10pt; background:transparent; padding:2px 4px 8px 4px;')
            self._web_cards_lay.addWidget(empty)
        self._add_section_label('WEB')
        if web:
            cards_by_url = {}
            for c in (web_cards or []):
                if isinstance(c, dict) and c.get('url'):
                    cards_by_url[str(c.get('url')).strip().lower()] = c
            for item in web[:8]:
                src = str(item.get('source') or '')
                badge = {
                    'crawl': 'Neuron crawl',
                    'peer_crawl': 'Peer crawl',
                    'live': 'Live web',
                }.get(src, '')
                snip = str(
                    item.get('snippet') or item.get('text') or item.get('description') or ''
                ).strip()
                if badge:
                    snip = ('%s · %s' % (badge, snip)).strip(' ·')
                url = str(item.get('url') or '').strip()
                card = cards_by_url.get(url.lower()) if url else None
                img_path = str(item.get('image_path') or item.get('path') or '').strip()
                if (not img_path or not os.path.isfile(img_path)) and card:
                    img_path = str(card.get('image_path') or '').strip()
                self._add_result_card({
                    'title': item.get('title') or 'Link',
                    'text': snip,
                    'url': url,
                    'image_data': item.get('image_data') or (card or {}).get('image_data'),
                    'image_path': img_path,
                })
        else:
            empty = QLabel(_t('No crawled pages or live web results yet.'))
            empty.setWordWrap(True)
            empty.setStyleSheet('color:#a8b8c8; font-size:10pt; background:transparent; padding:2px 4px 8px 4px;')
            self._web_cards_lay.addWidget(empty)
        self._web_cards_lay.addStretch(1)
        self._web_cards_scroll.show()

    def _set_result_height_mode(self, triplex=False):
        try:
            if triplex is True:
                lo, hi = getattr(self, '_result_height_triplex', (56, 500))
            elif triplex == 'neury':
                lo, hi = getattr(self, '_result_height_neury', (56, 500))
            else:
                lo, hi = getattr(self, '_result_height_default', (56, 360))
            self.result.setMinimumHeight(int(lo))
            self.result.setMaximumHeight(min(500, int(hi)))
            self._fit_result_height()
        except Exception:
            pass

    def _fit_result_height(self):
        """Hug status/answer text; grow up to max, no tall empty box."""
        box = getattr(self, 'result', None)
        if box is None or not hasattr(box, 'document') or box.isHidden():
            return
        try:
            doc = box.document()
            doc.setTextWidth(max(40, box.viewport().width()))
            margins = box.contentsMargins()
            frame = int(box.frameWidth()) * 2
            need = int(doc.size().height()) + margins.top() + margins.bottom() + frame + 24
            lo = 56
            try:
                hi = int(box.maximumHeight())
            except Exception:
                hi = 500
            hi = min(max(hi, lo), 500)
            h = max(lo, min(need, hi))
            box.setFixedHeight(h)
            if need > hi:
                box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            else:
                box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        except Exception:
            pass

    def _fit_steps_board_height(self):
        """Grow the steps panel with the task list (capped at 500px)."""
        scroll = getattr(self, '_steps_board_scroll', None)
        host = getattr(self, '_steps_board_host', None)
        lay = getattr(self, '_steps_board_lay', None)
        if scroll is None or host is None or scroll.isHidden():
            return
        try:
            # Always clear clamps first so sizeHint is not measured inside a tiny box.
            host.setMinimumHeight(0)
            host.setMaximumHeight(16777215)
            host.updateGeometry()
            if lay is not None:
                lay.activate()
                need = int(lay.sizeHint().height()) + 16
            else:
                host.adjustSize()
                need = int(host.sizeHint().height()) + 16
            # Gentle floor only — do not inflate height (that stretched gaps between rows).
            rows = max(1, int(lay.count()) if lay is not None else 1)
            need = max(need, 24 + rows * 18)
            lo, hi = 72, 500
            content_h = max(lo, need)
            host.setMinimumHeight(0)
            # Do not set maximumHeight to content — that crushed overlapping labels
            # when sizeHint was measured before widgets finished laying out.
            h = max(lo, min(content_h, hi))
            scroll.setFixedHeight(h)
            if content_h > hi:
                scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            else:
                scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        except Exception:
            pass

    def _place_neury_input_below_result(self, below=True):
        """In Neury reply mode, put the reply box under the transcript."""
        lay = getattr(self, '_main_lay', None) or (self._ui.layout() if self._ui else None)
        row = getattr(self, '_row_in', None)
        res = getattr(self, '_res_row', None)
        if lay is None or row is None:
            return
        try:
            for i in range(lay.count()):
                item = lay.itemAt(i)
                if item is not None and item.layout() is row:
                    lay.takeAt(i)
                    break
            if below and res is not None:
                for i in range(lay.count()):
                    item = lay.itemAt(i)
                    if item is not None and item.layout() is res:
                        lay.insertLayout(i + 1, row)
                        return
            idx = int(getattr(self, '_row_in_home_idx', 1) or 1)
            lay.insertLayout(min(max(0, idx), lay.count()), row)
        except Exception:
            pass

    def _set_busy(self, title, detail='', trust=''):
        self._phase = 'running'
        self._awaiting_consent = False
        self._consent_chrome_painted = False
        self._hide_project_board()
        try:
            self._consent_ev.clear()
        except Exception:
            pass
        self.lbl_q.setText(_t(title) if title else '')
        if detail:
            self.lbl_sub.setText(_t(detail) if detail else '')
            self.lbl_sub.show()
        else:
            self.lbl_sub.hide()
        self.lbl_mesh.hide()
        try:
            self.lbl_mesh_pulse.hide()
        except Exception:
            pass
        try:
            self.lbl_welcome.hide()
        except Exception:
            pass
        self.lbl_prompt.hide()
        self.lbl_stage.setText(_t('Elapsed 0:00'))
        self.lbl_stage.show()
        keep_chat = bool(
            getattr(self, '_neury_reply_mode', False)
            and str(getattr(self, '_neury_transcript', '') or '').strip()
        )
        if keep_chat:
            try:
                self.result.setPlainText(self._neury_transcript)
                self.result.show()
                self._set_result_height_mode('neury')
                self.result.moveCursor(QTextCursor.MoveOperation.End)
            except Exception:
                pass
        else:
            self.result.clear()
            # Stay hidden until the first progress line — avoids a tall empty box.
            self.result.hide()
            self._set_result_height_mode('neury')
        self._clear_web_cards()
        self._clear_project_showcase()
        self.input.hide()
        self._chip_wrap.hide()
        try:
            self._suggest_wrap.hide()
        except Exception:
            pass
        self.btn_how.hide()
        self.btn_again.hide()
        self.btn_save.hide()
        try:
            if getattr(self, '_btn_neury_home', None):
                self._btn_neury_home.hide()
        except Exception:
            pass
        try:
            self.btn_yes.hide()
            self.btn_no.hide()
        except Exception:
            pass
        try:
            self._set_drop_visible(False)
        except Exception:
            pass
        self._sync_add_file_button()
        try:
            self._mail_pick_wrap.hide()
        except Exception:
            pass
        self.btn_run.setEnabled(False)
        # Collapse the Go/+/prompt row entirely — a lone hidden-but-layout-
        # residual circle was reading as a gray "x" under the Neury title.
        self._hide_prompt_chrome()
        try:
            self.btn_abort.setText(_t('Stop'))
            self.btn_abort.show()
        except Exception:
            pass
        if trust:
            self.trust.setText(_t(trust))
            self.trust.show()
        else:
            self.trust.hide()
        if detail:
            self._append_job_log(str(detail))
        self._start_elapsed_timer()

    def _advance_story(self, stages, on_done):
        self._stages = list(stages)
        self._stage_i = 0
        self._on_story_done = on_done

        def _step():
            if self._fading_out:
                return
            if self._stage_i >= len(self._stages):
                if callable(self._on_story_done):
                    self._on_story_done()
                return
            label, detail = self._stages[self._stage_i]
            self.lbl_stage.setText(_t(label) if label else '')
            if detail:
                self.lbl_sub.setText(_t(detail))
            self._stage_i += 1
            QTimer.singleShot(650, _step)

        _step()

    def _show_stay_result(
        self, title, body, trust='', offer_save=False, web_cards=None,
        triplex=None, allow_neury_reply=False,
    ):
        """Show a result on Conductor — do not open desktop windows."""
        self._phase = 'done'
        self._awaiting_consent = False
        self._neury_reply_mode = bool(allow_neury_reply)
        self._stop_elapsed_timer()
        try:
            self.btn_yes.hide()
            self.btn_no.hide()
        except Exception:
            pass
        mark_completed_once()
        self.lbl_q.setText(_t(title) if title else '')
        if allow_neury_reply:
            self.lbl_sub.setText(
                _t('Type a reply and press Enter, or Open desktop for a longer Neury chat.')
            )
        else:
            self.lbl_sub.setText(_t('Still on the front door.'))
        self.lbl_sub.show()
        self.lbl_mesh.hide()
        try:
            self.lbl_mesh_pulse.hide()
        except Exception:
            pass
        trust_l = (trust or '').lower()
        body_l = (body or '').lower()
        stage = 'Completed' if (trust and 'could not' not in trust_l and 'failed' not in trust_l) else ''
        if any(k in trust_l for k in (
            'fail', 'declined', 'missing', 'abort', 'not ready', 'could not',
            'still running', 'timed out', 'timeout', 'stopped',
        )) or 'timed out' in body_l:
            stage = 'Stopped'
        elif offer_save or (body and 'Results ready' in body):
            stage = 'Completed'
        # Incomplete project board is never "Completed".
        if getattr(self, '_project_board_mode', False) and self._project_board_id:
            try:
                from Support.NeuronOrchestrator import project_status
                summ = (project_status(self._project_board_id) or {}).get('summary') or {}
                pending = int(summ.get('pending') or 0) + int(summ.get('awaiting') or 0)
                if pending > 0 or str(summ.get('status') or '') in ('active', 'awaiting_confirm', 'failed'):
                    if str(summ.get('status') or '') == 'completed':
                        stage = 'Completed'
                    elif str(summ.get('status') or '') == 'failed':
                        stage = 'Stopped'
                    else:
                        stage = 'Stopped' if stage == 'Completed' else (stage or 'Stopped')
            except Exception:
                pass
        self.lbl_stage.setText(_t(stage) if stage else '')
        self.lbl_prompt.hide()
        self._chip_wrap.hide()
        try:
            self._suggest_wrap.hide()
        except Exception:
            pass
        self.btn_how.hide()
        try:
            self._set_drop_visible(False)
        except Exception:
            pass
        raw_body = body or ''
        triplex_info = triplex if isinstance(triplex, dict) else None
        # Project jobs: keep the compact step board; skip the long text dump.
        if getattr(self, '_project_board_mode', False) and self._project_board_id:
            try:
                from Support.NeuronOrchestrator import project_status
                st = project_status(self._project_board_id)
                self._paint_project_steps((st or {}).get('steps') or [], None)
            except Exception:
                pass
            try:
                self.result.hide()
            except Exception:
                pass
            self._set_result_height_mode(False)
            self._show_web_cards(web_cards)
        else:
            self._hide_project_board()
            if triplex_info:
                self._set_result_height_mode(True)
                neury_text = str(triplex_info.get('neury_text') or '').strip()
                asked = str(triplex_info.get('question') or '').strip()
                display_body = 'NEURY\n\n%s' % (neury_text or raw_body or '')
                if asked:
                    display_body = 'You asked:\n%s\n\n%s' % (asked, display_body)
                self.result.setPlainText(display_body)
                self.result.show()
                self._fit_result_height()
                try:
                    self.result.moveCursor(QTextCursor.MoveOperation.Start)
                except Exception:
                    pass
                self._show_triplex_cards(
                    cloud_hits=triplex_info.get('cloud_hits'),
                    web_hits=triplex_info.get('web_hits'),
                    web_cards=triplex_info.get('web_cards') or web_cards,
                )
            else:
                if allow_neury_reply:
                    self._set_result_height_mode('neury')
                else:
                    self._set_result_height_mode(False)
                # Fixed one-line UI messages → translate; multi-line tool dumps stay as-is.
                if raw_body and '\n' not in raw_body and len(raw_body) < 220:
                    display_body = _t(raw_body)
                else:
                    display_body = raw_body
                self.result.setPlainText(display_body)
                self.result.show()
                self._fit_result_height()
                try:
                    if allow_neury_reply:
                        self.result.moveCursor(QTextCursor.MoveOperation.End)
                    else:
                        self.result.moveCursor(QTextCursor.MoveOperation.Start)
                except Exception:
                    pass
                self._show_web_cards(web_cards)
        if trust:
            self.trust.setText(_t(trust))
            self.trust.show()
        else:
            self.trust.hide()
        try:
            self.btn_abort.hide()
        except Exception:
            pass
        self.btn_again.show()
        if offer_save and self._last_produced:
            self.btn_save.show()
        else:
            self.btn_save.hide()
        project_wrap = bool(
            getattr(self, '_project_board_mode', False) and self._project_board_id
        )
        if project_wrap or stage in ('Completed', 'Stopped'):
            self._cleanup_job_work_files()
        if project_wrap:
            # Multi-step job finished or stopped — home is the clear exit.
            self._place_neury_input_below_result(False)
            self.input.hide()
            try:
                if getattr(self, '_btn_neury_home', None):
                    self._btn_neury_home.hide()
            except Exception:
                pass
            try:
                self.btn_again.setText(_t('Try another'))
                self.btn_again.clicked.disconnect()
            except Exception:
                pass
            self.btn_again.clicked.connect(self._back_to_chooser)
            self.btn_again.show()
            self._set_run_label(_t('Open desktop'))
            self.btn_run.setEnabled(True)
            try:
                self.btn_run.clicked.disconnect()
            except Exception:
                pass
            self.btn_run.clicked.connect(self.enter_desktop)
            self._show_project_showcase()
        elif allow_neury_reply:
            self._clear_project_showcase()
            self._place_neury_input_below_result(True)
            self.input.show()
            self._set_input_text('')
            self.input.setPlaceholderText(_t('Reply to Neury…'))
            self._set_run_label(_t('Ask again'))
            self.btn_run.setEnabled(True)
            try:
                self.btn_run.clicked.disconnect()
            except Exception:
                pass
            self.btn_run.clicked.connect(self._neury_followup_from_input)
            try:
                self.btn_again.setText(_t('Open desktop'))
                self.btn_again.clicked.disconnect()
            except Exception:
                pass
            self.btn_again.clicked.connect(self.enter_desktop)
            try:
                if not getattr(self, '_btn_neury_home', None):
                    self._btn_neury_home = QPushButton(_t('Try another'), self._ui)
                    self._btn_neury_home.setCursor(Qt.CursorShape.PointingHandCursor)
                    self._btn_neury_home.setMinimumHeight(40)
                    self._btn_neury_home.setStyleSheet(
                        "QPushButton { color:#cfe8ff; background:rgba(0,0,0,100);"
                        " border:1px solid rgba(160,200,230,120); border-radius:20px;"
                        " padding:8px 18px; font-size:13px; font-weight:600; }"
                        "QPushButton:hover { background:rgba(20,40,70,180); }"
                    )
                    self._btn_neury_home.clicked.connect(self._back_to_chooser)
                    parent_lay = self.btn_again.parentWidget().layout() if self.btn_again.parentWidget() else None
                    if parent_lay is not None:
                        parent_lay.insertWidget(parent_lay.indexOf(self.btn_again), self._btn_neury_home)
                self._btn_neury_home.setText(_t('Try another'))
                self._btn_neury_home.show()
            except Exception:
                pass
            try:
                self.input.setFocus()
            except Exception:
                pass
        else:
            self._clear_project_showcase()
            self._place_neury_input_below_result(False)
            self.input.hide()
            try:
                if getattr(self, '_btn_neury_home', None):
                    self._btn_neury_home.hide()
            except Exception:
                pass
            try:
                self.btn_again.setText(_t('Try another'))
                self.btn_again.clicked.disconnect()
            except Exception:
                pass
            self.btn_again.clicked.connect(self._back_to_chooser)
            self._set_run_label(_t('Open desktop'))
            self.btn_run.setEnabled(True)
            try:
                self.btn_run.clicked.disconnect()
            except Exception:
                pass
            self.btn_run.clicked.connect(self.enter_desktop)
        self._sync_add_file_button()

    def _cleanup_job_work_files(self):
        try:
            from Support.ConductorPower import cleanup_conductor_work_files
            cleanup_conductor_work_files()
        except Exception:
            pass

    def _abort_to_home(self):
        """Cancel the in-flight job and return to the Conductor home screen."""
        self._neury_cancel = True
        self._neury_run_id = int(getattr(self, '_neury_run_id', 0) or 0) + 1
        try:
            from Support.NeuronOrchestrator import cancel_project
            cancel_project('')
        except Exception:
            pass
        try:
            self._consent_ev.set()
        except Exception:
            pass
        try:
            self._ui_tool_ev.set()
        except Exception:
            pass
        try:
            self.btn_abort.hide()
        except Exception:
            pass
        self._cleanup_job_work_files()
        self._back_to_chooser()

    def _back_to_chooser(self):
        self._neury_cancel = True
        self._neury_run_id = int(getattr(self, '_neury_run_id', 0) or 0) + 1
        self._restore_prompt_chrome()
        try:
            from Support.NeuronOrchestrator import cancel_project
            cancel_project('')
        except Exception:
            pass
        self._phase = 'idle'
        self._awaiting_consent = False
        self._neury_reply_mode = False
        self._neury_thread_question = ''
        self._neury_thread_reply = ''
        self._neury_transcript = ''
        try:
            self._place_neury_input_below_result(False)
        except Exception:
            pass
        self._hide_project_board()
        self._stop_elapsed_timer()
        try:
            self._consent_ev.set()
        except Exception:
            pass
        try:
            self._ui_tool_ev.set()
        except Exception:
            pass
        try:
            self.btn_abort.hide()
        except Exception:
            pass
        self._menu_mode = 'home'
        self._menu_chips = []
        self._pending_question = ''
        self._pending_mail = None
        self._pending_files = []
        self._hive_job_id = ''
        self._hive_job_meta = None
        self._hive_tool_retry = False
        self._last_produced = []
        self._clear_mail_account_btns()
        try:
            if getattr(self, '_btn_neury_home', None):
                self._btn_neury_home.hide()
        except Exception:
            pass
        try:
            self.btn_again.setText(_t('Try another'))
            self.btn_again.clicked.disconnect()
        except Exception:
            pass
        try:
            self.btn_again.clicked.connect(self._back_to_chooser)
        except Exception:
            pass
        self.btn_run.show()
        self.btn_save.hide()
        try:
            self.btn_yes.hide()
            self.btn_no.hide()
        except Exception:
            pass
        self._clear_web_cards()
        self._clear_project_showcase()
        self._set_result_height_mode(False)
        try:
            self._set_drop_visible(False)
        except Exception:
            pass
        name = ''
        if isinstance(init.ME, dict):
            name = str(init.ME.get('node_name') or init.ME.get('nickname') or '').strip()
        self.lbl_q.setText(_t('A New Kind of Internet'))
        if self._soft:
            self.lbl_welcome.setText(
                _tf('Welcome back, %s', name) if name else _t('Welcome back')
            )
            self.lbl_welcome.show()
        else:
            self.lbl_welcome.hide()
            self.lbl_welcome.clear()
        self.lbl_sub.hide()
        self.lbl_sub.clear()
        self.lbl_mesh.show()
        try:
            self.lbl_mesh_pulse.show()
        except Exception:
            pass
        self.lbl_prompt.show()
        self.lbl_prompt.setText(_t('What do you want Neuron to do?'))
        self.lbl_stage.setText('')
        self.result.hide()
        self.result.clear()
        self.trust.hide()
        self._set_input_text('')
        self.input.setPlaceholderText(_t('Ask, search, create, or give Neuron a task…'))
        self.input.show()
        self._chip_wrap.show()
        try:
            self._suggest_wrap.show()
            self._rebuild_suggestions(force=True)
            self._suggest_paused = False
        except Exception:
            pass
        self.btn_how.show()
        self.btn_again.hide()
        self._set_run_label(_t('Go'))
        self.btn_run.setEnabled(True)
        try:
            self.btn_run.clicked.disconnect()
        except Exception:
            pass
        self.btn_run.clicked.connect(self._run_from_input)
        self._rebuild_capability_ui(force=True)
        self._refresh_mesh_live()
        self._sync_add_file_button()
        self._focus_input()

    # ---- paths (all stay inside Conductor) ----

    def _start_keep_safe(self):
        self._track_icon_id = ''
        self._track_file_name = ''
        try:
            from Support.FirstRunExperience import set_journey_stage
            set_journey_stage('neuronize', reveal_icon_id='', reveal_file_name='')
        except Exception:
            pass
        self._set_busy(
            'Keep a file safe',
            'Your file will be sliced and held across the mesh.',
            'What leaves: encrypted pieces to peers you already know.',
        )

        def after_story():
            main = getattr(init, 'mainApplication', None)
            if main is None:
                self._show_stay_result('Not ready', 'Desktop is not ready yet. Try again in a moment.')
                return
            self.lbl_stage.setText(_t('Choose a file…'))
            QTimer.singleShot(150, lambda: self._neuronize_pick(main))

        self._advance_story([
            ('Your node wakes', 'Preparing to protect the file.'),
            ('The mesh listens', 'Online peers can hold encrypted pieces.'),
        ], after_story)

    def _neuronize_pick(self, main):
        selected_files, _ = self._open_files_dialog(
            'Select a file to keep safe on the mesh',
            '',
            'Any file (*.*)',
        )
        if not selected_files:
            self._back_to_chooser()
            return

        room = getattr(init, 'selected_room_info', None) or {}
        room_id = room.get('room_id')
        if not room_id:
            self._show_stay_result('No room ready', 'Open desktop once to finish setup, then try again.')
            return

        created_ids = []
        try:
            from Support.Files import import_files
            x = QCursor.pos().x() - getattr(getattr(init, 'Screen', None), 'delta', 0)
            y = QCursor.pos().y()
            import_files(
                main, selected_files, x, y, room_id,
                room.get('allowed_peers'), room.get('type'),
                created_ids=created_ids,
            )
        except Exception as e:
            self._show_stay_result('Could not neuronize', str(e))
            return

        self._push_icons_behind()
        QTimer.singleShot(80, self._push_icons_behind)

        icon_id = str(created_ids[0]) if created_ids else ''
        file_name = os.path.basename(selected_files[0])
        self._track_icon_id = icon_id
        self._track_file_name = file_name
        try:
            from Support.FirstRunExperience import set_journey_stage
            set_journey_stage('neuronize', reveal_icon_id=icon_id, reveal_file_name=file_name)
        except Exception:
            pass

        self.lbl_stage.setText(_t('Spreading across the mesh…'))
        self.lbl_sub.setText(_tf('Prepared · %s', file_name))
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._poll_reveal)
        self._poll.start(1200)
        QTimer.singleShot(20000, self._reveal_timeout)

    def _holders_for_icon(self, icon_id):
        if not icon_id or not getattr(init, 'pool_main_db', None):
            return 0
        try:
            from Support.Utils import query_database
            rows = query_database(
                init.pool_main_db,
                "SELECT COUNT(DISTINCT peer_id) FROM ChunksSent "
                "WHERE icon_id = ? AND peer_id IS NOT NULL AND peer_id != ''",
                (icon_id,),
                commit=False,
            )
            if rows and rows[0]:
                return int(rows[0][0] or 0)
        except Exception:
            pass
        return 0

    def _poll_reveal(self):
        name = self._track_file_name or 'your file'
        holders = self._holders_for_icon(self._track_icon_id)
        if holders > 0:
            if self._poll:
                self._poll.stop()
            try:
                from Support.FirstRunExperience import set_journey_stage
                set_journey_stage(
                    'reveal',
                    reveal_icon_id=self._track_icon_id,
                    reveal_file_name=name,
                )
            except Exception:
                pass
            self._show_stay_result(
                '%s is alive in the mesh' % name,
                'Held by %s peer(s).\n\n'
                'That is Neuron — your file, carried by people\'s machines.\n'
                'Retrieve anytime from the desktop (Ctrl+F), or try another path here.'
                % holders,
                'Pieces are on peer machines. Nothing else left your PC.',
            )
            return
        self.lbl_sub.setText(_tf('Prepared · %s — waiting for the first peer holder…', name))

    def _reveal_timeout(self):
        if self._phase != 'running' or self._fading_out:
            return
        if self._poll and self._poll.isActive():
            self._poll.stop()
        name = self._track_file_name or 'your file'
        self._show_stay_result(
            'Mesh is ready',
            '%s is prepared.\nLeave Neuron open so peers can hold pieces.\n'
            'Retrieve from the desktop later (Ctrl+F).' % name,
        )

    def _emit_project_board(self, project_id, consent=None, steps=None, run_id=None):
        """Queue a board refresh on the UI thread."""
        try:
            # Drop paints from a superseded Neury run (two threads were fighting).
            if run_id is not None and int(run_id) != int(getattr(self, '_neury_run_id', 0) or 0):
                return
            payload = {
                'project_id': str(project_id or getattr(self, '_project_board_id', '') or ''),
                'consent': consent,
                # Prefer disk on UI thread; boot steps fill the blank 5s gap before save.
                'steps': list(steps) if isinstance(steps, list) and steps else None,
                'run_id': int(getattr(self, '_neury_run_id', 0) or 0),
            }
            self.project_board.emit(payload)
        except Exception:
            pass

    def _project_status_fn(self, project_id, run_id=None):
        """Refresh the compact step board; keep live % on the running step."""
        owned_run = int(run_id if run_id is not None else getattr(self, '_neury_run_id', 0) or 0)

        def status_fn(msg=None):
            if self._neury_cancel:
                return
            if int(getattr(self, '_neury_run_id', 0) or 0) != owned_run:
                return
            text = str(msg or '').strip()
            step_tag = ''
            mtag = re.match(r'\[\[step:([^\]]+)\]\]\s*(.*)$', text)
            if mtag:
                step_tag = str(mtag.group(1) or '').strip()
                text = str(mtag.group(2) or '').strip()
            pct = None
            try:
                # Prefer the LAST xx% — status_fn appends the authoritative pct after
                # peer messages that already contain their own "15%" etc.
                matches = re.findall(r'(\d+)\s*%', text)
                if matches:
                    pct = max(0, min(100, int(matches[-1])))
            except Exception:
                pct = None
            # Confirmed mesh + hire ask — show peer names as soon as we invite.
            mesh_live = bool(
                text and re.search(
                    r'\bMesh live\b|\bMesh done\b|\bPeers joined:\s*[1-9]',
                    text, re.I,
                )
            )
            peer_count = 0
            peer_names = []
            mcount = re.search(r'Mesh (?:live|done)\s*-\s*(\d+)\s+Neury', text, re.I)
            if not mcount:
                mcount = re.search(r'Peers joined:\s*(\d+)', text, re.I)
            if mcount:
                try:
                    peer_count = int(mcount.group(1))
                except Exception:
                    peer_count = 0

            def _split_peer_names(blob):
                blob = re.sub(r'\s*\(\d{1,3}\s*%\)\s*$', '', str(blob or '')).strip()
                blob = blob.lstrip(':').strip()
                out = []
                for p in blob.split(','):
                    name = p.strip().lstrip(':').strip()
                    if not name or name.lower() in ('working', 'done'):
                        continue
                    out.append(name)
                return out[:6]

            mnames = re.search(
                r'Mesh (?:live|done)\s*-\s*\d+\s+Neury\w*(?:\s+working)?\s*:\s*(.+)$',
                text, re.I,
            )
            if mnames:
                peer_names = _split_peer_names(mnames.group(1))
            if not peer_names:
                masking = re.search(
                    r'Asking\s+\d+\s+Neury[^:]*:\s*(.+)$',
                    text, re.I,
                )
                if masking:
                    peer_names = _split_peer_names(masking.group(1))
            asking = bool(peer_names) and not mesh_live and bool(
                text and re.search(r'\bAsking\s+\d+\s+Neury', text, re.I)
            )
            # Only final local fallback clears invite names — not the local pulse tick.
            local_handoff = bool(
                text and re.search(
                    r'No mesh takers|Mesh busy|doing it on this PC',
                    text, re.I,
                )
            )
            working_local = bool(
                text and re.search(r'Working on this PC', text, re.I)
            )
            finished_line = bool(text and re.search(r'^Finished:', text, re.I))
            try:
                from Support.NeuronOrchestrator import _load_project, save_project
                with _PROJECT_STATUS_LOCK:
                    # Reload from disk on critical transitions; otherwise reuse cache
                    # so parallel workers do not freeze ADMIN with JSON thrash.
                    cache = getattr(self, '_status_proj_cache', None)
                    must_reload = bool(
                        finished_line or mesh_live or asking or local_handoff
                        or not (
                            isinstance(cache, dict)
                            and str(cache.get('id') or '') == str(project_id or '')
                        )
                    )
                    if must_reload:
                        fresh = _load_project(project_id)
                        # Merge in-flight % / peer badges from cache so a reload
                        # cannot snap sibling steps back to a stuck 12%.
                        if (
                            isinstance(fresh, dict)
                            and isinstance(cache, dict)
                            and str(cache.get('id') or '') == str(fresh.get('id') or '')
                        ):
                            prev = {
                                str(s.get('id') or ''): s
                                for s in (cache.get('steps') or [])
                                if isinstance(s, dict)
                            }
                            for s in (fresh.get('steps') or []):
                                if not isinstance(s, dict):
                                    continue
                                old = prev.get(str(s.get('id') or ''))
                                if not old:
                                    continue
                                try:
                                    s['progress'] = max(
                                        int(s.get('progress') or 0),
                                        int(old.get('progress') or 0),
                                    )
                                except Exception:
                                    pass
                                if old.get('peer_help') and not s.get('peer_help'):
                                    s['peer_help'] = True
                                    s['peer_count'] = max(
                                        int(s.get('peer_count') or 0),
                                        int(old.get('peer_count') or 0),
                                    )
                                    if old.get('peer_names') and not s.get('peer_names'):
                                        s['peer_names'] = list(old.get('peer_names') or [])
                        proj = fresh
                        self._status_proj_cache = proj
                    else:
                        proj = cache
                    if proj:
                        ctx = proj.setdefault('context', {})
                        if pct is not None:
                            ctx['step_progress'] = pct
                        if text:
                            ctx['step_activity'] = text[:180]
                        if mesh_live and peer_count > 0:
                            ctx['peer_help_active'] = True
                        target = None
                        if step_tag:
                            for step in proj.get('steps') or []:
                                if str(step.get('id') or '') == step_tag:
                                    target = step
                                    break
                        if target is None:
                            # Only fall back when exactly one step is running —
                            # otherwise untagged ticks steal progress from siblings
                            # and leave the rest frozen (e.g. stuck at 12%).
                            running = [
                                s for s in (proj.get('steps') or [])
                                if str(s.get('status') or '') == 'running'
                            ]
                            if len(running) == 1:
                                target = running[0]
                        dirty = False
                        if target is not None:
                            st = str(target.get('status') or '')
                            # Never demote a finished step back to a live %.
                            if st in ('done', 'skipped', 'blocked', 'failed'):
                                if mesh_live and peer_count > 0:
                                    target['peer_help'] = True
                                    target['peer_count'] = max(
                                        peer_count, int(target.get('peer_count') or 0),
                                    )
                                    if peer_names:
                                        target['peer_names'] = peer_names
                                    dirty = True
                                elif finished_line and pct is not None:
                                    target['progress'] = 100
                                    dirty = True
                            else:
                                if pct is not None:
                                    try:
                                        old_p = int(target.get('progress') or 0)
                                    except Exception:
                                        old_p = 0
                                    # Local work may restart below a prior mesh ask %
                                    # — always take the max so bars never jump back,
                                    # but never refuse the local pulse climb.
                                    if local_handoff or working_local:
                                        new_p = max(old_p, int(pct))
                                        if new_p != old_p:
                                            target['progress'] = new_p
                                            dirty = True
                                    elif pct >= old_p:
                                        if old_p != pct:
                                            target['progress'] = pct
                                            dirty = True
                                    # 100% = peer finished — flip to done so the board
                                    # never sits on WORKING 100% + LIVE forever.
                                    try:
                                        cur_p = int(target.get('progress') or 0)
                                    except Exception:
                                        cur_p = 0
                                    if (
                                        cur_p >= 100
                                        or int(pct) >= 100
                                        or finished_line
                                        or (
                                            text
                                            and re.search(r'\bMesh done\b', text, re.I)
                                        )
                                    ):
                                        if str(target.get('status') or '') == 'running':
                                            target['status'] = 'done'
                                            target['progress'] = 100
                                            dirty = True
                                if text and str(target.get('activity_msg') or '') != text[:160]:
                                    target['activity_msg'] = text[:160]
                                    dirty = True
                                if mesh_live and peer_count > 0:
                                    if (
                                        not target.get('peer_help')
                                        or int(target.get('peer_count') or 0) != peer_count
                                    ):
                                        dirty = True
                                    target['peer_help'] = True
                                    target['peer_count'] = peer_count
                                    if peer_names:
                                        target['peer_names'] = peer_names
                                    try:
                                        from Support.NeuronNeuryAskParallel import peer_colors
                                        target['peer_colors'] = peer_colors(
                                            max(peer_count, len(peer_names) or 1)
                                        )
                                    except Exception:
                                        pass
                                elif asking and peer_names:
                                    # Show Rob1 / Rob Tower tracks immediately on invite.
                                    if list(target.get('peer_names') or []) != peer_names:
                                        target['peer_names'] = peer_names
                                        dirty = True
                                    if int(target.get('peer_count') or 0) < len(peer_names):
                                        target['peer_count'] = len(peer_names)
                                        dirty = True
                                    try:
                                        from Support.NeuronNeuryAskParallel import peer_colors
                                        want_colors = peer_colors(max(1, len(peer_names)))
                                    except Exception:
                                        want_colors = []
                                    if want_colors and list(target.get('peer_colors') or []) != want_colors:
                                        target['peer_colors'] = want_colors
                                        dirty = True
                                elif local_handoff:
                                    # Clear invite-only names when falling back to this PC.
                                    # Keep confirmed mesh hire (peer_help) through DONE.
                                    if not target.get('peer_help'):
                                        if target.get('peer_names') or target.get('peer_count'):
                                            target['peer_count'] = 0
                                            target['peer_names'] = []
                                            dirty = True
                                # Once a peer helped, keep their name through DONE.
                                if target.get('peer_help') and peer_names:
                                    if list(target.get('peer_names') or []) != peer_names:
                                        target['peer_names'] = peer_names
                                        dirty = True
                        if dirty:
                            now_save = time.time()
                            last_save = float(getattr(self, '_board_status_save_t0', 0) or 0)
                            critical = bool(
                                mesh_live
                                or asking
                                or local_handoff
                                or working_local
                                or finished_line
                                or (pct is not None and int(pct) >= 99)
                                or peer_names
                            )
                            if critical or (now_save - last_save) >= 0.8:
                                save_project(proj)
                                self._board_status_save_t0 = now_save
            except Exception:
                pass
            now = time.time()
            last = float(getattr(self, '_board_status_emit_t0', 0) or 0)
            # Critical events paint soon; steady % ticks stay calm so UI never freezes.
            if finished_line or mesh_live or asking or local_handoff or working_local:
                gap = 0.45
            else:
                gap = 1.2
            if (now - last) >= gap:
                self._board_status_emit_t0 = now
                self._emit_project_board(project_id, run_id=owned_run)

        return status_fn

    @staticmethod
    def _project_mesh_peer_total(steps):
        """Sum confirmed remote Neurys only (peer_help + peer_count), never job count."""
        total = 0
        for s in steps or []:
            if not s.get('peer_help'):
                continue
            try:
                total += max(0, int(s.get('peer_count') or 0))
            except Exception:
                pass
        return total

    @staticmethod
    def _project_overall_progress(steps):
        """Overall 0-100 from finished steps + average in-flight progress."""
        steps = list(steps or [])
        total = len(steps)
        if total <= 0:
            return 0, 0, 0
        finished = 0
        running_prog = []
        for s in steps:
            st = str(s.get('status') or '')
            try:
                sp = int(s.get('progress') or 0)
            except Exception:
                sp = 0
            # Treat 100% running as finished so Progress N/M does not freeze.
            if st in ('done', 'skipped', 'blocked', 'failed') or (
                st == 'running' and sp >= 100
            ):
                finished += 1
            elif st == 'running':
                running_prog.append(max(0, min(100, sp)))
        frac = float(finished) / float(total)
        if running_prog:
            avg = sum(running_prog) / float(len(running_prog))
            frac += (avg / 100.0) * (float(len(running_prog)) / float(total))
        pct = max(0, min(100, int(round(frac * 100.0))))
        return pct, finished, total

    def _thin_progress_bar(self, parent, value, accent='#7ad7ef', height=4):
        bar = QProgressBar(parent)
        bar.setRange(0, 100)
        bar.setValue(max(0, min(100, int(value or 0))))
        bar.setTextVisible(False)
        h = max(3, int(height or 4))
        bar.setFixedHeight(h)
        bar.setMaximumHeight(h)
        bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        radius = max(2, h // 2)
        # chunk width:1px keeps 85% from painting as a full bar on Windows styles.
        bar.setStyleSheet(
            "QProgressBar { background:rgba(10,24,40,100); border:none;"
            " border-radius:%spx; min-height:%spx; max-height:%spx; }"
            "QProgressBar::chunk { background:%s; border:none; border-radius:%spx;"
            " width:1px; margin:0; }"
            % (radius, h, h, accent, radius)
        )
        return bar

    def _project_ask_fn(self, project_id=''):
        """Thread-safe consent — Yes/No on the step row during project jobs."""

        def ask_fn(
            title, message, draft_text='', draft_field='message',
            apply_to_step='', preview_kind='', website_name='',
        ):
            if self._neury_cancel:
                return False
            try:
                self._consent_ev.clear()
            except Exception:
                pass
            self._consent_answer = None
            self._consent_edited_text = ''
            self._consent_draft_edit = None
            self._consent_chrome_painted = False
            self._consent_draft_meta = {
                'draft_field': str(draft_field or 'message'),
                'apply_to_step': str(apply_to_step or ''),
            }
            pid = str(project_id or self._project_board_id or '')
            consent = {
                'title': str(title or 'Neury needs permission'),
                'message': str(message or 'Allow this step?'),
            }
            if str(draft_text or '').strip():
                consent['draft_text'] = str(draft_text).strip()
            if str(preview_kind or '').strip():
                consent['preview_kind'] = str(preview_kind).strip()
            if str(website_name or '').strip():
                consent['website_name'] = str(website_name).strip()
            if str(apply_to_step or '').strip():
                consent['apply_to_step'] = str(apply_to_step).strip()
            self._pending_consent = dict(consent)
            if pid:
                self._project_board_mode = True
                self._project_board_id = pid
                self._emit_project_board(pid, consent=consent)
            try:
                self.consent_needed.emit(
                    str(title or 'Neury needs permission'),
                    str(message or 'Allow this step?'),
                )
            except Exception:
                return False
            while not self._consent_ev.wait(0.25):
                if self._neury_cancel:
                    return False
            ok = self._consent_to_bool(self._consent_answer)
            if ok and str(draft_text or '').strip():
                edited = str(getattr(self, '_consent_edited_text', '') or '').strip() or str(draft_text).strip()
                self._apply_consent_draft_to_project(
                    pid, self._consent_draft_meta, edited,
                )
            return ok

        return ask_fn

    def _project_ui_fn(self):
        """Run UI-thread tools (e.g. article.draft_from_news) on the GUI thread."""

        def ui_fn(name, args):
            if self._neury_cancel:
                return {'ok': False, 'error': 'aborted'}
            try:
                self._ui_tool_ev.clear()
            except Exception:
                pass
            self._ui_tool_result = None
            try:
                self.ui_tool_needed.emit(str(name or ''), dict(args or {}))
            except Exception:
                return {'ok': False, 'error': 'ui dispatch failed'}
            while not self._ui_tool_ev.wait(0.25):
                if self._neury_cancel:
                    return {'ok': False, 'error': 'aborted'}
            res = self._ui_tool_result
            return res if isinstance(res, dict) else {'ok': False, 'error': 'ui timeout'}

        return ui_fn

    def _on_ui_tool_needed(self, name, args):
        """GUI-thread executor for project tools that must touch widgets."""
        result = {'ok': False, 'error': 'ui failed'}
        try:
            from Support.NeuronNeuryAgent import _exec_tool
            # Stay inside Conductor — do not pop World News over the front door.
            use_args = dict(args or {})
            if str(name or '') == 'article.draft_from_news':
                use_args['open_ui'] = False
            try:
                self.progress_line.emit('Running: %s…' % (name or 'tool'))
            except Exception:
                pass
            result = _exec_tool(str(name or ''), use_args)
            if not isinstance(result, dict):
                result = {'ok': False, 'error': 'bad ui result'}
            # Show draft review on the Conductor board (Conductor keeps focus).
            if (
                str(name or '') == 'article.draft_from_news'
                and result.get('ok')
                and getattr(self, '_project_board_mode', False)
            ):
                try:
                    self.project_board.emit({
                        'project_id': self._project_board_id,
                        'article_review': {
                            'title': str(result.get('title') or ''),
                            'subtitle': str(result.get('subtitle') or ''),
                            'preview_text': str(result.get('preview_text') or ''),
                            'article_id': str(result.get('article_id') or ''),
                        },
                    })
                except Exception:
                    pass
        except Exception as exc:
            result = {'ok': False, 'error': str(exc)[:200]}
        self._ui_tool_result = result
        try:
            self._ui_tool_ev.set()
        except Exception:
            pass

    def _run_project_with_confirms(self, project_id, ask_fn=None, status_fn=None, run_id=None):
        """Run project steps; Yes/No sits on the step that needs consent."""
        from Support.NeuronNeuryAgent import _run_project
        from Support.NeuronOrchestrator import resume_project_after_confirm, project_status

        pid = str(project_id or '')
        owned = int(run_id if run_id is not None else getattr(self, '_neury_run_id', 0) or 0)

        def _stale():
            return (
                bool(self._neury_cancel)
                or int(getattr(self, '_neury_run_id', 0) or 0) != owned
            )

        self._project_board_mode = True
        self._project_board_id = pid
        ask = ask_fn or self._project_ask_fn(pid)
        status = status_fn or self._project_status_fn(pid, run_id=owned)
        ui_fn = self._project_ui_fn()
        # Always push concrete steps (never an empty list) so the board cannot go blank.
        try:
            boot_steps = (project_status(pid) or {}).get('steps') or []
        except Exception:
            boot_steps = []
        if boot_steps:
            self._emit_project_board(pid, steps=boot_steps, run_id=owned)
        else:
            self._emit_project_board(pid, run_id=owned)
        if _stale():
            return {'ok': False, 'error': 'superseded', 'summary': {'status': 'cancelled', 'id': pid}}
        result = _run_project(
            pid,
            ask_fn=ask,
            status_fn=status,
            should_abort=_stale,
            ui_fn=ui_fn,
        )
        while not _stale():
            summary = (result or {}).get('summary') or {}
            st = str(summary.get('status') or '')
            steps = (result or {}).get('steps')
            if st != 'awaiting_confirm':
                self._emit_project_board(pid, steps=steps)
                break
            pid = str(summary.get('id') or pid)
            # Prefer the confirm step's own short question + editable draft.
            confirm_msg = 'Publish it?'
            draft_text = ''
            draft_field = 'message'
            apply_to_step = ''
            preview_kind = ''
            website_name = ''
            for step in (steps or []):
                if str(step.get('status') or '') == 'awaiting':
                    args = step.get('args') if isinstance(step.get('args'), dict) else {}
                    confirm_msg = str(
                        args.get('message')
                        or step.get('label')
                        or confirm_msg
                    )
                    draft_text = str(args.get('draft_text') or '').strip()
                    draft_field = str(args.get('draft_field') or 'message')
                    apply_to_step = str(args.get('apply_to_step') or '')
                    preview_kind = str(args.get('preview_kind') or '').strip()
                    website_name = str(args.get('website_name') or '').strip()
                    break
            # Do not paint WAITING without Yes/No — ask() owns the consent chrome.
            if preview_kind == 'website' and not website_name:
                try:
                    from Support.NeuronOrchestrator import project_status as _ps
                    _st = _ps(pid) or {}
                    _proj = _st.get('project') or {}
                    _ctx = _proj.get('context') if isinstance(_proj.get('context'), dict) else {}
                    website_name = str(
                        _ctx.get('website_name') or _ctx.get('name') or ''
                    ).strip()
                    if not website_name and apply_to_step:
                        for _s in (_st.get('steps') or _proj.get('steps') or []):
                            if str(_s.get('id') or '') != apply_to_step:
                                continue
                            _a = _s.get('args') if isinstance(_s.get('args'), dict) else {}
                            cand = str(_a.get('name') or _a.get('website') or '').strip()
                            if cand and not cand.startswith('$'):
                                website_name = cand
                            break
                except Exception:
                    pass
            ok = ask(
                'Continue this project?',
                confirm_msg,
                draft_text=draft_text,
                draft_field=draft_field,
                apply_to_step=apply_to_step,
                preview_kind=preview_kind,
                website_name=website_name,
            )
            if _stale():
                break
            from Support.NeuronOrchestrator import decline_project_confirm
            if not ok:
                declined = decline_project_confirm(pid)
                if not declined.get('ok'):
                    break
                if declined.get('draft_kept') or declined.get('article_id'):
                    try:
                        self.progress_line.emit(
                            'Publish skipped — draft kept in My Articles.'
                        )
                    except Exception:
                        pass
                    rev = getattr(self, '_project_article_review', None) or {}
                    if declined.get('article_id') and not rev.get('article_id'):
                        rev = dict(rev)
                        rev['article_id'] = declined.get('article_id')
                        rev['draft_kept'] = True
                        self._project_article_review = rev
            else:
                resumed = resume_project_after_confirm(pid)
                if not resumed.get('ok'):
                    break
            self._emit_project_board(pid, run_id=owned)
            result = _run_project(
                pid,
                ask_fn=ask,
                status_fn=status,
                should_abort=_stale,
                ui_fn=ui_fn,
            )
        if not _stale():
            self._emit_project_board(pid, steps=(result or {}).get('steps'), run_id=owned)
        return result

    def _ask_neury_async(self, question, title='Neury'):
        # Single-flight: bump run id so any prior conductor_neury thread dies
        # (two overlapping projects were fighting over the board).
        self._neury_cancel = True
        try:
            self._consent_ev.set()
            self._ui_tool_ev.set()
        except Exception:
            pass
        try:
            from Support.NeuronOrchestrator import cancel_project
            cancel_project('')
        except Exception:
            pass
        self._neury_run_id = int(getattr(self, '_neury_run_id', 0) or 0) + 1
        run_id = int(self._neury_run_id)
        self._neury_cancel = False
        self._last_produced = []
        self._set_busy(
            title,
            'Working on this machine…',
            'Answer stays local unless you later choose to share.',
        )
        self.lbl_stage.setText(question[:160] + ('…' if len(question) > 160 else ''))
        q = question
        t = title
        # Seed thread memory for Conductor replies (follow-ups keep context).
        if not getattr(self, '_neury_thread_question', ''):
            self._neury_thread_question = q
        files = list(self._pending_files or [])
        long_job = False
        project_job = False
        mesh_assist = False
        try:
            from Support.NeuronNeuryAgent import _wants_creative_rewrite, neury_analyze_ask
            from Support.NeuronOrchestrator import wants_project_mode, is_website_build_prompt
            from Support.NeuronAIMesh import allow_escalate_outbound, escalate_needs_permission
            emotional_ask = False
            try:
                from Support.NeuronNeuryPsychology import neury_is_emotional_support_prompt
                emotional_ask = bool(neury_is_emotional_support_prompt(q))
            except Exception:
                emotional_ask = False
            # Loneliness / emotional asks must never become website builds.
            project_job = (not emotional_ask) and bool(
                wants_project_mode(q) or is_website_build_prompt(q)
            )
            long_job = bool(files) or _wants_creative_rewrite(q) or project_job
            # Complex asks + user allowed peer help (Auto) -> longer wall for mesh polish.
            plan0 = neury_analyze_ask(q, attachments=files) or {}
            coach0 = plan0.get('coach') if isinstance(plan0.get('coach'), dict) else {}
            complex_ask = (not emotional_ask) and bool(
                plan0.get('escalate_after')
                or coach0.get('escalate_hint')
                or coach0.get('decision')
                or coach0.get('multi_ask')
                or coach0.get('multi_life')
                or float(coach0.get('difficulty') or 0) >= 0.5
                or str(plan0.get('kind') or '') in ('advice', 'research')
            )
            mesh_assist = bool(
                complex_ask
                and allow_escalate_outbound()
                and not escalate_needs_permission()
            )
        except Exception:
            long_job = bool(files)
            mesh_assist = False
            emotional_ask = False
        if project_job:
            wait_sec = _CONDUCTOR_NEURY_PROJECT_TIMEOUT_SEC
        elif long_job:
            wait_sec = _CONDUCTOR_NEURY_LONG_TIMEOUT_SEC
        elif emotional_ask:
            # Local emotional support — do not sit on the 3-minute mesh wall.
            wait_sec = min(int(_CONDUCTOR_NEURY_TIMEOUT_SEC), 55)
        elif mesh_assist:
            wait_sec = _CONDUCTOR_NEURY_MESH_TIMEOUT_SEC
        else:
            wait_sec = _CONDUCTOR_NEURY_TIMEOUT_SEC
        box = {
            'reply': '', 'done': False, 'trust': '', 'need_account_choice': False,
            'accounts': [], 'pending_mail': {}, 'files': [], 'web_cards': [],
            'used_web_fallback': False, 'stage': '', 'open_calendar': False,
            'open_mail': False, 'long_job': long_job, 'project_job': project_job,
            'triplex': None, 'run_id': run_id,
        }
        def _alive():
            return (
                not bool(self._neury_cancel)
                and int(getattr(self, '_neury_run_id', 0) or 0) == run_id
            )

        def _stage(label):
            if not _alive():
                return
            box['stage'] = str(label or '')
            try:
                self.progress_line.emit(str(label or ''))
            except Exception:
                pass

        def work():
            try:
                # Same analyze-then-answer owner as Neury chat (no Conductor-only fork).
                from Support.NeuronNeuryAgent import (
                    neury_resolve_ask,
                    _format_project_reply,
                )
                from Support.NeuronOrchestrator import create_project

                if not _alive():
                    box['reply'] = '[Stopped]'
                    box['done'] = True
                    return

                # Trip / website projects: build the step board immediately.
                # Do NOT wait on neury_resolve_ask (that was the ~30s blank shell).
                resolved = {}
                if project_job or box.get('project_job'):
                    resolved = {'defer_project': True}
                else:
                    # Finish before the UI timeout wall (leave a few seconds of slack).
                    resolved = neury_resolve_ask(
                        q,
                        attachments=files,
                        stage_cb=_stage,
                        should_abort=lambda: not _alive(),
                        spoken=False,
                        budget_sec=max(25.0, float(wait_sec) - 8.0),
                    ) or {}

                if not _alive():
                    box['reply'] = '[Stopped]'
                    box['done'] = True
                    return

                # Conductor owns project confirm UI; resolver only flags defer_project.
                if resolved.get('defer_project'):
                    box['project_job'] = True
                    _stage('Planning website / project with Neury…')
                    # Paint a real board shell on the UI thread right away.
                    try:
                        self.progress_line.emit('Building your step board…')
                    except Exception:
                        pass
                    from Support.NeuronOrchestrator import save_project
                    from Support.NeuronNeuryAskComposer import seed_attachments_fast
                    import Support.NeuronNeuryAgentHelpers as _neury_helpers
                    # Sandbox allows user-attached paths for this turn + project context.
                    _neury_helpers._turn_attachments = list(files or [])
                    created = create_project(q, attachments=list(files or []) or None)
                    if not created.get('ok'):
                        box['reply'] = created.get('error') or 'Could not start that project.'
                        box['trust'] = 'Neury project planner.'
                        box['done'] = True
                        return
                    if not _alive():
                        try:
                            from Support.NeuronOrchestrator import cancel_project as _cx
                            _cx(str((created.get('project') or {}).get('id') or ''))
                        except Exception:
                            pass
                        box['reply'] = '[Stopped]'
                        box['done'] = True
                        return
                    project = created.get('project') or {}
                    if files:
                        # Instant seed — never stall Sort on isfile/MTP.
                        seed_attachments_fast(project, files)
                        try:
                            save_project(project)
                        except Exception:
                            pass
                    # Ship concrete steps immediately — never sit on a blank board.
                    try:
                        self._project_board_mode = True
                        self._project_board_id = str(project.get('id') or '')
                        boot = list(project.get('steps') or [])
                        self._emit_project_board(
                            project.get('id') or '',
                            steps=boot if boot else None,
                            run_id=run_id,
                        )
                    except Exception:
                        pass
                    result = self._run_project_with_confirms(
                        project.get('id') or '', run_id=run_id,
                    )
                    if not _alive():
                        box['reply'] = '[Stopped]'
                        box['done'] = True
                        return
                    summary = (result or {}).get('summary') or {}
                    st = str(summary.get('status') or '')
                    if st == 'completed':
                        box['reply'] = _format_project_reply(result) if result else 'All steps finished.'
                        paid_peers = int((result or {}).get('paid_peers') or 0)
                        paid_amount = float((result or {}).get('paid_amount') or 0)
                        if paid_peers > 0:
                            box['trust'] = 'Finished with mesh help from %s Neury%s.' % (
                                paid_peers, 's' if paid_peers != 1 else '',
                            )
                            try:
                                dlg = getattr(init, 'ai_dialog', None)
                                if dlg is not None and hasattr(dlg, 'notify_neuron_payment') and paid_amount > 0:
                                    dlg.notify_neuron_payment(
                                        'Paid %.4g Neurons to %s Neury%s for mesh help.' % (
                                            paid_amount, paid_peers,
                                            's' if paid_peers != 1 else '',
                                        ),
                                        'Neury mesh',
                                        paid_amount,
                                        'Project mesh work',
                                    )
                            except Exception:
                                pass
                        else:
                            box['trust'] = 'Built on this machine with Neury (same as chat / website builder).'
                    elif st in ('failed', 'blocked'):
                        box['reply'] = _format_project_reply(result)
                        box['trust'] = 'Stopped on a failed step — tap Try another to go home.'
                    elif st == 'cancelled' or (result or {}).get('error') == 'superseded':
                        box['reply'] = '[Stopped]'
                        box['trust'] = 'Stopped.'
                    else:
                        box['reply'] = _format_project_reply(result) if result else 'Job finished.'
                        box['trust'] = 'Built on this machine with Neury (same as chat / website builder).'
                    box['done'] = True
                    return

                if not _alive():
                    box['reply'] = '[Stopped]'
                    box['done'] = True
                    return

                box['reply'] = str(resolved.get('text') or '').strip()
                box['trust'] = str(resolved.get('trust') or '').strip() or 'Answered with Neury.'
                box['files'] = list(resolved.get('files') or [])
                box['web_cards'] = list(resolved.get('cards') or [])
                box['open_mail'] = bool(resolved.get('open_mail'))
                box['open_calendar'] = bool(resolved.get('open_calendar'))
                if resolved.get('need_account_choice'):
                    box['need_account_choice'] = True
                    box['accounts'] = list(resolved.get('accounts') or [])
                    box['pending_mail'] = dict(resolved.get('pending_mail') or {})
                if resolved.get('paid_peers'):
                    try:
                        dlg = getattr(init, 'ai_dialog', None)
                        if dlg is not None and hasattr(dlg, 'notify_neuron_payment'):
                            dlg.notify_neuron_payment(
                                'Paid %.4g Neurons to %s peer%s for mesh help.' % (
                                    float(resolved.get('paid_amount') or 0),
                                    int(resolved.get('paid_peers') or 0),
                                    's' if int(resolved.get('paid_peers') or 0) != 1 else '',
                                ),
                                'peer(s)',
                                float(resolved.get('paid_amount') or 0),
                                'AI mesh work',
                            )
                    except Exception:
                        pass
                if not box['reply']:
                    try:
                        from Support.NeuronNeuryAgentChat import neury_clarify_when_unsure
                        box['reply'] = neury_clarify_when_unsure(q, kind=str((resolved or {}).get('plan', {}) or {}).get('kind') or 'advice') or ''
                    except Exception:
                        box['reply'] = ''
                    if not box['reply']:
                        box['reply'] = 'I want to help, but I need one more detail. What matters most to you here?'
                    box['trust'] = box.get('trust') or 'Asked for clarification.'
            except Exception as e:
                try:
                    from Support.NeuronNeuryAgentChat import neury_clarify_when_unsure
                    box['reply'] = neury_clarify_when_unsure(q, kind='advice') or ''
                except Exception:
                    box['reply'] = ''
                if not box['reply']:
                    box['reply'] = 'I hit a snag (%s). Tell me the one thing you need most right now?' % e
            box['done'] = True

        # Project trips: show the board chrome immediately (create_project fills steps next).
        if project_job:
            try:
                self._show_project_planning_shell()
                self.btn_run.hide()
                self.btn_add_file.hide()
                self.btn_abort.show()
            except Exception:
                pass

        threading.Thread(target=work, name='conductor_neury', daemon=True).start()

        started = time.time()

        def poll():
            if self._fading_out:
                self._neury_cancel = True
                return
            # A newer ask superseded this poll loop — go quiet.
            if int(box.get('run_id') or 0) != int(getattr(self, '_neury_run_id', 0) or 0):
                return
            if box['done']:
                self._stop_elapsed_timer()
                if box.get('open_calendar'):
                    try:
                        tb = getattr(init, 'taskbar', None)
                        if tb is not None and hasattr(tb, 'open_calendar_dialog'):
                            # Already on Qt UI thread (QTimer poll).
                            tb.open_calendar_dialog()
                    except Exception:
                        pass
                if box.get('open_mail'):
                    try:
                        from Support.Menu import open_dialog
                        open_dialog('Mail')
                    except Exception:
                        pass
                reply = str(box['reply'] or '').strip()
                if not reply:
                    try:
                        from Support.NeuronNeuryAgentChat import neury_clarify_when_unsure
                        reply = neury_clarify_when_unsure(q, kind='advice') or ''
                    except Exception:
                        reply = ''
                    if not reply:
                        reply = 'I want to help, but I need one more detail. What matters most to you here?'
                produced = list(box.get('files') or [])
                if produced:
                    self._last_produced = produced
                    names = ', '.join(os.path.basename(p) for p in produced[:4])
                    reply = '%s\n\nReady: %s' % (reply, names)
                body = reply
                if box.get('need_account_choice'):
                    self._offer_mail_accounts(
                        q, 'You asked:\n%s\n\n%s' % (q, reply),
                        box.get('pending_mail') or {}, box.get('accounts') or [],
                    )
                    return
                self._pending_neury_meta = {
                    'trust': box.get('trust') or '',
                    'web_cards': list(box.get('web_cards') or []),
                    'offer_save': bool(produced),
                    'triplex': box.get('triplex'),
                    'question': q,
                    'reply': reply,
                    'run_id': run_id,
                }
                self.neury_done.emit(t, body, self._pending_neury_meta)
                return
            if getattr(self, '_awaiting_consent', False):
                QTimer.singleShot(400, poll)
                return
            elapsed = time.time() - started
            if elapsed >= wait_sec:
                # Multi-step projects must not be aborted by the UI wall clock —
                # that left "Completed" with half the steps still PENDING.
                if box.get('project_job'):
                    try:
                        mins, secs = divmod(int(elapsed), 60)
                        self.lbl_stage.setText(_tf('Still working… %s:%02d', mins, secs))
                        self.lbl_stage.show()
                        self.trust.setText(_t('Long job — leave Neuron open. Tap Stop to cancel.'))
                        self.trust.show()
                        self.btn_abort.show()
                    except Exception:
                        pass
                    QTimer.singleShot(1000, poll)
                    return
                self._neury_cancel = True
                self._stop_elapsed_timer()
                try:
                    self._consent_ev.set()
                except Exception:
                    pass
                try:
                    self._ui_tool_ev.set()
                except Exception:
                    pass
                if box.get('long_job'):
                    meta = {
                        'trust': 'Still working locally — no web search.',
                        'web_cards': [],
                        'offer_save': False,
                        'run_id': run_id,
                    }
                    self._pending_neury_meta = meta
                    self.neury_done.emit(
                        t,
                        'You asked:\n%s\n\nTimed out after %ss while rewriting your file.\n'
                        'Leave Neuron open and try again — a full scene can take a couple of minutes.'
                        % (q, wait_sec),
                        meta,
                    )
                    return
                clarify = ''
                try:
                    from Support.NeuronNeuryPsychology import (
                        neury_is_emotional_support_prompt,
                        neury_emotional_provisional_reply,
                    )
                    if neury_is_emotional_support_prompt(q):
                        clarify = neury_emotional_provisional_reply(q) or ''
                except Exception:
                    clarify = ''
                if not clarify:
                    try:
                        from Support.NeuronNeuryAgentChat import neury_clarify_when_unsure
                        clarify = neury_clarify_when_unsure(q, kind='advice') or ''
                    except Exception:
                        clarify = ''
                if not clarify:
                    clarify = (
                        'I want to help, but I ran out of time before a full answer. '
                        'Open the desktop and talk to Neury for a longer chat, '
                        'or type a shorter follow-up here.'
                    )
                meta = {
                    'trust': 'Answered after a slow turn.',
                    'web_cards': list(box.get('web_cards') or []),
                    'offer_save': False,
                    'question': q,
                    'reply': clarify,
                    'run_id': run_id,
                }
                self._pending_neury_meta = meta
                self.neury_done.emit(t, clarify, meta)
                return
            QTimer.singleShot(400, poll)

        QTimer.singleShot(400, poll)

    def _on_neury_done(self, title, body, meta=None):
        if self._fading_out:
            return
        try:
            info = meta if isinstance(meta, dict) else (getattr(self, '_pending_neury_meta', None) or {})
            # Drop completion from a superseded run (prevents blank→second board fight).
            try:
                rid = info.get('run_id')
                if rid is not None and int(rid) != int(getattr(self, '_neury_run_id', 0) or 0):
                    return
            except Exception:
                pass
            trust = str(info.get('trust') or '').strip()
            cards = list(info.get('web_cards') or [])
            triplex = info.get('triplex') if isinstance(info.get('triplex'), dict) else None
            offer_save = bool(info.get('offer_save')) or bool(self._last_produced)
            low = (body or '').lower()
            if not trust:
                trust = 'Answered on your machine.'
                if offer_save:
                    trust = 'File ready — use Save results to keep a copy.'
                elif 'sent from' in low or 'emailed' in low or 'i sent your message' in low:
                    trust = 'Action ran on this machine.'
                elif 'could not send' in low or 'mailbox' in low or 'smtp' in low or 'nothing was sent' in low:
                    trust = 'Nothing was sent — fix mail settings and try again.'
                elif cards:
                    trust = 'From the live web.'
            # Remember Neury thread so the user can reply on Conductor.
            asked = str(info.get('question') or getattr(self, '_neury_thread_question', '') or '').strip()
            reply_only = str(info.get('reply') or body or '').strip()
            if reply_only.startswith('You asked:') and '\n\n' in reply_only:
                reply_only = reply_only.split('\n\n', 1)[-1].strip()
            if asked:
                self._neury_thread_question = asked
            self._neury_thread_reply = reply_only
            is_neury = str(title or '').strip().lower() in ('neury', 'ask neury')
            display_body = body or reply_only
            if is_neury and (not offer_save) and (not triplex):
                block = 'You:\n%s\n\nNeury:\n%s' % (asked or '…', reply_only or '')
                prev = str(getattr(self, '_neury_transcript', '') or '').strip()
                if prev and getattr(self, '_neury_reply_mode', False):
                    self._neury_transcript = prev + '\n\n' + block
                else:
                    self._neury_transcript = block
                display_body = self._neury_transcript
                try:
                    from Support.NeuronNeuryAgentMemory import neury_remember_chat_turn
                    neury_remember_chat_turn(asked, reply_only)
                except Exception:
                    pass
                try:
                    from Support.NeuronNeuryPsychology import neury_note_last_emotional_reply
                    neury_note_last_emotional_reply(reply_only)
                except Exception:
                    pass
            self._show_stay_result(
                title, display_body, trust, offer_save=offer_save, web_cards=cards,
                triplex=triplex, allow_neury_reply=is_neury and (not offer_save),
            )
        except Exception as e:
            self.lbl_stage.setText(_tf('Could not show answer: %s', e))
            self.btn_run.setEnabled(True)
            self._set_run_label(_t('Try another'))
            try:
                self.btn_run.clicked.disconnect()
            except Exception:
                pass
            self.btn_run.clicked.connect(self._back_to_chooser)

    def _neury_followup_from_input(self):
        """Continue a Neury thread on Conductor — same memory path as desktop Neury."""
        follow = self._input_text().strip()
        if not follow:
            try:
                self.input.setFocus()
            except Exception:
                pass
            return
        # Chat memory already holds prior turns; send only the new line.
        self._neury_thread_question = follow
        self._neury_reply_mode = True
        self._ask_neury_async(follow, 'Neury')

    def _clear_mail_account_btns(self):
        for btn in list(getattr(self, '_mail_account_btns', None) or []):
            try:
                btn.setParent(None)
                btn.deleteLater()
            except Exception:
                pass
        self._mail_account_btns = []
        try:
            self._mail_pick_wrap.hide()
        except Exception:
            pass

    def _offer_mail_accounts(self, question, body, pending, accounts):
        self._pending_mail = dict(pending or {})
        self._pending_mail['_question'] = question
        self._show_stay_result('Choose mailbox', body, 'Pick which account should send this.')
        self.btn_run.hide()
        self._clear_mail_account_btns()
        while self._mail_pick_lay.count():
            item = self._mail_pick_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._mail_pick_lay.addStretch(1)
        for account in accounts:
            email = str(account.get('email') or '').strip()
            aid = str(account.get('id') or '').strip()
            if not email or not aid:
                continue
            btn = QPushButton(email)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(40)
            btn.setStyleSheet(
                "QPushButton { color:#041018; background:#7ad7ef; border:none;"
                " border-radius:18px; font-size:12px; font-weight:700; padding:8px 14px; }"
                "QPushButton:hover { background:#9aebff; }"
            )
            btn.clicked.connect(lambda checked=False, a=aid: self._send_pending_mail(a))
            self._mail_pick_lay.addWidget(btn)
            self._mail_account_btns.append(btn)
        self._mail_pick_lay.addStretch(1)
        self._mail_pick_wrap.show()

    def _send_pending_mail(self, account_id):
        pending = dict(getattr(self, '_pending_mail', None) or {})
        question = pending.pop('_question', '')
        if not pending:
            self._show_stay_result('Email', 'Nothing left to send. Try again.')
            return
        pending['account_id'] = account_id
        self._clear_mail_account_btns()
        self.btn_run.show()
        self._set_busy('Sending email', 'Using the mailbox you chose…')

        box = {'reply': '', 'done': False}

        def work():
            try:
                from Support.NeuronNeuryAgent import exec_tool_for_ui
                res = exec_tool_for_ui('mail.send', pending) or {}
                from Support.NeuronNeuryAgent import _format_tool_result
                text = _format_tool_result('mail.send', res) if isinstance(res, dict) else ''
                if not text:
                    if res.get('ok'):
                        text = 'Sent.'
                    else:
                        text = 'I could not send that email: %s' % (res.get('error') or 'send failed')
                if question:
                    box['reply'] = 'You asked:\n%s\n\n%s' % (question, text)
                else:
                    box['reply'] = text
            except Exception as e:
                box['reply'] = 'I could not send that email (%s).' % e
            box['done'] = True

        threading.Thread(target=work, name='conductor_mail', daemon=True).start()

        def poll():
            if self._fading_out:
                return
            if box['done']:
                self.neury_done.emit('Email', str(box['reply'] or ''), {})
                return
            QTimer.singleShot(300, poll)

        QTimer.singleShot(300, poll)

    def _start_ask_neury(self):
        if not self._ai_ready:
            self._start_install_neury()
            return
        question = str(getattr(self, '_pending_question', '') or '').strip()
        if not question:
            question = self._input_text().strip()
        self._pending_question = ''
        if question in self._chip_label_set() or question.lower() in ('ask neury', 'install neury'):
            question = ''

        if not question:
            self._show_stay_result(
                'Ask Neury',
                'Tell Neuron what you want to do in the box, then Go.\n'
                'Neury answers here on your machine.',
            )
            return

        self._ask_neury_async(question, 'Neury')

    def _start_install_neury(self, pending_question=''):
        saved = (pending_question or '').strip()
        if cpower.neury_installed():
            self._rebuild_capability_ui(force=True)
            self._show_stay_result('Neury is ready', 'Ask anything from the home screen.')
            return
        self._set_busy(
            'Installing Neury',
            'Downloading and setting up local AI on this machine…',
            'AI stays on your computer. Nothing is uploaded for this install.',
        )
        if saved:
            self.lbl_stage.setText(_tf('After install I can answer: %s', saved[:120]))
        self._pending_question = saved

        def _prog(n):
            self.install_progress.emit(int(n or 0))

        def _stat(s):
            self.install_status.emit(str(s or ''))

        def _done():
            self.install_finished.emit()

        def _fail(err):
            self.install_failed.emit(str(err or 'Install failed'))

        worker = cpower.start_neury_install(
            on_progress=_prog,
            on_status=_stat,
            on_done=_done,
            on_fail=_fail,
        )
        self._install_worker = worker
        if worker is None:
            self._show_stay_result(
                'Install Neury',
                'Could not start install automatically.\n'
                'Open desktop → AI settings if you need the full installer.',
            )

    def _on_install_progress(self, pct):
        self.lbl_stage.setText(_tf('Progress: %s%%', int(pct)))

    def _on_install_status(self, status):
        self.lbl_sub.setText(str(status or ''))
        self.lbl_sub.show()

    def _on_install_finished(self):
        self._install_worker = None
        try:
            from Support.NeuronAIInstaller import invalidate_ai_installed_cache, refresh_ai_taskbar_icon
            invalidate_ai_installed_cache()
            refresh_ai_taskbar_icon()
        except Exception:
            pass
        self._rebuild_capability_ui(force=True)
        q = (self._pending_question or '').strip()
        self._pending_question = ''
        if q:
            self._chip = 'ask_neury'
            self._pending_question = q
            self._start_ask_neury()
            return
        self._show_stay_result(
            'Neury is ready',
            'Local AI is installed on this machine.\n'
            'Try Ask Neury, translate, or create a picture — still on this screen.',
        )

    def _on_install_failed(self, err):
        self._install_worker = None
        self._show_stay_result('Install Neury', _tf('Install could not finish:\n%s', err))

    def _start_find_people(self):
        self._set_busy('Who is here?', 'Looking at rooms on your node…')

        def after():
            res = cpower.tool_who_is_here()
            self._show_stay_result('Who is here?', cpower.format_tool_result(res), _mesh_blurb())

        self._advance_story([
            ('Scanning living rooms', 'No fake crowds — only what is really here.'),
        ], after)

    def _start_mesh_status(self):
        self._set_busy('Your mesh', 'Reading live status…')

        def after():
            res = cpower.tool_mesh_status()
            self._show_stay_result('Your mesh', cpower.format_tool_result(res))

        self._advance_story([('Listening…', '')], after)

    def _start_invite(self):
        self._set_busy('Invite a friend', 'Preparing your invite link…')

        def after():
            res = cpower.tool_invite_copy()
            self._show_stay_result('Invite a friend', cpower.format_tool_result(res))

        self._advance_story([('Growing the mesh', '')], after)

    def _start_learn(self):
        self._set_busy('How it works', '')

        def after():
            self._show_stay_result(
                'How it works',
                'Neuron connects people, computers, and local AI on a peer-to-peer network — '
                'without living inside one company\'s cloud.\n\n'
                'Use Work on files, Connect, and Discover from this screen.\n'
                'Open desktop only when you want the full environment.',
            )

        self._advance_story([('The simple version', '')], after)

    def _refresh_drop_label(self):
        n = len(self._pending_files or [])
        if n <= 0:
            self.drop_zone.setText(_t('No files yet — use + Add file'))
            return
        names = [os.path.basename(p) for p in self._pending_files[:4]]
        extra = '' if n <= 4 else ' (+%s more)' % (n - 4)
        self.drop_zone.setText(_tf('%s file(s): %s%s', n, ', '.join(names), extra))

    def _browse_job_files(self):
        meta = self._hive_job_meta or {}
        filt = meta.get('file_filter') or 'All files (*.*)'
        paths, _ = self._open_files_dialog(
            meta.get('file_label') or 'Select files',
            '',
            filt,
        )
        if paths:
            for p in paths:
                if p not in self._pending_files:
                    self._pending_files.append(p)
            self._sync_add_file_button()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData() else []
        added = False
        for url in urls or []:
            path = url.toLocalFile()
            if path and os.path.isfile(path) and path not in self._pending_files:
                self._pending_files.append(path)
                added = True
        if added:
            self._sync_add_file_button()
            event.acceptProposedAction()
        else:
            event.ignore()

    def _prompt_missing_or_abort(self, title, message, placeholder=''):
        """Ask the user to fill missing data or abort. Returns True if they chose Continue."""
        from PySide6.QtWidgets import QMessageBox
        self._phase = 'idle'
        self.btn_run.setEnabled(True)
        self._set_run_label(_t('Go'))
        try:
            self.btn_run.clicked.disconnect()
        except Exception:
            pass
        self.btn_run.clicked.connect(self.run_selected)
        self.lbl_q.setText(_t(title or 'More information needed'))
        self.lbl_sub.hide()
        self.lbl_stage.setText(_t('Waiting for your input'))
        body = str(message or 'Something is missing.')
        self.result.setPlainText(body)
        self.result.show()
        self._fit_result_height()
        self.input.show()
        self.input.setPlaceholderText(_t(placeholder or 'Type here…'))
        self._focus_input()
        self.lbl_prompt.setText(_t('Fill in the details, then Go — or choose Abort.'))
        self.lbl_prompt.show()
        box = QMessageBox(self)
        box.setWindowTitle(_t(title or 'Missing information'))
        box.setText(body[:600] + ('…' if len(body) > 600 else ''))
        box.setInformativeText(_t('Continue to type the missing details, or Abort this job.'))
        cont = box.addButton(_t('Continue'), QMessageBox.ButtonRole.AcceptRole)
        abort = box.addButton(_t('Abort'), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cont)
        box.exec()
        if box.clickedButton() == abort:
            self._show_stay_result(
                title or 'Aborted',
                'Cancelled — nothing was changed.',
                trust='Aborted by you.',
            )
            return False
        self.btn_again.show()
        self.btn_save.hide()
        return True

    def _start_hive_job(self, job_id):
        jid = str(job_id or '').strip()
        jobs = {j['id']: j for j in cpower.list_hive_jobs(include_neury_gated=True)}
        meta = jobs.get(jid) or self._hive_job_meta or {}
        self._hive_job_id = jid
        self._hive_job_meta = meta
        if meta.get('needs_neury') and not self._ai_ready:
            self._start_install_neury()
            return
        instruction = self._input_text().strip()
        if meta.get('needs_files') and not self._pending_files:
            if not self._prompt_missing_or_abort(
                str(meta.get('title') or jid),
                'Add at least one file with + Add file, then press Go.\n'
                'Or Abort to cancel.',
                meta.get('file_label') or 'Add files…',
            ):
                return
            self._set_drop_visible(True)
            self.lbl_prompt.setText(_t('Add at least one file with + Add file, then Go.'))
            return
        # Jobs that need a written instruction / prompt
        needs_words = jid in ('translate', 'pictures', 'databases')
        if jid == 'voiceover' and not instruction and not self._pending_files:
            needs_words = True
        if needs_words and not instruction:
            hints = {
                'translate': 'Describe what to translate (and into which language).',
                'pictures': 'Describe the picture you want.',
                'databases': 'Describe the question or paste SQL.',
                'voiceover': 'Paste or describe the text to speak (or add a text file).',
            }
            if not self._prompt_missing_or_abort(
                str(meta.get('title') or jid),
                hints.get(jid, 'Describe what you want, then Go — or Abort.'),
                meta.get('placeholder') or 'Describe what you want…',
            ):
                return
            return
        # Prompt to download missing batch tools (Blender, Tesseract, Piper, …)
        self.lbl_stage.setText(_t('Checking required apps…'))
        prep = cpower.ensure_job_tools_ready(jid, parent=self)
        if not prep.get('ok'):
            self._show_stay_result(
                str(meta.get('title') or jid),
                str(prep.get('message') or 'Required apps are not installed yet.'),
                trust='Install declined or failed.',
            )
            return
        title = str(meta.get('title') or jid)
        self._set_busy(
            title,
            'Working on this machine…',
            str(meta.get('tool_note') or 'Results stay local until you save or share them.'),
        )
        self.lbl_stage.setText(_t('Starting…'))
        files = list(self._pending_files)
        outputs = list(meta.get('outputs') or ['txt'])
        fmt = outputs[0] if outputs else 'txt'
        instr = instruction

        def work():
            def prog(pct, msg=''):
                self.job_progress.emit(int(pct or 0), str(msg or ''))

            result = cpower.run_hive_job_local(
                jid,
                files,
                instr,
                export_format=fmt,
                progress_fn=prog,
            )
            self.job_finished.emit(result if isinstance(result, dict) else {'ok': False, 'message': 'No result'})

        threading.Thread(target=work, name='conductor_hive', daemon=True).start()

    def _on_job_progress(self, pct, msg):
        line = 'Progress: %s%%' % int(pct)
        if msg:
            line += ' — %s' % msg
        self.lbl_stage.setText(line)

    def _on_job_finished(self, result):
        self._last_produced = list((result or {}).get('produced') or [])
        body = cpower.format_hive_result(result or {})
        title = str((self._hive_job_meta or {}).get('title') or 'Job finished')
        if (result or {}).get('ok'):
            self._hive_tool_retry = False
            self._show_stay_result(
                title, body,
                trust='Completed — results stay on this machine until you save or share.',
                offer_save=True,
            )
            return
        err = str((result or {}).get('error') or (result or {}).get('status') or '')
        if err == 'neury_required':
            self._start_install_neury()
            return
        if err == 'need_input':
            msg = str((result or {}).get('message') or body or 'More information is needed.')
            self._prompt_missing_or_abort(title, msg, (self._hive_job_meta or {}).get('placeholder') or '')
            return
        if err == 'tool_missing':
            jid = str(self._hive_job_id or (self._hive_job_meta or {}).get('id') or '')
            self.lbl_stage.setText(_t('Missing app — offering install…'))
            if self._hive_tool_retry:
                self._hive_tool_retry = False
                self._show_stay_result(title, body, trust='Still missing a required app.')
                return
            prep = cpower.ensure_job_tools_ready(jid, parent=self)
            if prep.get('ok'):
                self._hive_tool_retry = True
                self.lbl_stage.setText(_t('App ready — continuing…'))
                self._start_hive_job(jid)
                return
            self._hive_tool_retry = False
            self._show_stay_result(
                title,
                str(prep.get('message') or body),
                trust='Install declined or failed.',
            )
            return
        self._hive_tool_retry = False
        self._show_stay_result(title, body, trust='Could not finish this job.')

    def _clear_project_showcase(self):
        wrap = getattr(self, '_showcase_wrap', None)
        lay = getattr(self, '_showcase_lay', None)
        if lay is None:
            return
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        try:
            wrap.hide()
        except Exception:
            pass

    def _project_showcase_targets(self, steps=None, ctx=None):
        """Buttons to open what this trip produced (Chat / Diary / site / …)."""
        steps = list(steps or [])
        ctx = ctx if isinstance(ctx, dict) else {}
        # tool -> (label, category, item_or_None)
        tool_map = {
            'chat.send': ('Chat', 'Chat', None),
            'diary.post': ('Diary', 'Diary', None),
            'mail.send': ('Email', 'Chat', 'Mail'),
            'websites.publish': ('Website', 'Websites', None),
            'website.build': ('Website', 'Websites', None),
            'article.publish': ('News', 'News', 'WorldNews'),
            'pedia.publish': ('Pedia', 'News', 'NeuronPedia'),
            'public_files.publish': ('Files', 'Public Files', 'Manage'),
            # files.commit_outputs → use Folder button from produced paths (not Manage Public Files).
        }
        seen = set()
        out = []
        for step in steps:
            st = str(step.get('status') or '')
            if st not in ('done', 'failed', 'skipped'):
                continue
            tool = str(step.get('tool') or '')
            meta = tool_map.get(tool)
            if not meta or meta[0] in seen:
                continue
            seen.add(meta[0])
            out.append({
                'label': meta[0],
                'category': meta[1],
                'item': meta[2],
                'tool': tool,
            })
        # Always offer folder when we have produced paths.
        paths = [
            p for p in list(ctx.get('produced') or []) + list(self._last_produced or [])
            if p and os.path.exists(p)
        ]
        if paths and 'Files' not in seen:
            out.append({
                'label': 'Folder',
                'category': '',
                'item': None,
                'tool': 'folder',
                'path': os.path.dirname(paths[0]) if not os.path.isdir(paths[0]) else paths[0],
            })
        return out

    def _open_showcase_target(self, target):
        target = target if isinstance(target, dict) else {}
        if target.get('tool') == 'folder':
            path = str(target.get('path') or '')
            if path and os.path.exists(path):
                try:
                    os.startfile(path)
                except Exception:
                    pass
            return
        cat = str(target.get('category') or '').strip()
        item = target.get('item')
        if not cat:
            return
        try:
            from Support.Menu import open_dialog
            if item:
                open_dialog(cat, item)
            else:
                open_dialog(cat)
        except Exception:
            pass

    def _show_project_showcase(self):
        """End-of-job WOW strip — open Chat / Diary / Website / … results."""
        self._clear_project_showcase()
        pid = str(getattr(self, '_project_board_id', '') or '')
        if not pid:
            return
        steps = []
        ctx = {}
        try:
            from Support.NeuronOrchestrator import project_status
            st = project_status(pid) or {}
            steps = list(st.get('steps') or [])
            proj = st.get('project') or {}
            ctx = proj.get('context') if isinstance(proj.get('context'), dict) else {}
        except Exception:
            return
        targets = self._project_showcase_targets(steps, ctx)
        if not targets:
            return
        tip = QLabel(_t('Open your results:'))
        tip.setStyleSheet(
            'color:#9fe9ff; font-size:12px; font-weight:700; background:transparent;'
        )
        self._showcase_lay.addWidget(tip)
        for t in targets[:8]:
            btn = QPushButton(_t(t['label']))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(32)
            btn.setStyleSheet(
                "QPushButton { color:#041018; background:#7ad7ef; border:none;"
                " border-radius:16px; padding:4px 14px; font-size:12px; font-weight:700; }"
                "QPushButton:hover { background:#9fe9ff; }"
            )
            btn.clicked.connect(
                lambda _=False, tgt=dict(t): self._open_showcase_target(tgt)
            )
            self._showcase_lay.addWidget(btn)
        self._showcase_wrap.show()

    def _save_produced(self):
        paths = [p for p in (self._last_produced or []) if p and os.path.exists(p)]
        if not paths:
            self.lbl_stage.setText(_t('Nothing to save yet.'))
            return
        dest = self._open_dir_dialog('Save results to folder')
        if not dest:
            return
        copied = []
        for src in paths:
            try:
                name = os.path.basename(src)
                target = os.path.join(dest, name)
                base, ext = os.path.splitext(name)
                n = 1
                while os.path.exists(target):
                    target = os.path.join(dest, '%s_%s%s' % (base, n, ext))
                    n += 1
                if os.path.isdir(src):
                    shutil.copytree(src, target)
                else:
                    shutil.copy2(src, target)
                copied.append(target)
            except Exception:
                pass
        if copied:
            self._show_stay_result(
                'Saved',
                'Copied %s item(s) to:\n%s\n\n%s' % (
                    len(copied), dest, '\n'.join('• %s' % os.path.basename(p) for p in copied[:20]),
                ),
                trust='Completed — files copied.',
            )
        else:
            self.lbl_stage.setText(_t('Could not copy results.'))

    def _start_neuron_action(self, action_id):
        aid = str(action_id or '').strip()
        act = cpower.action_by_id(aid) or {}
        if act.get('needs_neury') and not self._ai_ready:
            self._start_install_neury()
            return
        text = self._input_text().strip()
        if act.get('needs_input') and not text and aid != 'retrieve':
            if not self._prompt_missing_or_abort(
                str(act.get('title') or aid),
                act.get('input_placeholder') or 'Type the required details, then Go — or Abort.',
                act.get('input_placeholder') or 'Type here…',
            ):
                return
            return
        title = str(act.get('title') or aid)
        self._set_busy(title, 'Working…')
        self.lbl_stage.setText(_t('Starting…'))

        def work():
            def prog(pct, msg=''):
                self.job_progress.emit(int(pct or 0), str(msg or ''))

            res = cpower.run_neuron_action(aid, text, progress_fn=prog)
            body = cpower.format_tool_result(res)
            self.action_finished.emit(title, body, res if isinstance(res, dict) else {})

        threading.Thread(target=work, name='conductor_act', daemon=True).start()

    def _on_action_finished(self, title, body, result=None):
        res = result if isinstance(result, dict) else {}
        if res.get('error') == 'need_input':
            prompt = str(res.get('prompt') or '')
            msg = str(res.get('text') or body or 'More information is needed.')
            # Prefill category template only when the field is empty
            if (
                prompt
                and not self._input_text().strip()
                and 'Category:' in prompt
            ):
                self._set_input_text(prompt)
            elif prompt:
                self.input.setPlaceholderText(prompt)
            self._prompt_missing_or_abort(title, msg, prompt or 'Type here…')
            return
        if res.get('ok'):
            self._show_stay_result(title, body, trust='Completed.')
        else:
            self._show_stay_result(
                title, body,
                trust=str(res.get('error') or 'Could not finish.'),
            )

    def enter_desktop(self):
        # Unstick a finished fade-in that never cleared (blocks Open desktop on round 2+).
        try:
            anim = getattr(self, '_widget_fade_anim', None)
            running = False
            if anim is not None:
                try:
                    running = anim.state() == anim.State.Running
                except Exception:
                    running = False
            if self._fading_in and not running and float(self.windowOpacity()) >= 0.99:
                self._fading_in = False
            if self._enter_desktop_armed and not self._fading_out and not running:
                self._enter_desktop_armed = False
        except Exception:
            pass
        if self._fading_out or self._fading_in:
            return
        if getattr(self, '_enter_desktop_armed', False):
            return
        self._enter_desktop_armed = True
        mark_completed_once()
        if self._soft:
            mark_soft_shown()
        # Arm leave first so park/present cannot zero Main while we stage it.
        self._fading_out = True
        self._fading_in = False
        try:
            self.raise_()
        except Exception:
            pass
        # Stage the real desktop under this cover, then fade only Conductor.
        try:
            self._stage_main_for_crossfade()
        except Exception:
            pass
        try:
            self.raise_()
            self.activateWindow()
            _attach_window_controls_to_conductor(self, self._host)
        except Exception:
            pass
        try:
            self._fade_out_like_splash(already_leaving=True)
        except Exception:
            self._enter_desktop_armed = False
            self._fading_out = False
            return
        if not self._fading_out:
            self._enter_desktop_armed = False

    def _stage_main_for_crossfade(self):
        """Map Main opaque under Conductor with real wallpaper — no raise/flash."""
        win = self._host
        if win is None:
            return
        from Support.Misc import ensure_screen_dimensions, restore_desktop_canvas

        try:
            if hasattr(win, 'fade_object_timer') and win.fade_object_timer:
                win.fade_object_timer.stop()
            if hasattr(win, '_fade_started'):
                win._fade_started = True
        except Exception:
            pass

        ensure_screen_dimensions(win)

        # Map without activating so this Tool cover stays above Main.
        try:
            win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        except Exception:
            pass
        try:
            if (not win.isVisible()) or (not win.isMaximized()):
                win.showMaximized()
            win.setWindowOpacity(1.0)
        except Exception:
            pass
        try:
            win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        except Exception:
            pass

        # Real wallpaper under the cover (not the solid stand-in used while parked).
        try:
            canvas = getattr(win, 'canvas', None)
            if canvas is not None:
                restore_desktop_canvas(win)
                canvas.show()
                canvas.lower()
        except Exception:
            pass

        for attr in ('watermark', 'name', 'name1'):
            w = getattr(win, attr, None)
            if w is not None:
                try:
                    w.show()
                except Exception:
                    pass
        try:
            macros = getattr(init, 'macros_bar', None)
            if macros is not None:
                macros.hide()
        except Exception:
            pass

        _set_desktop_icons_visible(False)
        _ensure_main_owns_taskbar()
        # Taskbar stays buried until dismiss (icons/chrome finish the handoff).
        for src in (win, init):
            if src is None:
                continue
            tb = getattr(src, 'taskbar', None)
            if tb is None:
                continue
            try:
                tb.hide()
            except Exception:
                pass
        try:
            self.raise_()
        except Exception:
            pass

    def _fade_out_like_splash(self, already_leaving=False):
        """Splash-style windowOpacity fade-out on this top-level HWND."""
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve

        if self._fading_out and not already_leaving:
            return
        self._fading_out = True
        self._fading_in = False
        self._neury_cancel = True
        for tname in ('_raise_timer', '_mesh_timer', '_suggest_timer', '_poll'):
            t = getattr(self, tname, None)
            if t is not None:
                try:
                    t.stop()
                except Exception:
                    pass
        try:
            self.btn_run.setEnabled(False)
            self.btn_desktop.setEnabled(False)
            self.btn_again.setEnabled(False)
        except Exception:
            pass
        try:
            self.setGraphicsEffect(None)
            self._widget_fade_effect = None
        except Exception:
            pass
        # Stop any prior fade so finished handlers cannot stack across round-trips.
        old = getattr(self, '_widget_fade_anim', None)
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
            try:
                old.finished.disconnect()
            except Exception:
                pass
            self._widget_fade_anim = None
        try:
            start = float(self.windowOpacity())
        except Exception:
            start = 1.0
        if start < 0.01:
            start = 1.0
        try:
            self.setWindowOpacity(start)
        except Exception:
            pass

        anim = QPropertyAnimation(self, b'windowOpacity', self)
        anim.setDuration(int(_FADE_OUT_MS))
        anim.setStartValue(start)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._widget_fade_anim = anim

        def _done():
            self.dismiss()

        try:
            anim.finished.connect(_done)
        except Exception:
            pass
        anim.start()
        QTimer.singleShot(int(_FADE_OUT_MS) + 500, self._fade_out_failsafe)

    def _fade_out_like_fade_py(self):
        self._fade_out_like_splash()

    def _fade_out_then_dismiss(self):
        self._fade_out_like_splash()

    def _fade_out_failsafe(self):
        if _conductor is self and self._fading_out:
            self.dismiss()

    def dismiss(self):
        global _conductor
        if self._poll:
            try:
                self._poll.stop()
            except Exception:
                pass
        if self._mesh_timer:
            try:
                self._mesh_timer.stop()
            except Exception:
                pass
        if self._raise_timer:
            try:
                self._raise_timer.stop()
            except Exception:
                pass
        if getattr(self, '_suggest_timer', None):
            try:
                self._suggest_timer.stop()
            except Exception:
                pass
        then = getattr(self, '_after_fade', None)
        self._after_fade = None
        host = self._host
        self._fading_out = False
        self._fading_in = False
        self._enter_desktop_armed = False
        # Main is already opaque under us — keep it that way (no second fade-in).
        try:
            if host is not None:
                if hasattr(host, 'fade_object_timer') and host.fade_object_timer:
                    host.fade_object_timer.stop()
                if hasattr(host, '_fade_started'):
                    host._fade_started = True
                host.setWindowOpacity(1.0)
        except Exception:
            pass
        try:
            self.setWindowOpacity(0.0)
        except Exception:
            pass
        try:
            self.hide()
        except Exception:
            pass
        _detach_window_controls_from_conductor(self, host)
        set_conductor_visible(False)
        try:
            reveal_main_desktop(host, raise_window=True)
        except Exception:
            pass
        _ensure_main_owns_taskbar()
        # Belt-and-suspenders: chrome must be back after leave.
        try:
            restore_desktop_chrome(host)
        except Exception:
            pass
        # A queued _keep_above_desktop tick can fire after stop(); re-assert chrome.
        try:
            QTimer.singleShot(0, lambda h=host: restore_desktop_chrome(h))
            QTimer.singleShot(450, lambda h=host: restore_desktop_chrome(h))
        except Exception:
            pass
        self.closed.emit()
        if callable(then):
            QTimer.singleShot(0, then)




def _conductor_intro_active(overlay=None):
    """True when Conductor already owns (or is taking) the intro — do not re-fade."""
    ov = overlay if overlay is not None else _conductor
    if ov is None:
        return False
    try:
        ov.objectName()
    except RuntimeError:
        return False
    try:
        if getattr(ov, '_fading_in', False):
            return True
        if ov.isVisible() and not getattr(ov, '_fading_out', False):
            return True
    except Exception:
        return False
    return False


def _set_windows_taskbar_button(widget, show):
    """Show or hide a top-level HWND on the Windows taskbar (no flag remapping)."""
    import sys
    if sys.platform != 'win32' or widget is None:
        return
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = int(widget.winId())
        if not hwnd:
            return
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        gwl_exstyle = -20
        ws_ex_appwindow = 0x00040000
        ws_ex_toolwindow = 0x00000080
        get_long = user32.GetWindowLongPtrW
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        get_long.restype = ctypes.c_ssize_t
        set_long = user32.SetWindowLongPtrW
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_long.restype = ctypes.c_ssize_t
        ex = int(get_long(hwnd, gwl_exstyle) or 0)
        if show:
            ex = (ex | ws_ex_appwindow) & ~ws_ex_toolwindow
        else:
            ex = (ex | ws_ex_toolwindow) & ~ws_ex_appwindow
        set_long(hwnd, gwl_exstyle, ex)
        swp_nosize = 0x0001
        swp_nomove = 0x0002
        swp_nozorder = 0x0004
        swp_noactivate = 0x0010
        swp_framechanged = 0x0020
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            swp_nomove | swp_nosize | swp_nozorder | swp_noactivate | swp_framechanged,
        )
    except Exception:
        pass


def _ensure_main_owns_taskbar():
    """Main is always the sole Windows taskbar / Alt+Tab entry.

    Frameless Conductor does not reliably get a taskbar button when given
    APPWINDOW, and demoting Main left zero icons. Conductor stays reachable by
    activating Main (taskbar / Alt+Tab) via present_conductor_if_covering().
    """
    ov = _conductor
    win = None
    if ov is not None:
        win = getattr(ov, '_host', None)
    win = win or getattr(init, 'mainApplication', None)
    _set_windows_taskbar_button(ov, False)
    _set_windows_taskbar_button(win, True)


def _park_main_for_taskbar(win=None):
    """Keep Main mapped (taskbar icon) but invisible while Conductor covers."""
    win = win or getattr(init, 'mainApplication', None)
    if win is None:
        return
    try:
        win.setWindowOpacity(0.0)
        if not win.isVisible():
            win.showMaximized()
        win.setWindowOpacity(0.0)
    except Exception:
        pass
    _hide_main_chrome(win)
    _ensure_main_owns_taskbar()


def present_conductor_if_covering():
    """Instant restore when Main is activated from the taskbar / Alt+Tab.

    No soft fade — fading would flash the desktop under a transparent Conductor.
    """
    if not bool(getattr(init, 'conductor_visible', False)):
        return False
    ov = _conductor
    if ov is None or getattr(ov, '_fading_out', False):
        return False
    try:
        if ov._foreign_toplevel_present():
            return False
    except Exception:
        pass
    host = getattr(ov, '_host', None) or getattr(init, 'mainApplication', None)
    # Main owns the taskbar button; keep it mapped but invisible so activating
    # Main cannot paint the desktop over Conductor.
    _park_main_for_taskbar(host)
    try:
        ov._fading_in = False
        ov._suppress_focus = False
        try:
            ov.setWindowOpacity(1.0)
        except Exception:
            pass
        if not ov.isVisible():
            ov.show()
        ov.raise_()
        ov.activateWindow()
        ov._focus_input()
    except Exception:
        return False
    try:
        _attach_window_controls_to_conductor(ov, host)
        _keep_taskbar_buried()
    except Exception:
        pass
    return True


def _make_conductor_toplevel(ov):
    """Splash-style: own HWND so windowOpacity fades over the desktop, not over black Main.

    Tool (not a taskbar Window): Main keeps the only taskbar button; activating
    Main raises Conductor while it covers. Still no WindowStaysOnTopHint.
    """
    flags = (
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.Tool
    )
    try:
        ov.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    except Exception:
        pass
    try:
        ov.hide()
    except Exception:
        pass
    # Atomic promote: setParent(None, flags) avoids a framed default Window
    # between orphaning and setWindowFlags.
    try:
        ov.setParent(None, flags)
    except Exception:
        try:
            ov.setParent(None)
            ov.setWindowFlags(flags)
        except Exception:
            pass
    try:
        host = getattr(ov, '_host', None) or getattr(init, 'mainApplication', None)
        if host is not None:
            title = str(host.windowTitle() or '').strip()
            if title:
                ov.setWindowTitle(title)
            icon = host.windowIcon()
            if icon is not None and not icon.isNull():
                ov.setWindowIcon(icon)
    except Exception:
        pass
    try:
        ov.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        ov.setAutoFillBackground(True)
    except Exception:
        pass
    try:
        ov.hide()
    except Exception:
        pass


def _attach_window_controls_to_conductor(ov, win):
    """Desktop + minimize + close, inset from the right edge (never clipped)."""
    if ov is None or win is None:
        return
    if not bool(getattr(init, 'conductor_visible', False)):
        return
    try:
        if getattr(ov, '_fading_out', False) or not ov.isVisible():
            return
    except Exception:
        return
    bar = getattr(win, 'window_controls', None)
    if bar is None:
        return
    try:
        from PySide6.QtWidgets import QPushButton
        bar.setParent(ov)
        # Restore every control at full size — prior hide/width-0 left Close off-screen.
        for child in bar.findChildren(QPushButton):
            try:
                child.setMaximumWidth(16777215)
                child.setFixedSize(32, 32)
                child.show()
                child.setEnabled(True)
            except Exception:
                pass
        margin = 24
        btn = 32
        gap = int(getattr(win, '_win_ctrl_gap', 5) or 5)
        desk_gap = int(getattr(win, '_win_desktop_gap', 15) or 15)
        # desktop + desk_gap + minimize + gap + close
        w = btn + desk_gap + btn + gap + btn
        h = btn
        ow = max(1, int(ov.width() or 0))
        x = max(margin, ow - w - margin)
        y = margin
        bar.setGeometry(x, y, w, h)
        bar.show()
        bar.raise_()
    except Exception:
        pass


def _detach_window_controls_from_conductor(ov, win):
    if win is None:
        return
    bar = getattr(win, 'window_controls', None)
    if bar is None:
        return
    try:
        from PySide6.QtWidgets import QPushButton
        for child in bar.findChildren(QPushButton):
            try:
                child.setMaximumWidth(16777215)
                child.setFixedSize(32, 32)
                child.show()
            except Exception:
                pass
        bar.setParent(win)
        pos = getattr(win, '_position_window_controls', None)
        if callable(pos):
            pos()
        bar.show()
        bar.raise_()
    except Exception:
        pass


def _keep_taskbar_buried():
    """Taskbar must never paint over Conductor."""
    if not bool(getattr(init, 'conductor_visible', False)):
        return
    ov = _conductor
    try:
        if ov is not None and getattr(ov, '_fading_out', False):
            return
    except Exception:
        pass
    for src in (getattr(init, 'mainApplication', None), init):
        tb = getattr(src, 'taskbar', None) if src is not None else None
        if tb is None:
            continue
        try:
            tb.hide()
        except Exception:
            pass


def startup_conductor_like_fade_py(win, soft=False, finished=None):
    """
    Same as Splash: top-level HWND + windowOpacity fade over whatever is behind
    (desktop wallpaper on first show, Neuron desktop on soft reopen).
    Main stays opacity 0 / hidden during the fade so it cannot paint black underneath.
    """
    from PySide6.QtCore import QPropertyAnimation, QEasingCurve
    from PySide6.QtGui import QColor, QPalette

    global _conductor
    if not is_enabled():
        return None
    win = win or getattr(init, 'mainApplication', None)

    set_conductor_visible(True)

    if win is not None:
        try:
            if hasattr(win, 'fade_object_timer') and win.fade_object_timer:
                win.fade_object_timer.stop()
            if hasattr(win, '_fade_started'):
                win._fade_started = True
        except Exception:
            pass
        if soft:
            # Soft reopen: Main stays mapped under the fade (Neuron desktop visible through it).
            pass
        else:
            # First show: keep Main mapped at opacity 0 so the Windows taskbar icon
            # exists from the start (hide() removed the only APPWINDOW entry).
            _park_main_for_taskbar(win)

    dead = False
    if _conductor is not None:
        try:
            _conductor.objectName()
        except RuntimeError:
            dead = True
            _conductor = None

    if _conductor is None or dead:
        # Build as child of hidden Main so __init__ never creates a top-level HWND.
        ov = ConductorOverlay(win, soft=soft)
        _conductor = ov
    else:
        ov = _conductor
        ov._soft = soft

    ov._host = win
    ov._fading_out = False
    ov._fading_in = False
    ov._enter_desktop_armed = False
    ov._after_fade_in = finished
    try:
        ov.btn_run.setEnabled(True)
        ov.btn_desktop.setEnabled(True)
        ov.btn_again.setEnabled(True)
        ov.setEnabled(True)
    except Exception:
        pass

    try:
        ov.setGraphicsEffect(None)
        ov._widget_fade_effect = None
    except Exception:
        pass

    # Cancel any leftover fade from a previous Conductor↔desktop round-trip.
    old = getattr(ov, '_widget_fade_anim', None)
    if old is not None:
        try:
            old.stop()
        except Exception:
            pass
        try:
            old.finished.disconnect()
        except Exception:
            pass
        ov._widget_fade_anim = None

    # Promote only after UI exists: DontShowOnScreen + Frameless + full-screen
    # + opacity 0, then map. Never birth as a tiny default framed Window.
    _make_conductor_toplevel(ov)
    ov._apply_veil(1.0, 1.0)
    try:
        pal = ov.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(6, 10, 22))
        ov.setPalette(pal)
    except Exception:
        pass

    geo = _cover_screen_geometry()
    if geo is not None:
        try:
            ov.setGeometry(geo)
        except Exception:
            pass
    ov._geometry_locked = True

    try:
        ov.setWindowOpacity(0.0)
    except Exception:
        pass
    try:
        ov.hide()
    except Exception:
        pass
    try:
        ov.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
    except Exception:
        pass
    ov._suppress_focus = False
    ov.show()
    # Must own Z-order before burying Main chrome — otherwise soft reopen leaves a bare desktop.
    try:
        ov.raise_()
        ov.activateWindow()
    except Exception:
        pass
    # Main keeps the only taskbar button; Conductor is Tool / non-taskbar.
    _ensure_main_owns_taskbar()
    _attach_window_controls_to_conductor(ov, win)
    _keep_taskbar_buried()
    if soft:
        try:
            _set_desktop_icons_visible(False)
        except Exception:
            pass

    ov._fading_in = True
    anim = QPropertyAnimation(ov, b'windowOpacity', ov)
    anim.setDuration(int(_FADE_IN_MS))
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
    ov._widget_fade_anim = anim

    def _on_done():
        ov._fading_in = False
        if getattr(ov, '_fading_out', False) or not bool(getattr(init, 'conductor_visible', False)):
            return
        try:
            if not ov.isVisible():
                return
        except Exception:
            return
        try:
            ov.setWindowOpacity(1.0)
            ov.raise_()
        except Exception:
            pass
        # After cover is solid, park Main invisible so Alt+Tab/taskbar activate
        # cannot flash the desktop; icon stays via the mapped HWND.
        if soft:
            _park_main_for_taskbar(win)
        else:
            _ensure_main_owns_taskbar()
        _attach_window_controls_to_conductor(ov, win)
        _keep_taskbar_buried()
        then = getattr(ov, '_after_fade_in', None)
        ov._after_fade_in = None
        if callable(then):
            QTimer.singleShot(0, then)

    try:
        anim.finished.connect(_on_done)
    except Exception:
        pass
    anim.start()

    def _bury():
        if getattr(ov, '_fading_out', False):
            return
        if not bool(getattr(init, 'conductor_visible', False)):
            return
        try:
            if not ov.isVisible():
                return
        except Exception:
            return
        try:
            if ov._app_focus_elsewhere():
                return
        except Exception:
            pass
        try:
            ov.raise_()
        except Exception:
            pass
        _keep_taskbar_buried()
        if ov._fading_in:
            QTimer.singleShot(100, _bury)
    QTimer.singleShot(100, _bury)

    if soft:
        mark_soft_shown()
    return ov


def show_conductor(parent=None, soft=False, on_ready=None, map_window=None, start_fade=True,
                   keep_main_opacity_zero=False, after_fade_in=None):
    """Always splash-style top-level fade over whatever is behind the app."""
    if not is_enabled():
        return None
    parent = parent or getattr(init, 'mainApplication', None)
    return startup_conductor_like_fade_py(
        parent, soft=soft, finished=after_fade_in or on_ready,
    )


def mount_conductor_on_main(parent=None, soft=False):
    return startup_conductor_like_fade_py(parent, soft=soft)


def reveal_main_under_conductor_cover(win=None):
    return startup_conductor_like_fade_py(win) is not None


def stage_conductor_under_splash(splash_window=None):
    _ = splash_window
    return False



def schedule_conductor(parent=None, delay_ms=0):
    if not is_enabled():
        return False
    if not should_show_on_startup():
        return False
    parent = parent or getattr(init, 'mainApplication', None)
    soft = bool(load_config().get('completed_once'))

    def _show():
        if _conductor_intro_active():
            return
        show_conductor(parent, soft=soft)

    QTimer.singleShot(max(0, int(delay_ms)), _show)
    return True
