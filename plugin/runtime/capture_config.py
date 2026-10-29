"""Per-user installation paths; no project/user names in distributable config."""
import json,os
from pathlib import Path

def data_home():
 return Path(os.environ.get('PERSONAL_TODO_HOME') or Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'personal-todo').expanduser().resolve()

def settings():
 home=data_home()
 try:return json.loads((home/'settings.json').read_text())
 except FileNotFoundError:return {}

def save_settings(value):
 home=data_home();home.mkdir(parents=True,exist_ok=True,mode=0o700)
 path=home/'settings.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2));tmp.chmod(0o600);tmp.replace(path)

def db_path():return Path(settings().get('db',data_home()/'capture.sqlite'))
def workspace(default):return Path(settings().get('workspace',default))
