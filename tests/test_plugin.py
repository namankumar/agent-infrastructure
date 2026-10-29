import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from app import Store
from plugin.server import Server, URI, CHAT_URI

ROOT=Path(__file__).resolve().parents[1]
class PluginTests(unittest.TestCase):
 def test_overwrite_tools_declare_write_impact(self):
  listing={t['name']:t for t in self.server.handle({'method':'tools/list'})['tools']}
  for name in ('patch_table','ui_table','ui_record','update_record','apply_extraction','reconcile_episode','set_priority','save_board','set_save_preferences'):
   self.assertEqual(listing[name]['annotations'],{'readOnlyHint':False,'destructiveHint':True,'openWorldHint':False})
  for name in ('list_tables','get_table','read_workspace'):
   self.assertEqual(listing[name]['annotations'],{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False})
  self.assertFalse(listing['capture_note']['annotations']['destructiveHint'])
  self.assertEqual(listing['save_record']['annotations'],{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False})
 def test_correction_schema_declares_cancellation_and_fields(self):
  listing={t['name']:t for t in self.server.handle({'method':'tools/list'})['tools']}
  changes=listing['apply_extraction']['inputSchema']['properties']['batch']['properties']['corrections']['items']['properties']['changes']
  self.assertIn('canceled',changes['properties']['status']['enum'])
  self.assertFalse(changes['additionalProperties'])
  self.assertEqual(changes['properties']['steps']['items']['required'],['id','title','done'])
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=Path(self.tmp.name)/'tasks.sqlite';self.server=Server(ROOT,self.db)
 def tearDown(self):self.tmp.cleanup()
 def call(self,name,args=None):return self.server.handle({'method':'tools/call','params':{'name':name,'arguments':args or {}}})
 def seed(self):
  batch={'id':'seed','sources':[{'id':'s','label':'User','text':'Build the task UI.','source_type':'user'}],'projects':[{'id':'p','name':'Tasks','objective':'Build task UI','source_id':'s','quote':'Build the task UI.'}],'items':[{'id':'t','project_id':'p','kind':'task','title':'Build task UI','detail':'','evidence':[{'source_id':'s','quote':'Build the task UI.'}]}]}
  return self.call('apply_extraction',{'batch':batch})
 def test_subject_lookup_preserves_closed_work_evidence_scope_and_manual_rank(self):
  self.seed()
  other=dict(id='other',sources=[dict(id='private-other',label='Other',text='Unrelated archive marker '*5000,source_type='user')],projects=[dict(id='elsewhere',name='Elsewhere',objective='Unrelated work',source_id='private-other',quote='Unrelated')],items=[dict(id='elsewhere-task',project_id='elsewhere',kind='task',title='Unrelated work',detail='Elsewhere',evidence=[dict(source_id='private-other',quote='Unrelated')])])
  self.assertNotIn('isError',self.call('apply_extraction',{'batch':other}))
  self.call('ui_prioritize',{'payload':dict(item_id='t',version=0,position=1,reason='My manual choice',request_id='manual-rank')})
  self.call('update_task',dict(item_id='t',version=1,changes={'status':'done'},request_id='closed',source_id='closed-user',user_statement='I finished the task UI.'))
  before=self.call('read_workspace')['structuredContent']
  result=self.call('read_workspace',{'query':'TASK ui'})['structuredContent']
  self.assertEqual([(i['id'],i['status'],i['version']) for i in result['state']['items']],[('t','done',2)])
  self.assertEqual([p['id'] for p in result['state']['projects']],['p'])
  self.assertEqual({s['id'] for s in result['state']['sources']},{'s','closed-user',before['priorities']['inactive'][0]['source_id']})
  self.assertEqual(result['state']['events'],[e for e in before['state']['events'] if e['item_id']=='t'])
  self.assertEqual(result['priorities']['inactive'],before['priorities']['inactive'])
  self.assertFalse(result['coverage']['workspace_complete'])
  self.assertNotIn('Unrelated archive marker',json.dumps(result))
  self.assertLess(len(json.dumps(result)),len(json.dumps(before))/5)
  self.assertEqual(self.call('read_workspace')['structuredContent'],before)
 def test_subject_lookup_expands_project_and_handles_standalone_misses_and_bad_queries(self):
  self.seed()
  args=dict(request_id='standalone',source_id='standalone-source',user_statement='Return the microphone.',project_id=None,tasks=[dict(id='microphone',title='Return microphone',detail='',quote='Return the microphone.')])
  self.call('create_tasks',args)
  self.call('create_tasks',dict(request_id='step',source_id='step-source',user_statement='Review the board.',project_id='p',tasks=[dict(id='review',title='Review board',detail='',quote='Review the board.')]))
  scoped=self.call('read_workspace',{'query':'Build task UI'})['structuredContent']
  self.assertEqual({i['id'] for i in scoped['state']['items']},{'t','review'})
  standalone=self.call('read_workspace',{'query':'microphone'})['structuredContent']
  self.assertEqual([i['id'] for i in standalone['state']['items']],['microphone'])
  self.assertEqual(standalone['state']['projects'],[])
  missing=self.call('read_workspace',{'query':'nonexistent-subject'})['structuredContent']
  self.assertEqual(missing['state']['items'],[])
  self.assertFalse(missing['coverage']['workspace_complete'])
  for query in ('', '   ', 'x'*201, 1, False, None):
   self.assertTrue(self.call('read_workspace',{'query':query})['isError'])
 def test_subject_lookup_fails_closed_on_oversized_relevant_evidence(self):
  self.seed()
  db=Store(self.db)
  with db.db:db.db.execute('UPDATE sources SET text=? WHERE id=?',('Build the task UI. '+('original source '*12000),'s'))
  db.close()
  result=self.call('read_workspace',{'query':'task UI'})
  self.assertTrue(result['isError'])
  self.assertNotIn('structuredContent',result)
  self.assertIn('Nothing was truncated',result['content'][0]['text'])
 def test_subject_lookup_fails_closed_on_excessive_scope(self):
  db=Store(self.db)
  with db.db:
   db.source('big','Big','Many fictional checks','user')
   for n in range(101):
    db.db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',(str(n),None,'task','Common check','','open',1,'now'))
    db.db.execute('INSERT INTO evidence VALUES(?,?,?)',(str(n),'big','Many fictional checks'))
  db.close()
  self.assertTrue(self.call('read_workspace',{'query':'Common'})['isError'])
  self.assertEqual(len(self.call('read_workspace')['structuredContent']['state']['items']),101)
 def test_additive_task_creation_cannot_overwrite_or_reopen(self):
  args=dict(request_id='create',source_id='intent',user_statement='For Demo I will draft an invitation.',project_id='demo',project=dict(id='demo',name='Demo',objective='Run Demo',quote='For Demo'),tasks=[dict(id='draft',title='Draft invitation',detail='',quote='I will draft an invitation.')])
  first=self.call('create_tasks',args)
  self.assertNotIn('isError',first)
  self.assertEqual(first['structuredContent']['item_ids'],['draft'])
  self.assertTrue(self.call('create_tasks',args)['structuredContent']['replayed'])
  self.assertNotIn('isError',self.call('update_task',dict(item_id='draft',version=1,changes={'status':'done'},request_id='finished',source_id='done',user_statement='The draft is done.')))
  self.assertTrue(self.call('create_tasks',args)['structuredContent']['replayed'])
  bad={**args,'request_id':'overwrite','tasks':[{**args['tasks'][0],'title':'Different'}]}
  self.assertTrue(self.call('create_tasks',bad)['isError'])
  self.assertTrue(self.call('create_tasks',{**args,'request_id':'project-overwrite','project':{**args['project'],'objective':'Different objective'}})['isError'])
  state=self.call('read_workspace')['structuredContent']['state']
  self.assertEqual(state['projects'][0]['objective'],'Run Demo')
  self.assertEqual(len(state['items']),1)
  self.assertEqual((state['items'][0]['status'],state['items'][0]['version']),('done',2))
  listing={t['name']:t for t in self.server.available_tools()}
  self.assertEqual(listing['create_tasks']['annotations'],dict(readOnlyHint=False,destructiveHint=False,openWorldHint=False,idempotentHint=True))
  self.assertTrue(self.call('create_tasks',{**args,'request_id':'inject','tasks':[{**args['tasks'][0],'status':'done'}]})['isError'])
  self.server.cloud=True
  self.assertTrue(self.seed()['isError'])
 def test_additive_task_creation_standalone_evidence_and_project_conflicts(self):
  args=dict(request_id='standalone',source_id='original',user_statement='Return the camera.',project_id=None,tasks=[dict(id='camera',title='Return camera',detail='',quote='Return the camera.')])
  self.assertNotIn('isError',self.call('create_tasks',args))
  self.assertTrue(self.call('create_tasks',args)['structuredContent']['replayed'])
  self.assertTrue(self.call('create_tasks',{**args,'request_id':'fabricated','source_id':'bad','tasks':[{**args['tasks'][0],'id':'bad','quote':'Something else'}]})['isError'])
  state=self.call('read_workspace')['structuredContent']['state']
  self.assertEqual(state['projects'],[])
  self.assertEqual(len(state['items']),1)
  self.assertFalse(any(x['id']=='bad' for x in state['sources']))

 def test_focused_task_update_completion_replay_noop_and_guards(self):
  self.seed()
  args=dict(item_id='t',version=1,changes={'status':'done'},request_id='finished',source_id='done-user',user_statement='I finished the task UI.')
  result=self.call('update_task',args)
  self.assertEqual(result['structuredContent']['version'],2)
  self.assertTrue(self.call('update_task',args)['structuredContent']['replayed'])
  self.assertTrue(self.call('update_task',dict(args,changes={'status':'waiting'}))['isError'])
  state=self.call('read_workspace')['structuredContent']
  task=next(t for t in state['state']['items'] if t['id']=='t')
  self.assertEqual((task['status'],task['completion_kind']),('done','user_reported'))
  unchanged=self.call('update_task',dict(args,version=2,request_id='repeat',source_id='repeat-user'))
  self.assertTrue(unchanged['structuredContent']['unchanged'])
  self.assertTrue(self.call('update_task',dict(args,request_id='stale'))['isError'])
  self.assertTrue(self.call('update_task',dict(args,version=2,request_id='bad',changes={'completion_kind':'verified'}))['isError'])
  self.assertTrue(self.call('update_task',dict(args,item_id='missing',request_id='missing'))['isError'])
  self.assertEqual(self.call('read_workspace')['structuredContent']['priorities'],state['priorities'])
  tool=next(t for t in self.server.available_tools() if t['name']=='update_task')
  self.assertEqual(tool['annotations'],dict(readOnlyHint=False,destructiveHint=True,openWorldHint=False,idempotentHint=True))
 def test_focused_task_update_preserves_manual_edits_and_rejects_nonuser_evidence(self):
  self.seed()
  self.call('ui_correct',{'payload':dict(item_id='t',version=1,changes={'title':'My wording'},reason='Manual rename',request_id='manual')})
  args=dict(item_id='t',version=2,changes={'status':'waiting'},request_id='wait',source_id='user-wait',user_statement='Waiting on the venue.')
  self.assertNotIn('isError',self.call('update_task',args))
  task=self.call('read_workspace')['structuredContent']['state']['items'][0]
  self.assertEqual((task['title'],task['status'],task['version']),('My wording','waiting',3))
  db=Store(self.db)
  with db.db:db.source('assistant','Assistant','I finished it.','assistant')
  db.close()
  self.assertTrue(self.call('update_task',dict(args,version=3,changes={'status':'done'},source_id='assistant',user_statement='I finished it.',request_id='invalid-source'))['isError'])
  self.assertTrue(self.call('update_task',dict(args,version=3,user_statement='Not in original source.',request_id='changed-source'))['isError'])
 def test_hosted_extraction_cannot_overwrite_tasks_in_a_creation_batch(self):
  self.seed();self.server.cloud=True
  listing={t['name']:t for t in self.server.available_tools()}
  batch=listing['apply_extraction']['inputSchema']['properties']['batch']
  self.assertIn('corrections',batch['properties'])
  self.assertIn('project_updates',batch['properties'])
  self.assertEqual(listing['update_task']['securitySchemes'],[{'type':'oauth2','scopes':['todo']}])
  self.assertEqual(listing['update_task']['_meta']['securitySchemes'],listing['update_task']['securitySchemes'])
  self.assertEqual(listing['update_task']['outputSchema']['required'],['id'])
  correction=dict(item_id='t',version=1,changes={'status':'done'},reason='Done',request_id='bypass',source_id='s',quote='Build the task UI.')
  self.assertTrue(self.call('apply_extraction',{'batch':dict(id='bad-batch',sources=[],projects=[],items=[],corrections=[correction])})['isError'])

 def test_stdio_actual_process(self):
  messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},{'jsonrpc':'2.0','method':'notifications/initialized'},{'jsonrpc':'2.0','id':2,'method':'tools/list'},{'jsonrpc':'2.0','id':3,'method':'resources/read','params':{'uri':URI}}]
  p=subprocess.run([sys.executable,str(ROOT/'plugin/server.py'),'--workspace',str(ROOT),'--db',str(self.db)],input='\n'.join(map(json.dumps,messages))+'\n',text=True,capture_output=True,check=True)
  out=list(map(json.loads,p.stdout.splitlines()));self.assertEqual(len(out),3);self.assertEqual(out[0]['result']['serverInfo']['name'],'personal-todo');self.assertIn('text/html;profile=mcp-app',out[2]['result']['contents'][0]['mimeType'])
 def test_capture_replay_restart_shared_store(self):
  args={'id':'n','text':'Remember this thought'}
  self.call('capture_note',args);self.call('capture_note',args)
  s=Store(self.db)
  try:self.assertEqual(len(s.snapshot()['sources']),1);self.assertEqual(s.snapshot()['sources'][0]['processed'],0)
  finally:s.close()
  self.assertTrue(self.call('capture_note',dict(args,text='changed'))['isError'])
 def test_extraction_correction_conflict_and_priority(self):
  self.assertNotIn('isError',self.seed());self.assertTrue(self.seed()['structuredContent']['replayed'])
  data={'item_id':'t','version':1,'changes':{'title':'Finish task UI'},'reason':'Clearer','request_id':'edit'}
  self.assertNotIn('isError',self.call('ui_correct',{'payload':data}))
  self.assertTrue(self.call('ui_correct',{'payload':dict(data,request_id='stale')})['isError'])
  version=self.call('read_workspace')['structuredContent']['priorities']['version']
  result=self.call('ui_prioritize',{'payload':{'item_id':'t','version':version,'position':1,'reason':'My chosen focus','request_id':'rank'}})
  self.assertNotIn('isError',result)
  self.assertEqual(self.call('read_workspace')['structuredContent']['priorities']['confirmed'][0]['item_id'],'t')
 def test_authority_and_invalid_batch(self):
  self.seed()
  self.assertTrue(self.call('ui_prioritize',{'payload':{'authority':'assistant'}})['isError'])
  self.assertTrue(self.call('ui_correct',{'payload':{'changes':{'completion_kind':'verified'}}})['isError'])
  self.assertTrue(self.call('apply_extraction',{'batch':{}})['isError'])
  listing=self.server.handle({'method':'tools/list'})['tools']
  for t in listing:
   if t['name'].startswith('ui_'):self.assertEqual(t['_meta']['ui']['visibility'],['app'])
 def test_resource_and_missing_workspace(self):
  html=self.server.html();self.assertNotIn('await fetch(path',html);self.assertIn('connectHost().then(load)',html);self.assertIn("event.source!==window.parent",html)
  with self.assertRaises(ValueError):Server(Path(self.tmp.name)/'missing')
  result=self.call('open_workspace');self.assertIn('_meta',result);self.assertNotIn('structuredContent',result)

 def test_ui_resources_keep_old_cards_and_allow_inline_fallback(self):
  from plugin.server import LEGACY_URI,PREVIOUS_URI
  for uri in (URI,LEGACY_URI,PREVIOUS_URI):
   content=self.server.handle({'method':'resources/read','params':{'uri':uri}})['contents'][0]
   self.assertEqual(content['uri'],uri)
   self.assertIn('inline',content['_meta']['openai/ui']['availableDisplayModes'])
   self.assertIn('Search saved work',content['text'])
  self.server.ui_domain='https://todo.example'
  content=self.server.handle({'method':'resources/read','params':{'uri':URI}})['contents'][0]
  self.assertEqual(content['_meta']['ui']['domain'],'https://todo.example')

 def test_missing_database_after_start_does_not_recreate(self):
  self.seed();self.server.require_existing=True;self.db.unlink()
  self.assertTrue(self.call('read_workspace')['isError'])
  self.assertFalse(self.db.exists())

 def test_packaged_launch_contract(self):
  portable=json.loads((ROOT/'plugin/mcp.json').read_text())['mcpServers']['personal-todo']
  legacy=json.loads((ROOT/'plugin/.mcp.json').read_text())['mcpServers']['personal-todo']
  overlay=json.loads((ROOT/'plugin/.codex-plugin/plugin.json').read_text())
  self.assertEqual(overlay['mcpServers'],'./.mcp.json')
  self.assertEqual(portable['command'],'python3')
  self.assertEqual(portable['cwd'],'${PLUGIN_ROOT}')
  self.assertEqual({k:v for k,v in portable.items() if k!='type'},legacy)
  self.assertIn('--workspace',portable['args'])

 def test_entrypoints_and_resource_modes(self):
  listing={t['name']:t for t in self.server.handle({'method':'tools/list'})['tools']}
  for name,kind,uri in [('open_workspace','global',URI),('open_chat','thread',CHAT_URI)]:
   self.assertEqual(listing[name]['_meta']['openai/ui']['entrypoints'],[{'type':kind}])
   self.assertEqual(listing[name]['_meta']['ui']['resourceUri'],uri)
   self.assertNotIn('isError',self.call(name,{}))
   resource=self.server.handle({'method':'resources/read','params':{'uri':uri}})['contents'][0]
   self.assertEqual(resource['_meta']['openai/ui']['preferredDisplayMode'],'inline' if kind=='thread' else 'fullscreen')
  self.assertIn('Keep beside chat',self.server.html(CHAT_URI))

 def test_chat_scope_excludes_unrelated_and_reopens_shared_edits(self):
  self.seed()
  self.call('apply_extraction',{'batch':{'id':'b2','sources':[], 'projects':[{'id':'other','name':'Other','objective':'Other objective','source_id':'s','quote':'Build the task UI.'}], 'items':[{'id':'other-task','project_id':'other','kind':'task','title':'Unrelated','detail':'','evidence':[{'source_id':'s','quote':'Build the task UI.'}]}]}})
  self.assertEqual(self.call('read_chat')['structuredContent']['items'],[])
  scoped=self.call('read_chat',{'item_ids':['t']})['structuredContent']
  self.assertEqual([i['id'] for i in scoped['items']],['t'])
  self.assertEqual([p['id'] for p in scoped['projects']],['p'])
  self.assertNotIn('sources',scoped)
  self.assertTrue(self.call('read_chat',{'project_ids':['missing']})['isError'])
  self.assertTrue(self.call('read_chat',{'item_ids':'t'})['isError'])
  self.assertTrue(self.call('read_chat',{'item_ids':['t','t']})['isError'])
  self.call('ui_correct',{'payload':{'item_id':'t','version':1,'changes':{'status':'done'},'reason':'Completed','request_id':'done'}})
  self.server=Server(ROOT,self.db)
  self.assertEqual(self.call('read_chat',{'project_ids':['p']})['structuredContent']['items'][0]['status'],'done')
  self.assertEqual(next(i for i in self.call('read_workspace')['structuredContent']['state']['items'] if i['id']=='t')['status'],'done')

 def test_conversational_priorities_require_evidence_and_preserve_manual_choice(self):
  self.seed()
  args={'item_id':'t','version':0,'position':1,'reason':'Chosen','request_id':'choice','authority':'user','source_id':'s','quote':'invented'}
  self.assertTrue(self.call('set_priority',args)['isError'])
  args['quote']='Build the task UI.'
  self.assertNotIn('isError',self.call('set_priority',args))
  args.update(version=1,request_id='inference',authority='assistant',reason='Suggested')
  self.assertNotIn('isError',self.call('set_priority',args))
  focus=self.call('read_chat',{'item_ids':['t']})['structuredContent']['focus']
  self.assertEqual(focus[0]['authority'],'user')
  self.assertTrue(self.call('set_priority',dict(args,direct_user=True))['isError'])
