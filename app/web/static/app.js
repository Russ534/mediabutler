/* MediaButler 前端逻辑 */
let TOKEN = localStorage.getItem('mb_token') || '';
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

async function api(path, opts = {}) {
  opts.headers = Object.assign({ 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + TOKEN }, opts.headers);
  const r = await fetch('/api' + path, opts);
  if (r.status === 401) { logout(); throw new Error('未登录'); }
  return r.json();
}
const fmtTime = t => t ? new Date(t * 1000).toLocaleString('zh-CN', { hour12: false }) : '—';

/* ---------- 登录 ---------- */
async function doLogin() {
  const r = await fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: $('#login-user').value, password: $('#login-pass').value })
  });
  if (r.ok) {
    const d = await r.json();
    TOKEN = d.token; localStorage.setItem('mb_token', TOKEN);
    showApp();
  } else $('#login-err').textContent = '账号或密码错误';
}
function logout() { localStorage.removeItem('mb_token'); TOKEN = ''; location.reload(); }
function showApp() { $('#login-view').style.display = 'none'; $('#app-view').style.display = 'flex'; loadDashboard(); }

/* ---------- 导航 ---------- */
$$('.sidebar nav a').forEach(a => a.onclick = () => {
  $$('.sidebar nav a').forEach(x => x.classList.remove('active'));
  a.classList.add('active');
  $$('.page').forEach(p => p.classList.remove('active'));
  $('#page-' + a.dataset.page).classList.add('active');
  ({ dashboard: loadDashboard, subs: loadSubs, tg: loadTg, sync: loadSync, organize: loadOrg,
     tasks: loadTasks, resources: loadResources, settings: loadSettings, logs: loadLogs })[a.dataset.page]();
});

/* ---------- 仪表盘 ---------- */
async function loadDashboard() {
  const d = await api('/dashboard');
  const s = d.subscriptions;
  $('#stat-cards').innerHTML = [
    ['订阅总数', Object.values(s).reduce((a, b) => a + b, 0)],
    ['缺失', s.missing || 0], ['洗版中', s.washing || 0], ['已入库', s.in_library || 0],
    ['TG 缓存消息', d.tg_cache], ['STRM 文件', d.strm_files],
    ['资源已推送', d.resources.pushed || 0],
  ].map(([l, n]) => `<div class="stat"><div class="num">${n}</div><div class="label">${l}</div></div>`).join('');
  const jobs = Object.fromEntries(d.jobs.map(j => ['task_' + j.id, j.next]));
  $('#dash-tasks tbody').innerHTML = d.tasks.map(t => `<tr>
    <td>${t.name}</td><td><code>${t.cron || '手动'}</code></td><td>${fmtTime(t.last_run)}</td>
    <td title="${t.last_result || ''}">${(t.last_result || '').slice(0, 40)}</td>
    <td>${jobs['task_' + t.id] ? new Date(jobs['task_' + t.id]).toLocaleString('zh-CN', { hour12: false }) : '—'}</td>
    <td><button class="btn-sm" onclick="runTask(${t.id})">运行</button></td></tr>`).join('');
  $('#dash-events').innerHTML = d.events.map(e =>
    `<div class="ev"><span class="time">${fmtTime(e.created_at)}</span><b>${e.type}</b><span>${e.payload.slice(0, 120)}</span></div>`).join('') || '<p class="tip">暂无事件</p>';
}
async function runTask(id) { await api(`/tasks/${id}/run`, { method: 'POST' }); alert('已触发'); }

/* ---------- 订阅 ---------- */
const STATUS_CN = { missing: '缺失', partial: '部分', in_library: '已入库', washing: '洗版中', archived: '已归档' };
async function loadSubs() {
  const st = $('#sub-filter').value;
  const subs = await api('/subscriptions' + (st ? '?status=' + st : ''));
  $('#sub-list').innerHTML = subs.map(s => `<div class="sub-card">
    <div class="ops"><button onclick="refreshSub(${s.id})" title="刷新缺集">↻</button><button onclick="delSub(${s.id})" title="删除">✕</button></div>
    ${s.poster ? `<img src="${s.poster}" loading="lazy">` : '<img src="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22/>">'}
    <div class="info"><div class="t">${s.title}</div>
    <div class="meta">${s.media_type === 'tv' ? '剧集' : '电影'} ${s.year || ''} ${s.season ? 'S' + String(s.season).padStart(2, '0') : ''}</div>
    <span class="tag ${s.status}">${STATUS_CN[s.status] || s.status}</span>
    ${s.media_type === 'tv' && s.status !== 'in_library' ? `<div class="meta">缺 ${JSON.parse(s.missing || '[]').length} 集</div>` : ''}
    </div></div>`).join('') || '<p class="tip">暂无订阅，点右上角添加</p>';
}
async function refreshSub(id) { await api(`/subscriptions/${id}/refresh`, { method: 'POST' }); loadSubs(); }
async function delSub(id) { if (confirm('删除该订阅？')) { await api(`/subscriptions/${id}`, { method: 'DELETE' }); loadSubs(); } }

function showModal(title, bodyHtml, onOk) {
  $('#modal-title').textContent = title; $('#modal-body').innerHTML = bodyHtml;
  $('#modal').style.display = 'flex';
  $('#modal-ok').onclick = async () => { await onOk(); closeModal(); };
}
function closeModal() { $('#modal').style.display = 'none'; }

function showAddSub() {
  showModal('添加订阅', `
    <input id="as-q" placeholder="搜索 TMDB…" onkeydown="if(event.key==='Enter')tmdbSearch()">
    <button onclick="tmdbSearch()" style="width:100%;margin-bottom:8px">搜索</button>
    <div id="as-results" class="search-results"></div>
    <select id="as-season"><option value="">全部季</option></select>
    <input id="as-wash" type="number" placeholder="洗版天数（0=不洗版）" value="0">
    <input id="as-kw" placeholder="关键词过滤，逗号分隔，/正则/ 亦可">`, async () => {
    const sel = window._asSel; if (!sel) return alert('请先搜索并选择一个条目');
    await api('/subscriptions', { method: 'POST', body: JSON.stringify({
      media_type: sel.media_type, title: sel.title, tmdb_id: sel.tmdb_id, year: sel.year,
      poster: sel.poster, season: $('#as-season').value ? +$('#as-season').value : null,
      wash_days: +$('#as-wash').value || 0, keywords: $('#as-kw').value }) });
    loadSubs();
  });
}
async function tmdbSearch() {
  const rs = await api('/tmdb/search?q=' + encodeURIComponent($('#as-q').value));
  $('#as-results').innerHTML = rs.map((r, i) => `<div class="sr" onclick='selectTmdb(${i})'>
    ${r.poster ? `<img src="${r.poster}">` : ''}<div><b>${r.title}</b> <span class="tip">${r.media_type === 'tv' ? '剧集' : '电影'} ${r.year}</span></div></div>`).join('');
  window._asList = rs;
}
function selectTmdb(i) {
  window._asSel = window._asList[i];
  $$('#as-results .sr').forEach((el, j) => el.style.background = j === i ? '#e8edfd' : '');
  const sel = $('#as-season'); sel.innerHTML = '<option value="">全部季</option>';
  if (window._asSel.media_type === 'tv') for (let s = 1; s <= 30; s++) sel.innerHTML += `<option value="${s}">第 ${s} 季</option>`;
}

/* ---------- TG ---------- */
let tgHash = '';
async function loadTg() {
  const st = await api('/tg/status');
  $('#tg-status').innerHTML = st.authorized ? '✅ 已登录并监听中' :
    st.configured ? '⚠️ 已配置 api_id/hash，但未登录' : '❌ 请先在「设置」中填写 tg.api_id / tg.api_hash';
  $('#tg-login-form').style.display = st.authorized ? 'none' : 'block';
  const rules = await api('/tg/rules');
  $('#tg-rules tbody').innerHTML = rules.map(r => `<tr>
    <td>${r.name}</td><td>${r.channel_ids}</td><td>${r.push_target}</td>
    <td>${r.only_missing ? '是' : '否'}</td><td>${r.keywords || '—'}</td>
    <td><button class="btn-sm" onclick="delTgRule(${r.id})">删除</button>
        <button class="btn-sm" onclick="cacheHistory(${r.id}, '${r.channel_ids}')">缓存历史</button></td></tr>`).join('') || '<tr><td colspan=6 class="tip">暂无规则</td></tr>';
}
async function tgSendCode() {
  const r = await api('/tg/login/start', { method: 'POST', body: JSON.stringify({ phone: $('#tg-phone').value }) });
  tgHash = r.phone_code_hash; $('#tg-code-row').style.display = 'block';
}
async function tgFinishLogin() {
  const r = await api('/tg/login/finish', { method: 'POST', body: JSON.stringify({
    phone: $('#tg-phone').value, code: $('#tg-code').value, phone_code_hash: tgHash, password: $('#tg-password').value }) });
  if (r.ok) { alert('登录成功'); loadTg(); } else alert('登录失败');
}
async function showAddTgRule() {
  const ds = await api('/tg/dialogs').catch(() => []);
  const opts = ds.map(d => `<label style="display:block"><input type="checkbox" class="tgr-ch" value="${d.id}"> ${d.name} <span class="tip">(${d.type} ${d.id})</span></label>`).join('');
  showModal('新增监控规则', `
    <input id="tgr-name" placeholder="规则名称">
    <div style="max-height:200px;overflow:auto;border:1px solid #eef0f3;border-radius:8px;padding:8px;margin-bottom:10px">${opts || '未登录 TG，请先登录获取对话列表'}</div>
    <select id="tgr-target"><option value="pan115">推送到 115 网盘</option><option value="tg">TG 通知</option><option value="none">仅匹配不推送</option></select>
    <label><input type="checkbox" id="tgr-missing" checked> 仅匹配缺集（推荐）</label><br><br>
    <input id="tgr-kw" placeholder="额外关键词（可选）">`, async () => {
    const ids = [...$$('.tgr-ch:checked')].map(x => +x.value);
    await api('/tg/rules', { method: 'POST', body: JSON.stringify({
      name: $('#tgr-name').value, channel_ids: ids, push_target: $('#tgr-target').value,
      only_missing: $('#tgr-missing').checked, keywords: $('#tgr-kw').value }) });
    loadTg();
  });
}
async function delTgRule(id) { if (confirm('删除规则？')) { await api(`/tg/rules/${id}`, { method: 'DELETE' }); loadTg(); } }
async function cacheHistory(id, channelIds) {
  const ids = JSON.parse(channelIds);
  for (const cid of ids) await api(`/tg/cache/${cid}?limit=500`, { method: 'POST' });
  alert('历史缓存已在后台进行');
}
async function tgCacheSearch() {
  const rs = await api('/tg/search_cache?q=' + encodeURIComponent($('#tg-cache-q').value));
  $('#tg-cache-result').innerHTML = rs.map(r =>
    `<div class="card" style="padding:10px;margin:8px 0"><b>频道 ${r.channel_id}</b> <span class="tip">${fmtTime(r.msg_date)}</span><br>${(r.text || '').slice(0, 200)}</div>`).join('') || '<p class="tip">无结果</p>';
}

/* ---------- 同步 ---------- */
async function loadSync() {
  const ps = await api('/sync/paths');
  $('#sync-paths tbody').innerHTML = ps.map(p => `<tr>
    <td>${p.name}</td><td>${p.pan_cid}</td><td>${p.local_dir}</td><td>${p.media_type}</td>
    <td>${fmtTime(p.last_full_sync)}</td>
    <td><button class="btn-sm" onclick="api('/sync/paths/${p.id}/full',{method:'POST'}).then(()=>alert('已开始'))">全量同步</button>
        <button class="btn-sm" onclick="api('/sync/paths/${p.id}',{method:'DELETE'}).then(loadSync)">删除</button></td></tr>`).join('') || '<tr><td colspan=6 class="tip">暂无同步路径</td></tr>';
}
function showAddSyncPath() {
  showModal('添加同步路径', `
    <input id="sp-name" placeholder="名称，如 剧集库">
    <input id="sp-cid" placeholder="115 目录 CID（下方目录浏览器可查询）">
    <input id="sp-dir" placeholder="本地目录（/media 下相对路径，如 tv）">
    <select id="sp-type"><option value="tv">剧集</option><option value="movie">电影</option><option value="mixed">混合</option></select>
    <label><input type="checkbox" id="sp-meta" checked> 同步 nfo/字幕/图片等元数据</label>`, async () => {
    await api('/sync/paths', { method: 'POST', body: JSON.stringify({
      name: $('#sp-name').value, pan_cid: $('#sp-cid').value, local_dir: $('#sp-dir').value,
      media_type: $('#sp-type').value, with_meta: $('#sp-meta').checked }) });
    loadSync();
  });
}
async function browsePan() {
  const items = await api('/pan115/dir?cid=' + $('#pan-cid').value).catch(e => []);
  $('#pan-list').innerHTML = items.map(i => i.is_dir ?
    `<div class="sr" style="cursor:pointer;padding:6px" onclick="$('#pan-cid').value='${i.cid}';browsePan()">📁 ${i.name} <code class="tip">cid=${i.cid}</code></div>` :
    `<div style="padding:6px" class="tip">🎬 ${i.name}</div>`).join('');
}

/* ---------- 整理 ---------- */
async function loadOrg() {
  const rs = await api('/organize/rules');
  $('#org-rules tbody').innerHTML = rs.map(r => `<tr>
    <td>${r.name}</td><td>${r.watch_cid}</td><td>${r.target_cid}</td><td>${r.media_type}</td><td>${r.match_keywords || '—'}</td>
    <td><button class="btn-sm" onclick="api('/organize/rules/${r.id}/run',{method:'POST'}).then(()=>alert('已开始'))">立即整理</button>
        <button class="btn-sm" onclick="api('/organize/rules/${r.id}',{method:'DELETE'}).then(loadOrg)">删除</button></td></tr>`).join('') || '<tr><td colspan=6 class="tip">暂无规则</td></tr>';
}
function showAddRule() {
  showModal('添加整理规则', `
    <input id="or-name" placeholder="规则名称">
    <input id="or-watch" placeholder="监听目录 CID（如 待整理 目录）">
    <input id="or-target" placeholder="目标目录 CID（整理后的根目录）">
    <select id="or-type"><option value="auto">自动识别</option><option value="movie">按电影</option><option value="tv">按剧集</option></select>
    <input id="or-kw" placeholder="关键词过滤（可选）">`, async () => {
    await api('/organize/rules', { method: 'POST', body: JSON.stringify({
      name: $('#or-name').value, watch_cid: $('#or-watch').value, target_cid: $('#or-target').value,
      media_type: $('#or-type').value, match_keywords: $('#or-kw').value }) });
    loadOrg();
  });
}

/* ---------- 任务 ---------- */
async function loadTasks() {
  const d = await api('/dashboard');
  $('#task-list tbody').innerHTML = d.tasks.map(t => `<tr>
    <td>${t.name}</td><td>${t.job_type}</td><td><code>${t.cron || '手动'}</code></td>
    <td>${t.enabled ? '✅' : '❌'}</td><td title="${t.last_result || ''}">${(t.last_result || '').slice(0, 50)}</td>
    <td><button class="btn-sm" onclick="api('/tasks/${t.id}/toggle',{method:'POST'}).then(loadTasks)">启停</button>
        <button class="btn-sm" onclick="runTask(${t.id})">运行</button>
        <button class="btn-sm" onclick="api('/tasks/${t.id}',{method:'DELETE'}).then(loadTasks)">删除</button></td></tr>`).join('');
}

/* ---------- 资源 ---------- */
async function loadResources() {
  const rs = await api('/resources?limit=200');
  $('#res-list tbody').innerHTML = rs.map(r => `<tr>
    <td>${fmtTime(r.created_at)}</td><td title="${r.title}">${(r.title || '').slice(0, 40)}</td>
    <td>${r.link_type || ''}</td><td>${r.source || ''}</td>
    <td><span class="tag ${r.status === 'pushed' ? 'in_library' : r.status === 'failed' ? 'missing' : 'partial'}">${r.status}</span></td>
    <td>${r.push_target || ''}</td>
    <td>${r.link ? `<a href="${r.link}" target="_blank">链接</a>` : ''}</td></tr>`).join('') || '<tr><td colspan=7 class="tip">暂无记录</td></tr>';
}

/* ---------- 设置 ---------- */
const SETTING_GROUPS = [
  ['Emby', ['emby.host|Emby 地址（如 http://192.168.1.10:8096）', 'emby.api_key|Emby API Key']],
  ['TMDB', ['tmdb.api_key|TMDB API Key', 'tmdb.proxy|TMDB 代理（可选）']],
  ['网络', ['net.proxy|全局兜底代理（可选，http:// 或 socks5://）']],
  ['豆瓣', ['douban.user_id|豆瓣用户 ID', 'douban.cookie|豆瓣 Cookie', 'douban.user_agent|User-Agent']],
  ['115 网盘', ['pan115.cookie|115 Cookie（浏览器 F12 复制）']],
  ['影巢 HDHive', ['hdhive.base_url|影巢 API 地址（现 re0.me）', 'hdhive.api_key|影巢 API Key（re0.me 个人设置生成）', 'hdhive.proxy|影巢代理（CF 拦截时需走代理）', 'hdhive.gambler|赌狗签到 true/false']],
  ['盘搜', ['pansou.base_url|盘搜地址（默认千山）', 'pansou.base_url2|第二盘搜源（可选）']],
  ['AI 解析（wb-hub）', ['ai.base_url|OpenAI 兼容地址（wb-hub）', 'ai.api_key|API Key（hub 面板设置页复制）', 'ai.model|模型']],
  ['Telegram', ['tg.api_id|API ID', 'tg.api_hash|API Hash', 'tg.bot_token|通知 Bot Token', 'tg.notify_chat_id|通知 Chat ID', 'tg.proxy|TG 代理 socks5://（可选）', 'tg.session_string|Session 字符串（可选，免扫码登录）']],
  ['Gemini（AI 兜底）', ['gemini.api_key|Gemini Key（可选）', 'gemini.model|模型']],
  ['通知', ['notify.wecom_webhook|企业微信机器人 Webhook', 'notify.webhook|自定义 Webhook']],
];
async function loadSettings() {
  const s = await api('/settings');
  $('#settings-form').innerHTML = SETTING_GROUPS.map(([g, fields]) =>
    `<h3 style="grid-column:1/-1">${g}</h3>` + fields.map(f => {
      const [k, label] = f.split('|');
      return `<div class="field"><label>${label}</label><input data-key="${k}" value="${s[k] || ''}"></div>`;
    }).join('')).join('');
}
async function saveSettings() {
  const values = {};
  $$('#settings-form input').forEach(i => values[i.dataset.key] = i.value);
  await api('/settings', { method: 'POST', body: JSON.stringify({ values }) });
  alert('已保存');
}
async function changePwd() {
  const r = await fetch('/api/auth/password', { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + TOKEN },
    body: JSON.stringify({ old_password: $('#pwd-old').value, new_password: $('#pwd-new').value }) });
  alert(r.ok ? '修改成功' : '原密码错误');
}
async function testService(name) {
  const box = $('#test-result'); box.style.display = 'block'; box.textContent = '测试中…';
  try { box.textContent = JSON.stringify(await api('/test/' + name, { method: 'POST' }), null, 2); }
  catch (e) { box.textContent = '失败: ' + e.message; }
}
async function testPansou() {
  const box = $('#test-result'); box.style.display = 'block'; box.textContent = '盘搜测试中…';
  try { box.textContent = JSON.stringify(await api('/test/pansou?q=' + encodeURIComponent(prompt('搜索关键词', '流浪地球') || '流浪地球')), null, 2); }
  catch (e) { box.textContent = '失败: ' + e.message; }
}

/* ---------- 日志 ---------- */
async function loadLogs() {
  const lv = $('#log-level').value;
  const logs = await api('/logs?limit=300' + (lv ? '&level=' + lv : ''));
  $('#log-list').innerHTML = logs.map(l =>
    `<div class="${l.level}">[${fmtTime(l.created_at)}] [${l.level}] [${l.module}] ${l.message}</div>`).join('');
}

/* ---------- 启动 ---------- */
(async function init() {
  if (!TOKEN) { $('#login-view').style.display = 'flex'; return; }
  try { await api('/dashboard'); showApp(); }
  catch { $('#login-view').style.display = 'flex'; }
  setInterval(() => { if ($('#page-dashboard').classList.contains('active')) loadDashboard(); }, 30000);
})();
