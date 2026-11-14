import os
import threading

from PySide6.QtGui import QPixmap

# Multi-packet CMD_SRV_MOD (AdminConsole sends one .pyd per POST): defer restart
# until received module count for deployment_id reaches total_modules.
_mod_deploy_batch_lock = threading.Lock()
_mod_deploy_batch = {}  # deployment_id -> {expected, received, need_restart}

# Ask a peer to send me his error logs
def _queues_CMD_SYS_LOG(self, queue_item):
    import io
    import zipfile
    from Support.Header import PATH_LOGS, ME, CMD_SYS_LGR, PRT_HGH
    from Support.Utils import ip_to_send_packet_to, raise_exception
    from Support.SendData import send_packet

    # Cap each log so ISP/reverse peers can HTTP the zip to Direct admin
    # without blowing POST timeouts (full shellflash/NEURON logs are huge).
    _MAX_LOG_BYTES = 512 * 1024

    try:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for name in sorted(os.listdir(PATH_LOGS)):
                if not name.lower().endswith('.log'):
                    continue
                full_path = os.path.join(PATH_LOGS, name)
                if not os.path.isfile(full_path):
                    continue
                try:
                    size = os.path.getsize(full_path)
                    if size <= _MAX_LOG_BYTES:
                        zip_file.write(full_path, arcname=name)
                    else:
                        with open(full_path, 'rb') as fh:
                            fh.seek(-_MAX_LOG_BYTES, os.SEEK_END)
                            tail = fh.read()
                        zip_file.writestr(name, tail)
                except Exception:
                    continue

        payload = zip_buffer.getvalue()
        if not payload or len(payload) < 64:
            print("[CMD_SYS_LOG] no log files to send")
            return

        my_id = ''
        my_pub = ''
        try:
            if isinstance(ME, dict):
                my_id = str(ME.get('id') or ME.get('peer_id') or '')
                my_pub = str(ME.get('ip') or '').strip()
        except Exception:
            my_id = ''
            my_pub = ''
        src = queue_item.src or {}
        hdr = queue_item.header or {}
        admin_ip = str(src.get('ip') or '').strip()
        admin_local = str(src.get('ip_local') or '').strip()
        dst_peer_id = str(
            hdr.get('src_peer_id') or src.get('peer_id') or ''
        ).strip()
        same_wan = bool(my_pub and admin_ip and my_pub == admin_ip)

        if same_wan:
            ordered = (admin_local, admin_ip, ip_to_send_packet_to(admin_ip, admin_local))
        else:
            # Remote/ISP peer: public admin IP first (same bug as MAK to CGNAT).
            ordered = (admin_ip, ip_to_send_packet_to(admin_ip, admin_local), admin_local)

        dst_candidates = []
        for cand in ordered:
            c = str(cand or '').strip()
            if c and c not in dst_candidates:
                dst_candidates.append(c)
        if not dst_candidates:
            print("[CMD_SYS_LOG] no admin destination")
            return

        # Never use retry_policy='critical' for LGR: a failed HTTP parks the zip
        # for admin to poll, but Direct AdminConsole does not poll — logs vanish
        # while send_packet returns True. Sync + default = honest live delivery.
        sent = False
        used = ''
        for dst_ip in dst_candidates:
            sent = bool(send_packet(
                ME['ip'], ME['ip_local'], '',
                dst_ip,
                admin_local,
                '',
                CMD_SYS_LGR, PRT_HGH, '', 0, 0, '',
                [], [], [payload],
                '', '', [], [], [],
                'default', False,
                src_peer_id=my_id or None,
                dst_peer_id=dst_peer_id or None,
            ))
            if sent:
                used = dst_ip
                break
        print(
            f"[CMD_SYS_LOG] reply LGR bytes={len(payload)} dst={used or dst_candidates[0]} "
            f"same_wan={same_wan} sent={sent}"
        )
    except Exception:
        raise_exception()


def _retrieved_logs_temp_dirs():
    """Dirs AdminConsole may watch: this MAIN_PATH ADMIN plus sibling Neuron/Neuron_PROD."""
    from Support.Header import PATH_ADMIN, MAIN_PATH
    dirs = []
    primary = os.path.abspath(os.path.join(PATH_ADMIN, "temp_retrieved_logs"))
    dirs.append(primary)
    try:
        parent = os.path.dirname(os.path.abspath(MAIN_PATH).rstrip('\\/'))
        for tree in ('Neuron', 'Neuron_PROD'):
            cand = os.path.abspath(
                os.path.join(parent, tree, 'ADMIN', 'temp_retrieved_logs')
            )
            if cand not in dirs:
                dirs.append(cand)
    except Exception:
        pass
    return dirs


# Send ADMIN my error logs
def _queues_CMD_SYS_LGR(self, queue_item):
    import io
    import shutil
    import zipfile
    import base64
    from Support.Utils import raise_exception

    try:
        data3 = queue_item.file.get('data3') or []
        if not data3 or not data3[0]:
            print("[CMD_SYS_LGR] empty data3")
            return

        zip_bytes = data3[0]
        if isinstance(zip_bytes, str):
            try:
                zip_bytes = base64.b64decode(zip_bytes)
            except Exception:
                zip_bytes = zip_bytes.encode('latin-1', errors='ignore')
        if not isinstance(zip_bytes, (bytes, bytearray)):
            print(f"[CMD_SYS_LGR] data3 type={type(data3[0]).__name__} not bytes")
            return

        # Extract once, then mirror into every ADMIN tree AdminConsole might poll
        # (standalone console often uses source Neuron\\ADMIN while the live
        # Neuron process writes under Neuron_PROD\\ADMIN — replies looked "lost").
        staging = io.BytesIO(bytes(zip_bytes))
        members = []
        with zipfile.ZipFile(staging, 'r') as zip_file:
            for info in zip_file.infolist():
                name = info.filename.replace('\\', '/')
                if not name or name.endswith('/'):
                    continue
                base = os.path.basename(name)
                if not base or base.startswith('.'):
                    continue
                members.append((base, zip_file.read(info)))

        if not members:
            print("[CMD_SYS_LGR] zip had no log members")
            return

        ready_body = (
            str((queue_item.src or {}).get('ip', '') or '') + "\n"
            + str((queue_item.src or {}).get('ip_local', '') or '')
        )
        written = []
        for temp_dir in _retrieved_logs_temp_dirs():
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                os.makedirs(temp_dir, exist_ok=True)
                temp_dir_abs = os.path.abspath(temp_dir)
                for base, blob in members:
                    target = os.path.abspath(os.path.join(temp_dir_abs, base))
                    if not target.startswith(temp_dir_abs + os.sep):
                        continue
                    with open(target, 'wb') as dst:
                        dst.write(blob)
                with open(os.path.join(temp_dir, ".ready"), 'w', encoding='utf-8') as f:
                    f.write(ready_body)
                written.append(temp_dir)
            except Exception as e:
                print(f"[CMD_SYS_LGR] mirror failed {temp_dir}: {e}")
        print(
            f"[CMD_SYS_LGR] extracted {len(members)} log file(s) -> "
            + (", ".join(written) if written else "(none)")
        )
    except Exception:
        raise_exception()


# I receive an error from a user
def _queues_CMD_SRV_ERR(self, queue_item):
    from Support.Header import PATH_LOGS
    from Support.Utils import get_timestamp

    peer_ip = queue_item.src['ip']
    peer_ip_local = queue_item.src['ip_local']
    if queue_item.file['data2']:
        peer_id = queue_item.file['data2'][0]
        peer_name = queue_item.file['data2'][1]
    else:
        peer_id = 'UNKNOWN'
        peer_name = 'UNKNOWN'

    error = queue_item.file['data1'][0]
    if 'SystemExit: 0' in error:
        return
    # Suppress expected/transient non-errors that flood the log
    if '_get_public_ip_with_fallbacks' in error and (
            'ConnectionError' in error or 'ReadTimeoutError' in error or 'Timeout' in error):
        return
    if 'GetAdaptersAddresses() syscall failed' in error:
        return
    if "can't start new thread" in error:
        return
    if 'No space left on device' in error or 'WinError 1450' in error:
        return

    with open(os.path.join(PATH_LOGS, "USER_ERRORS.log"), 'a') as f:
        f.write(f"USER_ID: {peer_id}\n"
                f"USER NAME: {peer_name}\n"
                f"USER IP: {peer_ip} : {peer_ip_local}\n"
                f"DATE: {get_timestamp()}\n"
                f"ERROR: {error}\n"
                f"------------------------------------------------------\n\n")
        f.close()

# I'm an APP SERVER and receive a new app from ADMIN (legacy push; hubs prefer pull)
def _queues_CMD_SRV_APP(self, queue_item):
    from Support.Utils import raise_exception
    from Support.AppStoreDistribution import (
        ingest_hub_zip_payload, is_origin_admin, plaintext_zip_path,
    )

    try:
        data1 = queue_item.file.get('data1') or []
        data3 = queue_item.file.get('data3') or []
        app_id = data1[0]
        try:
            grid_size = int(data1[11]) if len(data1) > 11 else 2
        except Exception:
            grid_size = 2
        peer_id = str(data1[12]) if len(data1) > 12 else ''
        meta = {
            'app_id': app_id,
            'name': data1[1],
            'subtitle': data1[2],
            'description': data1[3],
            'extended_description': data1[4],
            'price': round(float(data1[5] or 0), 5),
            'developer': data1[6],
            'date_available': data1[7],
            'date_updated': data1[7],
            'version': data1[8],
            'main': data1[9],
            'category': data1[10],
            'grid_size': grid_size,
            'peer_id': peer_id,
        }
        zip_file = data3[0] if len(data3) > 0 else b''
        images = {
            'logo': data3[1] if len(data3) > 1 else b'',
            'image1': data3[2] if len(data3) > 2 else b'',
            'image2': data3[3] if len(data3) > 3 else b'',
            'image3': data3[4] if len(data3) > 4 else b'',
            'image4': data3[5] if len(data3) > 5 else b'',
            'image5': data3[6] if len(data3) > 6 else b'',
        }
        if is_origin_admin():
            os.makedirs(os.path.dirname(plaintext_zip_path(app_id)), exist_ok=True)
            with open(plaintext_zip_path(app_id), 'wb') as f:
                f.write(zip_file or b'')
            from Support.AppStoreDistribution import upsert_listing
            upsert_listing(meta, art_logo=images.get('logo') or None, images=images)
        else:
            ingest_hub_zip_payload(meta, zip_file or b'', images=images)

    except Exception as e:
        print(f"[CMD_SRV_APP] Failed to upsert app: {e}")
        raise_exception()


def _queues_CMD_NWS_SYN(self, queue_item):
    """Hub asked for store catalog digest + removals."""
    import json
    from Support.Header import ME, CMD_NWS_SYR, PRT_LOW
    from Support.SendData import send_packet
    from Support.Utils import ip_to_send_packet_to
    from Support.AppStoreDistribution import build_sync_manifest, is_store_hub_node, is_origin_admin

    if not (is_store_hub_node() or is_origin_admin()):
        return
    try:
        manifest = build_sync_manifest()
        payload = json.dumps(manifest, default=str)
        hdr = queue_item.header or {}
        send_packet(
            ME.get('ip', ''), ME.get('ip_local', ''), '',
            ip_to_send_packet_to(hdr.get('src_ip', ''), hdr.get('src_ip_local', '')),
            hdr.get('src_ip_local', '') or '', '',
            CMD_NWS_SYR, PRT_LOW, '', 0, 0, '',
            ['catalog'], [payload], [],
            '', '', [], [], [],
        )
    except Exception as e:
        print('[CMD_NWS_SYN] %s' % e)


def _queues_CMD_NWS_SYR(self, queue_item):
    """Receive catalog digest; queue zip pulls for missing/newer apps."""
    import json
    from Support.AppStoreDistribution import handle_catalog_reply

    try:
        data2 = queue_item.file.get('data2') or []
        raw = data2[0] if data2 else ''
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8', errors='replace')
        manifest = json.loads(raw) if raw else {}
        peer = {
            'ip': (queue_item.header or {}).get('src_ip', ''),
            'ip_local': (queue_item.header or {}).get('src_ip_local', ''),
            'peer_id': (queue_item.header or {}).get('src_peer_id', ''),
        }
        handle_catalog_reply(manifest, from_peer=peer)
    except Exception as e:
        print('[CMD_NWS_SYR] %s' % e)


def _queues_CMD_NWS_ZIP(self, queue_item):
    """Hub requests one app zip (+ listing images) for repository sync."""
    from Support.Header import ME, CMD_NWS_ZIR, PRT_LOW
    from Support.SendData import send_packet
    from Support.Utils import ip_to_send_packet_to
    from Support.AppStoreDistribution import (
        read_hub_zip_bytes, listing_payload_for_app, is_store_hub_node, is_origin_admin,
    )

    if not (is_store_hub_node() or is_origin_admin()):
        return
    try:
        data1 = queue_item.file.get('data1') or []
        app_id = str(data1[0] if data1 else '').strip()
        if not app_id:
            return
        zip_bytes = read_hub_zip_bytes(app_id)
        if not zip_bytes:
            return
        meta, blobs = listing_payload_for_app(app_id)
        form = [
            app_id,
            str(meta.get('name') or app_id),
            str(meta.get('subtitle') or ''),
            str(meta.get('description') or ''),
            str(meta.get('extended_description') or ''),
            float(meta.get('price') or 0),
            str(meta.get('developer') or ''),
            str(meta.get('date_available') or ''),
            str(meta.get('version') or ''),
            str(meta.get('main') or 'main'),
            str(meta.get('category') or ''),
            int(meta.get('grid_size') or 2),
            str(meta.get('peer_id') or ''),
        ]
        raw = [
            zip_bytes,
            blobs.get('logo') or b'',
            blobs.get('image1') or b'',
            blobs.get('image2') or b'',
            blobs.get('image3') or b'',
            blobs.get('image4') or b'',
            blobs.get('image5') or b'',
        ]
        hdr = queue_item.header or {}
        send_packet(
            ME.get('ip', ''), ME.get('ip_local', ''), '',
            ip_to_send_packet_to(hdr.get('src_ip', ''), hdr.get('src_ip_local', '')),
            hdr.get('src_ip_local', '') or '', '',
            CMD_NWS_ZIR, PRT_LOW, '', 0, 0, '',
            form, [], raw,
            '', '', [], [], [],
        )
    except Exception as e:
        print('[CMD_NWS_ZIP] %s' % e)


def _queues_CMD_NWS_ZIR(self, queue_item):
    """Receive hub zip sync payload; store encrypted + listing."""
    from Support.AppStoreDistribution import ingest_hub_zip_payload

    try:
        data1 = queue_item.file.get('data1') or []
        data3 = queue_item.file.get('data3') or []
        if len(data1) < 11:
            return
        try:
            grid_size = int(data1[11]) if len(data1) > 11 else 2
        except Exception:
            grid_size = 2
        meta = {
            'app_id': data1[0],
            'name': data1[1],
            'subtitle': data1[2],
            'description': data1[3],
            'extended_description': data1[4],
            'price': float(data1[5] or 0),
            'developer': data1[6],
            'date_available': data1[7],
            'date_updated': data1[7],
            'version': data1[8],
            'main': data1[9],
            'category': data1[10],
            'grid_size': grid_size,
            'peer_id': str(data1[12]) if len(data1) > 12 else '',
        }
        images = {
            'logo': data3[1] if len(data3) > 1 else b'',
            'image1': data3[2] if len(data3) > 2 else b'',
            'image2': data3[3] if len(data3) > 3 else b'',
            'image3': data3[4] if len(data3) > 4 else b'',
            'image4': data3[5] if len(data3) > 5 else b'',
            'image5': data3[6] if len(data3) > 6 else b'',
        }
        ingest_hub_zip_payload(meta, data3[0] if data3 else b'', images=images)
    except Exception as e:
        print('[CMD_NWS_ZIR] %s' % e)


def _reply_release_seed(queue_item, release_id, version, ok, message):
    """Send ACK/NAK back to the admin who seeded this release."""
    from Support.Header import ME, CMD_SRV_RAK, CMD_SRV_RNK, PRT_LOW
    from Support.SendData import send_packet
    from Support.Utils import ip_to_send_packet_to

    if not release_id:
        return
    try:
        cmd = CMD_SRV_RAK if ok else CMD_SRV_RNK
        my_id = ''
        try:
            my_id = str(ME.get('id') or ME.get('peer_id') or '') if isinstance(ME, dict) else ''
        except Exception:
            my_id = ''
        dst_peer_id = str(
            (queue_item.header or {}).get('src_peer_id')
            or (queue_item.src or {}).get('peer_id')
            or ''
        )
        send_packet(
            ME['ip'], ME['ip_local'], '',
            ip_to_send_packet_to(queue_item.src['ip'], queue_item.src['ip_local']),
            queue_item.src['ip_local'], '',
            cmd, PRT_LOW, '', 0, 0, '',
            [release_id, version or '', message or ''], [], [],
            '', '', [], [], [],
            'critical', False,
            src_peer_id=my_id,
            dst_peer_id=dst_peer_id,
        )
    except Exception:
        pass


def _reply_module_deploy(queue_item, deployment_id, ok, message):
    """Send ACK/NAK back to the admin who sent CMD_SRV_MOD.

    Must be synchronous: async MAK dies when the peer restarts right after
    staging, leaving AdminConsole Pending forever even though the module landed.

    Destination order matters: reverse/CGNAT peers must NOT try admin's
    RFC1918 ip_local first — that hangs on the wrong LAN and burns the
    AdminConsole 180s ACK window (modules stage, admin still times out NAK).
    """
    import time
    from Support.Header import ME, CMD_SRV_MAK, CMD_SRV_MNK, PRT_HGH
    from Support.SendData import send_packet
    from Support.Utils import ip_to_send_packet_to

    if not deployment_id:
        return False
    try:
        cmd = CMD_SRV_MAK if ok else CMD_SRV_MNK
        my_id = ''
        my_pub = ''
        try:
            if isinstance(ME, dict):
                my_id = str(ME.get('id') or ME.get('peer_id') or '')
                my_pub = str(ME.get('ip') or '').strip()
        except Exception:
            my_id = ''
            my_pub = ''
        dst_peer_id = str(
            (queue_item.header or {}).get('src_peer_id')
            or (queue_item.src or {}).get('peer_id')
            or ''
        )
        src = queue_item.src or {}
        admin_ip = str(src.get('ip') or '').strip()
        admin_local = str(src.get('ip_local') or '').strip()
        same_wan = bool(my_pub and admin_ip and my_pub == admin_ip)

        def _private_ip(ip):
            s = str(ip or '').strip()
            if not s:
                return False
            try:
                from Support.Communication import _is_private_ip_address
                return bool(_is_private_ip_address(s))
            except Exception:
                return s.startswith(('10.', '192.168.', '172.16.', '172.17.',
                                     '172.18.', '172.19.', '172.2', '172.3'))

        # Same WAN: LAN local first. Otherwise public first — never lead with
        # a private admin_local that is unreachable from a remote CGNAT peer.
        if same_wan:
            ordered = (admin_local, admin_ip, ip_to_send_packet_to(admin_ip, admin_local))
        else:
            ordered = (admin_ip, ip_to_send_packet_to(admin_ip, admin_local))
            # Only keep admin_local as last resort when it is not private (rare).
            if admin_local and not _private_ip(admin_local):
                ordered = ordered + (admin_local,)

        dst_candidates = []
        for cand in ordered:
            c = str(cand or '').strip()
            if c and c not in dst_candidates:
                dst_candidates.append(c)
        if not dst_candidates:
            dst_candidates = [ip_to_send_packet_to(admin_ip, admin_local) or admin_ip]

        sent = False
        used_dst = ''
        for dst_ip in dst_candidates:
            # One attempt per candidate: multi-retry on a dead private IP was
            # eating the whole AdminConsole ACK timeout before public was tried.
            sent = bool(send_packet(
                ME['ip'], ME['ip_local'], '',
                dst_ip,
                admin_local if same_wan else '',
                '',
                cmd, PRT_HGH, '', 0, 0, '',
                [deployment_id, message or ''], [], [],
                '', '', [], [], [],
                'critical', False,  # sync — must land before any restart
                src_peer_id=my_id,
                dst_peer_id=dst_peer_id,
            ))
            if sent:
                used_dst = dst_ip
                break
            time.sleep(0.2)
        print(
            f"[MODULE_UPDATE] {'ACK' if ok else 'NAK'} deploy={deployment_id} "
            f"dst={used_dst or (dst_candidates[0] if dst_candidates else '')} "
            f"peer={dst_peer_id[:12] if dst_peer_id else ''} "
            f"same_wan={same_wan} sent={sent} msg={message or ''}"
        )
        return bool(sent)
    except Exception as e:
        print(f"[MODULE_UPDATE] ACK/NAK send failed deploy={deployment_id}: {e}")
        return False


def _try_hot_apply_satellite_pending(module_names):
    """Best-effort: stop NeuronResident, swap .pending->.pyd, restart; clear Modules.pending.
    Full app restart still required when main holds the .pyd lock (compiled installs)."""
    import os
    import time
    from Support.Header import MAIN_PATH, pool_main_db
    from Support.Utils import query_database

    if not module_names:
        return []
    applied = []
    satellites = {
        'NeuronResident': 'restart_neuron_resident',
    }
    for name in module_names:
        if name not in satellites:
            continue
        try:
            pending_path = os.path.join(MAIN_PATH, 'Support', f'{name}.pending')
            pyd_path = os.path.join(MAIN_PATH, 'Support', f'{name}.pyd')
            if not os.path.isfile(pending_path):
                continue
            # Stop resident so the .pyd is not locked by the child process.
            try:
                import Support.Header as init
                proc = getattr(init, 'neuron_resident', None)
                if proc is not None:
                    try:
                        proc.terminate()
                        wait = getattr(proc, 'wait', None)
                        if callable(wait):
                            wait(timeout=8)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    try:
                        init.neuron_resident = None
                    except Exception:
                        pass
                    time.sleep(0.8)
            except Exception:
                pass
            # Swap pending onto .pyd
            try:
                with open(pending_path, 'rb') as fh:
                    head = fh.read(2)
                if head != b'MZ':
                    print(f"[MODULE_UPDATE] hot-apply skip {name}: pending not MZ")
                    continue
            except Exception:
                continue
            try:
                if os.path.isfile(pyd_path):
                    try:
                        os.remove(pyd_path)
                    except Exception as e:
                        print(f"[MODULE_UPDATE] hot-apply {name}: cannot remove .pyd ({e})")
                        continue
                os.rename(pending_path, pyd_path)
            except Exception as e:
                print(f"[MODULE_UPDATE] hot-apply {name}: rename failed ({e})")
                continue
            try:
                query_database(
                    pool_main_db,
                    "UPDATE Modules SET pending = '0' WHERE name = ?",
                    (name,),
                )
            except Exception:
                pass
            try:
                from Support.Subprocesses import restart_neuron_resident
                restart_neuron_resident()
            except Exception as e:
                print(f"[MODULE_UPDATE] hot-apply {name}: restart failed ({e})")
            applied.append(name)
            print(f"[MODULE_UPDATE] hot-applied satellite {name}")
        except Exception as e:
            print(f"[MODULE_UPDATE] hot-apply {name} failed: {e}")
    return applied


def _store_module_deploy_reply(deployment_id, status, message, queue_item):
    """Persist module-deploy ACK/NAK for AdminConsole polling (Admin.db)."""
    from Support.Header import pool_admin_db
    from Support.Utils import query_database
    from datetime import datetime

    if not deployment_id:
        return
    try:
        peer_ip = queue_item.src.get('ip', '') if getattr(queue_item, 'src', None) else ''
        peer_ip_local = queue_item.src.get('ip_local', '') if getattr(queue_item, 'src', None) else ''
        peer_id = str((queue_item.header or {}).get('src_peer_id') or '')
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        query_database(pool_admin_db, '''CREATE TABLE IF NOT EXISTS ModuleDeployResults (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deployment_id TEXT,
            peer_ip TEXT,
            peer_ip_local TEXT,
            peer_id TEXT,
            status TEXT,
            message TEXT,
            timestamp TEXT
        )''', (), commit=True)
        query_database(
            pool_admin_db,
            '''INSERT INTO ModuleDeployResults
               (deployment_id, peer_ip, peer_ip_local, peer_id, status, message, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (deployment_id, peer_ip, peer_ip_local, peer_id, status, message or '', timestamp),
            commit=True,
        )
    except Exception as e:
        print(f"[MODULE_UPDATE] store {status} failed deploy={deployment_id}: {e}")


def _queues_CMD_SRV_MAK(self, queue_item):
    data1 = queue_item.file.get('data1') or []
    if not data1:
        return
    deployment_id = data1[0]
    message = data1[1] if len(data1) > 1 else 'accepted'
    _store_module_deploy_reply(deployment_id, 'ack', message, queue_item)
    print(f"[CMD_SRV_MAK] ACK module deploy {deployment_id}: {message}")


def _queues_CMD_SRV_MNK(self, queue_item):
    data1 = queue_item.file.get('data1') or []
    if not data1:
        return
    deployment_id = data1[0]
    message = data1[1] if len(data1) > 1 else 'rejected'
    _store_module_deploy_reply(deployment_id, 'nak', message, queue_item)
    print(f"[CMD_SRV_MNK] NAK module deploy {deployment_id}: {message}")


def _rel_zip_staging_dir(release_id):
    from Support.Header import PATH_DISTRIBUTABLE_RELEASES
    safe = ''.join(c for c in str(release_id or '') if c.isalnum() or c in ('-', '_'))
    return os.path.join(PATH_DISTRIBUTABLE_RELEASES, 'staging', safe or 'unknown')


def _queues_CMD_SRV_REL_zip_piece(queue_item, data1, data3):
    """
    Chunked plaintext signed-zip deploy (AdminConsole Deploy).
    data1: [release_id, admin_ip, version, format, created_utc,
            'zip', piece_idx, piece_total, zip_sha512, apply_local]
    Any peer may accept apply_local zip pieces (not supernode-only).
    """
    from Support.Header import PATH_DISTRIBUTABLE_RELEASES, PATH_RELEASE_PUBKEY, MAIN_PATH
    from Support.ModuleSecurity import (
        verify_signed_release_zip, calculate_release_hash,
        is_release_newer_than_installed, stage_release_from_package,
        _notify_release_restart, _release_version_tuple,
        advertise_release_to_peers,
    )
    import json
    import shutil

    release_id = str(data1[0] or '')
    version = str(data1[2] if len(data1) > 2 else '') or ''
    created_utc = str(data1[4] if len(data1) > 4 else '') or ''
    try:
        piece_idx = int(data1[6]) if len(data1) > 6 else 0
        piece_total = int(data1[7]) if len(data1) > 7 else 0
    except (TypeError, ValueError):
        piece_idx, piece_total = 0, 0
    zip_sha512 = str(data1[8] if len(data1) > 8 else '') or ''
    apply_local = str(data1[9] if len(data1) > 9 else '1').strip().lower() in (
        '1', 'true', 'yes',
    )

    if not release_id or piece_idx < 1 or piece_total < 1 or piece_idx > piece_total:
        _reply_release_seed(queue_item, release_id, version, False, "invalid zip piece header")
        return
    if not data3 or not data3[0] or not isinstance(data3[0], (bytes, bytearray)):
        _reply_release_seed(queue_item, release_id, version, False, "missing zip piece payload")
        return

    piece = bytes(data3[0])
    staging = _rel_zip_staging_dir(release_id)
    os.makedirs(staging, exist_ok=True)
    piece_path = os.path.join(staging, f"piece_{piece_idx:05d}.bin")
    with open(piece_path, 'wb') as f:
        f.write(piece)

    meta_path = os.path.join(staging, 'meta.json')
    meta = {
        'release_id': release_id,
        'version': version,
        'created_utc': created_utc,
        'piece_total': piece_total,
        'zip_sha512': zip_sha512,
        'apply_local': apply_local,
    }
    try:
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f)
    except Exception:
        pass

    have = [
        n for n in os.listdir(staging)
        if n.startswith('piece_') and n.endswith('.bin')
    ]
    print(
        f"[CMD_SRV_REL] zip piece {piece_idx}/{piece_total} for {release_id} "
        f"({len(piece)} bytes); have {len(have)}/{piece_total}"
    )
    if len(have) < piece_total:
        _reply_release_seed(
            queue_item, release_id, version, True,
            f"piece {piece_idx}/{piece_total} ok",
        )
        return

    # Assemble full zip
    parts = []
    for i in range(1, piece_total + 1):
        p = os.path.join(staging, f"piece_{i:05d}.bin")
        if not os.path.isfile(p):
            _reply_release_seed(
                queue_item, release_id, version, False,
                f"missing piece {i}/{piece_total}",
            )
            return
        with open(p, 'rb') as f:
            parts.append(f.read())
    zip_bytes = b''.join(parts)

    if zip_sha512:
        actual = calculate_release_hash(zip_bytes)
        if actual != zip_sha512:
            _reply_release_seed(
                queue_item, release_id, version, False,
                "zip sha512 mismatch",
            )
            return

    ok, manifest, zip_bytes, message = verify_signed_release_zip(
        zip_bytes, PATH_RELEASE_PUBKEY
    )
    if not ok:
        _reply_release_seed(queue_item, release_id, version, False, message or "zip verify failed")
        return
    if manifest.get('release_id') and manifest.get('release_id') != release_id:
        _reply_release_seed(queue_item, release_id, version, False, "release_id mismatch")
        return

    try:
        from Support.ChangeManagement import channel_matches, local_channel
        pkg_ch = manifest.get('channel', 'prod')
        if not channel_matches(pkg_ch, MAIN_PATH):
            msg = f"channel mismatch: package={pkg_ch} local={local_channel(MAIN_PATH)}"
            _reply_release_seed(queue_item, release_id, version, False, msg)
            return
    except Exception as e:
        print(f"[CMD_SRV_REL] channel check skipped: {e}")

    os.makedirs(PATH_DISTRIBUTABLE_RELEASES, exist_ok=True)
    zip_path = os.path.join(PATH_DISTRIBUTABLE_RELEASES, release_id + '.zip')
    with open(zip_path, 'wb') as f:
        f.write(zip_bytes)

    ver = version or manifest.get('version', '')
    maj = int(manifest.get('major') or _release_version_tuple(ver or '0')[0])
    sidecar_path = os.path.join(PATH_DISTRIBUTABLE_RELEASES, release_id + '.json')
    existing_sc = {}
    if os.path.isfile(sidecar_path):
        try:
            with open(sidecar_path, 'r', encoding='utf-8') as f:
                existing_sc = json.load(f) or {}
        except Exception:
            existing_sc = {}
    sidecar = {
        'release_id': release_id,
        'version': ver,
        'created_utc': created_utc or manifest.get('created_utc', ''),
        'requires_restart': manifest.get('requires_restart', True),
        'notes': manifest.get('notes', ''),
        'channel': manifest.get('channel', 'prod'),
        'kind': manifest.get('kind', ''),
        'cumulative': bool(manifest.get('cumulative', manifest.get('kind') == 'minor')),
        'covers_from': manifest.get('covers_from', ''),
        'major': maj,
        'enc_file': release_id + '.nrel',
        'zip_sha512': zip_sha512 or calculate_release_hash(zip_bytes),
        'verified': True,
        'package_size': len(zip_bytes),
        'transport': 'zip',
    }
    if existing_sc.get('seed_status'):
        sidecar['seed_status'] = existing_sc.get('seed_status')
    sidecar.pop('verify_failed', None)
    sidecar.pop('verify_failed_utc', None)
    sidecar.pop('verify_failed_reason', None)

    stage_msg = 'stored zip'
    if apply_local:
        if existing_sc.get('staged') and existing_sc.get('version') == ver:
            stage_msg = 'already staged'
        elif is_release_newer_than_installed(manifest.get('version', '0.0.0')):
            staged, stage_msg = stage_release_from_package(manifest, zip_bytes)
            if staged:
                sidecar['staged'] = True
                sidecar['staged_utc'] = created_utc or manifest.get('created_utc', '')
                print(f"[CMD_SRV_REL] Staged zip release {release_id}: {stage_msg}")
                if manifest.get('requires_restart', True):
                    _notify_release_restart(
                        force_countdown=True,
                        version=str(ver or manifest.get('version') or ''),
                        kind=str(manifest.get('kind') or 'patch'),
                        notes=str(manifest.get('notes') or ''),
                    )
            else:
                print(f"[CMD_SRV_REL] Zip store-only {release_id}: {stage_msg}")
        else:
            stage_msg = 'stored (not newer than installed)'
            print(f"[CMD_SRV_REL] {release_id}: {stage_msg}")

    with open(sidecar_path, 'w', encoding='utf-8') as f:
        json.dump(sidecar, f, indent=2)

    try:
        shutil.rmtree(staging, ignore_errors=True)
    except Exception:
        pass

    _reply_release_seed(
        queue_item, release_id, ver, True,
        stage_msg or f"zip complete ({piece_total} pieces)",
    )
    try:
        advertise_release_to_peers(ver, release_id)
    except Exception:
        pass


def _queues_CMD_SRV_REL(self, queue_item):
    """Receive release package: classic encrypted .nrel (supers) or chunked signed zip (any peer)."""
    from Support.Header import (PATH_DISTRIBUTABLE_RELEASES, PATH_RELEASE_PUBKEY,
                                NODE_TYPE_DB_SERVER, NODE_TYPE_SUPER_DB_SERVER,
                                NODE_TYPE_APP_SERVER, ME)
    from Support.Utils import raise_exception
    from Support.ModuleSecurity import unlock_and_verify_release, calculate_release_hash
    import json

    release_id = ''
    version = ''
    try:
        data1 = queue_item.file.get('data1') or []
        data3 = queue_item.file.get('data3') or []
        if data1:
            release_id = str(data1[0] or '')
            version = str(data1[2] if len(data1) > 2 else '') or ''

        transport = str(data1[5] if len(data1) > 5 else '').strip().lower()
        if transport == 'zip':
            _queues_CMD_SRV_REL_zip_piece(queue_item, data1, data3)
            return

        my_type = None
        if ME:
            my_type = ME.get('node_type', ME.get('type'))
        try:
            my_type = int(my_type) if my_type is not None else None
        except (TypeError, ValueError):
            my_type = None
        supernode_types = (
            int(NODE_TYPE_DB_SERVER),
            int(NODE_TYPE_SUPER_DB_SERVER),
            int(NODE_TYPE_APP_SERVER),
        )

        if my_type not in supernode_types:
            print(f"[CMD_SRV_REL] Ignored - not a supernode (node_type={my_type})")
            _reply_release_seed(queue_item, release_id, version, False,
                                f"not a supernode (node_type={my_type})")
            return

        if not data1 or not data3 or not data3[0]:
            print("[CMD_SRV_REL] Missing release header or payload")
            _reply_release_seed(queue_item, release_id, version, False, "missing header or payload")
            return

        enc_bytes = data3[0]
        if not isinstance(enc_bytes, (bytes, bytearray)):
            print("[CMD_SRV_REL] Payload is not binary")
            _reply_release_seed(queue_item, release_id, version, False, "payload is not binary")
            return

        ok, manifest, zip_bytes, message = unlock_and_verify_release(bytes(enc_bytes), PATH_RELEASE_PUBKEY)
        if not ok:
            print(f"[CMD_SRV_REL] Rejected release {release_id}: {message}")
            _reply_release_seed(queue_item, release_id, version, False, message)
            return

        if manifest.get('release_id') != release_id:
            print("[CMD_SRV_REL] release_id mismatch - rejected")
            _reply_release_seed(queue_item, release_id, version, False, "release_id mismatch")
            return

        try:
            from Support.Header import MAIN_PATH
            from Support.ChangeManagement import channel_matches, local_channel
            pkg_ch = manifest.get('channel', 'prod')
            if not channel_matches(pkg_ch, MAIN_PATH):
                msg = f"channel mismatch: package={pkg_ch} local={local_channel(MAIN_PATH)}"
                print(f"[CMD_SRV_REL] {msg}")
                _reply_release_seed(queue_item, release_id, version, False, msg)
                return
        except Exception as e:
            print(f"[CMD_SRV_REL] channel check skipped: {e}")

        os.makedirs(PATH_DISTRIBUTABLE_RELEASES, exist_ok=True)
        enc_path = os.path.join(PATH_DISTRIBUTABLE_RELEASES, release_id + '.nrel')
        with open(enc_path, 'wb') as f:
            f.write(bytes(enc_bytes))

        sidecar_path = os.path.join(PATH_DISTRIBUTABLE_RELEASES, release_id + '.json')
        already_staged = False
        existing_sc = {}
        if os.path.exists(sidecar_path):
            try:
                with open(sidecar_path, 'r', encoding='utf-8') as f:
                    existing_sc = json.load(f)
                already_staged = bool(existing_sc.get('staged'))
            except Exception:
                already_staged = False

        from Support.ModuleSecurity import _release_version_tuple
        maj = int(manifest.get('major') or _release_version_tuple(
            version or manifest.get('version', '0')
        )[0])
        sidecar = {
            'release_id': release_id,
            'version': version or manifest.get('version', ''),
            'created_utc': manifest.get('created_utc', ''),
            'requires_restart': manifest.get('requires_restart', True),
            'notes': manifest.get('notes', ''),
            'channel': manifest.get('channel', 'prod'),
            'kind': manifest.get('kind', ''),
            'cumulative': bool(manifest.get('cumulative', manifest.get('kind') == 'minor')),
            'covers_from': manifest.get('covers_from', ''),
            'major': maj,
            'enc_file': release_id + '.nrel',
            'zip_sha512': calculate_release_hash(zip_bytes) if zip_bytes else '',
            'enc_sha512': calculate_release_hash(bytes(enc_bytes)),
            'verified': True,
            'package_size': len(bytes(enc_bytes)),
            'transport': 'nrel',
        }
        if existing_sc.get('seed_status'):
            sidecar['seed_status'] = existing_sc.get('seed_status')
        if already_staged:
            sidecar['staged'] = True
            sidecar['staged_utc'] = existing_sc.get('staged_utc', manifest.get('created_utc', ''))
            if existing_sc.get('installed'):
                sidecar['installed'] = True
                sidecar['installed_utc'] = existing_sc.get('installed_utc', '')
        with open(sidecar_path, 'w', encoding='utf-8') as f:
            json.dump(sidecar, f, indent=2)

        print(f"[CMD_SRV_REL] Stored verified release {release_id} version {sidecar['version']}")

        from Support.ModuleSecurity import (
            is_release_newer_than_installed,
            stage_release_from_package,
            _notify_release_restart,
        )

        stage_msg = ''
        if already_staged:
            print(f"[CMD_SRV_REL] Release {release_id} already staged - skipping re-stage")
            stage_msg = "already staged"
        elif is_release_newer_than_installed(manifest.get('version', '0.0.0')):
            staged, stage_msg = stage_release_from_package(manifest, zip_bytes)
            if staged:
                sidecar['staged'] = True
                sidecar['staged_utc'] = manifest.get('created_utc', '')
                with open(os.path.join(PATH_DISTRIBUTABLE_RELEASES, release_id + '.json'), 'w', encoding='utf-8') as f:
                    json.dump(sidecar, f, indent=2)
                print(f"[CMD_SRV_REL] Processed release {release_id} immediately")
                if manifest.get('requires_restart', True):
                    _notify_release_restart(
                        version=str(manifest.get('version') or version or ''),
                        kind=str(manifest.get('kind') or 'patch'),
                        notes=str(
                            sidecar.get('notes') or manifest.get('notes') or ''
                        ),
                    )
            else:
                print(f"[CMD_SRV_REL] Store-only release {release_id}: {stage_msg}")
        else:
            print(f"[CMD_SRV_REL] Release {release_id} not newer than installed - stored for peers")
            stage_msg = "stored for peers (not newer than installed)"

        _reply_release_seed(queue_item, release_id, sidecar.get('version', version), True,
                            stage_msg or "accepted")
        try:
            from Support.ModuleSecurity import advertise_release_to_peers
            advertise_release_to_peers(sidecar.get('version', version), release_id)
        except Exception:
            pass

    except Exception:
        _reply_release_seed(queue_item, release_id, version, False, "exception while processing")
        raise_exception()


def _queues_CMD_SRV_RAK(self, queue_item):
    """Admin receives release seed ACK from a peer."""
    from Support.ModuleSecurity import record_release_seed_reply
    from Support.Dictionaries import filter_dictionary
    from Support.Header import PEERS

    data1 = queue_item.file.get('data1') or []
    if not data1:
        return
    release_id = data1[0]
    version = data1[1] if len(data1) > 1 else ''
    message = data1[2] if len(data1) > 2 else 'accepted'
    ip = queue_item.src.get('ip', '')
    ip_local = queue_item.src.get('ip_local', '')
    peer_name = ''
    peer_id = ''
    matches = filter_dictionary(PEERS, 'ip', ip, 'ip_local', ip_local)
    if matches:
        peer_name = matches[0].get('name', '')
        peer_id = matches[0].get('peer_id', matches[0].get('id', ''))
    record_release_seed_reply(release_id, ip, ip_local, 'ack', message, peer_id, peer_name)
    print(f"[CMD_SRV_RAK] ACK release {release_id} from {peer_name or ip} ({message})")


def _queues_CMD_SRV_RNK(self, queue_item):
    """Admin receives release seed NAK from a peer."""
    from Support.ModuleSecurity import record_release_seed_reply
    from Support.Dictionaries import filter_dictionary
    from Support.Header import PEERS

    data1 = queue_item.file.get('data1') or []
    if not data1:
        return
    release_id = data1[0]
    version = data1[1] if len(data1) > 1 else ''
    message = data1[2] if len(data1) > 2 else 'rejected'
    ip = queue_item.src.get('ip', '')
    ip_local = queue_item.src.get('ip_local', '')
    peer_name = ''
    peer_id = ''
    matches = filter_dictionary(PEERS, 'ip', ip, 'ip_local', ip_local)
    if matches:
        peer_name = matches[0].get('name', '')
        peer_id = matches[0].get('peer_id', matches[0].get('id', ''))
    record_release_seed_reply(release_id, ip, ip_local, 'nak', message, peer_id, peer_name)
    print(f"[CMD_SRV_RNK] NAK release {release_id} from {peer_name or ip}: {message}")


def _queues_CMD_LOT_STR(self, queue_item):
    from Support.Lottery import Lottery

    Lottery.jackpot = queue_item.file['data1'][0]
    Lottery.previous_ip = queue_item.file['data1'][1]
    Lottery.previous_ip_local = queue_item.file['data1'][2]

# A peer is asking for a list of module versions
def _queues_CMD_APP_CHK(self, queue_item):
    from Support.Header import ME, CMD_APP_VRS, PRT_NOR, pool_main_db
    from Support.Utils import query_database, ip_to_send_packet_to
    from Support.SendData import send_packet
    import json

    # Module list
    results = query_database(pool_main_db, "SELECT * FROM Modules", '')


    send_packet(ME['ip'], ME['ip_local'], '',
                ip_to_send_packet_to(queue_item.src['ip'], queue_item.src['ip_local']), queue_item.src['ip_local'], '',
                CMD_APP_VRS, PRT_NOR, '', 0, 0, '',
                [json.dumps(results)], [], [],
                '', '', [], [], [])

# I receive a list of module versions from a server
def _queues_CMD_APP_VRS(self, queue_item):
    """
    Compare peer module list with my module list.

    Returns:
        ask_for_modules: list of module names to download/update
        remove_modules: list of module names to remove
    """
    from Support.Utils import query_database, raise_exception
    from Support.Header import pool_main_db
    import json

    try:
        his_results = json.loads(queue_item.file['data1'][0])
        my_results = query_database(pool_main_db, "SELECT * FROM Modules", '')

        def normalize_rows(rows):
            """
            Convert list of tuples OR list of dicts into
            dictionary keyed by module name.
            """
            modules = {}

            for row in rows:
                if isinstance(row, dict):
                    name = row.get("name")
                    version = str(row.get("version", "0"))
                else:  # assume tuple/list
                    name = row[0]
                    version = str(row[3])

                if name:
                    modules[name] = version

            return modules

        def is_version_newer(peer_version, my_version):
            """
            Compare semantic versions like:
            1.2.3 > 1.2.1
            """
            try:
                peer_parts = [int(x) for x in str(peer_version).split(".")]
                my_parts = [int(x) for x in str(my_version).split(".")]

                length = max(len(peer_parts), len(my_parts))
                peer_parts += [0] * (length - len(peer_parts))
                my_parts += [0] * (length - len(my_parts))

                return peer_parts > my_parts
            except Exception:
                # fallback to simple comparison
                return str(peer_version) > str(my_version)

        # Normalize both lists
        peer_modules = normalize_rows(his_results)
        my_modules = normalize_rows(my_results)

        ask_for_modules = []
        remove_modules = []

        # --------------------------------------------------
        # 1️⃣ New modules or updated modules from peer
        # --------------------------------------------------
        for name, peer_version in peer_modules.items():

            # New module
            if name not in my_modules:
                ask_for_modules.append(name)
                continue

            # Updated module
            my_version = my_modules[name]
            if is_version_newer(peer_version, my_version):
                ask_for_modules.append(name)

        # --------------------------------------------------
        # 2️⃣ Modules I have but peer removed
        # --------------------------------------------------
        for name in my_modules:
            if name not in peer_modules:
                remove_modules.append(name)
        return ask_for_modules, remove_modules

    except:
        raise_exception()


def _queues_CMD_APP_CAT(self, queue_item):
    """Peer asks for release catalog for its major (supernodes / any holder answer)."""
    from Support.Header import ME, CMD_APP_CTL, PRT_NOR
    from Support.Utils import ip_to_send_packet_to
    from Support.SendData import send_packet
    from Support.ModuleSecurity import release_catalog_entries
    import json

    data1 = queue_item.file.get('data1') or []
    major_filter = None
    if data1:
        try:
            major_filter = int(data1[0])
        except (TypeError, ValueError):
            major_filter = None
    entries = release_catalog_entries(major_filter)
    send_packet(
        ME['ip'], ME['ip_local'], '',
        ip_to_send_packet_to(queue_item.src['ip'], queue_item.src['ip_local']),
        queue_item.src['ip_local'], '',
        CMD_APP_CTL, PRT_NOR, '', 0, 0, '',
        [json.dumps(entries), str(major_filter if major_filter is not None else '')],
        [], [], '', '', [], [], []
    )


def _queues_CMD_APP_CTL(self, queue_item):
    """Catalog reply: pull newest applicable package (nrel) or start major piece swarm."""
    from Support.Header import ME, PEERS, CMD_APP_GET, PRT_NOR, NODE_TYPE_DB_SERVER, NODE_TYPE_SUPER_DB_SERVER, NODE_TYPE_APP_SERVER
    from Support.Utils import ip_to_send_packet_to
    from Support.SendData import send_packet
    from Support.ModuleSecurity import (
        get_installed_release_version, select_best_catalog_entry,
    )
    from Support.MajorUpgrade import (
        catalog_needs_piece_swarm, start_major_piece_swarm, set_wanted_release,
        get_wanted_release,
    )
    import json

    data1 = queue_item.file.get('data1') or []
    if not data1:
        return
    try:
        entries = json.loads(data1[0])
    except Exception:
        return
    if not isinstance(entries, list):
        return

    installed = get_installed_release_version()
    best = select_best_catalog_entry(entries, installed)
    if not best or not best.get('release_id'):
        return

    # Do not clobber an in-flight or ready major download with a later CAT.
    cur = get_wanted_release() or {}
    cur_status = str(cur.get('status') or '')
    if cur_status in ('downloading', 'ready_to_apply') and str(cur.get('kind') or '').lower() == 'major':
        return

    set_wanted_release({
        'release_id': best.get('release_id', ''),
        'version': best.get('version', ''),
        'kind': best.get('kind') or '',
        'status': 'wanted',
        'package_size': int(best.get('package_size') or 0),
        'file_hash': best.get('file_hash') or '',
        'piece_size': int(best.get('piece_size') or 0),
        'piece_hashes': list(best.get('piece_hashes') or []),
        'artifact_name': best.get('artifact_name') or '',
        'ops': best.get('ops') or {},
        'covers_from': best.get('covers_from') or '',
        'cumulative': bool(best.get('cumulative', False)),
    })

    # 4GB+ / kind=major: never CMD_APP_PKG — multi-seeder HTTP pieces.
    if catalog_needs_piece_swarm(best):
        start_major_piece_swarm(best)
        print(f"[CMD_APP_CTL] major/swarm pull started for {best.get('release_id')}")
        return

    # Ask catalog sender + online supers + a few online leaves (first PKG wins).
    targets = [{
        'ip': queue_item.src['ip'],
        'ip_local': queue_item.src['ip_local'],
    }]
    for peer in PEERS or []:
        if int(peer.get('online') or 0) != 1:
            continue
        if peer.get('type') in (NODE_TYPE_DB_SERVER, NODE_TYPE_SUPER_DB_SERVER, NODE_TYPE_APP_SERVER):
            if peer.get('ip') == targets[0]['ip'] and peer.get('ip_local') == targets[0]['ip_local']:
                continue
            targets.append({'ip': peer['ip'], 'ip_local': peer['ip_local']})
            if len(targets) >= 4:
                break
    for peer in PEERS or []:
        if len(targets) >= 6:
            break
        if int(peer.get('online') or 0) != 1:
            continue
        if peer.get('type') in (NODE_TYPE_DB_SERVER, NODE_TYPE_SUPER_DB_SERVER, NODE_TYPE_APP_SERVER):
            continue
        dup = any(
            t['ip'] == peer.get('ip') and t['ip_local'] == peer.get('ip_local')
            for t in targets
        )
        if dup:
            continue
        targets.append({'ip': peer['ip'], 'ip_local': peer['ip_local']})

    for dst in targets:
        try:
            send_packet(
                ME['ip'], ME['ip_local'], '',
                ip_to_send_packet_to(dst['ip'], dst['ip_local']), dst['ip_local'], '',
                CMD_APP_GET, PRT_NOR, '', 0, 0, '',
                [best.get('release_id', ''), best.get('version', '')],
                [], [], '', '', [], [], []
            )
        except Exception:
            pass


def _queues_CMD_APP_GET(self, queue_item):
    """Deliver a stored .nrel if we have it (refuse huge/major — piece swarm only)."""
    from Support.Header import ME, CMD_APP_PKG, PRT_NOR, PATH_DISTRIBUTABLE_RELEASES
    from Support.Utils import ip_to_send_packet_to
    from Support.SendData import send_packet
    from Support.ModuleSecurity import load_release_sidecar
    from Support.MajorUpgrade import MAJOR_MESH_PKG_MAX_BYTES
    import os

    data1 = queue_item.file.get('data1') or []
    release_id = data1[0] if data1 else ''
    version = data1[1] if len(data1) > 1 else ''
    if not release_id:
        return
    sidecar = load_release_sidecar(release_id) or {}
    if str(sidecar.get('kind') or '').lower() == 'major':
        print(f"[CMD_APP_GET] refuse major {release_id} via mesh PKG (use piece swarm)")
        return
    if str(sidecar.get('transport') or '') == 'piece_swarm':
        print(f"[CMD_APP_GET] refuse piece_swarm package {release_id}")
        return
    enc_name = sidecar.get('enc_file') or (release_id + '.nrel')
    enc_path = os.path.join(PATH_DISTRIBUTABLE_RELEASES, enc_name)
    if not os.path.isfile(enc_path):
        return
    size = os.path.getsize(enc_path)
    if size > MAJOR_MESH_PKG_MAX_BYTES:
        print(f"[CMD_APP_GET] refuse oversized {release_id} ({size} bytes)")
        return
    with open(enc_path, 'rb') as f:
        enc_bytes = f.read()
    send_packet(
        ME['ip'], ME['ip_local'], '',
        ip_to_send_packet_to(queue_item.src['ip'], queue_item.src['ip_local']),
        queue_item.src['ip_local'], '',
        CMD_APP_PKG, PRT_NOR, '', 0, 0, '',
        [release_id, version or sidecar.get('version', '')],
        [], [enc_bytes], '', '', [], [], []
    )


def _queues_CMD_APP_PKG(self, queue_item):
    """Receive .nrel bytes, store/stage, then advertise to peers."""
    from Support.ModuleSecurity import store_verified_release_bytes, advertise_release_to_peers

    data1 = queue_item.file.get('data1') or []
    data3 = queue_item.file.get('data3') or []
    release_id = data1[0] if data1 else ''
    version = data1[1] if len(data1) > 1 else ''
    if not data3 or not data3[0]:
        return
    enc_bytes = data3[0]
    if not isinstance(enc_bytes, (bytes, bytearray)):
        return
    ok, msg, sidecar = store_verified_release_bytes(bytes(enc_bytes), release_id, version)
    if ok and sidecar:
        try:
            from Support.MajorUpgrade import clear_wanted_release, get_wanted_release
            wanted = get_wanted_release()
            # Only clear wanted when this package was staged (or already held).
            staged_ok = bool(sidecar.get('staged')) or 'staged' in (msg or '').lower()
            if staged_ok and (
                wanted.get('release_id') in (sidecar.get('release_id'), release_id)
                or wanted.get('version') == (sidecar.get('version') or version)
            ):
                clear_wanted_release()
        except Exception:
            pass
        advertise_release_to_peers(
            sidecar.get('version') or version,
            sidecar.get('release_id') or release_id
        )
        print(f"[CMD_APP_PKG] {release_id} {msg}")
    else:
        print(f"[CMD_APP_PKG] rejected {release_id}: {msg}")


def _queues_CMD_APP_ADV(self, queue_item):
    """Peer advertises a newer version; pull if behind; rebroadcast if we already hold it."""
    from Support.Header import (
        ME, PEERS, CMD_APP_CAT, PRT_NOR, PATH_DISTRIBUTABLE_RELEASES,
        NODE_TYPE_DB_SERVER, NODE_TYPE_SUPER_DB_SERVER, NODE_TYPE_APP_SERVER,
        SYSTEM_ADMIN_IP, SYSTEM_ADMIN_IP_LOCAL
    )
    from Support.Utils import ip_to_send_packet_to
    from Support.SendData import send_packet
    from Support.ModuleSecurity import (
        get_installed_release_version, _release_version_tuple, load_release_sidecar,
        advertise_release_to_peers, release_applicable_to_installed,
    )
    from Support.MajorUpgrade import should_amplify_adv, set_wanted_release, get_wanted_release
    import os

    if ME and (ME.get('ip') == SYSTEM_ADMIN_IP or ME.get('ip') == SYSTEM_ADMIN_IP_LOCAL):
        return
    data1 = queue_item.file.get('data1') or []
    if not data1:
        return
    adv_version = str(data1[0] or '')
    adv_release_id = str(data1[1] or '') if len(data1) > 1 else ''
    adv_kind = str(data1[3] or '') if len(data1) > 3 else ''
    adv_covers = str(data1[4] or '') if len(data1) > 4 else ''
    adv_cumul = str(data1[5] or '') if len(data1) > 5 else ''
    if not adv_version:
        return
    installed = get_installed_release_version()
    sc = load_release_sidecar(adv_release_id) if adv_release_id else {}
    enc_path = ''
    if adv_release_id:
        enc_path = os.path.join(
            PATH_DISTRIBUTABLE_RELEASES,
            (sc or {}).get('enc_file') or (adv_release_id + '.nrel')
        )
    art = (sc or {}).get('artifact_path') or ''
    we_hold = bool(sc and (
        sc.get('staged') or sc.get('downloaded') or sc.get('installed')
        or (enc_path and os.path.isfile(enc_path))
        or (art and os.path.isfile(art))
        or (sc.get('file_hash') and str(sc.get('kind') or '').lower() == 'major')
    ))

    # Gossip amplify: holders fan out ADV; also stage anything already on disk.
    if we_hold:
        try:
            from Support.ModuleSecurity import process_stored_releases
            process_stored_releases()
        except Exception:
            pass
        if should_amplify_adv(adv_release_id or adv_version):
            advertise_release_to_peers(adv_version, adv_release_id)
        return

    if _release_version_tuple(adv_version) <= _release_version_tuple(installed):
        return

    kind = adv_kind or (sc or {}).get('kind') or ''
    cumul = bool(sc.get('cumulative')) if sc else (adv_cumul == '1' or kind == 'minor')
    if adv_cumul in ('0', '1'):
        cumul = adv_cumul == '1'
    probe = {
        'version': adv_version,
        'kind': kind,
        'covers_from': adv_covers or (sc or {}).get('covers_from') or '',
        'cumulative': cumul,
        'release_id': adv_release_id,
    }
    if kind and not release_applicable_to_installed(probe, installed):
        return

    cur = get_wanted_release() or {}
    if str(cur.get('status') or '') in ('downloading', 'ready_to_apply') and str(
        cur.get('kind') or ''
    ).lower() == 'major':
        return

    set_wanted_release({
        'release_id': adv_release_id,
        'version': adv_version,
        'kind': kind,
        'covers_from': probe.get('covers_from') or '',
        'cumulative': cumul,
        'status': 'wanted',
    })

    major = int(_release_version_tuple(installed)[0])
    asked = 0
    # Ask advertiser first (closest holder), then online supers, then a few online leaves.
    try:
        send_packet(
            ME['ip'], ME['ip_local'], '',
            ip_to_send_packet_to(queue_item.src['ip'], queue_item.src['ip_local']),
            queue_item.src['ip_local'], '',
            CMD_APP_CAT, PRT_NOR, '', 0, 0, '',
            [str(major)], [], [], '', '', [], [], []
        )
        asked += 1
    except Exception:
        pass
    for peer in PEERS or []:
        if asked >= 4:
            break
        if int(peer.get('online') or 0) != 1:
            continue
        if peer.get('type') in (NODE_TYPE_DB_SERVER, NODE_TYPE_SUPER_DB_SERVER, NODE_TYPE_APP_SERVER):
            send_packet(
                ME['ip'], ME['ip_local'], '',
                ip_to_send_packet_to(peer['ip'], peer['ip_local']), peer['ip_local'], '',
                CMD_APP_CAT, PRT_NOR, '', 0, 0, '',
                [str(major)], [], [], '', '', [], [], []
            )
            asked += 1
    for peer in PEERS or []:
        if asked >= 7:
            break
        if int(peer.get('online') or 0) != 1:
            continue
        if peer.get('type') in (NODE_TYPE_DB_SERVER, NODE_TYPE_SUPER_DB_SERVER, NODE_TYPE_APP_SERVER):
            continue
        send_packet(
            ME['ip'], ME['ip_local'], '',
            ip_to_send_packet_to(peer['ip'], peer['ip_local']), peer['ip_local'], '',
            CMD_APP_CAT, PRT_NOR, '', 0, 0, '',
            [str(major)], [], [], '', '', [], [], []
        )
        asked += 1


def _queues_CMD_SRV_SQL(self, queue_item):
    from Support.Header import ME, CMD_SRV_SQR, pool_main_db, PRT_LOW
    from Support.Utils import query_database, ip_to_send_packet_to, raise_exception
    from Support.SendData import send_packet
    import json
    import re

    def _unwrap_sql(value):
        # Packet fields sometimes arrive nested: [['SELECT ...']]
        while isinstance(value, (list, tuple)) and len(value) == 1:
            value = value[0]
        if isinstance(value, bytes):
            try:
                value = value.decode('utf-8', errors='replace')
            except Exception:
                value = str(value)
        return str(value or '').strip()

    data1 = queue_item.file.get('data1') or []
    query = _unwrap_sql(data1[0] if data1 else '')
    payload = '[]'
    try:
        if not query:
            payload = json.dumps({"error": "empty query", "rows": []}, default=str)
        elif not re.match(
            r'(?is)\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|PRAGMA|WITH|REPLACE|ATTACH|DETACH|ANALYZE|REINDEX|VACUUM)\b',
            query,
        ):
            # Reject junk that produces sqlite3 "near *" (e.g. "DELETE * FROM ...", bare "*")
            payload = json.dumps(
                {"error": "unsupported or empty SQL", "rows": [], "query": query[:200]},
                default=str,
            )
        else:
            # Prefer read-only semantics for SELECT; still allow admin writes as before
            rows = query_database(
                pool_main_db,
                query,
                (),
                commit=not query.lstrip().upper().startswith('SELECT'),
            )
            payload = json.dumps(rows, default=str)
    except Exception as e:
        # Do not raise_exception() — SQL syntax failures are expected admin feedback,
        # and flooding the peer console with a full traceback is noise.
        payload = json.dumps({"error": str(e), "rows": [], "query": query[:200]}, default=str)

    try:
        my_id = ''
        try:
            my_id = str(ME.get('id') or ME.get('peer_id') or '') if isinstance(ME, dict) else ''
        except Exception:
            my_id = ''
        dst_peer_id = str(
            (queue_item.header or {}).get('src_peer_id')
            or (queue_item.src or {}).get('peer_id')
            or ''
        )
        send_packet(
            ME['ip'], ME['ip_local'], '',
            ip_to_send_packet_to(queue_item.src['ip'], queue_item.src['ip_local']),
            queue_item.src['ip_local'], '',
            CMD_SRV_SQR, PRT_LOW, '', 0, 0, '',
            [payload], [], [],
            '', '', [], [], [],
            'critical',
            False,
            src_peer_id=my_id,
            dst_peer_id=dst_peer_id,
        )
    except Exception:
        raise_exception()

def _queues_CMD_SRV_DIS(self, queue_item):
    from Support.MessageBox import MessageBox
    from Support.Header import MESSAGE_CRITICAL, PEERS, pool_main_db
    from Support.Utils import query_database, ip_to_send_packet_to
    from Support.SendData import send_packet
    import Support.Header as init

    # The admin sent a message
    if queue_item.file['name']:
        MessageBox.show("Disable peer",
                        "Are you sure you wish to disable the selected peer/s)?",
                        MESSAGE_CRITICAL)

    # The admin wants to propagate the command to all peers
    if queue_item.file['crc']:
        from Support.ModuleSecurity import admin_auth_from_header
        _auth = admin_auth_from_header(queue_item.header)
        for peer in PEERS:
            send_packet(
                '',
                '', '',
                ip_to_send_packet_to(peer['ip'], peer['ip_local']), peer['ip_local'], '',
                queue_item.header['command'],
                queue_item.header['priority'],
                queue_item.file['crc'],
                0,
                0,
                queue_item.file['name'],
                [],
                [],
                [],
                '',
                '',
                [],
                [],
                [],
                admin_sig=_auth.get('admin_sig'),
                admin_ts=_auth.get('admin_ts'),
                admin_nonce=_auth.get('admin_nonce'))

    # Deactivate this peer
    try:
        query_database(pool_main_db, "UPDATE SystemInfo SET active = ?", ('0',))
    except:
        pass
    init.SYSTEM_ACTIVE = False


# Enable a peer
def _queues_CMD_SRV_ENA(self, queue_item):
    from Support.MessageBox import MessageBox
    from Support.Header import MESSAGE_CRITICAL, PEERS, pool_main_db
    from Support.Utils import query_database, ip_to_send_packet_to
    from Support.SendData import send_packet
    import Support.Header as init

    # The admin sent a message
    if queue_item.file['name']:
        MessageBox.show("Enable peers",
                        "Are you sure you wish to enable the selected peer(s)?",
                        MESSAGE_CRITICAL)

    # The admin wants to propagate the command to all peers
    if queue_item.file['crc']:
        from Support.ModuleSecurity import admin_auth_from_header
        _auth = admin_auth_from_header(queue_item.header)
        for peer in PEERS:
            send_packet(
                '',
                '', '',
                ip_to_send_packet_to(peer['ip'], peer['ip_local']), peer['ip_local'], '',
                queue_item.header['command'],
                queue_item.header['priority'],
                queue_item.file['crc'],
                0,
                0,
                queue_item.file['name'],
                [],
                [],
                [],
                '',
                '',
                [],
                [],
                [],
                admin_sig=_auth.get('admin_sig'),
                admin_ts=_auth.get('admin_ts'),
                admin_nonce=_auth.get('admin_nonce'))

    # Reactivate this peer
    try:
        query_database(pool_main_db, "UPDATE SystemInfo SET active = ?", ('1',))
    except:
        pass
    init.SYSTEM_ACTIVE = True


# Give/Take Neurons
def _queues_CMD_SRV_NEU(self, queue_item):
    from Support.Misc import update_neurons

    message = queue_item.file['data1'][0]
    amount = float(queue_item.file['data1'][1])

    update_neurons(amount, "I receive an anonymous gift." if amount else
    "I return an erroneous amount previously credited to me. " + message if message else '')


# Admin wants my info
def _queues_CMD_SRV_USR(self, queue_item):
    from Support.Header import PEERS, CMD_SRV_USR, PRT_NOR, CMD_SRV_USA, ME
    from Support.SendData import send_packet
    from Support.Utils import ip_to_send_packet_to, raise_exception, parse_user_space_entries

    try:
        # Propagate the request to all peers except for the originator
        from Support.ModuleSecurity import admin_auth_from_header
        _auth = admin_auth_from_header(queue_item.header)
        for peer in PEERS:
            if peer['ip'] != queue_item.src['ip'] and peer['ip_local'] != queue_item.src['ip_local']:
                send_packet(queue_item.src['ip'], queue_item.src['ip_local'], '',
                    ip_to_send_packet_to(peer['ip'], peer['ip_local']), peer['ip_local'], '',
                    CMD_SRV_USR, PRT_NOR, '', 0, 0, "",
                    [], [], [],
                    '', '', [], [], [],
                    admin_sig=_auth.get('admin_sig'),
                    admin_ts=_auth.get('admin_ts'),
                    admin_nonce=_auth.get('admin_nonce'))

    # Send back to the admin my node type, the amount of Neurons I have, my allotted disk space and my country
        entries = parse_user_space_entries(ME.get('user_space', ''))
        total_space = sum(allocation for _, allocation in entries)

        release_ver = ''
        try:
            from Support.ModuleSecurity import get_installed_release_version
            release_ver = str(get_installed_release_version() or '')
        except Exception:
            release_ver = ''
        stats_data = [
            ME['node_type'], ME['user_neurons'], total_space, ME['user_country_code'], release_ver
        ]
        print(f"[CMD_SRV_USR] Sending response to {queue_item.src['ip']}:{queue_item.src['ip_local']}")
        print(
            f"[CMD_SRV_USR] Sending: type={stats_data[0]}, neurons={stats_data[1]}, "
            f"space={stats_data[2]}, country={stats_data[3]}, version={stats_data[4]}"
        )

        send_packet(ME['ip'], ME['ip_local'], '',
            ip_to_send_packet_to(queue_item.src['ip'], queue_item.src['ip_local']), queue_item.src['ip_local'], '',
            CMD_SRV_USA, PRT_NOR, '', 0, 0, "",
            stats_data, [], [],
            '', '', [], [], [])

    except:
        raise_exception()

def _queues_CMD_LOT_WIN(self, queue_item):
    from Support.Header import PEERS, CMD_LOT_STR, PRT_NOR, ME
    from Support.Initializations import setShadowEffect
    from Support.SendData import send_packet
    from Support.Utils import ip_to_send_packet_to
    from Support.Misc import update_neurons
    from Support.Lottery import Lottery

    # Show the animation and the amount won
    lottery = Lottery(self)
    setShadowEffect(self, lottery)

    # Grab the old lottery info bundle
    old_lottery = queue_item.file['data1']
    old_ip = old_lottery.ip
    old_ip_local = old_lottery.ip_local

    # Increase the Neurons by the indicated amount
    update_neurons(old_lottery.jackpot, "I won the lottery!!!")

    # Reset the starting award amount to 10
    Lottery = old_lottery
    Lottery.running = True
    Lottery.executed = False
    Lottery.jackpot = 10
    Lottery.previous_ip = ME['ip']
    Lottery.previous_ip_local = ME['ip_local']

    # Pick the peer who will run the next lottery, making sure he's online and not the one who passed it to me
    for next_peer in PEERS:
        if (next_peer['online'] == 1 and
                next_peer['ip'] != old_ip and
                next_peer['ip_local'] != old_ip_local):
            # Tell that peer to start the lottery
            send_packet(
                '',
                '', '',
                ip_to_send_packet_to(next_peer['ip'], next_peer['ip_local']), next_peer['ip_local'], '',
                CMD_LOT_STR,
                PRT_NOR,
                '',
                0,
                0,
                '',
                [Lottery.jackpot, Lottery.previous_ip, Lottery.previous_ip_local],
                [],
                [],
                '',
                '',
                [],
                [],
                [])

            break


def _queues_CMD_SRV_PER(self, queue_item):
    from Support.Header import PEERS
    from Support.Dictionaries import filter_dictionary, save_data
    from Support.Misc import try_to_add_to_peer_list

    peer_info = queue_item.file['data1']
    peer_image_data = queue_item.file['data3']
    count = 0
    before = len(PEERS)

    for peer in peer_info:

        try:
            peer_id = peer['peer_id']
        except:
            peer_id = peer[0] if isinstance(peer, (list, tuple)) else peer.get('peer_id', '')

        already_in_list = filter_dictionary(PEERS,
                                            'peer_id', peer_id,
                                            '', '',
                                            '', '')

        # If the peer is not already in my list, I add him
        if not already_in_list:

            # Add the peer record
            if not PEERS[0]['id']:
                # Convert dictionary to list format if needed
                if isinstance(peer, dict):
                    peer_list = [
                        peer.get('peer_id', ''),
                        peer.get('name', ''),
                        peer.get('url', ''),
                        peer.get('ip', ''),
                        peer.get('ip_local', ''),
                        peer.get('city', ''),
                        peer.get('country', ''),
                        peer.get('type', 0),
                        peer.get('reliability', 0),
                        peer.get('image', ''),
                        peer.get('shared_folder', ''),
                        peer.get('shared_printer', '')
                    ]
                else:
                    peer_list = peer
                try_to_add_to_peer_list(self, peer_list, peer_image_data[count])

        count += 1

    # If my peer list in now longer, I save the PEERS list to DB
    if len(PEERS) > before:
        save_data(only_key="PEERS")


def _queues_CMD_SRV_QNU(self, queue_item):
    from Support.Header import PEERS, ME, CMD_SRV_QNA, CMD_SRV_QNU, PRT_NOR
    from Support.SendData import send_packet
    from Support.Utils import ip_to_send_packet_to

    all_ips = queue_item.file['data1']
    my_ips = []

    # If I receive a list of IPs it means we want all peers
    if all_ips:
        for peer in PEERS:
            this_ip = peer['ip'] + ":" + peer['ip_local']
            if this_ip not in all_ips and this_ip != queue_item.src['ip'] + ":" + queue_item.src['ip_local']:
                my_ips.append(peer['ip'] + ":" + peer['ip_local'])

    # Reply with my amount of Neurons
    send_packet(
        ME['ip'],
        ME['ip_local'], '',
        ip_to_send_packet_to(queue_item.src['ip'], queue_item.src['ip_local']), queue_item.src['ip_local'], '',
        CMD_SRV_QNA,
        queue_item.header['priority'],
        '',
        0,
        0,
        '',
        [ME['node_name'], ME['ip'], ME['ip_local'], ME['user_country_code'],
         ME['user_neurons']],
        [],
        [],
        '',
        '',
        [],
        [],
        [])

    # Ask my peers who weren't part of the original list how many Neurons they have
    if all_ips:
        for peer in my_ips:
            all_ips.append(peer)

        # Ask all my contacts who are not in list
        for peer in my_ips:
            ip, ip_local = peer.split(":")

            send_packet(
                queue_item.src['ip'],
                queue_item.src['ip_local'], '',
                ip_to_send_packet_to(ip, ip_local), ip_local, '',
                CMD_SRV_QNU,
                PRT_NOR,
                '',
                0,
                0,
                '',
                all_ips,
                [],
                [],
                '',
                '',
                [],
                [],
                [])


# Administrator is asking me to modify the files
def _queues_CMD_SRV_MDF(self, queue_item):
    from Support.Header import MAIN_PATH

    file_names = queue_item.file['data1'] if queue_item.file['data1'] else []
    file_data_list = queue_item.file['data3'] if queue_item.file['data3'] else []
    root = os.path.abspath(MAIN_PATH)

    for i, file_path in enumerate(file_names):
        if i >= len(file_data_list):
            break

        file_data = file_data_list[i]
        rel = str(file_path or '').replace('/', os.sep).replace('\\', os.sep)
        if os.path.isabs(rel) or '..' in rel.split(os.sep):
            print("[CMD_SRV_MDF]: Rejected unsafe path")
            continue
        full_path = os.path.abspath(os.path.join(root, rel))
        if full_path != root and not full_path.startswith(root + os.sep):
            print("[CMD_SRV_MDF]: Rejected path outside install")
            continue

        if file_data:
            try:
                file_dir = os.path.dirname(full_path)
                if file_dir:
                    os.makedirs(file_dir, exist_ok=True)
                with open(full_path, 'wb') as f:
                    f.write(file_data)
            except Exception as e:
                print(f"[CMD_SRV_MDF]: Error writing file: {e}")
        else:
            try:
                if os.path.exists(full_path):
                    os.remove(full_path)
            except Exception as e:
                print(f"[CMD_SRV_MDF]: Error removing file: {e}")




def _queues_CMD_BAN_SND(self, queue_item):
    import json
    from Support.Header import (PATH_NEURON_BANNERS, PRT_NOR, banner, pool_main_db,
                                         PEERS, CMD_BAN_SND, BALANCE_SHEET_I_ACCEPT_BANNER_CAMPAIGN, ME)
    from Support.SendData import send_packet
    from Support.Utils import ip_to_send_packet_to, query_database
    from Support.Misc import update_neurons
    from Support.Dictionaries import filter_dictionary

    ad_id = queue_item.file['data1'][0]  # AD_ID
    file_name = queue_item.file['data1'][1]  # File name
    urls = queue_item.file['data1'][2]
    impressions = queue_item.file['data1'][3]  # Impressions
    peer_id = queue_item.file['data1'][4]  # Peer ID
    strikes = queue_item.file['data1'][5]  # Strikes
    cities = queue_item.file['data1'][6]  # Cities
    countries = queue_item.file['data1'][7]  # Countries
    date_end = queue_item.file['data1'][8]  # Date end
    campaign_name = queue_item.file['data1'][9]  # Campaign name
    new = queue_item.file['data1'][10]  # New
    propagate = int(queue_item.file['data1'][11])  # Propagate
    neurons = queue_item.file['data1'][12]  # Neurons

    file_data = queue_item.file['data3'][0] if queue_item.file['data3'] else b''

    # Save the banner
    if file_name:
        try:
            with open(os.path.join(PATH_NEURON_BANNERS, file_name), 'wb') as f:
                f.write(file_data)
        except Exception as e:
            print(f"[BANNER]: Error saving banner {e}")

        # Check if the campaign already exists
        current_campaigns = query_database(pool_main_db, "SELECT * FROM Ads WHERE ad_id = ?", (ad_id,))

        # No existing campaigns
        if not current_campaigns:
            # Save the info
            query_database(pool_main_db, "INSERT INTO Ads (ad_id, file_name, link, impressions, peer_id, "
                 "strikes, cities, countries, date_end, campaign_name, new) "
                 "VALUES (?, ?, ?, ?, ?, ? ,?, ?, ?, ?, ?)",
                 (ad_id, file_name, urls, impressions, peer_id, 0, cities, countries, date_end, campaign_name, 0))

        # There are existing campaigns
        else:
            for current_campaign in current_campaigns:
                # There already is a banner campaign for the same banner, we just update the impressions
                if current_campaign[0] == file_name:
                    query_database(pool_main_db,
                       "UPDATE Ads SET link = ?, impressions = ?, cities = ?, countries = ? WHERE ad_id = ?",
                       (urls, impressions, cities, countries, ad_id))

                # There's already a click campaign, so we simply update the data
                else:
                    query_database(pool_main_db,
                       "UPDATE Ads SET link = ?, date_end = ? WHERE ad_id = ?",
                       (urls, date_end, ad_id))

        banner.banner_show()

        # I'm a first level peer, so I propagate
        if propagate:

            # Look for online peers
            online_peers = filter_dictionary(PEERS, 'online', 1)

            # There are no online peers (assuming the only online peer is the sender), I take the
            # whole sum and exit
            if len(online_peers) <= 1:
                update_neurons(neurons, BALANCE_SHEET_I_ACCEPT_BANNER_CAMPAIGN)
                return

            # There are online peers other than the sender, I subtract my part and divide the remainder by the
            # number of online peers minus the sender
            else:
                my_part = round(neurons * .2, 5)
                update_neurons(neurons, BALANCE_SHEET_I_ACCEPT_BANNER_CAMPAIGN)
                their_part = round((neurons - my_part) / (online_peers - 1), 5)

            city_list = json.loads(cities)
            country_list = json.loads(countries)

            for peer in online_peers:
                # Don't send it to the peer who sent it to me. Also if we have a specific list of cities
                # and/or countries, we don't send it to peers who are not in those cities/countries
                if (peer['peer_id'] == peer_id or
                        (city_list and peer['city'] not in city_list) or
                        (country_list and peer['country'] not in country_list)):
                    continue

                send_packet(
                    ME['ip'], ME['ip_local'], '',
                    ip_to_send_packet_to(peer['ip'], peer['ip_local']), peer['ip_local'], '',
                    CMD_BAN_SND, PRT_NOR, '', 0, 0, '',
                    [ad_id, file_name, urls, impressions, peer_id, strikes, cities, countries,
                     date_end, campaign_name, new, 0, their_part], [], [file_data],
                    '', '', [], [], [])

            # I'm a second level peer, so take all that was sent to me
            else:
                update_neurons(neurons, BALANCE_SHEET_I_ACCEPT_BANNER_CAMPAIGN)

        # I'm a first level peer but I'm not asked to propagate, therefore I take all the Neurons for myself
        else:
            update_neurons(neurons, BALANCE_SHEET_I_ACCEPT_BANNER_CAMPAIGN)


    # There is no banner, so it's just links to be clicked
    else:
        # Get the peer's info
        peer_info = filter_dictionary(PEERS, "peer_id", peer_id)[0]
        if peer_info:
            name = "Your peer " + peer_info['name']
        else:
            name = "A peer"

        campaign_popup = CampaignPopup(
            ad_id,
            name,
            json.loads(urls),
            queue_item,
            file_name,
            impressions,
            peer_id,
            date_end
        )
        campaign_popup.exec()


def _queues_CMD_BAN_BLK(self, queue_item):
    from Support.Header import MESSAGE_OK, PEERS, ME, CMD_BAN_DEL, PRT_NOR, pool_main_db
    from Support.MessageBox import MessageBox
    from Support.Utils import raise_exception, ip_to_send_packet_to, query_database
    from Support.SendData import send_packet
    import Support.Header as init

    ad_id = queue_item.file['data1'][0]

    try:
        banner = query_database(pool_main_db, "SELECT * FROM Ads WHERE ad_id = ?", (ad_id,))[0]

        # If we don't find the banner, it means we've already deleted it. So we just exit
        if not banner:
            return

        strikes = banner[5]
        strikes += 1

        # The banner received 5 strikes, so we remove it
        if strikes >= 5:

            # If he has 5 strikers on two other banners, totalling 3, we ban him from posting other banners
            count_five_strikes = query_database(pool_main_db, "SELECT COUNT(*) FROM Ads WHERE strikes >= ?", (5,))[0][0]
            if count_five_strikes and len(count_five_strikes >= 2):

                # Tell the user the ad is blocked
                MessageBox.show("Banned banner", f"Five peers find this banner '{banner[1]}' offensive or "
                     f"inappropriate and have reported it. This banner campaign is blocked. But since you already have 2"
                     f"other campaigns that were blocked, you are now banned from advertising on Neuron.",
                     MESSAGE_OK)

                query_database(pool_main_db, "UPDATE SystemInfo SET ban_ads = ?", (1,))

            else:
                MessageBox.show("Banned banner", f"Five peers find this banner '{banner[1]}' offensive "
                     f"or inappropriate and have reported it. This banner campaign is blocked and you will receive "
                     f"no refund.",
                     MESSAGE_OK)

                # Save the strike
                query_database(pool_main_db, "UPDATE Ads SET strikes = ? WHERE ad_id = ?", (strikes, banner[0]))

                # Update the icon in news_ads_dialog if it's open
                if hasattr(init, 'news_ads_dialog') and init.news_ads_dialog and init.news_ads_dialog.isVisible():
                    if hasattr(init.news_ads_dialog, 'tblBannerCampaigns') and init.news_ads_dialog.tblBannerCampaigns:
                        campaign_name = banner[9]
                        blocked_img_path = os.path.join(init.PATH_SYSTEM_SUPPORT_IMAGES, "Ban.webp")
                        pixmap = QPixmap(blocked_img_path)
                        if not pixmap.isNull():
                            from PySide6.QtCore import Qt as QtCore_Qt
                            scaled_pixmap = pixmap.scaledToHeight(50, QtCore_Qt.TransformationMode.SmoothTransformation)
                            for row in range(init.news_ads_dialog.tblBannerCampaigns.rowCount()):
                                cell_item = init.news_ads_dialog.tblBannerCampaigns.item(row, 1)
                                if cell_item and cell_item.text() == campaign_name:
                                    icon_item = init.news_ads_dialog.tblBannerCampaigns.item(row, 0)
                                    if icon_item:
                                        icon_item.setData(QtCore_Qt.ItemDataRole.DecorationRole, scaled_pixmap)
                                    break

                # Tell peers to remove my campaign (async to avoid blocking)
                def send_removal_packets():
                    for peer in PEERS:
                        send_packet(ME['ip'], ME['ip_local'], '',
                                    ip_to_send_packet_to(peer['ip'], peer['ip_local']), peer['ip_local'], '',
                                    CMD_BAN_DEL, PRT_NOR, '', 0, 0, '',
                                    [banner[0]], [], [],
                                    '', '', [], [], [])
                
                import threading
                threading.Thread(target=send_removal_packets, daemon=True).start()

        # We have less than 5 strikes, so we just update the count
        else:
            from Support.Messages import NotificationBubble
            from Support.Header import SYSTEM_MESSAGE_RED, PATH_SYSTEM_SUPPORT_IMAGES, messages
            txt = NotificationBubble(self, f"A peer has reported your banner '{banner[1]}'. "
                   f"You now have {str(strikes)} strike(s). At 5, the campaign will be blocked.",
                 SYSTEM_MESSAGE_RED, os.path.join(PATH_SYSTEM_SUPPORT_IMAGES, "Strike.webp"))
            messages.append(txt)

            # Save the strike
            query_database(pool_main_db, "UPDATE Ads SET strikes = ? WHERE ad_id = ?", (strikes, ad_id,))

    except:
        raise_exception()

# A peer deletes an ad campaign
def _queues_CMD_BAN_DEL(self, queue_item):
    from Support.Utils import query_database
    from Support.Header import pool_main_db
    import Support.Header as init

    ad_id = queue_item.file['data1'][0]

    # Get the ad info
    ad_info = query_database(pool_main_db, "SELECT * FROM Ads WHERE ad_id = ?", (ad_id,))
    if not ad_info:
        return

    query_database(pool_main_db, "DELETE FROM Ads WHERE ad_id = ?", (ad_id,))

    # If the ad was a click campaign, we first have to stop the clicker
    still_active = query_database(pool_main_db,
   "SELECT * FROM Ads WHERE peer_id <> ? AND date_end <> '' AND file_name IS NULL",
          (init.ME['id'],))
    if not ad_info[0][1] and init.click_active and not still_active:
        query_database(pool_main_db, "UPDATE SystemInfo SET click_active = ?", (0,))
        init.neuron_clicker.stop_clicker()

def _queues_CMD_PER_TYP(self, queue_item):
    from Support.Dictionaries import set_dictionary
    from Support.Header import PEERS

    peer_id = queue_item.file['data1'][0]
    new_node_type = queue_item.file['data1'][1]

    # Change te peer's node type
    set_dictionary(PEERS,
                   search_conditions={'peer_id': peer_id},
                   updates={'type': new_node_type})


def _queues_CMD_SRV_SQR(self, queue_item):
    from Support.Utils import query_database, raise_exception
    from Support.Header import pool_admin_db
    from datetime import datetime
    import json

    print(f"[CMD_SRV_SQR] Function called - processing SQL result")
    print(f"[CMD_SRV_SQR] Queue item file data1: {queue_item.file.get('data1')}")

    if not queue_item.file.get('data1'):
        print("[CMD_SRV_SQR] Empty SQL result")
        return

    try:
        # The data comes as JSON string from peer, need to parse it first
        data = queue_item.file['data1']
        print(f"[CMD_SRV_SQR] Raw data received: {type(data)} - {str(data)[:200]}...")
        
        # Handle different data formats
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], str):
            # Data is a list containing a JSON string: ['[...]']
            json_string = data[0]
            parsed_data = json.loads(json_string)
        elif isinstance(data, str):
            # Data is directly a JSON string
            parsed_data = json.loads(data)
        else:
            # Data is already parsed
            parsed_data = data
        
        # Extract rows from parsed data
        # Check if data is in [columns, rows] format or just rows
        if isinstance(parsed_data, dict):
            # Error / envelope payload from peer SQL handler
            rows = parsed_data.get('rows') or []
            if parsed_data.get('error'):
                rows = [["error", str(parsed_data.get('error'))]] + (
                    rows if isinstance(rows, list) else []
                )
        elif (isinstance(parsed_data, list) and len(parsed_data) >= 2 and 
            isinstance(parsed_data[0], list) and isinstance(parsed_data[1], list) and
            len(parsed_data[0]) != len(parsed_data[1])):
            # Data is in [columns, rows] format - columns and rows have different lengths
            rows = parsed_data[1]
        else:
            # Data is just rows directly
            rows = parsed_data if isinstance(parsed_data, list) else []
        
        # Count columns from first row
        column_count = len(rows[0]) if rows and len(rows) > 0 else 0
        
        print(f"[CMD_SRV_SQR] Processed SQL result: {len(rows)} rows, {column_count} columns")
        
    except Exception as e:
        print(f"[CMD_SRV_SQR] Error parsing SQL result data: {e}")
        raise_exception()
        return

    # Store results in Admin.db for ModuleDeployer to access
    try:
        peer_ip = queue_item.src.get('ip', 'Unknown')
        peer_ip_local = queue_item.src.get('ip_local', 'Unknown')
        peer_id = str(
            (queue_item.header or {}).get('src_peer_id')
            or queue_item.src.get('peer_id')
            or ''
        )
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Store rows as JSON (avoid double-encoding)
        rows_json = json.dumps(rows)
        
        # Ensure SqlResults table exists with correct schema
        query_database(pool_admin_db, '''CREATE TABLE IF NOT EXISTS SqlResults (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            peer_ip TEXT,
            peer_ip_local TEXT,
            peer_id TEXT,
            timestamp TEXT,
            rows TEXT,
            processed INTEGER DEFAULT 0
        )''', (), commit=True)
        
        # Insert the result
        query_database(pool_admin_db, '''INSERT INTO SqlResults 
                         (peer_ip, peer_ip_local, peer_id, timestamp, rows, processed)
                         VALUES (?, ?, ?, ?, ?, 0)''',
                      (peer_ip, peer_ip_local, peer_id, timestamp, rows_json), commit=True)
        
        print(f"[CMD_SRV_SQR] Stored SQL result in Admin.db from peer {peer_ip}")
        
    except:
        raise_exception()

def _queues_CMD_SRV_MOD(self, queue_item):
    from Support.Header import MESSAGE_CRITICAL, MAIN_PATH, pool_main_db
    from Support.Utils import get_timestamp, raise_exception, query_database
    from Support.MessageBox import MessageBox
    from Support.ModuleSecurity import verify_trusted_signature, backup_module, calculate_crc
    import shutil

    try:
        # Extract data according to Mo string list
        # data2: module_info as flattened string
        # data1: deployment_info as list
        # data3: binary_data_list as byte list
        deployment_info = queue_item.file.get('data1', [])
        module_info_flat = queue_item.file.get('data2', [])
        binary_data_list = queue_item.file.get('data3', [])

        if not deployment_info:
            print(
                f"[QUEUES] Error: No deployment info received in data1 for CMD_SRV_MOD from {queue_item.src['ip']}")
            _reply_module_deploy(queue_item, '', False, "no deployment info in data1")
            return

        # Parse deployment info from data1
        # Format v3.0: [deployment_id, admin_ip, deployment_time, total_modules, version, description, format_version, signature]
        if len(deployment_info) < 7:
            print(
                f"[QUEUES] Error: Invalid deployment info format - expected at least 7 fields, got {len(deployment_info)}")
            _reply_module_deploy(
                queue_item, str(deployment_info[0] or '') if deployment_info else '',
                False, f"invalid deployment info ({len(deployment_info)} fields)")
            return

        deployment_id = deployment_info[0]

        if not module_info_flat:
            print(f"[QUEUES] Error: No module info received in data2 D from {queue_item.src['ip']}")
            _reply_module_deploy(queue_item, deployment_id, False, "no module info in data2")
            return

        if not binary_data_list:
            print(f"[QUEUES] Error: No binary data received in data3 for CMD_SRV_MOD from {queue_item.src['ip']}")
            _reply_module_deploy(queue_item, deployment_id, False, "no binary data in data3")
            return
        admin_ip = deployment_info[1]
        deployment_time = deployment_info[2]
        total_modules = int(deployment_info[3])
        version = deployment_info[4]
        description = deployment_info[5]
        format_version = deployment_info[6]

        # Check for signature (format version 3.0+)
        # Signature is at index 7 in deployment_info list
        trusted_signature = deployment_info[7] if len(deployment_info) > 7 else None
        # Optional force flag (index 10) from AdminConsole "Force update" checkbox
        force_update = False
        if len(deployment_info) > 10:
            force_update = str(deployment_info[10]).strip().lower() in ('1', 'true', 'yes')

        print(f"[MODULE_UPDATE] Received module bundle format version: {format_version}")
        print(f"[MODULE_UPDATE] Deployment ID: {deployment_id} from {admin_ip}")
        print(f"[MODULE_UPDATE] Force update: {force_update}")
        if trusted_signature:
            print(f"[MODULE_UPDATE] Signature received: {trusted_signature}")
        else:
            print(f"[MODULE_UPDATE] Signature received: None")

        # Verify trusted sender signature (legacy digit pattern). Packet-level Ed25519
        # admin_sig is already enforced in Queues.manage_queue_item before we get here.
        if trusted_signature:
            if verify_trusted_signature(trusted_signature):
                print(f"[MODULE_UPDATE] Trusted sender signature verified: {trusted_signature[:10]}...")
            else:
                print(f"[MODULE_UPDATE] SECURITY WARNING: Invalid sender signature from {admin_ip}")
                print(f"[MODULE_UPDATE] Rejecting deployment - untrusted sender")
                _reply_module_deploy(
                    queue_item, deployment_id, False, "untrusted sender signature")
                return
        else:
            print(f"[MODULE_UPDATE] No legacy digit signature (Ed25519 admin_sig already verified)")

        # Parse module info from data2 (flattened list - 12 fields per module for v3.0)
        # Format per module: [name, sub_folder, deploy_date, version, pending, folder, 
        #                     deployment_id, install_date, checksum, size, file_extension, is_compiled]
        fields_per_module = 12 if format_version >= "3.0" else 8

        if len(module_info_flat) % fields_per_module != 0:
            print(f"[QUEUES] Error: Invalid module info format - expected multiple of {fields_per_module} fields, got {len(module_info_flat)}")
            _reply_module_deploy(
                queue_item, deployment_id, False,
                f"invalid module info length {len(module_info_flat)}")
            return

        modules = []
        if format_version >= "3.0":
            # New format with all Modules table fields
            for i in range(0, len(module_info_flat), 12):
                module = {
                    'name': module_info_flat[i],
                    'sub_folder': module_info_flat[i + 1],
                    'deploy_date': module_info_flat[i + 2],
                    'version': module_info_flat[i + 3],
                    'pending': module_info_flat[i + 4],
                    'folder': module_info_flat[i + 5],
                    'deployment_id': module_info_flat[i + 6],
                    'install_date': module_info_flat[i + 7],
                    'checksum': module_info_flat[i + 8],
                    'size': int(module_info_flat[i + 9]),
                    'file_extension': module_info_flat[i + 10],
                    'is_compiled': module_info_flat[i + 11].lower() == 'true'
                }
                modules.append(module)
        else:
            # Legacy format
            for i in range(0, len(module_info_flat), 8):
                module = {
                    'name': module_info_flat[i],
                    'folder': module_info_flat[i + 1],
                    'sub_folder': module_info_flat[i + 1],
                    'version': module_info_flat[i + 2],
                    'checksum': module_info_flat[i + 3],
                    'size': int(module_info_flat[i + 4]),
                    'deploy_date': module_info_flat[i + 5],
                    'file_extension': module_info_flat[i + 6],
                    'is_compiled': module_info_flat[i + 7].lower() == 'true'
                }
                modules.append(module)

        if not modules:
            print("[MODULE_UPDATE] No modules found in bundle")
            _reply_module_deploy(queue_item, deployment_id, False, "no modules in bundle")
            return

        # Verify we have the right number of binary data items
        if len(modules) != len(binary_data_list):
            print(f"[QUEUES] Error: Module count mismatch - metadata has {len(modules)} modules, but received {len(binary_data_list)} binary data items")
            _reply_module_deploy(
                queue_item, deployment_id, False,
                f"module/binary count mismatch ({len(modules)} vs {len(binary_data_list)})"
            )
            return

        if len(modules) != total_modules:
            # Normal for split deploys: one .pyd per packet, total_modules = batch size.
            if total_modules > 1 and len(modules) == 1:
                print(
                    f"[MODULE_UPDATE] Batch piece for {deployment_id}: "
                    f"1 module in packet, batch expects {total_modules}"
                )
            else:
                print(
                    f"[QUEUES] Warning: Module count mismatch - deployment info says "
                    f"{total_modules}, but parsed {len(modules)} modules"
                )

        print(f"[MODULE_UPDATE] Processing {len(modules)} modules from deployment {deployment_id}")

        successful_updates = []
        failed_updates = []
        skipped_updates = []
        requires_restart = False

        # Ensure backup and old folders exist
        backup_folder = os.path.join(MAIN_PATH, "System", "Bak")
        old_folder = os.path.join(MAIN_PATH, "System", "Old")
        os.makedirs(backup_folder, exist_ok=True)
        os.makedirs(old_folder, exist_ok=True)

        for i, module_info in enumerate(modules):

            try:
                module_name = module_info['name']
                module_folder = module_info.get('sub_folder', module_info.get('folder', ''))
                module_version = module_info.get('version', '1.0.0')
                module_data = binary_data_list[i]
                module_checksum = module_info.get('checksum', '')
                file_extension = module_info.get('file_extension', '.pyd')
                is_compiled = module_info.get('is_compiled', True)

                print(f"[MODULE_UPDATE] Processing module: {module_name} v{module_version}")

                # Verify checksum if provided
                if module_checksum:
                    calculated_checksum = calculate_crc(module_data)
                    if calculated_checksum != module_checksum:
                        print(f"[MODULE_UPDATE] Checksum mismatch for {module_name}")
                        print(f"[MODULE_UPDATE] Expected: {module_checksum}")
                        print(f"[MODULE_UPDATE] Calculated: {calculated_checksum}")
                        failed_updates.append(module_name)
                        continue
                    else:
                        print(f"[MODULE_UPDATE] [OK] Checksum verified for {module_name}")

                # Check if module exists in database
                existing_module = query_database(pool_main_db,
                                                 "SELECT name, sub_folder, version, pending FROM Modules WHERE name = ?",
                                                 (module_name,))

                should_update = False

                if not existing_module:
                    # New module - add to database with all fields
                    print(f"[MODULE_UPDATE] Adding new module: {module_name}")
                    query_database(pool_main_db,
                                   """INSERT INTO Modules (name, sub_folder, deploy_date, version, pending, 
                           folder, deployment_id, install_date, checksum, size) 
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                   (module_name, module_folder, get_timestamp(), module_version, 1,
                                    '', deployment_id, '', module_checksum, module_info['size']))
                    should_update = True
                else:
                    # Existing module - check version
                    existing_version = existing_module[0][2] if existing_module[0][2] else "0.0.0"

                    try:
                        # Parse versions for comparison
                        old_parts = [int(x) for x in existing_version.split('.')]
                        new_parts = [int(x) for x in module_version.split('.')]

                        # Pad shorter version with zeros
                        max_len = max(len(old_parts), len(new_parts))
                        old_parts.extend([0] * (max_len - len(old_parts)))
                        new_parts.extend([0] * (max_len - len(new_parts)))

                        # Compare versions (force update ignores and always stages)
                        if force_update or new_parts > old_parts:
                            print(
                                f"[MODULE_UPDATE] Updating {module_name} from v{existing_version} to v{module_version}"
                                + (" (forced)" if force_update else ""))
                            should_update = True
                        else:
                            print(
                                f"[MODULE_UPDATE] Skipping {module_name} - version {module_version} not newer than {existing_version}")
                            skipped_updates.append(
                                f"{module_name} (have {existing_version}, got {module_version})"
                            )

                    except ValueError as e:
                        print(f"[MODULE_UPDATE] Version comparison failed for {module_name}: {e}")
                        # If version comparison fails, update anyway
                        should_update = True

                if should_update:
                    # Determine file paths
                    folder_path = ""
                    if module_folder:
                        folder_path = module_folder + "\\"
                        # Ensure folder exists
                        full_folder_path = os.path.join(MAIN_PATH, module_folder)
                        os.makedirs(full_folder_path, exist_ok=True)

                    # Determine current module path (for backup)
                    current_module_path = None
                    for ext in ['.pyd', '.py']:
                        test_path = os.path.join(MAIN_PATH, folder_path + module_name + ext)
                        if os.path.exists(test_path):
                            current_module_path = test_path
                            break

                    # Backup existing module to System/Old folder
                    if current_module_path and os.path.exists(current_module_path):
                        old_module_name = f"{module_name}_{get_timestamp()}{os.path.splitext(current_module_path)[1]}"
                        old_module_path = os.path.join(old_folder, old_module_name)
                        try:
                            shutil.copy2(current_module_path, old_module_path)
                            print(f"[MODULE_UPDATE] Backed up old module to: {old_module_path}")
                        except Exception as e:
                            print(f"[MODULE_UPDATE] Warning: Could not backup old module: {e}")

                    # Create encrypted backup in System/Bak folder
                    pending_file_path = os.path.join(MAIN_PATH, folder_path + module_name + '.pending')

                    # Wipe tagged .cp312-*.pyd siblings so restart cannot keep loading them
                    try:
                        from Support.ModuleSecurity import remove_stale_module_binaries
                        _mod_dir = os.path.join(MAIN_PATH, module_folder) if module_folder else MAIN_PATH
                        remove_stale_module_binaries(
                            _mod_dir, module_name,
                            keep_path=current_module_path if current_module_path else None)
                    except Exception:
                        pass

                    # Write module data to pending file first
                    with open(pending_file_path, 'wb') as f:
                        f.write(module_data)

                    print(f"[MODULE_UPDATE] Saved pending module: {pending_file_path}")

                    # Create encrypted backup
                    try:
                        success, encrypted_name = backup_module(pending_file_path, backup_folder)
                        if success:
                            print(f"[MODULE_UPDATE] [OK] Created encrypted backup: {encrypted_name[:30]}...")
                        else:
                            print(f"[MODULE_UPDATE] Warning: Could not create encrypted backup")
                    except Exception as e:
                        print(f"[MODULE_UPDATE] Warning: Backup creation failed: {e}")

                    # Update database with all fields
                    if existing_module:
                        sql = """
                            UPDATE Modules
                            SET version = ?, pending = 1, deploy_date = ?, deployment_id = ?,
                                checksum = ?, size = ?, sub_folder = ?
                            WHERE name = ?
                        """
                        query_database(pool_main_db, sql,
                                       (module_version, get_timestamp(), deployment_id,
                                        module_checksum, module_info['size'], module_folder, module_name))
                    else:
                        # Update the newly inserted record with deployment_id
                        sql = """
                            UPDATE Modules
                            SET deployment_id = ?, checksum = ?, size = ?
                            WHERE name = ?
                        """
                        query_database(pool_main_db, sql,
                                       (deployment_id, module_checksum, module_info['size'], module_name))

                    successful_updates.append(module_name)
                    requires_restart = True

            except Exception as e:
                print(f"[MODULE_UPDATE] Failed to process module {module_info.get('name', 'unknown')}: {e}")
                import traceback
                traceback.print_exc()
                failed_updates.append(module_info.get('name', 'unknown'))

        # Log results
        if successful_updates:
            print(
                f"[MODULE_UPDATE] Successfully staged {len(successful_updates)} modules: {', '.join(successful_updates)}")

        if failed_updates:
            print(f"[MODULE_UPDATE] Failed to stage {len(failed_updates)} modules: {', '.join(failed_updates)}")
        if skipped_updates:
            print(f"[MODULE_UPDATE] Skipped (not newer): {', '.join(skipped_updates)}")

        # ACK/NAK admin FIRST (before any restart UI/quit) so Deployer does not
        # hit the 180s NAK timeout while the peer is shutting down or flapping.
        hot_applied = []
        still_need_restart = []
        if successful_updates:
            try:
                hot_applied = _try_hot_apply_satellite_pending(successful_updates) or []
            except Exception:
                hot_applied = []
            still_need_restart = [
                m for m in successful_updates if m not in hot_applied
            ]
            msg = f"staged {len(successful_updates)}: {', '.join(successful_updates)}"
            if hot_applied:
                msg += f"; hot-applied {', '.join(hot_applied)}"
            if skipped_updates:
                msg += f"; skipped {len(skipped_updates)}"
            if failed_updates:
                msg += f"; failed {len(failed_updates)}"
            _reply_module_deploy(queue_item, deployment_id, True, msg)
        else:
            parts = []
            if skipped_updates:
                parts.append("nothing newer (enable Force update): " + "; ".join(skipped_updates[:4]))
            if failed_updates:
                parts.append("failed: " + ", ".join(failed_updates[:4]))
            if not parts:
                parts.append("no modules staged")
            _reply_module_deploy(queue_item, deployment_id, False, " | ".join(parts))

        # Defer restart dialog until the full multi-packet batch is in.
        # Count every module slot in this packet (staged/skipped/failed) so a
        # mid-batch skip cannot leave the peer waiting forever.
        try:
            expected = max(1, int(total_modules or 1))
        except (TypeError, ValueError):
            expected = 1
        piece_count = max(1, len(modules))
        batch_complete = True
        should_restart = bool(still_need_restart)
        if expected > 1:
            with _mod_deploy_batch_lock:
                st = _mod_deploy_batch.get(deployment_id)
                if not st:
                    st = {
                        'expected': expected,
                        'received': 0,
                        'need_restart': False,
                    }
                    _mod_deploy_batch[deployment_id] = st
                else:
                    # Prefer the larger declared total if packets disagree.
                    try:
                        st['expected'] = max(int(st.get('expected') or 1), expected)
                    except (TypeError, ValueError):
                        st['expected'] = expected
                st['received'] = int(st.get('received') or 0) + piece_count
                st['need_restart'] = bool(st.get('need_restart')) or bool(still_need_restart)
                batch_complete = st['received'] >= int(st.get('expected') or expected)
                should_restart = bool(st.get('need_restart'))
                print(
                    f"[MODULE_UPDATE] Batch {deployment_id}: "
                    f"{st['received']}/{st['expected']} received"
                    + (", complete" if batch_complete else ", waiting for more")
                )
                if batch_complete:
                    _mod_deploy_batch.pop(deployment_id, None)
        if batch_complete and should_restart:
            from Support.ModuleSecurity import _notify_release_restart
            _notify_release_restart(force_countdown=True)

    except Exception as e:
        try:
            _reply_module_deploy(queue_item, deployment_id, False, f"exception: {e}")
        except Exception:
            pass
        raise_exception()


def _queues_CMD_SRV_USA(self, queue_item):
    import sqlite3
    import os
    from Support.Header import pool_admin_db, PATH_SQLITE, PATH_DATABASE

    try:
        print("[CMD_SRV_USA] CODE_VERSION=2026-07-22-sqlite-direct")
        print(f"[CMD_SRV_USA] Handler called, file keys: {queue_item.file.keys()}")
        data1 = queue_item.file.get('data1', [])
        print(f"[CMD_SRV_USA] data1: {data1}, length: {len(data1)}")
        
        if len(data1) < 3:
            print(f"[CMD_SRV_USA] Not enough data, need 3+ items")
            return
        
        peer_ip = str(queue_item.src['ip'])
        peer_ip_local = str(queue_item.src['ip_local'])
        try:
            peer_type = int(float(data1[0])) if data1[0] is not None and data1[0] != '' else 0
        except (ValueError, TypeError):
            peer_type = 0
        try:
            neurons_val = float(data1[1]) if data1[1] is not None and data1[1] != '' else 0.0
        except (ValueError, TypeError):
            neurons_val = 0.0
        try:
            space_val = int(float(data1[2])) if data1[2] is not None and data1[2] != '' else 0
        except (ValueError, TypeError):
            space_val = 0
        country_code = str(data1[3]) if len(data1) > 3 and data1[3] is not None else 'XX'
        release_ver = str(data1[4]) if len(data1) > 4 and data1[4] is not None else ''
        
        db_path = pool_admin_db.database_file if pool_admin_db else os.path.join(PATH_SQLITE, "Admin.db")
        print(f"[CMD_SRV_USA] Received from {peer_ip}:{peer_ip_local} - Type:{peer_type}, Neurons:{neurons_val}, Space:{space_val}, Country:{country_code}, Version:{release_ver}")
        print(f"[CMD_SRV_USA] PATH_SQLITE={PATH_SQLITE}")
        print(f"[CMD_SRV_USA] Writing to {db_path}")

        conn = sqlite3.connect(db_path, timeout=30)
        try:
            cur = conn.cursor()
            cur.execute("SELECT sql FROM sqlite_master WHERE name='Global'")
            print(f"[CMD_SRV_USA] Global DDL: {cur.fetchone()}")
            cur.execute(
                "DELETE FROM Global WHERE ip = ? AND ip_local = ?",
                (peer_ip, peer_ip_local)
            )
            cur.execute(
                "INSERT INTO Global (ip, ip_local, type, neurons, storage, country_code) VALUES (?, ?, ?, ?, ?, ?)",
                (peer_ip, peer_ip_local, peer_type, neurons_val, space_val, country_code)
            )
            conn.commit()
            cur.execute("SELECT COUNT(*) FROM Global")
            print(f"[CMD_SRV_USA] Saved row for {peer_ip}:{peer_ip_local}; Global count={cur.fetchone()[0]}")
        finally:
            conn.close()

        if release_ver:
            try:
                from Support.TowerDeploy import remember_peer_release_version
                remember_peer_release_version(peer_ip, peer_ip_local, release_ver)
            except Exception as ve:
                print(f"[CMD_SRV_USA] version cache warn: {ve}")
        
    except Exception as e:
        print(f"[CMD_SRV_USA] Exception: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


def _queues_CMD_SRV_HLP(self, queue_item):
    from Support.Header import PATH_ADMIN
    from Support.Utils import get_timestamp

    # Save error log file and error description
    with open(PATH_ADMIN +
              queue_item.src['ip'] + "_" +
              queue_item.src['ip_local'] + "_" +
              get_timestamp() + ".txt", "wb") as f:
        f.write(queue_item.file['data3'][0] + "\n\n" + queue_item.file['name'])


class CampaignPopup:
    def __init__(self, ad_id, peer_name, urls, queue_item, file_name, impressions, peer_id, date_end):
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QPushButton
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QIcon
        from PySide6.QtCore import Qt
        from Support.GlobalStyle import main_dialog_style
        import os
        import Support.Header as init
        
        self.dialog = QDialog()
        self.ad_id = ad_id
        self.peer_name = peer_name
        self.urls = urls
        self.queue_item = queue_item
        self.file_name = file_name
        self.impressions = impressions
        self.peer_id = peer_id
        self.date_end = date_end
        self.time_remaining = 120
        self.selected_action = None

        self.dialog.setWindowTitle("New Clicker Campaign")
        self.dialog.setWindowIcon(QIcon(os.path.join(init.PATH_SYSTEM_SUPPORT_IMAGES, "Neuron.png")))
        self.dialog.setMinimumSize(600, 500)
        self.dialog.setMaximumWidth(700)
        self.dialog.setStyleSheet(main_dialog_style)
        
        main_layout = QVBoxLayout()
        
        countdown_label = QLabel(f"Time remaining: {self.time_remaining}s")
        countdown_label.setStyleSheet("font-weight: bold; color: red;")
        main_layout.addWidget(countdown_label)
        
        message_text = (
            f"{peer_name} invites you to click on one or more websites to earn Neurons. Clicking will continue until midnight on {date_end}."
            ""
            "If you click ACCEPT, you will visit the links below with no disruption to your work. "
            "If you click DECLINE you'll ignore this invitation. "
            "If you feel these links are offensive or inappropriate, you may REPORT them."
            ""
            "You have 2 minutes to look over the websites and decide. When the timer runs out, if you make no choice, the offer will be automatically declined."
        )
        message_label = QLabel(message_text)
        message_label.setWordWrap(True)
        main_layout.addWidget(message_label)
        
        urls_label = QLabel("Links to visit:")
        main_layout.addWidget(urls_label)
        
        urls_list = QListWidget()
        for url in urls:
            urls_list.addItem(url)
        urls_list.itemClicked.connect(lambda item: self._show_preview(item, urls_list))
        main_layout.addWidget(urls_list)
        
        preview_label = QLabel("Preview:")
        main_layout.addWidget(preview_label)
        
        self.preview_display = QLabel()
        self.preview_display.setMinimumHeight(150)
        self.preview_display.setStyleSheet("border: 1px solid #3a4366; padding: 5px;")
        self.preview_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.preview_display)
        
        buttons_layout = QHBoxLayout()
        
        btn_accept = QPushButton("ACCEPT")
        btn_accept.clicked.connect(lambda: self._button_pressed("accept", countdown_label))
        buttons_layout.addWidget(btn_accept)
        
        btn_decline = QPushButton("DECLINE")
        btn_decline.clicked.connect(lambda: self._button_pressed("decline", countdown_label))
        buttons_layout.addWidget(btn_decline)
        
        btn_report = QPushButton("REPORT")
        btn_report.clicked.connect(lambda: self._button_pressed("report", countdown_label))
        buttons_layout.addWidget(btn_report)
        
        main_layout.addLayout(buttons_layout)
        
        self.dialog.setLayout(main_layout)

        self.timer = QTimer()
        self.timer.timeout.connect(lambda: self._update_countdown(countdown_label))
        self.timer.start(1000)
    
    def _parse_urls(self, link):
        if isinstance(link, list):
            return link
        if isinstance(link, str):
            urls = [url.strip() for url in link.split('\n') if url.strip()]
            if len(urls) == 1 and ';' in link:
                urls = [url.strip() for url in link.split(';') if url.strip()]
            if len(urls) == 1 and ',' in link:
                urls = [url.strip() for url in link.split(',') if url.strip()]
            return urls if urls else [link]
        return [link] if link else []
    
    def _show_preview(self, item, urls_list):
        import requests
        from bs4 import BeautifulSoup
        
        url = item.text()
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            html = requests.get(url, headers=headers, timeout=5).text
            soup = BeautifulSoup(html, "html.parser")
            
            tag = soup.find("meta", property="og:image")
            if not tag or not tag.get("content"):
                self.preview_display.setText("No preview available")
                return
            
            img_url = tag["content"]
            img_data = requests.get(img_url, headers=headers).content
            
            from PySide6.QtGui import QPixmap
            from PySide6.QtCore import Qt
            pixmap = QPixmap()
            pixmap.loadFromData(img_data)
            
            self.preview_display.setPixmap(
                pixmap.scaled(
                    self.preview_display.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            )
        except Exception as e:
            self.preview_display.setText("Preview error")
    
    def _update_countdown(self, label):
        self.time_remaining -= 1
        label.setText(f"Time remaining: {self.time_remaining}s")
        
        if self.time_remaining <= 0:
            self.timer.stop()
            label.setText("Time expired!")
    
    def _button_pressed(self, action, countdown_label):
        self.timer.stop()
        self.selected_action = action
        
        from PySide6.QtWidgets import QMessageBox
        
        msg = QMessageBox(self.dialog)
        msg.setWindowTitle("Confirm Action")
        msg.setText(f"Are you sure you want to {action.upper()} this campaign?")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if msg.exec() == QMessageBox.StandardButton.Yes:
            self._handle_action(action)
            self.dialog.accept()
        else:
            self.timer.start(1000)
    
    def _handle_action(self, action):
        if action == "accept":
            self._on_accept()
        elif action == "decline":
            self._on_decline()
        elif action == "report":
            self._on_report()
    
    def _on_accept(self):
        from Support.Utils import query_database
        from Support.Header import pool_main_db
        from Support.Subprocesses import restart_neuron_clicker
        import json
        
        query_database(pool_main_db,
                       "INSERT INTO Ads (ad_id, link, peer_id, date_end) VALUES (?, ?, ?, ?)",
                       (self.ad_id, json.dumps(self.urls), self.peer_id, self.date_end))
        
        query_database(pool_main_db, "UPDATE SystemInfo SET click_active = ?", (1,))
        restart_neuron_clicker()
    
    def _on_decline(self):
        from Support.Header import ME, CMD_BAN_NAK, PRT_NOR
        from Support.SendData import send_packet
        from Support.Utils import ip_to_send_packet_to

        req_ip = self.queue_item.src['ip']
        req_ip_local = self.queue_item.src['ip_local']
        send_packet(
            ME['ip'], ME['ip_local'], '',
            ip_to_send_packet_to(req_ip, req_ip_local), req_ip_local, '',
            CMD_BAN_NAK, PRT_NOR, '', 0, 0, '',
            self.urls, [], [],
            '', '', [], [], [])
    
    def _on_report(self):
        pass
    
    def exec(self):
        return self.dialog.exec()
