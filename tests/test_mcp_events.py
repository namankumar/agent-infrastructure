import base64
import hashlib
import hmac
import json
import socket
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hosting.events import EventStore, CallbackError, EVENT, SCOPE, destination, public_addresses, signed_headers
from tests import test_hosted_mcp as hosted


class EventStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'auth.sqlite'
        self.calls = []
        self.secret = 'whsec_' + base64.b64encode(b'x'*32).decode()
        self.params = {'name':EVENT, 'arguments':{'scope':SCOPE}, 'delivery':{
            'mode':'webhook', 'url':'https://receiver.example/events', 'secret':self.secret}}
        self.store = EventStore(self.path, self.sender)
        with self.store.connect() as c:
            c.execute('CREATE TABLE grants(hash TEXT,kind TEXT,expires REAL,data TEXT)')
            c.execute('INSERT INTO grants VALUES (?,?,?,?)', ('hash','refresh',time.time()+3600,json.dumps({'client_id':'owner'})))

    def sender(self, url, body, headers):
        self.calls.append((url,body,headers))
        value = json.loads(body)
        if value.get('type') == 'verification':
            return 200, json.dumps({'challenge':value['challenge']}).encode()
        return 202, b'{}'

    def test_signature_covers_exact_bytes_and_timestamp(self):
        body = b'{"fictional":true}'
        headers = signed_headers(self.secret,'evt-1',body,123)
        wanted = base64.b64encode(hmac.new(b'x'*32,b'evt-1.123.'+body,hashlib.sha256).digest()).decode()
        self.assertEqual(headers['webhook-signature'],'v1,'+wanted)
        with self.assertRaises(ValueError): signed_headers('short','evt-1',body)

    def test_subscription_replay_persistence_and_owner_scope(self):
        first = self.store.subscribe('owner',self.params)
        self.assertEqual(self.store.subscribe('owner',self.params)['id'],first['id'])
        self.assertEqual(len(self.calls),1)
        result = self.store.emit('owner','native-eval-1','Fictional Cedar: draft the invite.')
        self.assertTrue(result[0]['delivered'])
        self.assertTrue(EventStore(self.path,self.sender).emit('owner','native-eval-1','Fictional Cedar: draft the invite.')[0]['replayed'])
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.store.emit('different-owner','native-eval-2','Fictional'),[{'status':'authorization_expired','delivered':False}])
        with self.assertRaises(ValueError): self.store.emit('owner','native-eval-1','Changed source')
        self.store.unsubscribe('owner',self.params)
        self.store.unsubscribe('owner',self.params)
        self.assertEqual(self.store.emit('owner','native-eval-3','Fictional'),[])

    def test_callback_challenge_failure_does_not_subscribe(self):
        store=EventStore(self.path,lambda *args:(200,b'{"challenge":"wrong"}'))
        with self.assertRaises(CallbackError): store.subscribe('owner',self.params)
        with store.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM event_subscriptions').fetchone()[0],0)

    def test_expired_and_terminal_delivery_stops(self):
        sub=self.store.subscribe('owner',self.params)
        self.store.sender=lambda *args:(410,b'')
        self.assertEqual(self.store.emit('owner','native-eval-1','Fictional')[0]['status'],'terminal')
        self.assertEqual(self.store.emit('owner','native-eval-2','Fictional'),[])
        self.store.sender=self.sender
        self.store.subscribe('owner',self.params)
        with self.store.connect() as c:
            c.execute('UPDATE event_subscriptions SET expires=0')
        self.assertEqual(self.store.emit('owner','native-eval-3','Fictional'),[])

    def test_callback_destination_and_dns_rebinding_guard(self):
        for url in ['http://example.com/x','https://user:pw@example.com/x','https://example.com:444/x','https://example.com/x#f']:
            with self.assertRaises(ValueError): destination(url)
        with patch('hosting.events.socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError): public_addresses('receiver.example')
        with patch('hosting.events.socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443)),(socket.AF_INET,socket.SOCK_STREAM,6,'',('10.0.0.1',443))]):
            with self.assertRaises(ValueError): public_addresses('receiver.example')


class EventsHttpTests(hosted.HostedMcpTests):
    def setUp(self):
        super().setUp()
        self.config['experimental_events']=True

    def test_discovery_and_catalog_require_existing_auth(self):
        token=self.exchange(self.authorize()).json['access_token']
        discover=self.rpc(token,'server/discover').json['result']
        self.assertIn('2026-07-28',discover['supportedVersions'])
        self.assertIn('events',discover['capabilities'])
        self.assertEqual(self.rpc(token,'events/list').json['result']['events'][0]['name'],EVENT)
        result=self.request('post','/mcp',headers={'Authorization':'Bearer '+token,'MCP-Protocol-Version':'2026-07-28'},json={'jsonrpc':'2.0','id':1,'method':'events/list'})
        self.assertEqual(result.status_code,200)

    def test_invalid_callback_secret_is_rejected_without_network(self):
        token=self.exchange(self.authorize()).json['access_token']
        result=self.rpc(token,'events/subscribe',{'name':EVENT,'arguments':{'scope':SCOPE},'delivery':{'mode':'webhook','url':'https://example.com','secret':'bad'}})
        self.assertEqual(result.json['error']['code'],-32602)


if __name__=='__main__': unittest.main()
