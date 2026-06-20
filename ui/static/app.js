/* ═══════════════════════════════════════════════════
   JobLab · 暗夜星图 SPA
   视图切换 / 对话 / 雷达 / 研究 / 用户中心
   ═══════════════════════════════════════════════════ */

// ── 星空粒子 ──
(function stars(){
  const c=document.getElementById('starCanvas'),ctx=c.getContext('2d');
  let w,h,particles=[];
  function resize(){w=c.width=window.innerWidth;h=c.height=window.innerHeight;}
  resize();window.addEventListener('resize',resize);
  for(let i=0;i<80;i++)particles.push({x:Math.random()*w,y:Math.random()*h,r:Math.random()*1.2+0.3,a:Math.random()*0.5+0.2,s:Math.random()*0.3+0.1});
  function draw(){
    ctx.clearRect(0,0,w,h);
    for(const p of particles){
      ctx.beginPath();ctx.arc(p.x,p.y,p.r,0,Math.PI*2);
      ctx.fillStyle=`rgba(255,255,255,${p.a})`;ctx.fill();
      p.y-=p.s;if(p.y<-5){p.y=h+5;p.x=Math.random()*w;}
    }
    requestAnimationFrame(draw);
  }
  draw();
})();

// ── 工具 ──
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function fmt(s,id){
  // 先转义防止 XSS，再安全替换标记格式
  let t=esc(s);
  return t
    .replace(/\n/g,'<br>')
    .replace(/\[(\d+)\]/g,'<sup class="cite-badge" data-cite="$1">[$1]</sup>')
    .replace(/~~([^~]+)~~/g,'<del class="unverified">$1</del>');
}

// ── 状态 ──
let threadId=crypto.randomUUID(),userId=parseInt(localStorage.getItem('js_user_id')||'0');
let currentView='gap';
let allMessages=[];
const convMessageCache=new Map();
const deletedThreads=new Set();

// ── 画像分析状态 ──
let currentJobProfile=null;
let currentCandidateProfile=null;
let currentFitReport=null;
let currentAnalysisMode='';
let currentRuleScore=0;
let profileAnalysisLoading=false;
let profileAnalysisError='';
let parsedResumeText='';  // 上传文件解析出的简历文本
let bossCaptureResult=null;  // Boss 采集结果

// ── API ──
const api={
  async chat(msg,tid){const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg,thread_id:tid,user_id:userId})});return r.json();},
  async users(){const r=await fetch('/users');const d=await r.json();return d.users||[];},
  async deleteUser(uid){const r=await fetch('/user/'+uid,{method:'DELETE'});return r.json();},
  async deleteConversation(tid){const r=await fetch('/conversation/'+encodeURIComponent(tid)+'?user_id='+userId,{method:'DELETE'});return r.json();},
  async conversations(uid){const r=await fetch('/conversations?user_id='+uid);const d=await r.json();return d.conversations||[];},
  async analyzedJobs(){const r=await fetch('/skill_rank/_jobs');const d=await r.json();return d.jobs||[];},
  async skillRank(job,n=15){const r=await fetch('/skill_rank/'+encodeURIComponent(job)+'?top_n='+n+'&user_id='+userId);return r.json();},
  async jobProfile(job,n=20){const r=await fetch('/job_profile/'+encodeURIComponent(job)+'?top_n='+n);return r.json();},
  async skillGap(job,userSkills,n=15,userProfile=[]){const r=await fetch('/skill_gap',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_name:job,user_skills:userSkills,user_profile:userProfile,top_n:n})});return r.json();},
  async screeningReport(job,resumeText='',userProfile=[],n=20){const r=await fetch('/screening_report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_name:job,resume_text:resumeText,user_profile:userProfile,top_n:n})});return r.json();},
  async resumeProfileText(text){const r=await fetch('/profile/resume_text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({resume_text:text})});return r.json();},
  async resumeProfileFile(file,text=''){const fd=new FormData();if(file)fd.append('file',file);if(text)fd.append('resume_text',text);const r=await fetch('/profile/resume',{method:'POST',body:fd});return r.json();},
  async convMsgs(tid){const r=await fetch('/conversation/'+encodeURIComponent(tid));const d=await r.json();return d.messages||[];},
  async stats(){const r=await fetch('/stats');return r.json();},
  async analyzeJob(job){const r=await fetch('/analyze_job',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_name:job})});return r.json();},
  async task(taskId){const r=await fetch('/task/'+taskId);return r.json();},
  async analyzeJobProfile(job,n=20){const r=await fetch('/job_profiles/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_name:job,top_n:n})});return r.json();},
  async analyzeCandidateProfile(resumeText,userId){const r=await fetch('/candidate_profiles/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:userId,resume_text:resumeText})});return r.json();},
  async parseResume(file){const fd=new FormData();fd.append('file',file);const r=await fetch('/resume/parse',{method:'POST',body:fd});return r.json();},
  async candidateProfileFromText(text){return this.analyzeCandidateProfile(text,userId);},
  async createFitAnalysis(userId,jobProfileId,candidateProfileId){const r=await fetch('/fit_analysis_reports',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:userId,job_profile_id:jobProfileId,candidate_profile_id:candidateProfileId})});return r.json();},
  async submitEvaluation(data){const r=await fetch('/profile_evaluations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});return r.json();},
  async getEvaluationSummary(targetType){const r=await fetch('/profile_evaluations/summary?target_type='+encodeURIComponent(targetType||''));return r.json();},
  async listFitReports(uid,jobName,limit,offset){const p=new URLSearchParams();if(uid)p.set('user_id',uid);if(jobName)p.set('job_name',jobName);if(limit)p.set('limit',limit);if(offset)p.set('offset',offset);const r=await fetch('/fit_analysis_reports?'+p);return r.json();},
  async getFitReport(id,uid=0){const p=new URLSearchParams();if(uid)p.set('user_id',uid);const qs=p.toString();const r=await fetch('/fit_analysis_reports/'+id+(qs?'?'+qs:''));return r.json();},
  async deleteFitReport(id){const r=await fetch('/fit_analysis_reports/'+id+'?user_id='+userId,{method:'DELETE'});return r.json();},
  async rerunFitReport(id){const r=await fetch('/fit_analysis_reports/'+id+'/rerun',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:userId})});return r.json();},
  async bossCapture(jobName,extraJobKeywords,city,maxJobs,filters){const r=await fetch('/jd_sources/boss/capture',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_name:jobName,extra_job_keywords:extraJobKeywords,city,max_jobs:maxJobs,filters})});return r.json();},
  async bossManualImport(jobName,jdText,title,company){const r=await fetch('/jd_sources/boss/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_name:jobName,jd_text:jdText,title:title||'',company:company||''})});return r.json();},
  async rebuildJobProfile(jobName,sourcePlatform,topN){const r=await fetch('/job_profiles/rebuild',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_name:jobName,source_platform:sourcePlatform||'boss',top_n:topN||20})});return r.json();},
  async startBossBrowser(){const r=await fetch('/boss/browser/start',{method:'POST'});return r.json();},
  async stopBossBrowser(){const r=await fetch('/boss/browser/stop',{method:'POST'});return r.json();},
  async getBossBrowserStatus(){const r=await fetch('/boss/browser/status');return r.json();},
  async openBossLogin(){const r=await fetch('/boss/browser/open-login',{method:'POST'});return r.json();},
  async askAdvisor(reportId,question){const r=await fetch('/fit_analysis_reports/'+reportId+'/advisor',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:userId,question:question})});return r.json();},
};

function lastThreadKey(uid=userId){return 'last_thread_id_'+uid;}
function savedUsers(){
  try{return JSON.parse(localStorage.getItem('js_saved_users')||'[]');}catch(_){return[];}
}
function setSavedUsers(users){
  localStorage.setItem('js_saved_users',JSON.stringify(users));
}
function removeSavedUser(uid){
  setSavedUsers(savedUsers().filter(u=>u.id!==uid));
}
async function loadConversationMessages(tid,{fresh=false}={}){
  if(!fresh&&convMessageCache.has(tid))return convMessageCache.get(tid);
  const msgs=await api.convMsgs(tid);
  convMessageCache.set(tid,msgs);
  return msgs;
}

// ── DOM ──
const $=id=>document.getElementById(id);
const el={
  chatInput:$('chatInput'),btnSend:$('btnSend'),msgList:$('messageList'),
  convList:$('convList'),btnNewChat:$('btnNewChat'),
  radarInput:$('radarInput'),btnRadarSearch:$('btnRadarSearch'),radarQuickTags:$('radarQuickTags'),radarBody:$('radarBody'),
  gapJobInput:$('gapJobInput'),btnGapLoad:$('btnGapLoad'),gapQuickTags:$('gapQuickTags'),gapSkillList:$('gapSkillList'),
  gapMarketMeta:$('gapMarketMeta'),gapExtraInput:$('gapExtraInput'),btnGapAnalyze:$('btnGapAnalyze'),btnGapClear:$('btnGapClear'),gapResult:$('gapResult'),
  btnGapClearFeedback:$('btnGapClearFeedback'),resumeTextInput:$('resumeTextInput'),resumeFileInput:$('resumeFileInput'),
  btnResumeProfile:$('btnResumeProfile'),profileSkillList:$('profileSkillList'),
  researchInput:$('researchInput'),btnResearch:$('btnResearch'),researchTimeline:$('researchTimeline'),researchBody:$('researchBody'),
  userAvatar:$('userAvatar'),userName:$('userName'),userMeta:$('userMeta'),userStats:$('userStats'),
  toastContainer:$('toastContainer'),
  btnToggleBossCapture:$('btnToggleBossCapture'),bossCaptureBody:$('bossCaptureBody'),
  bossJobInput:$('bossJobInput'),bossExtraJobInput:$('bossExtraJobInput'),bossCityInput:$('bossCityInput'),
  bossExpFilter:$('bossExpFilter'),bossEduFilter:$('bossEduFilter'),
  bossCompanySizeFilter:$('bossCompanySizeFilter'),bossHrActivityFilter:$('bossHrActivityFilter'),
  bossMaxJobs:$('bossMaxJobs'),
  btnBossCapture:$('btnBossCapture'),bossManualJdInput:$('bossManualJdInput'),
  btnBossManualImport:$('btnBossManualImport'),bossCaptureStatus:$('bossCaptureStatus'),
  btnRebuildProfile:$('btnRebuildProfile'),
  btnStartBrowser:$('btnStartBrowser'),btnStopBrowser:$('btnStopBrowser'),
  btnRetryAfterLogin:$('btnRetryAfterLogin'),bossBrowserStatus:$('bossBrowserStatus'),
};

// ── 视图切换 ──
function switchView(v){
  currentView=v;
  localStorage.setItem('js_current_view',v);
  document.querySelectorAll('.nav-icon').forEach(b=>b.classList.toggle('active',b.dataset.view===v));
  document.querySelectorAll('.view').forEach(vw=>vw.classList.remove('active'));
  const viewMap={chat:'viewChat',radar:'viewRadar',gap:'viewGap',user:'viewUser'};
  const tgt=document.getElementById(viewMap[v]||'viewGap');
  if(tgt)tgt.classList.add('active');
  const workspaceTitle=$('workspaceTitle');
  const viewTitle={gap:'求职适配分析',radar:'岗位技能雷达',user:'用户与数据'};
  if(workspaceTitle)workspaceTitle.textContent=viewTitle[v]||'求职适配分析';
  if(v==='radar')loadRadarQuickTags();
  if(v==='gap'){loadGapQuickTags();loadFitReportHistory();updateGapStepper();}
  if(v==='user')loadUserCenter();
}
document.querySelectorAll('.nav-icon').forEach(b=>b.addEventListener('click',()=>switchView(b.dataset.view)));

// ── Toast ──
function toast(msg){const d=document.createElement('div');d.className='toast';d.textContent=msg;el.toastContainer.appendChild(d);setTimeout(()=>{d.style.opacity='0';setTimeout(()=>d.remove(),300);},3000);}

// ═══════════════════════════════════════════════
// 视图1: 智能对话
// ═══════════════════════════════════════════════
async function sendMessage(){
  const text=el.chatInput.value.trim();if(!text)return;
  el.chatInput.value='';el.btnSend.disabled=true;
  addMsg('user',esc(text));
  const loadDiv=addLoading();
  try{
    // 1. 提交异步任务
    const submit=await api.chat(text,threadId);
    if(!submit.async){throw new Error('服务器未返回任务ID');}
    threadId=submit.thread_id;
    const taskId=submit.task_id;

    // 2. 轮询直到完成
    let result=null;
    let taskError=null;
    for(let i=0;i<150;i++){  // 最多5分钟(150×2s)
      await new Promise(r=>setTimeout(r,2000));
      const poll=await fetch('/task/'+taskId).then(r=>r.json());
      if(poll.code!==200)break;
      const t=poll.task;
      loadDiv.textContent=t.progress||'处理中...';
      if(t.finished){
        if(t.status==='failed'){
          taskError=t.error||'任务执行失败';
          break;
        }
        if(t.status==='cancelled'){
          taskError='任务已取消';
          break;
        }
        result=t.result;
        break;
      }
    }
    loadDiv.remove();

    if(taskError){throw new Error(taskError);}
    if(!result){throw new Error('任务完成但没有返回结果');}
    if(result.response.includes('共完成')&&result.knowledge?.length){
      renderResearchInline(result.response,result.knowledge);
    }else{
      addMsg('assistant',fmt(result.response));
    }
    allMessages.push({role:'user',content:text},{role:'assistant',content:result.response});
    convMessageCache.set(threadId,allMessages);
    localStorage.setItem(lastThreadKey(),threadId);
    refreshSidebar();
  }catch(e){loadDiv.remove();addMsg('assistant','请求出错: '+e.message);}
  el.btnSend.disabled=false;el.chatInput.focus();
}

function addMsg(role,content){
  const d=document.createElement('div');d.className='msg msg-'+role;d.innerHTML=content;el.msgList.appendChild(d);el.msgList.scrollTop=el.msgList.scrollHeight;
}
function addLoading(){
  const d=document.createElement('div');d.className='msg-loading';el.msgList.appendChild(d);el.msgList.scrollTop=el.msgList.scrollHeight;return d;
}
function renderResearchInline(summary,cards){
  const c=document.createElement('div');c.className='research-container';
  c.innerHTML='<div class="research-header">◇ 求职研究报告</div><div class="research-grid"></div>';
  const grid=c.querySelector('.research-grid');
  const cats={技能:'skill',薪资:'salary',公司:'company',面试:'interview'};
  cards.forEach((card,i)=>{
    const lines=card.split('\n'),title=lines[0].replace('## ',''),items=lines.filter(l=>l.startsWith('- ')),src=lines.find(l=>l.startsWith('*'));
    const cat=Object.entries(cats).find(([k])=>title.includes(k))?.[1]||'default';
    const elCard=document.createElement('div');elCard.className='research-card cat-'+cat;elCard.style.animationDelay=(i*0.08)+'s';
    elCard.innerHTML='<div class="rc-title">'+esc(title)+'</div><div class="rc-items">'+items.map(it=>'<span class="rc-item">'+esc(it.replace('- ',''))+'</span>').join('')+'</div>'+(src?'<div class="rc-source">'+esc(src.replace(/\*/g,''))+'</div>':'');
    grid.appendChild(elCard);
  });
  el.msgList.appendChild(c);el.msgList.scrollTop=el.msgList.scrollHeight;
}

async function switchConv(tid){
  threadId=tid;el.msgList.innerHTML='';
  localStorage.setItem(lastThreadKey(),tid);
  allMessages=await loadConversationMessages(tid,{fresh:true});
  if(!allMessages.length){
    localStorage.removeItem(lastThreadKey());
    threadId=crypto.randomUUID();
    el.msgList.innerHTML='<div style="text-align:center;margin:auto;padding:2rem;color:var(--text-muted);font-size:0.82rem;">该会话没有可恢复的消息</div>';
    refreshSidebar();
    return;
  }
  allMessages.forEach(m=>addMsg(m.role,fmt(m.content)));
  refreshSidebar();
}

async function deleteConversation(tid){
  if(!tid)return;
  // 立即从 DOM 移除
  const row=document.querySelector('.conv-item-wrap[data-tid="'+tid+'"]');
  if(row)row.remove();
  convMessageCache.delete(tid);
  deletedThreads.add(tid);
  if(localStorage.getItem(lastThreadKey())===tid)localStorage.removeItem(lastThreadKey());

  // 如果删的是当前会话，切到新会话
  if(tid===threadId){
    threadId=crypto.randomUUID();
    allMessages=[];
    el.msgList.innerHTML='<div style="text-align:center;margin:auto;padding:2rem;color:var(--text-muted);font-size:0.82rem;line-height:1.8;">'
      +'<div style="font-size:1.5rem;margin-bottom:0.5rem;">◇</div>'
      +'<div>新对话已准备好</div>'
      +'</div>';
  }

  try{
    await api.deleteConversation(tid);
    // 保留在 deletedThreads 中，防止 refreshSidebar 时 checkpoint 孤儿会话重新出现
    toast('已删除对话');
  }catch(e){
    // 删除失败，恢复：从 deletedThreads 移除，刷新侧边栏让它重新出现
    deletedThreads.delete(tid);
    await refreshSidebar();
    toast('删除失败: '+e.message);
  }
}

// ═══════════════════════════════════════════════
// 视图2: 技能雷达
// ═══════════════════════════════════════════════
async function loadRadarQuickTags(){
  const jobs=await api.analyzedJobs();if(!jobs.length)return;
  el.radarQuickTags.innerHTML=jobs.slice(0,8).map(j=>'<span class="quick-tag" data-job="'+esc(j)+'">'+esc(j)+'</span>').join('');
  el.radarQuickTags.querySelectorAll('.quick-tag').forEach(t=>t.addEventListener('click',()=>{el.radarInput.value=t.dataset.job;runRadar();}));
}
async function runRadar(){
  const job=el.radarInput.value.trim();if(!job)return;
  el.radarBody.style.alignItems='center';el.radarBody.style.justifyContent='center';
  el.radarBody.innerHTML='<div class="msg-loading"></div>';
  const result=await fetch('/skill_rank/'+encodeURIComponent(job)+'?top_n=12');
  const resData=await result.json();
  const skills=resData.data||[];
  const total=resData.total_jds||0;
  const lastUpdate=resData.last_update||'';
  if(!skills.length){el.radarBody.style.alignItems='center';el.radarBody.style.justifyContent='center';el.radarBody.innerHTML='<div class="radar-empty">未找到该岗位数据，先在对话中分析它</div>';return;}
  const max=skills[0]?.count||1;
  let h='<div class="radar-results"><div class="radar-chart-container"><canvas id="radarCanvas" width="240" height="240"></canvas></div><div class="radar-ranking"><div class="radar-job-title">'+esc(job)+' 技能雷达</div>';
  skills.forEach((s,i)=>{
    const count=Number(s.count)||0;
    const pct=max>0?Math.min(Math.round(count/max*100),100):0;
    const cls=pct>60?'hot':pct>30?'warm':'cool';
    const trend='flat';
    const trendIcon={up:'↑',down:'↓',flat:'→'},trendCls={up:'up',down:'down',flat:'flat'};
    h+='<div class="rank-row-radar"><span class="rank-num">#'+(i+1)+'</span><span class="rank-skill">'+esc(s.skill)+'</span><span class="rank-bar-wrap"><span class="rank-bar-inner '+cls+'" style="width:'+pct+'%"></span></span><span class="rank-count-text">'+count+'次</span><span class="rank-trend '+trendCls[trend]+'">'+trendIcon[trend]+'</span></div>';
  });
  h+='</div></div>';
  // 数据来源：total_jds>0 才展示具体数字，否则不展示
  if(total||lastUpdate){
    const parts=[];
    if(total)parts.push('基于 <b>'+total+'</b> 条JD');
    if(lastUpdate)parts.push('更新于 '+lastUpdate);
    h+='<div class="radar-source-bar">'+parts.join(' · ')+'</div>';
  }
  el.radarBody.innerHTML=h;
  el.radarBody.style.alignItems='flex-start';el.radarBody.style.justifyContent='flex-start';
  drawRadarChart(skills.slice(0,8),max);
}
function drawRadarChart(skills,max){
  const cv=document.getElementById('radarCanvas');if(!cv)return;
  const ctx=cv.getContext('2d'),cx=120,cy=120,r=90,n=skills.length;
  ctx.clearRect(0,0,240,240);
  // 网格
  for(let l=1;l<=4;l++){ctx.beginPath();for(let i=0;i<n;i++){const a=Math.PI*2/n*i-Math.PI/2;const x=cx+Math.cos(a)*r*l/4,y=cy+Math.sin(a)*r*l/4;i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);}ctx.closePath();ctx.strokeStyle='rgba(255,255,255,0.06)';ctx.stroke();}
  // 轴线
  for(let i=0;i<n;i++){const a=Math.PI*2/n*i-Math.PI/2;ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(cx+Math.cos(a)*r,cy+Math.sin(a)*r);ctx.strokeStyle='rgba(255,255,255,0.04)';ctx.stroke();}
  // 数据区域
  ctx.beginPath();
  for(let i=0;i<n;i++){const v=skills[i].count/max,a=Math.PI*2/n*i-Math.PI/2,x=cx+Math.cos(a)*r*v,y=cy+Math.sin(a)*r*v;i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);}
  ctx.closePath();ctx.fillStyle='rgba(59,130,246,0.12)';ctx.fill();ctx.strokeStyle='#3b82f6';ctx.lineWidth=1.5;ctx.stroke();
  // 顶点 & 标签
  for(let i=0;i<n;i++){const v=skills[i].count/max,a=Math.PI*2/n*i-Math.PI/2,x=cx+Math.cos(a)*r*v,y=cy+Math.sin(a)*r*v;ctx.beginPath();ctx.arc(x,y,3,0,Math.PI*2);ctx.fillStyle='#3b82f6';ctx.fill();const lx=cx+Math.cos(a)*(r+20),ly=cy+Math.sin(a)*(r+20);ctx.fillStyle='#e8eaed';ctx.font='9px Inter,Noto Sans SC';ctx.textAlign='center';ctx.fillText(skills[i].skill.slice(0,6),lx,ly);}
}
el.btnRadarSearch.addEventListener('click',runRadar);
el.radarInput.addEventListener('keydown',e=>{if(e.key==='Enter')runRadar();});

// ── 雷达标签折叠 ──
$('radarTagsToggle').addEventListener('click',()=>{
  const tags=$('radarQuickTags');
  const toggle=$('radarTagsToggle');
  tags.classList.toggle('collapsed');
  toggle.classList.toggle('open');
});

// ═══════════════════════════════════════════════
// 视图3: 技能差距
// ═══════════════════════════════════════════════
let gapMarketSkills=[],gapTotalJds=0,gapCurrentJob='',gapJobProfile=null,userProfileSkills=[];

function normalizeSkillText(text){
  return String(text||'')
    .split(/[,\n，、;；]+/)
    .map(s=>s.trim())
    .filter(Boolean);
}
function profileSkillNames(){
  return userProfileSkills.map(s=>s.skill).filter(Boolean);
}
function renderProfileSkills(profile){
  if(!el.profileSkillList)return;
  const container=$('candidateProfileCard')||el.profileSkillList;
  if(!container)return;
  userProfileSkills=(profile?.skills||[]).filter(s=>s&&s.skill);
  if(!userProfileSkills.length){
    el.profileSkillList.innerHTML='<div class="gap-subtle">暂未识别到明确技能，可以补充经历或手动填写技能</div>';
    return;
  }
  el.profileSkillList.innerHTML=renderCandidatePreviewCard(profile);
  el.profileSkillList.querySelectorAll('.profile-remove').forEach(btn=>btn.addEventListener('click',()=>{
    userProfileSkills.splice(Number(btn.dataset.idx),1);
    renderProfileSkills({skills:userProfileSkills,summary:'已更新技能画像'});
  }));
}
function renderCandidatePreviewCard(profile){
  const skills=(profile?.skills||[]).filter(s=>s&&s.skill);
  const core=skills.filter(s=>/项目核心|熟练/.test(s.level||'')).length;
  const evidences=skills.map(s=>s.evidence).filter(Boolean).slice(0,2);
  return '<div class="profile-preview-card candidate">'
    +'<div class="profile-preview-head"><div><b>候选人画像预览</b><span>'+esc(profile.summary||('已识别 '+skills.length+' 个技能线索'))+'</span></div><em>'+skills.length+' 技能</em></div>'
    +'<div class="profile-preview-kpis"><span>核心/熟练 '+core+'</span><span>证据 '+evidences.length+'</span><span>'+esc(profile.parser==='llm'?'LLM':'规则')+'</span></div>'
    +'<div class="screening-chip-row matched">'+chipRow(skills.slice(0,10).map(s=>s.skill),'暂未识别')+'</div>'
    +(evidences.length?'<ul class="profile-preview-evidence">'+evidences.map(e=>'<li>'+esc(e)+'</li>').join('')+'</ul>':'')
    +'<div class="profile-preview-edit">'+skills.slice(0,10).map((s,i)=>'<button class="profile-remove" data-idx="'+i+'" title="移除">'+esc(s.skill)+' ×</button>').join('')+'</div>'
    +'</div>';
}
async function extractResumeProfile(){
  const text=el.resumeTextInput.value.trim();
  const file=el.resumeFileInput.files?.[0];
  if(!text&&!file){toast('请先粘贴简历或选择文件');return;}
  el.btnResumeProfile.disabled=true;
  el.profileSkillList.innerHTML='<div class="msg-loading"></div>';
  try{
    if(file){
      // 先用 /resume/parse 解析文件文本
      const parseRes=await api.parseResume(file);
      if(parseRes.code!==200)throw new Error(parseRes.detail||'文件解析失败');
      parsedResumeText=parseRes.text;
      el.profileSkillList.innerHTML='<div class="gap-subtle">✓ 文件解析成功（'+parseRes.char_count+'字）'+(parseRes.warnings?.length?' · '+esc(parseRes.warnings.join('; ')):'')+'</div>';
      // 用解析文本生成候选人画像
      const res=await api.candidateProfileFromText(parseRes.text);
      if(res.code===200){
        currentCandidateProfile=res.profile;
        renderProfileSkills(res.profile);
        renderCandidateProfileCard(res.profile);
        updateGapStepper();
        toast('候选人画像已生成');
      }
    }else{
      parsedResumeText=text;
      const res=await api.resumeProfileText(text);
      if(res.code!==200)throw new Error(res.detail||'简历解析失败');
      currentCandidateProfile=res.profile;
      renderProfileSkills(res.profile);
      renderCandidateProfileCard(res.profile);
      updateGapStepper();
      toast('技能画像已生成');
    }
  }catch(e){
    parsedResumeText='';
    el.profileSkillList.innerHTML='<div class="gap-subtle">解析失败：'+esc(e.message)+'，请手动粘贴简历文本</div>';
  }finally{
    el.btnResumeProfile.disabled=false;
  }
}

function renderCandidateProfileCard(profile){
  const container=$('candidateProfileCard');
  if(!container||!profile)return;
  const skills=(profile.skill_stack||[]).map(s=>typeof s==='string'?s:(s?.skill||''));
  const edu=profile.education_background||{};
  const projects=profile.projects||[];
  const internships=profile.internships||[];
  const work=profile.work_experiences||[];
  const achievements=profile.achievements||[];
  const risks=profile.risk_points||[];

  let h='<div class="job-profile-card-full" style="border-top-color:var(--green);">';

  // 顶部摘要
  h+='<div class="profile-section">';
  h+='<div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.5rem;">';
  h+='<span style="font-size:1rem;font-weight:800;">候选人画像</span>';
  h+='<span class="profile-confidence '+(profile.confidence||'low')+'">'+esc(profile.confidence||'low')+' 置信度</span>';
  h+='</div>';
  h+='<div class="profile-kv-grid">';
  h+='<div class="profile-kv-item"><span>学历</span><b>'+esc(edu.degree||'未识别')+'</b></div>';
  h+='<div class="profile-kv-item"><span>院校</span><b>'+esc(edu.school||'未识别')+'</b></div>';
  h+='<div class="profile-kv-item"><span>专业</span><b>'+esc(edu.major||'未识别')+'</b></div>';
  h+='<div class="profile-kv-item"><span>毕业年份</span><b>'+esc(edu.graduation_year||'未识别')+'</b></div>';
  h+='<div class="profile-kv-item"><span>技能数</span><b>'+skills.length+'</b></div>';
  h+='<div class="profile-kv-item"><span>项目数</span><b>'+projects.length+'</b></div>';
  h+='</div></div>';

  // 技能栈
  if(skills.length){
    h+='<div class="profile-section">';
    h+='<div class="profile-section-title">技能栈</div>';
    h+='<div class="profile-chip-row">';
    skills.slice(0,15).forEach(s=>{h+='<span class="profile-chip must">'+esc(s)+'</span>';});
    h+='</div></div>';
  }

  // 项目经历
  if(projects.length){
    h+='<div class="profile-section">';
    h+='<div class="profile-section-title">项目经历</div>';
    h+='<ul class="profile-resp-list">';
    projects.slice(0,4).forEach(p=>{
      const desc=p.description||p.name||'';
      h+='<li>'+esc(desc.length>100?desc.substring(0,100)+'...':desc)+'</li>';
    });
    h+='</ul></div>';
  }

  // 实习/工作经历
  if(internships.length||work.length){
    h+='<div class="profile-section">';
    h+='<div class="profile-section-title">实习/工作经历</div>';
    h+='<ul class="profile-resp-list">';
    internships.slice(0,2).forEach(i=>{
      h+='<li>'+esc((i.description||'').length>80?i.description.substring(0,80)+'...':(i.description||''))+'</li>';
    });
    work.slice(0,2).forEach(w=>{
      h+='<li>'+esc((w.description||'').length>80?w.description.substring(0,80)+'...':(w.description||''))+'</li>';
    });
    h+='</ul></div>';
  }

  // 成果证据
  if(achievements.length){
    h+='<div class="profile-section">';
    h+='<div class="profile-section-title">成果证据</div>';
    h+='<ul class="profile-resp-list">';
    achievements.slice(0,3).forEach(a=>{
      h+='<li>'+esc(a.description||'')+'</li>';
    });
    h+='</ul></div>';
  }

  // 风险点
  if(risks.length){
    h+='<div class="profile-section">';
    h+='<div style="padding:0.5rem 0.7rem;background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.15);border-radius:var(--radius-sm);">';
    h+='<div style="font-size:0.68rem;font-weight:600;color:var(--gold);margin-bottom:0.3rem;">⚠ 提示</div>';
    risks.forEach(r=>{h+='<div style="font-size:0.65rem;color:var(--text-dim);">• '+esc(r)+'</div>';});
    h+='</div></div>';
  }

  h+='</div>';
  container.innerHTML=h;
}
function skillTitle(item){return typeof item==='string'?item:(item?.skill||'');}
function marketRate(item,total=gapTotalJds){
  const count=Number(item?.count)||0;
  const base=Number(item?.total_jds)||Number(total)||0;
  return base>0?Math.round(count/base*100):0;
}
function priorityClass(rate){
  if(rate>=60)return 'high';
  if(rate>=40)return 'mid';
  return 'low';
}
async function loadGapQuickTags(){
  if(!el.gapQuickTags)return;
  const jobs=await api.analyzedJobs();if(!jobs.length)return;
  el.gapQuickTags.innerHTML=jobs.slice(0,10).map(j=>'<span class="quick-tag" data-job="'+esc(j)+'">'+esc(j)+'</span>').join('');
  el.gapQuickTags.querySelectorAll('.quick-tag').forEach(t=>t.addEventListener('click',()=>{el.gapJobInput.value=t.dataset.job;loadGapMarketSkills();}));
}
async function loadGapMarketSkills(){
  const job=el.gapJobInput.value.trim();if(!job)return;
  gapCurrentJob=job;
  el.btnGapLoad.disabled=true;
  el.gapSkillList.innerHTML='<div class="msg-loading"></div>';
  el.gapResult.innerHTML='<div class="msg-loading"></div>';
  try{
    const [res,profileRes]=await Promise.all([api.skillRank(job,15),api.jobProfile(job,20).catch(()=>null)]);
    gapMarketSkills=res.data||[];
    gapTotalJds=Number(res.total_jds)||Number(gapMarketSkills[0]?.total_jds)||0;
    gapJobProfile=profileRes?.code===200?profileRes.profile:null;
    if(gapJobProfile)renderMarketProfilePreview(gapJobProfile,res.last_update||'',res.confidence||'high',res.filtered_count||0);
    else renderGapSkillList(res.last_update||'',res.confidence||'high',res.filtered_count||0);
    if(profileRes?.code===200)renderJobProfilePreview(profileRes.profile);
    else el.gapResult.innerHTML='<div class="gap-result-empty">岗位技能已加载，粘贴简历后点击分析差距</div>';
  }catch(e){
    el.gapSkillList.innerHTML='<div class="radar-empty">请求出错: '+esc(e.message)+'</div>';
    el.gapResult.innerHTML='<div class="gap-result-empty">岗位画像加载失败</div>';
  }finally{
    el.btnGapLoad.disabled=false;
  }
}
function renderJobProfilePreview(job){
  el.gapResult.innerHTML='<div class="screening-report profile-only">'
    +'<div class="profile-card-grid single">'
    +'<article class="profile-card job-profile-card">'
    +'<div class="profile-card-head"><div><div class="profile-card-title">岗位画像</div><div class="profile-card-sub">'+esc(job.job_name||'目标岗位')+'</div></div><span>'+esc(job.job_type||'未知')+'</span></div>'
    +'<div class="profile-kv"><span>用工类型</span><b>'+esc(job.employment_type||'未明确')+'</b></div>'
    +'<div class="profile-kv"><span>面向人群</span><b>'+esc(job.target_audience||'未明确')+'</b></div>'
    +'<div class="profile-kv"><span>样本来源</span><b>'+esc(String(job.sample?.jd_count||0))+' 条 JD</b></div>'
    +'<p>必备技能</p><div class="screening-chip-row must">'+chipRow(job.must_have||[],'暂无')+'</div>'
    +'<p>学历 / 专业 / 经验</p><ul>'+profileEvidenceList([...(job.education_requirements||[]),...(job.major_requirements||[]),...(job.experience_requirements||[])],'未明确硬性要求')+'</ul>'
    +'</article>'
    +'</div>'
    +'<div class="gap-subtle">识别简历后点击“分析差距”，这里会展示岗位画像与候选人画像对比。</div>'
    +'</div>';
}
function renderMarketProfilePreview(job,lastUpdate='',confidence='high',filteredCount=0){
  const meta=[];
  const sample=job.sample||{};
  if(sample.jd_count)meta.push(sample.jd_count+' 条 JD');
  if(sample.skill_sample_jds)meta.push('技能样本 '+sample.skill_sample_jds);
  if(lastUpdate||sample.last_update)meta.push('更新 '+(lastUpdate||sample.last_update));
  if(confidence==='low')meta.push('<span style="color:var(--gold)">⚠ 样本较少</span>');
  if(filteredCount>0)meta.push('已过滤 '+filteredCount+' 个泛词');
  el.gapMarketMeta.innerHTML=meta.join(' · ')||'岗位画像已生成';
  el.gapSkillList.innerHTML='<div class="profile-preview-card job">'
    +'<div class="profile-preview-head"><div><b>岗位画像预览</b><span>'+esc(job.summary||'基于已分析 JD 汇总岗位要求')+'</span></div><em>'+esc(job.job_type||'未知')+'</em></div>'
    +'<div class="profile-preview-kpis"><span>'+esc(job.target_audience||'人群未明')+'</span><span>'+esc(String(sample.jd_count||0))+' JD</span><span>'+esc(confidence||'high')+'</span></div>'
    +'<p>必备技能</p><div class="screening-chip-row must">'+chipRow(job.must_have||[],'暂无')+'</div>'
    +'<p>加分技能</p><div class="screening-chip-row">'+chipRow((job.nice_to_have||[]).slice(0,8),'暂无')+'</div>'
    +'<p>学历 / 专业 / 经验</p><ul class="profile-preview-evidence">'+profileEvidenceList([...(job.education_requirements||[]),...(job.major_requirements||[]),...(job.experience_requirements||[])],'未明确硬性要求')+'</ul>'
    +'<p>业务与软性要求</p><div class="screening-chip-row">'+chipRow([...(job.business_domains||[]),...(job.soft_requirements||[])].slice(0,8),'暂无明显要求')+'</div>'
    +'</div>';
  loadFeedbackSummary(gapCurrentJob);
}
async function triggerGapAutoAnalyze(job){
  el.gapSkillList.innerHTML='<div class="msg-loading"></div><div style="text-align:center;margin-top:0.5rem;font-size:0.72rem;color:var(--text-dim);">正在分析「'+esc(job)+'」，首次分析可能需要 3-5 分钟...</div>';
  try{
    const submit=await api.analyzeJob(job);
    if(!submit.task_id)throw new Error('服务器未返回任务ID');
    const taskId=submit.task_id;
    let result=null;
    for(let i=0;i<180;i++){  // 最多6分钟(180×2s)
      await new Promise(r=>setTimeout(r,2000));
      const poll=await api.task(taskId);
      if(poll.code!==200)break;
      const t=poll.task;
      const progressEl=el.gapSkillList.querySelector('.msg-loading');
      if(progressEl)progressEl.textContent=t.progress||'分析中...';
      if(t.finished){
        result=t.result;
        break;
      }
    }
    if(!result)throw new Error('分析超时，请稍后重试');
    // 分析完成，重新加载技能列表
    toast('「'+job+'」分析完成');
    await loadGapMarketSkills();
  }catch(e){
    el.gapSkillList.innerHTML='<div class="radar-empty">分析失败: '+esc(e.message)+'</div>';
  }
}
function renderGapSkillList(lastUpdate,confidence,filteredCount){
  if(!gapMarketSkills.length){
    el.gapMarketMeta.textContent='暂无数据';
    el.gapSkillList.innerHTML='<div class="radar-empty" style="text-align:center;line-height:2;">'
      +'<div style="font-size:1.5rem;margin-bottom:0.5rem;">📭</div>'
      +'<div>「'+esc(gapCurrentJob)+'」暂无技能数据</div>'
      +'<div style="margin-top:0.8rem;"><button id="btnGapAutoAnalyze" style="padding:0.5rem 1.2rem;border-radius:var(--radius);border:1px solid var(--blue);background:transparent;color:var(--blue);cursor:pointer;font-size:0.75rem;">立即分析岗位</button></div>'
      +'</div>';
    const btn=$('btnGapAutoAnalyze');
    if(btn)btn.addEventListener('click',()=>triggerGapAutoAnalyze(gapCurrentJob));
    return;
  }
  const meta=[];
  if(gapTotalJds)meta.push(gapTotalJds+' 条 JD');
  if(lastUpdate)meta.push('更新 '+lastUpdate);
  if(confidence==='low')meta.push('<span style="color:var(--gold)">⚠ 样本较少</span>');
  if(filteredCount>0)meta.push('已过滤 '+filteredCount+' 个泛词');
  el.gapMarketMeta.innerHTML=meta.join(' · ')||gapCurrentJob;
  el.gapSkillList.innerHTML=gapMarketSkills.map((s,i)=>{
    const rate=marketRate(s);
    const cls=priorityClass(rate);
    const fb=applyFeedbackToSkill(s.skill,gapCurrentJob);
    const fbReject=fb.cls.includes('fb-rejected');
    const fbImportant=fb.cls.includes('fb-important');
    const rowCls='gap-skill-row '+fb.cls;
    const communityNote=fb.community?'<span class="fb-community-note">多人标记</span>':'';
    return '<label class="'+rowCls+'">'
      +'<input type="checkbox" class="gap-skill-check" data-idx="'+i+'"'+(fbReject?' disabled':'')+'>'
      +'<span class="gap-skill-main"><span class="gap-skill-name">'+(fbImportant?'<span class="fb-imp-icon">★</span>':'')+esc(s.skill)+communityNote+'</span><span class="gap-skill-bar"><span style="width:'+Math.max(rate,4)+'%"></span></span></span>'
      +'<span class="gap-skill-rate '+cls+'">'+rate+'%</span>'
      +'<span class="gap-skill-fb">'
      +'<button class="fb-btn fb-btn-reject'+(fbReject?' active':'')+'" data-skill="'+esc(s.skill)+'" title="不是技能">✕</button>'
      +'<button class="fb-btn fb-btn-important'+(fbImportant?' active':'')+'" data-skill="'+esc(s.skill)+'" title="重要技能">★</button>'
      +'</span>'
      +'</label>';
  }).join('');
  // 反馈按钮事件
  el.gapSkillList.querySelectorAll('.fb-btn-reject').forEach(btn=>{btn.addEventListener('click',async e=>{
    e.preventDefault();e.stopPropagation();
    await addSkillFeedback(gapCurrentJob,btn.dataset.skill,'reject');
    await loadFeedbackSummary(gapCurrentJob);
    renderGapSkillList(lastUpdate,confidence,filteredCount);
  });});
  el.gapSkillList.querySelectorAll('.fb-btn-important').forEach(btn=>{btn.addEventListener('click',async e=>{
    e.preventDefault();e.stopPropagation();
    await addSkillFeedback(gapCurrentJob,btn.dataset.skill,'important');
    await loadFeedbackSummary(gapCurrentJob);
    renderGapSkillList(lastUpdate,confidence,filteredCount);
  });});
  loadFeedbackSummary(gapCurrentJob);
}
function collectGapSkills(){
  const selected=[...el.gapSkillList.querySelectorAll('.gap-skill-check:checked')]
    .map(input=>gapMarketSkills[Number(input.dataset.idx)]?.skill)
    .filter(Boolean);
  const extra=normalizeSkillText(el.gapExtraInput.value);
  return [...new Set([...selected,...extra])];
}

// ── v0.26 normalizeJobProfile ──
function normalizeJobProfile(p){
  if(!p)return null;
  return {
    job_name:p.job_name||'',
    job_type:p.job_type||'未知',
    employment_type:p.employment_type||'未知',
    target_audience:p.target_audience||'未明确',
    responsibilities:p.responsibilities||[],
    must_have:p.must_have_capabilities||p.must_have||[],
    nice_to_have:p.nice_to_have_capabilities||p.nice_to_have||[],
    education_preference:p.education_preference||'未明确',
    major_preference:p.major_preference||'未明确',
    experience_requirement:p.experience_requirement||'未明确',
    business_context:p.business_context||[],
    growth_context:p.growth_context||[],
    confidence:p.confidence||'low',
    quality_flags:p.quality_flags||[],
    sample_count:p.sample_count||0,
    valid_sample_count:p.valid_sample_count||p.sample_count||0,
    filtered_sample_count:p.filtered_sample_count||0,
    _id:p.id||p.job_profile_id||0,
  };
}

// ── v0.26 Tab 切换 ──
function switchGapTab(tabName){
  document.querySelectorAll('.gap-tab').forEach(t=>
    t.classList.toggle('active',t.dataset.tab===tabName));
  const panelMap={capture:'tabCapture',resume:'tabResume',fit:'tabFit',history:'tabHistory'};
  document.querySelectorAll('.gap-tab-panel').forEach(p=>
    p.classList.toggle('active',p.id===panelMap[tabName]));
  if(tabName==='history')loadFitReportHistory();
  if(tabName==='fit')updateFitSummaries();
  const activePanel=document.getElementById(panelMap[tabName]);
  if(activePanel)activePanel.scrollTop=0;
}

function updateGapStepper(){
  const steps=document.querySelectorAll('#gapStepper .step');
  if(!steps.length)return;
  steps[0].classList.toggle('done',!!currentJobProfile);
  steps[0].classList.toggle('active',!currentJobProfile);
  steps[1].classList.toggle('done',!!currentCandidateProfile);
  steps[1].classList.toggle('active',!!currentJobProfile&&!currentCandidateProfile);
  steps[2].classList.toggle('done',!!currentFitReport);
  steps[2].classList.toggle('active',!!currentJobProfile&&!!currentCandidateProfile&&!currentFitReport);
}

function updateFitSummaries(){
  const jobCard=$('fitJobSummary');
  const candCard=$('fitCandSummary');
  if(jobCard){
    if(currentJobProfile){
      const p=normalizeJobProfile(currentJobProfile);
      jobCard.className='fit-summary-card has-data';
      jobCard.innerHTML='<span class="section-overline">岗位画像</span>'
        +'<strong>'+esc(p.job_name)+'</strong>'
        +'<p>'+esc(p.job_type)+' · '+esc(p.employment_type)
        +'<br>有效样本 '+p.valid_sample_count+' 条 · '+esc(p.confidence)+' 置信度</p>';
    }else{
      jobCard.className='fit-summary-card';
      jobCard.innerHTML='<span class="section-overline">岗位画像</span><strong>尚未准备</strong><p>请先采集或导入目标岗位 JD。</p>';
    }
  }
  if(candCard){
    if(currentCandidateProfile){
      const skills=(currentCandidateProfile.skill_stack||[]).length;
      candCard.className='fit-summary-card has-cand';
      const projects=(currentCandidateProfile.projects||[]).length;
      candCard.innerHTML='<span class="section-overline">候选人画像</span>'
        +'<strong>画像已建立</strong>'
        +'<p>识别 '+skills+' 项技能 · '+projects+' 段项目经历'
        +'<br>'+esc(currentCandidateProfile.confidence||'low')+' 置信度</p>';
    }else{
      candCard.className='fit-summary-card';
      candCard.innerHTML='<span class="section-overline">候选人画像</span><strong>尚未准备</strong><p>请先上传或粘贴简历。</p>';
    }
  }
}

const _QUALITY_FLAG_CN={
  low_sample_count:'样本较少',
  low_valid_ratio:'有效样本比例过低',
  low_quality_jds:'JD 整体质量偏低',
  missing_responsibilities:'缺少职责证据',
  missing_requirements:'缺少明确技能要求',
  mixed_experience_requirement:'经验要求存在冲突',
  mixed_employment_type:'用工类型不一致',
  short_jd_text:'JD 文本较短',
  fallback_only:'仅采集到卡片信息',
  detail_missing:'未采集到完整 JD',
  有效样本不足:'有效样本不足',
  有效样本比例过低:'有效样本比例过低',
  未提取到明确技能要求:'未提取到明确技能要求',
  JD整体质量偏低:'JD 整体质量偏低',
};

function qualityFlagToCN(flag){
  return _QUALITY_FLAG_CN[flag]||flag;
}

function renderJobProfileCard(p){
  const container=$('jobProfileCard');
  if(!container||!p)return;
  const np=normalizeJobProfile(p);
  let h='<div class="job-profile-card-full">';

  // 顶部摘要
  h+='<div class="profile-section">';
  h+='<div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.5rem;">';
  h+='<span style="font-size:1rem;font-weight:800;">'+esc(np.job_name)+'</span>';
  h+='<span class="profile-confidence '+np.confidence+'">'+esc(np.confidence)+' 置信度</span>';
  h+='</div>';
  h+='<div class="profile-kv-grid">';
  h+='<div class="profile-kv-item"><span>用工类型</span><b>'+esc(np.employment_type)+'</b></div>';
  h+='<div class="profile-kv-item"><span>面向人群</span><b>'+esc(np.target_audience)+'</b></div>';
  h+='<div class="profile-kv-item"><span>样本来源</span><b>'+np.valid_sample_count+' 条 JD / 有效 '+np.valid_sample_count+' / 过滤 '+np.filtered_sample_count+'</b></div>';
  h+='</div></div>';

  // 核心职责
  if(np.responsibilities.length){
    h+='<div class="profile-section">';
    h+='<div class="profile-section-title">核心职责</div>';
    h+='<ul class="profile-resp-list">';
    np.responsibilities.slice(0,6).forEach(r=>{h+='<li>'+esc(r)+'</li>';});
    h+='</ul></div>';
  }

  // 必备能力
  if(np.must_have.length){
    h+='<div class="profile-section">';
    h+='<div class="profile-section-title">必备能力</div>';
    h+='<div class="profile-chip-row">';
    np.must_have.forEach(s=>{h+='<span class="profile-chip must">'+esc(s)+'</span>';});
    h+='</div></div>';
  }

  // 加分能力
  if(np.nice_to_have.length){
    h+='<div class="profile-section">';
    h+='<div class="profile-section-title">加分能力</div>';
    h+='<div class="profile-chip-row">';
    np.nice_to_have.forEach(s=>{h+='<span class="profile-chip nice">'+esc(s)+'</span>';});
    h+='</div></div>';
  }

  // 要求摘要
  h+='<div class="profile-section">';
  h+='<div class="profile-section-title">要求</div>';
  h+='<div class="profile-kv-grid">';
  h+='<div class="profile-kv-item"><span>学历</span><b>'+esc(np.education_preference||'不明确')+'</b></div>';
  h+='<div class="profile-kv-item"><span>专业</span><b>'+esc(np.major_preference||'不明确')+'</b></div>';
  h+='<div class="profile-kv-item"><span>经验</span><b>'+esc(np.experience_requirement||'不明确')+'</b></div>';
  h+='</div></div>';

  // 业务场景
  if(np.business_context.length){
    h+='<div class="profile-section">';
    h+='<div class="profile-section-title">业务场景</div>';
    h+='<div class="profile-chip-row">';
    np.business_context.forEach(s=>{h+='<span class="profile-chip">'+esc(s)+'</span>';});
    h+='</div></div>';
  }

  // 成长信号
  if(np.growth_context.length){
    h+='<div class="profile-section">';
    h+='<div class="profile-section-title">成长信号</div>';
    h+='<div class="profile-chip-row">';
    np.growth_context.slice(0,3).forEach(s=>{h+='<span class="profile-chip">'+esc(s)+'</span>';});
    h+='</div></div>';
  }

  // 质量提示
  if(np.quality_flags.length){
    h+='<div class="profile-section">';
    h+='<div style="padding:0.5rem 0.7rem;background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.15);border-radius:var(--radius-sm);">';
    h+='<div style="font-size:0.68rem;font-weight:600;color:var(--gold);margin-bottom:0.3rem;">⚠ 质量提示</div>';
    np.quality_flags.forEach(f=>{h+='<div style="font-size:0.65rem;color:var(--text-dim);">• '+esc(qualityFlagToCN(f))+'</div>';});
    h+='</div></div>';
  }

  h+='</div>';
  container.innerHTML=h;
}

// ── v0.25 Boss 岗位采集 ──
let bossBrowserRunning=false;
let bossBrowserLoggedIn=false;

function updateBossBrowserUI(status){
  if(!status)return;
  bossBrowserRunning=status.running||false;
  bossBrowserLoggedIn=status.logged_in||false;
  const statusEl=el.bossBrowserStatus;
  if(statusEl){
    const statusText=status.status||'未启动';
    const statusColor=status.running?(status.logged_in?'var(--green)':'var(--gold)'):'var(--text-muted)';
    statusEl.innerHTML='浏览器: <span style="color:'+statusColor+'">'+esc(statusText)+'</span>';
    if(status.message)statusEl.title=status.message;
  }
  // 按钮显隐
  if(el.btnStartBrowser)el.btnStartBrowser.style.display=status.running?'none':'';
  if(el.btnStopBrowser)el.btnStopBrowser.style.display=status.running?'':'none';
  if(el.btnRetryAfterLogin)el.btnRetryAfterLogin.style.display=(status.running&&!status.logged_in)?'':'none';
}

async function refreshBossBrowserStatus(){
  try{
    const res=await api.getBossBrowserStatus();
    if(res.code===200)updateBossBrowserUI(res);
  }catch(_){}
}

async function startBossBrowser(){
  if(!el.btnStartBrowser)return;
  el.btnStartBrowser.disabled=true;
  el.btnStartBrowser.textContent='启动中...';
  el.bossCaptureStatus.innerHTML='<div style="display:flex;align-items:center;gap:0.5rem;"><div class="spinner" style="width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--blue);border-radius:50%;animation:spin 0.8s linear infinite;"></div><span>正在启动浏览器并打开 Boss 登录页...</span></div>';

  try{
    const res=await api.startBossBrowser();
    if(res.code===200){
      updateBossBrowserUI(res);
      if(!res.logged_in){
        el.bossCaptureStatus.innerHTML='<div class="capture-warning">⚠ 已打开 Boss 登录页，请在弹出的浏览器中扫码/验证登录，然后点击「我已完成登录，重试采集」</div>';
      }else{
        el.bossCaptureStatus.innerHTML='<div style="color:var(--green);font-size:0.75rem;">✓ Boss 浏览器已登录，可以开始采集</div>';
      }
      toast(res.message||'浏览器已启动');
    }else{
      toast('启动失败');
    }
  }catch(e){
    toast('启动失败: '+e.message);
  }finally{
    el.btnStartBrowser.disabled=false;
    el.btnStartBrowser.textContent='启动浏览器';
  }
}

async function stopBossBrowser(){
  if(!el.btnStopBrowser)return;
  el.btnStopBrowser.disabled=true;
  try{
    const res=await api.stopBossBrowser();
    if(res.code===200){
      updateBossBrowserUI(res);
      bossCaptureResult=null;
      renderBossCaptureStatus(null);
      toast('浏览器已关闭');
    }
  }catch(e){
    toast('停止失败: '+e.message);
  }finally{
    el.btnStopBrowser.disabled=false;
  }
}

async function retryAfterLogin(){
  // 刷新登录状态
  await refreshBossBrowserStatus();
  if(bossBrowserLoggedIn){
    toast('登录成功，可以开始采集');
    el.bossCaptureStatus.innerHTML='<div style="color:var(--green);font-size:0.75rem;">✓ 登录成功，可以开始采集</div>';
  }else{
    toast('仍未检测到登录，请在浏览器中完成登录');
  }
}

function renderBossCaptureStatus(result){
  if(!result){
    el.bossCaptureStatus.innerHTML='';
    el.btnRebuildProfile.style.display='none';
    return;
  }
  const r=result;
  let h='<div style="display:flex;flex-wrap:wrap;gap:0.4rem;margin-bottom:0.5rem;">';
  if(r.search_keywords&&r.search_keywords.length){
    h+='<span class="capture-stat">关键词 <b>'+r.search_keywords.length+'</b> 个</span>';
  }
  const filterLabels={
    company_size:'公司规模',
    hr_activity:'HR 活跃度',
    experience:'经验',
    education:'学历',
  };
  const activityLabels={recent:'刚刚 / 在线',today:'今日活跃','3d':'3 天内','7d':'7 天内','30d':'30 天内'};
  Object.entries(r.applied_filters||{}).forEach(([key,value])=>{
    const display=key==='hr_activity'?(activityLabels[value]||value):value;
    h+='<span class="capture-stat filter">'+esc(filterLabels[key]||key)+'：<b>'+esc(display)+'</b></span>';
  });
  h+='<span class="capture-stat">发现 <b>'+r.captured_count+'</b> 条</span>';
  if(r.imported_count>0)h+='<span class="capture-stat imported">导入 <b>'+r.imported_count+'</b> 条</span>';
  if(r.detail_missing_count>0)h+='<span class="capture-stat skipped">详情缺失 <b>'+r.detail_missing_count+'</b> 条</span>';
  if(r.skipped_count>0)h+='<span class="capture-stat skipped">跳过 <b>'+r.skipped_count+'</b> 条</span>';
  if(r.failed_count>0)h+='<span class="capture-stat failed">失败 <b>'+r.failed_count+'</b> 条</span>';
  h+='</div>';
  if(r.blocked_reason)h+='<div class="capture-warning">⚠ '+esc(r.blocked_reason)+'</div>';
  if(r.warnings&&r.warnings.length){
    h+='<div class="capture-warning">'+r.warnings.map(w=>'• '+esc(w)).join('<br>')+'</div>';
  }
  if(r.profile_generated){
    h+='<div style="margin-top:0.5rem;font-size:0.72rem;color:var(--green);">✓ 岗位画像已自动生成 (ID: '+r.job_profile_id+')</div>';
    el.btnRebuildProfile.style.display='';
  }else if(r.imported_count>0){
    h+='<div style="margin-top:0.5rem;font-size:0.72rem;color:var(--green);">✓ JD 已入库，可点击下方按钮生成岗位画像</div>';
    el.btnRebuildProfile.style.display='';
  }else{
    el.btnRebuildProfile.style.display='none';
  }
  el.bossCaptureStatus.innerHTML=h;
}

async function runBossCapture(){
  const jobName=el.bossJobInput.value.trim();
  if(!jobName){toast('请输入岗位关键词');return;}
  const extraJobKeywords=String(el.bossExtraJobInput?.value||'')
    .split(/[,，、;；\n]+/)
    .map(item=>item.trim())
    .filter(Boolean);
  const city=el.bossCityInput.value.trim();
  const maxJobs=parseInt(el.bossMaxJobs.value)||10;
  const filters={};
  if(el.bossExpFilter.value)filters.experience=el.bossExpFilter.value;
  if(el.bossEduFilter.value)filters.education=el.bossEduFilter.value;
  if(el.bossCompanySizeFilter?.value)filters.company_size=el.bossCompanySizeFilter.value;
  if(el.bossHrActivityFilter?.value)filters.hr_activity=el.bossHrActivityFilter.value;

  el.btnBossCapture.disabled=true;
  el.btnBossCapture.textContent='采集中...';
  el.bossCaptureStatus.innerHTML='<div style="display:flex;align-items:center;gap:0.5rem;"><div class="spinner" style="width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--gold);border-radius:50%;animation:spin 0.8s linear infinite;"></div><span>正在采集 Boss 直聘 JD...</span></div>';

  try{
    const res=await api.bossCapture(jobName,extraJobKeywords,city,maxJobs,filters);
    if(res.code!==200)throw new Error(res.message||'采集失败');
    bossCaptureResult=res;
    renderBossCaptureStatus(res);
    // 同步到岗位输入框
    el.gapJobInput.value=jobName;
    // 如果自动生成了画像，立即渲染
    if(res.profile_generated&&res.job_profile){
      currentJobProfile=normalizeJobProfile(res.job_profile);
      renderJobProfileCard(res.job_profile);
      updateGapStepper();
      // 折叠采集表单
      if(el.bossCaptureBody)el.bossCaptureBody.classList.add('collapsed');
      if(el.btnToggleBossCapture)el.btnToggleBossCapture.textContent='展开';
    }
    toast('采集完成');
  }catch(e){
    bossCaptureResult=null;
    renderBossCaptureStatus(null);
    el.bossCaptureStatus.innerHTML='<div class="capture-warning">⚠ '+esc(e.message)+'</div>';
    toast('采集失败: '+e.message);
  }finally{
    el.btnBossCapture.disabled=false;
    el.btnBossCapture.textContent='开始采集';
  }
}

async function runBossManualImport(){
  const jobName=el.bossJobInput.value.trim();
  if(!jobName){toast('请先输入岗位关键词');return;}
  const jdText=el.bossManualJdInput.value.trim();
  if(!jdText){toast('请粘贴 JD 文本');return;}

  el.btnBossManualImport.disabled=true;
  el.btnBossManualImport.textContent='导入中...';

  try{
    const res=await api.bossManualImport(jobName,jdText);
    if(res.code!==200)throw new Error(res.message||'导入失败');
    bossCaptureResult=res;
    renderBossCaptureStatus(res);
    el.bossManualJdInput.value='';
    // 同步到岗位输入框
    el.gapJobInput.value=jobName;
    // 如果自动生成了画像，立即渲染
    if(res.profile_generated&&res.job_profile){
      currentJobProfile=normalizeJobProfile(res.job_profile);
      renderJobProfileCard(res.job_profile);
      updateGapStepper();
    }
    toast('导入完成');
  }catch(e){
    toast('导入失败: '+e.message);
  }finally{
    el.btnBossManualImport.disabled=false;
    el.btnBossManualImport.textContent='导入 JD';
  }
}

async function runRebuildJobProfile(){
  const jobName=el.bossJobInput.value.trim()||el.gapJobInput.value.trim();
  if(!jobName){toast('请先输入岗位关键词');return;}

  el.btnRebuildProfile.disabled=true;
  el.btnRebuildProfile.textContent='重建中...';

  try{
    const res=await api.rebuildJobProfile(jobName,'boss',20);
    if(res.code!==200)throw new Error(res.message||'重建失败');
    // 更新当前岗位画像
    currentJobProfile=res.profile;
    // 同步到岗位输入框
    el.gapJobInput.value=jobName;
    // 显示画像预览
    renderJobProfilePreview(res.profile);
    toast('岗位画像已重建 (ID: '+res.job_profile_id+')');
  }catch(e){
    toast('重建失败: '+e.message);
  }finally{
    el.btnRebuildProfile.disabled=false;
    el.btnRebuildProfile.textContent='重建岗位画像';
  }
}

async function runGapAnalysis(){
  const job=el.gapJobInput.value.trim();
  if(!job){toast('请输入目标岗位');return;}
  const resumeText=parsedResumeText||(el.resumeTextInput?el.resumeTextInput.value:'').trim();

  el.btnGapAnalyze.disabled=true;
  profileAnalysisLoading=true;
  profileAnalysisError='';
  el.gapResult.innerHTML='<div class="msg-loading"></div><div style="text-align:center;margin-top:0.5rem;font-size:0.72rem;color:var(--text-dim)">正在分析岗位画像...</div>';

  try{
    // ── Step 1: 岗位画像（如果已有则复用）──
    let jobProfileId=currentJobProfile?._id||0;
    if(!jobProfileId){
      const jobRes=await api.analyzeJobProfile(job,20);
      if(jobRes.code!==200)throw new Error(jobRes.message||'岗位画像分析失败');
      currentJobProfile=normalizeJobProfile(jobRes.profile);
      currentJobProfile._id=jobRes.job_profile_id;
      jobProfileId=jobRes.job_profile_id;
    }
    el.gapResult.querySelector('.msg-loading').textContent='岗位画像完成，正在分析候选人...';

    // ── Step 2: 候选人画像 ──
    if(!resumeText&&!currentCandidateProfile){
      profileAnalysisLoading=false;
      renderProfileReport(null,null,null,'请上传简历或粘贴经历文本后再分析适配度');
      el.btnGapAnalyze.disabled=false;
      return;
    }
    let candRes;
    if(resumeText){
      candRes=await api.analyzeCandidateProfile(resumeText,userId);
    }else{
      candRes={code:200,profile:currentCandidateProfile};
    }
    if(candRes.code!==200)throw new Error(candRes.message||'候选人画像分析失败');
    currentCandidateProfile=candRes.profile;

    // ── Step 3: 综合适配分析 ──
    const fitRes=await api.createFitAnalysis(userId,jobProfileId,candRes.candidate_profile_id);
    if(fitRes.code!==200)throw new Error(fitRes.message||'适配分析失败');
    currentFitReport=fitRes.report;
    currentFitReport.id=fitRes.fit_analysis_id;
    currentCandidateProfile.id=currentCandidateProfile.id||candRes.candidate_profile_id;
    currentAnalysisMode=fitRes.analysis_mode||'agent';
    currentRuleScore=fitRes.rule_score||0;

    profileAnalysisLoading=false;
    profileAnalysisError='';
    renderProfileReport(currentJobProfile,currentCandidateProfile,currentFitReport);
    loadFitReportHistory();  // 刷新历史列表
    updateGapStepper();  // 更新流程状态

    // 后台加载旧技能差距数据作为补充
    try{
      const gapResult=await api.skillGap(job,collectGapSkills(),15,userProfileSkills);
      if(gapResult.code===200)renderGapResultCompact(gapResult);
    }catch(_){}

  }catch(e){
    profileAnalysisLoading=false;
    profileAnalysisError=e.message;
    // fallback 到旧流程
    try{
      const userSkills=collectGapSkills();
      const mergedProfile=[...userProfileSkills,...userSkills.map(skill=>({skill,source:'manual',confidence:0.65}))];
      const [result,screening]=await Promise.all([
        api.skillGap(job,userSkills,15,userProfileSkills),
        api.screeningReport(job,resumeText,mergedProfile,20)
      ]);
      if(screening.code===200)renderScreeningReport(screening.report,result);
      else renderGapResult(result);
      el.gapResult.insertAdjacentHTML('afterbegin','<div class="gap-confidence low" style="margin-bottom:0.8rem">⚠ 画像分析失败（'+esc(e.message)+'），已使用旧规则流程兜底</div>');
    }catch(fallbackErr){
      el.gapResult.innerHTML='<div class="radar-empty">请求出错: '+esc(e.message)+'</div>';
    }
  }finally{
    el.btnGapAnalyze.disabled=false;
  }
}
// ── v0.13 评估反馈按钮 ──
function renderEvalButtons(targetType,targetId){
  const btns=[
    {label:'准确',action:'correct',isCorrect:true},
    {label:'不准确',action:'wrong',isCorrect:false,errorType:'wrong_info'},
    {label:'缺少信息',action:'missing',isCorrect:false,errorType:'missing_info'},
  ];
  return '<div class="eval-feedback-row">'
    +btns.map(b=>'<button class="eval-btn" data-type="'+targetType+'" data-id="'+targetId
      +'" data-correct="'+b.isCorrect+'" data-error="'+(b.errorType||'')+'" data-action="'+b.action+'">'
      +esc(b.label)+'</button>').join('')
    +'</div>';
}
function renderFitEvalButtons(targetId){
  return '<div class="eval-feedback-row">'
    +'<span class="eval-label">评价报告：</span>'
    +[1,2,3,4,5].map(n=>'<button class="eval-btn eval-star" data-type="fit_analysis_report" data-id="'+targetId+'" data-rating="'+n+'">'+n+'</button>').join('')
    +'</div>';
}
// 事件委托：反馈按钮点击
document.addEventListener('click',async e=>{
  const btn=e.target.closest('.eval-btn');
  if(!btn)return;
  e.preventDefault();
  const targetType=btn.dataset.type;
  const targetId=parseInt(btn.dataset.id);
  const rating=parseInt(btn.dataset.rating||'0');
  const isCorrect=btn.dataset.correct==='true';
  const errorType=btn.dataset.error||'';
  try{
    await api.submitEvaluation({
      user_id:userId,
      target_type:targetType,
      target_id:targetId,
      rating:rating,
      is_correct:isCorrect,
      error_type:errorType,
    });
    // 视觉反馈
    btn.classList.add('eval-submitted');
    btn.textContent='✓ 已反馈';
    toast('反馈已记录');
  }catch(_){toast('反馈提交失败');}
});
// ── v0.11 画像 + Agent 适配报告渲染 ──
function renderProfileReport(jobProfile,candidateProfile,fitReport,errorMsg){
  if(errorMsg){
    el.gapResult.innerHTML='<div class="empty-state report-empty">'
      +'<span>!</span><strong>暂时无法生成报告</strong>'
      +'<p>'+esc(errorMsg)+'</p>'
      +'</div>';
    return;
  }
  const fit=fitReport||{};
  const job=jobProfile||{};
  const cand=candidateProfile||{};
  const fitLevel=fit.overall_fit_level||'moderate';
  const fitScore=Math.round(Number(fit.overall_score)||0);
  const mode=currentAnalysisMode||'agent';
  const dimLabel={
    capability_fit:'能力匹配',
    experience_relevance:'经历相关',
    growth_potential:'成长潜力',
    evidence_strength:'证据充分',
    risks_and_gaps:'风险短板',
  };
  const strengths=fit.strengths||[];
  const gaps=fit.gaps||[];
  const learning=fit.learning_plan||[];
  const interview=fit.interview_strategy||[];
  const refs=fit.evidence_refs||[];
  const resps=job.responsibilities||[];
  const musts=job.must_have_capabilities||[];
  const nices=job.nice_to_have_capabilities||[];
  const skills=(cand.skill_stack||[]).map(s=>s.skill||s);
  const projs=cand.projects||[];
  const achs=cand.achievements||[];
  const risks=cand.risk_points||[];
  const edu=cand.education_background||{};
  const reportId=fit.id||fitReport?.id||0;
  const jobId=currentJobProfile?.id||currentJobProfile?._id||job.id||0;
  const candidateId=currentCandidateProfile?.id||currentCandidateProfile?._id||cand.id||0;

  let h='<div class="analysis-report-shell">';

  h+='<section class="report-overview">';
  h+='<div class="report-score-panel">';
  h+='<div class="report-score-value">'+fitScore+'<small>/100</small></div>';
  h+='<div class="report-score-label">综合适配分</div>';
  h+='<span class="report-level screening-risk '+riskClass(fitLevel)+'">适配'+riskLabel(fitLevel)+'</span>';
  h+='</div>';
  h+='<div class="report-summary-panel">';
  h+='<span class="section-overline">差距分析结论</span>';
  h+='<h3>'+esc(job.job_name||gapCurrentJob||'目标岗位')+'</h3>';
  h+='<p>'+esc(fit.fit_summary||'已完成岗位画像与候选人画像对比。请结合五维评分和证据判断下一步行动。')+'</p>';
  h+='<div class="report-meta-row">';
  h+='<span>'+(mode==='agent'?'AI 综合分析':'规则兜底分析')+'</span>';
  h+='<span>'+esc(fit.confidence||'low')+' 置信度</span>';
  if(currentRuleScore)h+='<span>规则基准 '+Math.round(currentRuleScore)+'</span>';
  h+='</div></div></section>';

  h+='<section class="fit-dims-grid">';
  Object.entries(dimLabel).forEach(([key,label])=>{
    const dim=fit[key]||{};
    const score=Math.round(Number(dim.score)||0);
    const level=dim.level||'moderate';
    h+='<article class="fit-dim-card dim-'+esc(level)+'">';
    h+='<div class="fit-dim-head"><span class="fit-dim-label">'+esc(label)+'</span><span class="fit-dim-level">'+esc(level)+'</span></div>';
    h+='<div class="fit-dim-score">'+score+'<small>/100</small></div>';
    h+='<div class="fit-dim-summary">'+esc(dim.summary||'暂无维度说明')+'</div>';
    if((dim.evidence_refs||[]).length){
      h+='<div class="fit-dim-refs">';
      dim.evidence_refs.slice(0,2).forEach(r=>{h+='<span class="fit-dim-ref">'+esc(r)+'</span>';});
      h+='</div>';
    }
    h+='</article>';
  });
  h+='</section>';

  h+='<section class="fit-report-three-col">';
  h+='<article class="fit-report-card job-card"><div class="fit-report-card-title">岗位需要什么</div>';
  h+='<div class="profile-kv-grid">';
  h+='<div class="profile-kv-item"><span>岗位类型</span><b>'+esc(job.job_type||'未知')+'</b></div>';
  h+='<div class="profile-kv-item"><span>经验要求</span><b>'+esc(job.experience_requirement||'未明确')+'</b></div>';
  h+='<div class="profile-kv-item"><span>有效样本</span><b>'+(job.valid_sample_count||job.sample_count||0)+' 条 JD</b></div>';
  h+='</div>';
  if(musts.length){
    h+='<div class="profile-section"><div class="profile-section-title">必备能力</div><div class="profile-chip-row">';
    musts.slice(0,10).forEach(s=>{h+='<span class="profile-chip must">'+esc(s)+'</span>';});
    h+='</div></div>';
  }
  if(nices.length){
    h+='<div class="profile-section"><div class="profile-section-title">加分能力</div><div class="profile-chip-row">';
    nices.slice(0,6).forEach(s=>{h+='<span class="profile-chip nice">'+esc(s)+'</span>';});
    h+='</div></div>';
  }
  if(resps.length){
    h+='<div class="profile-section"><div class="profile-section-title">核心职责</div><ul class="profile-resp-list">';
    resps.slice(0,4).forEach(r=>{h+='<li>'+esc(r)+'</li>';});
    h+='</ul></div>';
  }
  h+='</article>';

  h+='<article class="fit-report-card cand-card"><div class="fit-report-card-title">你已经有什么</div>';
  h+='<div class="profile-kv-grid">';
  h+='<div class="profile-kv-item"><span>学历</span><b>'+esc(edu.degree||'未识别')+'</b></div>';
  h+='<div class="profile-kv-item"><span>技能</span><b>'+skills.length+' 项</b></div>';
  h+='<div class="profile-kv-item"><span>项目</span><b>'+projs.length+' 段</b></div>';
  h+='</div>';
  if(skills.length){
    h+='<div class="profile-section"><div class="profile-section-title">技能证据</div><div class="profile-chip-row">';
    skills.slice(0,12).forEach(s=>{h+='<span class="profile-chip must">'+esc(s)+'</span>';});
    h+='</div></div>';
  }
  if(projs.length){
    h+='<div class="profile-section"><div class="profile-section-title">项目经历</div><ul class="profile-resp-list">';
    projs.slice(0,3).forEach(p=>{
      const desc=p.description||p.name||'';
      h+='<li>'+esc(desc.length>100?desc.substring(0,100)+'…':desc)+'</li>';
    });
    h+='</ul></div>';
  }
  if(achs.length){
    h+='<div class="profile-section"><div class="profile-section-title">成果证据</div><ul class="profile-resp-list">';
    achs.slice(0,3).forEach(a=>{h+='<li>'+esc(a.description||'')+'</li>';});
    h+='</ul></div>';
  }
  if(risks.length){
    h+='<div class="profile-section"><div class="profile-section-title">信息提醒</div><ul class="profile-resp-list">';
    risks.slice(0,4).forEach(r=>{h+='<li>'+esc(r)+'</li>';});
    h+='</ul></div>';
  }
  h+='</article>';

  h+='<article class="fit-report-card action-card"><div class="fit-report-card-title">差距与下一步</div>';
  if(strengths.length){
    h+='<div class="profile-section"><div class="profile-section-title">可直接放大的优势</div><ul class="profile-resp-list">';
    strengths.slice(0,5).forEach(s=>{h+='<li>'+esc(s)+'</li>';});
    h+='</ul></div>';
  }
  if(gaps.length){
    h+='<div class="profile-section"><div class="profile-section-title">优先补齐的差距</div><ul class="profile-resp-list">';
    gaps.slice(0,6).forEach(g=>{h+='<li>'+esc(g)+'</li>';});
    h+='</ul></div>';
  }
  if(learning.length){
    h+='<div class="profile-section"><div class="profile-section-title">行动顺序</div><ol class="action-list">';
    learning.slice(0,5).forEach(l=>{h+='<li>'+esc(l)+'</li>';});
    h+='</ol></div>';
  }
  if(interview.length){
    h+='<div class="profile-section"><div class="profile-section-title">面试准备</div><ul class="profile-resp-list">';
    interview.slice(0,4).forEach(s=>{h+='<li>'+esc(s)+'</li>';});
    h+='</ul></div>';
  }
  h+='</article></section>';

  if(refs.length){
    h+='<section class="report-evidence surface-panel"><div><span class="section-overline">Evidence</span><h4>报告依据</h4></div><div class="report-evidence-list">';
    refs.slice(0,8).forEach(r=>{h+='<span>'+esc(r)+'</span>';});
    h+='</div></section>';
  }

  if(jobId||candidateId||reportId){
    h+='<section class="report-feedback">';
    if(jobId)h+=renderEvalButtons('job_profile',jobId);
    if(candidateId)h+=renderEvalButtons('candidate_profile',candidateId);
    if(reportId)h+=renderFitEvalButtons(reportId);
    h+='</section>';
  }
  if(reportId&&jobId&&candidateId)h+=renderAdvisorSection(reportId);

  h+='</div>';
  el.gapResult.innerHTML=h;

  if(reportId)bindAdvisorEvents(reportId);
}

function renderAdvisorSection(reportId){
  return '<div class="advisor-section">'
    +'<span class="section-overline">Report advisor</span>'
    +'<div class="fit-report-card-title">基于当前报告继续追问</div>'
    +'<div class="advisor-questions">'
    +'<button class="advisor-q-btn" data-q="为什么是'+esc(currentFitReport?.overall_fit_level||'这个')+'适配等级？">为什么是这个适配等级？</button>'
    +'<button class="advisor-q-btn" data-q="我最应该优先补什么？">我最应该优先补什么？</button>'
    +'<button class="advisor-q-btn" data-q="如何优化这份简历？">如何优化简历？</button>'
    +'<button class="advisor-q-btn" data-q="针对该岗位如何准备面试？">面试准备建议</button>'
    +'</div>'
    +'<div class="advisor-input-row">'
    +'<input type="text" class="advisor-input" id="advisorInput" placeholder="输入你的问题..." />'
    +'<button class="advisor-send-btn" id="btnAdvisorSend">提问</button>'
    +'</div>'
    +'<div id="advisorAnswer" class="advisor-answer"></div>'
    +'</div>';
}

function bindAdvisorEvents(reportId){
  document.querySelectorAll('.advisor-q-btn').forEach(btn=>{
    btn.addEventListener('click',()=>{askAdvisor(reportId,btn.dataset.q);});
  });
  const sendBtn=$('btnAdvisorSend');
  const input=$('advisorInput');
  if(sendBtn&&input){
    sendBtn.addEventListener('click',()=>{const q=input.value.trim();if(q)askAdvisor(reportId,q);});
    input.addEventListener('keydown',e=>{if(e.key==='Enter'){const q=input.value.trim();if(q)askAdvisor(reportId,q);}});
  }
}

async function askAdvisor(reportId,question){
  const answerEl=$('advisorAnswer');
  if(!answerEl)return;
  answerEl.innerHTML='<div style="display:flex;align-items:center;gap:0.5rem;"><div class="spinner" style="width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--blue);border-radius:50%;animation:spin 0.8s linear infinite;"></div><span>顾问分析中...</span></div>';
  try{
    const res=await api.askAdvisor(reportId,question);
    if(res.code===200&&res.answer){
      let a='<div class="advisor-answer-content">'+fmt(res.answer)+'</div>';
      if(res.evidence_refs&&res.evidence_refs.length){
        a+='<div class="advisor-refs"><span style="font-size:0.62rem;color:var(--text-muted);">依据: </span>';
        res.evidence_refs.slice(0,3).forEach(r=>{a+='<span class="advisor-ref-tag">'+esc(r)+'</span>';});
        a+='</div>';
      }
      a+='<div style="font-size:0.6rem;color:var(--text-muted);margin-top:0.3rem;">'+esc(res.analysis_mode==='agent'?'AI 分析':'规则分析')+'</div>';
      answerEl.innerHTML=a;
    }else{
      answerEl.innerHTML='<div style="color:var(--gold);font-size:0.72rem;">'+esc(res.message||'无法生成回答')+'</div>';
    }
  }catch(e){
    answerEl.innerHTML='<div style="color:var(--red);font-size:0.72rem;">请求失败: '+esc(e.message)+'</div>';
  }
}

// ── v0.22/v0.23 历史报告管理 ──
let fitReportHistoryOffset=0;
let fitReportHistoryHasMore=false;
let fitReportHistoryLoading=false;

function _renderFitReportItem(r){
  const score=Math.round(r.overall_score||0);
  return '<div class="fit-report-item" data-id="'+r.id+'">'
    +'<div class="fit-report-head"><span class="fit-report-job">'+esc(r.job_name||'未知岗位')+'</span>'
    +'<span class="screening-risk '+riskClass(r.overall_fit_level)+'">'+riskLabel(r.overall_fit_level)+'</span></div>'
    +'<div class="fit-report-meta"><span>'+score+'分</span><span>'+esc(r.confidence||'')+'</span><span>'+(r.created_at||'').slice(0,16)+'</span></div>'
    +'<div class="fit-report-summary">'+esc((r.fit_summary||'').slice(0,80))+'</div>'
    +'<div class="fit-report-actions">'
    +'<button class="gap-mini-btn" onclick="viewFitReport('+r.id+')">查看</button>'
    +'<button class="gap-mini-btn" onclick="rerunFitReport('+r.id+')">重新分析</button>'
    +'<button class="gap-mini-btn" style="color:var(--red);" onclick="deleteFitReport('+r.id+')">删除</button>'
    +'</div></div>';
}

async function loadFitReportHistory(){
  const container=$('fitReportHistory');
  if(!container)return;
  fitReportHistoryOffset=0;
  try{
    const res=await api.listFitReports(userId,'',20,0);
    const items=res.items||[];
    fitReportHistoryHasMore=!!res.has_more;
    fitReportHistoryOffset=res.next_offset||0;
    if(!items.length){
      container.innerHTML='<div style="font-size:0.68rem;color:var(--text-muted);padding:0.4rem;">暂无历史报告</div>';
      return;
    }
    container.innerHTML=items.map(r=>_renderFitReportItem(r)).join('')
      +(fitReportHistoryHasMore?'<button class="gap-mini-btn" id="btnLoadMoreReports" onclick="loadMoreFitReports()">加载更多</button>':'<div style="font-size:0.62rem;color:var(--text-muted);padding:0.3rem;">已加载全部</div>');
  }catch(e){
    console.error('加载历史报告失败:',e);
    container.innerHTML='<div style="font-size:0.68rem;color:var(--text-muted);padding:0.4rem;">加载失败，请刷新重试</div>';
  }
}

async function loadMoreFitReports(){
  if(fitReportHistoryLoading||!fitReportHistoryHasMore)return;
  fitReportHistoryLoading=true;
  const btn=$('btnLoadMoreReports');
  if(btn){btn.disabled=true;btn.textContent='加载中...';}
  try{
    const res=await api.listFitReports(userId,'',20,fitReportHistoryOffset);
    const items=res.items||[];
    fitReportHistoryHasMore=!!res.has_more;
    fitReportHistoryOffset=res.next_offset||0;
    const container=$('fitReportHistory');
    // 移除旧的"加载更多"按钮
    const oldBtn=$('btnLoadMoreReports');
    if(oldBtn)oldBtn.remove();
    // 追加新条目
    const tempDiv=document.createElement('div');
    tempDiv.innerHTML=items.map(r=>_renderFitReportItem(r)).join('');
    while(tempDiv.firstChild)container.appendChild(tempDiv.firstChild);
    // 追加新的按钮或"已加载全部"
    if(fitReportHistoryHasMore){
      container.insertAdjacentHTML('beforeend','<button class="gap-mini-btn" id="btnLoadMoreReports" onclick="loadMoreFitReports()">加载更多</button>');
    }else{
      container.insertAdjacentHTML('beforeend','<div style="font-size:0.62rem;color:var(--text-muted);padding:0.3rem;">已加载全部</div>');
    }
  }catch(e){
    toast('加载失败');
  }finally{
    fitReportHistoryLoading=false;
  }
}
async function viewFitReport(id){
  try{
    // 更新 URL（不刷新页面），支持刷新恢复
    const url=new URL(window.location);
    url.searchParams.set('report_id',id);
    history.pushState({report_id:id},'',url);
    let res=await api.getFitReport(id,userId);
    if(res.code!==200&&userId)res=await api.getFitReport(id);
    if(res.code!==200)throw new Error('报告不存在');
    const fitReport=res.report||{};
    const jp=res.job_profile||null;
    const cp=res.candidate_profile||null;
    const warnings=res.warnings||[];
    const reportUserId=fitReport.user_id||cp?.user_id||jp?.user_id||0;
    if(reportUserId&&reportUserId!==userId){
      userId=reportUserId;
      localStorage.setItem('js_user_id',String(userId));
    }
    currentJobProfile=jp;currentCandidateProfile=cp;currentFitReport=fitReport;
    renderProfileReport(jp,cp,fitReport);
    loadFitReportHistory();
    // 在报告顶部加返回按钮
    const reportEl=$('gapResult');
    const backBtn=document.createElement('div');
    backBtn.className='gap-confidence';
    backBtn.style.cssText='margin-bottom:0.6rem;cursor:pointer;text-align:left;';
    backBtn.innerHTML='← 返回分析';
    backBtn.onclick=()=>{history.pushState({},'',window.location.pathname);currentJobProfile=null;currentCandidateProfile=null;currentFitReport=null;el.gapResult.innerHTML='<div class="gap-result-empty">差距结果将在这里显示</div>';};
    reportEl.insertBefore(backBtn,reportEl.firstChild);
    if(warnings.length)toast('画像信息不完整：'+warnings.join('，'));
  }catch(e){
    toast('加载报告失败: '+e.message);
  }
}
async function rerunFitReport(id){
  if(!confirm('确定重新分析？将创建一份新报告。'))return;
  try{
    toast('正在重新分析...');
    const res=await api.rerunFitReport(id);
    if(res.code!==200)throw new Error('重跑失败');
    toast('分析完成');
    await viewFitReport(res.new_report_id);
    loadFitReportHistory();
  }catch(e){
    toast('重新分析失败: '+e.message);
  }
}
async function deleteFitReport(id){
  if(!confirm('确定删除此报告？'))return;
  try{
    const res=await api.deleteFitReport(id);
    if(res.code!==200)throw new Error('删除失败');
    toast('已删除');
    loadFitReportHistory();
  }catch(e){
    toast('删除失败: '+e.message);
  }
}

function renderGapResult(result){
  const ratio=Number(result.coverage_ratio)||0;
  const pct=Math.round(ratio*100);
  const status=ratio>=0.7?'竞争力强':ratio>=0.4?'需要提升':'差距较大';
  const matched=result.matched_skills||[];
  const missing=result.missing_skills||[];
  const priority=result.priority_order||[];
  const list=(items,cls)=>items.length?items.map(item=>{
    const name=skillTitle(item);
    const rate=typeof item==='string'?0:marketRate(item);
    const rateText=rate?'<span>'+rate+'%</span>':'';
    return '<li><span>'+esc(name)+'</span>'+rateText+'</li>';
  }).join(''):'<li class="gap-muted">暂无</li>';

  // 置信度提示
  const conf=result.confidence||'high';
  const confHtml=conf==='low'?'<div class="gap-confidence low">⚠ 当前岗位数据样本较少，结果仅供参考</div>'
    :conf==='medium'?'<div class="gap-confidence medium">ℹ 部分泛词已被过滤，结果基本可信</div>'
    :'<div class="gap-confidence high">✓ 数据置信度较高</div>';

  el.gapResult.innerHTML='<div class="gap-score">'
    +'<div><div class="gap-label">匹配度</div><div class="gap-score-num">'+pct+'%</div></div>'
    +'<span class="gap-status '+priorityClass(pct)+'">'+status+'</span>'
    +'</div>'
    +'<div class="gap-progress"><span style="width:'+Math.min(Math.max(pct,3),100)+'%"></span></div>'
    +confHtml
    +'<div class="gap-summary">'+esc(result.summary||'暂无摘要')+'</div>'
    +'<div class="gap-result-grid">'
    +'<div class="gap-result-block matched"><div class="gap-block-title">已匹配</div><ul>'+list(matched,'matched')+'</ul></div>'
    +'<div class="gap-result-block missing"><div class="gap-block-title">待补齐</div><ul>'+list(missing,'missing')+'</ul></div>'
    +'</div>'
    +'<div class="gap-priority"><div class="gap-block-title">学习优先级</div><div class="gap-priority-tags">'
    +(priority.length?priority.map((p,i)=>'<span>'+(i+1)+'. '+esc(p)+'</span>').join(''):'<span>暂无</span>')
    +'</div></div>';
}
function renderGapResultCompact(result){
  const ratio=Number(result.coverage_ratio)||0;
  const pct=Math.round(ratio*100);
  const matched=result.matched_skills||[];
  const missing=result.missing_skills||[];
  const priority=result.priority_order||[];
  const names=items=>items.length?items.slice(0,6).map(item=>'<span>'+esc(skillTitle(item))+'</span>').join(''):'<span>暂无</span>';
  return '<div class="screening-card screening-skill-brief">'
    +'<div class="gap-block-title">技能匹配明细</div>'
    +'<div class="screening-brief-score"><b>'+pct+'%</b><span>'+esc(result.summary||'')+'</span></div>'
    +'<p>已命中</p><div class="screening-chip-row matched">'+names(matched)+'</div>'
    +'<p>待补齐</p><div class="screening-chip-row missing">'+names(missing)+'</div>'
    +(priority.length?'<p>优先补强</p><div class="screening-chip-row priority">'+priority.slice(0,5).map(p=>'<span>'+esc(p)+'</span>').join('')+'</div>':'')
    +'</div>';
}
function riskLabel(risk){return risk==='low'?'较低':risk==='medium'?'中等':'较高';}
function riskClass(risk){return risk==='low'?'low':risk==='medium'?'medium':'high';}
function appendScreeningLoading(){
  el.gapResult.insertAdjacentHTML('beforeend','<div id="screeningReport" class="screening-report"><div class="msg-loading"></div></div>');
}
function renderScreeningError(msg){
  let box=$('screeningReport');
  if(!box){
    el.gapResult.insertAdjacentHTML('beforeend','<div id="screeningReport" class="screening-report"></div>');
    box=$('screeningReport');
  }
  box.innerHTML='<div class="screening-title">AI 初筛模拟</div><div class="gap-subtle">暂未生成报告：'+esc(msg||'请求失败')+'</div>';
}
function renderMiniList(items,empty='暂无'){
  return items&&items.length?items.map(x=>'<li>'+esc(typeof x==='string'?x:(x.name||x.skill||x.value||x.degree||x.major||''))+'</li>').join(''):'<li class="gap-muted">'+empty+'</li>';
}
function chipRow(items,empty='暂无'){
  if(!items||!items.length)return '<span class="muted">'+esc(empty)+'</span>';
  return items.map(x=>'<span>'+esc(typeof x==='string'?x:(x.name||x.skill||x.value||x.degree||x.major||''))+'</span>').join('');
}
function profileEvidenceList(items,empty='暂无'){
  if(!items||!items.length)return '<li class="gap-muted">'+esc(empty)+'</li>';
  return items.slice(0,4).map(x=>{
    const text=typeof x==='string'?x:(x.evidence||x.value||x.degree||x.major||x.name||'');
    return '<li>'+esc(text)+'</li>';
  }).join('');
}
function renderScreeningReport(report,gapResult=null){
  const job=report.job_profile||{};
  const cand=report.candidate_profile||{};
  const dims=report.dimension_scores||{};
  const missing=report.missing_requirements?.skills||[];
  const matched=report.matched_requirements?.skills||[];
  const issues=report.blocking_issues||[];
  const concerns=report.concerns||[];
  const suggestions=report.improvement_suggestions||[];
  const edu=cand.education||{};
  const exp=cand.experience||{};
  const candidateSkills=(cand.skills||[]).map(s=>typeof s==='string'?s:s.skill).filter(Boolean);
  const educationItems=[edu.degree,edu.major,edu.graduation_year].filter(Boolean);
  el.gapResult.innerHTML='<div id="screeningReport" class="screening-report">'
    +'<div class="screening-head">'
    +'<div><div class="screening-title">AI 初筛模拟</div><div class="gap-subtle">'+esc(job.job_type||'未知')+' · '+esc(job.target_audience||'未明确')+'</div></div>'
    +'<div class="screening-score"><span>'+Math.round(Number(report.score)||0)+'</span><small>/100</small></div>'
    +'<span class="screening-risk '+riskClass(report.pass_risk)+'">风险'+riskLabel(report.pass_risk)+'</span>'
    +'</div>'
    +'<div class="screening-summary">'+esc(report.summary||'暂无摘要')+'</div>'
    +'<div class="profile-card-grid">'
    +'<article class="profile-card job-profile-card">'
    +'<div class="profile-card-head"><div><div class="profile-card-title">岗位画像</div><div class="profile-card-sub">'+esc(job.job_name||'目标岗位')+'</div></div><span>'+esc(job.job_type||'未知')+'</span></div>'
    +'<div class="profile-kv"><span>用工类型</span><b>'+esc(job.employment_type||'未明确')+'</b></div>'
    +'<div class="profile-kv"><span>面向人群</span><b>'+esc(job.target_audience||'未明确')+'</b></div>'
    +'<div class="profile-kv"><span>样本来源</span><b>'+esc(String(job.sample?.jd_count||0))+' 条 JD</b></div>'
    +'<p>必备技能</p><div class="screening-chip-row must">'+chipRow(job.must_have||[])+'</div>'
    +'<p>加分技能</p><div class="screening-chip-row">'+chipRow(job.nice_to_have||[],'暂无')+'</div>'
    +'<p>学历 / 专业 / 经验</p><ul>'+profileEvidenceList([...(job.education_requirements||[]),...(job.major_requirements||[]),...(job.experience_requirements||[])],'未明确硬性要求')+'</ul>'
    +'<p>业务与软性要求</p><div class="screening-chip-row">'+chipRow([...(job.business_domains||[]),...(job.soft_requirements||[])].slice(0,8),'暂无明显要求')+'</div>'
    +'</article>'
    +'<article class="profile-card candidate-profile-card">'
    +'<div class="profile-card-head"><div><div class="profile-card-title">候选人画像</div><div class="profile-card-sub">'+esc(cand.parser==='llm'?'LLM 解析':'规则解析')+'</div></div><span>'+esc(String(candidateSkills.length))+' 技能</span></div>'
    +'<div class="profile-kv"><span>教育背景</span><b>'+esc(educationItems.join(' · ')||'未明确')+'</b></div>'
    +'<div class="profile-kv"><span>工作年限</span><b>'+esc(exp.experience_years!=null?exp.experience_years+'年':'未知')+(exp.experience_years_confidence==='inferred'?' (推算)':'')+'</b></div>'
    +'<div class="profile-kv"><span>经历证据</span><b>'+esc((exp.has_internship?'实习 ':'')+(exp.has_project?'项目':'')||'不足')+'</b></div>'
    +'<p>已识别技能</p><div class="screening-chip-row matched">'+chipRow(candidateSkills.slice(0,12),'暂未识别')+'</div>'
    +'<p>项目 / 实习经历</p><ul>'+profileEvidenceList([...(exp.internships||[]),...(exp.work_experience||[]),...(exp.projects||[])],'未识别到明确经历')+'</ul>'
    +'<p>量化成果</p><ul>'+profileEvidenceList(exp.metrics||[],'未识别到量化成果')+'</ul>'
    +'</article>'
    +'</div>'
    +'<div class="screening-dims">'
    +Object.entries(dims).map(([k,v])=>'<div><span>'+esc({skills:'技能',education:'学历',major:'专业',experience:'经历',evidence:'证据'}[k]||k)+'</span><b>'+esc(String(v))+'</b></div>').join('')
    +'</div>'
    +'<div class="screening-grid">'
    +'<div class="screening-card matched"><div class="gap-block-title">已命中要求</div><ul>'+renderMiniList(matched.map(s=>typeof s==='string'?s:s.name||s.skill))+'</ul></div>'
    +'<div class="screening-card missing"><div class="gap-block-title">主要缺口</div><ul>'+renderMiniList(missing.map(s=>typeof s==='string'?s:s.name||s.skill))+'</ul></div>'
    +'</div>'
    +(concerns.length?'<div class="screening-card concern"><div class="gap-block-title">扣分分析</div><ul>'+concerns.map(c=>'<li><span class="concern-dim">'+esc({skills:'技能',education:'学历',major:'专业',experience:'经历',evidence:'证据'}[c.dimension]||c.dimension)+'</span> <span class="concern-score">'+esc(String(c.score))+'/'+esc(String(c.max))+'</span> <span class="concern-reason">'+esc(c.reason)+'</span></li>').join('')+'</ul></div>':'')
    +(issues.length?'<div class="screening-card warn"><div class="gap-block-title">可能被筛原因</div><ul>'+renderMiniList(issues)+'</ul></div>':'')
    +(suggestions.length?'<div class="screening-card"><div class="gap-block-title">简历改写建议</div><ul>'+renderMiniList(suggestions)+'</ul></div>':'')
    +(gapResult?renderGapResultCompact(gapResult):'')
    +'</div>';
}

// ── 技能反馈（后端为主，localStorage 兜底） ──
let _feedbackCache={};
function getSkillFeedback(job,skill){
  const fb=_feedbackCache[skill];
  if(!fb)return null;
  if(fb.user_rejected)return{action:'reject'};
  if(fb.user_marked_important)return{action:'important'};
  return null;
}
async function loadFeedbackSummary(job){
  try{
    const r=await fetch('/skill_feedback/summary?job_name='+encodeURIComponent(job)+'&user_id='+userId);
    const d=await r.json();
    _feedbackCache=d.summary||{};
    renderFeedbackSummary(job);
  }catch(_){
    // 兜底：从 localStorage 读
    try{
      const local=JSON.parse(localStorage.getItem('joblab_skill_feedback')||'[]').filter(f=>f.job_name===job);
      _feedbackCache={};
      local.forEach(f=>{_feedbackCache[f.skill]={[f.action+'_count']:1,['user_'+(f.action==='reject'?'rejected':'marked_important')]:true};});
      renderFeedbackSummary(job);
    }catch(__){}
  }
}
async function addSkillFeedback(job,skill,action){
  // 先乐观更新本地
  try{
    const all=JSON.parse(localStorage.getItem('joblab_skill_feedback')||'[]');
    const idx=all.findIndex(f=>f.job_name===job&&f.skill===skill&&f.action===action);
    if(idx<0)all.push({job_name:job,skill,action,created_at:new Date().toISOString()});
    localStorage.setItem('joblab_skill_feedback',JSON.stringify(all));
  }catch(_){}
  try{
    await fetch('/skill_feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:userId,job_name:job,skill_name:skill,action})});
    await loadFeedbackSummary(job);
  }catch(_){
    toast('反馈暂存，本次未同步');
  }
}
function clearJobFeedback(job){
  _feedbackCache={};
  try{
    const all=JSON.parse(localStorage.getItem('joblab_skill_feedback')||'[]');
    localStorage.setItem('joblab_skill_feedback',JSON.stringify(all.filter(f=>f.job_name!==job)));
  }catch(_){}
}
function renderFeedbackSummary(job){
  const el=$('gapFeedbackSummary');
  if(!el)return;
  let rej=0,imp=0;
  Object.values(_feedbackCache).forEach(fb=>{rej+=fb.reject_count||0;imp+=fb.important_count||0;});
  if(!rej&&!imp){el.innerHTML='';return;}
  let h='<span class="fb-summary-item">社区标记：';
  if(rej)h+='<span class="fb-rej">✕ '+rej+' 个非技能</span>';
  if(imp)h+=(rej?' · ':'')+'<span class="fb-imp">★ '+imp+' 个重要</span>';
  h+='</span>';
  el.innerHTML=h;
}
function applyFeedbackToSkill(skill,job){
  const fb=_feedbackCache[skill];
  if(!fb)return{cls:'',community:false};
  const userRej=fb.user_rejected;
  const userImp=fb.user_marked_important;
  const communityRej=(fb.reject_count||0)>=3;
  const communityImp=(fb.important_count||0)>=3;
  let cls='';
  if(userRej)cls='fb-rejected';
  else if(communityRej)cls='fb-community-rejected';
  if(userImp)cls+=' fb-important';
  else if(communityImp)cls+=' fb-community-important';
  return{cls,community:communityRej||communityImp};
}

if(el.btnGapLoad)el.btnGapLoad.addEventListener('click',loadGapMarketSkills);
if(el.gapJobInput)el.gapJobInput.addEventListener('keydown',e=>{if(e.key==='Enter')loadGapMarketSkills();});
if(el.btnGapAnalyze)el.btnGapAnalyze.addEventListener('click',runGapAnalysis);
if(el.btnGapClear)el.btnGapClear.addEventListener('click',()=>{
  if(el.gapSkillList)el.gapSkillList.querySelectorAll('.gap-skill-check').forEach(c=>{c.checked=false;});
  if(el.gapExtraInput)el.gapExtraInput.value='';
});
if(el.btnResumeProfile)el.btnResumeProfile.addEventListener('click',extractResumeProfile);

// ── v0.26 Tab 切换 ──
document.querySelectorAll('.gap-tab').forEach(tab=>{
  tab.addEventListener('click',()=>switchGapTab(tab.dataset.tab));
});
// 文件选择时显示文件名
if(el.resumeFileInput){
  el.resumeFileInput.addEventListener('change',()=>{
    const f=el.resumeFileInput.files?.[0];
    const statusEl=$('resumeParseStatus');
    if(f&&statusEl){
      statusEl.textContent='已选择: '+f.name+' ('+(f.size/1024).toFixed(0)+'KB)';
      statusEl.style.color='var(--text-dim)';
      parsedResumeText=''; // 新文件，清空旧解析文本
    }
  });
}
const gapTagsToggle=$('gapTagsToggle');
if(gapTagsToggle)gapTagsToggle.addEventListener('click',()=>{
  const tags=$('gapQuickTags');
  const toggle=$('gapTagsToggle');
  if(tags)tags.classList.toggle('collapsed');
  if(toggle)toggle.classList.toggle('open');
});
if(el.btnGapClearFeedback)el.btnGapClearFeedback.addEventListener('click',async()=>{
  if(!gapCurrentJob)return;
  clearJobFeedback(gapCurrentJob);
  if(gapJobProfile)renderMarketProfilePreview(gapJobProfile,'','high',0);
  else renderGapSkillList('',0,0);
  renderFeedbackSummary(gapCurrentJob);
  toast('已清空「'+gapCurrentJob+'」的反馈');
});

// ── v0.25 Boss 采集面板事件 ──
if(el.btnToggleBossCapture){
  el.btnToggleBossCapture.addEventListener('click',()=>{
    console.log('Boss capture toggle clicked');
    const body=el.bossCaptureBody;
    const btn=el.btnToggleBossCapture;
    if(!body){console.error('bossCaptureBody not found');return;}
    body.classList.toggle('collapsed');
    btn.textContent=body.classList.contains('collapsed')?'展开':'折叠';
    console.log('Collapsed:',body.classList.contains('collapsed'));
    // 展开时刷新浏览器状态
    if(!body.classList.contains('collapsed'))refreshBossBrowserStatus();
  });
}else{
  console.warn('btnToggleBossCapture not found');
}
if(el.btnStartBrowser)el.btnStartBrowser.addEventListener('click',startBossBrowser);
if(el.btnStopBrowser)el.btnStopBrowser.addEventListener('click',stopBossBrowser);
if(el.btnRetryAfterLogin)el.btnRetryAfterLogin.addEventListener('click',retryAfterLogin);
if(el.btnBossCapture)el.btnBossCapture.addEventListener('click',runBossCapture);
if(el.btnBossManualImport)el.btnBossManualImport.addEventListener('click',runBossManualImport);
if(el.btnRebuildProfile)el.btnRebuildProfile.addEventListener('click',runRebuildJobProfile);
if(el.bossJobInput){
  el.bossJobInput.addEventListener('keydown',e=>{if(e.key==='Enter')runBossCapture();});
}

// ═══════════════════════════════════════════════
// legacy: 深度研究（保留代码，默认隐藏）
// ═══════════════════════════════════════════════
if(el.btnResearch){
  el.btnResearch.addEventListener('click',async()=>{
    const text=el.researchInput.value.trim();if(!text)return;
    el.researchTimeline.innerHTML='<span style="color:var(--green)">拆解需求</span> → <span style="color:var(--blue)">并行执行中...</span> → 聚合结果';
    el.researchBody.innerHTML='<div class="msg-loading"></div>';
    try{
      const result=await fetch('/research',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({topic:text})}).then(r=>r.json());
      if(result.knowledge?.length){
        el.researchTimeline.innerHTML='<span style="color:var(--green)">拆解需求</span> → <span style="color:var(--green)">并行执行</span> → <span style="color:var(--green)">聚合结果</span>';
        const cats={技能:'skill',薪资:'salary',公司:'company',面试:'interview'};
        let h='<div class="research-grid">';
        result.knowledge.forEach((card,i)=>{
          const lines=card.split('\n'),title=lines[0].replace('## ',''),items=lines.filter(l=>l.startsWith('- ')),src=lines.find(l=>l.startsWith('*'));
          const cat=Object.entries(cats).find(([k])=>title.includes(k))?.[1]||'default';
          h+='<div class="research-card cat-'+cat+'" style="animation-delay:'+(i*0.08)+'s">'
            +'<div class="rc-title">'+esc(title)+'</div>'
            +'<div class="rc-items">'+items.map(it=>'<span class="rc-item">'+esc(it.replace('- ',''))+'</span>').join('')+'</div>'
            +(src?'<div class="rc-source">'+esc(src.replace(/\*/g,''))+'</div>':'')
            +'</div>';
        });
        h+='</div>';
        el.researchBody.innerHTML=h;
      }else{
        el.researchBody.innerHTML='<div class="radar-empty">未获取到研究结果</div>';
      }
    }catch(e){
      el.researchBody.innerHTML='<div class="radar-empty">请求出错: '+esc(e.message)+'</div>';
    }
  });
}
if(el.researchInput)el.researchInput.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();el.btnResearch.click();}});

// ═══════════════════════════════════════════════
// 视图4: 用户中心
// ═══════════════════════════════════════════════
async function loadUserCenter(){
  const name=localStorage.getItem('js_username')||('用户_'+threadId.slice(0,8));
  el.userName.textContent=name;el.userAvatar.textContent=name[0].toUpperCase();
  el.userMeta.textContent='用户ID: '+userId+' · ID不会因刷新而改变';
  // 统计
  const convs=await api.conversations(userId),jobs=await api.analyzedJobs(),s=await api.stats();
  el.userStats.innerHTML='<div class="stat-card"><div class="stat-num">'+convs.length+'</div><div class="stat-label">对话数</div></div><div class="stat-card"><div class="stat-num">'+jobs.length+'</div><div class="stat-label">分析岗位</div></div><div class="stat-card"><div class="stat-num">'+(s.skill_count||0)+'</div><div class="stat-label">技能库</div></div><div class="stat-card"><div class="stat-num">'+(s.jd_count||0)+'</div><div class="stat-label">JD总量</div></div>';
  // 用户切换列表
  loadUserSwitchList();
}
async function loadUserSwitchList(){
  const list=el.userSwitchList||$('userSwitchList');
  if(!list)return;
  let users=[];
  try{users=await api.users();}catch(_){}
  const saved=savedUsers();
  saved.forEach(s=>{if(!users.find(u=>u.id===s.id))users.push(s);});
  list.innerHTML=users.map(u=>'<div class="user-switch-item'+(u.id===userId?' current':'')+'" data-uid="'+u.id+'" data-username="'+esc(u.username)+'"><span class="user-switch-name">'+esc(u.username)+'</span><button class="btn-user-delete" data-uid="'+u.id+'" title="删除用户">×</button></div>').join('');
  list.querySelectorAll('.user-switch-item').forEach(item=>{item.addEventListener('click',()=>switchUser(parseInt(item.dataset.uid)));});
  list.querySelectorAll('.btn-user-delete').forEach(btn=>btn.addEventListener('click',e=>{e.stopPropagation();deleteUser(parseInt(btn.dataset.uid));}));
}
async function switchUser(uid){
  if(uid===userId)return;
  userId=uid;localStorage.setItem('js_user_id',String(uid));
  const saved=savedUsers();
  const found=saved.find(u=>u.id===uid);
  const row=document.querySelector('.user-switch-item[data-uid="'+uid+'"]');
  const username=found?.username||row?.dataset.username;
  if(username)localStorage.setItem('js_username',username);
  convMessageCache.clear();
  threadId=crypto.randomUUID();allMessages=[];el.msgList.innerHTML='';
  refreshSidebar();loadUserCenter();toast('已切换到 '+ (username||('用户'+uid)));
}
async function deleteUser(uid){
  if(!uid)return;
  const deletingCurrent=uid===userId;
  const row=document.querySelector('.user-switch-item[data-uid="'+uid+'"]');
  if(row)row.remove();
  removeSavedUser(uid);
  localStorage.removeItem(lastThreadKey(uid));
  convMessageCache.clear();

  try{
    const result=await api.deleteUser(uid);
    if(!result.deleted){
      toast('用户不存在或已删除');
    }

    if(deletingCurrent){
      let users=await api.users();
      if(!users.length){
        const r=await fetch('/user');
        const d=await r.json();
        users=[{id:d.user_id,username:d.username}];
      }
      const next=users[0];
      userId=next.id;
      localStorage.setItem('js_user_id',String(next.id));
      localStorage.setItem('js_username',next.username);
      threadId=crypto.randomUUID();
      allMessages=[];
      el.msgList.innerHTML='';
    }

    await refreshSidebar();
    await loadUserCenter();
    toast('已删除用户');
  }catch(e){
    await loadUserCenter();
    toast('删除失败: '+e.message);
  }
}
$('btnAddUser').addEventListener('click',async()=>{
  const name=prompt('输入新用户名（留空则随机）：');if(name===null)return;
  const r=await fetch('/user?username='+encodeURIComponent(name||''));
  const d=await r.json();
  const saved=savedUsers();
  saved.push({id:d.user_id,username:d.username});
  setSavedUsers(saved);
  switchUser(d.user_id);
});

// ═══════════════════════════════════════════════
// 侧边栏
// ═══════════════════════════════════════════════
async function refreshSidebar(){
  if(!userId)return;
  const convs=await api.conversations(userId);
  // 过滤掉本地已标记删除的会话（防止异步删除未完成时重新出现）
  const filtered=convs.filter(c=>!deletedThreads.has(c.thread_id));
  const checked=await Promise.all(filtered.map(async c=>({...c,messages:await loadConversationMessages(c.thread_id)})));
  const visible=checked.filter(c=>!c.recovered||c.messages.length>0);
  el.convList.innerHTML=visible.length?visible.map(c=>'<div class="conv-item-wrap'+(c.thread_id===threadId?' active':'')+'" data-tid="'+c.thread_id+'"><button class="conv-item" data-tid="'+c.thread_id+'">'+esc(c.title||'未命名')+'</button><button class="btn-conv-delete" data-tid="'+c.thread_id+'" title="删除对话">×</button></div>').join(''):'<span style="color:var(--text-muted);font-size:0.62rem;padding:0.4rem;">暂无对话</span>';
  el.convList.querySelectorAll('.conv-item').forEach(b=>b.addEventListener('click',()=>switchConv(b.dataset.tid)));
  el.convList.querySelectorAll('.btn-conv-delete').forEach(b=>b.addEventListener('click',e=>{e.stopPropagation();deleteConversation(b.dataset.tid);}));
}

// ── 事件 ──
el.btnSend.addEventListener('click',sendMessage);
el.chatInput.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage();}});
el.btnNewChat.addEventListener('click',()=>{threadId=crypto.randomUUID();allMessages=[];el.msgList.innerHTML='';localStorage.removeItem(lastThreadKey());refreshSidebar();});

// ── 启动 ──
(async function init(){
  if(!userId){
    const users=await api.users().catch(()=>[]);
    if(users.length){
      userId=users[0].id;
      localStorage.setItem('js_user_id',String(userId));
      localStorage.setItem('js_username',users[0].username);
    }else{
      const r=await fetch('/user');const d=await r.json();userId=d.user_id;localStorage.setItem('js_user_id',String(userId));localStorage.setItem('js_username',d.username);
    }
  }
  const lastTid=localStorage.getItem(lastThreadKey());
  if(lastTid){threadId=lastTid;}
  const msgs=lastTid?await loadConversationMessages(threadId,{fresh:true}):[];
  allMessages=msgs;
  if(lastTid&&!msgs.length){
    localStorage.removeItem(lastThreadKey());
    convMessageCache.delete(lastTid);
    threadId=crypto.randomUUID();
  }
  if(!msgs.length){
    el.msgList.innerHTML='<div style="text-align:center;margin:auto;padding:2rem;color:var(--text-muted);font-size:0.82rem;line-height:1.8;">'
      +'<div style="font-size:1.5rem;margin-bottom:0.5rem;">◇</div>'
      +'<div>欢迎使用 <b style="color:var(--text);">JobLab</b></div>'
      +'<div style="margin-top:0.3rem;">输入岗位名称开始分析，例如：<span style="color:var(--blue);cursor:pointer;" onclick="document.getElementById(\'chatInput\').value=\'Python后端\';">Python后端</span></div>'
      +'</div>';
  }else{
    msgs.forEach(m=>addMsg(m.role,fmt(m.content)));
  }
  refreshSidebar();

  // v0.24: URL 参数 report_id 自动加载报告，始终加载历史列表
  const urlReportId=new URLSearchParams(window.location.search).get('report_id');
  if(urlReportId){
    switchView('gap');
    switchGapTab('fit');
    viewFitReport(urlReportId);
    loadFitReportHistory();
  }else{
    const savedView=localStorage.getItem('js_current_view');
    switchView(savedView==='radar'||savedView==='user'?savedView:'gap');
  }
})();
