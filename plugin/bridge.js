const rpcPending=new Map();let rpcId=0;
function hostRequest(method,params){return new Promise((resolve,reject)=>{
 const id=++rpcId;const timer=setTimeout(()=>{rpcPending.delete(id);reject(Error('The desktop connection timed out. Your draft is still here; try again.'))},15000);
 rpcPending.set(id,{resolve,reject,timer});window.parent.postMessage({jsonrpc:'2.0',id,method,params},'*');
})}
window.addEventListener('message',event=>{
 if(event.source!==window.parent)return;const m=event.data;if(!m||m.jsonrpc!=='2.0')return;
 if(rpcPending.has(m.id)){const p=rpcPending.get(m.id);rpcPending.delete(m.id);clearTimeout(p.timer);m.error?p.reject(Error(m.error.message||'Desktop request failed')):p.resolve(m.result)}
});
async function connectHost(){
 const initialized=await hostRequest('ui/initialize',{appInfo:{name:'Personal tasks',version:'0.7.2'},appCapabilities:{availableDisplayModes:['inline','fullscreen']},protocolVersion:'2026-01-26'});
 window.dispatchEvent(new CustomEvent('todo-host-ready',{detail:initialized}));
 window.parent.postMessage({jsonrpc:'2.0',method:'ui/notifications/initialized',params:{}},'*');
 let lastHeight=0,queued=false;
 new ResizeObserver(()=>{if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;const height=Math.min(900,Math.max(180,document.body.scrollHeight));if(height===lastHeight)return;lastHeight=height;window.parent.postMessage({jsonrpc:'2.0',method:'ui/notifications/size-changed',params:{height}},'*')})}).observe(document.body);
}
let workspaceRead=null;
async function api(path,data){
 const map={'/api/save-preferences':'set_save_preferences','/api/tables':'list_tables','/api/table':'get_table','/api/table-edit':'ui_table','/api/board':'ui_board','/api/records':'find_records','/api/record':'get_record','/api/record-edit':'ui_record','/api/capture':'capture_note','/api/correct':'ui_correct','/api/prioritize':'ui_prioritize'};
 let result;
 if(!data){
  if(!workspaceRead)workspaceRead=hostRequest('tools/call',{name:'read_workspace',arguments:{}}).finally(()=>{workspaceRead=null});
  result=await workspaceRead;
 }else result=await hostRequest('tools/call',{name:map[path],arguments:['/api/save-preferences','/api/tables','/api/table','/api/capture','/api/records','/api/record'].includes(path)?data:{payload:data}});
 if(result.isError)throw Error(result.content?.find(c=>c.type==='text')?.text||'Could not save');
 const value=result.structuredContent||JSON.parse(result.content.find(c=>c.type==='text').text);
 return data?value:path==='/api/state'?value.state:path==='/api/save-preferences'?value.save_preferences:value.priorities;
}
