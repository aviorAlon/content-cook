<script setup>
// 交付面遮挡区编辑（DESIGN §27）：预设下拉 + 「自定义」逐块列表（百分比填写，保存时换成 0–1 比例）。
// 只管表单，不管保存：emit('save', { overlay_preset, overlay_regions })，父组件走 post-layers PUT。
import { computed, reactive, ref, watch } from 'vue'
import { t } from '../i18n.js'
import OverlayMask from './OverlayMask.vue'

const props = defineProps({
  // 后端 post-layers.overlays：{ preset, custom, regions, presets: [{id, label}] }
  overlays: { type: Object, default: () => ({ preset: 'none', custom: false, regions: [], presets: [] }) },
})
const emit = defineEmits(['save'])

const mode = ref('none')          // 预设 id 或 'custom'
const rows = reactive([])         // 自定义区域，百分比
function pct(v) { return Math.round(Number(v || 0) * 1000) / 10 }
function sync() {
  const o = props.overlays || {}
  mode.value = o.custom ? 'custom' : (o.preset || 'none')
  rows.splice(0, rows.length, ...(o.custom ? (o.regions || []).map(r => ({ label: r.label || '', x: pct(r.x), y: pct(r.y), w: pct(r.w), h: pct(r.h) })) : []))
}
watch(() => props.overlays, sync, { immediate: true, deep: true })

const presets = computed(() => props.overlays?.presets || [])
function addRow() { rows.push({ label: '', x: 0, y: 80, w: 100, h: 20 }) }
function removeRow(i) { rows.splice(i, 1) }
function switchMode(next) {
  mode.value = next
  // 从预设切到自定义：把预设的块拷进来当起点，免得从空白画
  if (next === 'custom' && !rows.length) {
    for (const r of (props.overlays?.regions || [])) rows.push({ label: r.label || '', x: pct(r.x), y: pct(r.y), w: pct(r.w), h: pct(r.h) })
  }
}
// 预览用的区域：自定义模式看表单里的，预设模式看后端给的（后端按预设算）；切了预设但还没保存时按本地表算不到，先显示当前生效的
const previewRegions = computed(() => mode.value === 'custom'
  ? rows.filter(r => Number(r.w) > 0 && Number(r.h) > 0).map(r => ({ label: r.label || t('遮挡区'), x: Number(r.x) / 100, y: Number(r.y) / 100, w: Number(r.w) / 100, h: Number(r.h) / 100 }))
  : (mode.value === (props.overlays?.preset || 'none') && !props.overlays?.custom ? (props.overlays?.regions || []) : []))
const problems = computed(() => {
  const out = []
  if (mode.value !== 'custom') return out
  rows.forEach((r, i) => {
    const nums = ['x', 'y', 'w', 'h'].map(k => Number(r[k]))
    if (nums.some(n => !Number.isFinite(n) || n < 0 || n > 100)) out.push(t('第 {n} 块：数值要在 0–100 之间', { n: i + 1 }))
    else if (Number(r.w) <= 0 || Number(r.h) <= 0) out.push(t('第 {n} 块：宽高要大于 0', { n: i + 1 }))
    else if (Number(r.x) + Number(r.w) > 100 + 1e-6 || Number(r.y) + Number(r.h) > 100 + 1e-6) out.push(t('第 {n} 块：超出了画面', { n: i + 1 }))
  })
  return out
})
function save() {
  if (mode.value === 'custom') {
    emit('save', { overlay_preset: '', overlay_regions: rows.map(r => ({ label: (r.label || '').trim() || t('遮挡区'), x: Number(r.x) / 100, y: Number(r.y) / 100, w: Number(r.w) / 100, h: Number(r.h) / 100 })) })
  } else {
    emit('save', { overlay_preset: mode.value === 'none' ? '' : mode.value, overlay_regions: [] })
  }
}
</script>

<template>
  <div class="card" data-test="overlay-card">
    <div class="row between"><h3>{{ t('交付面遮挡区') }}</h3><button class="btn sm primary" :disabled="problems.length > 0" @click="save">{{ t('保存遮挡区') }}</button></div>
    <div class="hint" style="margin-bottom:8px">{{ t('平台的状态栏、右侧互动栏、底部文案区会压住画面。配好之后，首帧预览会叠出这些区域，可读文字和字幕落进去会被检查点名。不进任何付费请求，制作中也能改。') }}</div>
    <div class="row" style="align-items:flex-start;gap:16px">
      <div style="flex:1;min-width:260px">
        <div class="field"><label>{{ t('布局') }}</label>
          <select :value="mode" data-test="overlay-mode" @change="switchMode($event.target.value)">
            <option v-for="p in presets" :key="p.id" :value="p.id">{{ p.label }}</option>
            <option value="custom">{{ t('自定义') }}</option>
          </select>
        </div>
        <template v-if="mode === 'custom'">
          <table class="tbl" v-if="rows.length">
            <tr><th>{{ t('名称') }}</th><th>x %</th><th>y %</th><th>{{ t('宽 %') }}</th><th>{{ t('高 %') }}</th><th></th></tr>
            <tr v-for="(r, i) in rows" :key="i" data-test="overlay-row">
              <td><input v-model="r.label" :placeholder="t('如：底部文案区')" style="width:100%" /></td>
              <td style="width:64px"><input v-model.number="r.x" type="number" min="0" max="100" step="1" style="width:100%" /></td>
              <td style="width:64px"><input v-model.number="r.y" type="number" min="0" max="100" step="1" style="width:100%" /></td>
              <td style="width:64px"><input v-model.number="r.w" type="number" min="0" max="100" step="1" style="width:100%" /></td>
              <td style="width:64px"><input v-model.number="r.h" type="number" min="0" max="100" step="1" style="width:100%" /></td>
              <td style="width:40px"><button class="btn sm" @click="removeRow(i)">{{ t('删') }}</button></td>
            </tr>
          </table>
          <div v-else class="hint">{{ t('还没有自定义区域') }}</div>
          <button class="btn sm" style="margin-top:6px" @click="addRow">{{ t('+ 加一块') }}</button>
          <div v-for="(p, i) in problems" :key="i" class="bad small" style="margin-top:4px">{{ p }}</div>
        </template>
      </div>
      <div data-test="overlay-preview" style="width:135px;flex-shrink:0">
        <div class="hint" style="margin-bottom:4px">{{ t('预览（9:16）') }}</div>
        <div style="position:relative;width:135px;height:240px;border-radius:6px;overflow:hidden;background:linear-gradient(160deg,#3b3a45,#1b1a20 60%,#0d0c10)">
          <OverlayMask :regions="previewRegions" />
        </div>
      </div>
    </div>
  </div>
</template>
