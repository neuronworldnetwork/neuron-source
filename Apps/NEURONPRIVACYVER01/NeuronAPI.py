# NeuronAPI - SDK for sandboxed third-party apps.
# This file is shipped in NeuronSDK/ and copied into Apps\<app_id>\NeuronAPI.py
# by the broker on every launch.
# Do not import Support.* from your app. Use only the functions on NeuronAPI.

"""
Neuron Third-Party App API
==========================

Apps live under Apps/<app_id>/ and communicate with Neuron only through this SDK.
Wire commands are always APPID##### (app_id + five digits), never #app_id and
never Header CMD_* strings. Declare every command in commands.json. Mediated
features (calendar, chat, postit, ...) use maps_to so Neuron emits the real
Header command on your behalf.

Typical structure:
  Apps/<app_id>/manifest.json
  Apps/<app_id>/commands.json
  Apps/<app_id>/<main>.py
  Apps/<app_id>/QueuesApp_<app_id>.py   # def manage_queue(envelope)
  Apps/<app_id>/NeuronAPI.py            # this file (broker-written)

Apps never import Support.
"""

import base64
import importlib.util
import json
import os
import time
import uuid


class NeuronAPI(object):
    """API object returned by connect(). All Neuron interaction goes through here."""

    def __init__(self, app_id, app_root):
        self.app_id = app_id
        self.app_root = app_root
        self.ipc_in = os.path.join(app_root, 'ipc', 'in')
        self.ipc_out = os.path.join(app_root, 'ipc', 'out')
        self.data_dir = os.path.join(app_root, 'data')
        self.logs_dir = os.path.join(app_root, 'logs')
        for d in (self.ipc_in, self.ipc_out, self.data_dir, self.logs_dir):
            os.makedirs(d, exist_ok=True)
        self._pending = {}

    def _jail_path(self, name):
        """Resolve a relative storage name under data/. Raises on path escape."""
        if not name or not isinstance(name, str):
            raise ValueError('invalid name')
        if os.path.isabs(name) or '..' in name.replace('\\', '/').split('/'):
            raise ValueError('path escape')
        full = os.path.normpath(os.path.join(self.data_dir, name))
        data_root = os.path.normpath(self.data_dir)
        if not full.startswith(data_root + os.sep) and full != data_root:
            raise ValueError('path escape')
        return full

    def _encode_data3(self, data3):
        if isinstance(data3, (bytes, bytearray)):
            return base64.b64encode(bytes(data3)).decode('ascii')
        if isinstance(data3, list):
            return base64.b64encode(bytes(bytearray(data3))).decode('ascii')
        return ''

    def _request(self, verb, payload, timeout=5.0):
        req_id = uuid.uuid4().hex
        body = dict(payload)
        body['req'] = verb
        body['id'] = req_id
        name = '%s_%s.json' % (int(time.time() * 1000000), req_id[:10])
        path = os.path.join(self.ipc_out, name)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(body, f, ensure_ascii=False)
        os.replace(tmp, path)
        deadline = time.time() + timeout
        while time.time() < deadline:
            reply = self._pop_reply(req_id)
            if reply is not None:
                if not reply.get('ok'):
                    raise RuntimeError(reply.get('error') or 'request failed')
                return reply.get('result')
            time.sleep(0.05)
        raise TimeoutError('Neuron broker did not reply to %s' % verb)

    def _pop_reply(self, req_id):
        try:
            names = sorted(n for n in os.listdir(self.ipc_in) if n.endswith('.json'))
        except Exception:
            return None
        for name in names:
            path = os.path.join(self.ipc_in, name)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    env = json.load(f)
            except Exception:
                continue
            if env.get('kind') != 'reply' or str(env.get('id', '')) != str(req_id):
                continue
            try:
                os.remove(path)
            except Exception:
                pass
            return env
        return None

    def _normalize_command(self, command):
        command = '' if command is None else str(command).strip()
        if not command:
            raise ValueError('command required')
        if command.startswith('#') or command.startswith('CMD_'):
            raise ValueError('use APPID##### from commands.json, never #app_id or CMD_*')
        prefix = self.app_id
        if not command.startswith(prefix) or len(command) != len(prefix) + 5:
            raise ValueError('command must be app_id + 5 digits')
        if not command[-5:].isdigit():
            raise ValueError('command suffix must be digits')
        return command

    def send_command(self, handle, command, data1=None, data2=None, data3=b''):
        """
        Send one app.custom mesh packet to a peer.

        Parameters:
            handle (str): peer handle from peers()
            command (str): full wire id APPID##### declared in commands.json
            data1 (list of str): text payload
            data2 (list of str): secondary text list
            data3 (bytes or bytearray): binary payload

        Returns:
            True when the broker accepts the request.
        """
        command = self._normalize_command(command)
        data1 = list(data1 or [])
        data2 = list(data2 or [])
        return self._request('send_command', {
            'handle': str(handle),
            'command': command,
            'data1': [str(x) for x in data1],
            'data2': [str(x) for x in data2],
            'data3': self._encode_data3(data3),
        })

    def broadcast_command(self, command, data1=None, data2=None, data3=b'', node_type=None):
        """
        Send one app.custom packet to every online peer (optional node_type filter).

        Parameters:
            command (str): APPID##### from commands.json (feature app.custom)
            data1 (list of str): text payload
            data2 (list of str): secondary text list
            data3 (bytes): binary payload
            node_type: optional peer type filter

        Returns:
            int: number of recipients accepted by the broker.
        """
        command = self._normalize_command(command)
        data1 = list(data1 or [])
        data2 = list(data2 or [])
        payload = {
            'command': command,
            'data1': [str(x) for x in data1],
            'data2': [str(x) for x in data2],
            'data3': self._encode_data3(data3),
        }
        if node_type is not None:
            payload['node_type'] = node_type
        return self._request('broadcast_command', payload)

    def invoke_feature(self, command, **kwargs):
        """
        Run a mediated Neuron feature bound to command in commands.json.

        The broker looks up feature/maps_to (e.g. calendar.share -> CMD_PER_CAL)
        and performs the action. Apps never set Header CMD_* on the wire.

        Parameters:
            command (str): APPID##### with a non-app.custom feature
            **kwargs: feature-specific fields (handle, data1, data2, data3,
                      title, text, ...)

        Returns:
            broker result (often True)
        """
        command = self._normalize_command(command)
        body = dict(kwargs)
        body['command'] = command
        if 'data3' in body:
            body['data3'] = self._encode_data3(body.get('data3') or b'')
        if 'data1' in body and body['data1'] is not None:
            body['data1'] = [str(x) for x in list(body['data1'])]
        if 'data2' in body and body['data2'] is not None:
            body['data2'] = [str(x) for x in list(body['data2'])]
        return self._request('invoke_feature', body)

    def poll(self, timeout=0):
        """
        Return inbound packet envelopes. Does not return broker replies.

        Parameters:
            timeout (float): seconds to wait for at least one packet; 0 = non-blocking

        Returns:
            list of dicts: kind, from, command, ts, data1, data2, data3 (bytes)
        """
        deadline = time.time() + max(0.0, float(timeout))
        while True:
            packets = self._drain_packets()
            if packets:
                return packets
            if time.time() >= deadline:
                return []
            time.sleep(0.05)

    def _drain_packets(self):
        out = []
        try:
            names = sorted(n for n in os.listdir(self.ipc_in) if n.endswith('.json'))
        except Exception:
            return out
        for name in names:
            path = os.path.join(self.ipc_in, name)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    env = json.load(f)
            except Exception:
                continue
            if env.get('kind') == 'reply':
                continue
            if env.get('kind') != 'packet':
                try:
                    os.remove(path)
                except Exception:
                    pass
                continue
            try:
                os.remove(path)
            except Exception:
                continue
            if 'command' not in env and env.get('sub'):
                env['command'] = env.get('sub')
            data3 = env.get('data3', '')
            if isinstance(data3, str) and data3:
                try:
                    env['data3'] = base64.b64decode(data3)
                except Exception:
                    env['data3'] = b''
            elif isinstance(data3, list):
                env['data3'] = bytes(bytearray(data3))
            else:
                env['data3'] = b''
            out.append(env)
        return out

    def run(self):
        """
        Blocking loop: poll for packets and dispatch each to QueuesApp_<app_id>.manage_queue.
        """
        module = self._load_queues_module()
        while True:
            for envelope in self.poll(timeout=0.5):
                try:
                    module.manage_queue(envelope)
                except Exception as e:
                    self.log('manage_queue error: %s' % e)

    def _load_queues_module(self):
        path = os.path.join(self.app_root, 'QueuesApp_%s.py' % self.app_id)
        if not os.path.isfile(path):
            raise FileNotFoundError('Missing %s' % path)
        spec = importlib.util.spec_from_file_location('QueuesApp_%s' % self.app_id, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, 'manage_queue'):
            raise AttributeError('QueuesApp_%s.manage_queue not found' % self.app_id)
        return module

    def me(self):
        """
        Public identity for this node.

        Returns:
            dict: name, handle, city, country, node_type, level (no IPs, no PII).
        """
        return self._request('me', {})

    def peers(self, node_type=None):
        """
        Sanitized peer roster.

        Parameters:
            node_type: optional type filter

        Returns:
            list of dicts: handle, name, city, country, node_type, online (no IPs).
        """
        payload = {}
        if node_type is not None:
            payload['node_type'] = node_type
        return self._request('peers', payload)

    def credit_neurons(self, amount, description='App prize'):
        """First-party only: credit Neurons via AppBroker (small completion prizes)."""
        return self._request('credit_neurons', {
            'amount': float(amount),
            'description': str(description or 'App prize')[:120],
        })

    def notify(self, title, text):
        """
        Show a Neuron taskbar notification on behalf of this app.

        Parameters:
            title (str): short title
            text (str): message body

        Returns:
            True on success.
        """
        return self._request('notify', {'title': str(title or ''), 'text': str(text or '')})

    def app_info(self):
        """
        Metadata for this installed app from the Apps catalog.

        Returns:
            dict: app_id, name, version, developer, tier
        """
        return self._request('app_info', {})

    def storage_write(self, name, data):
        """
        Write a file under Apps/<app_id>/data.

        Parameters:
            name (str): relative path inside data/
            data (str or bytes): content
        """
        path = self._jail_path(name)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        mode = 'wb' if isinstance(data, (bytes, bytearray)) else 'w'
        encoding = None if mode == 'wb' else 'utf-8'
        with open(path, mode, encoding=encoding) as f:
            f.write(data)

    def storage_read(self, name):
        """
        Read a file from Apps/<app_id>/data.

        Parameters:
            name (str): relative path inside data/

        Returns:
            bytes content
        """
        path = self._jail_path(name)
        with open(path, 'rb') as f:
            return f.read()

    def storage_list(self, prefix=''):
        """
        List files under Apps/<app_id>/data.

        Parameters:
            prefix (str): optional relative subdirectory

        Returns:
            list of relative file paths
        """
        root = self._jail_path(prefix) if prefix else self.data_dir
        if not os.path.isdir(root):
            return []
        results = []
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, self.data_dir)
                results.append(rel.replace('\\', '/'))
        return results

    def storage_delete(self, name):
        """
        Delete a file under Apps/<app_id>/data.

        Parameters:
            name (str): relative path inside data/
        """
        path = self._jail_path(name)
        if os.path.isfile(path):
            os.remove(path)

    def log(self, text):
        """
        Append a line to Apps/<app_id>/logs/app.log.

        Parameters:
            text (str): log line
        """
        os.makedirs(self.logs_dir, exist_ok=True)
        path = os.path.join(self.logs_dir, 'app.log')
        with open(path, 'a', encoding='utf-8') as f:
            f.write('%s %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), str(text)))


def connect():
    """
    Create and return a NeuronAPI bound to this app folder.

    Reads app_id from NEURON_APP_ID or from the parent folder name / manifest.json.

    Returns:
        NeuronAPI
    """
    app_root = os.path.dirname(os.path.abspath(__file__))
    app_id = os.environ.get('NEURON_APP_ID', '').strip()
    if not app_id:
        manifest = os.path.join(app_root, 'manifest.json')
        if os.path.isfile(manifest):
            with open(manifest, 'r', encoding='utf-8') as f:
                data = json.load(f)
            app_id = str(data.get('app_id', '') or '')
    if not app_id:
        app_id = os.path.basename(app_root)
    return NeuronAPI(app_id, app_root)
