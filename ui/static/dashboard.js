/* ═══════════════════════════════════════════════════
   JobLab · 岗位数据看板 v0.44
   ECharts 图表渲染 + 筛选联动 + 数据洞察 + 技能模块
   ═══════════════════════════════════════════════════ */

// ── ECharts 主题色（Dark Constellation）──
const DB_THEME = {
  bg: 'transparent',
  textColor: '#a0aec0',
  axisLine: 'rgba(255,255,255,0.06)',
  teal: '#3dd6c8',
  amber: '#f59e0b',
  green: '#4ade80',
  red: '#f87171',
  blue: '#3b82f6',
  purple: '#a78bfa',
  palette: ['#3dd6c8', '#f59e0b', '#3b82f6', '#4ade80', '#f87171', '#a78bfa', '#f472b6', '#38bdf8', '#facc15', '#818cf8'],
};

// ── 图表实例管理 ──
const dashboardCharts = {};
let _dashboardLoading = false;

function disposeDashboardCharts() {
  Object.values(dashboardCharts).forEach(c => {
    try { if (c && typeof c.dispose === 'function' && !c.isDisposed()) c.dispose(); } catch(_){}
  });
  Object.keys(dashboardCharts).forEach(k => delete dashboardCharts[k]);
}

// ── 当前筛选状态 ──
let _currentFilters = { job_keyword: '', job_type: '', start_date: '', end_date: '' };
let _fitLevelData = [];
let _fitFocusLevel = '';
let _fitFocusLoading = false;

// ── 技能模块状态 ──
let _skillQueryJob = '';       // 当前技能模块查询的岗位（空=全景模式）
let _skillLoading = false;
let _skillOverviewData = null; // 缓存全景数据

// ── 筛选栏绑定 ──
function initDashboardFilters() {
  const applyBtn = document.getElementById('dbFilterApply');
  const resetBtn = document.getElementById('dbFilterReset');
  if (applyBtn) applyBtn.addEventListener('click', applyDashboardFilters);
  if (resetBtn) resetBtn.addEventListener('click', resetDashboardFilters);

  // 口径弹窗
  const glossaryBtn = document.getElementById('dbGlossaryBtn');
  const glossaryModal = document.getElementById('dbGlossaryModal');
  const glossaryClose = document.getElementById('dbGlossaryClose');
  if (glossaryBtn && glossaryModal) {
    glossaryBtn.addEventListener('click', () => { glossaryModal.style.display = 'flex'; });
  }
  if (glossaryClose && glossaryModal) {
    glossaryClose.addEventListener('click', () => { glossaryModal.style.display = 'none'; });
  }
  if (glossaryModal) {
    glossaryModal.addEventListener('click', (e) => {
      if (e.target === glossaryModal) glossaryModal.style.display = 'none';
    });
  }

  const fitFocusBack = document.getElementById('fitFocusBack');
  if (fitFocusBack) fitFocusBack.addEventListener('click', exitFitFocus);

  // 技能模块查询
  initSkillModule();
}

// ── 技能模块初始化 ──
function initSkillModule() {
  const queryBtn = document.getElementById('btnSkillQuery');
  const clearBtn = document.getElementById('btnSkillClear');
  const input = document.getElementById('skillJobInput');

  if (queryBtn) queryBtn.addEventListener('click', () => querySkillForJob());
  if (clearBtn) clearBtn.addEventListener('click', () => clearSkillQuery());
  if (input) input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') querySkillForJob();
  });
}

async function querySkillForJob() {
  const input = document.getElementById('skillJobInput');
  const job = (input?.value || '').trim();
  if (!job) return;
  _skillQueryJob = job;
  updateSkillUIState();
  await loadSkillData(job);
}

function clearSkillQuery() {
  _skillQueryJob = '';
  const input = document.getElementById('skillJobInput');
  if (input) input.value = '';
  updateSkillUIState();
  // 恢复全景数据
  if (_skillOverviewData) {
    renderSkillChart(_skillOverviewData, '');
  } else {
    // 如果没有缓存，重新从看板数据加载
    loadDashboard();
  }
}

function updateSkillUIState() {
  const clearBtn = document.getElementById('btnSkillClear');
  const overline = document.getElementById('skillSectionOverline');
  const subtitle = document.getElementById('skillSectionSubtitle');
  const status = document.getElementById('skillQueryStatus');

  if (_skillQueryJob) {
    if (clearBtn) clearBtn.style.display = '';
    if (overline) overline.textContent = '岗位技能拆解';
    if (subtitle) subtitle.textContent = _skillQueryJob;
    if (status) status.textContent = '当前查询: ' + _skillQueryJob + '（模块内临时条件）';
  } else {
    if (clearBtn) clearBtn.style.display = 'none';
    if (overline) overline.textContent = '技能全景';
    if (subtitle) subtitle.textContent = '热度 × 岗位覆盖';
    if (status) status.textContent = '';
  }
}

async function loadSkillData(jobName) {
  if (_skillLoading) return;
  _skillLoading = true;

  const dom = document.getElementById('chartSkillTreemap');
  const detailPanel = document.getElementById('skillDetailPanel');
  if (dom) dom.innerHTML = '<div class="chart-empty">加载中…</div>';
  if (detailPanel) detailPanel.innerHTML = '';

  // 销毁旧图表
  if (dashboardCharts.skillTreemap) {
    try { dashboardCharts.skillTreemap.dispose(); } catch(_){}
    delete dashboardCharts.skillTreemap;
  }

  try {
    const resp = await fetch('/dashboard/skill_trend?job_name=' + encodeURIComponent(jobName));

    if (!resp.ok) {
      let errMsg = '请求失败 (HTTP ' + resp.status + ')';
      if (resp.status === 400) {
        try { const b = await resp.json(); errMsg = b.detail || '参数错误'; } catch(_) { errMsg = '参数错误'; }
      } else if (resp.status === 404) {
        errMsg = '岗位技能数据不存在';
      } else if (resp.status === 500) {
        errMsg = '服务器内部错误';
      }
      if (dom) dom.innerHTML = '<div class="chart-empty" style="color:#f87171;">' + esc(errMsg) + '</div>';
      return;
    }

    let data;
    try { data = await resp.json(); } catch(_) {
      if (dom) dom.innerHTML = '<div class="chart-empty" style="color:#f87171;">响应不是有效的 JSON</div>';
      return;
    }

    const skills = data.skills || [];
    if (!skills.length) {
      if (dom) dom.innerHTML = '<div class="chart-empty">未找到「' + esc(jobName) + '」的技能数据</div>';
      return;
    }

    // 转换为图表数据格式
    const chartData = skills.map(s => ({
      skill: s.skill,
      count: s.count,
      jobs: 1, // 单岗位模式下 jobs=1
      last_seen: s.last_seen || '',
    }));

    renderSkillChart(chartData, jobName);
  } catch (e) {
    console.error('loadSkillData error', e);
    if (dom) dom.innerHTML = '<div class="chart-empty" style="color:#f87171;">网络异常: ' + esc(e.message) + '</div>';
  } finally {
    _skillLoading = false;
  }
}

// ── 技能图表渲染（全景/单岗位双模式）──
function renderSkillChart(skillData, queryJob) {
  const dom = document.getElementById('chartSkillTreemap');
  if (!dom || !skillData || !skillData.length) {
    if (dom) dom.innerHTML = '<div class="chart-empty">暂无数据</div>';
    return;
  }

  // 销毁旧图表
  if (dashboardCharts.skillTreemap) {
    try { dashboardCharts.skillTreemap.dispose(); } catch(_){}
    delete dashboardCharts.skillTreemap;
  }

  const chart = safeInit(dom);
  if (!chart) return;
  dashboardCharts.skillTreemap = chart;

  if (queryJob) {
    // 单岗位模式：横向技能排行
    renderSkillBarChart(chart, skillData, queryJob);
  } else {
    // 全景模式：气泡矩阵
    renderSkillBubbleChart(chart, skillData);
  }
}

// ── 全景模式：气泡矩阵 ──
function renderSkillBubbleChart(chart, skillData) {
  const data = skillData.slice(0, 15);
  const maxCount = Math.max(...data.map(s => s.count), 1);
  const maxJobs = Math.max(...data.map(s => s.jobs), 1);

  chart.setOption({
    tooltip: {
      backgroundColor: '#111b27',
      borderColor: 'rgba(67,215,197,.28)',
      textStyle: { color: '#dce6f2', fontSize: 12 },
      formatter: (info) => {
        const d = info.data;
        let html = '<strong style="color:#43d7c5">' + esc(d.name) + '</strong>'
          + '<br/>出现次数　' + d.count
          + '<br/>岗位覆盖　' + d.jobs + ' 个';
        if (d.last_seen) html += '<br/>最近出现　' + d.last_seen;
        return html;
      },
    },
    grid: { left: 72, right: 34, top: 8, bottom: 32 },
    xAxis: {
      type: 'value',
      name: '岗位覆盖数',
      nameLocation: 'middle',
      nameGap: 24,
      min: 0,
      max: Math.ceil(maxJobs * 1.15),
      axisLine: { lineStyle: { color: 'rgba(148,163,184,.15)' } },
      axisTick: { show: false },
      axisLabel: { color: '#627187', fontSize: 9 },
      splitLine: { lineStyle: { color: 'rgba(148,163,184,.07)', type: 'dashed' } },
      nameTextStyle: { color: '#627187', fontSize: 9 },
    },
    yAxis: {
      type: 'category',
      data: data.map(s => s.skill),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: '#9daabc', fontSize: 9, width: 62, overflow: 'truncate' },
      splitLine: { show: false },
    },
    series: [{
      type: 'scatter',
      data: data.map((s, i) => ({
        name: s.skill,
        value: [s.jobs, s.skill],
        count: s.count,
        jobs: s.jobs,
        last_seen: s.last_seen || '',
        symbolSize: 8 + (s.count / maxCount) * 18,
        itemStyle: { color: interpolateColor(i / Math.max(data.length - 1, 1)) },
      })),
      label: {
        show: true,
        position: 'right',
        distance: 6,
        formatter: p => p.data.count,
        fontSize: 9,
        color: '#c4ceda',
      },
      itemStyle: {
        borderColor: 'rgba(220,255,250,.8)',
        borderWidth: 1,
        shadowBlur: 10,
        shadowColor: 'rgba(67,215,197,.18)',
      },
      emphasis: { scale: 1.3, label: { color: '#fff', fontWeight: 600 } },
      animationDuration: 700,
    }],
  });

  // 点击技能 → 显示详情
  chart.on('click', (params) => {
    if (params.data) {
      showSkillDetail(params.data, '');
    }
  });
}

// ── 单岗位模式：横向技能排行 ──
function renderSkillBarChart(chart, skillData, queryJob) {
  const data = skillData.slice(0, 15);
  const maxCount = Math.max(...data.map(s => s.count), 1);

  chart.setOption({
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#111b27',
      borderColor: 'rgba(67,215,197,.28)',
      textStyle: { color: '#dce6f2', fontSize: 12 },
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(67,215,197,.035)' } },
      formatter: function(p) {
        const d = p[0];
        const item = data[d.dataIndex];
        let html = '<span style="color:#7f8da1">技能</span>　' + esc(d.name)
          + '<br/><span style="color:#7f8da1">出现次数</span>　<strong style="color:#43d7c5">' + d.value + '</strong>';
        if (item && item.last_seen) html += '<br/><span style="color:#7f8da1">最近出现</span>　' + item.last_seen;
        return html;
      },
    },
    grid: { left: 100, right: 42, top: 10, bottom: 28 },
    xAxis: {
      type: 'value',
      max: Math.ceil(maxCount * 1.12),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: '#607086', fontSize: 10 },
      splitLine: { lineStyle: { color: 'rgba(148,163,184,.075)', type: 'dashed' } },
    },
    yAxis: {
      type: 'category',
      data: data.map(s => s.skill).reverse(),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: '#aeb9c8', fontSize: 11, width: 80, overflow: 'truncate', margin: 14 },
    },
    series: [{
      type: 'bar',
      data: data.map(s => s.count).reverse().map((value, index, arr) => ({
        value,
        itemStyle: { opacity: .64 + ((index + 1) / arr.length) * .36 },
      })),
      barWidth: 10,
      showBackground: true,
      backgroundStyle: { color: 'rgba(148,163,184,.055)', borderRadius: 8 },
      label: {
        show: true, position: 'right', distance: 8,
        color: '#d8e2ed', fontSize: 10, fontWeight: 600,
      },
      itemStyle: {
        borderRadius: 8,
        color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
          { offset: 0, color: '#249e98' },
          { offset: 1, color: '#52dcc9' },
        ]),
      },
      emphasis: { itemStyle: { color: '#74ead8', shadowBlur: 14, shadowColor: 'rgba(67,215,197,.3)' } },
      animationDuration: 700,
      animationDelay: idx => idx * 32,
      animationEasing: 'cubicOut',
    }],
  });

  // 点击技能 → 显示详情
  chart.on('click', (params) => {
    if (params.dataIndex !== undefined) {
      const item = data[data.length - 1 - params.dataIndex] || data[params.dataIndex];
      if (item) showSkillDetail(item, queryJob);
    }
  });
}

// ── 技能详情面板 ──
async function showSkillDetail(skillItem, queryJob) {
  const panel = document.getElementById('skillDetailPanel');
  if (!panel) return;

  const skillName = skillItem.name || skillItem.skill || '';
  const jobContext = queryJob || _currentFilters.job_keyword || '';
  const html = '<div class="skill-detail-card">'
    + '<div class="skill-detail-header">'
    + '  <span class="skill-detail-name">' + esc(skillName) + '</span>'
    + '  <span class="skill-detail-close" onclick="hideSkillDetail()">&times;</span>'
    + '</div>'
    + '<div class="skill-evidence-loading">正在查找包含该技能词的真实 JD 文本…</div>'
    + '<div class="skill-detail-meta">'
    + '  <span>出现 ' + (skillItem.count || 0) + ' 次</span>'
    + (skillItem.jobs ? '<span>覆盖 ' + skillItem.jobs + ' 个岗位</span>' : '')
    + '</div>'
    + '</div>';

  panel.innerHTML = html;

  try {
    const params = new URLSearchParams({ skill_name: skillName });
    if (jobContext) params.set('job_name', jobContext);
    const resp = await fetch('/dashboard/skill_evidence?' + params.toString());
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    if (!panel.querySelector('.skill-detail-card')) return;

    const evidence = data.evidence;
    const loading = panel.querySelector('.skill-evidence-loading');
    if (!loading) return;
    if (!evidence || !evidence.excerpt) {
      loading.className = 'skill-evidence-empty';
      loading.textContent = jobContext
        ? '当前岗位范围内暂未找到包含该技能词的 JD 原句。'
        : '暂未找到包含该技能词的 JD 原句。';
      return;
    }

    const sourceParts = [evidence.title || evidence.job_name, evidence.company]
      .filter(Boolean).map(esc);
    loading.className = 'skill-evidence-content';
    loading.innerHTML = '<div class="skill-evidence-label">JD 原文证据</div>'
      + '<blockquote>' + esc(evidence.excerpt) + '</blockquote>'
      + (sourceParts.length
        ? '<div class="skill-evidence-source">来源：' + sourceParts.join(' · ') + '</div>'
        : '');
  } catch (error) {
    const loading = panel.querySelector('.skill-evidence-loading');
    if (loading) {
      loading.className = 'skill-evidence-empty';
      loading.textContent = 'JD 文本证据加载失败，请稍后重试。';
    }
  }
}

function hideSkillDetail() {
  const panel = document.getElementById('skillDetailPanel');
  if (panel) panel.innerHTML = '';
}

function collectFilters() {
  return {
    job_keyword: (document.getElementById('dbFilterKeyword')?.value || '').trim(),
    job_type: (document.getElementById('dbFilterType')?.value || '').trim(),
    start_date: (document.getElementById('dbFilterStart')?.value || '').trim(),
    end_date: (document.getElementById('dbFilterEnd')?.value || '').trim(),
  };
}

function applyDashboardFilters() {
  _currentFilters = collectFilters();
  loadDashboard();
}

function resetDashboardFilters() {
  const el = (id) => document.getElementById(id);
  if (el('dbFilterKeyword')) el('dbFilterKeyword').value = '';
  if (el('dbFilterType')) el('dbFilterType').value = '';
  if (el('dbFilterStart')) el('dbFilterStart').value = '';
  if (el('dbFilterEnd')) el('dbFilterEnd').value = '';
  _currentFilters = { job_keyword: '', job_type: '', start_date: '', end_date: '' };
  // 同时清除技能模块内部查询
  _skillQueryJob = '';
  const skillInput = document.getElementById('skillJobInput');
  if (skillInput) skillInput.value = '';
  updateSkillUIState();
  loadDashboard();
}

function setFilterAndRefresh(key, value) {
  _currentFilters[key] = value;
  // 同步到输入框
  if (key === 'job_keyword') {
    const el = document.getElementById('dbFilterKeyword');
    if (el) el.value = value;
  }
  if (key === 'job_type') {
    const el = document.getElementById('dbFilterType');
    if (el) el.value = value;
  }
  loadDashboard();
}

function renderFilterTags() {
  const container = document.getElementById('dbFilterTags');
  if (!container) return;
  const tags = [];
  if (_currentFilters.job_keyword) tags.push({ key: 'job_keyword', label: '关键词: ' + _currentFilters.job_keyword });
  if (_currentFilters.job_type) tags.push({ key: 'job_type', label: '类型: ' + _currentFilters.job_type });
  if (_currentFilters.start_date) tags.push({ key: 'start_date', label: '起始: ' + _currentFilters.start_date });
  if (_currentFilters.end_date) tags.push({ key: 'end_date', label: '截止: ' + _currentFilters.end_date });

  if (!tags.length) { container.innerHTML = ''; return; }

  container.innerHTML = tags.map(t =>
    '<span class="db-filter-tag">' + esc(t.label) +
    '<span class="db-filter-tag-x" data-key="' + t.key + '">&times;</span></span>'
  ).join('');

  container.querySelectorAll('.db-filter-tag-x').forEach(x => {
    x.addEventListener('click', () => {
      const k = x.dataset.key;
      _currentFilters[k] = '';
      if (k === 'job_keyword') { const e = document.getElementById('dbFilterKeyword'); if (e) e.value = ''; }
      if (k === 'job_type') { const e = document.getElementById('dbFilterType'); if (e) e.value = ''; }
      if (k === 'start_date') { const e = document.getElementById('dbFilterStart'); if (e) e.value = ''; }
      if (k === 'end_date') { const e = document.getElementById('dbFilterEnd'); if (e) e.value = ''; }
      loadDashboard();
    });
  });
}

// ── Meta 信息 ──
function renderMeta(meta) {
  const el = document.getElementById('dbMeta');
  if (!el || !meta) { if (el) el.innerHTML = ''; return; }
  let html = '';
  if (meta.source) html += '<span>数据来源: ' + esc(meta.source) + '</span>';
  if (meta.updated_at) html += '<span>更新: ' + esc(meta.updated_at) + '</span>';
  el.innerHTML = html;
}

// ── 数据洞察 ──
function renderInsights(insights) {
  const el = document.getElementById('dbInsights');
  if (!el) return;
  if (!insights || !insights.length) {
    el.innerHTML = '<div class="db-insight-card"><span class="db-insight-icon">💡</span><span class="db-insight-text">暂无足够数据生成洞察</span></div>';
    return;
  }
  el.innerHTML = insights.map(i =>
    '<div class="db-insight-card"><span class="db-insight-icon">' + esc(i.icon || '💡') +
    '</span><span class="db-insight-text">' + esc(i.text) + '</span></div>'
  ).join('');
}

// ── 主入口 ──
async function loadDashboard() {
  if (_dashboardLoading) return;
  _dashboardLoading = true;

  const applyBtn = document.getElementById('dbFilterApply');
  if (applyBtn) applyBtn.disabled = true;

  disposeDashboardCharts();

  // loading 状态
  ['chartTopJobs','chartJobType','chartSkillTreemap','chartFitLevel','chartFitScore'].forEach(id => {
    const d = document.getElementById(id);
    if (d) d.innerHTML = '<div class="chart-empty">加载中…</div>';
  });
  ['kpiJd','kpiProfile','kpiSkill','kpiReport'].forEach(id => {
    const d = document.getElementById(id);
    if (d) d.textContent = '…';
  });

  // 构建 query string
  const params = new URLSearchParams();
  if (_currentFilters.job_keyword) params.set('job_keyword', _currentFilters.job_keyword);
  if (_currentFilters.job_type) params.set('job_type', _currentFilters.job_type);
  if (_currentFilters.start_date) params.set('start_date', _currentFilters.start_date);
  if (_currentFilters.end_date) params.set('end_date', _currentFilters.end_date);
  const qs = params.toString();
  const url = '/dashboard/overview' + (qs ? '?' + qs : '');

  try {
    const resp = await fetch(url);

    if (!resp.ok) {
      let errMsg = '请求失败 (HTTP ' + resp.status + ')';
      if (resp.status === 400) {
        try { const b = await resp.json(); errMsg = b.detail || '参数错误'; } catch(_) { errMsg = '参数错误'; }
      } else if (resp.status === 404) {
        errMsg = '看板接口不存在，请确认服务版本';
      } else if (resp.status === 500) {
        errMsg = '服务器内部错误，请查看服务端日志';
      }
      showDashboardError(errMsg);
      return;
    }

    let data;
    try { data = await resp.json(); } catch(_) {
      showDashboardError('响应不是有效的 JSON');
      return;
    }

    if (data.code !== 200) {
      showDashboardError(data.detail || data.message || '接口返回异常 (code=' + (data.code || 'null') + ')');
      return;
    }

    // 渲染非图表部分
    renderMeta(data.meta);
    renderKPIs(data.kpis);
    renderInsights(data.insights);
    renderFilterTags();

    // 缓存全景技能数据
    _skillOverviewData = data.skill_heatmap || [];

    // ECharts 图表
    requestAnimationFrame(() => {
      setTimeout(() => {
        if (typeof echarts === 'undefined') {
          showDashboardError('ECharts 加载失败，请检查本地资源 /static/vendor/echarts.min.js');
          return;
        }
        renderTopJobsChart(data.top_jobs);
        renderJobTypeChart(data.job_type_distribution);
        // 技能图表：如果有模块内查询，加载单岗位数据；否则渲染全景
        if (_skillQueryJob) {
          loadSkillData(_skillQueryJob);
        } else {
          renderSkillChart(data.skill_heatmap, '');
        }
        renderFitLevelChart(data.fit_level_distribution);
        renderFitScoreChart(data.fit_score_distribution);
      }, 80);
    });
  } catch (e) {
    console.error('loadDashboard error', e);
    showDashboardError('数据加载失败：' + (e.message || '网络异常'));
  } finally {
    _dashboardLoading = false;
    if (applyBtn) applyBtn.disabled = false;
  }
}

function showDashboardError(msg) {
  ['chartTopJobs','chartJobType','chartSkillTreemap','chartFitLevel','chartFitScore'].forEach(id => {
    const d = document.getElementById(id);
    if (d) d.innerHTML = '<div class="chart-empty" style="color:#f87171;">' + esc(msg) + '</div>';
  });
}

// ── KPI 卡片 ──
function renderKPIs(kpis) {
  if (!kpis) {
    ['kpiJd','kpiProfile','kpiSkill','kpiReport'].forEach(id => {
      const d = document.getElementById(id);
      if (d) d.textContent = '—';
    });
    return;
  }
  const $ = (id) => document.getElementById(id);
  const animate = (elem, target) => {
    if (!elem) return;
    const start = parseInt(elem.textContent) || 0;
    const duration = 600;
    const t0 = performance.now();
    const step = (now) => {
      const progress = Math.min((now - t0) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      elem.textContent = Math.round(start + (target - start) * eased);
      if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };
  animate($('kpiJd'), kpis.jd_count || 0);
  animate($('kpiProfile'), kpis.profile_count || 0);
  animate($('kpiSkill'), kpis.skill_count || 0);
  animate($('kpiReport'), kpis.report_count || 0);
}

// ── 安全初始化 ECharts ──
function safeInit(dom) {
  if (!dom) return null;
  if (dom.offsetHeight < 10) dom.style.height = '300px';
  try { return echarts.init(dom); } catch (e) {
    console.error('echarts init failed', e);
    dom.innerHTML = '<div class="chart-empty" style="color:#f87171;">图表初始化失败</div>';
    return null;
  }
}

// ── Top 10 岗位柱状图 ──
function renderTopJobsChart(topJobs) {
  const dom = document.getElementById('chartTopJobs');
  if (!dom || !topJobs || !topJobs.length) { if (dom) dom.innerHTML = '<div class="chart-empty">暂无数据</div>'; return; }

  const chart = safeInit(dom);
  if (!chart) return;
  dashboardCharts.topJobs = chart;

  const names = topJobs.map(j => j.job_name).reverse();
  const values = topJobs.map(j => j.jd_count).reverse();
  const maxValue = Math.max(...values, 1);

  chart.setOption({
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#111b27',
      borderColor: 'rgba(67,215,197,.28)',
      textStyle: { color: '#dce6f2', fontSize: 12 },
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(67,215,197,.035)' } },
      formatter: function(p) {
        const d = p[0];
        return '<span style="color:#7f8da1">岗位</span>　' + esc(d.name)
          + '<br/><span style="color:#7f8da1">采集量</span>　<strong style="color:#43d7c5">' + d.value + ' 条 JD</strong>';
      },
    },
    grid: { left: 118, right: 42, top: 10, bottom: 28 },
    xAxis: {
      type: 'value',
      max: Math.ceil(maxValue * 1.12),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: '#607086', fontSize: 10 },
      splitLine: { lineStyle: { color: 'rgba(148,163,184,.075)', type: 'dashed' } },
    },
    yAxis: {
      type: 'category',
      data: names,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: '#aeb9c8', fontSize: 11, width: 96, overflow: 'truncate', margin: 14 },
    },
    series: [{
      type: 'bar',
      data: values.map((value, index) => ({
        value,
        itemStyle: { opacity: .64 + ((index + 1) / values.length) * .36 },
      })),
      barWidth: 10,
      showBackground: true,
      backgroundStyle: { color: 'rgba(148,163,184,.055)', borderRadius: 8 },
      label: {
        show: true, position: 'right', distance: 8,
        color: '#d8e2ed', fontSize: 10, fontWeight: 600,
      },
      itemStyle: {
        borderRadius: 8,
        color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
          { offset: 0, color: '#249e98' },
          { offset: 1, color: '#52dcc9' },
        ]),
      },
      emphasis: { itemStyle: { color: '#74ead8', shadowBlur: 14, shadowColor: 'rgba(67,215,197,.3)' } },
      animationDuration: 700,
      animationDelay: idx => idx * 32,
      animationEasing: 'cubicOut',
    }],
  });

  // 点击联动：设置岗位关键词筛选
  chart.on('click', (params) => {
    setFilterAndRefresh('job_keyword', params.name);
  });
}

// ── 岗位类型环形图 ──
function renderJobTypeChart(typeData) {
  const dom = document.getElementById('chartJobType');
  if (!dom || !typeData || !typeData.length) { if (dom) dom.innerHTML = '<div class="chart-empty">暂无数据</div>'; return; }

  const chart = safeInit(dom);
  if (!chart) return;
  dashboardCharts.jobType = chart;

  const total = typeData.reduce((s, d) => s + d.count, 0);
  const sorted = [...typeData].sort((a, b) => b.count - a.count);
  const typeColors = ['#43d7c5', '#4e8ee8', '#69c7b8', '#efb64b', '#7890a8', '#465568'];

  chart.setOption({
    tooltip: {
      trigger: 'item',
      backgroundColor: '#111b27',
      borderColor: 'rgba(67,215,197,.28)',
      textStyle: { color: '#dce6f2', fontSize: 12 },
      formatter: function(p) {
        const pct = total > 0 ? (p.value / total * 100).toFixed(1) : '0';
        return esc(p.name) + ': <strong>' + p.value + '</strong> (' + pct + '%)';
      },
    },
    title: {
      text: String(total),
      subtext: '岗位画像',
      left: '35%',
      top: '41%',
      textAlign: 'center',
      textStyle: { color: '#edf4fb', fontSize: 24, fontWeight: 700, fontFamily: 'JetBrains Mono' },
      subtextStyle: { color: '#6f7d90', fontSize: 10, lineHeight: 18 },
    },
    legend: {
      orient: 'vertical', right: 4, top: 'middle',
      itemWidth: 8, itemHeight: 8, itemGap: 14,
      icon: 'circle',
      textStyle: { color: '#9daabc', fontSize: 10 },
      formatter: function(name) {
        const item = sorted.find(d => d.type === name);
        const pct = item && total ? (item.count / total * 100).toFixed(1) : '0.0';
        return name + '  ' + (item ? item.count : 0) + '  ' + pct + '%';
      },
    },
    color: typeColors,
    series: [{
      type: 'pie',
      radius: ['53%', '73%'],
      center: ['35%', '53%'],
      startAngle: 90,
      avoidLabelOverlap: true,
      label: { show: false },
      itemStyle: { borderRadius: 3, borderColor: '#101925', borderWidth: 2 },
      emphasis: { scaleSize: 5, itemStyle: { shadowBlur: 18, shadowColor: 'rgba(67,215,197,.18)' } },
      data: sorted.map(d => ({ name: d.type, value: d.count })),
      animationType: 'scale',
      animationDuration: 700,
    }],
  });

  // 点击联动：设置岗位类型筛选
  chart.on('click', (params) => {
    setFilterAndRefresh('job_type', params.name);
  });
}

// ── 技能热度 Treemap（保留兼容，实际渲染由 renderSkillChart 处理）──
function renderSkillTreemap(skillData) {
  renderSkillChart(skillData, '');
}

function interpolateColor(t) {
  const colors = [
    [67, 215, 197],
    [84, 189, 232],
    [142, 200, 124],
    [239, 182, 75],
  ];
  const idx = t * (colors.length - 1);
  const i = Math.floor(idx);
  const f = idx - i;
  const c0 = colors[Math.min(i, colors.length - 1)];
  const c1 = colors[Math.min(i + 1, colors.length - 1)];
  const r = Math.round(c0[0] + (c1[0] - c0[0]) * f);
  const g = Math.round(c0[1] + (c1[1] - c0[1]) * f);
  const b = Math.round(c0[2] + (c1[2] - c0[2]) * f);
  return 'rgb(' + r + ',' + g + ',' + b + ')';
}

// ── 适配等级环形图 ──
function renderFitLevelChart(levelData) {
  const dom = document.getElementById('chartFitLevel');
  if (!dom || !levelData || !levelData.length) { if (dom) dom.innerHTML = '<div class="chart-empty">暂无数据</div>'; return; }

  const chart = safeInit(dom);
  if (!chart) return;
  dashboardCharts.fitLevel = chart;

  const colorMap = { strong: '#43d7c5', moderate: '#4e8ee8', weak: '#efb64b' };
  const labelMap = { strong: '强匹配', moderate: '中等匹配', weak: '弱匹配' };
  const total = levelData.reduce((s, d) => s + d.count, 0);
  _fitLevelData = levelData;

  chart.setOption({
    tooltip: {
      trigger: 'item',
      backgroundColor: '#111b27',
      borderColor: 'rgba(67,215,197,.28)',
      textStyle: { color: '#dce6f2', fontSize: 12 },
      formatter: function(p) {
        const pct = total > 0 ? (p.value / total * 100).toFixed(1) : '0';
        return (labelMap[p.name] || p.name) + ': <strong>' + p.value + '</strong> (' + pct + '%)';
      },
    },
    title: {
      text: String(total),
      subtext: '分析报告',
      left: 'center',
      top: '34%',
      textStyle: { color: '#edf4fb', fontSize: 24, fontWeight: 700, fontFamily: 'JetBrains Mono' },
      subtextStyle: { color: '#6f7d90', fontSize: 10, lineHeight: 18 },
    },
    legend: {
      bottom: 4, left: 'center',
      icon: 'circle', itemWidth: 8, itemHeight: 8, itemGap: 16,
      textStyle: { color: '#8f9caf', fontSize: 9 },
      formatter: (name) => labelMap[name] || name,
    },
    series: [{
      type: 'pie',
      radius: ['53%', '72%'],
      center: ['50%', '43%'],
      avoidLabelOverlap: false,
      label: { show: false },
      emphasis: { scaleSize: 5 },
      itemStyle: { borderRadius: 3, borderColor: '#101925', borderWidth: 2 },
      data: levelData.map(d => ({
        name: d.level,
        value: d.count,
        itemStyle: { color: colorMap[d.level] || DB_THEME.blue },
      })),
      animationType: 'scale',
      animationDuration: 800,
    }],
  });

  chart.on('click', (params) => {
    if (params && params.name) enterFitFocus(params.name);
  });
}

const FIT_LEVEL_UI = {
  strong: {
    label: '强匹配',
    color: '#43d7c5',
    subtitle: '查看高适配报告中的优势证据、能力结构与面试表达策略。',
    listTitle: '强匹配报告：优势与证据',
  },
  moderate: {
    label: '中等匹配',
    color: '#4e8ee8',
    subtitle: '定位最值得补齐的能力差距，判断哪些投入最能提升岗位适配度。',
    listTitle: '中等匹配报告：提升空间',
  },
  weak: {
    label: '弱匹配',
    color: '#efb64b',
    subtitle: '聚焦关键短板、证据缺口和可执行补齐路径，避免无效投递。',
    listTitle: '弱匹配报告：关键差距与行动建议',
  },
};

async function enterFitFocus(level) {
  const config = FIT_LEVEL_UI[level];
  const view = document.getElementById('viewDashboard');
  const stage = document.getElementById('fitFocusStage');
  if (!config || !view || !stage || _fitFocusLoading) return;

  _fitFocusLevel = level;
  _fitFocusLoading = true;
  view.classList.add('fit-focus-active');
  view.dataset.fitLevel = level;

  const title = document.getElementById('fitFocusTitle');
  const subtitle = document.getElementById('fitFocusSubtitle');
  const reportTitle = document.getElementById('fitFocusReportsTitle');
  const reportList = document.getElementById('fitFocusReportList');
  const metrics = document.getElementById('fitFocusMetrics');
  const dimensions = document.getElementById('fitFocusDimensions');
  if (title) title.textContent = config.label + '深度分析';
  if (subtitle) subtitle.textContent = config.subtitle;
  if (reportTitle) reportTitle.textContent = config.listTitle;
  if (reportList) reportList.innerHTML = '<div class="fit-focus-loading">正在读取真实适配报告…</div>';
  if (metrics) metrics.innerHTML = '';
  if (dimensions) dimensions.innerHTML = '';

  requestAnimationFrame(() => {
    renderFitFocusChart(level);
    stage.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  try {
    const resp = await fetch('/dashboard/fit_level_detail?level=' + encodeURIComponent(level));
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    renderFitFocusDetail(data, config);
  } catch (error) {
    if (reportList) {
      reportList.innerHTML = '<div class="fit-focus-empty">适配报告加载失败，请稍后重试。</div>';
    }
  } finally {
    _fitFocusLoading = false;
  }
}

function exitFitFocus() {
  const view = document.getElementById('viewDashboard');
  if (!view) return;
  view.classList.add('fit-focus-leaving');
  setTimeout(() => {
    view.classList.remove('fit-focus-active', 'fit-focus-leaving');
    delete view.dataset.fitLevel;
    _fitFocusLevel = '';
    if (dashboardCharts.fitLevelFocus) {
      try { dashboardCharts.fitLevelFocus.dispose(); } catch(_){}
      delete dashboardCharts.fitLevelFocus;
    }
    requestAnimationFrame(() => {
      Object.values(dashboardCharts).forEach(chart => {
        try { if (chart && !chart.isDisposed()) chart.resize(); } catch(_){}
      });
    });
    const card = document.getElementById('fitLevelCard');
    if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, 220);
}

function renderFitFocusChart(selectedLevel) {
  const dom = document.getElementById('chartFitLevelFocus');
  if (!dom || !_fitLevelData.length || typeof echarts === 'undefined') return;
  if (dashboardCharts.fitLevelFocus) {
    try { dashboardCharts.fitLevelFocus.dispose(); } catch(_){}
  }
  const chart = echarts.init(dom);
  dashboardCharts.fitLevelFocus = chart;
  const total = _fitLevelData.reduce((sum, item) => sum + item.count, 0);
  const selected = _fitLevelData.find(item => item.level === selectedLevel);
  const config = FIT_LEVEL_UI[selectedLevel];
  const colors = { strong: '#43d7c5', moderate: '#4e8ee8', weak: '#efb64b' };

  chart.setOption({
    title: {
      text: String(selected ? selected.count : 0),
      subtext: config.label + '报告',
      left: 'center', top: '35%',
      textStyle: { color: '#edf4fb', fontSize: 34, fontWeight: 700, fontFamily: 'JetBrains Mono' },
      subtextStyle: { color: config.color, fontSize: 11, lineHeight: 22 },
    },
    tooltip: {
      trigger: 'item',
      backgroundColor: '#111b27',
      borderColor: 'rgba(67,215,197,.28)',
      textStyle: { color: '#dce6f2', fontSize: 12 },
      formatter: p => (FIT_LEVEL_UI[p.name]?.label || p.name) + '：' + p.value + ' 份',
    },
    series: [{
      type: 'pie',
      radius: ['57%', '78%'],
      center: ['50%', '49%'],
      startAngle: 90,
      label: { show: false },
      itemStyle: { borderRadius: 5, borderColor: '#101925', borderWidth: 3 },
      data: _fitLevelData.map(item => ({
        name: item.level,
        value: item.count,
        selected: item.level === selectedLevel,
        itemStyle: {
          color: colors[item.level] || '#718096',
          opacity: item.level === selectedLevel ? 1 : .22,
        },
      })),
      selectedMode: 'single',
      selectedOffset: 8,
      animationDuration: 650,
    }],
  });
  chart.on('click', params => {
    if (params.name && params.name !== _fitFocusLevel) enterFitFocus(params.name);
  });
}

function renderFitFocusDetail(data, config) {
  const metrics = document.getElementById('fitFocusMetrics');
  const dimensions = document.getElementById('fitFocusDimensions');
  const reportList = document.getElementById('fitFocusReportList');
  const reportCount = document.getElementById('fitFocusReportCount');
  if (reportCount) reportCount.textContent = '共 ' + (data.total || 0) + ' 份报告';

  if (metrics) {
    metrics.innerHTML = [
      ['报告数量', data.total || 0],
      ['平均适配分', data.average_score || 0],
      ['报告完整度', (data.completeness_rate || 0) + '%'],
    ].map(item => '<div class="fit-focus-metric"><span>' + esc(String(item[0]))
      + '</span><strong>' + esc(String(item[1])) + '</strong></div>').join('');
  }

  if (dimensions) {
    dimensions.innerHTML = '<div class="fit-focus-dimension-title">五维平均表现</div>'
      + (data.dimensions || []).map(item =>
        '<div class="fit-focus-dimension">'
        + '<div><span>' + esc(item.label) + '</span><strong>' + item.score + '</strong></div>'
        + '<div class="fit-focus-dimension-track"><i style="width:' + Math.max(0, Math.min(100, item.score)) + '%"></i></div>'
        + '</div>'
      ).join('');
  }

  if (!reportList) return;
  const reports = data.reports || [];
  if (!reports.length) {
    reportList.innerHTML = '<div class="fit-focus-empty">当前等级暂无可展示的报告。</div>';
    return;
  }
  reportList.innerHTML = reports.map((report, index) => renderFitReportCard(report, config, index === 0)).join('');
}

function renderFitReportCard(report, config, featured) {
  const primaryItems = _fitFocusLevel === 'strong'
    ? report.strengths
    : (_fitFocusLevel === 'weak' ? report.gaps : [...(report.gaps || []), ...(report.strengths || [])].slice(0, 3));
  const actionItems = _fitFocusLevel === 'strong'
    ? report.interview_strategy
    : report.learning_plan;
  const primaryLabel = _fitFocusLevel === 'strong' ? '优势证据' : (_fitFocusLevel === 'weak' ? '关键差距' : '关键判断');
  const actionLabel = _fitFocusLevel === 'strong' ? '面试表达' : '行动建议';
  const list = items => (items && items.length)
    ? '<ul>' + items.map(item => '<li>' + esc(typeof item === 'string' ? item : JSON.stringify(item)) + '</li>').join('') + '</ul>'
    : '<p class="fit-report-muted">当前报告未记录该项信息。</p>';

  return '<article class="fit-focus-report-card' + (featured ? ' is-featured' : '') + '">'
    + '<div class="fit-report-card-top">'
    + '<div><span class="fit-report-job">' + esc(report.job_name || '未命名岗位') + '</span>'
    + '<span class="fit-report-date">' + esc(report.created_at || '') + '</span></div>'
    + '<strong class="fit-report-score" style="color:' + config.color + '">' + report.score + '</strong>'
    + '</div>'
    + (report.summary ? '<p class="fit-report-summary">' + esc(report.summary) + '</p>' : '')
    + (!report.has_detail
      ? '<div class="fit-report-quality-warning">该历史报告缺少五维分析、差距和证据字段，建议重新生成画像后再分析。</div>'
      : '')
    + '<div class="fit-report-columns">'
    + '<div><span>' + primaryLabel + '</span>' + list(primaryItems) + '</div>'
    + '<div><span>' + actionLabel + '</span>' + list(actionItems) + '</div>'
    + '</div>'
    + '</article>';
}

// ── 适配分分布柱状图 ──
function renderFitScoreChart(scoreData) {
  const dom = document.getElementById('chartFitScore');
  if (!dom || !scoreData || !scoreData.length) { if (dom) dom.innerHTML = '<div class="chart-empty">暂无数据</div>'; return; }

  const chart = safeInit(dom);
  if (!chart) return;
  dashboardCharts.fitScore = chart;

  chart.setOption({
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#111b27',
      borderColor: 'rgba(67,215,197,.28)',
      textStyle: { color: '#dce6f2', fontSize: 12 },
      axisPointer: { type: 'line', lineStyle: { color: 'rgba(67,215,197,.35)' } },
      formatter: function(p) {
        const d = p[0];
        return '分数段 ' + esc(d.name) + ': <strong>' + d.value + '</strong> 人';
      },
    },
    grid: { left: 35, right: 18, top: 28, bottom: 34 },
    xAxis: {
      type: 'category',
      data: scoreData.map(d => d.range),
      boundaryGap: false,
      axisLine: { lineStyle: { color: 'rgba(148,163,184,.15)' } },
      axisTick: { show: false },
      axisLabel: { color: '#6e7d91', fontSize: 9 },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: '#6e7d91', fontSize: 9 },
      splitLine: { lineStyle: { color: 'rgba(148,163,184,.07)', type: 'dashed' } },
    },
    series: [{
      type: 'line',
      data: scoreData.map(d => d.count),
      smooth: .42,
      symbol: 'circle',
      symbolSize: 7,
      showSymbol: true,
      lineStyle: { color: '#43d7c5', width: 2 },
      itemStyle: { color: '#0f1924', borderColor: '#65e3d1', borderWidth: 2 },
      label: { show: true, position: 'top', color: '#b8c4d2', fontSize: 9 },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(67,215,197,.32)' },
          { offset: 1, color: 'rgba(67,215,197,.015)' },
        ]),
      },
      animationDuration: 700,
      animationEasing: 'cubicOut',
    }],
  });
}

// ── 响应式 resize ──
window.addEventListener('resize', () => {
  Object.values(dashboardCharts).forEach(c => {
    try { if (c && typeof c.resize === 'function' && !c.isDisposed()) c.resize(); } catch(_){}
  });
});

// ── 初始化（app.js 中 switchView 会调用 loadDashboard）──
document.addEventListener('DOMContentLoaded', initDashboardFilters);
