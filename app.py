#!/usr/bin/env python3
"""Local capture workspace. Interpretation belongs to the host assistant."""
import argparse
import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from priorities import PriorityStore, Conflict
from work import WorkStore, WORK_DEFAULTS

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / 'data' / 'capture.sqlite'
KINDS = {'task', 'goal', 'idea', 'decision', 'context'}
STATUSES = {'open', 'done', 'waiting', 'blocked', 'parked', 'canceled'}


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


from tables import TableStore
from boards import BoardStore
from records import RecordStore
from reconciliation import ReconciliationStore

from save_preferences import SavePreferences

class Store(SavePreferences, PriorityStore, WorkStore, RecordStore, ReconciliationStore, BoardStore, TableStore):
    def __init__(self, path=DEFAULT_DB, require_existing=False):
        path = Path(path)
        self.monitor_state_path=path.parent / "priority-monitor-state.json"
        self.monitor_config_path=Path.home()/".codex/automations/hourly-priority-monitor/automation.toml" if path.resolve()==DEFAULT_DB.resolve() else None
        if not require_existing: path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path.resolve().as_uri()+'?mode=rw', uri=True) if require_existing else sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.init_save_preferences()
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS sources(
          id TEXT PRIMARY KEY, label TEXT NOT NULL, text TEXT NOT NULL,
          created TEXT NOT NULL, processed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS projects(
          id TEXT PRIMARY KEY, name TEXT NOT NULL, objective TEXT NOT NULL,
          source_id TEXT NOT NULL REFERENCES sources(id), quote TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS items(
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
          kind TEXT NOT NULL, title TEXT NOT NULL, detail TEXT NOT NULL,
          status TEXT NOT NULL, version INTEGER NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence(
          item_id TEXT NOT NULL REFERENCES items(id),
          source_id TEXT NOT NULL REFERENCES sources(id), quote TEXT NOT NULL,
          PRIMARY KEY(item_id,source_id,quote));
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id),
          source_id TEXT NOT NULL REFERENCES sources(id),
          previous TEXT, current TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS project_events(
          id INTEGER PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
          source_id TEXT NOT NULL REFERENCES sources(id), quote TEXT NOT NULL,
          previous TEXT NOT NULL, current TEXT NOT NULL, created TEXT NOT NULL);
        ''')
        if 'version' not in {r['name'] for r in self.db.execute('PRAGMA table_info(projects)')}:
            self.db.execute('ALTER TABLE projects ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
            self.db.commit()

        columns={r['name'] for r in self.db.execute('PRAGMA table_info(sources)')}
        for name,definition in [('source_type',"TEXT NOT NULL DEFAULT 'unknown'"),('reference','TEXT')]:
            if name not in columns:self.db.execute(f'ALTER TABLE sources ADD COLUMN {name} {definition}')
        self.db.commit()
        if any(r['name']=='project_id' and r['notnull'] for r in self.db.execute('PRAGMA table_info(items)')):
            self.db.execute('PRAGMA foreign_keys=OFF')
            try:
                with self.db:
                    self.db.execute('BEGIN IMMEDIATE')
                    if any(r['name']=='project_id' and r['notnull'] for r in self.db.execute('PRAGMA table_info(items)')):
                        self.db.execute('CREATE TABLE items_nullable(id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), kind TEXT NOT NULL, title TEXT NOT NULL, detail TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL, created TEXT NOT NULL)')
                        self.db.execute('INSERT INTO items_nullable SELECT * FROM items')
                        self.db.execute('DROP TABLE items')
                        self.db.execute('ALTER TABLE items_nullable RENAME TO items')
                        if self.db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Item migration failed integrity check')
            finally:self.db.execute('PRAGMA foreign_keys=ON')
        self.init_boards()
        self.init_tables()
        self.init_priorities()
        self.init_work()
        self.init_records()
        self.init_reconciliation()

    def close(self):
        self.db.close()

    @staticmethod
    def text(value, name):
        if not isinstance(value, str) or not value.strip() or len(value) > 200000:
            raise ValueError(f'{name}: expected nonempty text (maximum 200,000 characters)')
        return value

    def source(self, key, label, text, source_type='unknown', reference=None):
        for n,v in [('id',key),('label',label),('text',text)]: self.text(v,n)
        if source_type not in ('unknown','user','tool','record','assistant'): raise ValueError('Invalid source type')
        if reference is not None:self.text(reference,'reference')
        existing = self.db.execute('SELECT * FROM sources WHERE id=?',(key,)).fetchone()
        if existing:
            if existing['text'] != text or existing['label'] != label or (source_type!='unknown' and existing['source_type']!=source_type) or (reference is not None and existing['reference']!=reference):
                raise Conflict('Source ID already contains different content; use a new source ID')
            return key
        self.db.execute('INSERT INTO sources(id,label,text,created,source_type,reference) VALUES(?,?,?,?,?,?)', (key,label,text,now(),source_type,reference))
        return key

    def capture(self, key, label, text):
        with self.db:
            return self.source(key,label,text,source_type='user')

    def validate_quote(self, source_id, quote):
        self.text(quote,'quote')
        source=self.db.execute('SELECT text FROM sources WHERE id=?',(source_id,)).fetchone()
        if not source or quote not in source['text']:
            raise ValueError('Evidence must be an exact excerpt from a saved source')

    def apply(self, batch):
        if not {'id','sources','projects','items'} <= set(batch) or set(batch)-{'id','sources','projects','items','corrections','project_updates'}:
            raise ValueError('Batch requires exactly id, sources, projects and items')
        self.text(batch['id'],'batch id')
        for field in ['sources','projects','items','corrections','project_updates']:
            if not isinstance(batch.get(field,[]),list): raise ValueError(f'{field} must be a list')
        payload=encoded(batch)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            old=self.db.execute('SELECT payload FROM batches WHERE id=?',(batch['id'],)).fetchone()
            if old:
                if old['payload'] != payload: raise Conflict('Batch ID reused with different content')
                return {'replayed':True,'id':batch['id']}
            for s in batch['sources']:
                if not {'id','label','text'}<=set(s) or set(s)-{'id','label','text','source_type','reference'}: raise ValueError('Invalid source fields')
                self.source(s['id'],s['label'],s['text'],s.get('source_type','unknown'),s.get('reference'))
            for p in batch['projects']:
                if set(p) != {'id','name','objective','source_id','quote'}: raise ValueError('Invalid project fields')
                for k,v in p.items(): self.text(v,k)
                self.validate_quote(p['source_id'],p['quote'])
                old=self.db.execute('SELECT * FROM projects WHERE id=?',(p['id'],)).fetchone()
                if old and any(old[k]!=v for k,v in p.items()): raise Conflict('Project exists with different content; do not overwrite')
                if not old:
                    self.db.execute('INSERT INTO projects(id,name,objective,source_id,quote) VALUES(?,?,?,?,?)',tuple(p[k] for k in ['id','name','objective','source_id','quote']))
            for item in batch['items']:
                if set(item) != {'id','project_id','kind','title','detail','evidence'}: raise ValueError('Invalid item fields')
                for k in ['id','title']: self.text(item[k],k)
                if item['project_id'] is not None:self.text(item['project_id'],'project_id')
                if not isinstance(item['detail'],str): raise ValueError('detail must be text')
                if item['kind'] not in KINDS: raise ValueError('Invalid kind')
                if not isinstance(item['evidence'],list) or not item['evidence']: raise ValueError('Evidence required')
                for e in item['evidence']:
                    if set(e) != {'source_id','quote'}: raise ValueError('Invalid evidence fields')
                    self.validate_quote(e['source_id'],e['quote'])
                current={k:item[k] for k in ['id','project_id','kind','title','detail']}
                old=self.db.execute('SELECT * FROM items WHERE id=?',(item['id'],)).fetchone()
                if old:
                    if any(old[k]!=v for k,v in current.items()): raise Conflict('Item exists; use versioned correction')
                else:
                    self.db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',tuple(current.values())+('open',1,now()))
                for e in item['evidence']:
                    self.db.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?)',(item['id'],e['source_id'],e['quote']))
                if not old:
                    saved=dict(self.db.execute('SELECT * FROM items WHERE id=?',(item['id'],)).fetchone())
                    self.db.execute('INSERT INTO events(item_id,source_id,previous,current,created) VALUES(?,?,NULL,?,?)',(item['id'],item['evidence'][0]['source_id'],encoded(saved),now()))
            for correction in batch.get('corrections',[]):
                if not correction.get('source_id') or not correction.get('quote'):
                    raise ValueError('Batch corrections require original source_id and quote')
                self._correct(**correction)
            for update in batch.get('project_updates',[]):
                self._update_project(**update)
            # Mark only supplied sources processed, including consciously excluded inputs.
            for s in batch['sources']:
                self.db.execute('UPDATE sources SET processed=1 WHERE id=?',(s['id'],))
            self.db.execute('INSERT INTO batches VALUES(?,?)',(batch['id'],payload))
        return {'replayed':False,'id':batch['id'],'items':len(batch['items'])}

    def correct(self, item_id, version, changes, reason, request_id, source_id=None, quote=None):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            return self._correct(item_id, version, changes, reason, request_id, source_id, quote)

    def _correct(self, item_id, version, changes, reason, request_id, source_id=None, quote=None):
        changes=dict(changes)
        if changes.get('status')=='cancelled': changes['status']='canceled'
        if not isinstance(version,int) or isinstance(version,bool): raise ValueError('Version must be an integer')
        if not changes or set(changes)-({'title','detail','kind','status','project_id'}|set(WORK_DEFAULTS)): raise ValueError('Unsupported correction fields')
        for k,v in changes.items():
            if k in WORK_DEFAULTS: continue
            if k=='project_id' and v is None: continue
            if k=='detail' and isinstance(v,str): continue
            self.text(v,k)
        if 'kind' in changes and changes['kind'] not in KINDS: raise ValueError('Invalid kind')
        if 'status' in changes and changes['status'] not in STATUSES: raise ValueError('Invalid status; use open, done, waiting, blocked, parked or canceled')
        self.text(reason,'reason'); self.text(request_id,'request_id')
        payload=encoded({'item_id':item_id,'version':version,'changes':changes,'reason':reason,'source_id':source_id,'quote':quote})
        batch_key='correction:'+request_id
        prior=self.db.execute('SELECT payload FROM batches WHERE id=?',(batch_key,)).fetchone()
        if prior:
            if prior['payload']!=payload: raise Conflict('Correction ID reused with different content')
            return {'replayed':True}
        row=self.db.execute('SELECT * FROM items WHERE id=?',(item_id,)).fetchone()
        if not row: raise ValueError('Unknown item')
        old=self.work_context(dict(row))
        if old['version']!=version: raise Conflict('This item changed; refresh before correcting it')
        if changes.get('status')=='done' and old['kind']!='task': raise ValueError('Only tasks can be marked done')
        new={**old,**changes,'version':version+1}
        if new['kind']!='task' and new['status']=='done': raise ValueError('Only tasks can be done')
        if source_id:
            self.validate_quote(source_id,quote)
            sid=source_id
        else:
            if quote is not None: raise ValueError('quote requires source_id')
            sid='correction:'+request_id
            self.source(sid,'Direct local correction (completion is user-reported)',reason,source_type='user')
            self.db.execute('UPDATE sources SET processed=1 WHERE id=?',(sid,))
        self.validate_work(old,new,changes,sid)
        self.save_work(new)
        self.db.execute('UPDATE items SET project_id=?,kind=?,title=?,detail=?,status=?,version=? WHERE id=?',tuple(new[k] for k in ['project_id','kind','title','detail','status','version'])+(item_id,))
        self.db.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?)',(item_id,sid,quote if source_id else reason))
        self.db.execute('INSERT INTO events(item_id,source_id,previous,current,created) VALUES(?,?,?,?,?)',(item_id,sid,encoded(old),encoded(new),now()))
        self.db.execute('INSERT INTO batches VALUES(?,?)',(batch_key,payload))
        return {'id':item_id,'version':new['version']}

    def _update_project(self, project_id, version, objective, source_id, quote):
        self.text(objective,'objective')
        if not isinstance(version,int) or isinstance(version,bool): raise ValueError('Invalid version')
        self.validate_quote(source_id,quote)
        row=self.db.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone()
        if not row: raise ValueError('Unknown project')
        old=dict(row)
        if old['version']!=version: raise Conflict('Project changed; reread before updating')
        new={**old,'objective':objective,'source_id':source_id,'quote':quote,'version':version+1}
        self.db.execute('UPDATE projects SET objective=?,source_id=?,quote=?,version=? WHERE id=?',
                        (objective,source_id,quote,version+1,project_id))
        self.db.execute('INSERT INTO project_events(project_id,source_id,quote,previous,current,created) VALUES(?,?,?,?,?,?)',
                        (project_id,source_id,quote,encoded(old),encoded(new),now()))

    def snapshot(self):
        result={table:[dict(r) for r in self.db.execute(f'SELECT * FROM {table}')] for table in ['projects','items','sources','evidence','events','project_events']}
        result['items']=[self.work_context(i) for i in result['items']]
        result['boards']=self.board_context()
        return result


def serve(db_path, port):
    token=secrets.token_urlsafe(32)
    allowed_host=f'127.0.0.1:{port}'
    origin='http://'+allowed_host
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def reply(self,code,data,html=False):
            payload=data.encode() if html else encoded(data).encode()
            self.send_response(code)
            self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json')
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'nonce-"+token+"'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers(); self.wfile.write(payload)
        def guard(self,write=False):
            if self.headers.get('Host')!=allowed_host:
                self.reply(403,{'error':'Invalid host'}); return False
            if self.headers.get('Origin') not in (None,origin):
                self.reply(403,{'error':'Invalid origin'}); return False
            if write and (self.headers.get('X-Capture-Token')!=token or self.headers.get('Content-Type')!='application/json'):
                self.reply(403,{'error':'Invalid mutation token or content type'}); return False
            return True
        def do_GET(self):
            if not self.guard(): return
            if self.path=='/':
                self.reply(200,(ROOT/'index.html').read_text().replace('__TOKEN__',token),True)
            elif self.path=='/api/priorities':
                s=Store(db_path)
                try: self.reply(200,s.priority_context())
                finally: s.close()
            elif self.path=='/api/save-preferences':
                s=Store(db_path)
                try: self.reply(200,s.get_save_preferences())
                finally: s.close()
            elif self.path=='/api/state':
                s=Store(db_path)
                try: self.reply(200,s.snapshot())
                finally: s.close()
            else: self.reply(404,{'error':'Not found'})
        def do_POST(self):
            if not self.guard(True): return
            s=Store(db_path)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<=0 or size>500000: raise ValueError('Request too large or empty')
                data=json.loads(self.rfile.read(size))
                if self.path=='/api/save-preferences':
                    result=s.set_save_preferences(**data)
                elif self.path=='/api/capture':
                    result={'id':s.capture(data['id'],'Local workspace note',data['text']),'state':'pending'}
                elif self.path=='/api/board':
                    if set(data)-{'project_id','version','data','request_id'}:raise ValueError('Unsupported board fields')
                    result=s.save_board(**data,direct_user=True)
                elif self.path=='/api/tables': result=s.list_tables(**data)
                elif self.path=='/api/table': result=s.get_table(**data)
                elif self.path=='/api/table-edit':
                    if set(data)-{'id','version','request_id','operations'}:raise ValueError('Unsupported table fields')
                    result=s.patch_table(**data,direct_user=True)
                elif self.path=='/api/records': result=s.find_records(**data)
                elif self.path=='/api/record': result=s.get_record(**data)
                elif self.path=='/api/record-edit':
                    if set(data)-{'id','version','changes','request_id'}:raise ValueError('Unsupported record edit fields')
                    result=s.edit_record(**data)
                elif self.path=='/api/correct':
                    if set(data)-{'item_id','version','changes','reason','request_id'} or set(data.get('changes',{})) & {'completion_kind','verification_ref'}:
                        raise ValueError('Browser corrections cannot assert verified completion or source authority')
                    result=s.correct(**data)
                elif self.path=='/api/prioritize':
                    if set(data)-{'item_id','version','position','reason','request_id','due_date','dependencies'}:
                        raise ValueError('Unsupported priority fields')
                    result=s.prioritize(**data,authority='user',direct_user=True)
                else: self.reply(404,{'error':'Not found'}); return
                self.reply(200,result)
            except Conflict as e: self.reply(409,{'error':str(e)})
            except (ValueError,KeyError,TypeError,sqlite3.IntegrityError) as e: self.reply(400,{'error':str(e)})
            finally: s.close()
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(origin,flush=True)
    server.serve_forever()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=DEFAULT_DB)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('serve'); p.add_argument('--port',type=int,default=18473)
    p=sub.add_parser('reconcile'); p.add_argument('file',type=Path)
    sub.add_parser('capture-status')
    p=sub.add_parser('read-episode'); p.add_argument('id')
    p=sub.add_parser('resume-episode'); p.add_argument('id')
    p=sub.add_parser('apply'); p.add_argument('file',type=Path)
    sub.add_parser('snapshot'); sub.add_parser('pending'); sub.add_parser('monitor-context')
    p=sub.add_parser('prioritize'); p.add_argument('file',type=Path)
    p=sub.add_parser('correct'); p.add_argument('file',type=Path)
    args=parser.parse_args()
    if args.command=='serve': serve(args.db,args.port); return
    s=Store(args.db)
    try:
        if args.command=='reconcile': result=s.reconcile_episode(json.loads(args.file.read_text()))
        elif args.command=='read-episode': result=s.read_episode(args.id)
        elif args.command=='resume-episode': result=s.resume_episode(args.id)
        elif args.command=='capture-status': result=s.capture_status()
        elif args.command=='apply': result=s.apply(json.loads(args.file.read_text()))
        elif args.command=='monitor-context': result=s.priority_context()
        elif args.command=='prioritize':
            data=json.loads(args.file.read_text())
            if 'direct_user' in data: raise ValueError('CLI requires original source evidence')
            result=s.prioritize(**data)
        elif args.command=='correct': result=s.correct(**json.loads(args.file.read_text()))
        elif args.command=='pending': result=[x for x in s.snapshot()['sources'] if not x['processed']]
        else: result=s.snapshot()
        print(json.dumps(result,indent=2,ensure_ascii=False))
    finally: s.close()

if __name__=='__main__': main()
