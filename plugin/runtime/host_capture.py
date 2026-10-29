"""Periodic capture owned by the authorized MCP host process, not an OS daemon."""
import json,os,threading,time
from capture_config import settings,data_home
from background_capture import Capture,lock

class HostCapture:
 def __init__(self,interval=300):
  self.interval=interval;self.stop_event=threading.Event();self.thread=None
 def start(self):
  if self.thread and self.thread.is_alive():return
  self.thread=threading.Thread(target=self.run,name='personal-todo-capture',daemon=True);self.thread.start()
 def stop(self):self.stop_event.set()
 def status(self,state,error=None):
  home=data_home();home.mkdir(parents=True,exist_ok=True,mode=0o700)
  path=home/'host-worker.json';tmp=home/('host-worker.'+str(os.getpid())+'.tmp')
  tmp.write_text(json.dumps({'pid':os.getpid(),'at':time.time(),'state':state,'error':error}));tmp.chmod(0o600);tmp.replace(path)
 def cycle(self):
  c=None
  try:
   self.status('checking');c=Capture();result=c.tick()
   self.status('idle', '; '.join(result.get('errors',[]))[:1000] or None)
  except BlockingIOError:self.status('idle') # another explicitly enabled worker owns this tick
  except Exception as exc:self.status('error',str(exc)[:500])
  finally:
   if c:c.close()
 def run(self):
  # Only one MCP instance leads per user. Followers take over after leader exits.
  while not self.stop_event.is_set():
   cfg=settings()
   if not cfg.get('enabled') or cfg.get('scheduler_mode')!='host':
    if self.stop_event.wait(self.interval):return
    continue
   try:
    with lock('host-worker.lock',blocking=False):
     try:
      while not self.stop_event.is_set():
       cfg=settings()
       if not cfg.get('enabled') or cfg.get('scheduler_mode')!='host':break
       self.cycle()
       if self.stop_event.wait(self.interval):break
     finally:self.status('stopped')
   except BlockingIOError:
    self.stop_event.wait(min(self.interval,30))

def host_status():
 try:
  value=json.loads((data_home()/'host-worker.json').read_text())
  alive=False
  try:os.kill(value['pid'],0);alive=True
  except (ProcessLookupError,PermissionError):pass
  return {**value,'alive':alive and value['state']!='stopped' and time.time()-value['at']<1200}
 except (OSError,ValueError,KeyError):return {'alive':False,'state':'not_started'}
