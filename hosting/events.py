"""Bounded MCP Events experiment; no model runner or public event-ingest route.

Only fictional evaluation payloads are accepted. Subscription secrets stay in
the protected host auth database. Webhook destinations are resolved, checked
and pinned for each HTTPS connection; redirects and private addresses fail.
"""
import argparse
import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import math
import os
import secrets
import socket
import sqlite3
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

EVENT = 'todo.capture_test.ready'
SCOPE = 'native-background-eval'


class CallbackError(ValueError):
    pass


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def destination(url):
    p = urlsplit(url)
    try:
        port = p.port
    except ValueError as exc:
        raise ValueError('Invalid callback port') from exc
    if (p.scheme != 'https' or not p.hostname or p.username or p.password
            or p.fragment or port not in (None, 443) or len(url) > 4096):
        raise ValueError('Callback must be public HTTPS on port 443')
    return p


def public_addresses(host):
    results = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(row[4][0] for row in results))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError('Callback address is not public')
    return addresses


def signed_headers(secret, event_id, body, signed_at=None):
    if not isinstance(secret, str) or not secret.startswith('whsec_'):
        raise ValueError('Invalid webhook signing secret')
    try:
        key = base64.b64decode(secret[6:], validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError('Invalid webhook signing secret') from exc
    if not 24 <= len(key) <= 64:
        raise ValueError('Invalid webhook signing secret length')
    at = int(time.time() if signed_at is None else signed_at)
    message = event_id.encode() + b'.' + str(at).encode() + b'.' + body
    signature = base64.b64encode(hmac.new(key, message, hashlib.sha256).digest()).decode()
    return {'Content-Type': 'application/json', 'webhook-id': event_id,
            'webhook-timestamp': str(at), 'webhook-signature': 'v1,' + signature}


def public_post(url, body, headers):
    """Never use environment proxies or follow a redirected callback."""
    p = destination(url)
    addresses = public_addresses(p.hostname)
    # TLS validates the original hostname, but the socket is pinned to a
    # just-validated public address, preventing DNS-rebinding to private hosts.
    class PinnedHTTPS(http.client.HTTPSConnection):
        def connect(self):
            raw = socket.create_connection((addresses[0], 443), timeout=self.timeout)
            try:
                self.sock = self._context.wrap_socket(raw, server_hostname=p.hostname)
            except Exception:
                raw.close()
                raise
    conn = PinnedHTTPS(p.hostname, timeout=8, context=ssl.create_default_context())
    try:
        conn.request('POST', p.path or '/' if not p.query else (p.path or '/') + '?' + p.query,
                     body=body, headers=headers)
        r = conn.getresponse()
        data = r.read(32769)
        if len(data) > 32768:
            raise CallbackError('response_too_large')
        return r.status, data
    finally:
        conn.close()


class EventStore:
    def __init__(self, auth_db, sender=public_post):
        self.db, self.sender = Path(auth_db), sender
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS event_subscriptions (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                arguments TEXT NOT NULL, callback TEXT NOT NULL, secret TEXT NOT NULL,
                expires REAL NOT NULL, verified REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS event_deliveries (
                subscription_id TEXT NOT NULL, event_id TEXT NOT NULL,
                body TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                response_code INTEGER, PRIMARY KEY(subscription_id,event_id));''')
        os.chmod(self.db, 0o600)

    def connect(self):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def catalog():
        return {'events': [{
            'name': EVENT,
            'description': 'New fictional episode ready for the isolated Todo native-background evaluation. Not a real user commitment or an all-chat feed.',
            'delivery': ['webhook'],
            'inputSchema': {'type': 'object', 'properties': {'scope': {'type': 'string', 'enum': [SCOPE]}},
                            'required': ['scope'], 'additionalProperties': False},
            'payloadSchema': {'type': 'object', 'properties': {
                'scope': {'type': 'string', 'enum': [SCOPE]},
                'episode_id': {'type': 'string'}, 'text': {'type': 'string'},
                'fictional': {'type': 'boolean', 'const': True}},
                'required': ['scope', 'episode_id', 'text', 'fictional'], 'additionalProperties': False}
        }]}

    def identity(self, owner, params):
        if params.get('name') != EVENT or params.get('arguments') != {'scope': SCOPE}:
            raise ValueError('Unsupported evaluation event or scope')
        delivery = params.get('delivery') or {}
        if delivery.get('mode') != 'webhook':
            raise ValueError('Only webhook delivery is supported')
        url = delivery.get('url', '')
        destination(url)
        identity = canon([owner, EVENT, params['arguments'], url])
        return 'todo_evt_' + hashlib.sha256(identity.encode()).hexdigest()[:32], delivery

    def subscribe(self, owner, params):
        sid, delivery = self.identity(owner, params)
        secret = delivery.get('secret')
        signed_headers(secret, 'validate', b'{}')
        ttl = params.get('ttlMs', 3600000)
        # This bounded experiment grants finite subscriptions even if the host
        # asks for no expiry; it can refresh normally before the granted time.
        if ttl is None:
            ttl = 3600000
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not math.isfinite(ttl) or ttl <= 0:
            raise ValueError('Invalid subscription lifetime')
        expires = time.time() + min(ttl / 1000, 86400)
        with self.connect() as c:
            prior = c.execute('SELECT * FROM event_subscriptions WHERE id=? AND owner=?', (sid, owner)).fetchone()
        # A changed secret is challenge-verified before replacing the active one.
        if not prior or prior['secret'] != secret or time.time() - prior['verified'] > 60:
            challenge = secrets.token_urlsafe(32)
            body = canon({'type': 'verification', 'challenge': challenge}).encode()
            headers = signed_headers(secret, 'verify_' + secrets.token_hex(16), body)
            headers['X-MCP-Subscription-Id'] = sid
            try:
                status, response = self.sender(delivery['url'], body, headers)
                value = json.loads(response)
                valid = 200 <= status < 300 and isinstance(value, dict) and isinstance(value.get('challenge'), str)
                if not valid or not hmac.compare_digest(value['challenge'], challenge):
                    raise CallbackError('challenge_failed')
            except CallbackError:
                raise
            except (OSError, ValueError, http.client.HTTPException) as exc:
                raise CallbackError('callback_unreachable') from exc
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO event_subscriptions VALUES (?,?,?,?,?,?,?,?)',
                      (sid, owner, EVENT, canon(params['arguments']), delivery['url'], secret, expires, time.time()))
        return {'id': sid, 'refreshBefore': datetime.fromtimestamp(expires, timezone.utc).isoformat(),
                'cursor': None, 'truncated': False}

    def unsubscribe(self, owner, params):
        sid, _ = self.identity(owner, params)
        with self.connect() as c:
            c.execute('DELETE FROM event_subscriptions WHERE id=? AND owner=?', (sid, owner))
        return {}

    def emit(self, owner, event_id, text):
        if not event_id.startswith('native-eval-') or not 0 < len(text) <= 8000:
            raise ValueError('Only bounded fictional evaluation events are supported')
        payload = {'eventId': event_id, 'name': EVENT,
                   'timestamp': datetime.now(timezone.utc).isoformat(),
                   'data': {'scope': SCOPE, 'episode_id': event_id, 'text': text, 'fictional': True}, 'cursor': None}
        summary = []
        with self.connect() as c:
            subs = c.execute('SELECT * FROM event_subscriptions WHERE owner=? AND expires>?', (owner, time.time())).fetchall()
            authorized = any(json.loads(r['data']).get('client_id') == owner for r in c.execute(
                "SELECT data FROM grants WHERE kind IN ('access','refresh') AND expires>?", (time.time(),)))
        if not authorized:
            return [{'status': 'authorization_expired', 'delivered': False}]
        for sub in subs:
            with self.connect() as c:
                c.execute('INSERT OR IGNORE INTO event_deliveries(subscription_id,event_id,body,status) VALUES (?,?,?,?)',
                          (sub['id'], event_id, canon(payload), 'pending'))
                saved = c.execute('SELECT * FROM event_deliveries WHERE subscription_id=? AND event_id=?', (sub['id'], event_id)).fetchone()
                # Repeated IDs cannot change the source episode. Timestamp is
                # server-generated once and retained in the durable body.
                if json.loads(saved['body'])['data']['text'] != text:
                    raise ValueError('Existing event identity has different text')
                if saved['status'] in ('accepted', 'terminal'):
                    summary.append({'status': saved['status'], 'replayed': True})
                    continue
                if saved['attempts'] >= 3:
                    summary.append({'status': 'attempt_limit', 'delivered': False})
                    continue
                c.execute('UPDATE event_deliveries SET attempts=attempts+1 WHERE subscription_id=? AND event_id=?', (sub['id'], event_id))
            body = saved['body'].encode()
            headers = signed_headers(sub['secret'], event_id, body)
            headers['X-MCP-Subscription-Id'] = sub['id']
            code = None
            try:
                code, _ = self.sender(sub['callback'], body, headers)
                result = 'accepted' if 200 <= code < 300 else 'terminal' if code in (410, 413) else 'pending'
            except (OSError, ValueError, http.client.HTTPException):
                result = 'pending'
            with self.connect() as c:
                c.execute('UPDATE event_deliveries SET status=?,response_code=? WHERE subscription_id=? AND event_id=?',
                          (result, code, sub['id'], event_id))
                if code == 410:
                    c.execute('DELETE FROM event_subscriptions WHERE id=?', (sub['id'],))
            summary.append({'status': result, 'response_code': code, 'delivered': result == 'accepted'})
        return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--auth-db', type=Path, required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--event-id')
    parser.add_argument('--text')
    args = parser.parse_args()
    store = EventStore(args.auth_db)
    if args.event_id:
        print(canon({'deliveries': store.emit(args.owner, args.event_id, args.text or '')}))
    else:
        with store.connect() as c:
            print(canon({'active_subscriptions': c.execute('SELECT COUNT(*) FROM event_subscriptions WHERE owner=? AND expires>?', (args.owner, time.time())).fetchone()[0],
                        'deliveries': [dict(r) for r in c.execute('SELECT event_id,status,attempts,response_code FROM event_deliveries')]}))
