"""Resumable orchestration of existing source-backed writes; never executes work."""
import json
import fcntl
import os
from datetime import datetime, timezone
from records import encode

class ReconciliationStore:
 def init_reconciliation(self):
  self.db.executescript('''CREATE TABLE IF NOT EXISTS capture_episodes(
   id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, boundary TEXT NOT NULL,
   payload TEXT NOT NULL, status TEXT NOT NULL, result TEXT, updated TEXT NOT NULL);''')
  if 'replacement_id' not in {r['name'] for r in self.db.execute('PRAGMA table_info(capture_episodes)')}:
   self.db.execute('ALTER TABLE capture_episodes ADD COLUMN replacement_id TEXT');self.db.commit()

 def reconcile_episode(self, episode):
  # Serialize episode orchestration across local processes. Individual store
  # writes still use their normal SQLite transactions and optimistic versions.
  db_path=self.db.execute('PRAGMA database_list').fetchone()[2]
  if not db_path:raise ValueError('Reconciliation requires a durable local database')
  fd=os.open(db_path+'.reconcile.lock',os.O_CREAT|os.O_RDWR,0o600)
  try:
   fcntl.flock(fd,fcntl.LOCK_EX)
   return self._reconcile_episode(episode)
  finally:
   fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

 def _reconcile_episode(self, episode):
  required={'id','chat_id','boundary','batch','records','priorities','skipped'}
  if not isinstance(episode,dict) or not required<=set(episode) or set(episode)-required-{'supersedes','boards','tables'}:raise ValueError('Invalid episode fields')
  for key in ('id','chat_id','boundary'):self.text(episode[key],key)
  for key in ('records','priorities','skipped'):
   if not isinstance(episode[key],list) or len(episode[key])>50:raise ValueError('Episode lists must contain at most 50 entries')
  if not isinstance(episode['batch'],dict):raise ValueError('Source-backed task batch required, even for zero tasks')
  for row in episode['priorities']:
   if not isinstance(row,dict) or set(row)-{'item_id','version','position','reason','request_id','source_id','quote'}:
    raise ValueError('Automatic priorities cannot set user authority, deadlines or execution')
  for skip in episode['skipped']:
   if not isinstance(skip,dict) or set(skip)!={'source_id','quote','reason'}:raise ValueError('Skipped evidence requires source, quote and reason')
   self.text(skip['reason'],'skip reason')
  if not isinstance(episode.get('boards',[]),list) or len(episode.get('boards',[]))>20:raise ValueError('Invalid boards')
  for b in episode.get('boards',[]):
   if not isinstance(b,dict) or set(b)!={'project_id','version','data','request_id','source_id','quote'}:raise ValueError('Automatic boards require evidence and cannot claim UI authority')
  if not isinstance(episode.get('tables',[]),list) or len(episode.get('tables',[]))>50:raise ValueError('Invalid tables')
  for t in episode.get('tables',[]):
   if not isinstance(t,dict) or set(t)-{'id','version','request_id','operations','source_id','quote'}:raise ValueError('Table episodes require evidence and cannot claim UI authority')
  payload=encode(episode)
  if len(payload.encode())>1000000:raise ValueError('Episode exceeds 1 MB; split at settled boundaries')
  now=lambda:datetime.now(timezone.utc).isoformat()
  with self.db:
   self.db.execute('BEGIN IMMEDIATE')
   old=self.db.execute('SELECT * FROM capture_episodes WHERE id=?',(episode['id'],)).fetchone()
   if old:
    if old['payload']!=payload:raise ValueError('Episode ID reused with different content')
    if old['status']=='superseded' or old['replacement_id']:raise ValueError('Episode superseded; use its replacement')
    if old['status']=='complete':return {**json.loads(old['result']),'replayed':True}
   if episode.get('supersedes'):
    prior=self.db.execute('SELECT * FROM capture_episodes WHERE id=?',(episode['supersedes'],)).fetchone()
    if not prior or prior['chat_id']!=episode['chat_id'] or prior['boundary']!=episode['boundary'] or prior['status']!='pending' or prior['replacement_id'] not in (None,episode['id']):
     raise ValueError('Replacement must reconcile the same pending chat boundary')
    self.db.execute('UPDATE capture_episodes SET replacement_id=? WHERE id=?',(episode['id'],episode['supersedes']))
   if not old:self.db.execute('INSERT INTO capture_episodes(id,chat_id,boundary,payload,status,result,updated) VALUES(?,?,?,?,?,?,?)',(episode['id'],episode['chat_id'],episode['boundary'],payload,'pending',None,now()))
  # Each step already has its own atomic idempotent receipt. A failed step leaves
  # this episode pending; exact replay resumes without rewriting prior steps.
  self.apply(episode['batch'])
  for skip in episode['skipped']:self.validate_quote(skip['source_id'],skip['quote'])
  saved=[]
  for args in episode['records']:
   result=self.save_record(**args);current=self.get_record(result['id'])
   if current['version']<result['version']:raise ValueError('Record readback failed')
   saved.append(result)
  tables=[self.patch_table(**t) for t in episode.get('tables',[])]
  boards=[self.save_board(**b) for b in episode.get('boards',[])]
  priorities=[]
  for args in episode['priorities']:priorities.append(self.prioritize(**args,authority='assistant'))
  result={'id':episode['id'],'status':'complete','boundary':episode['boundary'],'records':saved,'tables':tables,'boards':boards,'priorities':priorities,'skipped':len(episode['skipped']),'replayed':False}
  with self.db:
   self.db.execute('UPDATE capture_episodes SET status=?,result=?,updated=? WHERE id=?',('complete',encode(result),now(),episode['id']))
   if episode.get('supersedes'):
    self.db.execute("UPDATE capture_episodes SET status='superseded',updated=? WHERE id=? AND status='pending'",(now(),episode['supersedes']))
  return result

 def read_episode(self,id):
  row=self.db.execute('SELECT payload,status FROM capture_episodes WHERE id=?',(id,)).fetchone()
  if not row:raise ValueError('Unknown episode')
  return {'episode':json.loads(row['payload']),'status':row['status']}

 def resume_episode(self,id):
  saved=self.read_episode(id)
  if saved['status']=='superseded':raise ValueError('Episode superseded; use its replacement')
  return self.reconcile_episode(saved['episode'])

 def capture_status(self, chat_id=None, limit=20):
  if type(limit) is not int or not 1<=limit<=100:raise ValueError('Limit must be 1–100')
  where=' WHERE chat_id=?' if chat_id else ''
  rows=self.db.execute('SELECT id,chat_id,boundary,status,replacement_id,result,updated FROM capture_episodes' +where+" ORDER BY CASE WHEN status='pending' AND replacement_id IS NULL THEN 0 ELSE 1 END, updated DESC LIMIT ?",([chat_id] if chat_id else [])+[limit])
  return {'episodes':[{**dict(r),'result':json.loads(r['result']) if r['result'] else None} for r in rows]}
