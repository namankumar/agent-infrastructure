import copy
import json
import tempfile
import unittest
from pathlib import Path
from app import Store, Conflict

class StoreTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'test.sqlite';self.s=Store(self.path)
  self.batch={'id':'b1','sources':[{'id':'s1','label':'User test source','text':'Build a local proof. Maybe add a recorder later.'}],'projects':[{'id':'p1','name':'Capture','objective':'Build a local proof','source_id':'s1','quote':'Build a local proof.'}],'items':[{'id':'i1','project_id':'p1','kind':'task','title':'Build local proof','detail':'No stated deadline','evidence':[{'source_id':'s1','quote':'Build a local proof.'}]},{'id':'i2','project_id':'p1','kind':'idea','title':'Recorder','detail':'Tentative future idea','evidence':[{'source_id':'s1','quote':'Maybe add a recorder later.'}]}]}
 def tearDown(self): self.s.close();self.tmp.cleanup()
 def test_restart_and_replay(self):
  self.s.apply(self.batch);self.s.close();self.s=Store(self.path)
  self.assertTrue(self.s.apply(self.batch)['replayed']);self.assertEqual(len(self.s.snapshot()['items']),2)
 def test_failed_quote_rolls_back_whole_batch(self):
  self.batch['items'][1]['evidence'][0]['quote']='Invented deadline tomorrow'
  with self.assertRaises(ValueError):self.s.apply(self.batch)
  self.assertEqual(self.s.snapshot()['sources'],[]);self.assertEqual(self.s.snapshot()['items'],[])
 def test_reused_batch_conflicts(self):
  self.s.apply(self.batch);self.batch['items'][0]['title']='Something else'
  with self.assertRaises(Conflict):self.s.apply(self.batch)
 def test_stale_correction_preserves_history(self):
  self.s.apply(self.batch);self.s.correct('i1',1,{'title':'Prove capture'},'Better title','r1')
  with self.assertRaises(Conflict):self.s.correct('i1',1,{'title':'Overwritten'},'Stale edit','r2')
  state=self.s.snapshot();self.assertEqual(state['items'][0]['title'],'Prove capture');self.assertEqual(len(state['events']),3)
  self.assertTrue(self.s.correct('i1',1,{'title':'Prove capture'},'Better title','r1')['replayed'])
 def test_idea_not_done_and_no_dates(self):
  self.s.apply(self.batch)
  with self.assertRaises(ValueError):self.s.correct('i2',1,{'status':'done'},'Done','r1')
  self.assertEqual(self.s.snapshot()['items'][1]['kind'],'idea');self.assertNotIn('due',self.s.snapshot()['items'][0])
 def test_missing_project_rolls_back(self):
  self.batch['items'][1]['project_id']='absent'
  with self.assertRaises(Exception):self.s.apply(self.batch)
  self.assertEqual(self.s.snapshot()['items'],[])
 def test_source_immutable_and_pending(self):
  self.s.capture('note','Note','hello')
  with self.assertRaises(Conflict):self.s.capture('note','Note','changed')
  self.assertEqual(self.s.snapshot()['sources'][0]['processed'],0)
 def test_semantic_id_reuse_no_duplicate(self):
  self.s.apply(self.batch);other=copy.deepcopy(self.batch);other['id']='b2';self.s.apply(other)
  self.assertEqual(len(self.s.snapshot()['items']),2);self.assertEqual(len(self.s.snapshot()['events']),2)
 def test_done_is_report_not_verified_execution(self):
  self.s.apply(self.batch);self.s.correct('i1',1,{'status':'done'},'I completed it','r1')
  self.assertIn('user-reported',self.s.snapshot()['sources'][-1]['label'])

if __name__=='__main__':unittest.main()
