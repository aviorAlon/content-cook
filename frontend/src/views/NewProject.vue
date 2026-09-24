<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { get, post, put, pollJob } from '../api.js'
import { store, roleOk, loadEpisodes, withBusy } from '../store.js'
import { notice, prompt, confirm, toast } from '../ui.js'
import { t } from '../i18n.js'
import Examples from '../components/Examples.vue'
import ScreenplayEditor from '../components/ScreenplayEditor.vue'
import LocalTip from '../components/LocalTip.vue'

const router = useRouter()
const profiles = ref([])
const profileDetails = ref({}) // /api/camera-options 的 profile_details：{ name: { english, refs, basis, confidence } }（DESIGN §48）
const genres = ref([])        // /api/scripts/genres：[{ id, name, aliases, core, confidence, basis, source_file, pairing: { recommended, avoid, why } }]
const formCards = ref([])     // /api/scripts/forms：[{ id, name, aliases, defaults, must_have, summary }]（形态卡，DESIGN §31）
const form = reactive({ episode_id: '', title: '', direction: '', target_seconds: 90, camera_profile: '', prompt_language: 'zh', cast_hint: '', genre: '', form: '' })
const selectedGenre = computed(() => genres.value.find((g) => g.id === form.genre) || null)
const selectedForm = computed(() => formCards.value.find((f) => f.id === form.form) || null)
// 出处与置信度（DESIGN §48）：题材卡抄自 drama-skills 原卡（例子是合成虚构的）；镜头风格是按参考作品的公认风格人工设定的
const genreProvenance = computed(() => {
  const g = selectedGenre.value
  return g && g.confidence ? t('出处：drama-skills 题材卡 · 置信度 {c} · 例子为合成虚构，非数据统计', { c: t(g.confidence) }) : ''
})
const profileProvenance = computed(() => {
  const d = profileDetails.value[form.camera_profile]
  if (!d || !d.confidence) return ''
  const refs = (d.refs || []).join('')
  return refs ? t('按{refs}等作品的公认风格人工设定 · 置信度 {c} · 未做逐镜分析', { refs, c: t(d.confidence) }) : t('置信度 {c}', { c: t(d.confidence) })
})
// 题材 × 镜头风格搭配：常配 → 一句肯定；会打架 → 理由 + 可点的常配档；其余什么都不说
const pairingHint = computed(() => {
  const g = selectedGenre.value
  const p = form.camera_profile
  const pairing = g?.pairing
  if (!pairing || !p) return null
  if ((pairing.avoid || []).includes(p)) {
    return { kind: 'avoid', why: pairing.why || '', options: (pairing.recommended || []).filter((x) => x !== p && profiles.value.includes(x)) }
  }
  if ((pairing.recommended || []).includes(p)) return { kind: 'recommended' }
  return null
})
// 形态卡摘要在前端拼（英文界面也能翻）：画幅 · 单镜时长 · 每分钟镜数 · 字幕
function formSummary(card) {
  const d = card?.defaults || {}
  const [lo, hi] = d.shot_seconds || []
  const parts = [d.aspect_ratio]
  if (lo || hi) parts.push(t('单镜 {lo}–{hi} 秒', { lo, hi }))
  if (d.max_shots_per_minute) parts.push(t('每分钟不超过 {n} 镜', { n: d.max_shots_per_minute }))
  parts.push(d.subtitles ? t('字幕常开') : t('字幕可选'))
  return parts.filter(Boolean).join(' · ')
}
const status = ref('')
const draft = ref(null)       // job.result：{ spec, shots, warnings, cost_usd, usage_tokens, backend }
const draftId = ref('')
const pending = ref([])       // 恢复条：还在跑 / 没保存的 job
const stop = reactive({ stopped: false })
// 两步起草（DESIGN §32）：先出剧本（场次/节拍/对白/契约），人看过改过再拆镜。剧本存在 <id>.screenplay.json
const twoStep = ref(false)
const screenplay = ref(null)          // { episode_id, screenplay }：screenplay 就是编辑器直接改的那份
const screenplayWarnings = ref([])    // 后端 lint 的告警（保存后刷新）
const screenplayDirty = ref(false)
const screenplayError = ref('')
// 系列（DESIGN §32）：选已有系列或新建；起草时把上一集的记忆注入提示词
const seriesList = ref([])        // /api/series：[{ id, episodes, latest_no, latest_title }]
const seriesChoice = ref('')      // '' = 不属于系列；'__new__' = 新系列；否则是系列 id
const seriesNewId = ref('')
const seriesEpisodeNo = ref(null)
const seriesId = computed(() => (seriesChoice.value === '__new__' ? seriesNewId.value.trim() : seriesChoice.value) || '')
const selectedSeries = computed(() => seriesList.value.find((s) => s.id === seriesChoice.value) || null)
// 带内联标签的说明，v-html 渲染（纯常量）
const DRAFT_INTRO_HTML = '用「模型设置」里的<b>剧本文本模型</b>起草整集：角色、逐镜画面、台词、运镜。会真花钱，但和图片/视频不是一个量级。起草结果先在这页预览，确认后才写成项目。'

onMounted(async () => {
  try { form.episode_id = (await get('/api/episodes/next-id?prefix=EP')).id } catch { /* 用户自己填 */ }
  // 默认竖屏短剧（市面上跑得最多的产线）；列表里没有它时退回首项
  try { const c = await get('/api/camera-options'); profiles.value = c.profiles; profileDetails.value = c.profile_details || {}; form.camera_profile = c.profiles.includes('short_drama_vertical') ? 'short_drama_vertical' : (c.profiles[0] || '') } catch {}
  try { genres.value = await get('/api/scripts/genres') } catch { genres.value = [] }
  try { formCards.value = await get('/api/scripts/forms') } catch { formCards.value = [] }
  try { const r = await get('/api/series'); seriesList.value = Array.isArray(r) ? r : [] } catch { seriesList.value = [] }
  await checkPending()
})
function pickSeries() {
  // 选了已有系列就把集数预填成最新一集 + 1
  const s = selectedSeries.value
  if (s && !seriesEpisodeNo.value) seriesEpisodeNo.value = (s.latest_no || 0) + 1
  if (seriesChoice.value === '__new__' && !seriesEpisodeNo.value) seriesEpisodeNo.value = 1
}
function payload() {
  return {
    ...form, episode_id: form.episode_id.trim() || null, title: form.title.trim() || null, cast_hint: form.cast_hint.trim() || null,
    genre: form.genre || null, form: form.form || null,
    series_id: seriesId.value || null, episode_no: seriesId.value && seriesEpisodeNo.value ? Number(seriesEpisodeNo.value) : null,
  }
}
async function writeScreenplay() {
  if (!form.direction.trim()) { await notice(t('大致方向要填')); return }
  status.value = t('提交中…')
  stop.stopped = false
  let created
  try { created = await post('/api/scripts/jobs/screenplay', payload()) }
  catch (e) { status.value = t('提交失败：{err}', { err: e.message }); return }
  form.episode_id = created.episode_id || form.episode_id
  await watch(created.job_id, created.episode_id || form.episode_id.trim(), 'generate_screenplay')
}
function showScreenplay(episodeId, sp, warnings = []) {
  screenplay.value = { episode_id: episodeId, screenplay: sp }
  screenplayWarnings.value = warnings
  screenplayDirty.value = false
  screenplayError.value = ''
  twoStep.value = true
}
async function saveScreenplay() {
  screenplayError.value = ''
  try {
    const r = await put(`/api/episodes/storyvideo/${encodeURIComponent(screenplay.value.episode_id)}/screenplay`, { screenplay: screenplay.value.screenplay })
    screenplay.value = { episode_id: screenplay.value.episode_id, screenplay: r.screenplay }
    screenplayWarnings.value = r.warnings || []
    screenplayDirty.value = false
    toast(t('剧本已保存'))
    return r
  } catch (e) { screenplayError.value = e.message; await notice(t('保存失败：{err}', { err: e.message })); return null }
}
async function breakdown() {
  // 第二步：按已保存的剧本拆镜。编辑框改过没保存的先保存，拆的才是人看过的那份
  if (screenplayDirty.value && !(await saveScreenplay())) return
  status.value = t('提交中…')
  stop.stopped = false
  let created
  try { created = await post('/api/scripts/jobs/draft', { ...payload(), episode_id: screenplay.value.episode_id, use_screenplay: true }) }
  catch (e) { status.value = t('提交失败：{err}', { err: e.message }); return }
  await watch(created.job_id, created.episode_id || screenplay.value.episode_id)
}
async function checkPending() {
  try {
    const jobs = await get('/api/scripts/jobs')
    const byEp = {}
    for (const j of jobs) {
      if (j.dismissed || j.saved || !['pending', 'running', 'succeeded'].includes(j.status)) continue
      if (!byEp[j.episode_id] || j.created_at > byEp[j.episode_id].created_at) byEp[j.episode_id] = j
    }
    pending.value = Object.values(byEp)
  } catch { pending.value = [] }
}
async function submit() {
  if (!form.direction.trim()) { await notice(t('大致方向要填')); return }
  status.value = t('提交中…')
  stop.stopped = false
  let created
  // 项目编号由后端生成（预取过就带上）；用户只起名
  try { created = await post('/api/scripts/jobs/draft', payload()) }
  catch (e) { status.value = t('提交失败：{err}', { err: e.message }); return }
  await watch(created.job_id, created.episode_id || form.episode_id.trim())
}
async function watch(jobId, episodeId, kind = 'generate_draft') {
  let job
  const label = kind === 'generate_screenplay' ? '出剧本中…已经 {s} 秒（可以切走，回来在这页顶部找回）' : '起草中…已经 {s} 秒（可以切走，回来在这页顶部找回）'
  try {
    job = await pollJob(jobId, (j, s) => { status.value = t(label, { s }) }, stop)
  } catch (e) { status.value = t('轮询失败：{err}', { err: e.message }); return }
  if (job.status === 'watch_stopped') { status.value = t('已停止查看；起草仍在后台跑。'); return }
  if (job.status === 'failed') { status.value = ''; await notice(t('起草失败：\n\n{err}', { err: job.error })); return }
  status.value = ''
  if (kind === 'generate_screenplay') { showScreenplay(episodeId, job.result.screenplay, job.result.warnings || []); return }
  draftId.value = episodeId
  draft.value = job.result
}
async function resume(j) {
  if (j.status === 'succeeded') {
    if (j.kind === 'generate_screenplay') { showScreenplay(j.episode_id, j.result.screenplay, j.result.warnings || []); return }
    draftId.value = j.episode_id; draft.value = j.result; return
  }
  status.value = t('恢复中…')
  await watch(j.job_id, j.episode_id, j.kind)
}
async function dismiss(j) { await post(`/api/scripts/jobs/${j.job_id}/dismiss`); await checkPending() }
async function regenShot(shotId) {
  const feedback = await prompt(t('这一镜（{id}）想怎么改？留空 = 让模型自由改进', { id: shotId }), { confirmLabel: t('重写') })
  if (feedback === null) return
  status.value = t('重写 {id} 中…', { id: shotId })
  try {
    const c = await post('/api/scripts/jobs/shot', { episode_id: draftId.value, spec: draft.value.spec, shot_id: shotId, feedback: feedback || null })
    const job = await pollJob(c.job_id, (j, s) => { status.value = t('重写 {id} 中…{s} 秒', { id: shotId, s }) })
    status.value = ''
    if (job.status === 'failed') { await notice(t('重写失败：{err}', { err: job.error })); return }
    draft.value = job.result
  } catch (e) { status.value = e.message }
}
async function save() {
  await withBusy(async () => {
    try {
      await post('/api/scripts/save', { episode_id: draftId.value, spec: draft.value.spec })
      await loadEpisodes()
      router.push(`/drama/p/${draftId.value}/overview`)
    } catch (e) { await notice(t('保存失败：{err}', { err: e.message })) }
  })
}
async function discard() {
  if (await confirm(t('放弃这份草稿？它仍留在起草记录里，可以从这页顶部的恢复条找回。'))) { draft.value = null; draftId.value = ''; await checkPending() }
}
</script>

<template>
  <div class="page narrow">
    <div v-if="pending.length" class="stack" style="margin-bottom:14px">
      <div v-for="j in pending" :key="j.job_id" class="note">
        <b>{{ j.episode_id }}</b> {{ j.kind === 'generate_screenplay' ? $t('剧本还在写') : j.status === 'succeeded' ? $t('起草完了，还没保存') : $t('还在起草中') }}{{ $t('（真花过钱，标签页关了也不会丢）') }}
        <button class="btn sm" style="margin-left:8px" @click="resume(j)">{{ j.status === 'succeeded' ? $t('恢复查看') : $t('查看进度') }}</button>
        <button class="btn sm" @click="dismiss(j)">{{ $t('不再提醒') }}</button>
      </div>
    </div>

    <template v-if="!draft">
      <div class="card">
        <h3>{{ $t('从示例开始') }}</h3>
        <p class="hint" style="margin-bottom:10px">{{ $t('不用起草、不花钱：把随程序附带的示例剧集复制成你的项目，直接进入「开始制作 → 出首帧」。角色、镜头、旁白都可以在项目里改。') }}</p>
        <Examples />
      </div>
      <div class="card">
        <h3>{{ $t('用 AI 起草一集') }}</h3>
        <p class="hint" style="margin-bottom:10px" v-html="$t(DRAFT_INTRO_HTML)"></p>
        <div v-if="!roleOk('text')" class="note" style="margin-bottom:10px">{{ $t('还没配置剧本文本模型——') }}<router-link to="/settings">{{ $t('去「模型设置」填好') }}</router-link><LocalTip role="text" /></div>
        <div class="field"><label>{{ $t('项目名（可选，不填就用 AI 起的剧名）') }}</label><input v-model="form.title" :placeholder="$t('如「加班夜的许愿」')" /></div>
        <div class="field"><label>{{ $t('大致方向') }}</label><textarea v-model="form.direction" :placeholder="$t('故事方向、人物、冲突、想要的调性……越具体越好')"></textarea></div>
        <div class="grid2">
          <div class="field"><label>{{ $t('目标总时长（秒）') }}</label><input v-model.number="form.target_seconds" type="number" /></div>
          <div class="field">
            <label>{{ $t('镜头风格') }}</label>
            <select v-model="form.camera_profile" data-test="camera-profile-select"><option v-for="p in profiles" :key="p" :value="p">{{ p }}</option></select>
            <div v-if="profileProvenance" class="hint small" style="margin-top:4px" data-test="profile-provenance" :title="profileDetails[form.camera_profile]?.basis">{{ profileProvenance }}</div>
          </div>
          <div class="field">
            <label>{{ $t('题材（可选）') }}</label>
            <select v-model="form.genre" data-test="genre-select"><option value="">{{ $t('（不指定）') }}</option><option v-for="g in genres" :key="g.id" :value="g.id">{{ g.name }}</option></select>
            <div v-if="selectedGenre" class="hint" style="margin-top:4px">{{ selectedGenre.core }}</div>
            <div v-if="genreProvenance" class="hint small" style="margin-top:2px" data-test="genre-provenance" :title="[selectedGenre.basis, selectedGenre.source_file].filter(Boolean).join(' · ')">{{ genreProvenance }}</div>
          </div>
          <div class="field" data-test="form-field">
            <label>{{ $t('形态（可选）') }}</label>
            <select v-model="form.form" data-test="form-select"><option value="">{{ $t('（不指定）') }}</option><option v-for="f in formCards" :key="f.id" :value="f.id">{{ $t(f.name) }}</option></select>
            <div v-if="selectedForm" class="hint" style="margin-top:4px" data-test="form-summary">{{ formSummary(selectedForm) }}<br>{{ $t('画幅、单镜节奏和必带项按这张卡定；运动预算句会进每镜视频提示词，事后检查也按它核对。') }}</div>
          </div>
          <div class="field"><label>{{ $t('提示词语言') }}</label><select v-model="form.prompt_language"><option value="zh">{{ $t('中文') }}</option><option value="en">{{ $t('英文') }}</option></select></div>
        </div>
        <div v-if="pairingHint" class="hint small" style="margin:-4px 0 8px" :data-test="'pairing-' + pairingHint.kind">
          <template v-if="pairingHint.kind === 'recommended'">{{ $t('这个题材常配这档镜头风格') }}</template>
          <template v-else>
            {{ $t('这个组合容易打架：{why}', { why: pairingHint.why }) }}
            <span v-if="pairingHint.options.length"> {{ $t('常配：') }}<button v-for="o in pairingHint.options" :key="o" class="btn sm" style="margin-left:4px" :data-test="'pairing-option-' + o" @click="form.camera_profile = o">{{ o }}</button></span>
          </template>
        </div>
        <div class="field"><label>{{ $t('角色/关系提示（可选）') }}</label><textarea v-model="form.cast_hint" :placeholder="$t('主要角色、关系、声音特征……不填就让模型自己设计')"></textarea></div>
        <div class="grid2" data-test="series-fields">
          <div class="field">
            <label>{{ $t('系列（可选）') }}</label>
            <select v-model="seriesChoice" data-test="series-select" @change="pickSeries">
              <option value="">{{ $t('（单集，不属于系列）') }}</option>
              <option v-for="s in seriesList" :key="s.id" :value="s.id">{{ s.id }}{{ s.latest_no ? $t('（已到第 {n} 集）', { n: s.latest_no }) : '' }}</option>
              <option value="__new__">{{ $t('（新系列…）') }}</option>
            </select>
            <input v-if="seriesChoice === '__new__'" v-model="seriesNewId" data-test="series-new-id" :placeholder="$t('系列 id（英文/数字/短横线）')" style="margin-top:4px" />
            <div v-if="selectedSeries" class="hint" style="margin-top:4px">{{ $t('上一集《{title}》的角色外形、未决线索和已成立的事实会注入起草提示词，同名角色的外形原样沿用。', { title: selectedSeries.latest_title || selectedSeries.id }) }}</div>
          </div>
          <div v-if="seriesChoice" class="field"><label>{{ $t('第几集') }}</label><input v-model.number="seriesEpisodeNo" type="number" min="1" data-test="series-episode-no" /></div>
        </div>
        <label class="row small" style="gap:6px;margin:6px 0" data-test="two-step-toggle"><input type="checkbox" v-model="twoStep" /> {{ $t('两步起草：先出剧本（场次 / 节拍 / 对白 / 单集契约），看过改过再拆镜') }}</label>
        <div v-if="twoStep && screenplay" class="card" style="padding:12px 14px" data-test="screenplay-panel">
          <div class="row between">
            <b>{{ $t('剧本') }} <span class="mono hint">{{ screenplay.episode_id }}</span></b>
            <span class="hint small">{{ $t('逐场改（场次、节拍、对白、契约）；拆镜会按保存后的版本来。') }}</span>
          </div>
          <div v-if="screenplayError" class="note bad small" style="margin-top:6px" data-test="screenplay-error">{{ screenplayError }}</div>
          <ScreenplayEditor :doc="screenplay.screenplay" :warnings="screenplayWarnings" @dirty="screenplayDirty = true">
            <template #actions>
              <button class="btn sm" @click="saveScreenplay" :disabled="!screenplayDirty" data-test="save-screenplay">{{ $t('保存剧本') }}</button>
              <button class="btn spend" @click="breakdown" :disabled="!!status" data-test="breakdown">{{ $t('拆镜') }}</button>
              <button class="btn sm spend" @click="writeScreenplay" :disabled="!!status">{{ $t('重新出剧本') }}</button>
            </template>
          </ScreenplayEditor>
        </div>
        <div class="row">
          <button v-if="twoStep && !screenplay" class="btn spend" @click="writeScreenplay" :disabled="!!status" data-test="write-screenplay">{{ $t('出剧本') }}</button>
          <button v-else-if="!twoStep" class="btn spend" @click="submit" :disabled="!!status" data-test="submit-draft">{{ $t('生成草稿') }}</button>
          <button v-if="status" class="btn sm" @click="stop.stopped = true">{{ $t('停止查看进度') }}</button>
          <span class="status">{{ status }}</span>
        </div>
      </div>
    </template>

    <template v-else>
      <div class="row between" style="margin-bottom:10px">
        <div><h2 style="font-size:18px">{{ draft.spec.title_zh || draftId }} <span class="pill">{{ $t('草稿，尚未保存') }}</span></h2>
          <div class="hint">{{ draft.spec.logline }}</div>
          <div class="hint" v-if="typeof draft.cost_usd === 'number'">{{ $t('这次起草花费 ${v}', { v: draft.cost_usd.toFixed(4) }) }}</div>
          <div class="hint" v-else-if="typeof draft.usage_tokens === 'number'">{{ $t('这次起草用了 {n} tokens', { n: draft.usage_tokens }) }}</div>
        </div>
        <div class="row"><button class="btn" @click="discard">{{ $t('放弃') }}</button><button class="btn primary" @click="save">{{ $t('保存为项目') }}</button></div>
      </div>
      <div v-if="draft.warnings?.length" class="note" style="margin-bottom:10px">
        <div v-for="(w, i) in draft.warnings" :key="i">⚠ {{ w }}</div>
      </div>
      <div class="hint" style="margin-bottom:8px">{{ $t('下面是逐镜预览。保存成项目后，每一镜的画面、台词、运镜、角色都能在项目里细改；这里只做"整体像不像"的判断，不满意的镜可以让模型重写。') }}</div>
      <div class="card" v-for="s in draft.shots" :key="s.id" style="padding:12px 14px">
        <div class="row between">
          <div><b class="mono">{{ s.id }}</b> <span class="hint">{{ s.dur }}s · {{ (s.cast || []).join('、') || $t('无角色') }}{{ s.camera_move ? ' · ' + $t('运镜 {v}', { v: s.camera_move }) : '' }}</span></div>
          <button class="btn sm spend" @click="regenShot(s.id)">{{ $t('重写这一镜') }}</button>
        </div>
        <div v-if="s.shot_error" class="bad small" style="margin-top:4px">⚠ {{ s.shot_error }}</div>
        <div class="small" style="margin-top:6px">{{ draft.spec.shots.find(x => x.id === s.id)?.shot }}｜{{ draft.spec.shots.find(x => x.id === s.id)?.art }}</div>
        <div class="small hint">{{ draft.spec.shots.find(x => x.id === s.id)?.motion }}</div>
        <div v-for="(l, i) in s.lines" :key="i" class="small" style="margin-top:4px"><b>{{ l.who }}</b>：{{ l.zh }}</div>
      </div>
      <div class="row" style="margin-top:12px"><span class="status">{{ status }}</span></div>
    </template>
  </div>
</template>
