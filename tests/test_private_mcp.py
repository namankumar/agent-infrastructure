import json,tempfile,unittest
from pathlib import Path
from app import Store
from plugin.server import Server,LOCAL_ONLY_TOOLS
ROOT=Path(__file__).resolve().parents[1]
class PrivateMcpTests(unittest.TestCase):
 def test_cloud_excludes_host_access_but_shares_store(self):
  with tempfile.TemporaryDirectory() as d:
   db=Path(d)/'data.sqlite';s=Store(db);s.close()
   local=Server(ROOT,db);remote=Server(ROOT,db,cloud=True)
   names={t['name'] for t in remote.handle({'method':'tools/list'})['tools']}
   self.assertFalse(names & LOCAL_ONLY_TOOLS)
   for name in LOCAL_ONLY_TOOLS:
    with self.assertRaises(ValueError):remote.call(name,{})
   remote.call('capture_note',{'id':'shared-proof','text':'Fictional shared-store connection test'})
   state=local.call('read_workspace',{})['structuredContent']['state']
   self.assertTrue(any(s['id']=='shared-proof' for s in state['sources']))
   self.assertIn('save_record',names);self.assertIn('patch_table',names)
