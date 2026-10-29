"""Read local scheduler configuration and run receipts; never starts a timer."""
import json
import re
from pathlib import Path

AUTOMATION_ID='hourly-priority-monitor'
THREAD_ID='01a141b5-586f-7833-a8a3-d04538d4f197'

def monitor_status(config_path, state_path):
    result={'schedule_status':'not_configured','automation_id':AUTOMATION_ID,
            'last_check':None,'alignment_status':'not_checked'}
    if config_path is not None:
        try:
            # Read only the app's flat, JSON-compatible quoted fields (Python 3.9).
            # Unknown/duplicate formatting fails closed; this is not a general TOML parser.
            text=Path(config_path).read_text();config={}
            for key in ('id','kind','target_thread_id','status','rrule'):
                values=re.findall(r'^'+key+r'\s*=\s*("(?:[^"\\]|\\.)*")\s*$',text,re.M)
                if len(values)!=1:raise ValueError('Unsupported scheduler config')
                config[key]=json.loads(values[0])
            if config.get('id')!=AUTOMATION_ID or config.get('kind')!='heartbeat' or config.get('target_thread_id')!=THREAD_ID:
                result['schedule_status']='unverified'
            else:
                result['schedule_status']={'ACTIVE':'active','PAUSED':'paused'}.get(config.get('status'),'unverified')
                result['cadence']=config.get('rrule')
        except FileNotFoundError:pass
        except (OSError,ValueError):result['schedule_status']='unverified'
    try:
        state=json.loads(Path(state_path).read_text())
        last=state.get('last_run')
        if isinstance(last,dict):
            result['last_check']=last.get('finished_at')
            result['alignment_status']=last.get('outcome','not_checked')
            result['run_trigger']=last.get('trigger')
    except (OSError,ValueError,AttributeError):pass
    return result
