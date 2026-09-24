<script setup>
// 清单（docs/imageset-spec.md §5）：items 表——缩略图（current）、标签、prompt 可改（PUT 整份项目文件）、状态、「重出这一张」、
// 历次产物条（「采用」= POST /pick {item, attempt}）、挑选勾（POST /pick {picks}）。
// 头部：「展开清单」= POST /expand（不调模型）；「生成缺的」/「全部重出」= POST /generate（先确认 N 次生图请求）。
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { post, put } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { confirm, notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { setUrl, specBody, refreshSet, runSetTask, itemStatus, hasCurrent, itemImageUrl, itemAttempts, isCurrentAttempt, itemRow, countRequests, loadTemplates } from '../../imageset.js'
import { loadStyleCards, styleCardById, cardName, dialectOf, dialectName, dialectHint, dialectOptions } from '../../stylecards.js'
import StyleCardPicker from '../../components/StyleCardPicker.vue'

const STATUS = { todo: '待出', running: '出图中', done: '已出', failed: '失败', skipped: '跳过' }
const route = useRoute()
const m = computed(() => route.meta.module || 'sticker')
const s = computed(() => store.set)
const d = computed(() => store.setDetail)
const items = ref([])
const picks = ref([])
const dirty = ref(false)
const saveError = ref('')
// 风格行的本地状态（保存时随整份项目文件 PUT 回去）
const style = ref('')
const styleAnchor = ref('')
const dialect = ref('')
const pickerOpen = ref(false)
const styleChanged = ref(false)
const clone = (x) => JSON.parse(JSON.stringify(x))
function sync() { items.value = clone(s.value?.items || []); picks.value = [...(s.value?.picks || [])]; dirty.value = false }
watch(() => s.value?.id, () => { sync(); syncStyle(); saveError.value = '' }, { immediate: true })

// ---- 风格行（docs/image-style-cards.md §5）：当前风格名 + 「换风格」开挑选器；换过之后提示「换风格要重出才生效」。
const templates = ref([])
watch(m, async (mod) => { templates.value = await loadTemplates(mod) }, { immediate: true })
loadStyleCards()
const tpl = computed(() => templates.value.find(x => x.id === s.value?.template) || null)
const presets = computed(() => tpl.value?.styles || [])
const fitsKey = computed(() => (s.value?.template ? `${m.value}.${s.value.template}` : ''))
function syncStyle() {
  style.value = s.value?.style || ''
  styleAnchor.value = s.value?.style_anchor || ''
  dialect.value = dialectOf(d.value, s.value).override
  pickerOpen.value = false
  styleChanged.value = false
}
const styleName = computed(() => {
  const id = style.value
  if (!id || id === 'custom') return t('自写风格锚')
  const c = styleCardById(id)
  if (c) return cardName(c)
  return presets.value.find(x => x.id === id)?.name || id
})
// 换风格：预设换成预设自带的锚句，风格卡清空自写锚（锚句由卡给），custom 保留（「改成自写」会填好起点）
function onStyle(v) {
  style.value = v
  const p = presets.value.find(x => x.id === v)
  if (p) styleAnchor.value = p.anchor || ''
  else if (styleCardById(v)) styleAnchor.value = ''
  styleChanged.value = true
  dirty.value = true
}
function onAnchor(v) { styleAnchor.value = v; styleChanged.value = true; dirty.value = true }
// 图像方言（§2）：自动判出来的摆在这里，手选覆盖后进项目文件；选「自动」= 不写这个键
const dialects = computed(() => dialectOptions(d.value))
const dialectNow = computed(() => dialectOf(d.value, { ...s.value, image_dialect: dialect.value }))
const dialectLabel = computed(() => {
  const now = dialectNow.value
  if (now.override) return dialectName(now.override)
  return now.detected ? t('自动（{name}）', { name: dialectName(now.detected) }) : t('自动')
})
const dialectWhat = computed(() => dialectHint(dialectNow.value.effective))
async function saveStyle() {
  if (await save()) { pickerOpen.value = false; styleChanged.value = true }
}
// 别的页面刷新了（任务完成等）而这里没改过：跟着刷新
watch(() => [s.value?.items, s.value?.picks], () => { if (!dirty.value) sync() })

const stamp = computed(() => d.value?.updated_at || s.value?.updated_at || 0)
const missing = computed(() => countRequests(items.value))
const statusOf = (it) => itemStatus(it)
const statusClass = (it) => ({ ok: statusOf(it) === 'done', bad: statusOf(it) === 'failed', warn: statusOf(it) === 'running' })
const errorOf = (it) => itemRow(it.id)?.error || itemRow(it.id)?.last_error || ''
// 多模型对比（docs/imagestudio-spec.md §4）：这一张自己指定了模型就摆出来，其余模板没有这个键
const modelOf = (it) => (itemRow(it.id)?.meta || it.meta || {}).model || ''
const touch = () => { dirty.value = true }

function body() {
  const out = { ...specBody(s.value), items: items.value, picks: picks.value, style: style.value, style_anchor: styleAnchor.value }
  if (dialect.value) out.image_dialect = dialect.value
  else delete out.image_dialect      // 「自动」= 项目文件里不写这个键（老项目零变化）
  return out
}
async function save() {
  saveError.value = ''
  return withBusy(async () => {
    try { await put(setUrl(m.value, s.value.id), body()); dirty.value = false; await refreshSet(); toast(t('已保存')); return true }
    catch (e) { saveError.value = e.message; return false }
  })
}
async function expand() {
  if (dirty.value && !(await save())) return
  await withBusy(async () => {
    try { await post(`${setUrl(m.value, s.value.id)}/expand`, {}); await refreshSet(); toast(t('清单已按模板展开（已出的图保留）')) }
    catch (e) { await notice(t('展开失败：{msg}', { msg: e.message })) }
  })
}
// 生图：先确认 N 次请求。「缺的」= 没有 current 的；「全部重出」= 全部（force）；单张 = only + force
async function generate({ only = null, force = false } = {}) {
  if (dirty.value && !(await save())) return
  const n = countRequests(items.value, { only, force })
  if (!n) { await notice(t('没有要出的图：每一张都已经有产物。想重新出先点「全部重出」或某一张的「重出」。')); return }
  const label = only ? t('重出 {id}', { id: only[0] }) : (force ? t('全部重出') : t('生成缺的'))
  const ok = await confirm(t('{label}：将发出 {n} 次生图请求（每张一次，失败的不拖累其他张）。继续？', { label, n }), { confirmLabel: label, localRole: 'image' })
  if (!ok) return
  const bodyReq = {}
  if (only) bodyReq.only = only
  if (force) bodyReq.force = true
  await runSetTask(`${setUrl(m.value, s.value.id)}/generate`, bodyReq, label)
}
async function adopt(it, a) {
  await withBusy(async () => {
    try { await post(`${setUrl(m.value, s.value.id)}/pick`, { item: it.id, attempt: a.key }); await refreshSet(); toast(t('{id} 已采用第 {n} 次', { id: it.id, n: a.index + 1 })) }
    catch (e) { await notice(e.message) }
  })
}
async function togglePick(it, ev) {
  const on = ev.target.checked
  const next = picks.value.filter(x => x !== it.id)
  if (on) next.push(it.id)
  picks.value = next
  await withBusy(async () => {
    try { await post(`${setUrl(m.value, s.value.id)}/pick`, { picks: next }); await refreshSet() }
    catch (e) { await notice(e.message) }
  })
}
</script>

<template>
  <div class="ws-content" v-if="s">
    <div class="ws-center" data-test="set-items">
      <div class="card" style="padding:10px 12px;margin-bottom:8px" data-test="style-row">
        <div class="row between">
          <div class="row" style="gap:8px">
            <span class="hint">{{ $t('风格') }}</span>
            <span class="pill" data-test="style-name">{{ styleName }}</span>
            <span class="hint">{{ $t('图像方言') }}</span>
            <span class="pill" data-test="dialect-name">{{ dialectLabel }}</span>
          </div>
          <button class="btn sm" data-test="style-change" @click="pickerOpen = !pickerOpen">{{ pickerOpen ? $t('收起') : $t('换风格') }}</button>
        </div>
        <p class="hint" data-test="dialect-what" style="margin-top:4px">{{ dialectWhat }}</p>
        <div v-if="pickerOpen" style="margin-top:8px" data-test="style-editor">
          <StyleCardPicker :model-value="style" :presets="presets" :fits="fitsKey" :anchor="styleAnchor"
                           select-test="style-select" @update:model-value="onStyle" @update:anchor="onAnchor" />
          <div class="field" style="margin-top:8px">
            <label>{{ $t('风格锚（会进每一张的提示词，可改）') }}</label>
            <textarea :value="styleAnchor" data-test="style-anchor" rows="2" style="width:100%" @input="onAnchor($event.target.value)"></textarea>
          </div>
          <div class="row">
            <label class="hint">{{ $t('图像方言') }}</label>
            <select v-model="dialect" data-test="dialect-select" @change="dirty = true">
              <option v-for="x in dialects" :key="x.id" :value="x.id">{{ $t(x.name) }}</option>
            </select>
            <button class="btn sm primary" data-test="style-save" :disabled="!!store.gen" @click="saveStyle">{{ $t('保存风格') }}</button>
          </div>
        </div>
        <div v-if="styleChanged" class="note" style="margin-top:8px" data-test="style-restage">{{ $t('换风格要重出才生效') }}</div>
      </div>
      <div class="row between" style="margin-bottom:8px">
        <div>
          <h3 style="font-size:16px">{{ $t('清单') }} <span class="hint">{{ $t('{n} 张', { n: items.length }) }}</span></h3>
          <p class="hint">{{ $t('每张一条提示词（模板展开，可改后保存再出）；同一张多次出图从不覆盖，「采用」选一次做 current；勾「挑选」决定导出带哪些（都不勾 = 全部）。') }}</p>
        </div>
        <div class="row">
          <button class="btn sm" data-test="items-expand" :disabled="!!store.gen" @click="expand">{{ items.length ? $t('重新展开清单') : $t('展开清单') }}</button>
          <button class="btn sm spend" data-test="items-generate" :disabled="!!store.gen || !items.length" @click="generate()">{{ $t('生成缺的') }}<span v-if="missing"> ({{ missing }})</span></button>
          <button class="btn sm spend" data-test="items-regenerate" :disabled="!!store.gen || !items.length" @click="generate({ force: true })">{{ $t('全部重出') }}</button>
          <button class="btn primary" :disabled="!dirty || !!store.gen" data-test="items-save" @click="save">{{ $t('保存') }}</button>
        </div>
      </div>
      <div v-if="saveError" class="note bad" style="margin-bottom:8px" data-test="items-error">{{ saveError }}</div>
      <div style="overflow-x:auto">
        <table class="tbl" style="min-width:900px" data-test="items-table">
          <thead><tr>
            <th style="width:36px">{{ $t('挑') }}</th><th style="width:96px">{{ $t('当前') }}</th><th style="width:120px">{{ $t('标签') }}</th>
            <th style="min-width:280px">{{ $t('提示词') }}</th><th style="width:90px">{{ $t('状态') }}</th><th style="min-width:200px">{{ $t('历次') }}</th>
          </tr></thead>
          <tbody>
            <tr v-for="it in items" :key="it.id" data-test="item-row">
              <td><input type="checkbox" :checked="picks.includes(it.id)" data-test="item-pick" :disabled="!hasCurrent(it)" @change="togglePick(it, $event)" /></td>
              <td>
                <img v-if="hasCurrent(it)" :src="itemImageUrl(m, s.id, it, stamp)" alt="" data-test="item-thumb" style="width:80px;aspect-ratio:1;object-fit:contain;border-radius:3px;border:1px solid var(--border);background:var(--panel3);display:block" />
                <div v-else class="hint" style="width:80px;aspect-ratio:1;display:flex;align-items:center;justify-content:center;border:1px dashed var(--border);border-radius:3px" :title="errorOf(it)">{{ errorOf(it) ? '⚠' : '—' }}</div>
                <button class="btn sm spend" style="margin-top:4px;width:80px" :disabled="!!store.gen" data-test="item-redo" @click="generate({ only: [it.id], force: true })">{{ hasCurrent(it) ? $t('重出这一张') : $t('出这一张') }}</button>
              </td>
              <td>
                <div class="mono small"><b>{{ it.id }}</b></div>
                <input v-model="it.label" @input="touch" style="width:100%;margin-top:4px" data-test="item-label" />
                <div class="hint mono" v-if="it.aspect_ratio">{{ it.aspect_ratio }}</div>
                <div class="pill" v-if="modelOf(it)" data-test="item-model" :title="$t('这一张用这个模型出（多模型对比）')">{{ modelOf(it) }}</div>
              </td>
              <td><textarea v-model="it.prompt" @input="touch" rows="4" style="width:100%" data-test="item-prompt"></textarea></td>
              <td>
                <span class="pill" :class="statusClass(it)" data-test="item-status">{{ $t(STATUS[statusOf(it)] || statusOf(it)) }}</span>
                <div v-if="errorOf(it)" class="hint bad small" style="margin-top:4px">{{ errorOf(it) }}</div>
              </td>
              <td>
                <div class="row" style="gap:6px" data-test="item-attempts">
                  <div v-for="a in itemAttempts(m, s.id, it, stamp)" :key="a.key" style="width:64px;text-align:center" data-test="item-attempt">
                    <img :src="a.url" alt="" style="width:64px;aspect-ratio:1;object-fit:contain;border-radius:3px;background:var(--panel3);display:block" :style="isCurrentAttempt(it, a) ? 'border:2px solid var(--accent)' : 'border:1px solid var(--border)'" />
                    <button class="btn sm" style="margin-top:2px;width:100%" :disabled="isCurrentAttempt(it, a)" data-test="item-adopt" @click="adopt(it, a)">{{ isCurrentAttempt(it, a) ? $t('当前') : $t('采用') }}</button>
                  </div>
                  <span v-if="!itemAttempts(m, s.id, it, stamp).length" class="hint">—</span>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!items.length" class="hint" style="margin-top:8px" data-test="items-empty">{{ $t('还没有清单。点「展开清单」按模板把每一张的提示词列出来（不花钱）。') }}</p>
    </div>
  </div>
</template>
