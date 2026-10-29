"""Per-workspace save routing. Opt-in never enables transcript access or hooks."""
import json
from datetime import datetime,timezone
from priorities import Conflict

class SavePreferences:
 def init_save_preferences(self):
  self.db.execute('CREATE TABLE IF NOT EXISTS save_preferences (id INTEGER PRIMARY KEY CHECK(id=1), destination TEXT NOT NULL, version INTEGER NOT NULL, evidence TEXT NOT NULL, updated TEXT NOT NULL)')
  self.db.commit()
 def get_save_preferences(self):
  row=self.db.execute('SELECT * FROM save_preferences WHERE id=1').fetchone()
  return dict(row) if row else {'destination':'unset','version':0,'evidence':None,'updated':None}
 def set_save_preferences(self,destination,version,evidence):
  if destination not in ('todo','ask'):raise ValueError('Choose todo or ask')
  if not isinstance(evidence,str) or not evidence.strip():raise ValueError('Explicit user choice is required')
  if not isinstance(version,int) or isinstance(version,bool):raise ValueError('Observed version required')
  with self.db:
   self.db.execute('BEGIN IMMEDIATE')
   current=self.get_save_preferences()
   if current['version']!=version:raise Conflict('Save preference changed; reload before updating')
   self.db.execute('INSERT OR REPLACE INTO save_preferences VALUES(1,?,?,?,?)',(destination,version+1,evidence,datetime.now(timezone.utc).isoformat()))
  return self.get_save_preferences()
