import base64
import hashlib
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from app import Store
from hosting.web import create_app

ROOT = Path(__file__).resolve().parents[1]


class HostedMcpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name)/'capture.sqlite'
        self.auth_db = Path(self.tmp.name)/'auth.sqlite'
        Store(self.db).close()
        self.config = dict(base_url='https://todo.example', client_id='todo-chatgpt',
                           owner_key='owner'*10, client_secret='secret'*10, form_secret='form'*10,
                           redirect_uris=['https://chatgpt.com/connector_platform_oauth_redirect'])
        self.app = create_app(ROOT, self.db, self.config, self.auth_db)
        self.client = self.app.test_client()
        self.verifier = 'v'*64
        self.params = dict(client_id='todo-chatgpt', redirect_uri=self.config['redirect_uris'][0],
                           response_type='code', resource='https://todo.example/mcp', scope='todo', state='state-123',
                           code_challenge_method='S256', code_challenge=base64.urlsafe_b64encode(
                               hashlib.sha256(self.verifier.encode()).digest()).rstrip(b'=').decode())

    def request(self, method, path, **kwargs):
        return getattr(self.client, method)(path, base_url='https://todo.example', **kwargs)

    def authorize(self):
        r = self.request('get', '/authorize', query_string=self.params)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers['Referrer-Policy'], 'same-origin')
        self.assertIn("form-action 'self' https://chatgpt.com/connector_platform_oauth_redirect;", r.headers['Content-Security-Policy'])
        nonce = re.search('name="nonce" value="([^"]+)"', r.text)[1]
        r = self.request('post', '/authorize', data={'nonce':nonce, 'owner_key':self.config['owner_key']})
        self.assertEqual(r.status_code, 302)
        parts = parse_qs(urlparse(r.location).query)
        self.assertEqual(parts['state'], ['state-123'])
        self.assertEqual(parts['iss'], ['https://todo.example'])
        return parts['code'][0]

    def exchange(self, code, **changes):
        data = dict(grant_type='authorization_code', code=code, code_verifier=self.verifier,
                    client_id='todo-chatgpt', client_secret=self.config['client_secret'],
                    redirect_uri=self.params['redirect_uri'], resource=self.params['resource'])
        data.update(changes)
        return self.request('post', '/token', data=data)

    def rpc(self, access, method, params=None):
        return self.request('post', '/mcp', headers={'Authorization':'Bearer '+access},
                            json={'jsonrpc':'2.0','id':1,'method':method,'params':params or {}})

    def test_no_access_without_auth_and_no_cross_origin(self):
        self.assertEqual(self.request('post','/mcp',json={}).status_code,401)
        r = self.request('post','/mcp',headers={'Origin':'https://evil.example'},json={})
        self.assertEqual(r.status_code,403)
        self.assertNotIn('owner_key',self.request('get','/.well-known/oauth-authorization-server').text)

    def test_pkce_code_single_use_and_target_binding(self):
        code = self.authorize()
        self.assertEqual(self.exchange(code,code_verifier='x'*64).status_code,400)
        self.assertEqual(self.exchange(code,resource='https://evil.example').status_code,400)
        self.assertEqual(self.exchange(code,client_secret='bad').status_code,401)
        self.assertEqual(self.exchange(code).status_code,200)
        self.assertEqual(self.exchange(code).status_code,400)

    def test_redirect_and_consent_protection(self):
        params = dict(self.params,redirect_uri='https://evil.example')
        self.assertEqual(self.request('get','/authorize',query_string=params).status_code,400)
        self.assertEqual(self.request('post','/authorize',data={'owner_key':self.config['owner_key']}).status_code,403)

    def test_store_persists_across_restart_and_local_tools_hidden(self):
        token = self.exchange(self.authorize()).json['access_token']
        r = self.rpc(token,'tools/list').json
        names = {t['name'] for t in r['result']['tools']}
        self.assertIn('patch_table',names)
        table_tool = next(t for t in r['result']['tools'] if t['name']=='patch_table')
        self.assertEqual(table_tool['annotations'],dict(readOnlyHint=False,destructiveHint=True,openWorldHint=False))
        self.assertNotIn('enable_background_capture',names)
        self.assertNotIn('reconcile_episode',names)
        self.assertIn('update_record',names)
        self.rpc(token,'tools/call',{'name':'capture_note','arguments':{'id':'host-proof','text':'Fictional hosting proof'}})
        self.app = create_app(ROOT,self.db,self.config,self.auth_db)
        self.client = self.app.test_client()
        state = self.rpc(token,'tools/call',{'name':'read_workspace','arguments':{}}).json['result']['structuredContent']['state']
        self.assertEqual(sum(s['id']=='host-proof' for s in state['sources']),1)

    def test_core_connection_does_not_advertise_paused_events(self):
        self.config.pop('experimental_events',None)
        token=self.exchange(self.authorize()).json['access_token']
        self.assertEqual(self.rpc(token,'server/discover').json['error']['code'],-32601)
        self.assertEqual(self.rpc(token,'events/list').json['error']['code'],-32601)
        from plugin.server import URI
        result=self.rpc(token,'resources/read',{'uri':URI}).json['result']
        self.assertIn('Search saved work',result['contents'][0]['text'])

    def test_tool_audit_does_not_log_payload_or_token(self):
        token = self.exchange(self.authorize()).json['access_token']
        with self.assertLogs(self.app.logger,level='INFO') as captured:
            self.rpc(token,'tools/call',{'name':'capture_note','arguments':{'id':'audit-proof','text':'private fictional note'}})
        message='\n'.join(captured.output)
        self.assertIn('todo_tool name=capture_note result=ok',message)
        self.assertNotIn('private fictional note',message)
        self.assertNotIn(token,message)

    def test_refresh_rotation_and_expiry(self):
        tokens = self.exchange(self.authorize()).json
        data = dict(grant_type='refresh_token',refresh_token=tokens['refresh_token'],
                    client_id='todo-chatgpt',client_secret=self.config['client_secret'],resource=self.params['resource'])
        r = self.request('post','/token',data=data)
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.request('post','/token',data=data).status_code,400)
        with sqlite3.connect(self.auth_db) as c:
            c.execute('UPDATE grants SET expires=0')
        self.assertEqual(self.rpc(r.json['access_token'],'tools/list').status_code,401)

    def test_task_status_http_contract_readback_replay_and_noop(self):
        access = self.exchange(self.authorize()).json['access_token']
        tools = self.rpc(access, 'tools/list').json['result']['tools']
        update_schema = next(t for t in tools if t['name'] == 'update_task')['outputSchema']
        wording = 'I will confirm the room, print the checklist and email the invitation.'
        create = dict(request_id='http-status-create', source_id='http-status-source',
                      user_statement=wording, project_id=None, tasks=[
                          dict(id='http-room', title='Confirm room', detail='Fictional transport test.', quote='confirm the room'),
                          dict(id='http-checklist', title='Print checklist', detail='Fictional transport test.', quote='print the checklist'),
                          dict(id='http-invitation', title='Email invitation', detail='Fictional transport test.', quote='email the invitation')])
        result = self.rpc(access, 'tools/call', dict(name='create_tasks', arguments=create))
        self.assertEqual(result.status_code, 200)
        self.assertNotIn('isError', result.json['result'])
        for item_id, status, statement in [
                ('http-room', 'waiting', 'The room is waiting on the venue manager.'),
                ('http-checklist', 'canceled', 'I am not printing the checklist anymore.'),
                ('http-invitation', 'done', 'I emailed the invitation.')]:
            args = dict(item_id=item_id, version=1, changes={'status': status},
                        request_id=item_id+'-update', source_id=item_id+'-report', user_statement=statement)
            response = self.rpc(access, 'tools/call', dict(name='update_task', arguments=args))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, 'application/json')
            self.assertNotIn('error', response.json)
            output = response.json['result']
            self.assertNotIn('isError', output)
            value = output['structuredContent']
            self.assertEqual(json.loads(output['content'][0]['text']), value)
            self.assertLessEqual(set(update_schema['required']), set(value))
            self.assertLessEqual(set(value), set(update_schema['properties']))
            self.assertEqual((value['id'], value['version']), (item_id, 2))
            read = self.rpc(access, 'tools/call', dict(name='read_chat', arguments={'item_ids':[item_id]})).json['result']['structuredContent']
            self.assertEqual(len(read['items']), 1)
            self.assertEqual((read['items'][0]['status'], read['items'][0]['version']), (status, 2))
            replay = self.rpc(access, 'tools/call', dict(name='update_task', arguments=args)).json['result']['structuredContent']
            self.assertTrue(replay['replayed'])
            unchanged = dict(args, version=2, request_id=item_id+'-repeat', source_id=item_id+'-repeat-report')
            noop = self.rpc(access, 'tools/call', dict(name='update_task', arguments=unchanged)).json['result']['structuredContent']
            self.assertEqual((noop['unchanged'], noop['version']), (True, 2))
            reread = self.rpc(access, 'tools/call', dict(name='read_chat', arguments={'item_ids':[item_id]})).json['result']['structuredContent']
            self.assertEqual(reread['items'], read['items'])

    def test_notification_does_not_execute_a_tool(self):
        token = self.exchange(self.authorize()).json['access_token']
        r = self.request('post','/mcp',headers={'Authorization':'Bearer '+token},json={
            'jsonrpc':'2.0','method':'tools/call','params':{'name':'capture_note','arguments':{'id':'bad','text':'not a request'}}})
        self.assertEqual(r.status_code,202)
        s = Store(self.db)
        self.assertEqual(s.snapshot()['sources'],[])
        s.close()


if __name__ == '__main__':
    unittest.main()
