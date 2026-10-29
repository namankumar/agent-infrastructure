"""Living tables reuse revisioned records; patches retain identities and context."""
import json, math
from records import encode
from priorities import Conflict

TYPES={'text','number','currency','boolean','json','currency_range'}
class TableStore:
 def init_tables(self):
  self.db.execute('CREATE TABLE IF NOT EXISTS table_requests(id TEXT PRIMARY KEY,payload TEXT NOT NULL,result TEXT NOT NULL)');self.db.commit()
 def list_tables(self,query='',project_id=None,after='',limit=30):
  if type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid limit')
  args=[after];where="kind='table' AND json_extract(data,'$.table_schema')=1 AND id>?"
  if project_id is not None:where+=" AND json_extract(data,'$.project_id')=?";args.append(project_id)
  if query:
   where+=" AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')";q='%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%';args.extend([q,q])
  rows=[dict(r) for r in self.db.execute('SELECT id,title,version,data,updated FROM saved_records WHERE '+where+' ORDER BY id LIMIT ?',args+[limit+1])]
  for row in rows:
   d=json.loads(row.pop('data'));row.update(project_id=d.get('project_id'),row_count=len(d['rows']),column_count=len(d['columns']))
  return {'tables':rows[:limit],'next':rows[limit-1]['id'] if len(rows)>limit else None}
 def get_table(self,id,version=None):
  r=self.get_record(id,version)
  if r['kind']!='table' or (version is None and (not isinstance(r['data'],dict) or r['data'].get('table_schema')!=1)):raise ValueError('Not a managed table; explicitly adopt the existing record first')
  return r
 def _validate_table(self,d):
  project=d.get('project_id')
  if project is not None and not self.db.execute('SELECT 1 FROM projects WHERE id=?',(project,)).fetchone():raise ValueError('Unknown project')
  cols=d['columns'];rows=d['rows']
  if not isinstance(cols,list) or not 1<=len(cols)<=100 or not isinstance(rows,list) or len(rows)>2000:raise ValueError('Use 1–100 columns and at most 2000 rows; table size also capped at 200 KB')
  seen=set()
  for c in cols:
   if not isinstance(c,dict) or set(c)!={'id','label','type'} or c['type'] not in TYPES:raise ValueError('Column requires id, label and supported type')
   self.text(c['id'],'column ID');self.text(c['label'],'column label')
   if c['id'] in seen:raise ValueError('Duplicate column ID')
   seen.add(c['id'])
  rowids=set();types={c['id']:c['type'] for c in cols}
  for row in rows:
   if not isinstance(row,dict) or set(row)!={'id','cells'} or not isinstance(row['cells'],dict):raise ValueError('Row requires id and cells')
   self.text(row['id'],'row ID')
   if row['id'] in rowids:raise ValueError('Duplicate row ID')
   rowids.add(row['id'])
   if set(row['cells'])-seen:raise ValueError('Cell uses unknown column')
   for key,v in row['cells'].items():
    t=types[key]
    if v is None:continue
    if t in ('number','currency') and (type(v) not in (int,float) or not math.isfinite(v)):raise ValueError('Numeric cell requires a finite number or null')
    if t=='text' and not isinstance(v,str):raise ValueError('Text cell requires text or null')
    if t=='boolean' and type(v) is not bool:raise ValueError('Boolean cell requires true/false or null')
    if t=='currency_range' and (not isinstance(v,dict) or not {'min','max'}<=v.keys() or any(type(v[k]) not in (int,float) or not math.isfinite(v[k]) for k in ('min','max')) or v['min']>v['max']):raise ValueError('Range requires numeric min <= max')
 def patch_table(self,id,version,request_id,operations,source_id=None,quote=None,source=None,direct_user=False):
  self.text(id,'table ID');self.text(request_id,'request ID')
  if type(version) is not int or version<0 or not isinstance(operations,list) or not 1<=len(operations)<=200:raise ValueError('Invalid table version or operations')
  payload=encode(dict(id=id,version=version,operations=operations,source_id=source_id,quote=quote,source=source,direct_user=direct_user))
  with self.db:
   self.db.execute('BEGIN IMMEDIATE')
   receipt=self.db.execute('SELECT * FROM table_requests WHERE id=?',(request_id,)).fetchone()
   if receipt:
    if receipt['payload']!=payload:raise Conflict('Table request ID reused with different payload')
    return {**json.loads(receipt['result']),'replayed':True}
   if direct_user:
    if source is not None or source_id is not None or quote is not None:raise ValueError('UI cannot assert source authority')
    source_id='table-ui:'+request_id;quote='I edited this table.'
    source={'id':source_id,'label':'Direct table edit','text':quote,'source_type':'user'}
   if source:
    if source.get('id')!=source_id:raise ValueError('Source ID mismatch')
    self.source(source_id,source['label'],source['text'],source.get('source_type','unknown'),source.get('reference'))
   self.validate_quote(source_id,quote)
   exists=self.db.execute('SELECT * FROM saved_records WHERE id=?',(id,)).fetchone()
   if version!=(exists['version'] if exists else 0):raise Conflict('Table changed; read latest before applying this patch')
   r=self.get_record(id) if exists else None
   if r and r.get('canonical_ref'):raise ValueError('External canonical owner; update it there, do not replace with a managed table')
   for op in operations:
    if not isinstance(op,dict):raise ValueError('Invalid operation')
    kind=op.get('op')
    if kind=='create':
     if r:raise ValueError('Table exists; use row/column patches')
     if set(op)-{'op','title','project_id','columns','context'}:raise ValueError('Unknown create fields')
     r={'id':id,'collection':'Tables','kind':'table','title':op['title'],'body':op.get('context',''),'data':{'table_schema':1,'project_id':op.get('project_id'),'columns':op['columns'],'rows':[]}}
    elif kind=='adopt':
     if not r or r['kind']!='table' or r['data'].get('table_schema')==1:raise ValueError('Adopt requires an existing legacy table')
     if set(op)-{'op','columns','rows','project_id'}:raise ValueError('Unknown adopt fields')
     olddata=r['data'];r['data']={'table_schema':1,'project_id':op.get('project_id'),'columns':op['columns'],'rows':op['rows'],'legacy_context':{k:v for k,v in olddata.items() if k not in ('rows','conditional_rows')}}
    else:
     if not r or r['data'].get('table_schema')!=1:raise ValueError('Create/adopt the table first')
     d=r['data']
     if kind=='metadata':
      if set(op)-{'op','title','project_id','context'}:raise ValueError('Unknown metadata fields')
      if 'title' in op:r['title']=op['title']
      if 'project_id' in op:d['project_id']=op['project_id']
      if 'context' in op:r['body']=op['context']
     elif kind=='column':
      if set(op)!={'op','column'}:raise ValueError('Invalid column operation')
      col=op['column'];index=next((i for i,c in enumerate(d['columns']) if c['id']==col['id']),None)
      if index is None:d['columns'].append(col)
      else:d['columns'][index]=col
     elif kind=='row':
      if set(op)!={'op','id','cells'} or not isinstance(op['cells'],dict):raise ValueError('Invalid row patch')
      row=next((x for x in d['rows'] if x['id']==op['id']),None)
      if row is None:row={'id':op['id'],'cells':{}};d['rows'].append(row)
      row['cells'].update(op['cells'])
     elif kind=='delete_row':
      if set(op)!={'op','id'} or not any(x['id']==op['id'] for x in d['rows']):raise ValueError('Unknown row')
      d['rows']=[x for x in d['rows'] if x['id']!=op['id']]
     else:raise ValueError('Unknown table operation')
   self._validate_table(r['data']);r.pop('updated',None);r.update(version=version,source_id=source_id,quote=quote)
   result=self.save_record(request_id='table:'+request_id,record=r,_table_write=True)
   self.db.execute('INSERT INTO table_requests VALUES(?,?,?)',(request_id,payload,encode(result)))
   return result
