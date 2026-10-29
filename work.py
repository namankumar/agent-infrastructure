"""Item-level execution context, independent of priority recommendations."""
import json
from priorities import Conflict

WORK_DEFAULTS={'next_action_id':None,'blocker':'','dependencies':[],
               'progress_note':'','completion_kind':'none','verification_ref':None,'steps':[]}


class WorkStore:
    def init_work(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS item_work(
          item_id TEXT PRIMARY KEY REFERENCES items(id), next_action_id TEXT REFERENCES items(id),
          blocker TEXT NOT NULL, dependencies TEXT NOT NULL, progress_note TEXT NOT NULL,
          completion_kind TEXT NOT NULL, verification_ref TEXT)''')
        self.db.execute('CREATE TABLE IF NOT EXISTS item_steps(item_id TEXT PRIMARY KEY REFERENCES items(id), steps TEXT NOT NULL)')
        self.db.commit()

    def work_context(self,item):
        row=self.db.execute('SELECT * FROM item_work WHERE item_id=?',(item['id'],)).fetchone()
        if row:
            data=dict(row);data.pop('item_id');data['dependencies']=json.loads(data['dependencies'])
        else:
            data={**WORK_DEFAULTS,'dependencies':[]}
            if item['status']=='done': data['completion_kind']='user_reported'
        steps=self.db.execute('SELECT steps FROM item_steps WHERE item_id=?',(item['id'],)).fetchone()
        data['steps']=json.loads(steps[0]) if steps else []
        return {**item,**data}

    def validate_work(self,old,new,changes,source_id):
        steps=new['steps']
        if not isinstance(steps,list) or len(steps)>100:raise ValueError('Steps must be a list of at most 100 entries')
        seen=set();previous={s['id']:s for s in old.get('steps',[])}
        for step in steps:
            if not isinstance(step,dict) or set(step)!={'id','title','done'} or type(step['done']) is not bool:raise ValueError('Step requires id, title and done boolean')
            self.text(step['id'],'step ID');self.text(step['title'],'step title')
            if step['id'] in seen:raise ValueError('Duplicate step ID')
            seen.add(step['id'])
            if step['done'] and step!=previous.get(step['id']):
                src=self.db.execute('SELECT source_type,reference FROM sources WHERE id=?',(source_id,)).fetchone()
                if not src or not (src['source_type']=='user' or (src['source_type']=='tool' and src['reference'])):raise ValueError('Completed steps need original user confirmation or a tool receipt')
        for field in ('blocker','progress_note'):

            if not isinstance(new[field],str):raise ValueError(field+' must be text')
        for field in ('next_action_id','verification_ref'):
            if new[field] is not None:self.text(new[field],field)
        if new['completion_kind'] not in ('none','user_reported','verified'):
            raise ValueError('Invalid completion kind')
        deps=new['dependencies']
        if not isinstance(deps,list) or any(not isinstance(d,str) for d in deps):raise ValueError('Dependencies must be task IDs')
        if len(set(deps))!=len(deps) or new['id'] in deps:raise ValueError('Duplicate or self dependency')
        if deps and new['kind'] not in ('task','goal'):raise ValueError('Only tasks and goals have dependencies')
        for key in deps:
            row=self.db.execute('SELECT * FROM items WHERE id=?',(key,)).fetchone()
            if not row or row['kind']!='task':raise ValueError('Dependency must reference an existing task')
        action=new['next_action_id']
        if action:
            row=self.db.execute('SELECT * FROM items WHERE id=?',(action,)).fetchone()
            if new['kind']!='goal' or not row or row['kind']!='task' or row['project_id']!=new['project_id']:
                raise ValueError('Next action must be an existing task in the same project as the goal')
        graph={r['item_id']:json.loads(r['dependencies']) for r in self.db.execute('SELECT * FROM item_work')}
        for r in self.db.execute("SELECT item_id,dependencies FROM priorities WHERE authority='user' AND position IS NOT NULL"):
            graph[r['item_id']]=list(dict.fromkeys(graph.get(r['item_id'],[])+json.loads(r['dependencies'])))
        priority_deps=self.db.execute("SELECT dependencies FROM priorities WHERE item_id=? AND authority='user' AND position IS NOT NULL",(new['id'],)).fetchone()
        graph[new['id']]=list(dict.fromkeys(deps+(json.loads(priority_deps[0]) if priority_deps else [])))
        def visit(key,path):
            if key in path:raise ValueError('Dependency cycle')
            for dep in graph.get(key,[]):visit(dep,path|{key})
        for key in graph:visit(key,set())
        definition_changed=any(new[k]!=old[k] for k in ('title','detail','project_id','kind'))
        if old['completion_kind']=='verified' and new['status']=='done' and definition_changed and not {'completion_kind','verification_ref'} & set(changes):
            # A receipt for an earlier task definition cannot verify changed work.
            new['status']='open'
        if new['status']!='done':
            if changes.get('completion_kind','none')!='none' or changes.get('verification_ref'):
                raise ValueError('Completion evidence requires done status')
            new['completion_kind']='none';new['verification_ref']=None
            if new['status']=='canceled' and old['status']!='canceled':
                source=self.db.execute('SELECT source_type,reference FROM sources WHERE id=?',(source_id,)).fetchone()
                if not source or not (source['source_type']=='user' or (source['source_type']=='tool' and source['reference'])):
                    raise ValueError('Cancellation needs original user confirmation or a referenced tool receipt')
        elif old['status']!='done' or {'completion_kind','verification_ref'} & set(changes):
            source=self.db.execute('SELECT * FROM sources WHERE id=?',(source_id,)).fetchone()
            kind=changes.get('completion_kind','user_reported')
            if kind=='verified':
                if definition_changed and old['completion_kind']=='verified' and new['verification_ref']==old['verification_ref']:
                    raise ValueError('Changed task definition needs fresh verification evidence')
                if source['source_type']!='tool' or not source['reference'] or new['verification_ref']!=source['reference']:
                    raise ValueError('Verified completion needs a referenced tool receipt and verification_ref')
            elif kind=='user_reported':
                if source['source_type']!='user':raise ValueError('User-reported completion needs original user evidence')
                new['verification_ref']=None
            else:raise ValueError('Done requires a completion classification')
            new['completion_kind']=kind

    def create_tasks(self, request_id, source_id, user_statement, project_id, tasks, project=None):
        """Add user commitments only; existing rows can never be replaced."""
        for key,value in [('request ID',request_id),('source ID',source_id),('user statement',user_statement)]:self.text(value,key)
        if len(user_statement)>4000:raise ValueError('Use only the relevant original statement, at most 4000 characters')
        if project_id is not None:self.text(project_id,'project ID')
        if not isinstance(tasks,list) or not 1<=len(tasks)<=20:raise ValueError('Create 1 to 20 tasks')
        seen=set();items=[]
        for task in tasks:
            if not isinstance(task,dict) or set(task)!={'id','title','detail','quote'}:raise ValueError('Task requires only id, title, detail and quote')
            for k in ['id','title','quote']:self.text(task[k],k)
            if not isinstance(task['detail'],str) or task['quote'] not in user_statement:raise ValueError('Task needs exact original user evidence')
            if task['id'] in seen:raise ValueError('Duplicate task ID')
            seen.add(task['id'])
            items.append(dict(id=task['id'],project_id=project_id,kind='task',title=task['title'],detail=task['detail'],evidence=[dict(source_id=source_id,quote=task['quote'])]))
        projects=[]
        if project is not None:
            if not isinstance(project,dict) or set(project)!={'id','name','objective','quote'} or project['id']!=project_id:
                raise ValueError('New project must match project_id and contain id, name, objective and quote only')
            if not isinstance(project['quote'],str) or project['quote'] not in user_statement:raise ValueError('Project needs exact original user evidence')
            projects=[dict(project,source_id=source_id)]
        source=self.db.execute('SELECT * FROM sources WHERE id=?',(source_id,)).fetchone()
        if source:
            if source['source_type']!='user':raise ValueError('Task capture requires original user evidence')
            self.validate_quote(source_id,user_statement)
        owns_source=not source or (source['label']=='User task capture' and source['text']==user_statement and source['reference'] is None)
        sources=[dict(id=source_id,label='User task capture',text=user_statement,source_type='user')] if owns_source else []
        # apply enforces immutable sources, unchanged existing projects/items, FK
        # ownership, evidence and an atomic exact-request receipt.
        value=self.apply(dict(id='task-create:'+request_id,sources=sources,projects=projects,items=items))
        return {**value,'item_ids':[task['id'] for task in tasks],'project_id':project_id}

    def update_task(self, item_id, version, changes, request_id, source_id, user_statement):
        """One user-reported task edit; never creates work or changes priorities."""
        allowed={'title','detail','status','blocker','dependencies','progress_note','steps'}
        if not isinstance(changes,dict) or not changes or set(changes)-allowed:
            raise ValueError('Task update accepts only title, detail, status, blocker, dependencies, progress_note and steps')
        for key,value in [('item ID',item_id),('request ID',request_id),('source ID',source_id),('user statement',user_statement)]:
            self.text(value,key)
        if len(user_statement)>2000:raise ValueError('Use only the relevant user statement, at most 2000 characters')
        if type(version) is not int or version<1:raise ValueError('Read the latest task version before updating')
        payload=json.dumps(dict(item_id=item_id,version=version,changes=changes,source_id=source_id,user_statement=user_statement),sort_keys=True)
        receipt='task-update:'+request_id
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            prior=self.db.execute('SELECT payload FROM batches WHERE id=?',(receipt,)).fetchone()
            if prior:
                if prior['payload']!=payload:raise Conflict('Task update request ID reused with different content')
                return {'id':item_id,'replayed':True}
            row=self.db.execute('SELECT * FROM items WHERE id=?',(item_id,)).fetchone()
            if not row or row['kind']!='task':raise ValueError('Task update requires an existing task')
            old=self.work_context(dict(row))
            if old['version']!=version:raise Conflict('This task changed; reread before updating')
            source=self.db.execute('SELECT * FROM sources WHERE id=?',(source_id,)).fetchone()
            if source:
                if source['source_type']!='user':raise ValueError('Task update requires original user evidence')
                self.validate_quote(source_id,user_statement)
            else:self.source(source_id,'User task update',user_statement,source_type='user')
            diff=dict(changes)
            if diff.get('status')=='cancelled':diff['status']='canceled'
            new={**old,**diff}
            self.validate_work(old,new,diff,source_id)
            if new==old:
                value={'id':item_id,'version':version,'unchanged':True}
            else:
                value=self._correct(item_id,version,diff,'User-reported task update',receipt,source_id,user_statement)
            self.db.execute('UPDATE sources SET processed=1 WHERE id=?',(source_id,))
            self.db.execute('INSERT INTO batches VALUES(?,?)',(receipt,payload))
            return value

    def save_work(self,item):
        self.db.execute('INSERT INTO item_steps VALUES(?,?) ON CONFLICT(item_id) DO UPDATE SET steps=excluded.steps',(item['id'],json.dumps(item['steps'])))
        self.db.execute('''INSERT INTO item_work VALUES(?,?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET
         next_action_id=excluded.next_action_id,blocker=excluded.blocker,dependencies=excluded.dependencies,
         progress_note=excluded.progress_note,completion_kind=excluded.completion_kind,verification_ref=excluded.verification_ref''',
         (item['id'],item['next_action_id'],item['blocker'],json.dumps(item['dependencies']),item['progress_note'],item['completion_kind'],item['verification_ref']))
