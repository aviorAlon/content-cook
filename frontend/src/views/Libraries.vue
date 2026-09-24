<script setup>
import { onMounted, ref } from 'vue'
import { get, put } from '../api.js'
import { withBusy } from '../store.js'
import { confirm, custom, notice, prompt } from '../ui.js'
import { t } from '../i18n.js'

// 镜头语言库 / 风格骨架库：所有项目共用。逐条编辑（弹出该条的 JSON），整份保存；后端写盘前校验一致性。
const cam = ref(null)
const style = ref(null)
const status = ref('')

async function load() {
  try { [cam.value, style.value] = await Promise.all([get('/api/config/camera-library'), get('/api/config/style-library')]) }
  catch (e) { status.value = e.message }
}
onMounted(load)

async function saveDoc(which) {
  const path = which === 'cam' ? '/api/config/camera-library' : '/api/config/style-library'
  const doc = which === 'cam' ? cam.value : style.value
  await put(path, { data: doc })
}

// 通用：编辑"带 id 的对象数组"里的一条（或新增）。label 传中文原文，这里再 t()
async function editItem(which, key, item, template, label) {
  const doc = which === 'cam' ? cam.value : style.value
  const list = doc[key] || (doc[key] = [])
  const edited = await custom({ view: 'json', title: item ? t('编辑{label}：{id}', { label: t(label), id: item.id }) : t('新增{label}', { label: t(label) }), value: item || template })
  if (!edited) return
  if (!edited.id) { await notice(t('id 必填')); return }
  const idx = item ? list.findIndex(x => x.id === item.id) : -1
  if (!item && list.some(x => x.id === edited.id)) { await notice(t('id {id} 已经存在', { id: edited.id })); return }
  const backup = JSON.parse(JSON.stringify(list))
  if (idx >= 0) list[idx] = edited; else list.push(edited)
  try { await withBusy(() => saveDoc(which)); status.value = t('已保存，所有项目立刻生效') }
  catch (e) { doc[key] = backup; await notice(t('保存失败：{err}', { err: e.message })) }
}
async function removeItem(which, key, item, label) {
  if (!await confirm(t('删除{label}「{id}」？如果有 profile 还在引用它，保存时会被拒绝。', { label: t(label), id: item.id }), { confirmLabel: t('删除'), danger: true })) return
  const doc = which === 'cam' ? cam.value : style.value
  const backup = JSON.parse(JSON.stringify(doc[key]))
  doc[key] = doc[key].filter(x => x.id !== item.id)
  try { await withBusy(() => saveDoc(which)); status.value = t('已删除') }
  catch (e) { doc[key] = backup; await notice(t('删除失败：{err}', { err: e.message })) }
}
// profile 类：按名字做 key 的字典
async function editProfile(which, key, name, template, label) {
  const doc = which === 'cam' ? cam.value : style.value
  const map = doc[key] || (doc[key] = {})
  let target = name
  if (!name) {
    target = await prompt(t('新 {label} 的名称（英文 slug）', { label: t(label) }), { multiline: false, confirmLabel: t('继续') })
    if (!target?.trim()) return
    target = target.trim()
    if (map[target]) { await notice(t('{label} {name} 已经存在', { label: t(label), name: target })); return }
  }
  const edited = await custom({ view: 'json', title: name ? t('编辑{label}：{id}', { label: t(label), id: target }) : t('新增{label}：{id}', { label: t(label), id: target }), value: map[target] || template })
  if (!edited) return
  const backup = JSON.parse(JSON.stringify(map))
  map[target] = edited
  try { await withBusy(() => saveDoc(which)); status.value = t('已保存') }
  catch (e) { doc[key] = backup; await notice(t('保存失败：{err}', { err: e.message })) }
}
async function removeProfile(which, key, name, label) {
  if (!await confirm(t('删除{label}「{name}」？', { label: t(label), name }), { confirmLabel: t('删除'), danger: true })) return
  const doc = which === 'cam' ? cam.value : style.value
  const backup = JSON.parse(JSON.stringify(doc[key]))
  delete doc[key][name]
  try { await withBusy(() => saveDoc(which)); status.value = t('已删除') }
  catch (e) { doc[key] = backup; await notice(t('删除失败：{err}', { err: e.message })) }
}
// 风格骨架 + 检查清单按 profile 名成对存在，一起编辑
async function editSkeleton(name) {
  const doc = style.value
  const sk = doc.frame_style_skeletons || (doc.frame_style_skeletons = {})
  const cl = doc.scene_anchor_checklists || (doc.scene_anchor_checklists = {})
  let target = name
  if (!name) {
    target = await prompt(t('新风格骨架 profile 的名称（要跟镜头语言库的 profile 同名，起草才会用上）'), { multiline: false, confirmLabel: t('继续') })
    if (!target?.trim()) return
    target = target.trim()
    if (sk[target] || cl[target]) { await notice(t('profile {name} 已经存在', { name: target })); return }
  }
  const edited = await custom({ view: 'json', title: name ? t('编辑风格骨架：{name}', { name: target }) : t('新增风格骨架：{name}', { name: target }),
    value: { skeleton: sk[target] || { '用于': '', structure_cn: [], guardrail_cn: '', guardrail_en: '' }, checklist: cl[target] || [] } })
  if (!edited) return
  if (!edited.skeleton || typeof edited.skeleton !== 'object' || Array.isArray(edited.skeleton)) { await notice(t('skeleton 必须是对象')); return }
  if (!Array.isArray(edited.checklist)) { await notice(t('checklist 必须是数组')); return }
  const bs = JSON.parse(JSON.stringify(sk)), bc = JSON.parse(JSON.stringify(cl))
  sk[target] = edited.skeleton; cl[target] = edited.checklist
  try { await withBusy(() => saveDoc('style')); status.value = t('已保存') }
  catch (e) { doc.frame_style_skeletons = bs; doc.scene_anchor_checklists = bc; await notice(t('保存失败：{err}', { err: e.message })) }
}
async function removeSkeleton(name) {
  if (!await confirm(t('删除风格骨架「{name}」？骨架和检查清单一起删。', { name }), { confirmLabel: t('删除'), danger: true })) return
  const doc = style.value
  const bs = JSON.parse(JSON.stringify(doc.frame_style_skeletons || {})), bc = JSON.parse(JSON.stringify(doc.scene_anchor_checklists || {}))
  delete doc.frame_style_skeletons[name]; delete doc.scene_anchor_checklists[name]
  try { await withBusy(() => saveDoc('style')); status.value = t('已删除') }
  catch (e) { doc.frame_style_skeletons = bs; doc.scene_anchor_checklists = bc; await notice(t('删除失败：{err}', { err: e.message })) }
}
async function editWhole(which) {
  const doc = which === 'cam' ? cam.value : style.value
  const edited = await custom({ view: 'json', title: which === 'cam' ? t('整份镜头语言库') : t('整份风格骨架库'), value: doc, tall: true })
  if (!edited) return
  try {
    await withBusy(async () => { await put(which === 'cam' ? '/api/config/camera-library' : '/api/config/style-library', { data: edited }) })
    await load(); status.value = t('已保存整份')
  } catch (e) { await notice(t('保存失败：{err}', { err: e.message })) }
}

// '用于' 是库文件里的数据字段名，不是界面文案
const TPL = {
  shot: { id: '', cn: '', cat: 'motion', risk_source: 'inferred', prompt_cn: '', prompt_en: '', function: [], risk: 'low', note: '' },
  comboWithin: { id: '', cn: '', parts: [], prompt_cn: '', prompt_en: '', when: '', risk: 'mid' },
  comboSeq: { id: '', cn: '', seq: [], when: '' },
  neg: { id: '', cn: '', en: '', tier: 'common', note: '' },
  camProfile: { '用于': '', allow: [], combos: [], recommended_sequences: [], max_moves_per_episode: null, why: '' },
}
</script>

<template>
  <div class="page">
    <div class="row between" style="margin-bottom:8px">
      <h2 style="font-size:20px">{{ $t('镜头库与风格库') }}</h2>
      <span class="status">{{ status }}</span>
    </div>
    <p class="hint" style="margin-bottom:12px">{{ $t('所有项目共用：镜头语言库决定镜头编辑器里"运镜"能选什么、每种镜头风格允许哪些运镜；风格骨架库决定 AI 起草时的画风指导和负向词。改一条存一条，立刻对所有项目生效。') }}</p>
    <div v-if="cam" class="card">
      <div class="row between"><h3>{{ $t('镜头语言库') }}</h3><button class="btn sm" @click="editWhole('cam')">{{ $t('兜底：编辑整份 JSON') }}</button></div>
      <details class="fold" open><summary>{{ $t('镜头（{n}）', { n: (cam.shots || []).length }) }}</summary>
        <button class="btn sm" style="margin:6px 0" @click="editItem('cam', 'shots', null, TPL.shot, '镜头')">{{ $t('+ 新增镜头') }}</button>
        <table class="tbl"><tr><th>id</th><th>{{ $t('名称') }}</th><th>{{ $t('类别') }}</th><th>{{ $t('风险') }}</th><th></th></tr>
          <tr v-for="s in cam.shots" :key="s.id"><td class="mono">{{ s.id }}</td><td>{{ s.cn }}</td><td>{{ s.cat }}</td><td>{{ s.risk }}</td>
            <td style="white-space:nowrap"><button class="btn sm" @click="editItem('cam', 'shots', s, null, '镜头')">{{ $t('改') }}</button> <button class="btn sm" @click="removeItem('cam', 'shots', s, '镜头')">{{ $t('删') }}</button></td></tr>
        </table></details>
      <details class="fold"><summary>{{ $t('组合内运镜（{n}）', { n: (cam.combos_within_shot || []).length }) }}</summary>
        <button class="btn sm" style="margin:6px 0" @click="editItem('cam', 'combos_within_shot', null, TPL.comboWithin, '组合')">{{ $t('+ 新增') }}</button>
        <table class="tbl"><tr v-for="c in cam.combos_within_shot" :key="c.id"><td class="mono">{{ c.id }}</td><td>{{ c.cn }}</td><td>{{ c.risk }}</td>
          <td style="white-space:nowrap"><button class="btn sm" @click="editItem('cam', 'combos_within_shot', c, null, '组合')">{{ $t('改') }}</button> <button class="btn sm" @click="removeItem('cam', 'combos_within_shot', c, '组合')">{{ $t('删') }}</button></td></tr></table></details>
      <details class="fold"><summary>{{ $t('镜头组（{n}）', { n: (cam.combos_sequence || []).length }) }}</summary>
        <button class="btn sm" style="margin:6px 0" @click="editItem('cam', 'combos_sequence', null, TPL.comboSeq, '镜头组')">{{ $t('+ 新增') }}</button>
        <table class="tbl"><tr v-for="c in cam.combos_sequence" :key="c.id"><td class="mono">{{ c.id }}</td><td>{{ c.cn }}</td><td>{{ $t('{n} 步', { n: (c.seq || []).length }) }}</td>
          <td style="white-space:nowrap"><button class="btn sm" @click="editItem('cam', 'combos_sequence', c, null, '镜头组')">{{ $t('改') }}</button> <button class="btn sm" @click="removeItem('cam', 'combos_sequence', c, '镜头组')">{{ $t('删') }}</button></td></tr></table></details>
      <details class="fold"><summary>{{ $t('镜头风格 profile（{n}）', { n: Object.keys(cam.profiles || {}).length }) }}</summary>
        <button class="btn sm" style="margin:6px 0" @click="editProfile('cam', 'profiles', null, TPL.camProfile, 'profile')">{{ $t('+ 新增') }}</button>
        <table class="tbl"><tr><th>id</th><th>{{ $t('说明') }}</th><th>{{ $t('运镜') }}</th><th>{{ $t('出处 / 置信度') }}</th><th></th></tr>
          <tr v-for="(p, name) in cam.profiles" :key="name"><td class="mono">{{ name }}</td><td class="hint">{{ p['用于'] }}</td><td>{{ $t('{n} 种运镜', { n: (p.allow || []).length }) }}</td>
          <td class="small" data-test="profile-provenance" :title="(p.refs || []).join('')">{{ p.confidence ? $t('置信度 {c}', { c: $t(p.confidence) }) : $t('未标注') }}<div v-if="p.basis" class="hint">{{ p.basis }}</div></td>
          <td style="white-space:nowrap"><button class="btn sm" @click="editProfile('cam', 'profiles', name, null, 'profile')">{{ $t('改') }}</button> <button class="btn sm" @click="removeProfile('cam', 'profiles', name, 'profile')">{{ $t('删') }}</button></td></tr></table></details>
    </div>
    <div v-if="style" class="card">
      <div class="row between"><h3>{{ $t('风格骨架库') }}</h3><button class="btn sm" @click="editWhole('style')">{{ $t('兜底：编辑整份 JSON') }}</button></div>
      <details class="fold" open><summary>{{ $t('负向词池（{n}）', { n: (style.neg_pool || []).length }) }}</summary>
        <button class="btn sm" style="margin:6px 0" @click="editItem('style', 'neg_pool', null, TPL.neg, '负向词')">{{ $t('+ 新增') }}</button>
        <table class="tbl"><tr v-for="n in style.neg_pool" :key="n.id"><td class="mono">{{ n.id }}</td><td>{{ n.cn }}</td><td>{{ n.tier }}</td>
          <td style="white-space:nowrap"><button class="btn sm" @click="editItem('style', 'neg_pool', n, null, '负向词')">{{ $t('改') }}</button> <button class="btn sm" @click="removeItem('style', 'neg_pool', n, '负向词')">{{ $t('删') }}</button></td></tr></table></details>
      <details class="fold"><summary>{{ $t('风格骨架 + 检查清单（{n} 个 profile）', { n: Object.keys(style.frame_style_skeletons || {}).length }) }}</summary>
        <p class="hint" style="margin:6px 0">{{ $t('按 profile 名成对存在（骨架是一段结构化画风文案，清单是场景锚点检查项），一起编辑、一起删；名字要跟镜头风格 profile 同名，起草才会用上。') }}</p>
        <button class="btn sm" style="margin:6px 0" @click="editSkeleton(null)">{{ $t('+ 新增') }}</button>
        <table class="tbl"><tr v-for="(sk, name) in style.frame_style_skeletons" :key="name"><td class="mono">{{ name }}</td><td class="hint">{{ sk['用于'] }}</td><td>{{ $t('{n} 条检查', { n: (style.scene_anchor_checklists?.[name] || []).length }) }}</td>
          <td style="white-space:nowrap"><button class="btn sm" @click="editSkeleton(name)">{{ $t('改') }}</button> <button class="btn sm" @click="removeSkeleton(name)">{{ $t('删') }}</button></td></tr></table></details>
    </div>
    <div v-if="!cam && !style" class="empty">{{ $t('读取中…') }}</div>
  </div>
</template>
