import copy
import json
import tempfile
import unittest
from pathlib import Path
from app import Store, Conflict


def fixture(store):
    store.apply({'id':'priority-fixture','sources':[{'id':'s','label':'Priority test conversation','source_type':'user','text':'Ship the draft. Call the client. Maybe a recorder. Draft first, then the call.'}],
      'projects':[{'id':'p','name':'Client work','objective':'Deliver the agreed work','source_id':'s','quote':'Ship the draft.'}],
      'items':[{'id':key,'project_id':'p','kind':kind,'title':title,'detail':'','evidence':[{'source_id':'s','quote':quote}]} for key,kind,title,quote in [('a','task','Ship draft','Ship the draft.'),('b','task','Call client','Call the client.'),('c','idea','Recorder','Maybe a recorder.')]]})

class PriorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'capture.sqlite';self.s=Store(self.path);fixture(self.s)
    def tearDown(self): self.s.close();self.tmp.cleanup()
    def choose(self,key='a',position=1,**extra):
        args=dict(item_id=key,version=self.s.priority_context()['version'],position=position,reason='Draft first',request_id=f'r{self.s.priority_context()["version"]}',authority='user',source_id='s',quote='Draft first, then the call.')
        args.update(extra);return self.s.prioritize(**args)
    def test_initial_state_unranked_and_ideas_excluded(self):
        p=self.s.priority_context();self.assertFalse(p['alignment_ready']);self.assertEqual(p['confirmed'],[])
        self.assertEqual({i['id'] for i in p['unranked']},{'a','b'})
        with self.assertRaises(ValueError):self.choose('c')
        self.assertEqual(self.s.priority_context()['version'],0)
    def test_reorder_remove_restart_and_history(self):
        self.choose();self.choose('b');self.choose('a',1)
        self.s.close();self.s=Store(self.path)
        self.assertEqual([r['item_id'] for r in self.s.priority_context()['confirmed']],['a','b'])
        self.choose('a',None)
        self.assertEqual([r['position'] for r in self.s.priority_context()['confirmed']],[1])
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM priority_events').fetchone()[0],4)
    def test_assistant_cannot_replace_user_choice(self):
        self.choose('a',1)
        self.choose('b',1,authority='assistant')
        self.choose('a',2,authority='assistant',reason='Consider later')
        p=self.s.priority_context();self.assertEqual(p['confirmed'][0]['item_id'],'a');self.assertEqual(p['confirmed'][0]['reason'],'Draft first')
        self.assertEqual(len(p['recommendations']),2)
    def test_stale_conflict_and_idempotent_replay(self):
        args=dict(item_id='a',version=0,position=1,reason='Draft first',request_id='same',authority='user',source_id='s',quote='Draft first, then the call.')
        self.s.prioritize(**args);self.assertTrue(self.s.prioritize(**args)['replayed'])
        with self.assertRaises(Conflict):self.s.prioritize(**{**args,'reason':'Changed'})
        other=Store(self.path)
        try:
            with self.assertRaises(Conflict):other.prioritize(**{**args,'request_id':'stale'})
        finally:other.close()
        self.assertEqual(self.s.priority_context()['version'],1)
    def test_invalid_source_rolls_back_and_direct_action_records_evidence(self):
        with self.assertRaises(ValueError):self.choose(quote='Invented')
        self.assertEqual(self.s.priority_context()['version'],0)
        self.s.prioritize('a',0,1,'My first priority','direct',authority='user',direct_user=True)
        r=self.s.priority_context()['confirmed'][0];self.assertEqual(r['quote'],'My first priority')
        self.assertIn('Direct priority choice',r['source_label'])
    def test_dependencies_waiting_done_and_fingerprint(self):
        self.choose('a',dependencies=['b'],due_date='2026-10-20');p=self.s.priority_context()
        self.assertFalse(p['alignment_ready']);self.assertEqual(p['confirmed'][0]['unfinished_dependencies'],['b'])
        self.s.correct('b',1,{'status':'done'},'Finished','done')
        q=self.s.priority_context();self.assertTrue(q['alignment_ready']);self.assertNotEqual(p['fingerprint'],q['fingerprint'])
        self.s.correct('a',1,{'status':'waiting'},'Waiting on approval','wait')
        self.assertFalse(self.s.priority_context()['alignment_ready'])
    def test_invalid_dependencies_dates_and_cycles(self):
        for extra in [{'dependencies':['a']},{'dependencies':['c']},{'dependencies':['missing']},{'due_date':'2026-02-30'},{'due_date':'20261020'},{'position':True}]:
            with self.assertRaises(ValueError):self.choose(**extra)
        self.choose('a',dependencies=['b'])
        with self.assertRaises(ValueError):self.choose('b',dependencies=['a'])
        self.assertEqual(self.s.priority_context()['version'],1)
    def test_non_task_correction_never_creates_false_alignment(self):
        self.choose();self.s.correct('a',1,{'kind':'idea'},'This is only an idea','idea')
        p=self.s.priority_context();self.assertFalse(p['alignment_ready']);self.assertEqual(p['confirmed'],[])
        self.assertEqual(len(p['inactive']),1)
    def test_recommendations_alone_not_ready(self):
        self.choose(authority='assistant');self.assertFalse(self.s.priority_context()['alignment_ready'])
    def test_hidden_records_do_not_consume_active_positions(self):
        self.choose('a');self.choose('b',2)
        self.s.correct('a',1,{'status':'done'},'Done','complete-a')
        self.assertEqual(self.s.priority_context()['confirmed'][0]['position'],1)
        self.s.capture('new','New task','Send the invoice.')
        self.s.apply({'id':'new-task','sources':[],'projects':[],'items':[{'id':'d','project_id':'p','kind':'task','title':'Invoice','detail':'','evidence':[{'source_id':'new','quote':'Send the invoice.'}]}]})
        self.choose('d',2)
        self.assertEqual([r['item_id'] for r in self.s.priority_context()['confirmed']],['b','d'])

    def test_missing_source_rejected_even_for_user_claim(self):
        with self.assertRaises(ValueError):self.choose(source_id=None,quote=None)
        self.assertEqual(self.s.priority_context()['version'],0)

if __name__=='__main__':unittest.main()
