"""Canonical priority decisions and projections; no scheduler or semantic scoring."""
import hashlib
import json
from datetime import date, datetime, timezone
from monitor import monitor_status


class Conflict(ValueError):
    pass


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


class PriorityStore:
    def init_priorities(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS priority_revision(id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL);
        INSERT OR IGNORE INTO priority_revision VALUES(1,0);
        CREATE TABLE IF NOT EXISTS priorities(
          item_id TEXT NOT NULL REFERENCES items(id), authority TEXT NOT NULL,
          position INTEGER, reason TEXT NOT NULL, due_date TEXT,
          dependencies TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES sources(id),
          quote TEXT NOT NULL, updated TEXT NOT NULL,
          PRIMARY KEY(item_id,authority));
        CREATE TABLE IF NOT EXISTS priority_events(
          request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, version INTEGER NOT NULL,
          previous TEXT NOT NULL, current TEXT NOT NULL, created TEXT NOT NULL);
        ''')

    def prioritize(self, item_id, version, position, reason, request_id,
                   authority='assistant', source_id=None, quote=None,
                   due_date=None, dependencies=None, direct_user=False):
        """CLI user decisions require original evidence; HTTP direct choices create evidence."""
        if type(version) is not int or version < 0: raise ValueError('Invalid priority version')
        if position is not None and (type(position) is not int or position < 1):
            raise ValueError('Position must be a positive integer or null')
        if authority not in ('user','assistant'): raise ValueError('Invalid priority authority')
        if direct_user and authority != 'user': raise ValueError('Direct action must be a user choice')
        self.text(item_id,'item_id'); self.text(reason,'reason'); self.text(request_id,'request_id')
        if due_date is not None:
            if not isinstance(due_date,str): raise ValueError('Invalid due date')
            try: parsed=date.fromisoformat(due_date)
            except ValueError: raise ValueError('Due date must be YYYY-MM-DD')
            if parsed.isoformat()!=due_date: raise ValueError('Due date must be YYYY-MM-DD')
        dependencies=[] if dependencies is None else dependencies
        if not isinstance(dependencies,list) or any(not isinstance(d,str) for d in dependencies):
            raise ValueError('Dependencies must be item IDs')
        if len(set(dependencies))!=len(dependencies) or item_id in dependencies:
            raise ValueError('Duplicate or self dependency')
        payload=encode(dict(item_id=item_id,version=version,position=position,reason=reason,
                            authority=authority,source_id=source_id,quote=quote,due_date=due_date,
                            dependencies=dependencies,direct_user=direct_user))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            prior=self.db.execute('SELECT * FROM priority_events WHERE request_id=?',(request_id,)).fetchone()
            if prior:
                if prior['payload']!=payload: raise Conflict('Priority request ID reused with different content')
                return {'replayed':True,'version':prior['version']}
            actual=self.db.execute('SELECT version FROM priority_revision WHERE id=1').fetchone()[0]
            if actual!=version: raise Conflict('Priorities changed; refresh before saving')
            row=self.db.execute('SELECT * FROM items WHERE id=?',(item_id,)).fetchone()
            if not row: raise ValueError('Unknown item')
            if row['kind'] not in ('task','goal'): raise ValueError('Only tasks and goals can be prioritized')
            if position is not None and row['status'] in ('done','canceled'): raise ValueError('Completed or canceled tasks cannot be prioritized')
            for dep in dependencies:
                target=self.db.execute('SELECT kind FROM items WHERE id=?',(dep,)).fetchone()
                if not target or target['kind']!='task': raise ValueError('Dependencies must reference existing tasks')
            old=[dict(r) for r in self.db.execute('SELECT * FROM priorities WHERE authority=? ORDER BY position,item_id',(authority,))]
            graph={r['item_id']:json.loads(r['dependencies']) for r in self.db.execute('SELECT item_id,dependencies FROM item_work')}
            for r in old:
                if r['position'] is not None:graph[r['item_id']]=list(dict.fromkeys(graph.get(r['item_id'],[])+json.loads(r['dependencies'])))
            item_deps=self.db.execute('SELECT dependencies FROM item_work WHERE item_id=?',(item_id,)).fetchone()
            graph[item_id]=list(dict.fromkeys((json.loads(item_deps[0]) if item_deps else [])+(dependencies if position is not None else [])))
            def visit(node,path):
                if node in path: raise ValueError('Dependency cycle')
                for dep in graph.get(node,[]): visit(dep,path|{node})
            for node in graph: visit(node,set())
            if direct_user:
                if source_id is not None or quote is not None: raise ValueError('Direct user choices use their own action evidence')
                sid='priority:'+request_id
                quote=reason
                self.source(sid,'Direct priority choice in local workspace',reason,source_type='user')
                self.db.execute('UPDATE sources SET processed=1 WHERE id=?',(sid,))
            else:
                if not source_id or not quote: raise ValueError('Saved source and exact quote required')
                self.validate_quote(source_id,quote);sid=source_id
                source=self.db.execute('SELECT source_type FROM sources WHERE id=?',(sid,)).fetchone()
                if authority=='user' and source['source_type']!='user':
                    raise ValueError('Confirmed priority needs original user evidence')
            timestamp=datetime.now(timezone.utc).isoformat()
            self.db.execute('''INSERT INTO priorities VALUES(?,?,?,?,?,?,?,?,?)
              ON CONFLICT(item_id,authority) DO UPDATE SET position=excluded.position,
              reason=excluded.reason,due_date=excluded.due_date,dependencies=excluded.dependencies,
              source_id=excluded.source_id,quote=excluded.quote,updated=excluded.updated''',
              (item_id,authority,position,reason,due_date,encode(dependencies),sid,quote,timestamp))
            eligible={r['id'] for r in self.db.execute("SELECT id FROM items WHERE kind IN ('task','goal') AND status NOT IN ('done','canceled')")}
            ordered=[r['item_id'] for r in old if r['position'] is not None and r['item_id']!=item_id and r['item_id'] in eligible]
            hidden=[r['item_id'] for r in old if r['position'] is not None and r['item_id']!=item_id and r['item_id'] not in eligible]
            if position is not None: ordered.insert(min(position-1,len(ordered)),item_id)
            for rank,key in enumerate(ordered+hidden,1):
                self.db.execute('UPDATE priorities SET position=? WHERE item_id=? AND authority=?',(rank,key,authority))
            self.db.execute('UPDATE priority_revision SET version=? WHERE id=1',(version+1,))
            current=[dict(r) for r in self.db.execute('SELECT * FROM priorities WHERE authority=? ORDER BY position,item_id',(authority,))]
            self.db.execute('INSERT INTO priority_events VALUES(?,?,?,?,?,?)',
                            (request_id,payload,version+1,encode(old),encode(current),timestamp))
        return {'replayed':False,'version':version+1}

    def priority_context(self):
        # One read transaction gives the monitor a coherent cross-table projection.
        with self.db:
            self.db.execute('BEGIN')
            version=self.db.execute('SELECT version FROM priority_revision WHERE id=1').fetchone()[0]
            items={r['id']:self.work_context(dict(r)) for r in self.db.execute('SELECT * FROM items')}
            projects={r['id']:dict(r) for r in self.db.execute('SELECT * FROM projects')}
            sources={r['id']:dict(r) for r in self.db.execute('SELECT * FROM sources')}
            records=[dict(r) for r in self.db.execute('SELECT * FROM priorities ORDER BY position,item_id')]
            evidence=[dict(r) for r in self.db.execute('SELECT * FROM evidence')]
        result={'version':version,'confirmed':[],'recommendations':[],'unranked':[],
                'inactive':[],'coverage':'Local captured records only; not a complete backlog.',
                'monitor_policy':'confirmed_only'}
        chosen={r['item_id'] for r in records if r['authority']=='user' and r['position'] is not None}
        confirmed_deps={r['item_id']:json.loads(r['dependencies']) for r in records if r['authority']=='user' and r['position'] is not None}
        def availability(item,extra_deps=()):
            blockers=[]
            if item['status']!='open': blockers.append('Item is '+item['status'])
            if item['kind'] not in ('task','goal'): blockers.append('Item is no longer a task or goal')
            if item['blocker'].strip(): blockers.append(item['blocker'])
            deps=list(dict.fromkeys(item['dependencies']+confirmed_deps.get(item['id'],[])+list(extra_deps)))
            unfinished=[d for d in deps if d not in items or items[d]['kind']!='task' or items[d]['status']!='done']
            if unfinished:blockers.append('Unfinished dependencies')
            return blockers,deps,unfinished
        for r in records:
            item=items[r['item_id']]
            blockers,deps,unavailable=availability(item,json.loads(r['dependencies']))
            action=item if item['kind']=='task' else items.get(item['next_action_id'])
            if item['kind']=='goal':
                if not action or action['kind']!='task' or action['project_id']!=item['project_id']:
                    blockers.append('No executable next action linked');action=None
                else:
                    action_blockers,action_deps,action_unfinished=availability(action)
                    blockers.extend('Next action: '+b for b in action_blockers)
                    unavailable=list(dict.fromkeys(unavailable+action_unfinished))
            record={**r,'dependencies':json.loads(r['dependencies']),'effective_dependencies':deps,'item':item,'project':projects.get(item['project_id'],{'id':None,'name':'','objective':''}),
                    'source_label':sources[r['source_id']]['label'],
                    'item_evidence':[e for e in evidence if e['item_id']==item['id']],
                    'next_action':action,'actionable':not blockers and action is not None and r['position'] is not None,
                    'blockers':blockers,'unfinished_dependencies':unavailable}
            if r['position'] is None or item['status'] in ('done','canceled') or item['kind'] not in ('task','goal'):
                result['inactive'].append(record)
            else:
                result['confirmed' if r['authority']=='user' else 'recommendations'].append(record)
        result['unranked']=[i for i in items.values() if i['kind'] in ('task','goal') and i['status'] not in ('done','canceled') and i['id'] not in chosen]
        for key in ('confirmed','recommendations'):
            for position,record in enumerate(result[key],1): record['position']=position
        # A user row, even a dismissal, wins over later inference for the same item.
        overridden={r['item_id'] for r in records if r['authority']=='user'}
        result['effective_focus']=result['confirmed']+[r for r in result['recommendations'] if r['item_id'] not in overridden]
        result['next_action']=next((r['next_action'] for r in result['confirmed'] if r['actionable']),None)
        result['alignment_ready']=any(r['actionable'] for r in result['confirmed'])
        result['gap']=None if result['alignment_ready'] else 'No actionable confirmed priorities. Do not infer drift.'
        result['fingerprint']=hashlib.sha256(encode(result).encode()).hexdigest()
        monitor=monitor_status(self.monitor_config_path,self.monitor_state_path)
        result['schedule_status']=monitor['schedule_status'];result['monitor']=monitor
        return result
