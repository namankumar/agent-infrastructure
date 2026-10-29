"""Additive local records. No domain schema or task/CRM migration."""
from contextlib import nullcontext
import json
from datetime import datetime, timezone


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class RecordStore:
    def init_records(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS saved_records(
          id TEXT PRIMARY KEY, collection TEXT NOT NULL, kind TEXT NOT NULL,
          title TEXT NOT NULL, data TEXT NOT NULL, body TEXT NOT NULL,
          source_id TEXT NOT NULL REFERENCES sources(id), quote TEXT NOT NULL,
          canonical_ref TEXT, version INTEGER NOT NULL, updated TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS records_collection_id ON saved_records(collection,id);
        CREATE TABLE IF NOT EXISTS record_revisions(
          record_id TEXT NOT NULL REFERENCES saved_records(id), version INTEGER NOT NULL,
          snapshot TEXT NOT NULL, PRIMARY KEY(record_id,version));
        CREATE TABLE IF NOT EXISTS record_requests(
          id TEXT PRIMARY KEY,payload TEXT NOT NULL,result TEXT NOT NULL);
        CREATE VIRTUAL TABLE IF NOT EXISTS record_search USING fts5(id UNINDEXED, title, body, data);
        ''')

    def save_record(self, request_id, record, source=None, _table_write=False):
        self.text(request_id,'request_id')
        required={'id','collection','kind','title','data','body','source_id','quote','version'}
        if not isinstance(record,dict) or not required<=record.keys() or set(record)-required-{'canonical_ref'}:
            raise ValueError('Supply a complete record and expected version (0 to create)')
        for key in ('id','collection','kind','title','source_id','quote'):self.text(record[key],key)
        if not isinstance(record['body'],str) or type(record['version']) is not int or record['version']<0:
            raise ValueError('Invalid body or version')
        if record.get('canonical_ref') is not None:self.text(record['canonical_ref'],'canonical_ref')
        payload=encode({'record':record,'source':source})
        if len(payload.encode())>200000:raise ValueError('Record exceeds 200 KB; save a reference instead')
        with (nullcontext() if _table_write else self.db):
            if not _table_write:self.db.execute('BEGIN IMMEDIATE')
            receipt=self.db.execute('SELECT * FROM record_requests WHERE id=?',(request_id,)).fetchone()
            if receipt:
                if receipt['payload']!=payload:raise ValueError('Request ID reused with changed content')
                return json.loads(receipt['result'])
            if source:
                if source.get('id')!=record['source_id']:raise ValueError('Source ID mismatch')
                existed=self.db.execute('SELECT 1 FROM sources WHERE id=?',(source['id'],)).fetchone()
                self.source(**{'key':source['id'],'label':source['label'],'text':source['text'],'source_type':source.get('source_type','unknown'),'reference':source.get('reference')})
                if not existed:self.db.execute('UPDATE sources SET processed=1 WHERE id=?',(source['id'],))
            self.validate_quote(record['source_id'],record['quote'])
            old=self.db.execute('SELECT version,data FROM saved_records WHERE id=?',(record['id'],)).fetchone()
            if not _table_write and ((old and isinstance(json.loads(old['data']),dict) and json.loads(old['data']).get('table_schema')==1) or (isinstance(record['data'],dict) and record['data'].get('table_schema')==1)):raise ValueError('Managed tables require patch_table; preserve existing rows and cells')
            if record['version']!=(old['version'] if old else 0):raise ValueError('Stale record; read latest version before saving')
            saved={**record,'canonical_ref':record.get('canonical_ref'),'version':record['version']+1,'updated':datetime.now(timezone.utc).isoformat()}
            self.db.execute('INSERT OR REPLACE INTO saved_records VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                tuple(encode(saved[k]) if k=='data' else saved[k] for k in ('id','collection','kind','title','data','body','source_id','quote','canonical_ref','version','updated')))
            self.db.execute('INSERT INTO record_revisions VALUES(?,?,?)',(saved['id'],saved['version'],encode(saved)))
            self.db.execute('DELETE FROM record_search WHERE id=?',(saved['id'],))
            self.db.execute('INSERT INTO record_search VALUES(?,?,?,?)',(saved['id'],saved['title'],saved['body'],encode(saved['data'])))
            result={'id':saved['id'],'version':saved['version']}
            self.db.execute('INSERT INTO record_requests VALUES(?,?,?)',(request_id,payload,encode(result)))
            return result

    def get_record(self,id,version=None):
        if version is not None:
            row=self.db.execute('SELECT snapshot FROM record_revisions WHERE record_id=? AND version=?',(id,version)).fetchone()
            if not row:raise ValueError('Record version not found')
            return json.loads(row['snapshot'])
        row=self.db.execute('SELECT * FROM saved_records WHERE id=?',(id,)).fetchone()
        if not row:raise ValueError('Record not found')
        value=dict(row);value['data']=json.loads(value['data']);return value

    def find_records(self,collection=None,query='',after='',limit=30):
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('Limit must be 1–100')
        if not all(isinstance(x,str) for x in (query,after)) or (collection is not None and not isinstance(collection,str)):raise ValueError('Invalid search')
        if len(query)>500:raise ValueError('Search is too long')
        clauses=['r.id>?'];args=[after]
        if collection is not None:clauses.append('r.collection=?');args.append(collection)
        if query.strip():
            clauses.append('(r.id IN (SELECT id FROM record_search WHERE record_search MATCH ?) OR instr(lower(r.collection),lower(?))>0 OR instr(lower(r.id),lower(?))>0)')
            args.append(' AND '.join('"'+word.replace('"','""')+'"' for word in query.split()))
            args.extend([query.strip(),query.strip()])
        rows=[dict(x) for x in self.db.execute('SELECT r.id,r.collection,r.kind,r.title,r.version,r.canonical_ref,r.updated FROM saved_records r WHERE '+' AND '.join(clauses)+' ORDER BY r.id LIMIT ?',args+[limit+1])]
        for row in rows[:limit]:
            data=json.loads(self.db.execute('SELECT data FROM saved_records WHERE id=?',(row['id'],)).fetchone()[0])
            fields=data if isinstance(data,dict) else {'value':data}
            row['preview']={str(k)[:80]:(v if v is None or isinstance(v,(int,float,bool)) else (v if isinstance(v,str) else encode(v))[:160]) for k,v in list(fields.items())[:8]}
        return {'records':rows[:limit],'next':rows[limit-1]['id'] if len(rows)>limit else None}

    def edit_record(self, id, version, changes, request_id):
        """Direct UI text edits: versioned, replay-safe, no source/authority input."""
        if not isinstance(changes, dict) or not changes or set(changes)-{'title','body'}:
            raise ValueError('Only title and body can be edited here')
        if type(version) is not int or version < 1 or any(not isinstance(v,str) for v in changes.values()):
            raise ValueError('Invalid edit or version')
        if 'title' in changes:self.text(changes['title'],'title')
        self.text(request_id,'request_id')
        old=self.get_record(id,version=version)
        current=self.get_record(id)
        if current.get('canonical_ref') or old.get('canonical_ref'):
            raise ValueError('Edit this record at its external source')
        if isinstance(old['data'],dict) and old['data'].get('table_schema')==1:
            raise ValueError('Use the table editor to preserve cells')
        record={k:old[k] for k in ('id','collection','kind','title','data','body','source_id','quote','canonical_ref','version')}
        evidence=encode({'record_id':id,'changes':changes})
        record.update(changes,source_id='ui-record:'+request_id,quote=evidence)
        return self.save_record(request_id,record,source={'id':record['source_id'],
            'label':'Direct workspace edit','text':evidence,'source_type':'user',
            'reference':'todo:record:'+id})
