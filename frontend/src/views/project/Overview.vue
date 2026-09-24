<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { store, roleOk, capabilityCheck, retryFailed, runGenerate, withBusy, reviseEdit, refreshProject } from '../../store.js'
import { get, put, post, ep } from '../../api.js'
import { notice, toast } from '../../ui.js'
import { T } from '../../ui.js'
import { t } from '../../i18n.js'
import LocalTip from '../../components/LocalTip.vue'
import ModelReviewCard from '../../components/ModelReviewCard.vue'
// 工艺清单的组名跟播客脚本页、剧本工作台共用一份（checklists.js），别各抄各的
import { CRAFT_GROUPS } from '../../checklists.js'
// 问题卡分类名同理：镜像 webui/problems.py 的 KIND_LABELS，全前端出 problems.js 这一份
import { problemKind } from '../../problems.js'

const d = computed(() => store.detail)
const s = computed(() => store.summary)
const missingRoles = computed(() => ['image', 'video'].filter(r => !roleOk(r)))
const check = ref(null)
async function loadCheck() { check.value = await capabilityCheck() }
// 视频提示词语法（方言）：老项目缺省是通用散文；当前模型有官方语法时给一键切换
const DIALECT_LABEL = { generic: '通用散文', 'minimax-h3': 'MiniMax H3 三段结构', seedance: 'Seedance 中文语法', auto: '自动' }
const dialect = computed(() => check.value?.dialect || null)
const canSwitchDialect = computed(() => dialect.value && dialect.value.effective === 'generic' && dialect.value.for_model !== 'generic' && dialect.value.setting !== 'generic')
async function useModelDialect() {
  const vp = { ...(store.detail?.video_params || {}), prompt_dialect: 'auto' }
  if (store.detail?.locked) {
    const r = await reviseEdit({ kind: 'globals', values: { video_params: vp } })
    return r
  }
  try { await put(`${ep(store.projectId)}/globals`, { values: { video_params: vp } }); await refreshProject(); await loadCheck(); toast(t('已切换：视频提示词按 {name} 写', { name: t(DIALECT_LABEL[dialect.value?.for_model] || dialect.value?.for_model) })) }
  catch (e) { await notice(e.message) }
}
// 问题卡按镜/角色分组；kind 标签渲染时翻译（分类名在 problems.js，全前端一份）
const problemGroups = computed(() => {
  const out = {}
  for (const c of (d.value?.problems || [])) { const key = c.target ? `${c.scope}:${c.target}` : 'episode'; (out[key] ||= []).push(c) }
  return out
})
const failures = computed(() => s.value?.failures || [])
// 后台轮询（DESIGN §34）：还在服务商那边的镜；「刷新」重新拉概览，后台没在等的话顺手补发 generate-resume（不花钱）
const inProgress = computed(() => s.value?.in_progress || [])
async function refreshInProgress() {
  if (s.value && s.value.watching === false) { try { await post(`${ep(store.projectId)}/generate-resume`) } catch { /* 刷新照常 */ } }
  await refreshProject()
}
// 尾帧续接（DESIGN §30）：源镜视频还没过检、这一轮被推迟的镜 [{shot_id, source, mode, reason}]
const tailPending = computed(() => s.value?.tail_pending || [])
// 形态卡（DESIGN §31）：spec.form 在这里改——下拉列 /api/scripts/forms，「不指定」= 删键；保存走 globals PUT（制作中走 revise）
const formCards = ref([])
const formId = ref('')
async function loadForms() { try { formCards.value = await get('/api/scripts/forms') } catch { formCards.value = [] } }
function syncForm() { formId.value = s.value?.form?.id || '' }
async function saveForm() {
  const values = { form: formId.value || null }
  if (store.detail?.locked) {
    const r = await reviseEdit({ kind: 'globals', values })
    if (!r) syncForm()
    return r
  }
  try {
    await put(`${ep(store.projectId)}/globals`, { values }); await refreshProject()
    const card = formCards.value.find(c => c.id === formId.value)
    toast(card ? t('形态已改为 {name}', { name: t(card.name) }) : t('已清掉形态'))
  } catch (e) { syncForm(); await notice(e.message) }
}
// 题材卡（DESIGN §49）：spec.genre 在这里改——下拉列 /api/scripts/genres，「不指定」= 删键；跟形态同一条保存路径（制作中走 revise）
const genreCards = ref([])
const genreId = ref('')
async function loadGenres() { try { const r = await get('/api/scripts/genres'); genreCards.value = Array.isArray(r) ? r : [] } catch { genreCards.value = [] } }
function syncGenre() { genreId.value = s.value?.genre?.id || '' }
async function saveGenre() {
  const values = { genre: genreId.value || null }
  if (store.detail?.locked) {
    const r = await reviseEdit({ kind: 'globals', values })
    if (!r) syncGenre()
    return r
  }
  try {
    await put(`${ep(store.projectId)}/globals`, { values }); await refreshProject()
    const card = genreCards.value.find(c => c.id === genreId.value)
    toast(card ? t('题材已改为 {name}', { name: t(card.name) }) : t('已清掉题材'))
  } catch (e) { syncGenre(); await notice(e.message) }
}
// 两张模型审稿卡（共用 ModelReviewCard）：按题材审稿（DESIGN §49）只在选了题材时出现，题材换了就换 key 重挂；
// 剧本工艺审稿跟题材无关，永远显示。引文都由后端逐字核对，对不上的降级、不计入
const GENRE_SOURCE_LABELS = { core: '题材核心', pitfall: '常见坑', drift: '禁止漂移' }
const retryable = computed(() => [...new Set(failures.value.filter(f => f.retryable).map(f => f.shot_id))])
const moderated = computed(() => [...new Set(failures.value.filter(f => f.kind === 'moderation').map(f => f.shot_id))])
async function retryAll(force = false) {
  const r = await retryFailed(null, { force })
  if (r?.reset?.length) {
    const through = failures.value.some(f => f.stage === 'frames') ? 'frames' : 'videos'
    await withBusy(() => runGenerate(through, r.reset.map(x => x.replace(/^(frame|video)-/, ''))))
  }
}
// 单集契约与系列（DESIGN §32）：不进提示词，事后由检查核对正文有没有兑现；系列交接把本集写进系列记忆
const contract = ref({ premise: '', hook: '', cliffhanger: '', must_pay_off: '', carry_over: '' })
const seriesForm = ref({ id: '', episode_no: '' })
const contractDirty = ref(false)
function loadContract() {
  const c = d.value?.episode_contract || {}
  contract.value = { premise: c.premise || '', hook: c.hook || '', cliffhanger: c.cliffhanger || '', must_pay_off: (c.must_pay_off || []).join('\n'), carry_over: (c.carry_over || []).join('\n') }
  const s = d.value?.series || {}
  seriesForm.value = { id: s.id || '', episode_no: s.episode_no || '' }
  contractDirty.value = false
}
function contractValues() {
  const lines = (text) => String(text || '').split('\n').map(x => x.trim()).filter(Boolean)
  const c = { premise: contract.value.premise.trim(), hook: contract.value.hook.trim(), cliffhanger: contract.value.cliffhanger.trim(), must_pay_off: lines(contract.value.must_pay_off), carry_over: lines(contract.value.carry_over) }
  const hasContract = Object.values(c).some(v => Array.isArray(v) ? v.length : v)
  const sid = seriesForm.value.id.trim()
  const series = sid ? { id: sid, ...(seriesForm.value.episode_no ? { episode_no: Number(seriesForm.value.episode_no) } : {}) } : null
  return { episode_contract: hasContract ? c : null, series }
}
async function saveContract() {
  const values = contractValues()
  if (store.detail?.locked) { const r = await reviseEdit({ kind: 'globals', values }); return r }
  try { await put(`${ep(store.projectId)}/globals`, { values }); await refreshProject(); loadContract(); toast(t('契约已保存')) }
  catch (e) { await notice(e.message) }
}
async function handoff() {
  if (contractDirty.value) await saveContract()
  try {
    const r = await post(`${ep(store.projectId)}/series/handoff`)
    toast(t('已写进系列「{id}」的记忆：已成立 {a} 条、未决 {b} 条', { id: r.series_id, a: r.entry.established.length, b: r.entry.open_threads.length }))
  } catch (e) { await notice(e.message) }
}
onMounted(() => { loadCheck(); loadContract(); loadForms(); syncForm(); loadGenres(); syncGenre() })
watch(() => store.projectId, loadCheck)
watch(() => s.value?.form?.id, syncForm)
watch(() => s.value?.genre?.id, syncGenre)
// 刷新（生成期间每 8 秒一次）把 detail 换成新对象也会触发这两个 watch：契约改了还没保存时不能被冲回去；保存成功后 saveContract 自己会重灌
watch(() => d.value?.episode_contract, () => { if (!contractDirty.value) loadContract() })
watch(() => d.value?.series, () => { if (!contractDirty.value) loadContract() })
</script>

<template>
  <div class="ws-content">
    <div class="ws-center" style="max-width:900px">
      <div v-if="d.readonly_reason" class="note bad" style="margin-bottom:12px">{{ d.readonly_reason }}</div>
      <div v-if="missingRoles.length" class="note" style="margin-bottom:12px">
        {{ $t('还没配置：{roles}模型——生成会在提交时被拒。', { roles: missingRoles.map(r => $t(T.role[r])).join($t('、')) }) }}<router-link to="/settings">{{ $t('去「模型设置」填好') }}</router-link>
        <LocalTip :role="missingRoles[0]" />
      </div>
      <div class="card">
        <div class="row between">
          <div>
            <h3 style="font-size:18px">{{ s.title }}</h3>
            <div class="hint">{{ s.logline || $t('（没有一句话简介）') }}</div>
          </div>
          <img v-if="s.cover_url" :src="s.cover_url" alt="" style="width:72px;aspect-ratio:9/16;object-fit:cover;border-radius:4px;border:1px solid var(--border)" />
        </div>
        <div class="row" style="margin-top:10px;gap:14px" >
          <span class="pill">{{ $t('{n} 镜 · 约 {s} 秒', { n: s.shots_total, s: Math.round(s.total_seconds) }) }}</span>
          <span class="pill">{{ $t('{c} 个角色 · {s} 个场景/道具', { c: s.cast_count, s: s.scene_count }) }}</span>
          <span class="pill">{{ $t('镜头风格 {p}', { p: s.camera_profile || $t('默认') }) }}</span>
          <label class="pill" data-test="form-pill" :title="$t('交付形态：定画幅、单镜节奏、字幕和每镜视频提示词里的运动预算句；制作中改会预告哪些镜要重出视频')">
            {{ $t('形态') }}
            <select v-model="formId" @change="saveForm" data-test="form-select" style="margin-left:4px">
              <option value="">{{ $t('（不指定）') }}</option>
              <option v-for="c in formCards" :key="c.id" :value="c.id">{{ $t(c.name) }}</option>
              <option v-if="s.form && !formCards.some(c => c.id === s.form.id)" :value="s.form.id">{{ $t(s.form.name) }}</option>
            </select>
          </label>
          <label class="pill" data-test="genre-pill" :title="$t('题材卡：给「按题材审稿」对照，不进任何提示词；制作中改不会让任何镜重出')">
            {{ $t('题材') }}
            <select v-model="genreId" @change="saveGenre" data-test="genre-select" style="margin-left:4px">
              <option value="">{{ $t('（不指定）') }}</option>
              <option v-for="c in genreCards" :key="c.id" :value="c.id">{{ $t(c.name) }}</option>
              <option v-if="s.genre && !genreCards.some(c => c.id === s.genre.id)" :value="s.genre.id">{{ $t(s.genre.name) }}</option>
            </select>
          </label>
          <span class="pill" :class="s.locked ? 'warn' : ''">{{ s.locked ? $t('制作中') : $t('草稿') }}</span>
        </div>
      </div>
      <div class="card">
        <h3>{{ $t('进度') }}</h3>
        <table class="tbl">
          <tr><th>{{ $t('阶段') }}</th><th>{{ $t('完成') }}</th><th>{{ $t('已审通过') }}</th><th>{{ $t('待审') }}</th><th>{{ $t('待重拍') }}</th></tr>
          <tr><td>{{ $t('首帧') }}</td><td>{{ s.frames.done }} / {{ s.frames.total }}</td><td>{{ s.frames.passed.length }}</td><td>{{ s.frames.pending.length }}</td><td>{{ s.frames.retake.join($t('、')) || '—' }}</td></tr>
          <tr><td>{{ $t('视频') }}</td><td>{{ s.videos.done }} / {{ s.videos.total }}</td><td>{{ s.videos.passed.length }}</td><td>{{ s.videos.pending.length }}</td><td>{{ s.videos.retake.join($t('、')) || '—' }}</td></tr>
          <tr><td>{{ $t('台词识别') }}</td><td colspan="4">{{ s.asr.ran ? $t('通过 {pass} · 不符 {fail}', { pass: s.asr.pass, fail: s.asr.fail }) : $t('还没跑（视频出来后在「镜头」页或「成片」页跑）') }}</td></tr>
          <tr><td>{{ $t('后期层') }}</td><td colspan="4">{{ s.post_layers.present ? (s.post_layers.composed ? $t('已合成后期版') : $t('有旁白/插卡，还没合成')) : $t('本集没有旁白、插卡、画面文字') }}</td></tr>
          <tr><td>{{ $t('成片') }}</td><td colspan="4">{{ s.final.exists ? `${s.final.kind === 'post' ? $t('后期合成版') : $t('装配版')} · ${$t(T.qc[s.final.decision] || '待审')}` : $t('还没合成') }}</td></tr>
        </table>
        <p class="hint" style="margin:10px 0 0">{{ $t('下一步：') }}<b>{{ s.next_step.label }}</b>{{ $t('——{hint}。顶部按钮永远指向这一步。', { hint: s.next_step.hint }) }}</p>
        <p class="hint" v-if="s.poster?.hint" data-test="poster-hint" style="margin:4px 0 0">{{ $t('海报：') }}{{ s.poster.hint }}</p>
      </div>
      <div v-if="inProgress.length" class="card" data-test="in-progress-card" style="border-color:var(--warn)">
        <div class="row between">
          <span class="pill warn" data-test="in-progress-pill">{{ s.watching === false
            ? $t('生成中：{list}（后台没有在等——点「刷新」接上）', { list: inProgress.join('、') })
            : $t('生成中：{list}（后台在等服务商）', { list: inProgress.join('、') }) }}</span>
          <button class="btn sm" @click="refreshInProgress" data-test="in-progress-refresh">{{ $t('刷新') }}</button>
        </div>
      </div>
      <div v-if="failures.length" class="card" data-test="failures-card" style="border-color:var(--bad)">
        <h3>{{ $t('失败的任务') }} <span class="pill bad">{{ failures.length }}</span></h3>
        <table class="tbl">
          <tr><th>{{ $t('镜') }}</th><th>{{ $t('阶段') }}</th><th>{{ $t('原因') }}</th><th>{{ $t('怎么办') }}</th></tr>
          <tr v-for="f in failures" :key="f.step_id">
            <td><router-link :to="`/drama/p/${store.projectId}/shots/${f.shot_id}`" class="mono">{{ f.shot_id }}</router-link> <span v-if="(f.attempts || 0) >= 2" class="pill small" data-test="attempts-pill" :title="$t('同一份提示词已出过 {n} 次', { n: f.attempts })">{{ $t('第 {n} 次', { n: f.attempts }) }}</span></td>
            <td>{{ f.stage === 'frames' ? $t('首帧') : $t('视频') }}</td>
            <td><b>{{ f.label }}</b><div class="hint small mono" style="max-width:420px;word-break:break-all">{{ f.error }}</div></td>
            <td class="small">{{ f.advice }}</td>
          </tr>
        </table>
        <div class="row" style="margin-top:10px">
          <button v-if="retryable.length" class="btn primary sm" @click="retryAll(false)">{{ $t('重提失败任务（{n} 镜）并重新生成', { n: retryable.length }) }}</button>
          <button v-if="moderated.length" class="btn sm" @click="retryAll(true)">{{ $t('强制重提审核拦截的镜') }}</button>
          <span class="hint">{{ $t('重提不花钱；重新生成才会发起请求。') }}</span>
        </div>
      </div>
      <div v-if="tailPending.length" class="card" data-test="tail-pending-card">
        <h3>{{ $t('等尾帧') }} <span class="pill warn">{{ tailPending.length }}</span></h3>
        <p class="hint small" style="margin:4px 0 8px">{{ $t('这些镜的首帧要接源镜视频的最后一帧：源镜视频过检后下一轮自动抽帧、接上；这一轮不提交它们。') }}</p>
        <div class="row" style="flex-wrap:wrap;gap:6px">
          <router-link v-for="tp in tailPending" :key="tp.shot_id" :to="`/drama/p/${store.projectId}/shots/${tp.shot_id}`" class="pill" data-test="tail-pending-pill" :title="tp.reason">
            {{ $t('{shot} ← 等 {source} 视频过检', { shot: tp.shot_id, source: tp.source }) }}{{ tp.mode === 'direct' ? $t('（直接当首帧）') : '' }}
          </router-link>
        </div>
      </div>
      <div v-if="check && (check.issues?.length || dialect)" class="card">
        <h3>{{ $t('模型能力检查') }} <span v-if="check.issues?.length" class="pill" :class="check.errors ? 'bad' : 'warn'">{{ check.errors ? $t('{n} 项会被拒', { n: check.errors }) : $t('仅提醒') }}</span></h3>
        <div v-if="dialect" class="row small" style="margin-bottom:6px" data-test="dialect-row">
          <span>{{ $t('视频提示词语法：') }}<b>{{ $t(DIALECT_LABEL[dialect.effective] || dialect.effective) }}</b>{{ dialect.setting === 'auto' ? $t('（按当前视频模型自动选）') : '' }}</span>
          <button v-if="canSwitchDialect" class="btn sm" @click="useModelDialect">{{ $t('改用 {name}（按模型自动）', { name: $t(DIALECT_LABEL[dialect.for_model] || dialect.for_model) }) }}</button>
          <span v-if="canSwitchDialect" class="hint">{{ store.detail?.locked ? $t('制作中：会预告哪些镜要重出视频，再保存为新版本') : $t('只改视频提示词的写法，内容不变') }}</span>
        </div>
        <div v-for="(i, k) in check.issues" :key="k" class="small" :class="i.level === 'error' ? 'bad' : i.level === 'warn' ? 'warn' : 'hint'" style="margin:3px 0">
          {{ i.level === 'error' ? '✗' : i.level === 'warn' ? '⚠' : '·' }} {{ i.message }}
        </div>
        <p class="hint" style="margin:8px 0 0">{{ $t('按当前「模型设置」里的生图 {img} 和视频 {vid} 判断；换了来源要重新看。', { img: check.image?.model || '—', vid: check.video?.model || '—' }) }}</p>
      </div>
      <div class="card" data-test="contract-card">
        <div class="row between">
          <h3>{{ $t('单集契约') }} <span v-if="d.series" class="pill">{{ $t('系列 {id}', { id: d.series.id }) }}{{ d.series.episode_no ? $t(' · 第 {n} 集', { n: d.series.episode_no }) : '' }}</span></h3>
          <div class="row">
            <button class="btn sm primary" @click="saveContract" :disabled="!contractDirty" data-test="save-contract">{{ $t('保存契约') }}</button>
            <button class="btn sm" @click="handoff" :disabled="!seriesForm.id.trim()" data-test="series-handoff" :title="$t('把本集的契约、角色外形、已兑现的承诺和未决线索写进系列记忆，起草下一集时注入')">{{ $t('系列交接') }}</button>
          </div>
        </div>
        <p class="hint" style="margin:4px 0 8px">{{ $t('写镜之前先立的承诺，不进任何提示词；检查会核对钩子是否在前两镜、集尾是否落在最后一镜、承诺兑现的事有没有写进某一镜。') }}</p>
        <div class="grid2" @input="contractDirty = true" @change="contractDirty = true">
          <div class="field"><label>{{ $t('前提（进入状态 + 谁现在要什么）') }}</label><input v-model="contract.premise" data-test="contract-premise" /></div>
          <div class="field"><label>{{ $t('开场钩子（观众能立即理解的未稳定局面）') }}</label><input v-model="contract.hook" data-test="contract-hook" /></div>
          <div class="field"><label>{{ $t('集尾（某人已经决定做什么 / 某个后果已经启动）') }}</label><input v-model="contract.cliffhanger" data-test="contract-cliffhanger" /></div>
          <div class="field"><label>{{ $t('本集必须兑现（一行一条）') }}</label><textarea v-model="contract.must_pay_off" data-test="contract-must-pay-off"></textarea></div>
          <div class="field"><label>{{ $t('留给下一集（一行一条：谁知道了什么、谁拿着什么、哪个危险已经开始）') }}</label><textarea v-model="contract.carry_over" data-test="contract-carry-over"></textarea></div>
          <div class="field">
            <label>{{ $t('系列 id（可选）') }}</label><input v-model="seriesForm.id" data-test="series-id" :placeholder="$t('如 kitchen；英文/数字/短横线')" />
            <label style="margin-top:4px">{{ $t('第几集') }}</label><input v-model="seriesForm.episode_no" type="number" min="1" data-test="series-episode-no" />
          </div>
        </div>
      </div>
      <ModelReviewCard v-if="s.genre" :key="`genre-${s.genre.id}`" title="按题材审稿" path="genre-review" :group-labels="GENRE_SOURCE_LABELS" test-prefix="genre-review"
        :name="$t(s.genre.name)" hint="用文本模型按题材卡的常见坑和禁止漂移逐条对照剧本；每条问题都要引用剧本原文，引文对不上的不算"
        confirm-text="按题材卡「{name}」审一遍剧本：{note}。" footer="这是模型按题材卡的判断，不是保证" />
      <ModelReviewCard title="剧本工艺审稿" path="craft-review" :group-labels="CRAFT_GROUPS" test-prefix="craft-review"
        :name="$t('剧本工艺清单')" hint="用文本模型按张力 / 情绪可见 / 节奏 / 合规四组工艺清单逐条对照剧本，跟题材无关；每条问题都要引用剧本原文，引文对不上的不算；合规组的「违反」是一票否决级别的风险"
        confirm-text="按工艺清单审一遍剧本：{note}。" footer="这是模型按工艺清单的判断，不是保证"
        missing-text="模型漏答了 {n} 条清单条目" dropped-text="丢掉 {n} 条编号对不上清单的回答" />
      <details v-if="d.problems?.length" class="fold card" style="padding:10px 14px" data-test="problems">
        <summary>{{ $t('{n} 条检查提醒（不阻止生成，但通常值得看一眼）', { n: d.problems.length }) }}</summary>
        <div v-for="(group, key) in problemGroups" :key="key" style="margin:8px 0 4px">
          <div class="small" style="font-weight:600;margin-bottom:3px">
            <router-link v-if="group[0].scope === 'shot'" :to="`/drama/p/${store.projectId}/shots/${group[0].target}`" class="link mono">{{ group[0].target }}</router-link>
            <router-link v-else-if="group[0].scope === 'cast'" :to="`/drama/p/${store.projectId}/cast/${group[0].target}`" class="link mono">{{ group[0].target }}</router-link>
            <span v-else>{{ $t('全集') }}</span>
          </div>
          <div v-for="(c, i) in group" :key="i" class="small" style="margin:2px 0 2px 8px;display:flex;gap:6px;align-items:flex-start">
            <span class="pill" style="flex-shrink:0">{{ $t(problemKind(c.kind)) }}</span>
            <span>{{ c.impact }}<template v-if="c.fix"> <span class="hint">→ {{ c.fix }}</span></template></span>
          </div>
        </div>
      </details>
    </div>
  </div>
</template>
