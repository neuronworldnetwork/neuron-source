"""
TimedActivities - Separated sync and async timer operations for performance optimization
Handles the 5-second timer tick that was blocking the main thread.

Operations are split into:
- Synchronous (main thread): state updates, UI changes, critical checks
- Asynchronous (background thread): database operations, disk I/O, expensive computations
"""

import json
import random
import os
import sys
import subprocess
from datetime import datetime

import Support.Header as init
from Support.Utils import (
    query_database, 
    get_timestamp_in_seconds, 
    ip_to_send_packet_to, 
    raise_exception
)
from Support.Header import pool_main_db
from Support.SendData import send_packet
from Support.Dictionaries import load_dictionaries, save_data
from Support.Messages import NotificationBubble
from Support.Misc import (
    check_new_devices, 
    check_storage_space, 
    try_to_delete_peer,
    heal_peer_chunks, 
    set_reliability, 
    update_peer_status,
    load_desktop_image,
    backup_system,
    start_queue_maintenance_thread,
    update_neurons,
    peer_ping_watchdog,
    PING_HEALTH_WARNING_THRESHOLD,
    PING_HEALTH_VERBOSE_LOGGING,
    _is_protected_bootstrap_peer,
)


def timed_activities_sync(main_window_self):
    """
    SYNCHRONOUS timed activities - runs on main thread every 5 seconds
    Contains only quick operations: UI updates, state changes, taskbar updates
    Heavy DB operations are now offloaded to background thread (ASYNC)
    """
    if getattr(init, 'ending_services', False):
        return
    try:
        # save_data is heavy SQLite — never on the UI thread (freezes fade / timers).
        # Background worker already runs save_dictionaries via timed_activities_async.
        timed_activities_async()

        # Check for mobile device connection signals (from Flask server)
        _monitor_mobile_device_signals(main_window_self)

        # Refresh neuronize bars while Resident parks/sends chunks out-of-process
        try:
            from Support.Files import sync_neuronize_desktop_progress
            sync_neuronize_desktop_progress()
        except Exception:
            pass

        # Device approval UI must stay on the main thread (never in TIMED_ACTIVITY_ASYNC).
        check_new_devices()
        
        # Keep LEDs in sync with SystemInfo pings (HiveService may start long after boot)
        _check_and_update_resident_apps(main_window_self)
        
        # ASYNC: Check calendar alarms (moved to background - no need for main thread blocking)
        _check_calendar_alarms_async()
        
        # Peer online/offline on the UI thread (async path never applied the result)
        _update_peer_alive_status(main_window_self)
        
        # EVERY 30 SECONDS - Change desktop background if random is enabled
        _update_desktop_background(main_window_self)

        # EVERY ~30 SECONDS - Peer chunk integrity (tamper grace / incompatible)
        if init.main_timer_counter % 6 == 0:
            try:
                from Support.Misc import check_peer_chunk_integrity
                check_peer_chunk_integrity()
            except Exception as e:
                print(f"[ERROR] check_peer_chunk_integrity: {e}")
        
        # EVERY 25 SECONDS - Maintenance tasks counter (backup_system moved to async)
        if init.main_timer_counter % 5 == 0:
            _handle_maintenance_counter(main_window_self)
        
        # EVERY 2 MINUTES - Show banner
        if init.main_timer_counter % 24 == 0:
            try:
                init.banner.banner_show()
            except Exception as e:
                print(f"[ERROR] Error showing banner: {e}")
        
        # AT 90 SECONDS - Process clicker activity
        if init.main_timer_counter == 18 and init.click_active:
            _process_clicker_activity(main_window_self)
        
        # AT 120 SECONDS - Lottery and periodic tasks
        if init.main_timer_counter == 19:
            _handle_120_second_tasks(main_window_self)
        
        # EVERY 2 MINUTES - Queue maintenance
        if init.main_timer_counter % 24 == 0:
            start_queue_maintenance_thread()

        # EVERY CYCLE - Check expired votes and send tallies
        if init.main_timer_counter == 20:
            from Support.NeuronVoting import check_expired_votes
            check_expired_votes()
        
        # EVERY 15 MINUTES - Public media engagement P2P sync
        if init.main_timer_counter % 180 == 0:
            try:
                from Support.PublicMediaEngagement import run_engagement_sync_cycle
                run_engagement_sync_cycle()
            except Exception as e:
                print(f"[ERROR] public media engagement sync: {e}")

        # Increment counter
        init.main_timer_counter += 1
        if init.main_timer_counter == 25:
            init.main_timer_counter = 0
    
    except Exception as e:
        print(f"[ERROR] Critical error in timed_activities_sync: {e}")
        import traceback
        traceback.print_exc()


def timed_activities_async():
    """
    ASYNCHRONOUS timed activities - queued to background thread
    Contains expensive operations: database writes, disk I/O, complex queries
    Should be called periodically but not blocking the main thread
    """
    try:
        from Support.BackgroundTaskWorker import add_background_task
        
        # Queue async tasks to background worker
        add_background_task("TIMED_ACTIVITY_ASYNC", {
            "operations": [
                "reload_peers_data",
                "reload_me_dictionary", 
                "save_dictionaries",
                "check_storage_space",
                "peer_status_checks"
            ]
        })
    except Exception as e:
        print(f"[ERROR] Error queuing async timed activities: {e}")


def _read_signal_json(path):
    """Return dict from a signal JSON file, or None if empty/corrupt."""
    try:
        if not path or not os.path.exists(path):
            return None
        if os.path.getsize(path) <= 0:
            return None
        with open(path, 'r', encoding='utf-8') as f:
            raw = f.read().strip()
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _monitor_mobile_device_signals(main_window_self):
    """Monitor signal files for mobile device connections/disconnections"""
    if getattr(init, 'ending_services', False):
        return
    try:
        signal_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 
            "System", "neuron", "htdocs", "signals"
        )
        os.makedirs(signal_dir, exist_ok=True)
        
        # Check for connected signal
        connect_signal = os.path.join(signal_dir, "mobile_device_connected.json")
        if os.path.exists(connect_signal):
            try:
                data = _read_signal_json(connect_signal)
                if not data:
                    try:
                        os.remove(connect_signal)
                    except Exception:
                        pass
                else:
                    device_id = data.get('device_id', 'unknown')
                    device_name = data.get('device_name', 'Mobile Device')

                    if init.taskbar and hasattr(init.taskbar, 'set_system_icon_enabled'):
                        init.taskbar.set_system_icon_enabled(init.TASKBAR_SYSTEM_ICON_DEVICES, True)

                    try:
                        from Support.Misc import signal_mobile_device_connected
                        signal_mobile_device_connected(device_id, device_name)
                    except Exception:
                        raise_exception()

                    os.remove(connect_signal)
            except Exception:
                try:
                    os.remove(connect_signal)
                except Exception:
                    pass
        
        # Check for disconnected signal
        disconnect_signal = os.path.join(signal_dir, "mobile_device_disconnected.json")
        if os.path.exists(disconnect_signal):
            try:
                data = _read_signal_json(disconnect_signal)
                if not data:
                    try:
                        os.remove(disconnect_signal)
                    except Exception:
                        pass
                else:
                    device_id = data.get('device_id', 'unknown')
                    device_name = data.get('device_name', 'Mobile Device')

                    if init.taskbar and hasattr(init.taskbar, 'set_system_icon_enabled'):
                        init.taskbar.set_system_icon_enabled(init.TASKBAR_SYSTEM_ICON_DEVICES, False)

                    try:
                        from Support.Misc import signal_mobile_device_disconnected
                        signal_mobile_device_disconnected(device_id, device_name)
                    except Exception:
                        raise_exception()

                    os.remove(disconnect_signal)
            except Exception:
                try:
                    os.remove(disconnect_signal)
                except Exception:
                    pass
        
        # New-device signal: clear file only. check_new_devices() runs on the
        # GUI thread from timed_activities_sync (never from a worker thread).
        auth_signal = os.path.join(signal_dir, "new_device_authorization.json")
        if os.path.exists(auth_signal):
            try:
                os.remove(auth_signal)
            except Exception as cleanup_error:
                raise_exception()
    
    except Exception as e:
        raise_exception()


def _check_and_update_resident_apps(main_window_self):
    """Check if resident apps are loaded - fast query, update UI"""
    try:
        if not init.taskbar:
            return
        resident_result = query_database(pool_main_db,
                                         "SELECT ping_resident, ping_projector, ping_crawler, "
                                         "ping_indexer, ping_streaming, ping_webrtc, ping_hive, "
                                         "ping_clicker, ping_ai, ping_web FROM SystemInfo", '')
        if resident_result and len(resident_result) > 0:
            resident, projector, crawler, indexer, streaming, webrtc, hive, clicker, ai_learning, website_server = resident_result[0]

            if resident and not init.neuron_resident_started:
                txt = NotificationBubble(main_window_self, "Neuron Resident started.", init.SYSTEM_MESSAGE_GREEN,
                                        init.PATH_SYSTEM_SUPPORT_IMAGES + "Services.webp")
                init.messages.append(txt)
                txt.show_notification()
                init.neuron_resident_started = True
                init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_RESIDENT, True)
                load_dictionaries(init.DICTIONARY_ME)
            
            if crawler and not init.neuron_crawler_started:
                txt = NotificationBubble(main_window_self, "Neuron Crawler started.", init.SYSTEM_MESSAGE_GREEN,
                                        init.PATH_SYSTEM_SUPPORT_IMAGES + "Services.webp")
                init.messages.append(txt)
                txt.show_notification()
                init.neuron_crawler_started = True
                init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_CRAWLER, True)
            
            if indexer and not init.neuron_indexer_started:
                txt = NotificationBubble(main_window_self, "Neuron Indexer started.", init.SYSTEM_MESSAGE_GREEN,
                                        init.PATH_SYSTEM_SUPPORT_IMAGES + "Services.webp")
                init.messages.append(txt)
                txt.show_notification()
                init.neuron_indexer_started = True
                init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_INDEXER, True)
            elif not indexer and init.neuron_indexer_started:
                init.neuron_indexer_started = False
                if init.taskbar:
                    init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_INDEXER, False)
            
            if projector and not init.neuron_projector_started:
                txt = NotificationBubble(main_window_self, "Neuron Projector started.", init.SYSTEM_MESSAGE_GREEN,
                                        init.PATH_SYSTEM_SUPPORT_IMAGES + "Services.webp")
                init.messages.append(txt)
                txt.show_notification()
                init.neuron_projector_started = True
                init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_PROJECTOR, True)
            
            if clicker and not init.neuron_clicker_started:
                txt = NotificationBubble(main_window_self, "Neuron Clicker started.", init.SYSTEM_MESSAGE_GREEN,
                                        init.PATH_SYSTEM_SUPPORT_IMAGES + "Services.webp")
                init.messages.append(txt)
                txt.show_notification()
                init.neuron_clicker_started = True
                init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_CLICKER, True)
            elif not clicker and init.neuron_clicker_started:
                init.neuron_clicker_started = False
                if init.taskbar:
                    init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_CLICKER, False)
            
            if webrtc and not init.neuron_webrtc_started:
                txt = NotificationBubble(main_window_self, "Neuron WebRTC started.", init.SYSTEM_MESSAGE_GREEN,
                                        init.PATH_SYSTEM_SUPPORT_IMAGES + "Services.webp")
                init.messages.append(txt)
                txt.show_notification()
                init.neuron_webrtc_started = True
                init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_WEBRTC, True)
            
            if hive and not init.neuron_hive_started:
                txt = NotificationBubble(main_window_self, "Neuron Hive started.", init.SYSTEM_MESSAGE_GREEN,
                                        init.PATH_SYSTEM_SUPPORT_IMAGES + "Services.webp")
                init.messages.append(txt)
                txt.show_notification()
                init.neuron_hive_started = True
                init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_HIVE, True)
            elif hive and init.neuron_hive_started and init.taskbar:
                init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_HIVE, True)
            elif not hive and init.neuron_hive_started:
                init.neuron_hive_started = False
                if init.taskbar:
                    init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_HIVE, False)
            # Only poll HiveService after it is alive (ping_hive set). Doing this
            # earlier caused UI freezes while the HTTP status call timed out.
            if hive:
                try:
                    from Support.NeuronAIMesh import sync_requester_hive_icon
                    sync_requester_hive_icon()
                except Exception:
                    pass
            
            if streaming and not init.neuron_streaming_started:
                txt = NotificationBubble(main_window_self, "Neuron Streaming started.", init.SYSTEM_MESSAGE_GREEN,
                                        init.PATH_SYSTEM_SUPPORT_IMAGES + "Services.webp")
                init.messages.append(txt)
                txt.show_notification()
                init.neuron_streaming_started = True
                init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_STREAMING, True)
            
            if ai_learning and not init.neuron_ai_learning_started:
                txt = NotificationBubble(main_window_self, "Neuron AI Learning started.", init.SYSTEM_MESSAGE_GREEN,
                                        init.PATH_SYSTEM_SUPPORT_IMAGES + "Services.webp")
                init.messages.append(txt)
                txt.show_notification()
                init.neuron_ai_learning_started = True
                init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_AI, True)
            elif not ai_learning and init.neuron_ai_learning_started:
                init.neuron_ai_learning_started = False
                if init.taskbar:
                    init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_AI, False)
            
            if website_server and not init.neuron_website_server_started:
                txt = NotificationBubble(main_window_self, "Neuron Website Server started.", init.SYSTEM_MESSAGE_GREEN,
                                        init.PATH_SYSTEM_SUPPORT_IMAGES + "Services.webp")
                init.messages.append(txt)
                txt.show_notification()
                init.neuron_website_server_started = True
                init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_HOSTING, True)
            elif not website_server and init.neuron_website_server_started:
                init.neuron_website_server_started = False
                if init.taskbar:
                    init.taskbar.set_resident_icon_enabled(init.TASKBAR_RESIDENT_ICON_HOSTING, False)
    
    except Exception as e:
        print(f"[ERROR] Error checking resident apps: {e}")


def _peer_offline_image(peer):
    name = (peer or {}).get('image') or ''
    path = os.path.join(init.PATH_USER_IMAGES, name) if name else ''
    if path and os.path.isfile(path):
        return path
    return os.path.join(init.PATH_USER_IMAGES, 'Avatar_male.png')


def _peer_id_key(peer_id):
    return str(peer_id or '').strip()


def _load_peer_fresh_pings(now, timeout):
    """Best last_ping_in per peer from DB + poll heartbeats (str keys)."""
    fresh_map = {}
    try:
        fresh = query_database(
            pool_main_db,
            "SELECT peer_id, last_ping_in FROM Peers WHERE last_ping_in IS NOT NULL AND last_ping_in > ?",
            (now - timeout,))
        if fresh:
            for row in fresh:
                pid = _peer_id_key(row[0])
                if pid:
                    fresh_map[pid] = float(row[1] or 0)
    except Exception:
        pass
    try:
        from Support.ModuleSecurity import load_peer_poll_heartbeats
        for pid, ts in (load_peer_poll_heartbeats() or {}).items():
            key = _peer_id_key(pid)
            if not key:
                continue
            try:
                ts = float(ts)
            except (TypeError, ValueError):
                continue
            if ts > float(fresh_map.get(key) or 0):
                fresh_map[key] = ts
    except Exception:
        pass
    return fresh_map


def _peer_best_last_ping(peer, fresh_map=None):
    """Same freshness rule as save_data — memory, DB map, and heartbeats."""
    mem = 0.0
    try:
        mem = float((peer or {}).get('last_ping_in') or 0)
    except (TypeError, ValueError):
        mem = 0.0
    mapped = 0.0
    if fresh_map is not None:
        try:
            mapped = float(fresh_map.get(_peer_id_key((peer or {}).get('peer_id'))) or 0)
        except (TypeError, ValueError):
            mapped = 0.0
    return max(mem, mapped)


def _announce_peer_offline(main_window_self, peer):
    try:
        txt = NotificationBubble(
            main_window_self,
            'Peer ' + str((peer or {}).get('name') or '').upper() + " is offline.",
            init.SYSTEM_MESSAGE_ORANGE,
            _peer_offline_image(peer),
        )
        init.messages.append(txt)
        from Support.Messages import process_message_queue
        process_message_queue()
    except Exception:
        pass


def _update_peer_alive_status(main_window_self):
    """EVERY 5 SECONDS - Check one peer's alive status (critical for connectivity)"""
    try:
        if not init.PEERS or len(init.PEERS) == 0:
            return

        now = get_timestamp_in_seconds()
        timeout = float(getattr(init, 'PEER_ONLINE_MAX_SEC', 360) or 360)
        fresh_map = _load_peer_fresh_pings(now, timeout)

        # Apply fresher heartbeats into memory before any online/offline decision.
        for peer in list(init.PEERS):
            pid = _peer_id_key(peer.get('peer_id'))
            if not pid:
                continue
            best = _peer_best_last_ping(peer, fresh_map)
            if best > float(peer.get('last_ping_in') or 0):
                peer['last_ping_in'] = best
                try:
                    query_database(
                        pool_main_db,
                        "UPDATE Peers SET last_ping_in = ? WHERE peer_id = ? "
                        "AND (last_ping_in IS NULL OR last_ping_in < ?)",
                        (best, pid, best))
                except Exception:
                    pass
            if best and (now - best) < timeout:
                if not peer.get('online'):
                    update_peer_status(pid, True)
                    peer['online'] = 1

        # Stale online peers → offline (use best last_ping, same as save_data).
        for peer in list(init.PEERS):
            if not peer.get('online'):
                continue
            last = _peer_best_last_ping(peer, fresh_map)
            peer['last_ping_in'] = last
            if last and (now - last) < timeout:
                continue
            pid = _peer_id_key(peer.get('peer_id'))
            if not pid:
                continue
            still_online = update_peer_status(pid, False)
            if still_online:
                # save_data / heartbeat proved the peer is still alive — do not toast.
                continue
            _announce_peer_offline(main_window_self, peer)

        if not init.PEERS or len(init.PEERS) == 0:
            return

        now = get_timestamp_in_seconds()
        if init.check_peer_alive_index >= len(init.PEERS):
            init.check_peer_alive_index = 0
        peer = init.PEERS[init.check_peer_alive_index]
        peer_id = _peer_id_key(peer.get('peer_id'))
        if not peer_id:
            init.check_peer_alive_index = (init.check_peer_alive_index + 1) % max(1, len(init.PEERS))
            return

        # Bootstrap ADMIN must stay in Peers forever (never_pinged / idle purge).
        if _is_protected_bootstrap_peer(peer_id):
            peer_ping_watchdog.pop(peer_id, None)
            peer_ping_watchdog.pop(str(peer_id) + ':zero_since', None)
            init.check_peer_alive_index = (init.check_peer_alive_index + 1) % max(1, len(init.PEERS))
            return

        # Refresh this peer's last_ping from DB + heartbeats.
        try:
            db_row = query_database(
                pool_main_db,
                "SELECT last_ping_in FROM Peers WHERE peer_id = ?",
                (peer_id,))
            db_ping = float(db_row[0][0]) if db_row and db_row[0][0] else 0.0
        except Exception:
            db_ping = 0.0
        hb_ping = float(fresh_map.get(peer_id) or 0)
        last_ping_in = max(float(peer.get('last_ping_in') or 0), db_ping, hb_ping)
        peer['last_ping_in'] = last_ping_in
        init.PEERS[init.check_peer_alive_index]['last_ping_in'] = last_ping_in

        time_diff = now - last_ping_in if last_ping_in else 0
        deleted = False

        # ONLINE
        if last_ping_in and 0 < time_diff < init.PEER_ONLINE_MAX_SEC:
            peer_ping_watchdog.pop(peer_id, None)
            peer_ping_watchdog.pop(str(peer_id) + ':zero_since', None)
            set_reliability(main_window_self, init.check_peer_alive_index, init.RELIABILITY_STEP)
            if not peer.get('online'):
                update_peer_status(peer_id, True)

        # OFFLINE
        elif last_ping_in and init.PEER_ONLINE_MAX_SEC <= time_diff < init.PEER_OFFLINE_MAX_SEC:
            peer_ping_watchdog.pop(peer_id, None)
            if peer.get('online'):
                still_online = update_peer_status(peer_id, False)
                if not still_online:
                    _announce_peer_offline(main_window_self, peer)
            else:
                set_reliability(main_window_self, init.check_peer_alive_index, -init.RELIABILITY_STEP)

        # After a week: ask another peer for updated IP
        elif last_ping_in and init.PEER_OFFLINE_MAX_SEC <= time_diff < init.PEER_IDLE_QUERY_MAX_SEC:
            set_reliability(main_window_self, init.check_peer_alive_index, -init.RELIABILITY_STEP)
            random_peer = int(random.random() * len(init.PEERS))
            if init.PEERS[random_peer].get('online'):
                send_packet(init.ME['ip'], init.ME['ip_local'], '',
                    ip_to_send_packet_to(init.PEERS[random_peer]['ip'], init.PEERS[random_peer]['ip_local']),
                    init.PEERS[random_peer]['ip_local'], '',
                    init.CMD_PER_QRY, init.PRT_LOW, '', 0, 0, '',
                    [peer_id], [], [],
                    '', '', [], [], [], retry_policy='killable')

        # After ~7.6 days: heal chunks he held
        elif last_ping_in and init.PEER_IDLE_QUERY_MAX_SEC <= time_diff < init.PEER_IDLE_DELETE_SEC:
            try:
                from Support.ReliabilityCert import record_exchange
                record_exchange(peer_id, 'storage', False)
            except Exception:
                pass
            heal_peer_chunks(peer_id)

        # Never pinged (last_ping_in = 0) - one-shot join, never stayed online.
        elif not last_ping_in:
            zero_key = str(peer_id) + ':zero_since'
            first_seen = peer_ping_watchdog.get(zero_key)
            if not first_seen:
                peer_ping_watchdog[zero_key] = now
            elif (now - int(first_seen)) >= init.PEER_ONLINE_MAX_SEC:
                try:
                    deleted = try_to_delete_peer(
                        init.PEERS[init.check_peer_alive_index]['peer_id'],
                        init.PEERS[init.check_peer_alive_index]['image'],
                        'never_pinged')
                except Exception:
                    raise_exception()
                    deleted = False
                peer_ping_watchdog.pop(zero_key, None)

        # Over a month idle - delete when chunks are safe
        elif last_ping_in and time_diff >= init.PEER_IDLE_DELETE_SEC:
            try:
                deleted = try_to_delete_peer(
                    init.PEERS[init.check_peer_alive_index]['peer_id'],
                    init.PEERS[init.check_peer_alive_index]['image'],
                    'idle')
            except Exception:
                raise_exception()
                deleted = False

        # Update taskbar (always — even if a bubble toast threw)
        try:
            if init.taskbar:
                def _is_on(p):
                    try:
                        return int(float(p.get("online") or 0)) == 1
                    except (TypeError, ValueError):
                        return bool(p.get("online"))
                online_count = sum(1 for p in init.PEERS if _is_on(p))
                init.taskbar.set_system_icon_tooltip(
                    init.TASKBAR_SYSTEM_ONLINE_PEERS,
                    f'Total peers:&nbsp;<span style="color: red; background-color: transparent; font-weight: bold;">'
                    f'{len(init.PEERS)}</span><br>Online peers:&nbsp;<span style="color: #3cd10b; background-color: transparent; '
                    f'font-weight: bold;">{online_count}</span><br> (click to view)')
                init.taskbar.set_system_icon_enabled(
                    init.TASKBAR_SYSTEM_ONLINE_PEERS, (online_count > 0))
        except Exception:
            pass

        if not deleted:
            init.check_peer_alive_index += 1
        if init.check_peer_alive_index >= len(init.PEERS):
            init.check_peer_alive_index = 0

    except Exception:
        raise_exception()


def _update_desktop_background(main_window_self):
    """EVERY 30 SECONDS - Change background if random is enabled"""
    try:
        if init.selected_room_info:
            if init.main_timer_counter and init.main_timer_counter % 6 == 0 and init.selected_room_info['random']:
                folder = init.PATH_DESKTOP_BACKGROUNDS
                files = [
                    f for f in os.listdir(folder)
                    if os.path.isfile(os.path.join(folder, f))
                ]
                # Prefer images that will not stall the UI thread (huge GIFs).
                light = []
                for f in files:
                    path = os.path.join(folder, f)
                    try:
                        if f.lower().endswith('.gif') and os.path.getsize(path) > (8 * 1024 * 1024):
                            continue
                    except Exception:
                        pass
                    light.append(f)
                pick_from = light or files
                if pick_from:
                    load_desktop_image(main_window_self, random.choice(pick_from))
    except:
        raise_exception()


def _request_initial_peer_list():
    """Ask a kickstarter / first online / referring peer for his online peer list."""
    if not init.ME or not init.ME.get('ip'):
        return

    peer_ip = ''
    peer_ip_local = ''
    bootstrap_path = os.path.join(init.PATH_HTDOCS, 'neuron_bootstrap.json')
    try:
        with open(bootstrap_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        peer_ip = (data.get('peer_ip') or '').strip()
        peer_ip_local = (data.get('peer_ip_local') or '').strip()
    except Exception:
        pass

    if not peer_ip and init.PEERS:
        kickstarter = None
        first_online = None
        first_any = None
        my_id = init.ME.get('id')
        for peer in init.PEERS:
            if not peer.get('ip') or peer.get('peer_id') == my_id:
                continue
            if first_any is None:
                first_any = peer
            try:
                ptype = int(peer.get('type') or 0)
            except (TypeError, ValueError):
                ptype = 0
            if kickstarter is None and ptype == init.NODE_TYPE_KICKSTARTER:
                kickstarter = peer
            try:
                online = int(float(peer.get('online') or 0))
            except (TypeError, ValueError):
                online = 0
            if first_online is None and online == 1:
                first_online = peer
        chosen = kickstarter or first_online or first_any
        if chosen:
            peer_ip = chosen.get('ip') or ''
            peer_ip_local = chosen.get('ip_local') or ''

    if not peer_ip:
        return

    send_packet(init.ME['ip'], init.ME['ip_local'], '',
                ip_to_send_packet_to(peer_ip, peer_ip_local), peer_ip_local, '',
                init.CMD_PER_ADD, init.PRT_NOR, '', 0, 0, '',
                [init.ME.get('node_name') or ''], [], [],
                '', '', [], [], [], 'critical')


def _handle_maintenance_counter(main_window_self):
    """EVERY 25 SECONDS - Queue async maintenance tasks"""
    try:
        from Support.BackgroundTaskWorker import add_background_task
        
        # Queue background maintenance
        try:
            if init.main_timer_counter % 20 == 0:
                add_background_task("CLEANUP_TEMP_FILES")
            if init.main_timer_counter % 24 == 0:
                add_background_task("CALCULATE_STATISTICS")
        except:
            pass
        
        # Update backup countdown (ASYNC: queue backup to background thread)
        init.COUNTDOWN_TO_BACKUP -= 1
        if init.COUNTDOWN_TO_BACKUP == 0:
            add_background_task("BACKUP_SYSTEM_ASYNC")
            init.COUNTDOWN_TO_BACKUP = init.DAILY_MAINTENANCE_TICKS

        # EVERY DAY - Remove expired WORLD NEWS articles from local News.db
        init.COUNTDOWN_TO_NEWS_CLEANUP -= 1
        if init.COUNTDOWN_TO_NEWS_CLEANUP == 0:
            add_background_task("CLEANUP_OLD_NEWS_ARTICLES")
            init.COUNTDOWN_TO_NEWS_CLEANUP = init.DAILY_MAINTENANCE_TICKS

        init.COUNTDOWN_TO_DB_MAINTENANCE -= 1
        if init.COUNTDOWN_TO_DB_MAINTENANCE == 0:
            add_background_task("DATABASE_MAINTENANCE")
            init.COUNTDOWN_TO_DB_MAINTENANCE = init.DAILY_MAINTENANCE_TICKS

        # ~25s: Promote dropped FORCE_LOCAL_RAR_ARM.flag — arm without hourly wait
        try:
            from Support.TowerDeploy import consume_force_local_rar_arm_flag
            if consume_force_local_rar_arm_flag():
                from Support.ModuleSecurity import process_stored_releases
                from Support.MajorUpgrade import try_arm_local_neuron_rar, apply_ready_major_if_any
                process_stored_releases()
                armed, amsg = try_arm_local_neuron_rar()
                print(f"[FORCE_RAR_ARM] local Neuron.rar: {armed} {amsg}")
                ok, msg = apply_ready_major_if_any()
                if ok or msg:
                    print(f"[FORCE_RAR_ARM] major apply: {ok} {msg}")
        except Exception as e:
            print(f"[FORCE_RAR_ARM] {e}")

        # EVERY 60 MINUTES - Ask supernodes for newer module versions
        init.COUNTDOWN_TO_APP_UPDATE -= 1
        if init.COUNTDOWN_TO_APP_UPDATE == 0:
            _check_app_updates_from_supernodes()
            _check_installed_app_updates_from_supernodes()
            init.COUNTDOWN_TO_APP_UPDATE = init.APP_UPDATE_CHECK_INTERVAL

        # EVERY 60 MINUTES - Reclassify node from allocation + presence
        init.COUNTDOWN_TO_NODE_RECLASS -= 1
        if init.COUNTDOWN_TO_NODE_RECLASS == 0:
            try:
                from Support.NodeClassify import reclassify_self, check_presence_demotion
                check_presence_demotion()
                reclassify_self(reason="periodic")
            except Exception as e:
                print(f"[ERROR] Node reclass: {e}")
            init.COUNTDOWN_TO_NODE_RECLASS = init.NODE_RECLASS_INTERVAL_TICKS
        
        # Check first run: keep asking the referring peer for a peer list until CMD_SRV_LST arrives
        try:
            first_run_result = query_database(pool_main_db, "SELECT start FROM SystemInfo", '')
            if first_run_result and len(first_run_result) > 0:
                first_run = first_run_result[0]
                if first_run and first_run[0]:
                    _request_initial_peer_list()
        except Exception as e:
            print(f"[ERROR] Error in first run check: {e}")
    
    except Exception as e:
        print(f"[ERROR] Error in maintenance counter: {e}")


def _process_clicker_activity(main_window_self):
    """AT 90 SECONDS - Process clicker activity"""
    try:
        try:
            amount_result = query_database(pool_main_db, "SELECT click_delta FROM SystemInfo", '')
            if amount_result and len(amount_result) > 0:
                amount = amount_result[0][0]
                if amount:
                    update_neurons(amount, init.BALANCE_SHEET_NEURON_CLICKER_ACTIVITY)
                    query_database(pool_main_db, "UPDATE SystemInfo SET click_delta = ?", ('0',))
        except Exception as e:
            print(f"[ERROR] Error processing click delta: {e}")
    except Exception as e:
        print(f"[ERROR] Error in clicker processing: {e}")


def _handle_120_second_tasks(main_window_self):
    """AT 120 SECONDS - Lottery checks and storage checks"""
    try:
        # Queue storage check to background (expensive I/O operation)
        from Support.BackgroundTaskWorker import add_background_task
        add_background_task("CHECK_STORAGE_SPACE")
        
        # Lottery logic (kept sync as it's critical and state-dependent)
        if init.Lottery.running and not init.Lottery.executed:
            _process_lottery_draw(main_window_self)
    
    except Exception as e:
        print(f"[ERROR] Error in 120 second tasks: {e}")


def _process_lottery_draw(main_window_self):
    """Process lottery draw if conditions are met"""
    try:
        current_time = datetime.now()
        
        # Draw windows: Wednesday (2) and Saturday (5) at 12:00
        if current_time.weekday() not in (2, 5):
            return
        if current_time.hour != 12 or current_time.minute != 0:
            return
        if init.Lottery.executed:
            return
        
        init.Lottery.executed = True
            
        if len(init.PEERS) <= 1:
            init.Lottery.running = False
            return
        
        random_peer = int(random.random() * len(init.PEERS))
        
        if init.PEERS[random_peer]['online'] == 1 and init.PEERS[random_peer]['reliability'] >= 3:
            ip = init.PEERS[random_peer]['ip']
            ip_local = init.PEERS[random_peer]['ip_local']
            send_packet('', '', '', ip_to_send_packet_to(ip, ip_local), ip_local, '',
                       init.CMD_LOT_WIN, init.PRT_NOR, '', 0, 0, '',
                       [init.Lottery], [], [], '', '', [], [], [])
        else:
            old_ip = init.Lottery.previous_ip
            old_ip_local = init.Lottery.previous_ip_local
            
            init.Lottery.running = True
            init.Lottery.jackpot += 2
            init.Lottery.previous_ip = init.ME['ip']
            init.Lottery.previous_ip_local = init.ME['ip_local']
            
            for next_peer in init.PEERS:
                if (next_peer['online'] == 1 and
                        next_peer['ip'] != old_ip and
                        next_peer['ip_local'] != old_ip_local):
                        send_packet('', '', '',
                                   ip_to_send_packet_to(next_peer['ip'], next_peer['ip_local']),
                                   next_peer['ip_local'], '',
                                   init.CMD_LOT_STR, init.PRT_NOR, '', 0, 0, '',
                                   [init.Lottery.jackpot, init.Lottery.previous_ip,
                                    init.Lottery.previous_ip_local], [], [],
                                   '', '', [], [], [])
                        break
    
    except Exception as e:
        print(f"[ERROR] Error processing lottery: {e}")


def _check_app_updates_from_supernodes():
    """Ask holders for release catalog; CTL pulls .nrel or starts major piece swarm."""
    try:
        from Support.ModuleSecurity import (
            get_installed_release_version, _release_version_tuple,
            process_stored_releases, advertise_release_to_peers, list_local_releases,
        )
        from Support.MajorUpgrade import (
            get_wanted_release, start_major_piece_swarm, apply_ready_major_if_any,
            flush_pending_adv, catalog_needs_piece_swarm, try_arm_local_neuron_rar,
        )

        # Flush ADVs that were queued while peers were offline.
        try:
            n = flush_pending_adv()
            if n:
                print(f"[APP_UPDATE] flushed {n} pending ADV sends")
        except Exception as e:
            print(f"[APP_UPDATE] pending ADV flush: {e}")

        # Stage any verified packages sitting on disk; triggers restart ticker.
        try:
            process_stored_releases()
        except Exception as e:
            print(f"[APP_UPDATE] process_stored_releases: {e}")

        # Resume / finish major apply (never wipes DB files).
        # Stuck patch/minor wanted: hourly CAT below re-issues GET via CTL.
        # Tower Promote drops Neuron.rar in htdocs — arm same as swarm-complete.
        try:
            armed, amsg = try_arm_local_neuron_rar()
            if armed:
                print(f"[APP_UPDATE] local Neuron.rar: {amsg}")
        except Exception as e:
            print(f"[APP_UPDATE] local Neuron.rar arm: {e}")
        try:
            wanted = get_wanted_release()
            if wanted.get('status') == 'ready_to_apply':
                ok, msg = apply_ready_major_if_any()
                print(f"[APP_UPDATE] major apply: {ok} {msg}")
            elif wanted.get('status') in ('wanted', 'piece_failed', 'no_seeds', 'downloading'):
                if catalog_needs_piece_swarm(wanted) or wanted.get('file_hash'):
                    start_major_piece_swarm(wanted)
                elif wanted.get('release_id') and not catalog_needs_piece_swarm(wanted):
                    # Re-GET .nrel from online supers then leaves (idempotent).
                    from Support.Header import CMD_APP_GET, PRT_NOR
                    rid = wanted.get('release_id') or ''
                    ver = wanted.get('version') or ''
                    nget = 0
                    for peer in init.PEERS:
                        if nget >= 3:
                            break
                        if int(peer.get('online') or 0) != 1:
                            continue
                        if peer['type'] not in (
                            init.NODE_TYPE_DB_SERVER, init.NODE_TYPE_SUPER_DB_SERVER,
                            init.NODE_TYPE_APP_SERVER,
                        ):
                            continue
                        send_packet(
                            init.ME['ip'], init.ME['ip_local'], '',
                            ip_to_send_packet_to(peer.get('ip'), peer.get('ip_local')),
                            peer.get('ip_local'), '',
                            CMD_APP_GET, PRT_NOR, '', 0, 0, '',
                            [rid, ver], [], [], '', '', [], [], []
                        )
                        nget += 1
                    for peer in init.PEERS:
                        if nget >= 6:
                            break
                        if int(peer.get('online') or 0) != 1:
                            continue
                        if peer['type'] in (
                            init.NODE_TYPE_DB_SERVER, init.NODE_TYPE_SUPER_DB_SERVER,
                            init.NODE_TYPE_APP_SERVER,
                        ):
                            continue
                        send_packet(
                            init.ME['ip'], init.ME['ip_local'], '',
                            ip_to_send_packet_to(peer.get('ip'), peer.get('ip_local')),
                            peer.get('ip_local'), '',
                            CMD_APP_GET, PRT_NOR, '', 0, 0, '',
                            [rid, ver], [], [], '', '', [], [], []
                        )
                        nget += 1
        except Exception as e:
            print(f"[APP_UPDATE] wanted major tick: {e}")

        # If we hold a release newer than installed, keep gossiping (mesh fan-out).
        try:
            installed = get_installed_release_version()
            inst_t = _release_version_tuple(installed)
            for sc in list_local_releases()[:5]:
                ver = sc.get('version') or ''
                if _release_version_tuple(ver) <= inst_t:
                    continue
                if (
                    sc.get('verified') or sc.get('downloaded') or sc.get('staged')
                    or sc.get('file_hash') or sc.get('_nrel_path')
                ):
                    advertise_release_to_peers(ver, sc.get('release_id', ''))
                    break
        except Exception as e:
            print(f"[APP_UPDATE] holder re-ADV: {e}")

        my_ip = init.ME.get('ip') if isinstance(init.ME, dict) else None
        my_local = init.ME.get('ip_local') if isinstance(init.ME, dict) else None
        i_am_admin = (
            my_ip == init.SYSTEM_ADMIN_IP or my_local == init.SYSTEM_ADMIN_IP_LOCAL
            or my_ip == init.SYSTEM_ADMIN_IP_LOCAL
        )
        major = int(_release_version_tuple(get_installed_release_version())[0])
        asked = 0

        def _ask(ip, ip_local):
            nonlocal asked
            if not ip and not ip_local:
                return
            send_packet(init.ME['ip'], init.ME['ip_local'], '',
                        ip_to_send_packet_to(ip, ip_local), ip_local, '',
                        init.CMD_APP_CAT, init.PRT_NOR, '', 0, 0, '',
                        [str(major)], [], [],
                        '', '', [], [], [])
            asked += 1

        if not i_am_admin:
            _ask(init.SYSTEM_ADMIN_IP, init.SYSTEM_ADMIN_IP_LOCAL)

        for peer in init.PEERS:
            if asked >= 8:
                break
            if int(peer.get('online') or 0) != 1:
                continue
            if (peer['type'] == init.NODE_TYPE_DB_SERVER or
                peer['type'] == init.NODE_TYPE_SUPER_DB_SERVER or
                peer['type'] == init.NODE_TYPE_APP_SERVER):
                _ask(peer.get('ip'), peer.get('ip_local'))

        for peer in init.PEERS:
            if asked >= 12:
                break
            if int(peer.get('online') or 0) != 1:
                continue
            if peer['type'] in (
                init.NODE_TYPE_DB_SERVER, init.NODE_TYPE_SUPER_DB_SERVER, init.NODE_TYPE_APP_SERVER
            ):
                continue
            _ask(peer.get('ip'), peer.get('ip_local'))
    except Exception as e:
        print(f"[ERROR] Error checking app updates from supernodes: {e}")


def _check_installed_app_updates_from_supernodes():
    """Ask holders whether purchased marketplace apps have newer versions."""
    try:
        from Support.AppUpdates import start_installed_app_update_check
        start_installed_app_update_check()
    except Exception as e:
        print(f"[ERROR] Error checking installed app updates: {e}")


def _check_and_update_resident_apps_async():
    """ASYNC wrapper - queue resident app check to background thread"""
    try:
        from Support.BackgroundTaskWorker import add_background_task
        add_background_task("CHECK_RESIDENT_APPS", None)
    except Exception as e:
        print(f"[ERROR] Error queueing resident apps check: {e}")


def _check_calendar_alarms_async():
    """ASYNC wrapper - queue calendar alarm check to background thread"""
    try:
        from Support.BackgroundTaskWorker import add_background_task
        add_background_task("CHECK_CALENDAR_ALARMS", None)
    except Exception as e:
        print(f"[ERROR] Error queueing calendar alarms check: {e}")


def _update_peer_alive_status_async(main_window_self):
    """ASYNC wrapper - queue peer alive status check to background thread"""
    try:
        from Support.BackgroundTaskWorker import add_background_task
        add_background_task("UPDATE_PEER_ALIVE_STATUS", {"main_window_self": main_window_self})
    except Exception as e:
        print(f"[ERROR] Error queueing peer alive status check: {e}")
