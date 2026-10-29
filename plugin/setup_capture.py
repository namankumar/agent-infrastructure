#!/usr/bin/env python3
"""Explicit per-user setup; installs no credentials and never trusts hooks."""
import argparse,json,os,platform,plistlib,shutil,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'runtime'))
from capture_config import data_home,settings,save_settings
from app import Store

def setup(codex=None,db=None,transcripts=None,activate=True,mode="host"):
 if mode not in ("host","standalone"):raise ValueError("Unknown scheduler mode")
 home=data_home();home.mkdir(parents=True,exist_ok=True,mode=0o700)
 cfg=settings();first_enable=not cfg.get('enabled');cfg.update(enabled=True,enabled_at=cfg.get('enabled_at',time.time()),settle_seconds=300,max_chats=3)
 if db:
  if not Path(db).is_file():raise ValueError('Existing database path does not exist')
  cfg['db']=str(Path(db).resolve())
 if cfg.get('db') and not Path(cfg['db']).is_file():raise ValueError('Configured database missing; restore it before enabling capture')
 cfg['transcripts']=str(Path(transcripts or cfg.get('transcripts') or Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'sessions').resolve())
 cli=codex or cfg.get('codex_command') or shutil.which('codex')
 if not cli:
  candidate=Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
  if candidate.exists():cli=str(candidate)
 if not cli:raise ValueError('Install/sign in to Codex CLI before enabling background extraction')
 cfg['codex_command']=str(Path(cli).resolve())
 # Stable per-user runtime survives versioned plugin cache replacement.
 runtime=home/'runtime';runtime.mkdir(exist_ok=True)
 for source in (Path(__file__).parent/'runtime').iterdir():
  if source.is_file():shutil.copy2(source,runtime/source.name)
 save_settings(cfg);s=Store(cfg.get('db',home/'capture.sqlite'),require_existing=bool(cfg.get('db')));s.close()
 if first_enable:
  from background_capture import Capture
  capture=Capture()
  try:
   for path in Path(cfg['transcripts']).rglob('*.jsonl'):
    try:
     with path.open('rb') as handle:
      first=json.loads(handle.readline());meta=first.get('payload',{});chat=meta.get('id') or meta.get('session_id')
      size=path.stat().st_size;handle.seek(max(0,size-65536));tail=handle.read();last=tail.rfind(b'\n');offset=max(0,size-65536)+last+1 if last>=0 else 0
     with capture.db:capture.db.execute('INSERT OR IGNORE INTO capture_files(path,offset,chat_id,last_change) VALUES(?,?,?,?)',(str(path.resolve()),offset,chat,path.stat().st_mtime))
    except (OSError,ValueError):continue
  finally:capture.close()
 if mode=='host':
  # Remove prior standalone registrations; host supplies its existing file access.
  if platform.system()=='Darwin':
   registration=Path.home()/'Library/LaunchAgents/com.personal-todo.capture.plist'
   subprocess.run(['launchctl','bootout','gui/'+str(os.getuid()),str(registration)],capture_output=True)
   registration.unlink(missing_ok=True)
  elif platform.system()=='Linux':subprocess.run(['systemctl','--user','disable','--now','personal-todo-capture.timer'],capture_output=True)
  cfg.update(scheduler_mode='host',scheduler_activated=False);cfg.pop('scheduler_error',None);save_settings(cfg)
  return {'enabled':True,'scheduler':'host','data_home':str(home),'hooks':'optional; no approval required for catch-up','lifecycle':'Runs while the plugin host process is active; catches up when reopened'}
 cfg['scheduler_mode']='standalone'
 args=[sys.executable,str(runtime/'background_capture.py'),'tick']
 system=platform.system()
 if system=='Darwin':
  path=Path.home()/'Library/LaunchAgents/com.personal-todo.capture.plist';path.parent.mkdir(parents=True,exist_ok=True)
  plist={'Label':'com.personal-todo.capture','ProgramArguments':args,'StartInterval':300,'RunAtLoad':True,'EnvironmentVariables':{'PERSONAL_TODO_HOME':str(home),'PATH':os.environ.get('PATH','/usr/bin:/bin'),'CODEX_HOME':os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))},'StandardOutPath':'/dev/null','StandardErrorPath':str(home/'scheduler-error.log')}
  path.write_bytes(plistlib.dumps(plist));path.chmod(0o600)
  if activate:
   target='gui/'+str(os.getuid());subprocess.run(['launchctl','bootout',target,str(path)],capture_output=True)
   subprocess.run(['launchctl','bootstrap',target,str(path)],capture_output=True,check=True)
 elif system=='Linux':
  import shlex
  directory=Path.home()/'.config/systemd/user';directory.mkdir(parents=True,exist_ok=True)
  service=directory/'personal-todo-capture.service';timer=directory/'personal-todo-capture.timer'
  # systemd quoting uses double quotes; argv is fixed and paths escape quote/backslash.
  esc=lambda v:'"'+v.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
  service.write_text('[Service]\nType=oneshot\nEnvironment='+esc('PERSONAL_TODO_HOME='+str(home))+'\nExecStart='+' '.join(esc(x) for x in args)+'\n')
  timer.write_text('[Timer]\nOnBootSec=5min\nOnUnitActiveSec=5min\n[Install]\nWantedBy=timers.target\n')
  if activate:
   subprocess.run(['systemctl','--user','daemon-reload'],check=True,capture_output=True)
   subprocess.run(['systemctl','--user','enable','--now','personal-todo-capture.timer'],check=True,capture_output=True)
 else:raise ValueError('Automatic scheduling currently supports macOS and Linux only')
 cfg.pop('scheduler_error',None);cfg['scheduler_activated']=activate;cfg['scheduler_platform']=system;save_settings(cfg)
 return {'enabled':True,'scheduler':system,'activated':activate,'data_home':str(home),'hooks':'Codex /hooks review required; not auto-trusted'}
def disable():
 cfg=settings();cfg['enabled']=False;cfg['scheduler_activated']=False;save_settings(cfg)
 if platform.system()=='Darwin':
  subprocess.run(['launchctl','bootout','gui/'+str(os.getuid()),str(Path.home()/'Library/LaunchAgents/com.personal-todo.capture.plist')],capture_output=True)
 elif platform.system()=='Linux':subprocess.run(['systemctl','--user','disable','--now','personal-todo-capture.timer'],capture_output=True)
 return {'enabled':False,'data_preserved':True}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--disable',action='store_true');p.add_argument('--mode',choices=['host','standalone'],default='host');p.add_argument('--codex');p.add_argument('--db');p.add_argument('--transcripts');p.add_argument('--prepare-only',action='store_true');a=p.parse_args()
 print(json.dumps(disable() if a.disable else setup(a.codex,a.db,a.transcripts,not a.prepare_only,a.mode)))
