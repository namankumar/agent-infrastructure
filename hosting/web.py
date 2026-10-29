"""Single-owner hosted proof. Same Todo tools/Store, stateless MCP over HTTPS.

Not a multi-tenant identity provider. Secrets and auth state live outside source.
"""
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, Response, jsonify, redirect, request
from markupsafe import escape
from plugin.server import Server, TOOLS
from hosting.events import EventStore, CallbackError


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def create_app(workspace, db, config, auth_db):
    db, auth_db = Path(db), Path(auth_db)
    if not db.is_file():
        raise ValueError('Initialize the isolated database explicitly before deployment')
    for key in ('owner_key', 'client_secret', 'form_secret'):
        if len(config.get(key, '')) < 32:
            raise ValueError('Missing strong hosting secret: ' + key)
    base = config['base_url'].rstrip('/')
    if not base.startswith('https://'):
        raise ValueError('Public deployment requires HTTPS')
    resource = base + '/mcp'
    server = Server(workspace, db, cloud=True)
    server.require_existing = True
    app = Flask(__name__)
    app.logger.setLevel(logging.INFO)
    app.config['MAX_CONTENT_LENGTH'] = 2_000_000

    def connect():
        c = sqlite3.connect(auth_db)
        c.row_factory = sqlite3.Row
        return c

    with connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS grants (
            hash TEXT PRIMARY KEY, kind TEXT NOT NULL, expires REAL NOT NULL,
            data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (ip TEXT PRIMARY KEY, count INTEGER, until REAL);''')
    events = EventStore(auth_db)

    def put(c, kind, duration, data):
        token = secrets.token_urlsafe(32)
        c.execute('INSERT INTO grants VALUES (?,?,?,?)',
                  (digest(token), kind, time.time()+duration, json.dumps(data)))
        return token

    def get(c, token, kind):
        row = c.execute('SELECT * FROM grants WHERE hash=? AND kind=? AND expires>?',
                        (digest(token), kind, time.time())).fetchone()
        return json.loads(row['data']) if row else None

    def error(name, status=400):
        return jsonify(error=name), status

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'none'; form-action 'self' https://chatgpt.com/connector_platform_oauth_redirect; frame-ancestors 'none'"
        return response

    @app.before_request
    def origin_guard():
        origin = request.headers.get('Origin')
        allowed = {base, 'https://chatgpt.com', 'https://chat.openai.com'}
        if origin and origin not in allowed:
            return error('invalid_origin', 403)

    @app.get('/health')
    def health():
        if not db.is_file():
            return error('database_unavailable', 503)
        return jsonify(status='ok', service='todo', transport='http', version='0.7.10')

    @app.get('/.well-known/oauth-protected-resource')
    @app.get('/.well-known/oauth-protected-resource/mcp')
    def protected_resource():
        return jsonify(resource=resource, authorization_servers=[base], scopes_supported=['todo'])

    @app.get('/.well-known/oauth-authorization-server')
    def metadata():
        return jsonify(issuer=base, authorization_endpoint=base+'/authorize',
                       token_endpoint=base+'/token', response_types_supported=['code'],
                       grant_types_supported=['authorization_code', 'refresh_token'],
                       code_challenge_methods_supported=['S256'], scopes_supported=['todo'],
                       token_endpoint_auth_methods_supported=['client_secret_post', 'client_secret_basic'],
                       authorization_response_iss_parameter_supported=True)

    def valid_authorization(p):
        return (p.get('client_id') == config['client_id']
                and p.get('redirect_uri') in config['redirect_uris']
                and p.get('response_type') == 'code'
                and p.get('resource') == resource
                and p.get('scope', 'todo') == 'todo'
                and p.get('code_challenge_method') == 'S256'
                and re.fullmatch(r'[A-Za-z0-9_-]{43}', p.get('code_challenge', ''))
                and 0 < len(p.get('state', '')) < 2048)

    @app.route('/authorize', methods=['GET', 'POST'])
    def authorize():
        if request.method == 'GET':
            p = request.args.to_dict()
            if not valid_authorization(p):
                return error('invalid_authorization_request')
            # One-use nonce binds the form to this browser and validated request.
            with connect() as c:
                c.execute('DELETE FROM grants WHERE expires<?', (time.time(),))
                nonce = put(c, 'form', 600, p)
            html = ('<!doctype html><title>Connect Todo</title><h1>Connect Todo to ChatGPT</h1>'
                    '<p>Allow ChatGPT to read and update your isolated Todo test workspace.</p>'
                    '<form method="post"><label>Workspace access key '
                    '<input type="password" name="owner_key" required autocomplete="off"></label>'
                    '<input type="hidden" name="nonce" value="'+str(escape(nonce))+'">'
                    '<button type="submit">Connect Todo</button></form>')
            response = Response(html, mimetype='text/html')
            response.set_cookie('todo_consent', nonce, secure=True, httponly=True,
                                samesite='Lax', max_age=600, path='/authorize')
            return response
        nonce = request.form.get('nonce', '')
        if not nonce or not hmac.compare_digest(nonce, request.cookies.get('todo_consent', '')):
            return error('invalid_consent', 403)
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            ip = request.remote_addr or 'unknown'
            attempt = c.execute('SELECT * FROM attempts WHERE ip=?', (ip,)).fetchone()
            if attempt and attempt['until'] > time.time() and attempt['count'] >= 10:
                return error('try_later', 429)
            p = get(c, nonce, 'form')
            if not p or not valid_authorization(p):
                return error('expired_consent', 403)
            if not hmac.compare_digest(digest(request.form.get('owner_key', '')), digest(config['owner_key'])):
                count = attempt['count']+1 if attempt and attempt['until'] > time.time() else 1
                c.execute('INSERT OR REPLACE INTO attempts VALUES (?,?,?)', (ip, count, time.time()+600))
                return error('invalid_credentials', 403)
            c.execute('DELETE FROM grants WHERE hash=?', (digest(nonce),))
            code = put(c, 'code', 120, p)
        response = redirect(p['redirect_uri']+'?'+urlencode({'code':code, 'state':p['state'], 'iss':base}))
        response.delete_cookie('todo_consent', path='/authorize', secure=True, httponly=True, samesite='Lax')
        return response

    @app.post('/token')
    def token():
        p = request.form.to_dict()
        auth = request.authorization
        client = auth.username if auth and auth.type == 'basic' else p.get('client_id', '')
        secret = auth.password if auth and auth.type == 'basic' else p.get('client_secret', '')
        if client != config['client_id'] or not hmac.compare_digest(digest(secret or ''), digest(config['client_secret'])):
            return error('invalid_client', 401)
        if p.get('resource') != resource:
            return error('invalid_target')
        kind = {'authorization_code':'code', 'refresh_token':'refresh'}.get(p.get('grant_type'))
        if not kind:
            return error('unsupported_grant_type')
        value = p.get('code' if kind == 'code' else 'refresh_token', '')
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            grant = get(c, value, kind)
            if not grant or grant['client_id'] != client or grant['resource'] != resource:
                return error('invalid_grant')
            if kind == 'code':
                verifier = p.get('code_verifier', '')
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
                if (not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', verifier)
                        or not hmac.compare_digest(challenge, grant['code_challenge'])
                        or p.get('redirect_uri') != grant['redirect_uri']):
                    return error('invalid_grant')
            c.execute('DELETE FROM grants WHERE hash=?', (digest(value),))
            claims = {'client_id':client, 'resource':resource, 'scope':'todo'}
            access = put(c, 'access', 3600, claims)
            refresh = put(c, 'refresh', 30*86400, claims)
        return jsonify(access_token=access, token_type='Bearer', expires_in=3600,
                       refresh_token=refresh, scope='todo')

    @app.route('/mcp', methods=['GET', 'POST', 'DELETE'])
    def mcp():
        auth = request.headers.get('Authorization', '')
        with connect() as c:
            claims = get(c, auth[7:], 'access') if auth.startswith('Bearer ') else None
        if not claims or claims.get('resource') != resource or claims.get('scope') != 'todo':
            response = jsonify(error='unauthorized')
            response.status_code = 401
            response.headers['WWW-Authenticate'] = 'Bearer resource_metadata="'+base+'/.well-known/oauth-protected-resource/mcp"'
            return response
        if request.method != 'POST':
            return Response(status=405, headers={'Allow':'POST'})
        if request.headers.get('MCP-Protocol-Version', '2025-03-26') not in {'2025-03-26','2025-06-18','2025-11-25','2026-07-28'}:
            return error('unsupported_protocol')
        msg = request.get_json(silent=True)
        if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or not isinstance(msg.get('method'), str):
            return error('invalid_request')
        if 'id' not in msg:
            return Response(status=202)
        if not isinstance(msg['id'], (str, int)) or isinstance(msg['id'], bool):
            return error('invalid_request')
        try:
            method, params = msg['method'], msg.get('params') or {}
            if (method=='server/discover' or method.startswith('events/')) and not config.get('experimental_events',False):
                raise LookupError('Experimental events are paused')
            if method == 'server/discover':
                result = {'resultType':'complete','supportedVersions':['2026-07-28'],
                          'capabilities':{'tools':{},'resources':{},'events':{}}}
            elif method == 'events/list':
                result = events.catalog()
            elif method == 'events/subscribe':
                result = events.subscribe(claims['client_id'], params)
            elif method == 'events/unsubscribe':
                result = events.unsubscribe(claims['client_id'], params)
            else:
                result = server.handle(msg)
            if method in {'server/discover','events/list','events/subscribe','events/unsubscribe'}:
                app.logger.info('todo_event method=%s result=ok', method)
            if msg['method']=='tools/call':
                name=(msg.get('params') or {}).get('name')
                known={t['name'] for t in TOOLS}
                app.logger.info('todo_tool name=%s result=%s',name if name in known else 'unknown',
                                'error' if result.get('isError') else 'ok')
            return jsonify(jsonrpc='2.0', id=msg['id'], result=result)
        except CallbackError as exc:
            app.logger.info('todo_event method=events/subscribe result=callback_error reason=%s',str(exc))
            return jsonify(jsonrpc='2.0',id=msg['id'],error={'code':-32015,'message':'Callback endpoint verification failed','data':{'reason':str(exc)}})
        except LookupError:
            return jsonify(jsonrpc='2.0', id=msg['id'], error={'code':-32601,'message':'Method not found'})
        except (ValueError, TypeError, KeyError):
            return jsonify(jsonrpc='2.0', id=msg['id'], error={'code':-32602,'message':'Invalid parameters'})

    return app
