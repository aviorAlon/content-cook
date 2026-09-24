// 「模型设置」页：五个角色（剧本文本 / 生图 / 语音 / 视频 / 配乐）各选一个适配器，填自己的接口地址、密钥、模型名。
// 密钥保存后只回显打码值；再次保存时打码值不会覆盖原密钥（后端 update_settings 的规则）。
// 依赖 index.html 里的 api() / escapeHtml() / showNoticeModal() / loadHealth() / state。


// 页面自带样式，不依赖 index.html 有没有这些类
(function injectSettingsCss() {
  if (document.getElementById('settings-css')) return;
  const style = document.createElement('style');
  style.id = 'settings-css';
  style.textContent = `
    .card{background:var(--panel,#fff);border:1px solid var(--border,rgba(0,0,0,.08));border-radius:var(--r-md,8px);padding:14px 16px;box-shadow:var(--shadow-sm)}
    .card-head{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:10px}
    .form-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px 16px}
    .form-grid label{display:flex;flex-direction:column;gap:4px;font-size:13px}
    .form-grid input,.form-grid select{padding:7px 9px;border:1px solid var(--border,rgba(0,0,0,.15));border-radius:var(--r-sm,6px);font:inherit;background:var(--panel2);color:var(--text)}
    .form-grid [data-fields]{display:contents}
    .form-grid [data-hint]{grid-column:1/-1}
    .pill{padding:2px 10px;border-radius:999px;font-size:12px;margin-left:auto;font-weight:600}
    .pill-ok{background:var(--good-bg,#e6f4ea);color:var(--good,#1e7b34)}.pill-warn{background:var(--warn-bg,#fdecea);color:var(--warn,#b3261e)}
    .notice{padding:10px 12px;border-radius:var(--r-sm,8px);background:var(--panel2,rgba(0,0,0,.04));font-size:13px;line-height:1.55}
    .notice code{font-family:var(--mono);font-size:11.5px}
    .row{display:flex;align-items:center;flex-wrap:wrap}
    .env-table{font-size:12px;color:var(--dim);margin:8px 0 14px;display:grid;grid-template-columns:auto 1fr;gap:3px 12px}
    .env-table code{font-family:var(--mono);font-size:11.5px;color:var(--text);word-break:break-all}
    @media (max-width:800px){.form-grid{grid-template-columns:1fr}}
  `;
  document.head.appendChild(style);
})();

const ROLE_LABELS = {
  text: { title: '剧本文本模型', desc: '「新建项目（AI 起草）」和逐镜改写用它。' },
  image: { title: '生图模型（首帧 / 定妆）', desc: '每一镜的首帧和角色定妆图。参考图/角色锚图随请求以 base64 发送。' },
  voice: { title: '语音模型（旁白）', desc: '后期层的旁白轨。Edge TTS 免费；画内台词由视频模型原生生成，不走这里。' },
  video: { title: '视频模型', desc: '按首帧出每一镜的视频（含原生对白/环境音，取决于模型）。' },
  music: { title: '配乐模型', desc: '「配乐」模块按意图出成段音乐。统一网关不覆盖这一档，单独配。' },
};
const EMPTY_ROLE_HINT = '目前没有可选的来源（内置来源不覆盖这一档，可以装一个提供它的插件）';

// 插件一行：名字 版本（状态）；没装就说没装
function pluginSummary(list) {
  if (!list || !list.length) return '没有装插件';
  return list.map(p => `${p.name}${p.version ? ' ' + p.version : ''}${p.status === 'loaded' ? '' : '（' + (p.message || p.status) + '）'}`).join('；');
}
const FIELD_LABELS = { base_url: '接口地址（base_url）', api_key: '密钥（api_key）', model: '模型 id', voice: '音色（voice）' };

async function showSettingsPage() {
  document.querySelectorAll('aside .item.active').forEach(el => el.classList.remove('active'));
  state.current = null;
  const main = document.getElementById('main');
  main.innerHTML = '<div class="empty">加载模型设置…</div>';
  let data;
  try {
    data = await api('/api/settings');
  } catch (e) {
    main.innerHTML = `<div class="empty">加载失败：${escapeHtml(e.message)}</div>`;
    return;
  }
  state.settingsDoc = data;
  renderSettingsPage();
}

function renderSettingsPage() {
  const data = state.settingsDoc;
  const h = state.health;
  const main = document.getElementById('main');
  let html = `<div class="ep-header"><h2>模型设置</h2>
    <span class="hint">密钥只存在本机，不进任何仓库，也不会上传到本产品之外的地方。</span></div>`;
  if (h) {
    html += `<div class="env-table">
      <span>版本</span><code>v${escapeHtml(h.version)}${h.frozen ? '（桌面版）' : '（开发态）'}</code>
      <span>设置文件</span><code>${escapeHtml(h.settings_path)}</code>
      <span>项目目录</span><code>${escapeHtml(h.project_root)}</code>
      <span>ffmpeg</span><code>${h.ffmpeg_ok ? escapeHtml(h.ffmpeg) : '未找到（成片装配、后期层、台词识别需要它）'}</code>
      <span>台词识别闸</span><code>${h.asr_available ? 'faster-whisper 可用' : '未安装 faster-whisper（可选；pip install faster-whisper）'}</code>
      <span>插件</span><code data-plugins>${escapeHtml(pluginSummary(h.plugins || data.plugins))}</code>
    </div>`;
  } else {
    html += `<div class="env-table"><span>设置文件</span><code>${escapeHtml(data.path)}</code></div>`;
  }
  html += `<div class="notice" style="margin:0 0 14px">四类模型各选一个来源。只有一个 <b>OpenRouter</b> key 的话：文本选「OpenAI 兼容对话」并把接口地址填 <code>https://openrouter.ai/api/v1</code>，生图/视频/语音都选 OpenRouter 那一项。
    切换来源时已经填过的字段会保留，直到你点「保存」才写盘。</div>`;
  for (const role of data.roles) {
    const cfg = data.settings.roles[role] || {};
    const adapters = data.adapters[role] || {};
    const missing = data.status[role] || [];
    const meta = ROLE_LABELS[role] || { title: role, desc: '' };
    // 存着的来源不在表里（提供它的插件没装上）：显示成选中的「X（未加载）」且不可再选。以前下拉框会退回「（未选择）」，
    // 点保存就把 adapter:"" 发出去，悄悄清掉来源（外带清掉密钥）
    const gone = cfg.adapter && !adapters[cfg.adapter] ? cfg.adapter : '';
    html += `<section class="card" data-role="${role}" style="margin-bottom:14px">
      <div class="card-head"><h3 style="margin:0">${meta.title}</h3>
        <span class="hint">${meta.desc}</span>
        <span class="pill ${missing.length ? 'pill-warn' : 'pill-ok'}" data-status>${missing.length ? '未配置：' + escapeHtml(missing.join('、')) : '已配置'}</span></div>
      <div class="form-grid">
        <label>来源
          <select data-field="adapter">
            <option value="">（未选择）</option>
            ${Object.entries(adapters).map(([id, a]) => `<option value="${id}" ${cfg.adapter === id ? 'selected' : ''}>${escapeHtml(a.label)}${a.plugin ? '（插件）' : ''}</option>`).join('')}
            ${gone ? `<option value="${escapeHtml(gone)}" disabled selected data-unavailable>${escapeHtml(gone)}（未加载）</option>` : ''}
          </select>
        </label>
        <div data-fields></div>
        <div class="hint" data-hint></div>
      </div>
      <div class="row" style="gap:8px;margin-top:8px">
        <button class="save-btn compact" data-act="save">保存</button>
        <button class="gen-btn" data-act="test">测试连接</button>
        <span class="hint" data-result></span>
      </div>
    </section>`;
  }
  main.innerHTML = html;
  for (const section of main.querySelectorAll('section[data-role]')) {
    const role = section.dataset.role;
    const select = section.querySelector('[data-field="adapter"]');
    // 切换来源时保留已经敲进去的字段（base_url / api_key / model / voice），
    // 不然选错一次来源再选回来，刚粘的密钥就没了——2026-09-07 BACKLOG M1。
    const typed = {};
    const captureTyped = () => {
      section.querySelectorAll('[data-fields] input').forEach(inp => { if (inp.value !== '') typed[inp.dataset.field] = inp.value; });
    };
    const renderFields = () => {
      const adapters = data.adapters[role] || {};
      const a = adapters[select.value];
      const cfg = data.settings.roles[role] || {};
      const box = section.querySelector('[data-fields]');
      const gone = (data.unavailable || {})[role];
      const hint = a ? a.hint : (gone && gone.adapter === select.value ? gone.message : (Object.keys(adapters).length ? '' : EMPTY_ROLE_HINT));
      section.querySelector('[data-hint]').textContent = hint;
      if (!a) { box.innerHTML = ''; return; }
      box.innerHTML = a.fields.map(f => {
        const value = typed[f] !== undefined ? typed[f] : (cfg[f] || '');
        return `<label>${FIELD_LABELS[f] || f}
        <input data-field="${f}" type="${f === 'api_key' ? 'password' : 'text'}" value="${escapeHtml(value)}" placeholder="${escapeHtml((a.defaults || {})[f] || '')}" autocomplete="off"></label>`;
      }).join('');
    };
    select.onchange = () => { captureTyped(); renderFields(); };
    renderFields();
    section.querySelector('[data-act="save"]').onclick = async () => {
      const values = { adapter: select.value };
      section.querySelectorAll('[data-fields] input').forEach(inp => { values[inp.dataset.field] = inp.value; });
      const result = section.querySelector('[data-result]');
      result.textContent = '保存中…';
      try {
        const saved = await api('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ roles: { [role]: values } }) });
        data.settings = saved.settings; data.status = saved.status; data.unavailable = saved.unavailable || data.unavailable;
        for (const key of Object.keys(typed)) delete typed[key]; // 已落盘，回显以服务端（打码后）的为准
        const missing = saved.status[role] || [];
        const pill = section.querySelector('[data-status]');
        pill.textContent = missing.length ? '未配置：' + missing.join('、') : '已配置';
        pill.className = 'pill ' + (missing.length ? 'pill-warn' : 'pill-ok');
        renderFields();
        result.textContent = '已保存';
        if (typeof loadHealth === 'function') loadHealth(); // 头部/空状态/生成面板的角色状态跟着刷新
      } catch (e) {
        result.textContent = '保存失败：' + e.message;
      }
    };
    section.querySelector('[data-act="test"]').onclick = async () => {
      const result = section.querySelector('[data-result]');
      result.textContent = '测试中…（测的是已保存的配置，改了先保存）';
      const startedAt = performance.now();
      try {
        const r = await api(`/api/settings/test/${role}`, { method: 'POST' });
        const ms = Math.round(performance.now() - startedAt);
        result.textContent = `${r.ok ? '✓' : '✗'} ${r.message}（${ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : ms + 'ms'}）`;
      } catch (e) {
        const ms = Math.round(performance.now() - startedAt);
        result.textContent = `✗ ${e.message}（${ms}ms）`;
      }
    };
  }
}
