"""Independent, no-inference audit of frozen requirements against saved run evidence."""
import argparse
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent


def audit(report):
    cases={c['id']:c for c in json.loads((HERE/'cases.json').read_text())['model_tasks']}
    expected_versions={'M01':3,'M02':2,'M03':4,'M04':4,'M05':2,'M06':2,'M07':2,'M08':2}
    results=[]
    for row in report['cases']:
        checks={}
        state=row['final_state']['projection']
        target=next(i for i in state['state']['items'] if i['id']=='invite')
        checks['exact_expected_revision']=target['version']==expected_versions[row['id']]
        checks['frozen_requirements_binding']=row['requirements_sha256']==hashlib.sha256((HERE/'requirements.md').read_bytes()).hexdigest()
        calls=[c for t in row['turns'] for c in t['calls']]
        checks['no_unrequested_artifact_writes']=not any(c['tool'] in ('save_record','update_record','patch_table','apply_extraction') for c in calls)
        for turn in row['turns']:
            for n,call in enumerate(turn['calls']):
                if call['tool'] in ('create_tasks','update_task'):
                    statement=call['arguments'].get('user_statement','')
                    checks[f'turn{turn["turn"]}_write{n}_original_clause']=bool(statement) and statement in turn['user_text']
                    checks[f'turn{turn["turn"]}_write{n}_read_before']=any(c['tool'] in ('read_workspace','read_chat') for c in turn['calls'][:n])
                    checks[f'turn{turn["turn"]}_write{n}_read_after']=any(c['tool'] in ('read_workspace','read_chat') for c in turn['calls'][n+1:])
        if row['id']=='M08':
            answer=row['turns'][-1]['answer']
            checks['reply_mentions_actual_indoor_room']='indoor' in answer['text'].lower() and 'room' in answer['text'].lower()
            status_sources={e['source_id'] for e in state['state']['events'] if e['item_id']=='invite' and json.loads(e['current'])['status']=='open'}
            checks['status_citation_belongs_to_this_task']=any(f.get('field')=='status' and f.get('subject_id')=='invite' and f.get('source_id') in status_sources for f in answer.get('facts',[]))
        results.append(dict(id=row['id'],trial=row['trial'],checks=checks,passed=all(checks.values())))
    return dict(requirements_version='2026-10-31',source_report_sha256=hashlib.sha256(json.dumps(report,sort_keys=True).encode()).hexdigest(),
                model_calls=0,rows=results,passed=sum(r['passed'] for r in results),total=len(results))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=audit(json.loads(args.report.read_text()))
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'passed':result['passed'],'total':result['total']}))
    return 0 if result['passed']==result['total'] else 1


if __name__=='__main__':raise SystemExit(main())
