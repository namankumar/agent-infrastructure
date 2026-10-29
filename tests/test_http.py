import json
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from app import Store
from test_priorities import fixture

class HttpTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.db=Path(cls.tmp.name)/'http.sqlite'
  sock=socket.socket();sock.bind(('127.0.0.1',0));cls.port=sock.getsockname()[1];sock.close()
  cls.url=f'http://127.0.0.1:{cls.port}'
  store=Store(cls.db);fixture(store);store.close()
  cls.proc=subprocess.Popen([sys.executable,'app.py','--db',str(cls.db),'serve','--port',str(cls.port)],stdout=subprocess.DEVNULL)
  for _ in range(50):
   try:
    html=urllib.request.urlopen(cls.url,timeout=1).read().decode();break
   except OSError:time.sleep(.05)
  else:raise RuntimeError('Server did not start')
  cls.token=re.search("const token='([^']+)'",html).group(1)
 @classmethod
 def tearDownClass(cls):cls.proc.terminate();cls.proc.wait();cls.tmp.cleanup()
 def test_capture_survives_readback(self):
  body={'id':'http-proof','text':'<script>not executed</script> Maybe a future idea.'}
  req=urllib.request.Request(self.url+'/api/capture',json.dumps(body).encode(),{'Content-Type':'application/json','X-Capture-Token':self.token,'Origin':self.url})
  self.assertEqual(urllib.request.urlopen(req).status,200)
  data=json.load(urllib.request.urlopen(self.url+'/api/state'))
  saved=next(s for s in data['sources'] if s['id']==body['id']);self.assertEqual(saved['text'],body['text']);self.assertEqual(saved['processed'],0)
 def test_cross_origin_and_missing_token_rejected(self):
  for headers in [{'Content-Type':'application/json'},{'Content-Type':'application/json','X-Capture-Token':self.token,'Origin':'https://attacker.example'}]:
   with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(urllib.request.Request(self.url+'/api/capture',b'{}',headers))
   self.assertEqual(e.exception.code,403)
 def test_host_rebinding_rejected(self):
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(urllib.request.Request(self.url,headers={'Host':'attacker.example'}))
  self.assertEqual(e.exception.code,403)

 def test_priority_user_path_and_authority_guard(self):
  context=json.load(urllib.request.urlopen(self.url+'/api/priorities'))
  body={'item_id':'a','version':context['version'],'position':1,'reason':'First today','request_id':'http-priority'}
  headers={'Content-Type':'application/json','X-Capture-Token':self.token,'Origin':self.url}
  def post(value):return urllib.request.urlopen(urllib.request.Request(self.url+'/api/prioritize',json.dumps(value).encode(),headers))
  self.assertEqual(post(body).status,200)
  actual=json.load(urllib.request.urlopen(self.url+'/api/priorities'))
  self.assertEqual(actual['confirmed'][0]['reason'],'First today');self.assertTrue(actual['alignment_ready'])
  self.assertTrue(json.load(post(body))['replayed'])
  for key,value in [('authority','assistant'),('direct_user',False),('source_id','s')]:
   with self.assertRaises(urllib.error.HTTPError) as error:post({**body,key:value})
   self.assertEqual(error.exception.code,400)
  with self.assertRaises(urllib.error.HTTPError) as error:post({**body,'request_id':'stale'})
  self.assertEqual(error.exception.code,409)

 def test_browser_cannot_claim_verification(self):
  headers={'Content-Type':'application/json','X-Capture-Token':self.token,'Origin':self.url}
  body={'item_id':'b','version':1,'changes':{'status':'done','completion_kind':'verified','verification_ref':'fake'},'reason':'Finished','request_id':'forged'}
  def post(value):return urllib.request.urlopen(urllib.request.Request(self.url+'/api/correct',json.dumps(value).encode(),headers))
  with self.assertRaises(urllib.error.HTTPError) as error:post(body)
  self.assertEqual(error.exception.code,400)
  body['changes']={'status':'done','progress_note':'I finished the call'};body['request_id']='reported'
  self.assertEqual(post(body).status,200)
  state=json.load(urllib.request.urlopen(self.url+'/api/state'))
  item=next(i for i in state['items'] if i['id']=='b');self.assertEqual(item['completion_kind'],'user_reported')
  with self.assertRaises(urllib.error.HTTPError):post({**body,'source_id':'s'})
