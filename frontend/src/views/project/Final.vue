<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { store, runGenerate, review, withBusy, refreshProject } from '../../store.js'
import { confirm, notice, toast, T } from '../../ui.js'
import { t } from '../../i18n.js'
import { get, post, ep, pollTask } from '../../api.js'
import QcBadge from '../../components/QcBadge.vue'
import { loadStyleCards, groupCards, cardName, styleCardById, cardAnchor } from '../../stylecards.js'

const d = computed(() => store.detail)
const s = computed(() => store.summary)
const mb = computed(() => d.value?.final_size_bytes ? (d.value.final_size_bytes / 1048576).toFixed(1) + ' MB' : '')
const gen = () => withBusy(() => runGenerate('final'))
async function pass() { await withBusy(async () => { try { await review('final', 'pass', ['final']); toast(t('成片已通过')) } catch (e) { await notice(e.message) } }) }
// 没有「成片重拍」按钮：runner 不收成片的重拍裁决（成片是拼出来的，要重的是镜次），以前这个按钮每次点都是 400。
// 成片有问题 → 回镜头页重拍出问题的镜，重拍会让成片自动作废，改完再重新合成（按钮下面的提示说的就是这个）

// ---- 交付包：spec 快照 + 提示词 + 首帧/视频/成片/旁白 + manifest + checksums，打成 zip（本地拷贝，不花钱）
const packages = ref([])
const exportStatus = ref('')
const fmtMB = (n) => (n == null ? '' : (n / 1048576).toFixed(1) + ' MB')
async function loadPackages() {
  if (!store.projectId) return
  try { packages.value = (await get(`${ep(store.projectId)}/export`)).packages || [] } catch { packages.value = [] }
}
async function exportPackage() {
  let created
  try { created = await post(`${ep(store.projectId)}/export`) } catch (e) { await notice(e.message); return }
  const label = t('打包中')
  store.gen = { text: `${label}…` }
  exportStatus.value = ''
  try {
    const task = await pollTask(created.task_id, (_t, sec) => { store.gen = { text: t('{label}…已经 {s} 秒（可以切走）', { label, s: sec }) } })
    if (task.status === 'failed') { await notice(t('打包失败：') + '\n\n' + task.error); return }
    const r = task.result
    exportStatus.value = t('已导出 {name}：{n} 个文件，{size}', { name: r.name, n: r.files, size: fmtMB(r.bytes) })
    toast(t('交付包已生成'))
  } catch (e) { await notice(t('打包失败：') + '\n\n' + e.message)   // 服务重启 / 任务记录没了：轮询抛错也要有一句提示
  } finally { store.gen = null; await loadPackages() }
}
const deliverableLabel = (kind) => kind === 'post' ? t('后期合成版') : kind === 'final' ? t('装配版') : t('无成片')

// ---- 海报（DESIGN §36）：底图 = 无文字关键画面（模型 1 次生图，或某一镜首帧不花钱），标题本机用字幕字体叠上去
const poster = ref(null)
const pTitle = ref('')
const pSubtitle = ref('')
const pFont = ref('')
const pLayout = ref('auto')
const pKicker = ref(null)   // null = 用建议值；'' = 不要小标
const pSource = ref('model')
const pShot = ref('')
const posterError = ref('')
// 出图风格卡（docs/image-style-cards.md §4）：挑了就进海报请求的 style_card，不挑就是现在的行为
const pStyleCard = ref('')
const styleGroups = computed(() => groupCards('poster', { only: true, keep: pStyleCard.value }))
const styleCardNow = computed(() => styleCardById(pStyleCard.value))
const styleCardText = computed(() => cardAnchor(styleCardNow.value))
const styleLabel = (c) => cardName(c)
const framesForPoster = computed(() => poster.value?.shots_with_frames || [])
async function loadPoster() {
  if (!store.projectId) return
  try {
    const p = await get(`${ep(store.projectId)}/poster`)
    poster.value = p
    if (!pTitle.value) pTitle.value = p.latest?.title || p.suggested_title || ''
    if (!pSubtitle.value) pSubtitle.value = p.latest?.subtitle ?? (p.suggested_subtitle || '')
    if (pKicker.value === null) pKicker.value = p.latest?.kicker ?? (p.suggested_kicker || '')
    if (p.latest?.layout && pLayout.value === 'auto') pLayout.value = p.latest.layout
    if (!pShot.value) pShot.value = (p.shots_with_frames || [])[0] || ''
    if (!(p.shots_with_frames || []).length) pSource.value = 'model'
  } catch { poster.value = null }
}
function posterBody() {
  const b = { source: pSource.value, title: pTitle.value.trim(), subtitle: pSubtitle.value.trim(), font: pFont.value || null,
              layout: pLayout.value, kicker: (pKicker.value ?? '').trim(), shot_id: pSource.value === 'frame' ? pShot.value : null }
  if (pStyleCard.value) b.style_card = pStyleCard.value
  return b
}
async function generatePoster() {
  posterError.value = ''
  const body = posterBody()
  if (pSource.value === 'model') {
    const ok = await confirm(t('会发 1 次生图请求（{model}）生成一张无文字的关键画面，再在本机叠上标题。继续？', { model: poster.value?.model || '' }), { confirmLabel: t('生成海报') })
    if (!ok) return
    let created
    try { created = await post(`${ep(store.projectId)}/poster/generate`, body) } catch (e) { posterError.value = e.message; return }
    const label = t('生成海报')
    store.gen = { text: `${label}…` }
    try {
      const task = await pollTask(created.task_id, (_t, sec) => { store.gen = { text: t('{label}…已经 {s} 秒（可以切走）', { label, s: sec }) } })
      if (task.status === 'failed') { posterError.value = task.error; return }
      toast(t('海报已生成'))
    } catch (e) { posterError.value = e.message   // 服务重启 / 任务记录没了：轮询抛错也要落到海报卡上
    } finally { store.gen = null; await loadPoster(); refreshProject().catch(() => {}) }
    return
  }
  try {
    await post(`${ep(store.projectId)}/poster/generate`, body)
    toast(t('海报已生成'))
  } catch (e) { posterError.value = e.message } finally { await loadPoster(); refreshProject().catch(() => {}) }
}
async function rerenderPoster() {
  posterError.value = ''
  const body = { title: pTitle.value.trim(), subtitle: pSubtitle.value.trim(), font: pFont.value || null, layout: pLayout.value, kicker: (pKicker.value ?? '').trim() }
  if (pStyleCard.value) body.style_card = pStyleCard.value
  try {
    await post(`${ep(store.projectId)}/poster/render`, body)
    toast(t('文字已重排'))
  } catch (e) { posterError.value = e.message } finally { await loadPoster(); refreshProject().catch(() => {}) }
}
const fmtTime = (ts) => ts ? new Date(ts * 1000).toISOString().replace('T', ' ').slice(0, 16) : ''

onMounted(() => { loadPackages(); loadPoster(); loadStyleCards() })
watch(() => store.projectId, () => { loadPackages(); pTitle.value = ''; pSubtitle.value = ''; pKicker.value = null; pLayout.value = 'auto'; pShot.value = ''; pStyleCard.value = ''; loadPoster() })
</script>

<template>
  <div class="ws-content">
    <div class="ws-center" style="max-width:820px">
      <h3 style="font-size:16px;margin-bottom:8px">{{ $t('成片') }}</h3>
      <div v-if="!d.final_url" class="card">
        <p class="hint">{{ $t('还没有成片。流程：所有镜的首帧和视频都审过 → {next}。', { next: d.has_post_layers ? $t('「生成后期合成版」（旁白、插卡、画面文字会合进去）') : $t('「合成成片」（本地拼接，不花钱）') }) }}</p>
        <p class="hint" v-if="s">{{ $t('现在：首帧通过 {fp}/{ft}，视频通过 {vp}/{vt}。', { fp: s.frames.passed.length, ft: s.frames.total, vp: s.videos.passed.length, vt: s.videos.total }) }}</p>
        <div class="row" style="margin-top:8px">
          <button class="btn primary" :disabled="!!store.gen" @click="gen">{{ $t('合成成片（装配版）') }}</button>
          <router-link v-if="d.has_post_layers" class="btn" :to="`/drama/p/${d.id}/post`" style="text-decoration:none">{{ $t('去生成后期合成版') }}</router-link>
        </div>
      </div>
      <div v-else class="card">
        <div class="row between" style="margin-bottom:8px">
          <div><b>{{ d.final_kind === 'post' ? $t('后期合成版') : $t('装配版') }}</b> <QcBadge :decision="d.final_qc_decision" /><div class="hint mono">{{ d.final_path }}{{ mb ? ' · ' + mb : '' }}</div></div>
          <a class="btn" :href="d.final_url" download style="text-decoration:none">{{ $t('下载 mp4') }}</a>
        </div>
        <video :src="d.final_url" controls preload="metadata" style="width:100%;max-width:420px;aspect-ratio:9/16;background:#000;border-radius:var(--r-md);display:block"></video>
        <div class="row" style="margin-top:10px">
          <button class="btn sm chop" :disabled="d.final_qc_decision === 'pass'" @click="pass">{{ $t('成片通过') }}</button>
          <button class="btn sm" :disabled="!!store.gen" @click="gen">{{ $t('重新合成装配版') }}</button>
          <router-link v-if="d.has_post_layers" class="btn sm" :to="`/drama/p/${d.id}/post`" style="text-decoration:none">{{ $t('重新生成后期版') }}</router-link>
        </div>
        <p class="hint" style="margin-top:8px" data-test="final-retake-hint">
          {{ $t('成片有问题：回「镜头」页重拍出问题的镜次（重拍后成片会自动作废），改完再重新合成。') }}
          <router-link :to="`/drama/p/${d.id}/shots`">{{ $t('去镜头页 →') }}</router-link>
        </p>
      </div>

      <div class="card" style="margin-top:12px" data-test="poster-card">
        <div class="row between" style="margin-bottom:6px">
          <b>{{ $t('海报') }}</b>
          <span class="hint" v-if="poster?.latest">{{ $t('最新：{time} · {source}', { time: fmtTime(poster.latest.created_at), source: poster.latest.source === 'frame' ? $t('来自首帧 {shot}', { shot: poster.latest.shot_id }) : $t('模型生成') }) }}</span>
        </div>
        <p class="hint">{{ $t('项目成型时出一张海报：底图是无文字的关键画面（用模型生成，或直接用某一镜首帧），满幅裁到 1080×1920；标题用粗体中文字体在本机叠上去，自动放在画面细节少的一端，背后渐变压暗。最新一张会当项目封面并进交付包。') }}</p>
        <div class="row" style="gap:16px;align-items:flex-start;margin-top:8px">
          <div style="width:180px;flex:none">
            <img v-if="poster?.latest" :src="poster.latest.url" data-test="poster-preview" alt="" style="width:180px;aspect-ratio:9/16;object-fit:cover;border-radius:var(--r-md);border:1px solid var(--border);display:block" />
            <div v-else class="hint" data-test="poster-empty" style="width:180px;aspect-ratio:9/16;display:flex;align-items:center;justify-content:center;border:1px dashed var(--border);border-radius:var(--r-md)">{{ $t('还没有海报') }}</div>
            <a v-if="poster?.latest" class="btn sm" :href="poster.latest.url" download style="text-decoration:none;margin-top:6px;display:inline-block">{{ $t('下载 PNG') }}</a>
          </div>
          <div style="flex:1;min-width:0">
            <label class="hint">{{ $t('标题') }}</label>
            <input v-model="pTitle" data-test="poster-title" style="width:100%" />
            <label class="hint" style="margin-top:6px;display:block">{{ $t('副标题（可空，一句话，不超过 {n} 字；超出会在标点处截断）', { n: poster?.subtitle_max || 24 }) }}</label>
            <input v-model="pSubtitle" data-test="poster-subtitle" style="width:100%" :maxlength="(poster?.subtitle_max || 24) * 2" />
            <label class="hint" style="margin-top:6px;display:block">{{ $t('小标（标题上方一行小字，如「第 1 集」；留空不要）') }}</label>
            <input v-model="pKicker" data-test="poster-kicker" style="width:100%" />
            <div class="row" style="margin-top:6px;gap:12px;flex-wrap:wrap">
              <div><label class="hint" style="display:block">{{ $t('标题位置') }}</label>
                <select v-model="pLayout" data-test="poster-layout">
                  <option value="auto">{{ $t('自动（放在画面细节少的一端）') }}</option>
                  <option value="top">{{ $t('上方') }}</option>
                  <option value="bottom">{{ $t('下方') }}</option>
                </select></div>
              <div><label class="hint" style="display:block">{{ $t('字体') }}</label>
                <select v-model="pFont" data-test="poster-font">
                  <option value="">{{ $t('自动（粗体中文字体）') }}</option>
                  <option v-for="f in poster?.fonts || []" :key="f.path" :value="f.path">{{ f.label }}</option>
                </select></div>
            </div>
            <div class="row" style="margin-top:6px;gap:8px;flex-wrap:wrap">
              <label class="hint">{{ $t('出图风格卡') }}</label>
              <select v-model="pStyleCard" data-test="poster-style-card">
                <option value="">{{ $t('不指定（沿用项目的风格骨架）') }}</option>
                <optgroup v-for="g in styleGroups" :key="g.id" :label="g.name">
                  <option v-for="c in g.cards" :key="c.id" :value="c.id">{{ styleLabel(c) }}</option>
                </optgroup>
              </select>
              <span v-if="styleCardNow" class="hint" data-test="poster-style-anchor" :title="styleCardText">{{ styleCardNow.summary || styleCardText }}</span>
            </div>
            <div class="row" style="margin-top:8px;gap:12px;flex-wrap:wrap">
              <label><input type="radio" value="model" v-model="pSource" data-test="poster-source-model" /> {{ $t('用模型生成关键画面') }}</label>
              <label><input type="radio" value="frame" v-model="pSource" data-test="poster-source-frame" :disabled="!framesForPoster.length" /> {{ $t('用某一镜首帧') }}</label>
              <select v-if="pSource === 'frame'" v-model="pShot" data-test="poster-shot">
                <option v-for="sid in framesForPoster" :key="sid" :value="sid">{{ sid }}</option>
              </select>
            </div>
            <p class="hint" v-if="pSource === 'model'">{{ $t('会发 1 次生图请求（{model}），不进账本；提示词沿用项目的风格骨架、场景锚、出镜最多的角色与开场钩子，并明令画面里不出现文字。', { model: poster?.model || '' }) }}</p>
            <p class="hint" v-else>{{ $t('本地拷贝，不花钱。') }}</p>
            <div class="row" style="margin-top:8px">
              <button class="btn sm primary" :disabled="!!store.gen || !pTitle.trim() || (pSource === 'frame' && !pShot)" data-test="poster-generate" @click="generatePoster">{{ $t('生成海报') }}</button>
              <button class="btn sm" :disabled="!!store.gen || !poster?.latest || !pTitle.trim()" data-test="poster-rerender" @click="rerenderPoster">{{ $t('只重排文字') }}</button>
            </div>
            <p class="hint" v-if="posterError" data-test="poster-error" style="color:var(--danger)">{{ posterError }}</p>
            <div v-if="(poster?.history || []).length > 1" style="margin-top:8px">
              <div class="hint">{{ $t('历史（最新的在前）') }}</div>
              <div class="row" style="flex-wrap:wrap;gap:6px">
                <a v-for="h in poster.history" :key="h.poster" :href="h.url" target="_blank" :title="h.title">
                  <img :src="h.url" alt="" data-test="poster-history" style="width:54px;aspect-ratio:9/16;object-fit:cover;border-radius:4px;border:1px solid var(--border);display:block" />
                </a>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="card" style="margin-top:12px" data-test="export-card">
        <div class="row between" style="margin-bottom:6px">
          <b>{{ $t('导出交付包') }}</b>
          <button class="btn sm primary" :disabled="!!store.gen" @click="exportPackage">{{ $t('导出交付包') }}</button>
        </div>
        <p class="hint">{{ $t('一个 zip：项目快照、每镜编译好的提示词、首帧、视频、成片（后期版优先）、海报（有则带）、旁白/配音音频、审片裁决、manifest 与 sha256 校验。设置、密钥、锚图输入和账本不会进包。本地拷贝，不花钱。') }}</p>
        <p class="hint" v-if="exportStatus">{{ exportStatus }}</p>
        <table v-if="packages.length" class="tbl" style="margin-top:8px;width:100%">
          <thead><tr><th>{{ $t('文件') }}</th><th>{{ $t('内容') }}</th><th>{{ $t('成片') }}</th><th>{{ $t('时间') }}</th><th></th></tr></thead>
          <tbody>
            <tr v-for="p in packages" :key="p.name">
              <td class="mono">{{ p.name }}<div class="hint">{{ fmtMB(p.size) }}</div></td>
              <td>{{ p.files != null ? $t('{n} 个文件，{size}', { n: p.files, size: fmtMB(p.bytes) }) : '—' }}</td>
              <td>{{ p.files != null ? deliverableLabel(p.deliverable) : '—' }}</td>
              <td class="hint">{{ (p.created || '').replace('T', ' ').slice(0, 16) }}</td>
              <td><a class="btn sm" :href="p.download_url" download style="text-decoration:none">{{ $t('下载 zip') }}</a></td>
            </tr>
          </tbody>
        </table>
        <p class="hint" v-else style="margin-top:6px">{{ $t('还没有导出过。') }}</p>
      </div>
    </div>
  </div>
</template>
