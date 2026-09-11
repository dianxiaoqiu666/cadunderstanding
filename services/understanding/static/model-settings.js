/* One local model configuration; no keys in browser storage or CAD exports. */
let modelConfigured=false;
const settingsDialog=$('modelSettingsDialog'),settingsForm=$('modelSettingsForm');
function settingsMessage(text,error=false){$('settingsMessage').textContent=text;$('settingsMessage').classList.toggle('error',error);}
function showSettings(value){
  modelConfigured=!!value.configured;
  $('modelName').value=value.name||'';
  $('modelBaseUrl').value=value.base_url||'';
  $('modelApiFormat').value=value.provider||'openai_compatible';
  $('modelId').value=value.model||'';
  $('modelApiKey').value='';
  $('modelApiKey').placeholder=value.api_key_set?'已保存，留空保持现有 Key':'输入按量计费 API Key';
  $('modelApiKey').required=!value.api_key_set;
  settingsMessage(value.message||'填写一个模型，保存后用于 CAD 理解。');
}
async function readModelSettings(){
  const response=await fetch('/v1/cad/model-settings',{cache:'no-store'});
  const value=await response.json();
  if(!response.ok)throw new Error(value.detail?.message||'无法读取模型设置。');
  return value;
}
$('openModelSettings').onclick=async()=>{
  settingsDialog.showModal();settingsMessage('正在读取设置…');$('saveModelSettings').disabled=true;
  try{showSettings(await readModelSettings());}catch(error){settingsMessage(error.message,true);}
  finally{$('saveModelSettings').disabled=false;}
};
$('closeModelSettings').onclick=()=>settingsDialog.close();
$('cancelModelSettings').onclick=()=>settingsDialog.close();
settingsDialog.addEventListener('close',()=>{$('modelApiKey').value='';});
settingsForm.onsubmit=async event=>{
  event.preventDefault();if(!settingsForm.reportValidity())return;
  const body={name:$('modelName').value.trim(),base_url:$('modelBaseUrl').value.trim(),
    provider:$('modelApiFormat').value,model:$('modelId').value.trim(),api_key:$('modelApiKey').value.trim()};
  for(const field of settingsForm.elements)field.disabled=true;
  settingsMessage('正在保存…');
  try{
    const response=await fetch('/v1/cad/model-settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const value=await response.json();if(!response.ok)throw new Error(value.detail?.message||'保存失败，请检查设置。');
    showSettings(value);
  }catch(error){settingsMessage(error.message,true);}
  finally{body.api_key='';$('modelApiKey').value='';for(const field of settingsForm.elements)field.disabled=false;}
};
readModelSettings().then(value=>{modelConfigured=!!value.configured;}).catch(()=>{});
