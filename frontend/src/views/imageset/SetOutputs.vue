<script setup>
// 产物（docs/imageset-spec.md §5）：「后处理」= POST /post（本机 Pillow，不花钱；可能是后台任务）→ 每张的产物按尺寸预览；
// 「导出 zip」= POST /export → 下载链接；导出历史来自 detail.exports。
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { post } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { setUrl, refreshSet, runSetTask, itemOutputs, hasCurrent, setExports, urlOf, progressOf } from '../../imageset.js'

const route = useRoute()
const m = computed(() => route.meta.module || 'sticker')
const s = computed(() => store.set)
const d = computed(() => store.setDetail)
const stamp = computed(() => d.value?.updated_at || s.value?.updated_at || 0)
const items = computed(() => s.value?.items || [])
const picks = computed(() => s.value?.picks || [])
const shown = computed(() => (picks.value.length ? items.value.filter(i => picks.value.includes(i.id)) : items.value))
const prog = computed(() => progressOf({ items: items.value, picks: picks.value }))
const postResult = ref(null)   // POST /post 的同步返回：{items: {I01: [{file, url, size, kind}]}} 或 [{item, outputs}]
const lastExport = ref(null)   // POST /export 的返回：{url, file, bytes, sha256}
const fmtKB = (n) => (n == null ? '' : n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.round(n / 1024) + ' KB')

function outputsOf(it) {
  const fromDetail = itemOutputs(m.value, s.value.id, it, stamp.value)
  if (fromDetail.length) return fromDetail
  const r = postResult.value
  if (!r) return []
  const list = Array.isArray(r.items) ? (r.items.find(x => x.item === it.id || x.id === it.id)?.outputs || []) : (r.items?.[it.id] || r.outputs?.[it.id] || r[it.id] || [])
  return list.map(o => ({ ...(typeof o === 'string' ? { file: o } : o), url: urlOf(m.value, s.value.id, 'out', o, stamp.value) }))
}
const sizeOf = (o) => o.size || (o.width && o.height ? `${o.width}×${o.height}` : '') || o.kind || ''
const exportsList = computed(() => setExports(m.value, s.value?.id))
const globalOutputs = computed(() => {
  const list = d.value?.outputs || postResult.value?.extra || postResult.value?.global || []
  return list.map(o => ({ ...(typeof o === 'string' ? { file: o } : o), url: urlOf(m.value, s.value.id, 'out', o, stamp.value) }))
})

async function runPost() {
  const r = await runSetTask(`${setUrl(m.value, s.value.id)}/post`, {}, t('后处理'))
  if (r && !r.task_id && r.status !== 'failed') postResult.value = r.result || r
  else if (r?.result) postResult.value = r.result
}
async function runExport() {
  await withBusy(async () => {
    try {
      const r = await post(`${setUrl(m.value, s.value.id)}/export`, {})
      lastExport.value = { ...r, url: r.url || r.download_url || urlOf(m.value, s.value.id, 'export', r) }
      await refreshSet()
      toast(t('已打包 {name}', { name: r.file || r.name || 'zip' }))
    } catch (e) { await notice(t('导出失败：{msg}', { msg: e.message })) }
  })
}
</script>

<template>
  <div class="ws-content" v-if="s">
    <div class="ws-center" style="max-width:960px" data-test="set-outputs">
      <div class="row between" style="margin-bottom:8px">
        <div>
          <h3 style="font-size:16px">{{ $t('产物') }}</h3>
          <p class="hint">{{ $t('后处理在本机跑（不花钱）：白底转透明 / 裁切到目标尺寸 / 叠字；导出按模板目录结构打成 zip。挑选了的只带挑选的。') }}</p>
        </div>
        <div class="row">
          <button class="btn" data-test="outputs-post" :disabled="!!store.gen || !prog.done" @click="runPost">{{ $t('后处理') }}</button>
          <button class="btn primary" data-test="outputs-export" :disabled="!!store.gen || !prog.done" @click="runExport">{{ $t('导出 zip') }}</button>
        </div>
      </div>
      <p v-if="!prog.done" class="hint" data-test="outputs-nothing">{{ $t('还没有出过图，先去「清单」出图。') }}</p>
      <div v-if="lastExport" class="card" data-test="export-card">
        <div class="row between">
          <div><b>{{ $t('已打包') }}</b><div class="hint mono">{{ lastExport.file || lastExport.name || '' }}<span v-if="lastExport.bytes"> · {{ fmtKB(lastExport.bytes) }}</span><span v-if="lastExport.sha256"> · sha256 {{ String(lastExport.sha256).slice(0, 12) }}</span></div></div>
          <a class="btn" :href="lastExport.url" download data-test="export-download" style="text-decoration:none">{{ $t('下载 zip') }}</a>
        </div>
      </div>
      <div v-if="globalOutputs.length" class="card">
        <h3>{{ $t('整套产物（横幅 / 图标 / 九宫格）') }}</h3>
        <div class="media">
          <div v-for="o in globalOutputs" :key="o.url"><div class="cap"><span class="mono">{{ o.file || o.kind }}</span><span>{{ sizeOf(o) }}</span></div><img :src="o.url" alt="" /></div>
        </div>
      </div>
      <div v-for="it in shown" :key="it.id" class="card" data-test="output-item">
        <div class="row between"><h3>{{ it.id }} · {{ it.label }}</h3><span class="pill" v-if="picks.includes(it.id)">{{ $t('已挑选') }}</span></div>
        <div v-if="!hasCurrent(it)" class="hint">{{ $t('还没有出图') }}</div>
        <div v-else-if="!outputsOf(it).length" class="hint">{{ $t('还没有后处理产物——点上面「后处理」。') }}</div>
        <div v-else class="media">
          <div v-for="o in outputsOf(it)" :key="o.url" data-test="output-file">
            <div class="cap"><span class="mono">{{ o.file ? String(o.file).split(/[\\/]/).pop() : o.kind }}</span><span>{{ sizeOf(o) }}<span v-if="o.bytes"> · {{ fmtKB(o.bytes) }}</span></span></div>
            <img :src="o.url" alt="" style="background:repeating-conic-gradient(var(--panel3) 0 25%, var(--panel) 0 50%) 0 0 / 16px 16px" />
          </div>
        </div>
      </div>
      <div class="card" data-test="export-history">
        <h3>{{ $t('导出历史') }}</h3>
        <p class="hint" v-if="!exportsList.length">{{ $t('还没有导出过。') }}</p>
        <div v-for="e in exportsList" :key="e.url" class="row between small" style="padding:4px 0;border-top:1px solid var(--border-soft)">
          <span class="mono">{{ e.file ? String(e.file).split(/[\\/]/).pop() : e.url }}<span v-if="e.created_at" class="hint"> · {{ e.created_at }}</span><span v-if="e.bytes" class="hint"> · {{ fmtKB(e.bytes) }}</span></span>
          <a class="btn sm" :href="e.url" download style="text-decoration:none">{{ $t('下载') }}</a>
        </div>
      </div>
    </div>
  </div>
</template>
