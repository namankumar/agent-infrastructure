"""Project stages reference canonical tasks; placement never implies completion."""
import json
from datetime import datetime, timezone
from priorities import Conflict
from records import encode

class BoardStore:
    def init_boards(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS project_boards(
          project_id TEXT PRIMARY KEY REFERENCES projects(id), version INTEGER NOT NULL,
          data TEXT NOT NULL, manual_layout INTEGER NOT NULL DEFAULT 0,
          source_id TEXT NOT NULL REFERENCES sources(id), quote TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS board_requests(
          id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS board_events(
          id INTEGER PRIMARY KEY, project_id TEXT NOT NULL, previous TEXT, current TEXT NOT NULL);
        ''')

    def board_context(self):
        return [{**dict(r),'data':json.loads(r['data'])} for r in self.db.execute('SELECT * FROM project_boards')]

    def save_board(self, project_id, version, data, request_id, source_id=None, quote=None, direct_user=False):
        self.text(project_id,'project_id');self.text(request_id,'request_id')
        if type(version) is not int or version<0:raise ValueError('Invalid board version')
        if not isinstance(data,dict) or not {'stages','current_stage','milestone'}<=set(data) or set(data)-{'stages','current_stage','milestone','current_stage_confirmed'}:raise ValueError('Board requires stages, current_stage and milestone')
        if not isinstance(data['milestone'],str) or len(data['milestone'])>2000:raise ValueError('Invalid milestone')
        if type(data.get('current_stage_confirmed',False)) is not bool:raise ValueError('Invalid stage confirmation')
        stages=data['stages']
        if not isinstance(stages,list) or not 1<=len(stages)<=20:raise ValueError('Use 1 to 20 stages')
        ids=[];tasks=[]
        for stage in stages:
            if not isinstance(stage,dict) or set(stage)!={'id','title','item_ids'}:raise ValueError('Invalid stage')
            for key in ('id','title'):self.text(stage[key],key)
            if len(stage['title'])>100:raise ValueError('Stage title too long')
            if not isinstance(stage['item_ids'],list) or any(not isinstance(i,str) for i in stage['item_ids']):raise ValueError('Stage tasks must be IDs')
            ids.append(stage['id']);tasks.extend(stage['item_ids'])
        if len(set(ids))!=len(ids) or len(set(tasks))!=len(tasks):raise ValueError('Duplicate stage or task')
        if data['current_stage'] is not None and data['current_stage'] not in ids:raise ValueError('Unknown current stage')
        payload=encode({'project_id':project_id,'version':version,'data':data,'source_id':source_id,'quote':quote,'direct_user':direct_user})
        if len(payload)>100000:raise ValueError('Board too large')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            receipt=self.db.execute('SELECT * FROM board_requests WHERE id=?',(request_id,)).fetchone()
            if receipt:
                if receipt['payload']!=payload:raise Conflict('Board request reused with different content')
                return json.loads(receipt['result'])
            if not self.db.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():raise ValueError('Unknown project')
            old=self.db.execute('SELECT * FROM project_boards WHERE project_id=?',(project_id,)).fetchone()
            if version!=(old['version'] if old else 0):raise Conflict('Board changed; refresh before moving cards')
            for item_id in tasks:
                task=self.db.execute('SELECT kind,project_id FROM items WHERE id=?',(item_id,)).fetchone()
                if not task or task['kind']!='task' or task['project_id']!=project_id:raise ValueError('Board cards must be existing tasks in this project')
            if old and old['manual_layout'] and not direct_user and json.loads(old['data'])['stages']!=stages:
                raise Conflict('Preserve the user-arranged stages; new tasks remain visible without placement')
            if old and not direct_user and json.loads(old['data']).get('current_stage_confirmed') and data['current_stage']!=json.loads(old['data'])['current_stage']:
                raise Conflict('Preserve the user-confirmed current stage')
            if data.get('current_stage_confirmed') and not direct_user:
                evidence=self.db.execute('SELECT source_type FROM sources WHERE id=?',(source_id,)).fetchone()
                if not evidence or evidence[0]!='user':raise ValueError('Confirmed stage requires original user evidence')
            if direct_user:
                if source_id is not None or quote is not None:raise ValueError('UI cannot assert source authority')
                source_id='board:'+request_id;quote='I updated this project board.'
                self.source(source_id,'Direct workspace board edit',quote,source_type='user')
                self.db.execute('UPDATE sources SET processed=1 WHERE id=?',(source_id,))
            else:self.validate_quote(source_id,quote)
            saved={'project_id':project_id,'version':version+1,'data':data,'manual_layout':int(direct_user or bool(old and old['manual_layout'])),'source_id':source_id,'quote':quote,'updated':datetime.now(timezone.utc).isoformat()}
            self.db.execute('INSERT INTO project_boards VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET version=excluded.version,data=excluded.data,manual_layout=excluded.manual_layout,source_id=excluded.source_id,quote=excluded.quote,updated=excluded.updated',tuple(encode(saved[k]) if k=='data' else saved[k] for k in ('project_id','version','data','manual_layout','source_id','quote','updated')))
            self.db.execute('INSERT INTO board_events(project_id,previous,current) VALUES(?,?,?)',(project_id,encode(dict(old)) if old else None,encode(saved)))
            result={'project_id':project_id,'version':version+1}
            self.db.execute('INSERT INTO board_requests VALUES(?,?,?)',(request_id,payload,encode(result)))
            return result
