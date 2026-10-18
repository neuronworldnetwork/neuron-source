# NeuronNeuryAgentApps.py — extracted from NeuronNeuryAgent.py (satellite module).
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
_fuzzy_one = None
_iter_peers = None
_maybe_decrypt_preview = None
_my_id = None
_neury_path_inside_install = None
_neury_sandbox_deny = None
_neury_sandbox_redact_path = None
_normalize_attachment_list = None
_normalize_url = None
_parse_availability_arg = None
_peer_crawl_candidates = None
_peer_crawl_search_sync = None
_peer_record = None
_resolve_peer = None
_run_peer_neury_job = None
apply_neury_mark = None
neury_marked = None


def _chat_send_show_on_wall(dlg, message_id, chat_id, text, my_id, ts, extras, peer_id):
    if dlg is None:
        return
    try:
        if getattr(dlg, 'current_destination_id', None) != peer_id:
            return
        rendered = getattr(dlg, 'rendered_message_ids', None)
        if rendered is not None and message_id in rendered:
            return
        dlg.add_message_to_wall(message_id, chat_id, text, my_id, ts, '', extras, '')
    except Exception:
        pass

def _chat_send(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Dictionaries import filter_dictionary
    from Support.EncDec import encrypt
    from Support.NeuronProjectorChat import get_or_create_chat_id
    from Support.SendData import send_packet
    from Support.Utils import generate_random_hash, get_timestamp_in_seconds, ip_to_send_packet_to, query_database
    raw = (args.get('message') or args.get('text') or '').strip()
    if not raw:
        return {'ok': False, 'error': 'message required'}
    group_key = str(args.get('group') or args.get('group_id') or '')
    if args.get('is_group') or group_key:
        return _chat_send_group(args)
    peer_key = str(args.get('peer') or args.get('name') or args.get('peer_id') or '')
    resolved = _resolve_peer(peer_key)
    if not resolved.get('ok'):
        grouped = _resolve_group(peer_key or group_key)
        if grouped.get('ok'):
            args = dict(args)
            args['group'] = grouped['group']['id']
            return _chat_send_group(args)
        return resolved
    text = apply_neury_mark(raw)
    my_id = str(init.ME['id'] or init.ME.get('peer_id') or _my_id() or '')
    peer_id = resolved['peer']['peer_id']
    peer_info = filter_dictionary(init.PEERS, 'peer_id', peer_id)
    if not peer_info:
        rec = _peer_record(peer_id)
        if rec:
            peer_info = [rec]
    if not peer_info:
        return {'ok': False, 'error': 'peer not reachable'}
    dest = peer_info[0]
    chat_id = get_or_create_chat_id(peer_id, False, my_id)
    message_id = generate_random_hash()
    ts = get_timestamp_in_seconds()
    encrypted = encrypt(text, peer_id=peer_id)
    extras = '1;0;0;'
    pool = getattr(init, 'pool_main_db', None)
    query_database(pool, 'INSERT INTO Chats (id, chat_id, src, src_peer, dst, date, msg, attachment, pending, heart, reply_to, extras) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (message_id, chat_id, '', my_id, peer_id, ts, encrypted, '', 1, '', '', extras))
    send_packet(init.ME['ip'], init.ME['ip_local'], '', ip_to_send_packet_to(dest.get('ip'), dest.get('ip_local')), dest.get('ip_local') or '', '', init.CMD_MSG_SND, init.PRT_HGH, '', 0, 0, '', [message_id, my_id, peer_id, chat_id, ts], [encrypted, '', extras], [b''], '', '', [], [], [], retry_policy='critical', src_peer_id=my_id, dst_peer_id=peer_id)
    try:
        from Support.ChatSpeedOptimizer import signal_chat_message_ready
        signal_chat_message_ready(message_id, chat_id)
    except Exception:
        pass
    dlg = getattr(init, 'chat_dialog', None)
    if dlg is None:
        try:
            from Support.Header import chat_dialog as header_chat_dialog
            dlg = header_chat_dialog
        except Exception:
            dlg = None
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, lambda: _chat_send_show_on_wall(dlg, message_id, chat_id, text, my_id, ts, extras, peer_id))
        else:
            _chat_send_show_on_wall(dlg, message_id, chat_id, text, my_id, ts, extras, peer_id)
    except Exception:
        _chat_send_show_on_wall(dlg, message_id, chat_id, text, my_id, ts, extras, peer_id)
    return {'ok': True, 'message_id': message_id, 'peer': resolved['peer'], 'marked': True}

def _diary_post(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.EncDec import encrypt
    from Support.NeuronProjectorDiary import _send_diary_post_to_peered_peers
    from Support.Utils import generate_random_hash, query_database
    raw = (args.get('message') or args.get('text') or '').strip()
    if not raw:
        return {'ok': False, 'error': 'message required'}
    text = apply_neury_mark(raw)
    my_id = _my_id()
    post_id = generate_random_hash()
    ts = int(time.time())
    encrypted = encrypt(text)
    pool = getattr(init, 'pool_main_db', None)
    query_database(pool, 'INSERT INTO Diary (id, peer_id, message, attachment, my_timestamp, peer_timestamp, heart) VALUES (?, ?, ?, ?, ?, ?, ?)', (post_id, my_id, encrypted, '', ts, ts, ''))
    entry_data = {'id': post_id, 'peer_id': my_id, 'text': text, 'attachments': '', 'my_timestamp': ts, 'peer_timestamp': ts, 'timestamp': ts}
    _send_diary_post_to_peered_peers(entry_data)
    try:
        from Support.NeuronProjectorDiary import send_post_to_diary_dialog
        send_post_to_diary_dialog({'id': post_id, 'post_id': post_id, 'user_id': my_id, 'peer_id': my_id, 'user_name': (getattr(init, 'ME', None) or {}).get('node_name') or 'Me', 'content': text, 'message': text, 'attachment': '', 'timestamp': ts, 'date_posted': ts, 'likes': 0, 'comments': []})
    except Exception:
        pass
    return {'ok': True, 'post_id': post_id, 'marked': True}

def _article_save(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import generate_random_hash, get_timestamp_in_seconds, query_database
    title = apply_neury_mark((args.get('title') or '').strip())
    if title == NEURY_MARK or not (args.get('title') or '').strip():
        return {'ok': False, 'error': 'title required'}
    article_text = apply_neury_mark((args.get('article') or args.get('body') or args.get('text') or '').strip())
    subtitle = args.get('subtitle') or ''
    tags = args.get('tags') or ''
    if 'neury' not in str(tags).lower():
        tags = (str(tags).strip() + ' neury').strip()
    article_id = (args.get('article_id') or '').strip()
    ts = get_timestamp_in_seconds()
    pool = getattr(init, 'pool_main_db', None)
    if article_id:
        query_database(pool, 'UPDATE Articles SET title=?, subtitle=?, short_description=?, long_description=?, article=?, date_last_updated=?, image_list=?, tags=? WHERE article_id=?', (title, subtitle, subtitle, article_text[:200], article_text, ts, '', tags, article_id))
    else:
        article_id = generate_random_hash()
        query_database(pool, 'INSERT INTO Articles (article_id, title, subtitle, short_description, long_description, article, date_created, date_last_updated, image_list, image_list_data, tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (article_id, title, subtitle, subtitle, article_text[:200], article_text, ts, ts, '', None, tags))
    return {'ok': True, 'article_id': article_id, 'title': title, 'marked': True}

def _article_publish(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.NeuronProjectorNews import _news_server_types
    from Support.SendData import send_packet
    from Support.Utils import get_timestamp_in_seconds, ip_to_send_packet_to, query_database
    article_id = (args.get('article_id') or '').strip()
    if not article_id:
        return {'ok': False, 'error': 'article_id required'}
    pool = getattr(init, 'pool_main_db', None)
    news_pool = getattr(init, 'pool_news_db', None)
    local = query_database(pool, 'SELECT article_id, title, subtitle, short_description, long_description, article, date_created, date_last_updated, image_list, tags FROM Articles WHERE article_id = ?', (article_id,), commit=False) or []
    if not local:
        return {'ok': False, 'error': 'article not found'}
    row = local[0]
    title, subtitle, short_desc, long_desc, article_text = (row[1], row[2], row[3], row[4], row[5])
    if not neury_marked(title or ''):
        title = apply_neury_mark(title or '')
    if not neury_marked(article_text or ''):
        article_text = apply_neury_mark(article_text or '')
    date_created = row[6]
    published_at = get_timestamp_in_seconds()
    date_updated = row[7] or published_at
    image_list = row[8] or ''
    tags_str = row[9] or ''
    if 'neury' not in str(tags_str).lower():
        tags_str = (str(tags_str).strip() + ' neury').strip()
    cost = args.get('cost')
    if cost is None:
        existing_cost = query_database(news_pool, 'SELECT cost FROM Articles WHERE article_id = ?', (article_id,), commit=False) or []
        cost = existing_cost[0][0] if existing_cost else 0
    country = (getattr(init, 'ME', None) or {}).get('user_country', '') or ''
    peer_id = _my_id()
    author = (getattr(init, 'ME', None) or {}).get('node_name', '')
    news_exists = query_database(news_pool, 'SELECT article_id FROM Articles WHERE article_id = ?', (article_id,), commit=False)
    if news_exists:
        query_database(news_pool, 'UPDATE Articles SET category=?, title=?, subtitle=?, short_description=?, long_description=?, article=?, peer_id=?, author=?, date_created=?, date_updated=?, date_published=?, image_list=?, tags=?, country_code=?, cost=? WHERE article_id=?', (init.USER_ARTICLES_IDX, title, subtitle, short_desc, long_desc, article_text, peer_id, author, date_created, date_updated, published_at, image_list, tags_str, country, cost, article_id))
    else:
        query_database(news_pool, 'INSERT INTO Articles (article_id, category, title, subtitle, short_description, long_description, article, peer_id, author, date_created, date_updated, date_published, image_list, image_list_data, tags, country_code, cost) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (article_id, init.USER_ARTICLES_IDX, title, subtitle, short_desc, long_desc, article_text, peer_id, author, date_created, date_updated, published_at, image_list, None, tags_str, country, cost))
    sent = 0
    payload = (article_id, init.USER_ARTICLES_IDX, title, subtitle, short_desc, long_desc, article_text, peer_id, author, date_created, date_updated, published_at, image_list, tags_str, country, cost, 10)
    me = getattr(init, 'ME', None) or {}
    for peer in _iter_peers():
        if peer.get('online') and peer.get('type') in _news_server_types():
            send_packet(me.get('ip'), me.get('ip_local'), '', ip_to_send_packet_to(peer.get('ip'), peer.get('ip_local')), peer.get('ip_local') or '', '', init.CMD_NWS_ART, init.PRT_LOW, '', 0, 0, '', list(payload), [me.get('id'), me.get('node_name'), me.get('ip'), me.get('ip_local'), me.get('node_type')], [], '', '', [], [], [])
            sent += 1
    return {'ok': True, 'article_id': article_id, 'servers': sent, 'marked': True}

def _mail_send(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.NeuronMail import send_external_mail, list_accounts, get_account
    to_addr = str(args.get('to') or args.get('email') or args.get('sendee_email') or '').strip()
    if to_addr and '@' not in to_addr:
        found = _contacts_get({'name': to_addr})
        if found.get('ok'):
            to_addr = str((found.get('contact') or {}).get('email') or '').strip()
    if not to_addr or '@' not in to_addr:
        return {'ok': False, 'error': 'email address required'}
    subject = str(args.get('subject') or args.get('title') or 'Neuron').strip() or 'Neuron'
    body = str(args.get('body') or args.get('message') or args.get('text') or '').strip()
    attachments = []
    extra = args.get('attachments') or args.get('files') or []
    if isinstance(extra, str):
        extra = [extra]
    attachments.extend(extra)
    one = (args.get('path') or args.get('file') or args.get('pdf_path') or '').strip()
    if one:
        attachments.append(one)
    attachments = _normalize_attachment_list(attachments)
    accounts = list_accounts() or []
    if not accounts:
        return {'ok': False, 'error': 'No mailbox configured. Open desktop → Mail → Mailboxes and add one.'}
    account_id = str(args.get('account_id') or '').strip() or None
    if not account_id:
        account_id = _resolve_mail_account_hint(args, accounts)
    if not account_id:
        if len(accounts) == 1:
            account_id = str(accounts[0].get('id') or '') or None
        else:
            return {'ok': False, 'need_account_choice': True, 'error': 'Choose which mailbox should send this.', 'accounts': [{'id': str(a.get('id') or ''), 'email': str(a.get('email') or ''), 'provider': str(a.get('provider') or '')} for a in accounts if a.get('id') and a.get('email')], 'pending': {'to': to_addr, 'subject': subject, 'body': body, 'attachments': attachments}}
    account = get_account(account_id) if account_id else None
    from_addr = str((account or {}).get('email') or '').strip()
    ok, message = send_external_mail(to_addr, subject, body, account_id=account_id, attachments=attachments)
    return {'ok': bool(ok), 'email': to_addr, 'from_email': from_addr, 'subject': subject, 'attachments': attachments, 'error': '' if ok else message or 'send failed', 'text': message or ''}

def _resolve_mail_account_hint(args: Dict[str, Any], accounts: list) -> Optional[str]:
    hint = str(args.get('from') or args.get('from_email') or args.get('account') or args.get('mailbox') or '').strip().lower()
    if not hint:
        return None
    for account in accounts:
        email = str(account.get('email') or '').strip().lower()
        provider = str(account.get('provider') or '').strip().lower()
        if not email:
            continue
        if hint == email or hint in email or email in hint:
            return str(account.get('id') or '') or None
        if provider and (hint == provider or hint in provider or provider in hint):
            return str(account.get('id') or '') or None
        local = email.split('@', 1)[0]
        if hint == local:
            return str(account.get('id') or '') or None
    return None

def _files_neuronize(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Files import import_files
    rel = (args.get('path') or args.get('file') or '').strip()
    if not rel:
        return {'ok': False, 'error': 'path required'}
    want_room = str(args.get('room') or args.get('room_id') or '').strip()
    if want_room:
        switched = _rooms_switch({'room': want_room})
        if not switched.get('ok'):
            return switched
    user_root = os.path.abspath(getattr(init, 'PATH_USER', '') or '')
    full = os.path.abspath(rel if os.path.isabs(rel) else os.path.join(user_root, rel))
    if not _neury_path_inside_install(full):
        return _neury_sandbox_deny(full)
    if not os.path.isfile(full):
        return {'ok': False, 'error': 'file not found', 'path': full}
    app = getattr(init, 'mainApplication', None)
    room = getattr(init, 'selected_room_info', None)
    if app is None or room is None:
        return {'ok': False, 'error': 'open a room first'}
    try:
        room_id = room['room_id'] if not isinstance(room, dict) else room.get('room_id') or room.get('id')
        allowed = room['allowed_peers'] if not isinstance(room, dict) else room.get('allowed_peers') or ''
        rtype = room['type'] if not isinstance(room, dict) else room.get('type') or ''
    except Exception:
        return {'ok': False, 'error': 'open a room first'}
    if not room_id:
        return {'ok': False, 'error': 'open a room first'}
    ok = import_files(app, [full], 120, 120, room_id, allowed or '', rtype or '')
    if ok is False:
        return {'ok': False, 'error': 'upload refused (duplicate or not enough Neurons)'}
    return {'ok': True, 'path': full, 'room_id': room_id}

def _split_names(raw: Any) -> List[str]:
    out = []
    for part in str(raw or '').replace(',', ';').split(';'):
        part = part.strip()
        if part:
            out.append(part)
    return out

def _resolve_group(name_or_id: str) -> Dict[str, Any]:
    from Support.Utils import query_database
    want = (name_or_id or '').strip()
    if not want:
        return {'ok': False, 'error': 'group required'}
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT id, name, peers, owner_id FROM ChatGroups', (), commit=False) or []
    low = want.lower()
    names = []
    by_name = {}
    for row in rows:
        gid = str(row[0] or '')
        gname = str(row[1] or '')
        rec = {'id': gid, 'name': gname, 'peers': str(row[2] or ''), 'owner_id': str(row[3] or '')}
        names.append(gname)
        by_name[gname] = rec
        if gid == want or gname.lower() == low:
            return {'ok': True, 'group': rec}
    fuzzy = _fuzzy_one(want, names, cutoff=0.7)
    if fuzzy:
        return {'ok': True, 'group': by_name[fuzzy], 'interpreted': fuzzy}
    return {'ok': False, 'error': 'no group matched', 'query': want}

def _chat_send_group(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Dictionaries import filter_dictionary
    from Support.EncDec import encrypt
    from Support.NeuronProjectorChat import get_or_create_chat_id
    from Support.SendData import send_packet
    from Support.Utils import generate_random_hash, get_timestamp_in_seconds, ip_to_send_packet_to, query_database
    raw = (args.get('message') or args.get('text') or '').strip()
    grouped = _resolve_group(str(args.get('group') or args.get('group_id') or args.get('peer') or ''))
    if not grouped.get('ok'):
        return grouped
    text = apply_neury_mark(raw)
    my_id = _my_id()
    group_id = grouped['group']['id']
    peer_ids = [p for p in grouped['group']['peers'].split(';') if p]
    extras = str(len(peer_ids)) + ';0;0;'
    chat_id = get_or_create_chat_id(group_id, True, my_id)
    message_id = generate_random_hash()
    ts = get_timestamp_in_seconds()
    encrypted = encrypt(text, peer_id=None)
    pool = getattr(init, 'pool_main_db', None)
    query_database(pool, 'INSERT INTO Chats (id, chat_id, src, src_peer, dst, date, msg, attachment, pending, heart, reply_to, extras) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (message_id, chat_id, '', my_id, group_id, ts, encrypted, '', 1, '', '', extras))
    sent = 0
    for peer in peer_ids:
        if peer == my_id:
            continue
        peer_info = filter_dictionary(init.PEERS, 'peer_id', peer)
        if not peer_info:
            continue
        dest = peer_info[0]
        send_packet(init.ME['ip'], init.ME['ip_local'], '', ip_to_send_packet_to(dest.get('ip'), dest.get('ip_local')), dest.get('ip_local') or '', '', init.CMD_MSG_SND, init.PRT_HGH, '', 0, 0, '', [message_id, my_id, group_id, chat_id, ts], [encrypted, '', extras], [b''], '', '', [], [], [], retry_policy='critical', src_peer_id=my_id, dst_peer_id=dest.get('peer_id'))
        sent += 1
    return {'ok': True, 'message_id': message_id, 'group': grouped['group'], 'peer': {'name': grouped['group']['name'], 'peer_id': group_id}, 'sent': sent, 'marked': True}

def _groups_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT id, name, peers, owner_id FROM ChatGroups', (), commit=False) or []
    groups = []
    for row in rows:
        peers = [p for p in str(row[2] or '').split(';') if p]
        groups.append({'id': row[0], 'name': row[1], 'peer_count': len(peers), 'owner_id': row[3]})
    return {'ok': True, 'groups': groups, 'count': len(groups)}

def _groups_create(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Dictionaries import filter_dictionary
    from Support.SendData import send_packet
    from Support.Utils import generate_random_hash, ip_to_send_packet_to, query_database
    name = (args.get('name') or args.get('title') or '').strip()
    if not name:
        return {'ok': False, 'error': 'name required'}
    peer_ids = []
    for part in _split_names(args.get('peers') or args.get('peer') or ''):
        resolved = _resolve_peer(part)
        if resolved.get('ok'):
            pid = resolved['peer']['peer_id']
            if pid not in peer_ids:
                peer_ids.append(pid)
    my_id = _my_id()
    if my_id not in peer_ids:
        peer_ids.append(my_id)
    if len(peer_ids) < 2:
        return {'ok': False, 'error': 'peers required'}
    peer_list = ';'.join(peer_ids)
    group_id = generate_random_hash()
    img_name = 'No_image.png'
    query_database(getattr(init, 'pool_main_db', None), 'INSERT INTO ChatGroups (id, name, peers, owner_id, image) VALUES (?, ?, ?, ?, ?)', (group_id, name, peer_list, my_id, img_name))
    img_path = os.path.join(getattr(init, 'PATH_GROUP_IMAGES', ''), img_name)
    with open(img_path, 'rb') as f:
        image_data = f.read()
    for peer in peer_ids:
        if peer == my_id:
            continue
        peer_info = filter_dictionary(init.PEERS, 'peer_id', peer)
        if not peer_info:
            continue
        dest = peer_info[0]
        send_packet(init.ME['ip'], init.ME['ip_local'], '', ip_to_send_packet_to(dest.get('ip'), dest.get('ip_local')), dest.get('ip_local') or '', '', init.CMD_PER_GRP, init.PRT_NOR, '', 0, 0, '', [group_id, name, peer_list, my_id, img_name], [], [image_data], '', '', [], [], [], retry_policy='critical', src_peer_id=my_id, dst_peer_id=peer)
    return {'ok': True, 'group_id': group_id, 'name': name, 'peers': peer_ids}

def _neurons_balance(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Misc import check_neurons
    check_neurons()
    bal = init.ME['user_neurons']
    return {'ok': True, 'balance': bal}

def _neurons_askgive(args: Dict[str, Any], ask: bool) -> Dict[str, Any]:
    from Support.PeerIPValidator import get_peer_ip_validated
    from Support.SendData import send_packet
    from Support.Utils import generate_random_hash, get_timestamp_in_seconds, ip_to_send_packet_to, query_database
    resolved = _resolve_peer(str(args.get('peer') or args.get('name') or args.get('peer_id') or ''))
    if not resolved.get('ok'):
        return resolved
    amount = str(args.get('amount') or args.get('neurons') or '').strip()
    if not amount:
        return {'ok': False, 'error': 'amount required'}
    try:
        amount_value = float(amount)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'invalid amount'}
    if amount_value <= 0:
        return {'ok': False, 'error': 'amount must be positive'}
    message = str(args.get('message') or args.get('text') or '').strip()
    peer_id = resolved['peer']['peer_id']
    pool = getattr(init, 'pool_main_db', None)
    ip, ip_local = get_peer_ip_validated(peer_id, pool, query_database)
    if not ip:
        return {'ok': False, 'error': 'peer ip unavailable'}
    if not ask:
        from Support.Misc import has_neuron_purchase, update_neurons
        if not has_neuron_purchase():
            return {'ok': False, 'error': 'give requires a completed Neuron purchase'}
        if update_neurons(-amount_value, 'I escrowed a Neuron donation.') is False:
            return {'ok': False, 'error': 'insufficient Neurons'}
    askgive_id = generate_random_hash()
    command = init.CMD_NEU_ASK if ask else init.CMD_NEU_GIV
    send_packet(init.ME['ip'], init.ME['ip_local'], '', ip_to_send_packet_to(ip, ip_local), ip_local, '', command, init.PRT_NOR, '', 0, 0, '', [init.ME['node_name'], str(amount_value), message, askgive_id], [], [], '', '', [], [], [])
    query_database(pool, 'INSERT INTO AskGive (id, peer_id, amount, message, date, type, reply) VALUES (?, ?, ?, ?, ?, ?, ?)', (askgive_id, peer_id, str(amount_value), message, get_timestamp_in_seconds(), 1 if ask else 0, -1))
    return {'ok': True, 'id': askgive_id, 'peer': resolved['peer'], 'amount': amount, 'ask': ask}

def _rooms_list(args: Dict[str, Any]) -> Dict[str, Any]:
    rooms = []
    for room in init.ROOMS:
        rooms.append({'id': room.get('room_id') or room.get('id'), 'name': room.get('name'), 'type': room.get('type')})
    current = getattr(init, 'selected_room_info', None) or {}
    return {'ok': True, 'rooms': rooms, 'count': len(rooms), 'current': current.get('name') or current.get('room_id')}

def _rooms_switch(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Misc import room_change
    want = str(args.get('room') or args.get('name') or args.get('room_id') or '').strip()
    if not want:
        return {'ok': False, 'error': 'room required'}
    low = want.lower()
    match = None
    names = []
    by_name = {}
    for room in init.ROOMS:
        rid = str(room.get('room_id') or room.get('id') or '')
        rname = str(room.get('name') or '')
        names.append(rname or rid)
        by_name[rname or rid] = room
        if rid == want or rname.lower() == low:
            match = room
            break
    if match is None:
        fuzzy = _fuzzy_one(want, names, cutoff=0.7)
        if fuzzy:
            match = by_name[fuzzy]
    if match is None:
        return {'ok': False, 'error': 'no room matched', 'query': want}
    room_change(match.get('room_id') or match.get('id'))
    return {'ok': True, 'room': {'id': match.get('room_id'), 'name': match.get('name')}}

def _website_rows(kind: str, directory) -> List[Dict[str, Any]]:
    from pathlib import Path
    from Support.WebsitesUtils import WebsiteManager
    rows = []
    for item in WebsiteManager.list_websites(Path(directory)):
        rows.append({'name': item.get('name'), 'description': item.get('description') or '', 'kind': kind})
    return rows

def _websites_list(args: Dict[str, Any]) -> Dict[str, Any]:
    drafts = _website_rows('draft', init.PATH_WEBSITES_WORKS)
    published = _website_rows('published', init.PATH_WEBSITES_PUBLISHED)
    return {'ok': True, 'drafts': drafts, 'published': published, 'count': len(drafts) + len(published)}

def _websites_publish(args: Dict[str, Any]) -> Dict[str, Any]:
    from pathlib import Path
    from Support.WebsitesUtils import WebsiteManager
    name = (args.get('name') or args.get('website') or args.get('website_name') or '').strip()
    if name.startswith('$'):
        name = ''
    drafts = _website_rows('draft', init.PATH_WEBSITES_WORKS)
    names = [w.get('name') for w in drafts if w.get('name')]
    if not name and len(names) == 1:
        name = names[0]
    if not name:
        return {
            'ok': False,
            'error': 'name required',
            'message': 'No website draft name — build the page first, then publish.',
        }
    resolved = _fuzzy_one(name, names, cutoff=0.7) or name
    src = Path(init.PATH_WEBSITES_WORKS) / resolved
    if not src.is_dir():
        # Case-insensitive folder match under Works.
        try:
            works = Path(init.PATH_WEBSITES_WORKS)
            low = resolved.lower()
            for child in works.iterdir():
                if child.is_dir() and child.name.lower() == low:
                    src = child
                    resolved = child.name
                    break
        except Exception:
            pass
    if not src.is_dir():
        # Fall back to the newest draft folder (build may have used a sanitized name).
        try:
            works = Path(init.PATH_WEBSITES_WORKS)
            candidates = []
            for child in works.iterdir():
                if child.is_dir() and (child / 'index.html').is_file():
                    try:
                        candidates.append((child.stat().st_mtime, child))
                    except Exception:
                        candidates.append((0, child))
            candidates.sort(reverse=True)
            if candidates:
                src = candidates[0][1]
                resolved = src.name
        except Exception:
            pass
    if not src.is_dir():
        return {
            'ok': False,
            'error': 'source_folder_not_found',
            'message': 'Draft website folder not found for "%s". Build the page first.' % (
                name or resolved
            ),
            'name': resolved or name,
        }
    dest = Path(init.PATH_WEBSITES_PUBLISHED) / resolved
    if dest.exists():
        # Replacing a prior publish of the same draft.
        try:
            import shutil
            shutil.rmtree(str(dest), ignore_errors=True)
        except Exception:
            pass
    ok, message = WebsiteManager.move_website(src, dest)
    return {'ok': bool(ok), 'name': resolved, 'error': '' if ok else message}

def _call_invite(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.PhoneCall import send_outgoing_call_invite
    resolved = _resolve_peer(str(args.get('peer') or args.get('name') or args.get('peer_id') or ''))
    if not resolved.get('ok'):
        return resolved
    video = args.get('video')
    if isinstance(video, str):
        video = video.strip().lower() in ('1', 'true', 'yes', 'video')
    call_type = 1 if video else 0
    room_id = send_outgoing_call_invite([resolved['peer']['peer_id']], call_type)
    return {'ok': True, 'peer': resolved['peer'], 'video': bool(video), 'room_id': room_id}

def _contacts_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    limit = int(args.get('limit') or 30)
    limit = max(1, min(limit, 80))
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT first_name, last_name, phone, email FROM Contacts', (), commit=False) or []
    contacts = []
    for row in rows[:limit]:
        contacts.append({'first_name': row[0] or '', 'last_name': row[1] or '', 'phone': row[2] or '', 'email': row[3] or ''})
    return {'ok': True, 'contacts': contacts, 'count': len(contacts)}

def _contacts_get(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    want = str(args.get('name') or args.get('query') or '').strip()
    if not want:
        return {'ok': False, 'error': 'name required'}
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT first_name, last_name, phone, email FROM Contacts', (), commit=False) or []
    names = []
    by_name = {}
    low = want.lower()
    exact = None
    for row in rows:
        first = str(row[0] or '').strip()
        last = str(row[1] or '').strip()
        full = ('%s %s' % (first, last)).strip()
        rec = {'first_name': first, 'last_name': last, 'phone': row[2] or '', 'email': row[3] or ''}
        if full.lower() == low or first.lower() == low or last.lower() == low:
            exact = rec
            break
        if full:
            names.append(full)
            by_name[full] = rec
    if exact:
        return {'ok': True, 'contact': exact, 'email': exact.get('email'), 'name': want}
    fuzzy = _fuzzy_one(want, names, cutoff=0.72)
    if fuzzy:
        rec = by_name[fuzzy]
        return {'ok': True, 'contact': rec, 'email': rec.get('email'), 'name': fuzzy}
    return {'ok': False, 'error': 'no contact matched', 'query': want}

def _contacts_invite(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.ContactsInviteDialog import _send_email, _send_whatsapp
    name = (args.get('name') or args.get('first_name') or '').strip()
    email = (args.get('email') or '').strip()
    phone = (args.get('phone') or args.get('whatsapp') or '').strip()
    sent = []
    if email:
        _send_email(email, name)
        sent.append('email')
    if phone:
        _send_whatsapp(phone, name)
        sent.append('whatsapp')
    if not sent:
        return {'ok': False, 'error': 'email or phone required'}
    return {'ok': True, 'name': name, 'via': sent}

def _lottery_status(args: Dict[str, Any]) -> Dict[str, Any]:
    lot = init.Lottery
    return {'ok': True, 'jackpot': lot.jackpot, 'running': bool(lot.running), 'executed': bool(lot.executed)}

def _market_search(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    from Support.HumanSkillBooking import is_human_skills_category, is_human_skills_ad
    query = (args.get('query') or args.get('q') or args.get('text') or '').strip()
    category = (args.get('category') or '').strip()
    limit = int(args.get('limit') or 12)
    limit = max(1, min(limit, 30))
    sql = 'SELECT ad_id, title, subtitle, description, price, currency, location, category, tags, peer_id FROM MarketAds WHERE status=0 OR status=1'
    params = []
    if query:
        sql += ' AND (LOWER(title) LIKE ? OR LOWER(subtitle) LIKE ? OR LOWER(description) LIKE ? OR LOWER(tags) LIKE ? OR LOWER(location) LIKE ?)'
        like = '%' + query.lower() + '%'
        params.extend([like, like, like, like, like])
    if category:
        sql += ' AND category = ?'
        params.append(category)
    sql += ' ORDER BY created_date DESC'
    rows = query_database(getattr(init, 'pool_market_db', None), sql, tuple(params), commit=False) or []
    ads = []
    for row in rows[:limit]:
        tags = row[8] or ''
        cat = row[7] or ''
        is_skill = is_human_skills_ad(cat, tags)
        session_type = 'seminar' if 'call_type:3' in str(tags) else 'one_on_one' if is_skill else ''
        ads.append({'ad_id': row[0], 'title': row[1], 'subtitle': row[2], 'description': (row[3] or '')[:180], 'price': row[4], 'currency': row[5], 'location': row[6], 'category': cat, 'tags': tags, 'peer_id': row[9], 'is_human_skill': is_skill, 'session_type': session_type, 'bookable': is_skill})
    return {'ok': True, 'ads': ads, 'count': len(ads), 'query': query}

def _market_publish(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.SendData import send_packet
    from Support.Utils import generate_random_hash, get_timestamp_in_seconds, ip_to_send_packet_to, query_database
    from Support.Calendar import create_human_skill_slots
    from Support.HumanSkillBooking import is_human_skills_category, human_skills_tags
    title = (args.get('title') or '').strip()
    description = (args.get('description') or args.get('text') or '').strip()
    category = (args.get('category') or '').strip()
    if not title or not description or (not category):
        return {'ok': False, 'error': 'title, description and category required'}
    subtitle = (args.get('subtitle') or '').strip()
    location = (args.get('location') or '').strip()
    tags = (args.get('tags') or '').strip()
    contact = (args.get('contact') or '').strip()
    price = args.get('price') or 0
    currency = (args.get('currency') or 'FREE').strip() or 'FREE'
    skill_slots = []
    if is_human_skills_category(category):
        session_mode = (args.get('session_mode') or args.get('session_type') or 'one_on_one').strip().lower()
        try:
            max_spectators = int(args.get('max_spectators') or args.get('max_seats') or 0)
        except Exception:
            max_spectators = 0
        if session_mode in ('seminar', 'multi', 'multi_peer', 'group'):
            call_type = 3
            if max_spectators < 2:
                max_spectators = 2
        else:
            call_type = 1
            max_spectators = 0
            session_mode = 'one_on_one'
        availability = _parse_availability_arg(args.get('availability') or args.get('slots') or [])
        if not availability:
            return {'ok': False, 'error': 'needs_availability', 'hint': 'Human Skills requires availability slots as [{start_ts,end_ts}, ...]', 'session_mode': session_mode}
        cur_l = currency.lower()
        if cur_l not in ('free', 'neurons (n)', 'neurons', 'n'):
            return {'ok': False, 'error': 'Human Skills require currency FREE or Neurons (N)'}
        if cur_l in ('neurons', 'n'):
            currency = 'Neurons (N)'
        try:
            price_f = 0.0 if cur_l == 'free' else float(price or 0)
        except Exception:
            return {'ok': False, 'error': 'invalid Neuron price'}
        if cur_l != 'free' and price_f < 0:
            return {'ok': False, 'error': 'invalid Neuron price'}
        if cur_l == 'free':
            price = 0
            price_f = 0.0
        else:
            price = price_f
        tags = human_skills_tags(session_mode, max_spectators, tags)
        ad_id = generate_random_hash()
        created = create_human_skill_slots(ad_id, title, availability, call_type=call_type, max_spectators=max_spectators, price=price_f, currency=currency)
        if not created.get('ok'):
            return {'ok': False, 'error': created.get('error') or 'could not create availability'}
        skill_slots = created.get('slot_ids') or []
    else:
        ad_id = generate_random_hash()
    timestamp = get_timestamp_in_seconds()
    node_types = [init.NODE_TYPE_APP_SERVER, init.NODE_TYPE_DB_SERVER, init.NODE_TYPE_KICKSTARTER, init.NODE_TYPE_SUPER_DB_SERVER]
    if init.ME['node_type'] in node_types:
        query_database(getattr(init, 'pool_market_db', None), 'INSERT OR REPLACE INTO MarketAds (ad_id, peer_id, title, subtitle, description, price, currency, location, tags, contact, category, image_list, created_date, status, image_list_data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (ad_id, init.ME['id'], title, subtitle, description, price, currency, location, tags, contact, category, '', timestamp, 0, None))
    sent = 0
    for peer in init.PEERS:
        if peer['online'] and peer['type'] in node_types:
            send_packet(init.ME['ip'], init.ME['ip_local'], '', ip_to_send_packet_to(peer['ip'], peer['ip_local']), peer['ip_local'], '', init.CMD_NWS_ADS, init.PRT_LOW, '', 0, 0, '', [ad_id, init.ME['id'], title, subtitle, description, price, currency, location, tags, contact, category, '', timestamp, 0, 4], [init.ME['id'], init.ME['node_name'], init.ME['ip'], init.ME['ip_local'], init.ME['node_type']], [], '', '', [], [], [])
            sent += 1
    return {'ok': True, 'ad_id': ad_id, 'title': title, 'servers': sent, 'slot_ids': skill_slots, 'is_human_skill': bool(skill_slots)}

def _market_find_ad(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    from Support.HumanSkillBooking import is_human_skills_ad
    ad_id = (args.get('ad_id') or args.get('id') or '').strip()
    title = (args.get('title') or args.get('name') or args.get('query') or '').strip()
    pool = getattr(init, 'pool_market_db', None)

    def _row_to_ad(row):
        tags = row[5] if len(row) > 5 else ''
        category = row[4] if len(row) > 4 else ''
        is_skill = is_human_skills_ad(category, tags)
        return {'ad_id': row[0], 'peer_id': row[1], 'title': row[2], 'contact': row[3], 'category': category, 'tags': tags, 'price': row[6] if len(row) > 6 else 0, 'currency': row[7] if len(row) > 7 else '', 'is_human_skill': is_skill}
    if ad_id:
        rows = query_database(pool, 'SELECT ad_id, peer_id, title, contact, category, tags, price, currency FROM MarketAds WHERE ad_id = ?', (ad_id,), commit=False) or []
        if rows:
            return {'ok': True, 'ad': _row_to_ad(rows[0])}
    if title:
        rows = query_database(pool, 'SELECT ad_id, peer_id, title, contact, category, tags, price, currency FROM MarketAds WHERE LOWER(title) LIKE ? ORDER BY created_date DESC', ('%' + title.lower() + '%',), commit=False) or []
        if rows:
            return {'ok': True, 'ad': _row_to_ad(rows[0])}
    return {'ok': False, 'error': 'ad not found'}

def _market_slots(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.HumanSkillBooking import parse_skill_offer
    found = _market_find_ad(args)
    if not found.get('ok'):
        return found
    ad = found['ad']
    if not ad.get('is_human_skill'):
        return {'ok': False, 'error': 'ad is not a Human Skills offer'}
    from Support.Utils import query_database
    rows = query_database(getattr(init, 'pool_market_db', None), 'SELECT description FROM MarketAds WHERE ad_id = ? LIMIT 1', (ad.get('ad_id'),), commit=False) or []
    description = rows[0][0] if rows else ''
    offer = parse_skill_offer(description)
    return {'ok': True, 'ad_id': ad.get('ad_id'), 'teacher_peer_id': ad.get('peer_id'), 'slots': offer.get('slots') or [], 'offline': True}

def _market_book_skill(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.HumanSkillBooking import book_skill_seat_offline
    slot_id = str(args.get('slot_id') or args.get('id') or '').strip()
    if not slot_id:
        return {'ok': False, 'error': 'slot_id required'}
    found = _market_find_ad(args)
    if not found.get('ok'):
        return found
    ad = found['ad']
    if not ad.get('is_human_skill'):
        return {'ok': False, 'error': 'ad is not a Human Skills offer'}
    return book_skill_seat_offline(ad.get('ad_id'), slot_id, teacher_peer_id=ad.get('peer_id'), title=ad.get('title'), price=ad.get('price'), currency=ad.get('currency'))

def _market_buy_or_book(args: Dict[str, Any]) -> Dict[str, Any]:
    found = _market_find_ad(args)
    if found.get('ok') and (found.get('ad') or {}).get('is_human_skill'):
        if args.get('slot_id'):
            return _market_book_skill(args)
        slots = _market_slots({'ad_id': found['ad'].get('ad_id')})
        return {'ok': False, 'error': 'use_market_book_skill', 'hint': 'This is a Human Skills offer. Use market.slots then market.book_skill with a slot_id.', 'ad': found['ad'], 'slots': slots.get('slots') if slots.get('ok') else []}
    return _market_contact(args, buy=True)

def _market_contact(args: Dict[str, Any], buy: bool) -> Dict[str, Any]:
    found = _market_find_ad(args)
    if not found.get('ok'):
        return found
    ad = found['ad']
    if buy and ad.get('is_human_skill'):
        return _market_buy_or_book(args)
    peer_id = ad.get('peer_id') or ''
    if not peer_id:
        return {'ok': False, 'error': 'ad has no seller peer'}
    msg = (args.get('message') or args.get('text') or '').strip()
    if not msg:
        if buy:
            msg = 'I would like to buy "%s".' % (ad.get('title') or 'your item')
        else:
            return {'ok': False, 'error': 'message required'}
    return _chat_send({'peer': peer_id, 'message': apply_neury_mark(msg)})

def _barters_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT barter_id, community1_id, community2_id, start_date, last_bid_date, end_date, my_turn FROM CommunityBarters', (), commit=False) or []
    barters = []
    for row in rows:
        barters.append({'barter_id': row[0], 'community1_id': row[1], 'community2_id': row[2], 'start_date': row[3], 'last_bid_date': row[4], 'end_date': row[5], 'my_turn': row[6]})
    return {'ok': True, 'barters': barters, 'count': len(barters)}

def _transactions_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    limit = int(args.get('limit') or 20)
    limit = max(1, min(limit, 50))
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT bill_id, amount, timestamp, printed FROM Bills', '', commit=False) or []
    bills = []
    for row in rows[:limit]:
        when = row[2]
        try:
            when = datetime.fromtimestamp(float(row[2])).strftime('%Y-%m-%d')
        except Exception:
            pass
        bills.append({'bill_id': row[0], 'amount': row[1], 'date': when, 'printed': row[3]})
    return {'ok': True, 'bills': bills, 'count': len(bills)}

def _apps_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    limit = int(args.get('limit') or 30)
    limit = max(1, min(limit, 80))
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT app_id, name, description, developer, cost, version, date_purchased FROM Apps ORDER BY name', (), commit=False) or []
    apps = []
    for row in rows[:limit]:
        apps.append({'app_id': row[0], 'name': row[1], 'description': (row[2] or '')[:120], 'developer': row[3], 'cost': row[4], 'version': row[5], 'purchased': bool(row[6])})
    return {'ok': True, 'apps': apps, 'count': len(apps)}

def _files_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    room = getattr(init, 'selected_room_info', None) or {}
    room_id = room.get('room_id') or room.get('id')
    if not room_id:
        return {'ok': False, 'error': 'open a room first'}
    limit = int(args.get('limit') or 30)
    limit = max(1, min(limit, 80))
    rows = query_database(getattr(init, 'pool_main_db', None), "SELECT icon_id, label, path, type, description, tags FROM Icons WHERE room_id = ? AND label <> 'Recycle bin' AND type <> '#folder'", (room_id,), commit=False) or []
    files = []
    for row in rows[:limit]:
        files.append({'icon_id': row[0], 'label': row[1], 'path': _neury_sandbox_redact_path(row[2]), 'type': row[3], 'description': row[4] or '', 'tags': row[5] or ''})
    return {'ok': True, 'files': files, 'count': len(files), 'room_id': room_id}

def _public_files_search(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    query = (args.get('query') or args.get('q') or args.get('text') or '').strip().lower()
    if not query:
        return {'ok': False, 'error': 'query required'}
    limit = int(args.get('limit') or 12)
    limit = max(1, min(limit, 30))
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT peer_name, file_name, description, tags FROM Public_files', (), commit=False) or []
    hits = []
    for row in rows:
        hay = ' '.join([str(row[0] or ''), str(row[1] or ''), str(row[2] or ''), str(row[3] or '')]).lower()
        if query not in hay:
            continue
        hits.append({'peer': row[0], 'file': row[1], 'description': (row[2] or '')[:120], 'tags': row[3] or ''})
        if len(hits) >= limit:
            break
    return {'ok': True, 'hits': hits, 'count': len(hits), 'query': query}

def _diary_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    limit = int(args.get('limit') or 8)
    limit = max(1, min(limit, 20))
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT id, peer_id, message, my_timestamp FROM Diary ORDER BY my_timestamp DESC', (), commit=False) or []
    posts = []
    for row in rows[:limit]:
        when = row[3]
        try:
            when = datetime.fromtimestamp(int(row[3])).strftime('%Y-%m-%d %H:%M')
        except Exception:
            pass
        posts.append({'id': row[0], 'peer_id': row[1], 'preview': _maybe_decrypt_preview(row[2] or ''), 'date': when})
    return {'ok': True, 'posts': posts, 'count': len(posts)}

def _macros_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT name, function FROM Functions ORDER BY name', '', commit=False) or []
    macros = [{'name': row[0], 'type': row[1]} for row in rows]
    return {'ok': True, 'macros': macros, 'count': len(macros)}

def _macros_run(args: Dict[str, Any]) -> Dict[str, Any]:
    want = (args.get('name') or args.get('macro') or '').strip()
    if not want:
        return {'ok': False, 'error': 'name required'}
    listed = _macros_list({})
    names = [m.get('name') for m in listed.get('macros') or [] if m.get('name')]
    resolved = _fuzzy_one(want, names, cutoff=0.7) or want
    bar = getattr(init, 'macros_bar', None)
    bar.execute_function(resolved)
    return {'ok': True, 'name': resolved}

def _goods_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    room = getattr(init, 'selected_room_info', None) or {}
    room_id = room.get('room_id') or room.get('id')
    if not room_id:
        return {'ok': False, 'error': 'open a community room first'}
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT name, description, quantity, neurons FROM CommunityGoodsServices WHERE room_id = ?', (str(room_id),), commit=False) or []
    if not rows:
        rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT name, description, quantity, neurons FROM CommunityGoodsServices WHERE room_id = ?', (str(room_id),), commit=False) or []
    goods = []
    for row in rows:
        goods.append({'name': row[0], 'description': (row[1] or '')[:120], 'quantity': row[2], 'neurons': row[3]})
    return {'ok': True, 'goods': goods, 'count': len(goods)}

def _current_room() -> Optional[Dict[str, Any]]:
    room = getattr(init, 'selected_room_info', None)
    if not room:
        return None
    if isinstance(room, dict):
        return room
    return None

def _require_community_owner() -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    room = _current_room()
    if not room:
        return (None, {'ok': False, 'error': 'open a COMMUNITY room you own first'})
    if room.get('type') != getattr(init, 'ROOM_TYPE_COMMUNITY', 'Community'):
        return (None, {'ok': False, 'error': 'must be in a COMMUNITY room you own'})
    if str(room.get('owner_id') or '') != str(init.ME.get('id') or ''):
        return (None, {'ok': False, 'error': 'only the community owner can do this'})
    return (room, None)

def _icon_auth_bits(icon: Dict[str, Any]) -> Tuple[str, str, str]:
    auth = str(icon.get('authorizations') or '000')
    auth = (auth + '000')[:3]
    return (auth[0], auth[1], auth[2])

def _can_file_action(icon: Dict[str, Any], action: str) -> bool:
    me = str(init.ME.get('id') or '')
    if str(icon.get('owner_id') or '') == me:
        return True
    room = _current_room() or {}
    rtype = room.get('type')
    if rtype not in (getattr(init, 'ROOM_TYPE_GROUP', 'Group'), getattr(init, 'ROOM_TYPE_COMMUNITY', 'Community')):
        return False
    open_bit, delete_bit, download_bit = _icon_auth_bits(icon)
    if action == 'delete':
        return delete_bit == '1'
    if action == 'download':
        return download_bit == '1'
    if action == 'open':
        return open_bit == '1'
    return False

def _resolve_room_icon(file_key: str) -> Dict[str, Any]:
    from Support.Dictionaries import filter_dictionary
    want = (file_key or '').strip()
    if not want:
        return {'ok': False, 'error': 'file required'}
    room = _current_room()
    if not room:
        return {'ok': False, 'error': 'open a room first'}
    room_id = room.get('room_id') or room.get('id')
    icons = [i for i in getattr(init, 'DESKTOPICONS', None) or [] if str(i.get('room_id') or '') == str(room_id) and str(i.get('label') or '') != getattr(init, 'LABEL_RECYCLE_BIN', 'Recycle bin') and (str(i.get('type') or '') != '#folder')]
    low = want.lower()
    match = None
    names = []
    by_name = {}
    for icon in icons:
        iid = str(icon.get('icon_id') or '')
        label = str(icon.get('label') or '')
        names.append(label or iid)
        by_name[label or iid] = icon
        if iid == want or label.lower() == low:
            match = icon
            break
    if match is None:
        fuzzy = _fuzzy_one(want, names, cutoff=0.7)
        if fuzzy:
            match = by_name[fuzzy]
    if match is None:
        found = filter_dictionary(init.DESKTOPICONS, 'icon_id', want) or []
        for icon in found:
            if str(icon.get('room_id') or '') == str(room_id):
                match = icon
                break
    if match is None:
        return {'ok': False, 'error': 'no file matched', 'query': want}
    return {'ok': True, 'icon': match, 'room': room}

def _find_phone_call():
    try:
        from PySide6.QtWidgets import QApplication
        for w in QApplication.topLevelWidgets():
            if w.__class__.__name__ == 'PhoneCall' and hasattr(w, 'end_call'):
                return w
        for w in QApplication.allWidgets():
            if w.__class__.__name__ == 'PhoneCall' and hasattr(w, 'end_call'):
                return w
    except Exception:
        pass
    return None

def _resolve_community_room(name_or_id: str) -> Dict[str, Any]:
    want = (name_or_id or '').strip()
    if not want:
        return {'ok': False, 'error': 'community required'}
    low = want.lower()
    match = None
    names = []
    by_name = {}
    for room in init.ROOMS:
        if room.get('type') != getattr(init, 'ROOM_TYPE_COMMUNITY', 'Community'):
            continue
        rid = str(room.get('room_id') or room.get('id') or '')
        rname = str(room.get('name') or '')
        names.append(rname or rid)
        by_name[rname or rid] = room
        if rid == want or rname.lower() == low:
            match = room
            break
    if match is None:
        fuzzy = _fuzzy_one(want, names, cutoff=0.7)
        if fuzzy:
            match = by_name[fuzzy]
    if match is None:
        return {'ok': False, 'error': 'no community matched', 'query': want}
    return {'ok': True, 'room': match}

def _diary_follow(args: Dict[str, Any], follow: bool) -> Dict[str, Any]:
    from Support.Dictionaries import filter_dictionary
    from Support.SendData import send_packet
    from Support.Utils import ip_to_send_packet_to, query_database
    resolved = _resolve_peer(str(args.get('peer') or args.get('name') or args.get('peer_id') or ''))
    if not resolved.get('ok'):
        return resolved
    peer_id = resolved['peer']['peer_id']
    peer_info = filter_dictionary(init.PEERS, 'peer_id', peer_id)
    if not peer_info:
        return {'ok': False, 'error': 'peer not reachable'}
    dest = peer_info[0]
    new_val = 1 if follow else 0
    cmd = init.CMD_DRY_PER if follow else init.CMD_DRY_UNP
    send_packet(init.ME['ip'], init.ME['ip_local'], '', ip_to_send_packet_to(dest.get('ip'), dest.get('ip_local')), dest.get('ip_local') or '', '', cmd, init.PRT_NOR, '', 0, 0, '', [init.ME['id']], [], [], '', '', [], [], [], retry_policy='critical', src_peer_id=init.ME['id'], dst_peer_id=peer_id)
    query_database(getattr(init, 'pool_main_db', None), 'UPDATE Peers SET peering = ? WHERE peer_id = ?', (new_val, peer_id))
    dest['peering'] = new_val
    return {'ok': True, 'peer': resolved['peer'], 'following': follow}

def _diary_comment(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.DiaryWidgets1 import save_reply_to_db
    from Support.Dictionaries import filter_dictionary
    from Support.EncDec import encrypt
    from Support.SendData import send_packet
    from Support.Utils import generate_random_hash, get_timestamp_in_seconds, ip_to_send_packet_to, query_database
    post_id = (args.get('post_id') or args.get('id') or '').strip()
    raw = (args.get('message') or args.get('text') or args.get('comment') or '').strip()
    if not post_id or not raw:
        return {'ok': False, 'error': 'post_id and message required'}
    text = apply_neury_mark(raw)
    my_id = _my_id()
    ts = get_timestamp_in_seconds()
    save_reply_to_db(post_id, my_id, text, '', '', str(ts))
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT peer_id FROM Diary WHERE id = ?', (post_id,), commit=False) or []
    if rows:
        owner_id = str(rows[0][0] or '')
        if owner_id and owner_id != my_id:
            peer_info = filter_dictionary(init.PEERS, 'peer_id', owner_id)
            if peer_info:
                dest = peer_info[0]
                encrypted = encrypt(text)
                comment_id = generate_random_hash()
                send_packet(init.ME['ip'], init.ME['ip_local'], '', ip_to_send_packet_to(dest.get('ip'), dest.get('ip_local')), dest.get('ip_local') or '', '', init.CMD_DRY_CMT, init.PRT_NOR, '', 0, 0, '', [comment_id, post_id, my_id, ts], [encrypted, ''], [b''], '', '', [], [], [], src_peer_id=my_id, dst_peer_id=owner_id)
    return {'ok': True, 'post_id': post_id, 'marked': True}

def _diary_like(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.DiaryWidgets1 import toggle_heart_for_diary_post
    from Support.Dictionaries import filter_dictionary
    from Support.SendData import send_packet
    from Support.Utils import ip_to_send_packet_to, query_database
    post_id = (args.get('post_id') or args.get('id') or '').strip()
    if not post_id:
        return {'ok': False, 'error': 'post_id required'}
    my_id = _my_id()
    toggle_heart_for_diary_post(post_id, my_id)
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT peer_id FROM Diary WHERE id = ?', (post_id,), commit=False) or []
    if rows:
        owner_id = str(rows[0][0] or '')
        if owner_id and owner_id != my_id:
            peer_info = filter_dictionary(init.PEERS, 'peer_id', owner_id)
            if peer_info:
                dest = peer_info[0]
                send_packet(init.ME['ip'], init.ME['ip_local'], '', ip_to_send_packet_to(dest.get('ip'), dest.get('ip_local')), dest.get('ip_local') or '', '', init.CMD_DRY_HRT, init.PRT_NOR, '', 0, 0, '', [post_id, my_id], [], [], '', '', [], [], [], src_peer_id=my_id, dst_peer_id=owner_id)
    return {'ok': True, 'post_id': post_id}

def _diary_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    post_id = (args.get('post_id') or args.get('id') or '').strip()
    if not post_id:
        return {'ok': False, 'error': 'post_id required'}
    my_id = _my_id()
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT peer_id FROM Diary WHERE id = ?', (post_id,), commit=False) or []
    if not rows:
        return {'ok': False, 'error': 'post not found'}
    if str(rows[0][0] or '') != str(my_id):
        return {'ok': False, 'error': 'can only delete your own diary posts'}
    query_database(getattr(init, 'pool_main_db', None), 'DELETE FROM Diary WHERE id = ?', (post_id,))
    try:
        query_database(getattr(init, 'pool_main_db', None), 'DELETE FROM DiaryReplies WHERE post_id = ?', (post_id,))
    except Exception:
        pass
    return {'ok': True, 'post_id': post_id}

def _files_download(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Files import retrieve_neuronized_files
    key = str(args.get('icon_id') or args.get('file') or args.get('path') or args.get('label') or '')
    resolved = _resolve_room_icon(key)
    if not resolved.get('ok'):
        return resolved
    icon = resolved['icon']
    if not _can_file_action(icon, 'download'):
        return {'ok': False, 'error': 'no download authorization for this file'}
    init.selected_icon_id = icon.get('icon_id')
    retrieve_neuronized_files(getattr(init, 'NEURONIZE_FILES', 0), skip_recompose=False)
    return {'ok': True, 'icon_id': icon.get('icon_id'), 'label': icon.get('label'), 'started': True}

def _files_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Files import delete_file
    key = str(args.get('icon_id') or args.get('file') or args.get('path') or args.get('label') or '')
    resolved = _resolve_room_icon(key)
    if not resolved.get('ok'):
        return resolved
    icon = resolved['icon']
    if not _can_file_action(icon, 'delete'):
        return {'ok': False, 'error': 'no delete authorization for this file'}
    delete_file(icon)
    return {'ok': True, 'icon_id': icon.get('icon_id'), 'label': icon.get('label')}

def _public_files_set(args: Dict[str, Any], make_public: bool) -> Dict[str, Any]:
    import json
    from Support.Dictionaries import set_dictionary
    from Support.Files import needs_crypto_reneuronize, reneuronize_for_visibility_change, send_cmd_pub_pub
    from Support.Utils import generate_random_hash, query_database
    key = str(args.get('icon_id') or args.get('file') or args.get('path') or args.get('label') or '')
    resolved = _resolve_room_icon(key)
    if not resolved.get('ok'):
        return resolved
    icon = resolved['icon']
    me = str(init.ME.get('id') or '')
    if str(icon.get('owner_id') or '') != me:
        return {'ok': False, 'error': 'can only publish/unpublish files you own'}
    room = resolved['room']
    room_id = room.get('room_id') or room.get('id')
    icon_id = icon.get('icon_id')
    file_name = icon.get('label') or ''
    description = str(args.get('description') if args.get('description') is not None else icon.get('description') or '')
    tags = str(args.get('tags') if args.get('tags') is not None else icon.get('tags') or '')
    publish_info = {'room_id': room_id, 'file_name': file_name, 'description': description, 'tags': tags}
    deferred = False
    if needs_crypto_reneuronize(icon_id, make_public):
        result = reneuronize_for_visibility_change(icon_id, make_public, publish_info if make_public else None)
        if result is None:
            return {'ok': False, 'error': 'could not start visibility change (local file missing or upload busy)'}
        deferred = bool(make_public and result)
    set_dictionary(init.DESKTOPICONS, search_conditions={'room_id': room_id, 'icon_id': icon_id}, updates={'public': 1 if make_public else 0, 'description': description, 'tags': tags})
    if deferred:
        return {'ok': True, 'icon_id': icon_id, 'public': make_public, 'deferred': True}
    chunks = ''
    if make_public:
        chunk_list = query_database(getattr(init, 'pool_main_db', None), 'SELECT * FROM ChunksSent WHERE icon_id = ?', (icon_id,), commit=False) or []
        chunks = json.dumps(chunk_list) if chunk_list else ''
    delta_list = [[1 if make_public else 0, generate_random_hash(), init.ME['id'], room_id, icon_id, init.ME['node_name'], file_name, description, chunks, init.ME['ip'], tags]]
    sent = send_cmd_pub_pub(delta_list, [], [])
    return {'ok': True, 'icon_id': icon_id, 'public': make_public, 'servers': sent}

def _rooms_create(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Dictionaries import add_dictionary, filter_dictionary, save_data, set_dictionary
    from Support.Utils import generate_random_hash, get_timestamp, query_database
    name = (args.get('name') or args.get('title') or '').strip()
    rtype = (args.get('type') or args.get('room_type') or '').strip()
    if not name:
        return {'ok': False, 'error': 'name required'}
    if not rtype:
        return {'ok': False, 'error': 'type required (Personal/Group/Community/Professional)'}
    for cand in (getattr(init, 'ROOM_TYPE_PERSONAL', 'Personal'), getattr(init, 'ROOM_TYPE_GROUP', 'Group'), getattr(init, 'ROOM_TYPE_COMMUNITY', 'Community'), getattr(init, 'ROOM_TYPE_BUSINESS', 'Business'), getattr(init, 'ROOM_TYPE_PROFESSIONAL', 'Professional')):
        if rtype.lower() == str(cand).lower():
            rtype = cand
            break
    if filter_dictionary(init.ROOMS, 'name', name):
        return {'ok': False, 'error': 'a room with that name already exists'}
    room_id = generate_random_hash()
    peer_ids = []
    for part in _split_names(args.get('peers') or args.get('peer') or ''):
        resolved = _resolve_peer(part)
        if resolved.get('ok'):
            peer_ids.append(resolved['peer']['peer_id'])
    me = init.ME['id']
    if rtype in (getattr(init, 'ROOM_TYPE_GROUP', 'Group'), getattr(init, 'ROOM_TYPE_COMMUNITY', 'Community'), getattr(init, 'ROOM_TYPE_PROFESSIONAL', 'Professional')):
        if me not in peer_ids:
            peer_ids.append(me)
    allowed = ';'.join(peer_ids)
    description = str(args.get('description') or '')
    image = 'Default.jpg'
    room_data_value = ''
    if rtype == getattr(init, 'ROOM_TYPE_PROFESSIONAL', 'Professional'):
        from Support.ProfessionalChannel import default_channel_data, encode_room_data, sanitize_channel_slug
        room_data_value = encode_room_data(default_channel_data(room_id, name, description, sanitize_channel_slug(name)))
    add_dictionary(init.ROOMS, [generate_random_hash(), room_id, name, rtype, description, image, me, allowed, 0, room_data_value])
    init.selected_room_info = filter_dictionary(init.ROOMS, 'room_id', room_id)[0]
    try:
        from Support.RoomsUtil import manage_peers
        manage_peers(getattr(init, 'mainApplication', None), '', allowed, allowed)
    except Exception:
        pass
    icon_id = generate_random_hash()
    add_dictionary(init.DESKTOPICONS, [generate_random_hash(), icon_id, room_id, getattr(init, 'LABEL_RECYCLE_BIN', 'Recycle bin'), '', '0.9010417', '0.8796296', '', getattr(init, 'ICON_TYPE_RECYCLE_BIN', '#recycle'), get_timestamp(), '', '0', '0', '0', me, '', '0', '', '0'])
    room_folder = os.path.join(getattr(init, 'PATH_NEURON_ROOMS', ''), name)
    if room_folder and _neury_path_inside_install(room_folder) and (not os.path.exists(room_folder)):
        os.mkdir(room_folder)
    if rtype == getattr(init, 'ROOM_TYPE_COMMUNITY', 'Community'):
        for base in (getattr(init, 'PATH_COMMUNITY_MISC', ''), getattr(init, 'PATH_COMMUNITY_DOCUMENTS', ''), getattr(init, 'PATH_COMMUNITY_PHOTOS', '')):
            if base:
                path = os.path.join(base, room_id)
                if not os.path.exists(path):
                    os.mkdir(path)
        query_database(getattr(init, 'pool_main_db', None), 'INSERT INTO CommunityInfo (room_id, tags, email, phone, home_page, searchable, logo, location) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (room_id, '', init.ME.get('user_email') or '', init.ME.get('user_phone') or '', '', 0, '', ''))
        try:
            save_data(only_key='ROOMS')
        except Exception:
            pass
    channel_slug = ''
    if rtype == getattr(init, 'ROOM_TYPE_PROFESSIONAL', 'Professional'):
        from Support.ProfessionalChannel import create_professional_website, default_channel_data, encode_room_data, ensure_professional_folders
        ensure_professional_folders(room_id)
        try:
            channel_slug = create_professional_website(room_id, name, description)
        except Exception as e:
            return {'ok': False, 'error': 'room created but website failed: %s' % e, 'room_id': room_id}
        channel_data = default_channel_data(room_id, name, description, channel_slug)
        channel_data['home_page'] = channel_slug
        channel_data['channel_slug'] = channel_slug
        set_dictionary(init.ROOMS, search_conditions={'room_id': room_id}, updates={'data': encode_room_data(channel_data)})
    try:
        save_data(only_key='ROOMS')
    except Exception:
        pass
    try:
        from Support.ContextMenu import setupContextSubmenu
        setupContextSubmenu(getattr(init, 'mainApplication', None))
    except Exception:
        pass
    out = {'ok': True, 'room_id': room_id, 'name': name, 'type': rtype}
    if channel_slug:
        out['home_page'] = channel_slug
    return out

def _resolve_professional_room(args: Dict[str, Any]):
    from Support.ProfessionalChannel import studio_professional_room, owned_professional_rooms
    want = (args.get('room') or args.get('name') or args.get('channel') or '').strip()
    if want:
        for room in init.ROOMS or []:
            if str(room.get('type') or '').upper() != str(init.ROOM_TYPE_PROFESSIONAL).upper():
                continue
            if want.lower() in (str(room.get('room_id') or '').lower(), str(room.get('name') or '').lower()):
                return room
        for room in owned_professional_rooms():
            if want.lower() == str(room.get('name') or '').lower():
                return room
        return None
    sel = getattr(init, 'selected_room_info', None) or {}
    if str(sel.get('type') or '') == str(init.ROOM_TYPE_PROFESSIONAL):
        return sel
    return studio_professional_room()

def _find_episode_in_room(room, episode_ref):
    from Support.ProfessionalChannel import list_channel_episodes, _episode_key
    ref = str(episode_ref or '').strip().lower()
    if not ref:
        return None
    for ep in list_channel_episodes(room):
        if ref in (_episode_key(ep).lower(), str(ep.get('title') or '').lower(), str(ep.get('icon_id') or '').lower()):
            return ep
    return None

def _channel_status(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.ProfessionalChannel import get_room_channel_data, list_channel_episodes, channel_local_url
    room = _resolve_professional_room(args)
    if not room:
        return {'ok': False, 'error': 'no channel found'}
    data = get_room_channel_data(room)
    live = data.get('live') if isinstance(data.get('live'), dict) else {}
    eps = list_channel_episodes(room)
    slug = data.get('home_page') or data.get('channel_slug') or ''
    return {'ok': True, 'room_id': room.get('room_id'), 'name': room.get('name'), 'description': data.get('description') or room.get('description') or '', 'slug': slug, 'local_url': channel_local_url(slug) if slug else '', 'live': bool(live.get('active')), 'episode_count': len(eps), 'episodes': [{'key': str(ep.get('icon_id') or ep.get('published_at') or ep.get('title') or ''), 'title': ep.get('title') or 'Episode', 'views': int(ep.get('views') or 0), 'hearts': int(ep.get('hearts') or 0)} for ep in eps[:40]]}

def _channel_create(args: Dict[str, Any]) -> Dict[str, Any]:
    name = str(args.get('name') or args.get('title') or args.get('channel') or '').strip()
    if not name:
        return {'ok': False, 'error': 'name required'}
    return _rooms_create({'name': name, 'type': getattr(init, 'ROOM_TYPE_PROFESSIONAL', 'Professional'), 'description': args.get('description') or args.get('about') or '', 'peers': args.get('peers') or ''})

def _channel_rename(args: Dict[str, Any]) -> Dict[str, Any]:
    name = str(args.get('name') or args.get('title') or args.get('new_name') or '').strip()
    if not name:
        return {'ok': False, 'error': 'name required'}
    room = _resolve_professional_room({'room': args.get('room') or args.get('channel') or ''})
    if not room:
        return {'ok': False, 'error': 'no channel found'}
    from Support.ProfessionalChannel import update_channel_info
    return update_channel_info(room.get('room_id'), name=name, description=None)

def _channel_update(args: Dict[str, Any]) -> Dict[str, Any]:
    room = _resolve_professional_room({'room': args.get('room') or args.get('channel') or ''})
    if not room:
        room = _resolve_professional_room({})
    if not room:
        return {'ok': False, 'error': 'no channel found'}
    name = args.get('name') if args.get('name') is not None else args.get('title')
    description = args.get('description') if args.get('description') is not None else args.get('about')
    if name is None and description is None:
        return {'ok': False, 'error': 'name or description required'}
    from Support.ProfessionalChannel import update_channel_info
    return update_channel_info(room.get('room_id'), name=str(name).strip() if name is not None else None, description=None if description is None else str(description))

def _channel_open_studio(args: Dict[str, Any]) -> Dict[str, Any]:
    room = _resolve_professional_room(args)
    if not room:
        return {'ok': False, 'error': 'no channel found'}
    from Support.ChannelStudio import open_channel_studio
    open_channel_studio(room=room)
    return {'ok': True, 'room_id': room.get('room_id'), 'name': room.get('name')}

def _channel_site_open(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.ProfessionalChannel import open_channel_website
    room = _resolve_professional_room(args)
    if not room:
        return {'ok': False, 'error': 'no channel found'}
    local = args.get('local')
    if local is None:
        prefer_local = True
    else:
        prefer_local = bool(local) if not isinstance(local, str) else local.strip().lower() in ('1', 'true', 'yes')
    ok = open_channel_website(room, prefer_local=prefer_local)
    return {'ok': bool(ok), 'room_id': room.get('room_id')}

def _channel_go_live(args: Dict[str, Any]) -> Dict[str, Any]:
    room = _resolve_professional_room(args)
    if not room:
        return {'ok': False, 'error': 'no channel found'}
    room_id = str(room.get('room_id') or '')
    try:
        surface = str(getattr(init, 'neury_surface', '') or args.get('surface') or '').lower()
    except Exception:
        surface = ''
    if surface in ('web', 'projector') or not getattr(init, 'mainApplication', None):
        return {'ok': True, 'room_id': room_id, 'name': room.get('name'), 'url': '/phone?type=4&channel=%s' % room_id, 'text': 'Open Phone in Podcast mode to go live on %s.' % (room.get('name') or 'your channel')}
    try:
        from Support.PhoneInitiateCall import open_outgoing_call_drawer
        open_outgoing_call_drawer(call_type=4, channel_room_id=room_id, parent=getattr(init, 'mainApplication', None))
        return {'ok': True, 'room_id': room_id, 'name': room.get('name')}
    except Exception as e:
        return {'ok': False, 'error': str(e) or 'could not go live'}

def _channel_schedule_live(args: Dict[str, Any]) -> Dict[str, Any]:
    room = _resolve_professional_room(args)
    try:
        main = getattr(init, 'mainApplication', None)
        if main and hasattr(main, 'open_calendar'):
            main.open_calendar()
        elif main and hasattr(main, 'show_calendar'):
            main.show_calendar()
        else:
            from Support.Calendar import Calendar
            cal = Calendar(main)
            cal.show()
        return {'ok': True, 'room_id': (room or {}).get('room_id'), 'name': (room or {}).get('name')}
    except Exception as e:
        return {'ok': False, 'error': str(e) or 'could not open calendar'}

def _channel_restyle(args: Dict[str, Any]) -> Dict[str, Any]:
    room = _resolve_professional_room(args)
    if not room:
        return {'ok': False, 'error': 'no channel found'}
    prompt = str(args.get('prompt') or args.get('look') or args.get('style') or args.get('description') or '').strip()
    if not prompt:
        return {'ok': False, 'error': 'look/style prompt required'}
    from Support.ProfessionalChannel import restyle_channel_website
    return restyle_channel_website(room, prompt)

def _episode_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.ProfessionalChannel import list_channel_episodes, _episode_key
    room = _resolve_professional_room(args)
    if not room:
        return {'ok': False, 'error': 'no channel found'}
    eps = list_channel_episodes(room)
    return {'ok': True, 'room_id': room.get('room_id'), 'episodes': [{'key': _episode_key(ep), 'title': ep.get('title') or 'Episode', 'description': ep.get('description') or '', 'tags': ep.get('tags') or '', 'views': int(ep.get('views') or 0), 'hearts': int(ep.get('hearts') or 0)} for ep in eps]}

def _episode_publish(args: Dict[str, Any]) -> Dict[str, Any]:
    room = _resolve_professional_room(args)
    if not room:
        return {'ok': False, 'error': 'no channel found'}
    path = str(args.get('path') or args.get('file') or args.get('recording') or args.get('recording_path') or '').strip()
    if not path:
        return {'ok': False, 'error': 'path required'}
    open_site = args.get('open_site')
    if open_site is None:
        open_site = True
    elif isinstance(open_site, str):
        open_site = open_site.strip().lower() in ('1', 'true', 'yes')
    searchable = args.get('searchable')
    if searchable is None:
        searchable = True
    from Support.PodcastPublish import publish_episode_direct
    return publish_episode_direct(channel_room_id=str(room.get('room_id') or ''), recording_path=path, title=str(args.get('title') or '').strip(), tags=args.get('tags') or '', description=str(args.get('description') or '').strip(), searchable=1 if searchable else 0, delete_local=1 if args.get('delete_local') else 0, open_site=bool(open_site))

def _episode_update(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.ProfessionalChannel import update_channel_episode, _episode_key
    room = _resolve_professional_room(args)
    if not room:
        return {'ok': False, 'error': 'no channel found'}
    ep = _find_episode_in_room(room, args.get('episode') or args.get('title') or args.get('key'))
    if not ep:
        return {'ok': False, 'error': 'video not found'}
    fields = {}
    for key in ('title', 'description', 'tags'):
        if args.get(key) is not None:
            fields[key] = args.get(key)
    if args.get('searchable') is not None:
        fields['searchable'] = args.get('searchable')
    if args.get('new_title') is not None:
        fields['title'] = args.get('new_title')
    return update_channel_episode(room.get('room_id'), _episode_key(ep), fields)

def _episode_reorder(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.ProfessionalChannel import reorder_channel_episodes, list_channel_episodes, _episode_key
    room = _resolve_professional_room(args)
    if not room:
        return {'ok': False, 'error': 'no channel found'}
    raw = args.get('episodes') or args.get('order') or args.get('keys') or []
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(',') if p.strip()]
    if not isinstance(raw, (list, tuple)) or not raw:
        return {'ok': False, 'error': 'videos list required'}
    keys = []
    for ref in raw:
        ep = _find_episode_in_room(room, ref)
        if ep:
            keys.append(_episode_key(ep))
        else:
            keys.append(str(ref))
    return reorder_channel_episodes(room.get('room_id'), keys)

def _episode_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.ProfessionalChannel import delete_channel_episode, _episode_key
    room = _resolve_professional_room(args)
    if not room:
        return {'ok': False, 'error': 'no channel found'}
    ep = _find_episode_in_room(room, args.get('episode') or args.get('title') or args.get('key'))
    if not ep:
        return {'ok': False, 'error': 'video not found'}
    return delete_channel_episode(room.get('room_id'), _episode_key(ep))

def _episode_open_editor(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.RoomAddonTools import open_media_in_video_editor
    from Support.ProfessionalChannel import get_room_channel_data, sanitize_channel_slug
    room = _resolve_professional_room(args)
    if not room:
        return {'ok': False, 'error': 'no channel found'}
    ep = _find_episode_in_room(room, args.get('episode') or args.get('title') or args.get('key'))
    if not ep:
        return {'ok': False, 'error': 'episode not found'}
    path = str(ep.get('recording_path') or '').strip()
    if not path or not os.path.isfile(path):
        icon_id = str(ep.get('icon_id') or '').strip()
        if icon_id:
            from Support.Dictionaries import filter_dictionary
            rows = filter_dictionary(init.DESKTOPICONS, 'icon_id', icon_id, '', '', '', '') or []
            if rows:
                cand = os.path.join(rows[0].get('path') or '', rows[0].get('label') or '')
                if cand and os.path.isfile(cand):
                    path = cand
        if not path or not os.path.isfile(path):
            data = get_room_channel_data(room)
            slug = data.get('home_page') or data.get('channel_slug') or sanitize_channel_slug(room.get('name'))
            media_rel = str(ep.get('media') or '').strip().replace('\\', '/')
            if slug and media_rel:
                cand = os.path.join(init.PATH_WEBSITES_PUBLISHED, slug, media_rel.replace('/', os.sep))
                if os.path.isfile(cand):
                    path = cand
    if not path or not os.path.isfile(path):
        return {'ok': False, 'error': 'no media file for episode'}
    ok = open_media_in_video_editor(path)
    return {'ok': bool(ok), 'path': path}

def _rooms_add_peer(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Dictionaries import set_dictionary
    from Support.RoomsUtil import manage_peers
    want = str(args.get('room') or args.get('room_id') or '').strip()
    peer_raw = str(args.get('peer') or args.get('peer_id') or '').strip()
    if want:
        switched = _rooms_switch({'room': want})
        if not switched.get('ok'):
            return switched
    room = _current_room() or {}
    if not room:
        return {'ok': False, 'error': 'open a room first'}
    if str(room.get('owner_id') or '') != str(init.ME.get('id') or ''):
        return {'ok': False, 'error': 'only the room owner can invite peers'}
    resolved = _resolve_peer(peer_raw)
    if not resolved.get('ok'):
        return resolved
    peer_id = resolved['peer']['peer_id']
    before = str(room.get('allowed_peers') or '')
    parts = [p for p in before.split(';') if p]
    if peer_id in parts:
        return {'ok': True, 'room_id': room.get('room_id'), 'peer': resolved['peer'], 'already': True}
    parts.append(peer_id)
    after = ';'.join(parts)
    set_dictionary(init.ROOMS, search_conditions={'room_id': room.get('room_id')}, updates={'allowed_peers': after})
    room['allowed_peers'] = after
    init.selected_room_info = dict(room)
    manage_peers(getattr(init, 'mainApplication', None), before, after, after)
    return {'ok': True, 'room_id': room.get('room_id'), 'name': room.get('name'), 'peer': resolved['peer']}

def _rooms_update(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Dictionaries import set_dictionary
    want = str(args.get('room') or args.get('room_id') or args.get('name') or '').strip()
    if not want:
        return {'ok': False, 'error': 'room required'}
    switched = _rooms_switch({'room': want})
    if not switched.get('ok'):
        return switched
    room = _current_room() or {}
    if str(room.get('owner_id') or '') != str(init.ME.get('id') or ''):
        return {'ok': False, 'error': 'only the room owner can update it'}
    room_id = room.get('room_id') or room.get('id')
    if str(room_id) == '1' and args.get('name'):
        return {'ok': False, 'error': 'MAIN room name cannot be changed'}
    updates = {}
    if args.get('name'):
        updates['name'] = str(args.get('name')).strip()
    if args.get('description') is not None:
        updates['description'] = str(args.get('description') or '')
    if not updates:
        return {'ok': False, 'error': 'name or description required'}
    set_dictionary(init.ROOMS, search_conditions={'room_id': room_id}, updates=updates)
    init.selected_room_info = dict(room)
    init.selected_room_info.update(updates)
    return {'ok': True, 'room_id': room_id, 'updates': updates}

def _rooms_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Dictionaries import delete_dictionary, filter_dictionary
    from Support.Misc import room_change
    want = str(args.get('room') or args.get('room_id') or args.get('name') or '').strip()
    if not want:
        return {'ok': False, 'error': 'room required'}
    low = want.lower()
    match = None
    for room in init.ROOMS:
        rid = str(room.get('room_id') or room.get('id') or '')
        rname = str(room.get('name') or '')
        if rid == want or rname.lower() == low:
            match = room
            break
    if match is None:
        names = [str(r.get('name') or r.get('room_id') or '') for r in init.ROOMS]
        by_name = {str(r.get('name') or r.get('room_id') or ''): r for r in init.ROOMS}
        fuzzy = _fuzzy_one(want, names, cutoff=0.7)
        if fuzzy:
            match = by_name[fuzzy]
    if match is None:
        return {'ok': False, 'error': 'no room matched', 'query': want}
    room_id = match.get('room_id') or match.get('id')
    if str(room_id) == '1':
        return {'ok': False, 'error': 'cannot delete MAIN room'}
    if str(match.get('owner_id') or '') != str(init.ME.get('id') or ''):
        return {'ok': False, 'error': 'only the room owner can delete it'}
    icons = filter_dictionary(init.DESKTOPICONS, 'room_id', room_id) or []
    for icon in list(icons):
        if str(icon.get('owner_id') or '') == str(init.ME.get('id') or '') or str(icon.get('authorizations') or '000')[1:2] == '1':
            try:
                from Support.Files import delete_file
                delete_file(icon)
            except Exception:
                pass
    delete_dictionary(init.ROOMS, 'room_id', room_id, '', '', '', '', '', '', '', '')
    if (_current_room() or {}).get('room_id') == room_id:
        room_change('1')
    return {'ok': True, 'room_id': room_id, 'name': match.get('name')}

def _apps_launch(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.AppBroker import spawn_app
    from Support.Utils import query_database
    want = str(args.get('name') or args.get('app') or args.get('app_id') or '').strip()
    if not want:
        return {'ok': False, 'error': 'name or app_id required'}
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT app_id, name, main FROM Apps', (), commit=False) or []
    match = None
    names = []
    by_name = {}
    low = want.lower()
    for row in rows:
        app_id, name, main = (str(row[0] or ''), str(row[1] or ''), str(row[2] or ''))
        names.append(name or app_id)
        by_name[name or app_id] = (app_id, name, main)
        if app_id == want or name.lower() == low:
            match = (app_id, name, main)
            break
    if match is None:
        fuzzy = _fuzzy_one(want, names, cutoff=0.7)
        if fuzzy:
            match = by_name[fuzzy]
    if match is None:
        apps_root = getattr(init, 'PATH_APPS', '')
        if apps_root and os.path.isdir(os.path.join(apps_root, want)):
            match = (want, want, '')
    if match is None:
        return {'ok': False, 'error': 'no app matched', 'query': want}
    app_id, name, main = match
    if not main:
        app_dir = os.path.join(getattr(init, 'PATH_APPS', ''), str(app_id))
        if os.path.isdir(app_dir):
            for fn in os.listdir(app_dir):
                if fn.endswith('.py') or fn.endswith('.pyd'):
                    main = fn
                    break
    if not main:
        return {'ok': False, 'error': 'app main file not found', 'app_id': app_id}
    if main.endswith('.py') or main.endswith('.pyd'):
        main = main.rsplit('.', 1)[0]
    spawn_app(str(app_id), main)
    return {'ok': True, 'app_id': app_id, 'name': name, 'main': main}

def _goods_table() -> str:
    return 'CommunityGoodsServices'

def _goods_save(args: Dict[str, Any], create: bool) -> Dict[str, Any]:
    from Support.Utils import generate_random_hash, query_database
    room, err = _require_community_owner()
    if err:
        return err
    room_id = str(room.get('room_id') or room.get('id'))
    name = (args.get('name') or args.get('title') or '').strip()
    if not name:
        return {'ok': False, 'error': 'name required'}
    description = str(args.get('description') or args.get('text') or '').replace("'", '|')
    quantity = args.get('quantity')
    quantity_type = str(args.get('quantity_type') or '')
    neurons = args.get('neurons')
    neurons_unit = str(args.get('neurons_unit') or '')
    if quantity is None:
        quantity = 0
    if neurons is None:
        neurons = 0
    pool = getattr(init, 'pool_main_db', None)
    table = _goods_table()
    existing = query_database(pool, 'SELECT good_service_id FROM %s WHERE room_id = ? AND name = ?' % table, (room_id, name), commit=False) or []
    if create and existing:
        return {'ok': False, 'error': 'goods/services already exists', 'name': name}
    if not create and (not existing):
        return {'ok': False, 'error': 'goods/services not found', 'name': name}
    if existing:
        query_database(pool, 'UPDATE %s SET description=?, quantity=?, quantity_type=?, neurons=?, neurons_unit=? WHERE room_id=? AND good_service_id=?' % table, (description, quantity, quantity_type, neurons, neurons_unit, room_id, existing[0][0]))
        return {'ok': True, 'name': name, 'updated': True}
    gid = generate_random_hash()
    query_database(pool, 'INSERT INTO %s (room_id, good_service_id, name, description, quantity, quantity_type, neurons, neurons_unit, accept_barter, searchable, shareable, photos, documents) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)' % table, (room_id, gid, name, description, quantity, quantity_type, neurons, neurons_unit, 0, 0, 0, '', ''))
    return {'ok': True, 'name': name, 'good_service_id': gid, 'created': True}

def _goods_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    room, err = _require_community_owner()
    if err:
        return err
    room_id = str(room.get('room_id') or room.get('id'))
    name = (args.get('name') or args.get('title') or '').strip()
    if not name:
        return {'ok': False, 'error': 'name required'}
    query_database(getattr(init, 'pool_main_db', None), 'DELETE FROM %s WHERE room_id = ? AND name = ?' % _goods_table(), (room_id, name))
    return {'ok': True, 'name': name, 'deleted': True}

def _assets_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    room = _current_room() or {}
    room_id = room.get('room_id') or room.get('id')
    if not room_id:
        return {'ok': False, 'error': 'open a community room first'}
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT asset_id, name, description, status FROM CommunityAssets WHERE room_id = ?', (str(room_id),), commit=False) or []
    assets = [{'asset_id': r[0], 'name': r[1], 'description': (r[2] or '')[:120], 'status': r[3]} for r in rows]
    return {'ok': True, 'assets': assets, 'count': len(assets)}

def _assets_save(args: Dict[str, Any], create: bool) -> Dict[str, Any]:
    from Support.Utils import generate_random_hash, query_database
    room, err = _require_community_owner()
    if err:
        return err
    room_id = str(room.get('room_id') or room.get('id'))
    name = (args.get('name') or args.get('title') or '').strip()
    asset_id = (args.get('asset_id') or args.get('id') or '').strip()
    description = str(args.get('description') or '').replace("'", '|')
    status = str(args.get('status') or '')
    pool = getattr(init, 'pool_main_db', None)
    existing = None
    if asset_id:
        existing = query_database(pool, 'SELECT asset_id, name FROM CommunityAssets WHERE asset_id = ?', (asset_id,), commit=False) or []
    elif name:
        existing = query_database(pool, 'SELECT asset_id, name FROM CommunityAssets WHERE room_id = ? AND name = ?', (room_id, name), commit=False) or []
    if create and existing:
        return {'ok': False, 'error': 'asset already exists'}
    if not create and (not existing):
        return {'ok': False, 'error': 'asset not found'}
    if existing:
        aid = existing[0][0]
        query_database(pool, "UPDATE CommunityAssets SET name=COALESCE(NULLIF(?, ''), name), description=?, status=COALESCE(NULLIF(?, ''), status) WHERE asset_id=?", (name, description, status, aid))
        return {'ok': True, 'asset_id': aid, 'updated': True}
    if not name:
        return {'ok': False, 'error': 'name required'}
    aid = generate_random_hash()
    query_database(pool, 'INSERT INTO CommunityAssets (room_id, asset_id, name, description, photos, status, maintenance, searchable, shareable) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (room_id, aid, name, description, '', status, '', 'False', 'False'))
    return {'ok': True, 'asset_id': aid, 'name': name, 'created': True}

def _assets_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    room, err = _require_community_owner()
    if err:
        return err
    room_id = str(room.get('room_id') or room.get('id'))
    asset_id = (args.get('asset_id') or args.get('id') or '').strip()
    name = (args.get('name') or '').strip()
    pool = getattr(init, 'pool_main_db', None)
    if asset_id:
        query_database(pool, 'DELETE FROM CommunityAssets WHERE asset_id = ?', (asset_id,))
        return {'ok': True, 'asset_id': asset_id, 'deleted': True}
    if name:
        query_database(pool, 'DELETE FROM CommunityAssets WHERE room_id = ? AND name = ?', (room_id, name))
        return {'ok': True, 'name': name, 'deleted': True}
    return {'ok': False, 'error': 'name or asset_id required'}

def _contacts_add(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import generate_random_hash, get_timestamp_in_seconds, query_database
    first = (args.get('first_name') or args.get('firstname') or '').strip()
    last = (args.get('last_name') or args.get('lastname') or '').strip()
    if not first and (not last) and args.get('name'):
        parts = str(args.get('name')).strip().split(None, 1)
        first = parts[0] if parts else ''
        last = parts[1] if len(parts) > 1 else ''
    phone = (args.get('phone') or args.get('phone1') or '').strip()
    email = (args.get('email') or '').strip()
    if not first and (not last):
        return {'ok': False, 'error': 'first_name or last_name required'}
    if not phone:
        return {'ok': False, 'error': 'phone required'}
    contact_id = generate_random_hash()
    query_database(getattr(init, 'pool_main_db', None), 'INSERT INTO Contacts (id, photo_path, last_name, first_name, phone, phone1, phone2, email, email1, email2, job_title, company, birthday, url, notes, date_added, peer_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (contact_id, '', last, first, phone, '', '', email, '', '', '', '', '', '', '', get_timestamp_in_seconds(), ''))
    return {'ok': True, 'id': contact_id, 'first_name': first, 'last_name': last, 'phone': phone}

def _contacts_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    phone = (args.get('phone') or '').strip()
    name = (args.get('name') or '').strip()
    first = (args.get('first_name') or '').strip()
    last = (args.get('last_name') or '').strip()
    pool = getattr(init, 'pool_main_db', None)
    if phone:
        query_database(pool, 'DELETE FROM Contacts WHERE phone = ? OR phone1 = ? OR phone2 = ?', (phone, phone, phone))
        return {'ok': True, 'deleted_by': 'phone', 'phone': phone}
    if name or first or last:
        if name and (not first) and (not last):
            parts = name.split(None, 1)
            first = parts[0]
            last = parts[1] if len(parts) > 1 else ''
        rows = query_database(pool, 'SELECT id, first_name, last_name FROM Contacts', (), commit=False) or []
        deleted = []
        for row in rows:
            fn = str(row[1] or '')
            ln = str(row[2] or '')
            full = (fn + ' ' + ln).strip().lower()
            if first and fn.lower() == first.lower() or (last and ln.lower() == last.lower()) or (name and (full == name.lower() or fn.lower() == name.lower())):
                query_database(pool, 'DELETE FROM Contacts WHERE id = ?', (row[0],))
                deleted.append(row[0])
        if not deleted:
            return {'ok': False, 'error': 'no contact matched'}
        return {'ok': True, 'deleted': deleted}
    return {'ok': False, 'error': 'name or phone required'}

def _groups_notify_peers(group_id: str, name: str, peer_list: str, owner_id: str, img_name: str='No_image.png'):
    from Support.Dictionaries import filter_dictionary
    from Support.SendData import send_packet
    from Support.Utils import ip_to_send_packet_to
    img_path = os.path.join(getattr(init, 'PATH_GROUP_IMAGES', ''), img_name)
    image_data = b''
    if os.path.isfile(img_path):
        with open(img_path, 'rb') as f:
            image_data = f.read()
    my_id = _my_id()
    for peer in [p for p in peer_list.split(';') if p]:
        if peer == my_id:
            continue
        peer_info = filter_dictionary(init.PEERS, 'peer_id', peer)
        if not peer_info:
            continue
        dest = peer_info[0]
        send_packet(init.ME['ip'], init.ME['ip_local'], '', ip_to_send_packet_to(dest.get('ip'), dest.get('ip_local')), dest.get('ip_local') or '', '', init.CMD_PER_GRP, init.PRT_NOR, '', 0, 0, '', [group_id, name, peer_list, owner_id, img_name], [], [image_data], '', '', [], [], [], retry_policy='critical', src_peer_id=my_id, dst_peer_id=peer)

def _groups_update(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    grouped = _resolve_group(str(args.get('group') or args.get('group_id') or ''))
    if not grouped.get('ok'):
        return grouped
    new_name = (args.get('new_name') or args.get('title') or args.get('name') or '').strip()
    if not new_name:
        return {'ok': False, 'error': 'new name required'}
    if new_name == grouped['group']['name']:
        return {'ok': False, 'error': 'new name is the same as current name'}
    group = grouped['group']
    if str(group.get('owner_id') or '') != str(_my_id()):
        return {'ok': False, 'error': 'only the group owner can rename it'}
    query_database(getattr(init, 'pool_main_db', None), 'UPDATE ChatGroups SET name = ? WHERE id = ?', (new_name, group['id']))
    _groups_notify_peers(group['id'], new_name, group.get('peers') or '', group.get('owner_id') or '')
    return {'ok': True, 'group_id': group['id'], 'name': new_name}

def _groups_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    grouped = _resolve_group(str(args.get('group') or args.get('group_id') or args.get('name') or ''))
    if not grouped.get('ok'):
        return grouped
    group = grouped['group']
    group_id = group['id']
    my_id = _my_id()
    peers = [p for p in str(group.get('peers') or '').split(';') if p]
    if str(group.get('owner_id') or '') == str(my_id):
        query_database(getattr(init, 'pool_main_db', None), 'DELETE FROM Chats WHERE src = ? OR dst = ?', (group_id, group_id))
        query_database(getattr(init, 'pool_main_db', None), 'DELETE FROM ChatGroups WHERE id = ?', (group_id,))
        _groups_notify_peers(group_id, group.get('name') or '', '', my_id)
        return {'ok': True, 'group_id': group_id, 'deleted': True}
    peers = [p for p in peers if p != my_id]
    peer_list = ';'.join(peers)
    query_database(getattr(init, 'pool_main_db', None), 'UPDATE ChatGroups SET peers = ? WHERE id = ?', (peer_list, group_id))
    return {'ok': True, 'group_id': group_id, 'left': True}

def _groups_peer_change(args: Dict[str, Any], add: bool) -> Dict[str, Any]:
    from Support.Utils import query_database
    grouped = _resolve_group(str(args.get('group') or args.get('group_id') or ''))
    if not grouped.get('ok'):
        return grouped
    group = grouped['group']
    if str(group.get('owner_id') or '') != str(_my_id()):
        return {'ok': False, 'error': 'only the group owner can change members'}
    resolved = _resolve_peer(str(args.get('peer') or args.get('name') or args.get('peer_id') or ''))
    if not resolved.get('ok'):
        return resolved
    peer_id = resolved['peer']['peer_id']
    peers = [p for p in str(group.get('peers') or '').split(';') if p]
    if add:
        if peer_id not in peers:
            peers.append(peer_id)
    else:
        peers = [p for p in peers if p != peer_id]
    peer_list = ';'.join(peers)
    query_database(getattr(init, 'pool_main_db', None), 'UPDATE ChatGroups SET peers = ? WHERE id = ?', (peer_list, group['id']))
    _groups_notify_peers(group['id'], group.get('name') or '', peer_list, group.get('owner_id') or '')
    out = {'ok': True, 'group_id': group['id'], 'peers': peers}
    out['added' if add else 'removed'] = peer_id
    return out

def _chat_attach(args: Dict[str, Any]) -> Dict[str, Any]:
    import shutil
    from Support.NeuronProjectorChat import get_or_create_chat_id
    from Support.Utils import generate_random_hash
    rel = (args.get('path') or args.get('file') or args.get('attachment') or '').strip()
    if not rel:
        return {'ok': False, 'error': 'path required'}
    user_root = os.path.abspath(getattr(init, 'PATH_USER', '') or '')
    full = os.path.abspath(rel if os.path.isabs(rel) else os.path.join(user_root, rel))
    if not _neury_path_inside_install(full):
        return _neury_sandbox_deny(full)
    if not os.path.isfile(full):
        return {'ok': False, 'error': 'file not found', 'path': full}
    max_size = int(getattr(init, 'CHAT_INLINE_ATTACHMENT_MAX', 5 * 1024 * 1024) or 5 * 1024 * 1024)
    if os.path.getsize(full) > max_size:
        return {'ok': False, 'error': 'attachment too large', 'max': max_size}
    peer_key = str(args.get('peer') or args.get('name') or args.get('peer_id') or '')
    group_key = str(args.get('group') or args.get('group_id') or '')
    my_id = _my_id()
    if group_key or args.get('is_group'):
        grouped = _resolve_group(group_key or peer_key)
        if not grouped.get('ok'):
            return grouped
        dest_id = grouped['group']['id']
        is_group = True
    else:
        resolved = _resolve_peer(peer_key)
        if not resolved.get('ok'):
            return resolved
        dest_id = resolved['peer']['peer_id']
        is_group = False
    chat_id = get_or_create_chat_id(dest_id, is_group, my_id)
    attach_dir = os.path.join(getattr(init, 'MAIN_PATH', ''), 'User', 'Chat', 'Attachments', str(chat_id))
    os.makedirs(attach_dir, exist_ok=True)
    filename = os.path.basename(full)
    dest_path = os.path.join(attach_dir, filename)
    if os.path.abspath(dest_path) != full:
        shutil.copy2(full, dest_path)
    send_args = dict(args)
    send_args['message'] = (args.get('message') or args.get('text') or filename).strip() or filename
    result = _chat_send_group(send_args) if is_group else _chat_send(send_args)
    if not result.get('ok'):
        return result
    from Support.Utils import query_database
    mid = result.get('message_id')
    if mid:
        query_database(getattr(init, 'pool_main_db', None), 'UPDATE Chats SET attachment = ? WHERE id = ?', (filename, mid))
    result['attachment'] = filename
    return result

def _chat_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.NeuronProjectorChat import get_or_create_chat_id
    from Support.Utils import query_database
    peer_key = str(args.get('peer') or args.get('name') or args.get('peer_id') or '')
    group_key = str(args.get('group') or args.get('group_id') or '')
    my_id = _my_id()
    if group_key or args.get('is_group'):
        grouped = _resolve_group(group_key or peer_key)
        if not grouped.get('ok'):
            return grouped
        dest_id = grouped['group']['id']
        chat_id = get_or_create_chat_id(dest_id, True, my_id)
    else:
        resolved = _resolve_peer(peer_key)
        if not resolved.get('ok'):
            return resolved
        dest_id = resolved['peer']['peer_id']
        chat_id = get_or_create_chat_id(dest_id, False, my_id)
    query_database(getattr(init, 'pool_main_db', None), 'DELETE FROM Chats WHERE chat_id = ?', (chat_id,))
    return {'ok': True, 'chat_id': chat_id, 'deleted': True}

def _votes_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.NeuronVoting import get_active_votes
    votes = get_active_votes() or []
    slim = []
    for v in votes[:30]:
        if isinstance(v, dict):
            slim.append({'vote_id': v.get('vote_id') or v.get('referendum_id') or v.get('id'), 'topic': v.get('topic') or v.get('title') or '', 'options': v.get('options') or []})
        else:
            slim.append({'vote': str(v)[:120]})
    return {'ok': True, 'votes': slim, 'count': len(slim)}

def _votes_create(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.NeuronVoting import create_vote
    topic = (args.get('topic') or args.get('title') or '').strip()
    if not topic:
        return {'ok': False, 'error': 'topic required'}
    options = args.get('options')
    if isinstance(options, str):
        options = [p.strip() for p in options.replace(',', ';').split(';') if p.strip()]
    if not isinstance(options, list) or len(options) < 2:
        return {'ok': False, 'error': 'at least two options required'}
    expiry = int(args.get('expiry_seconds') or args.get('expiry') or 86400)
    description = str(args.get('description') or '')
    vote_id = create_vote(topic, options, expiry, description=description)
    return {'ok': True, 'vote_id': vote_id, 'topic': topic, 'options': options}

def _votes_cast(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.NeuronVoting import cast_vote
    vote_id = (args.get('vote_id') or args.get('id') or args.get('referendum_id') or '').strip()
    choice = (args.get('choice') or args.get('option') or '').strip()
    if not vote_id or not choice:
        return {'ok': False, 'error': 'vote_id and choice required'}
    result = cast_vote(vote_id, choice)
    if isinstance(result, dict):
        result.setdefault('ok', True)
        return result
    return {'ok': True, 'vote_id': vote_id, 'choice': choice, 'result': result}

def _votes_results(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.NeuronVoting import get_vote_results, get_vote_results_list
    vote_id = (args.get('vote_id') or args.get('id') or args.get('referendum_id') or '').strip()
    if vote_id:
        return {'ok': True, 'results': get_vote_results(vote_id)}
    return {'ok': True, 'results': get_vote_results_list() or []}

def _barters_create(args: Dict[str, Any]) -> Dict[str, Any]:
    import json
    from Support.Dictionaries import filter_dictionary
    from Support.SendData import send_packet
    from Support.Utils import generate_random_hash, get_timestamp_in_seconds, ip_to_send_packet_to, query_database
    room, err = _require_community_owner()
    if err:
        return err
    c1 = _resolve_community_room(str(args.get('community1') or args.get('my_community') or room.get('name') or ''))
    c2 = _resolve_community_room(str(args.get('community2') or args.get('peer_community') or ''))
    if not c1.get('ok'):
        return c1
    if not c2.get('ok'):
        return c2
    if str(c1['room'].get('owner_id') or '') != str(init.ME.get('id') or ''):
        return {'ok': False, 'error': 'community1 must be a COMMUNITY room you own'}
    items1 = args.get('items1') or args.get('offer') or []
    items2 = args.get('items2') or args.get('request') or []
    if isinstance(items1, str):
        items1 = [{'item': p.strip()} for p in items1.split(';') if p.strip()]
    if isinstance(items2, str):
        items2 = [{'item': p.strip()} for p in items2.split(';') if p.strip()]
    if not items1 or not items2:
        return {'ok': False, 'error': 'items1 and items2 required'}
    community1_id = c1['room'].get('room_id') or c1['room'].get('id')
    community2_id = c2['room'].get('room_id') or c2['room'].get('id')
    now = get_timestamp_in_seconds()
    barter_id = generate_random_hash()
    query_database(getattr(init, 'pool_main_db', None), 'INSERT INTO CommunityBarters\n           (barter_id, community1_id, community2_id, community1_bid, community2_bid,\n            start_date, last_bid_date, end_date, my_turn)\n           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (barter_id, community1_id, community2_id, json.dumps(items1), json.dumps(items2), now, now, 0, 0))
    linked = query_database(getattr(init, 'pool_main_db', None), 'SELECT * FROM CommunityLinks WHERE room_id = ? AND linked_room_id = ?', (community1_id, community2_id), commit=False) or []
    sent = 0
    if linked:
        peer_id = linked[0][12] if len(linked[0]) > 12 else ''
        peer_info = filter_dictionary(init.PEERS, 'peer_id', peer_id) if peer_id else None
        if peer_info:
            dest = peer_info[0]
            send_packet(init.ME['ip'], init.ME['ip_local'], '', ip_to_send_packet_to(dest.get('ip'), dest.get('ip_local')), dest.get('ip_local') or '', '', init.CMD_COM_BID, init.PRT_NOR, '', 0, 0, '', [barter_id, community1_id, community2_id, json.dumps(items1), json.dumps(items2), now, now, 0, 1, c1['room'].get('name') or ''], [], [], '', '', [], [], [])
            sent = 1
    return {'ok': True, 'barter_id': barter_id, 'sent': sent}

def _barters_bid(args: Dict[str, Any]) -> Dict[str, Any]:
    import json
    from Support.Dictionaries import filter_dictionary
    from Support.SendData import send_packet
    from Support.Utils import get_timestamp_in_seconds, ip_to_send_packet_to, query_database
    room, err = _require_community_owner()
    if err:
        return err
    barter_id = (args.get('barter_id') or args.get('id') or '').strip()
    items = args.get('items') or args.get('bid') or []
    if not barter_id:
        return {'ok': False, 'error': 'barter_id required'}
    if isinstance(items, str):
        items = [{'item': p.strip()} for p in items.split(';') if p.strip()]
    if not items:
        return {'ok': False, 'error': 'items required'}
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT barter_id, community1_id, community2_id, community1_bid, community2_bid, my_turn FROM CommunityBarters WHERE barter_id = ?', (barter_id,), commit=False) or []
    if not rows:
        return {'ok': False, 'error': 'barter not found'}
    row = rows[0]
    my_room = str(room.get('room_id') or room.get('id'))
    now = get_timestamp_in_seconds()
    if str(row[1]) == my_room:
        query_database(getattr(init, 'pool_main_db', None), 'UPDATE CommunityBarters SET community1_bid=?, last_bid_date=?, my_turn=0 WHERE barter_id=?', (json.dumps(items), now, barter_id))
        other = row[2]
    elif str(row[2]) == my_room:
        query_database(getattr(init, 'pool_main_db', None), 'UPDATE CommunityBarters SET community2_bid=?, last_bid_date=?, my_turn=0 WHERE barter_id=?', (json.dumps(items), now, barter_id))
        other = row[1]
    else:
        return {'ok': False, 'error': 'current community is not part of this barter'}
    linked = query_database(getattr(init, 'pool_main_db', None), 'SELECT * FROM CommunityLinks WHERE room_id = ? AND linked_room_id = ?', (my_room, other), commit=False) or []
    if linked:
        peer_id = linked[0][12] if len(linked[0]) > 12 else ''
        peer_info = filter_dictionary(init.PEERS, 'peer_id', peer_id) if peer_id else None
        if peer_info:
            dest = peer_info[0]
            send_packet(init.ME['ip'], init.ME['ip_local'], '', ip_to_send_packet_to(dest.get('ip'), dest.get('ip_local')), dest.get('ip_local') or '', '', init.CMD_COM_BID, init.PRT_NOR, '', 0, 0, '', [barter_id, row[1], row[2], json.dumps(items), '', now, now, 0, 1, room.get('name') or ''], [], [], '', '', [], [], [])
    return {'ok': True, 'barter_id': barter_id, 'items': items}

def _article_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    query = (args.get('query') or args.get('q') or args.get('text') or '').strip().lower()
    limit = max(1, min(int(args.get('limit') or 12), 40))
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT article_id, title, subtitle, short_description, date_created, tags FROM Articles ORDER BY date_created DESC', (), commit=False) or []
    articles = []
    for row in rows:
        hay = ' '.join([str(row[1] or ''), str(row[2] or ''), str(row[3] or ''), str(row[5] or '')]).lower()
        if query and query not in hay:
            continue
        articles.append({'article_id': row[0], 'title': row[1], 'subtitle': row[2] or '', 'preview': (row[3] or '')[:160], 'date': row[4], 'tags': row[5] or ''})
        if len(articles) >= limit:
            break
    return {'ok': True, 'articles': articles, 'count': len(articles)}

def _article_read(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    article_id = (args.get('article_id') or args.get('id') or '').strip()
    title = (args.get('title') or '').strip()
    pool = getattr(init, 'pool_main_db', None)
    row = None
    if article_id:
        rows = query_database(pool, 'SELECT article_id, title, subtitle, article, tags FROM Articles WHERE article_id = ?', (article_id,), commit=False) or []
        row = rows[0] if rows else None
    elif title:
        rows = query_database(pool, 'SELECT article_id, title, subtitle, article, tags FROM Articles WHERE LOWER(title) LIKE ?', ('%' + title.lower() + '%',), commit=False) or []
        row = rows[0] if rows else None
    if not row:
        return {'ok': False, 'error': 'article not found'}
    return {'ok': True, 'article_id': row[0], 'title': row[1], 'subtitle': row[2] or '', 'article': (row[3] or '')[:4000], 'tags': row[4] or ''}

def _load_website_plan_executor():
    import importlib.util
    import sys
    from pathlib import Path
    app_dir = Path(getattr(init, 'PATH_APPS', '')) / 'MBNKOT642TIPWQDHCNX'
    mod_path = app_dir / 'website_plan_executor.py'
    if not mod_path.is_file():
        return None
    app_dir_str = str(app_dir.resolve())
    if app_dir_str not in sys.path:
        sys.path.insert(0, app_dir_str)
    cached = getattr(init, '_website_plan_executor_mod', None)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location('_website_plan_executor', str(mod_path))
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    init._website_plan_executor_mod = mod
    return mod

def _website_builder_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    mod = _load_website_plan_executor()
    if mod is None:
        return {'ok': False, 'error': 'website builder module not found'}
    if name == 'website.build':
        return mod.neury_tool_build(args)
    if name == 'website.database.setup':
        return mod.neury_tool_database_setup(args)
    if name == 'website.page.generate':
        return mod.neury_tool_page_generate(args)
    if name == 'website.bind':
        return mod.neury_tool_bind(args)
    if name == 'website.save':
        return mod.neury_tool_save(args)
    return {'ok': False, 'error': 'unknown website tool'}

def _websites_create(args: Dict[str, Any]) -> Dict[str, Any]:
    from pathlib import Path
    from Support.WebsitesUtils import WebsiteManager
    name = (args.get('name') or args.get('website') or '').strip()
    if not name:
        return {'ok': False, 'error': 'name required'}
    if os.sep in name or '/' in name or '\\' in name or ('..' in name):
        return {'ok': False, 'error': 'invalid website name'}
    description = str(args.get('description') or '')
    tags = args.get('tags') or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.replace(',', ';').split(';') if t.strip()]
    works_root = Path(init.PATH_WEBSITES_WORKS)
    site_dir = works_root / name
    resolved = os.path.realpath(str(site_dir))
    works_real = os.path.realpath(str(works_root))
    if not _neury_path_inside_install(resolved):
        return _neury_sandbox_deny(resolved)
    if os.path.normcase(resolved) != os.path.normcase(works_real) and (not os.path.normcase(resolved).startswith(os.path.normcase(works_real) + os.sep)):
        return _neury_sandbox_deny(resolved)
    if site_dir.exists():
        return {'ok': True, 'name': name, 'path': str(site_dir), 'reused': True, 'message': 'Using existing website draft "%s".' % name}
    ok, message = WebsiteManager.create_website(works_root, name, description, tags)
    return {'ok': bool(ok), 'name': name, 'error': '' if ok else str(message), 'path': str(site_dir)}

def _websites_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    from pathlib import Path
    from Support.WebsitesUtils import WebsiteManager
    name = (args.get('name') or args.get('website') or '').strip()
    if not name:
        return {'ok': False, 'error': 'name required'}
    if os.sep in name or '/' in name or '\\' in name or ('..' in name):
        return {'ok': False, 'error': 'invalid website name'}
    kind = (args.get('kind') or 'draft').strip().lower()
    base = init.PATH_WEBSITES_WORKS if kind != 'published' else init.PATH_WEBSITES_PUBLISHED
    path = Path(base) / name
    if not path.exists():
        alt = Path(init.PATH_WEBSITES_PUBLISHED if kind != 'published' else init.PATH_WEBSITES_WORKS) / name
        if alt.exists():
            path = alt
        else:
            return {'ok': False, 'error': 'website not found', 'name': name}
    resolved = os.path.realpath(str(path))
    if not _neury_path_inside_install(resolved):
        return _neury_sandbox_deny(resolved)
    ok, message = WebsiteManager.delete_website(path)
    return {'ok': bool(ok), 'name': name, 'error': '' if ok else message}

def _neurons_purchase(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.PurchaseNeurons import create_packages_with_currency_conversion, start_checkout_by_index, start_checkout_for_package
    idx = args.get('package_index')
    neurons = args.get('neurons') or args.get('amount')
    if idx is None and neurons is not None:
        packages = create_packages_with_currency_conversion()
        want = int(neurons)
        for i, pkg in enumerate(packages):
            if int(getattr(pkg, 'neurons', 0) or 0) == want:
                idx = i
                break
        if idx is None:
            return {'ok': False, 'error': 'no package matched that neuron amount', 'wanted': want}
    if idx is None:
        idx = 0
    payload, error = start_checkout_by_index(int(idx))
    if error:
        return {'ok': False, 'error': error}
    if payload and payload.get('open_checkout_in_browser') and payload.get('checkout_url'):
        try:
            import webbrowser
            webbrowser.open(payload['checkout_url'])
        except Exception:
            pass
    return {'ok': True, 'checkout': payload}

def _call_hangup(args: Dict[str, Any]) -> Dict[str, Any]:
    call = _find_phone_call()
    if call is None:
        return {'ok': False, 'error': 'no active call'}
    call.end_call(False)
    return {'ok': True, 'ended': True}

def _screen_share(args: Dict[str, Any], start: bool) -> Dict[str, Any]:
    call = _find_phone_call()
    if call is None:
        return {'ok': False, 'error': 'no active call'}
    if start:
        call.share_screen()
        return {'ok': True, 'sharing': True}
    call.stop_screen_sharing()
    return {'ok': True, 'sharing': False}

def _devices_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    limit = max(1, min(int(args.get('limit') or 40), 100))
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT device_id, device_name, ip, date, pending, online FROM Devices', (), commit=False) or []
    devices = []
    for row in rows[:limit]:
        devices.append({'device_id': row[0], 'name': row[1], 'ip': row[2], 'date': row[3], 'pending': row[4], 'online': row[5]})
    return {'ok': True, 'devices': devices, 'count': len(devices)}

def _devices_set(args: Dict[str, Any], allow: bool) -> Dict[str, Any]:
    from Support.Utils import query_database
    want = str(args.get('device') or args.get('device_id') or args.get('name') or '').strip()
    if not want:
        return {'ok': False, 'error': 'device required'}
    rows = query_database(getattr(init, 'pool_main_db', None), 'SELECT device_id, device_name FROM Devices', (), commit=False) or []
    match = None
    names = []
    by_name = {}
    low = want.lower()
    for row in rows:
        did, dname = (str(row[0] or ''), str(row[1] or ''))
        names.append(dname or did)
        by_name[dname or did] = did
        if did == want or dname.lower() == low:
            match = did
            break
    if match is None:
        fuzzy = _fuzzy_one(want, names, cutoff=0.7)
        if fuzzy:
            match = by_name[fuzzy]
    if match is None:
        return {'ok': False, 'error': 'no device matched', 'query': want}
    pending = 0 if allow else -1
    query_database(getattr(init, 'pool_main_db', None), 'UPDATE Devices SET pending = ? WHERE device_id = ?', (pending, match))
    return {'ok': True, 'device_id': match, 'allowed': allow}

def _vault_open(args: Dict[str, Any], retrieve: bool) -> Dict[str, Any]:
    from Support.Menu import open_dialog
    open_dialog('Vault')
    return {'ok': True, 'opened': 'Vault', 'mode': 'retrieve' if retrieve else 'store', 'hint': args.get('path') or ''}

def _macros_assign(args: Dict[str, Any]) -> Dict[str, Any]:
    name = (args.get('name') or args.get('macro') or '').strip()
    key = args.get('key') or args.get('key_index') or args.get('f_key')
    if not name or key is None:
        return {'ok': False, 'error': 'name and key required'}
    key_index = int(key) - 1 if int(key) >= 1 else int(key)
    bar = getattr(init, 'macros_bar', None)
    if bar is None:
        return {'ok': False, 'error': 'macros bar unavailable'}
    bar.assign_function(key_index, name, save_to_db=True)
    return {'ok': True, 'name': name, 'key': int(key)}

def _macros_clear(args: Dict[str, Any]) -> Dict[str, Any]:
    key = args.get('key') or args.get('key_index') or args.get('f_key')
    if key is None:
        return {'ok': False, 'error': 'key required'}
    key_index = int(key) - 1 if int(key) >= 1 else int(key)
    bar = getattr(init, 'macros_bar', None)
    if bar is None:
        return {'ok': False, 'error': 'macros bar unavailable'}
    bar.unassign_function(key_index)
    return {'ok': True, 'key': int(key), 'cleared': True}

def _pedia_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Utils import query_database
    limit = max(1, min(int(args.get('limit') or 20), 50))
    pool = getattr(init, 'pool_pedia_db', None)
    rows = query_database(pool, 'SELECT id, title, status, date_published FROM Entry WHERE peer_id = ? ORDER BY date_published DESC', (init.ME.get('id'),), commit=False) or []
    entries = []
    for row in rows[:limit]:
        entries.append({'entry_id': row[0], 'title': row[1], 'status': row[2], 'date': row[3]})
    return {'ok': True, 'entries': entries, 'count': len(entries)}

def _pedia_search(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.NeuronPedia import ask_pedia_sky
    from Support.Utils import query_database
    import time as _time
    query = (args.get('query') or args.get('q') or '').strip()
    if not query:
        return {'ok': False, 'error': 'query required'}
    limit = max(1, min(int(args.get('limit') or 12), 30))
    ask_pedia_sky(query)
    _time.sleep(1.2)
    pool = getattr(init, 'pool_pedia_db', None)
    like = '%' + query.replace('%', '') + '%'
    rows = query_database(pool, "SELECT id, title, lead, status, author, peer_id, IFNULL(tags,''), date_updated FROM Entry WHERE title LIKE ? OR lead LIKE ? OR IFNULL(tags,'') LIKE ? ORDER BY date_updated DESC LIMIT ?", (like, like, like, limit), commit=False) or []
    cards = []
    for row in rows:
        cards.append({'entry_id': row[0], 'title': row[1], 'lead': row[2], 'status': row[3], 'author': row[4], 'peer_id': row[5], 'tags': row[6], 'date_updated': row[7]})
    return {'ok': True, 'query': query, 'cards': cards, 'count': len(cards)}

def _pedia_read(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.NeuronPedia import ask_pedia_sky
    from Support.Utils import query_database
    import time as _time
    entry_id = (args.get('entry_id') or args.get('id') or '').strip()
    if not entry_id:
        return {'ok': False, 'error': 'entry_id required'}
    ask_pedia_sky('', entry_id)
    _time.sleep(1.5)
    pool = getattr(init, 'pool_pedia_db', None)
    rows = query_database(pool, 'SELECT id, title, lead, body, status, author, peer_id, tags, version FROM Entry WHERE id = ? LIMIT 1', (entry_id,), commit=False) or []
    if not rows:
        return {'ok': False, 'error': 'entry not available yet', 'entry_id': entry_id}
    row = rows[0]
    body = row[3] or ''
    plain = re.sub('<[^>]+>', ' ', body)
    plain = re.sub('\\s+', ' ', plain).strip()
    return {'ok': True, 'entry_id': row[0], 'title': row[1], 'lead': row[2], 'body_text': plain[:4000], 'status': row[4], 'author': row[5], 'peer_id': row[6], 'tags': row[7], 'version': row[8]}

def _pedia_publish(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.NeuronPedia import publish_pedia_catalog
    entry_id = (args.get('entry_id') or args.get('id') or '').strip() or None
    publish_pedia_catalog(entry_id)
    return {'ok': True, 'entry_id': entry_id or 'all'}

def _tools_open(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Tools import _TOOL_LABEL_PREFIXES, start_tool_by_index
    want = (args.get('name') or args.get('tool') or '').strip()
    if not want:
        return {'ok': False, 'error': 'name required'}
    low = want.lower()
    idx = None
    label = None
    for tool_idx, prefix in _TOOL_LABEL_PREFIXES:
        if low == prefix.lower() or low.startswith(prefix.lower()) or prefix.lower() in low:
            idx = tool_idx
            label = prefix
            break
    if idx is None:
        names = [p for _, p in _TOOL_LABEL_PREFIXES]
        fuzzy = _fuzzy_one(want, names, cutoff=0.7)
        if fuzzy:
            for tool_idx, prefix in _TOOL_LABEL_PREFIXES:
                if prefix == fuzzy:
                    idx = tool_idx
                    label = prefix
                    break
    if idx is None:
        return {'ok': False, 'error': 'no tool matched', 'query': want, 'available': [p for _, p in _TOOL_LABEL_PREFIXES]}
    parent = getattr(init, 'mainApplication', None)
    start_tool_by_index(parent, idx)
    return {'ok': True, 'tool': label, 'index': idx}

def _tools_play(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.Tools import TOOL_RADIO, TOOL_TV
    opened = _tools_open(args)
    if not opened.get('ok'):
        return opened
    selection = (args.get('selection') or args.get('station') or args.get('channel') or '').strip()
    idx = opened.get('index')
    label = opened.get('tool') or ''
    if idx == TOOL_RADIO:
        radio = getattr(init, 'radio', None)
        if radio is None:
            return {'ok': False, 'error': 'Radio did not open', 'tool': label}
        stations = getattr(radio, 'stations', None) or []
        if selection:
            names = [str(s.get('name') or '') for s in stations]
            pick = _fuzzy_one(selection, names, cutoff=0.6)
            if not pick:
                return {'ok': False, 'error': 'station not found', 'selection': selection, 'ask': 'Which radio station should I play?', 'available': names[:40]}
            for i, s in enumerate(stations):
                if str(s.get('name') or '') == pick:
                    radio.current_index = i
                    break
        elif getattr(radio, 'current_index', -1) < 0 and stations:
            radio.current_index = 0
        if hasattr(radio, 'start_streaming'):
            radio.start_streaming()
        elif hasattr(radio, 'update_station'):
            radio.update_station()
        return {'ok': True, 'tool': 'Radio', 'playing': True, 'selection': selection or 'current'}
    if idx == TOOL_TV:
        tv = getattr(init, 'tv_player_dialog', None)
        if tv is None:
            return {'ok': False, 'error': 'TV did not open', 'tool': label}
        sidebar = getattr(tv, 'sidebar', None)
        channels = getattr(sidebar, 'channels_data', None) or []
        uri = ''
        channel_name = ''
        if selection:
            names = [str(c[1]) for c in channels if isinstance(c, (list, tuple)) and len(c) >= 3]
            pick = _fuzzy_one(selection, names, cutoff=0.6)
            if not pick:
                return {'ok': False, 'error': 'channel not found', 'selection': selection, 'ask': 'Which TV channel should I play?', 'available': names[:40]}
            for c in channels:
                if isinstance(c, (list, tuple)) and len(c) >= 3 and (str(c[1]) == pick):
                    channel_name = str(c[1])
                    uri = str(c[2])
                    break
        elif channels:
            c = channels[0]
            if isinstance(c, (list, tuple)) and len(c) >= 3:
                channel_name = str(c[1])
                uri = str(c[2])
        if not uri:
            return {'ok': False, 'error': 'no channel to play', 'ask': 'Which TV channel should I play?'}
        if hasattr(tv, 'play_channel'):
            tv.play_channel(uri)
        return {'ok': True, 'tool': 'TV', 'playing': True, 'selection': channel_name or selection}
    return {'ok': True, 'tool': label, 'opened': True, 'note': 'Opened; play is supported for Radio and TV'}

def _chat_open(args: Dict[str, Any]) -> Dict[str, Any]:
    peer = (args.get('peer') or args.get('name') or args.get('group') or '').strip()
    if not peer:
        return {'ok': False, 'error': 'peer required', 'ask': 'Which peer should I open chat with?'}
    resolved = _resolve_peer(peer)
    if not resolved.get('ok'):
        return {'ok': False, 'error': resolved.get('error') or 'peer not found', 'ask': 'I could not find that peer. Who should I open chat with?', 'query': peer}
    peer_obj = resolved.get('peer') or {}
    peer_id = str(peer_obj.get('peer_id') or peer)
    peer_name = str(peer_obj.get('name') or peer_id)
    record_video = bool(args.get('record_video') or args.get('video_message') or args.get('record'))

    def _after_select():
        chat = getattr(init, 'chat_dialog', None)
        if chat is None or not getattr(chat, 'current_chat_id', None):
            return
        if record_video and hasattr(chat, 'start_recording_video_message'):
            chat.start_recording_video_message()
    from Support.NeuronOrchestrator import open_chat_with_peer
    ok = open_chat_with_peer(peer_id, on_selected=_after_select if record_video else None)
    if not ok:
        return {'ok': False, 'error': 'could not open chat', 'peer': peer_name}
    return {'ok': True, 'peer': peer_name, 'peer_id': peer_id, 'record_video': record_video, 'opened_target': peer_name}

def _cloud_allocation(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.NeuronProjectorSettings import _cloud_space_snapshot
    snap = _cloud_space_snapshot() or {}
    return {'ok': True, 'drives': snap.get('drives') or [], 'total_allocated_gb': snap.get('total_allocated_gb') or 0, 'total_used_gb': snap.get('total_used_gb') or 0, 'total_free_gb': snap.get('total_free_gb') or 0}

def _article_draft_from_news(args: Dict[str, Any]) -> Dict[str, Any]:
    topic = (args.get('topic') or args.get('subject') or args.get('title') or '').strip()
    research = (args.get('research') or args.get('sources') or args.get('text') or '').strip()
    if not topic:
        return {'ok': False, 'error': 'topic required', 'ask': 'What topic should the article cover?'}
    if not research or len(research) < 40:
        return {'ok': False, 'error': 'insufficient research', 'ask': 'I need real news research first. Should I look up current sources for this topic?'}
    timeframe = (args.get('timeframe') or '').strip()
    title = (args.get('title') or topic).strip()
    subtitle = (args.get('subtitle') or timeframe or 'News summary').strip()
    tags = (args.get('tags') or topic).strip()
    body = '<p><b>%s</b></p><p>Draft based on current news research%s.</p><hr/>%s' % (title.replace('<', '&lt;'), ' (%s)' % timeframe.replace('<', '&lt;') if timeframe else '', research if '<' in research else '<p>%s</p>' % research.replace('\n', '</p><p>'))
    saved = _article_save({'title': title, 'subtitle': subtitle, 'article': body, 'tags': tags})
    if not saved.get('ok'):
        return saved
    article_id = saved.get('article_id')
    import re as _re
    preview_text = _re.sub('<[^>]+>', ' ', body or '')
    preview_text = _re.sub('\\s+', ' ', preview_text).strip()[:500]
    open_ui = True
    try:
        if args.get('open_ui') is False:
            open_ui = False
        elif bool(getattr(init, 'conductor_visible', False)):
            open_ui = False
    except Exception:
        pass
    opened = False
    if open_ui:
        try:
            from PySide6.QtCore import QTimer, QThread
            from PySide6.QtWidgets import QApplication
            from Support.Menu import open_dialog

            def _open_and_load():
                try:
                    open_dialog('News', 'WorldNews')
                    dlg = getattr(init, 'news_world_dialog', None)
                    if dlg is not None and hasattr(dlg, 'load_draft_by_id'):
                        dlg.load_draft_by_id(article_id)
                except Exception:
                    pass
            app = QApplication.instance()
            if app is not None and QThread.currentThread() != app.thread():
                QTimer.singleShot(400, app, _open_and_load)
                opened = True
            else:
                _open_and_load()
                opened = True
        except Exception as e:
            return {'ok': True, 'article_id': article_id, 'title': title, 'opened_newsworld': False, 'warning': str(e)[:200], 'research': research[:500], 'hint': 'Draft saved. Review it in World News when ready, then confirm before publishing.'}
    hint = 'Draft saved in My Articles. Review it here, then Yes to publish or No to keep the draft only.' if not opened else 'Review the draft in World News, then confirm before publishing.'
    return {'ok': True, 'article_id': article_id, 'title': title, 'subtitle': subtitle, 'preview_text': preview_text, 'draft_kept_local': True, 'opened_newsworld': opened, 'research': research[:500], 'hint': hint}

_NAMED_UI_OPENERS = {'statistics.open': ('Settings', 'Statistics'), 'subscriptions.open': ('Neurons', 'Subscriptions'), 'ads.open': ('News', 'Ads'), 'store.open': ('News', 'Store'), 'updates.open': ('News', 'AppUpdates'), 'help.open': ('Settings', 'Help'), 'promotions.open': ('News', 'Promotions'), 'public_files.manage.open': ('Public Files', 'Manage'), 'system.open': ('Settings', 'System')}

def _named_ui_open(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if name == 'phone.open':
        taskbar = getattr(init, 'taskbar', None) or getattr(init, 'taskBar', None)
        if taskbar is not None and hasattr(taskbar, '_handle_phone_button_click'):
            taskbar._handle_phone_button_click()
            return {'ok': True, 'opened': 'Phone'}
        return {'ok': False, 'error': 'phone taskbar not ready'}
    if name == 'explorer.open':
        try:
            from Support.Folder import Folder
            if not hasattr(init, 'folder_dialogs'):
                init.folder_dialogs = []
            folder_name = (args.get('folder') or '').strip()
            folder_dialog = Folder(None, folder_name)
            if folder_dialog and (not folder_dialog.property('initialization_failed')):
                init.folder_dialogs.append(folder_dialog)
                folder_dialog.set_background()
                folder_dialog.show_files(folder_name or 'DESKTOP')
                return {'ok': True, 'opened': 'Explorer', 'folder': folder_name or 'DESKTOP'}
            return {'ok': False, 'error': 'explorer failed to open'}
        except Exception as e:
            return {'ok': False, 'error': str(e)[:200]}
    target = _NAMED_UI_OPENERS.get(name)
    if not target:
        return {'ok': False, 'error': 'unknown opener'}
    category, item = target
    from Support.Menu import open_dialog
    open_dialog(category, item)
    return {'ok': True, 'opened': '%s/%s' % (category, item) if item else category}

def _crawl_start(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.NeuronCrawler import crawl_and_extract
    url = (args.get('url') or args.get('link') or '').strip()
    if not url:
        return {'ok': False, 'error': 'url required'}
    url = _normalize_url(url)
    max_depth = int(args.get('max_depth') or 1)
    ip = init.ME.get('ip') or init.ME.get('ip_local') or '127.0.0.1'
    result = crawl_and_extract(ip, url, max_depth=max_depth)
    return {'ok': True, 'url': url, 'result': result if isinstance(result, dict) else {'raw': str(result)[:500]}}

def _crawl_peer_search(args: Dict[str, Any]) -> Dict[str, Any]:
    """Ask KRT-ranked crawl-capable peers for index hits; no file dumps."""
    q = (args.get('query') or args.get('q') or args.get('text') or args.get('prompt') or '').strip()
    if not q:
        return {'ok': False, 'error': 'query required'}
    try:
        timeout_sec = float(args.get('timeout_sec') or args.get('timeout') or 2.0)
    except Exception:
        timeout_sec = 2.0
    timeout_sec = max(0.6, min(timeout_sec, 8.0))
    max_peers = args.get('peers') or args.get('max_peers') or args.get('limit')
    try:
        max_peers = int(max_peers) if max_peers not in (None, '') else None
    except Exception:
        max_peers = None
    if max_peers is not None:
        max_peers = max(1, min(max_peers, 24))
    prefer = []
    try:
        from Support.NeuronAILearning import krt_route
        prefer = [c.get('peer_id') for c in (krt_route(q, limit_peers=max_peers or 8).get('peer_crawl_candidates') or []) if c.get('peer_id')]
    except Exception:
        prefer = []
    candidates = _peer_crawl_candidates(max_peers=max_peers, preferred_peer_ids=prefer or None)
    if not candidates:
        return {'ok': True, 'query': q, 'hits': [], 'count': 0, 'peers_asked': 0, 'ask': 'No crawl-capable peers are online right now.'}
    hits = _peer_crawl_search_sync(q, timeout_sec=timeout_sec, max_peers=max_peers, preferred_peer_ids=prefer or None) or []
    slim = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        slim.append({'title': hit.get('title') or hit.get('name') or '', 'url': hit.get('url') or hit.get('link') or '', 'snippet': (hit.get('snippet') or hit.get('body') or hit.get('text') or hit.get('description') or '')[:280], 'peer': hit.get('peer') or hit.get('peer_name') or hit.get('peer_id') or '', 'score': hit.get('score'), 'source': 'peer_crawl'})
        if len(slim) >= 12:
            break
    return {'ok': True, 'query': q, 'hits': slim, 'count': len(slim), 'peers_asked': len(candidates), 'sources_used': ['peer_crawl'] if slim else [], 'trust': "From peers' crawled pages." if slim else '', 'routed_peers': prefer[:8]}

def _knowledge_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.NeuronAILearning import AILearner
    limit = max(1, min(int(args.get('limit') or 30), 80))
    docs = AILearner().list_knowledge_documents() or []
    slim = []
    for d in docs[:limit]:
        slim.append({'title': d.get('title'), 'base_url': d.get('base_url'), 'topic': d.get('topic') or '', 'chunk_count': d.get('chunk_count') or 0})
    return {'ok': True, 'documents': slim, 'count': len(slim)}

def _knowledge_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    from Support.NeuronAILearning import AILearner
    base_url = (args.get('base_url') or args.get('url') or '').strip()
    title = (args.get('title') or '').strip()
    learner = AILearner()
    if not base_url and title:
        docs = learner.list_knowledge_documents() or []
        names = [d.get('title') or '' for d in docs]
        by_name = {d.get('title') or '': d for d in docs}
        low = title.lower()
        match = None
        for d in docs:
            if str(d.get('title') or '').lower() == low:
                match = d
                break
        if match is None:
            fuzzy = _fuzzy_one(title, names, cutoff=0.7)
            if fuzzy:
                match = by_name.get(fuzzy)
        if match is None:
            return {'ok': False, 'error': 'no knowledge document matched', 'query': title}
        base_url = match.get('base_url') or ''
    if not base_url:
        return {'ok': False, 'error': 'base_url or title required'}
    learner.delete_knowledge_document(base_url)
    return {'ok': True, 'base_url': base_url, 'deleted': True}

def _room_assist(args: Dict[str, Any]) -> Dict[str, Any]:
    """First-slice room co-pilot: local guidance + optional peer ask."""
    prompt = (args.get('prompt') or args.get('message') or '').strip()
    if not prompt:
        return {'ok': False, 'error': 'prompt required'}
    room_name = ''
    try:
        me = getattr(init, 'ME', None) or {}
        if isinstance(me, dict):
            room_name = str(me.get('room') or me.get('room_name') or me.get('current_room') or '')
    except Exception:
        room_name = ''
    guide = 'Room co-pilot for "%s". Focus on shared files, who is online, chat/groups for this room, and clear next clicks. User ask: %s' % (room_name or 'current room', prompt)
    from Support.NeuronAILearning import AILearner
    excerpts = AILearner().query_knowledge(guide, limit=2) or []
    bits = [(e.get('text') or '')[:350] for e in excerpts if e.get('text')]
    lines = ['Room assist (%s):' % (room_name or 'current'), '1) Say what you want done in this room (files, members, meeting, barter).', '2) Use Settings > Rooms to switch rooms; Chat/Groups for people here.']
    if bits:
        lines.append('Local tips:')
        lines.extend(('- ' + b for b in bits))
    peer_notes = []
    if args.get('ask_peers'):
        peers = getattr(init, 'PEERS', None) or []
        if isinstance(peers, dict):
            peers = list(peers.values())
        online = [p for p in peers if isinstance(p, dict) and p.get('online') and p.get('peer_id')][:3]
        q = 'Room co-pilot question: %s' % prompt
        for p in online:
            res = _run_peer_neury_job(p['peer_id'], 'ask', q)
            if res.get('ok') and res.get('text'):
                peer_notes.append('%s: %s' % (p.get('name') or p['peer_id'][:8], res['text'][:300]))
        if peer_notes:
            lines.append('Peer Neurys:')
            lines.extend(('- ' + n for n in peer_notes))
    return {'ok': True, 'room': room_name, 'text': '\n'.join(lines), 'peer_notes': peer_notes}

def _prompt_craft(args: Dict[str, Any]) -> Dict[str, Any]:
    field = (args.get('field') or args.get('domain') or 'general').strip()
    goal = (args.get('goal') or args.get('task') or args.get('prompt') or args.get('topic') or '').strip()
    tone = (args.get('tone') or 'clear and professional').strip()
    if not goal:
        return {'ok': False, 'error': 'goal required'}
    text = ''
    if not args.get('fast'):
        craft = 'Write one reusable high-quality prompt template for the field "%s". Goal: %s. Tone: %s. Include placeholders in [BRACKETS]. Output only the prompt text.' % (field, goal, tone)
        try:
            from Support.NeuronAIMesh import run_local_ollama
            text = run_local_ollama(craft, num_predict=320, timeout_sec=25)
        except Exception:
            text = ''
    if not text:
        text = 'You are an expert in %s. Task: %s. Write in a %s tone. Use these inputs: [CONTEXT], [AUDIENCE], [CONSTRAINTS]. Return only the final deliverable.' % (field, goal, tone)
    out = {'ok': True, 'field': field, 'prompt': text.strip(), 'text': text.strip()}
    if args.get('list_for_sale'):
        out['hint'] = 'Prompt ready. Say "list this prompt for sale" with a name and Neuron price when you want it on Promotions.'
    return out

def _prompt_list_for_sale(args: Dict[str, Any]) -> Dict[str, Any]:
    """List a prompt pack on Promotions using Websites market + sale_type:prompt tag."""
    import shutil
    import tempfile
    from pathlib import Path
    from Support.Utils import generate_random_hash, query_database, ip_to_send_packet_to
    from Support.SendData import send_packet
    from Support.Dictionaries import load_dictionaries
    name = (args.get('name') or args.get('title') or '').strip()
    prompt_text = (args.get('prompt_text') or args.get('prompt') or args.get('text') or '').strip()
    description = (args.get('description') or '').strip() or 'Reusable AI prompt: %s' % name
    try:
        cost = float(args.get('cost') if args.get('cost') is not None else 1.0)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'invalid cost'}
    if not name or not prompt_text:
        return {'ok': False, 'error': 'name and prompt_text required'}
    if cost < 0:
        return {'ok': False, 'error': 'cost must be >= 0'}
    market_id = generate_random_hash()
    tags = 'sale_type:prompt, prompt, neury'
    tmp = Path(tempfile.mkdtemp(prefix='neuron_prompt_'))
    try:
        (tmp / 'prompt.txt').write_text(prompt_text, encoding='utf-8')
        meta = {'name': name, 'description': description, 'sale_type': 'prompt', 'field': args.get('field') or ''}
        (tmp / 'manifest.json').write_text(json.dumps(meta, ensure_ascii=True, indent=2), encoding='utf-8')
        bundles_dir = Path(init.PATH_AI_BUNDLES)
        bundles_dir.mkdir(parents=True, exist_ok=True)
        archive_base = bundles_dir / market_id
        shutil.make_archive(str(archive_base), 'zip', root_dir=str(tmp))
        query_database(init.pool_websites_db, 'INSERT INTO Websites (id, name, description, tags, image1, image2, image3, image4, image5, creator_id, cost, rating, rating_count, country) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (market_id, name, description, tags, None, None, None, None, None, init.ME['id'], cost, 0, 0, init.ME.get('user_country') or ''))
        load_dictionaries(init.DICTIONARY_PEERS)
        my_ip = init.ME.get('ip', '')
        my_ip_local = init.ME.get('ip_local', '')
        my_id = init.ME.get('id', '')
        for peer in init.PEERS or []:
            if not isinstance(peer, dict):
                continue
            if peer.get('peer_id') == my_id or peer.get('id') == my_id:
                continue
            peer_type = peer.get('type')
            if peer_type != getattr(init, 'NODE_TYPE_SUPER_DB_SERVER', None) and peer_type != getattr(init, 'NODE_TYPE_DB_SERVER', None) and (peer_type != getattr(init, 'NODE_TYPE_APP_SERVER', None)):
                if not peer.get('online'):
                    continue
            try:
                dst = ip_to_send_packet_to(peer.get('ip'), peer.get('ip_local'))
                if not dst:
                    continue
                send_packet(my_ip, my_ip_local, '', dst, peer.get('ip_local') or '', '', init.CMD_PRM_WEB, init.PRT_NOR, '', 0, 0, '', [market_id, name, description, tags, None, None, None, None, None, my_id, cost, 0, 0, init.ME.get('user_country') or ''], [10, init.ME.get('node_name', ''), my_ip, my_ip_local, init.ME.get('node_type', 0)], [], '', '', [], [], [])
            except Exception:
                pass
        return {'ok': True, 'id': market_id, 'name': name, 'cost': cost, 'sale_type': 'prompt'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


__all__ = [
    '_chat_send_show_on_wall',
    '_chat_send',
    '_diary_post',
    '_article_save',
    '_article_publish',
    '_mail_send',
    '_resolve_mail_account_hint',
    '_files_neuronize',
    '_split_names',
    '_resolve_group',
    '_chat_send_group',
    '_groups_list',
    '_groups_create',
    '_neurons_balance',
    '_neurons_askgive',
    '_rooms_list',
    '_rooms_switch',
    '_website_rows',
    '_websites_list',
    '_websites_publish',
    '_call_invite',
    '_contacts_list',
    '_contacts_get',
    '_contacts_invite',
    '_lottery_status',
    '_market_search',
    '_market_publish',
    '_market_find_ad',
    '_market_slots',
    '_market_book_skill',
    '_market_buy_or_book',
    '_market_contact',
    '_barters_list',
    '_transactions_list',
    '_apps_list',
    '_files_list',
    '_public_files_search',
    '_diary_list',
    '_macros_list',
    '_macros_run',
    '_goods_list',
    '_current_room',
    '_require_community_owner',
    '_icon_auth_bits',
    '_can_file_action',
    '_resolve_room_icon',
    '_find_phone_call',
    '_resolve_community_room',
    '_diary_follow',
    '_diary_comment',
    '_diary_like',
    '_diary_delete',
    '_files_download',
    '_files_delete',
    '_public_files_set',
    '_rooms_create',
    '_resolve_professional_room',
    '_find_episode_in_room',
    '_channel_status',
    '_channel_create',
    '_channel_rename',
    '_channel_update',
    '_channel_open_studio',
    '_channel_site_open',
    '_channel_go_live',
    '_channel_schedule_live',
    '_channel_restyle',
    '_episode_list',
    '_episode_publish',
    '_episode_update',
    '_episode_reorder',
    '_episode_delete',
    '_episode_open_editor',
    '_rooms_add_peer',
    '_rooms_update',
    '_rooms_delete',
    '_apps_launch',
    '_goods_table',
    '_goods_save',
    '_goods_delete',
    '_assets_list',
    '_assets_save',
    '_assets_delete',
    '_contacts_add',
    '_contacts_delete',
    '_groups_notify_peers',
    '_groups_update',
    '_groups_delete',
    '_groups_peer_change',
    '_chat_attach',
    '_chat_delete',
    '_votes_list',
    '_votes_create',
    '_votes_cast',
    '_votes_results',
    '_barters_create',
    '_barters_bid',
    '_article_list',
    '_article_read',
    '_load_website_plan_executor',
    '_website_builder_tool',
    '_websites_create',
    '_websites_delete',
    '_neurons_purchase',
    '_call_hangup',
    '_screen_share',
    '_devices_list',
    '_devices_set',
    '_vault_open',
    '_macros_assign',
    '_macros_clear',
    '_pedia_list',
    '_pedia_search',
    '_pedia_read',
    '_pedia_publish',
    '_tools_open',
    '_tools_play',
    '_chat_open',
    '_cloud_allocation',
    '_article_draft_from_news',
    '_NAMED_UI_OPENERS',
    '_named_ui_open',
    '_crawl_start',
    '_crawl_peer_search',
    '_knowledge_list',
    '_knowledge_delete',
    '_room_assist',
    '_prompt_craft',
    '_prompt_list_for_sale',
]
