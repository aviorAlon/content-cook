<script setup>
// 角色与风格：风格锚 / 负面词、角色表（key / 名字 / 英文名 / 外形块 / 定妆图上传）、场景锚表。保存走 PUT 整本；
// 定妆图 / 场景锚图走 POST /assets/{kind}/{key}（base64，同 Cast 页的上传），先保存再上传（后端按 key 找）。
import { computed, reactive, ref, watch } from 'vue'
import { put, post, fileToBase64 } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { pb, refreshBook, STYLE_PRESETS } from '../../picturebook.js'
import { loadStyleCards, styleCardById, cardName, dialectOf, dialectName, dialectHint, dialectOptions } from '../../stylecards.js'
import StyleCardPicker from '../../components/StyleCardPicker.vue'

const b = computed(() => store.book)
const bd = computed(() => store.bookDetail)
const form = reactive({ style_anchor: '', negative: '', style_card: '', image_dialect: '', characters: [], scenes: [] })
const dirty = ref(false)
const error = ref('')
const styleSel = ref('custom')   // 挑选器里的当前项：卡 id / 本模块预设 id / custom
function sync() {
  form.style_anchor = b.value?.style_anchor || ''
  form.negative = b.value?.negative || ''
  form.style_card = b.value?.style_card || ''
  form.image_dialect = dialectOf(bd.value, b.value).override
  styleSel.value = form.style_card || 'custom'
  form.characters = Object.entries(b.value?.characters || {}).map(([key, c]) => ({ key, name: c.name || '', name_en: c.name_en || '', block: c.block || '', asset: c.asset || null }))
  form.scenes = Object.entries(b.value?.scenes || {}).map(([key, c]) => ({ key, name: c.name || '', block: c.block || '', asset: c.asset || null }))
  dirty.value = false
}
watch(() => b.value?.id, sync, { immediate: true })
watch(() => [b.value?.characters, b.value?.scenes], () => { if (!dirty.value) sync() })
const touch = () => { dirty.value = true }
const addChar = () => { form.characters.push({ key: '', name: '', name_en: '', block: '', asset: null }); touch() }
const addScene = () => { form.scenes.push({ key: '', name: '', block: '', asset: null }); touch() }
const slug = (s) => String(s || '').trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '')

// ---- 出图风格卡（docs/image-style-cards.md §4）：`style_card` 填了就用卡的锚句，空着 = 现在的行为（风格锚自写）。
// 挑选器里的选择既可能是卡 id，也可能是本模块自带的四个预设或 custom——只有卡 id 进 `style_card`。
loadStyleCards()
const styleCardName = computed(() => {
  const c = styleCardById(form.style_card)
  return c ? cardName(c) : t('自写风格锚')
})
function onStyle(v) {
  styleSel.value = v
  dirty.value = true
  const card = styleCardById(v)
  if (card) { form.style_card = card.id; return }
  form.style_card = ''
  const p = STYLE_PRESETS.find(x => x.id === v)
  if (p) { form.style_anchor = p.anchor; form.negative = p.negative }
}
function onAnchor(v) { styleSel.value = 'custom'; form.style_card = ''; form.style_anchor = v; dirty.value = true }
// 图像方言（§2）：自动判出来的摆出来，手选覆盖后进项目文件；「自动」= 不写这个键
const dialects = computed(() => dialectOptions(bd.value))
const dialectNow = computed(() => dialectOf(bd.value, { image_dialect: form.image_dialect }))
const dialectLabel = computed(() => {
  const now = dialectNow.value
  if (now.override) return dialectName(now.override)
  return now.detected ? t('自动（{name}）', { name: dialectName(now.detected) }) : t('自动')
})
const dialectWhat = computed(() => dialectHint(dialectNow.value.effective))

function body() {
  const { output_root: _o, project_root: _r, ...spec } = b.value
  const characters = {}, scenes = {}
  for (const c of form.characters) { const key = slug(c.key) || slug(c.name_en) || slug(c.name); if (key) characters[key] = { name: c.name.trim(), name_en: c.name_en.trim(), block: c.block.trim(), asset: c.asset || null } }
  for (const s of form.scenes) { const key = slug(s.key) || slug(s.name); if (key) scenes[key] = { name: s.name.trim(), block: s.block.trim(), asset: s.asset || null } }
  const out = { ...spec, style_anchor: form.style_anchor.trim(), negative: form.negative.trim(), characters, scenes }
  if (form.style_card) out.style_card = form.style_card
  else delete out.style_card
  if (form.image_dialect) out.image_dialect = form.image_dialect
  else delete out.image_dialect
  return out
}
async function save() {
  error.value = ''
  return withBusy(async () => {
    try { await put(pb(b.value.id), body()); dirty.value = false; await refreshBook(); toast(t('已保存')); return true }
    catch (e) { error.value = e.message; return false }
  })
}
// 上传：kind = character | scene；后端不覆盖已有文件，所以换图要先删旧的（这里只提示）
async function upload(ev, kind, row) {
  const file = ev.target.files?.[0]; ev.target.value = ''
  if (!file) return
  const key = kind === 'character' ? (slug(row.key) || slug(row.name_en) || slug(row.name)) : (slug(row.key) || slug(row.name))
  if (!key) { await notice(t('先填 key 或名字，再上传')); return }
  if (dirty.value && !(await save())) return
  await withBusy(async () => {
    try {
      const data_base64 = await fileToBase64(file)
      const r = await post(`${pb(b.value.id)}/assets/${kind}/${encodeURIComponent(key)}`, { filename: file.name, data_base64 })
      await refreshBook()
      toast(t('已上传 {name}', { name: r?.file || file.name }))
    } catch (e) { await notice(t('上传失败：{msg}', { msg: e.message })) }
  })
}
// 定妆 / 场景锚图预览：项目文件里存相对路径 content/assets/<ID>/<表>/<文件名>，流接口按文件名取
const assetUrl = (row, kind) => row.asset ? `${pb(b.value.id)}/artifact/asset/${kind}/${encodeURIComponent(String(row.asset).split(/[\\/]/).pop())}` : null
</script>

<template>
  <div class="ws-content" v-if="b">
    <div class="ws-center" style="max-width:900px" data-test="book-characters">
      <div class="row between" style="margin-bottom:8px">
        <h3 style="font-size:16px">{{ $t('角色与风格') }}</h3>
        <button class="btn primary" :disabled="!dirty" data-test="chars-save" @click="save">{{ $t('保存') }}</button>
      </div>
      <div v-if="error" class="note bad" style="margin-bottom:8px">{{ error }}</div>
      <div class="card">
        <div class="row between" style="margin-bottom:4px">
          <h3 style="margin:0">{{ $t('风格') }}</h3>
          <span class="pill" data-test="book-style-name">{{ styleCardName }}</span>
        </div>
        <p class="hint">{{ $t('挑一张风格卡，全书插画用它的锚句；不挑就用下面自写的风格锚（老书零变化）。') }}</p>
        <StyleCardPicker :model-value="styleSel" :presets="STYLE_PRESETS" fits="picturebook" :anchor="form.style_anchor"
                         select-test="book-style-select" @update:model-value="onStyle" @update:anchor="onAnchor" />
        <h3 style="margin-top:10px">{{ $t('风格锚') }}</h3>
        <p class="hint">{{ $t('每一页插画提示词的第一句：画风、形状语言、色板、纸感。全书固定，不要在页里重复写。') }}</p>
        <div class="field"><textarea v-model="form.style_anchor" @input="touch" data-test="style-anchor" rows="3"></textarea></div>
        <div class="field"><label>{{ $t('负面词') }}</label><input v-model="form.negative" @input="touch" data-test="style-negative" /></div>
        <div class="row">
          <label class="hint">{{ $t('图像方言') }}</label>
          <span class="pill" data-test="book-dialect-name">{{ dialectLabel }}</span>
          <select v-model="form.image_dialect" data-test="book-dialect-select" @change="touch">
            <option v-for="x in dialects" :key="x.id" :value="x.id">{{ $t(x.name) }}</option>
          </select>
        </div>
        <p class="hint" data-test="book-dialect-what" style="margin-top:4px">{{ dialectWhat }}</p>
      </div>
      <div class="card">
        <div class="row between"><h3>{{ $t('角色表') }}</h3><button class="btn sm" data-test="char-add" @click="addChar">{{ $t('+ 角色') }}</button></div>
        <p class="hint">{{ $t('外形块写清颜色 / 形状 / 标志物（橘红色小狐狸，白色尾巴尖，浅绿色小背心）；定妆图会作为参考图进每一页的生图请求。分页表按 key 点名出场角色。') }}</p>
        <table class="tbl" style="margin-top:8px">
          <thead><tr><th style="width:90px">key</th><th style="width:110px">{{ $t('名字') }}</th><th style="width:120px">{{ $t('英文名') }}</th><th>{{ $t('外形块') }}</th><th style="width:150px">{{ $t('定妆图') }}</th><th style="width:40px"></th></tr></thead>
          <tbody>
            <tr v-for="(c, i) in form.characters" :key="i" data-test="char-row">
              <td><input v-model="c.key" @input="touch" class="mono" style="width:100%" placeholder="fox" /></td>
              <td><input v-model="c.name" @input="touch" style="width:100%" :placeholder="$t('小狐狸')" /></td>
              <td><input v-model="c.name_en" @input="touch" style="width:100%" placeholder="Little Fox" /></td>
              <td><textarea v-model="c.block" @input="touch" rows="2" style="width:100%"></textarea></td>
              <td>
                <div class="hint mono small" v-if="c.asset">{{ String(c.asset).split(/[\\/]/).pop() }}</div>
                <img v-if="assetUrl(c, 'character')" :src="assetUrl(c, 'character')" alt="" data-test="char-asset" style="width:60px;aspect-ratio:3/4;object-fit:cover;border-radius:3px;border:1px solid var(--border);display:block;margin:4px 0" />
                <label class="btn sm" style="cursor:pointer">{{ c.asset ? $t('换图') : $t('上传定妆图') }}<input type="file" accept="image/*" style="display:none" @change="upload($event, 'character', c)" /></label>
              </td>
              <td><button class="btn sm" :title="$t('删除')" @click="form.characters.splice(i, 1); touch()">✕</button></td>
            </tr>
          </tbody>
        </table>
        <p v-if="!form.characters.length" class="hint" style="margin-top:6px">{{ $t('还没有角色。') }}</p>
      </div>
      <div class="card">
        <div class="row between"><h3>{{ $t('场景锚表') }}</h3><button class="btn sm" data-test="scene-add" @click="addScene">{{ $t('+ 场景') }}</button></div>
        <p class="hint">{{ $t('反复出现的地点写成场景锚（木屋，暖黄灯光，圆窗），分页表里按 key 引用，跨页画面才稳。') }}</p>
        <table class="tbl" style="margin-top:8px">
          <thead><tr><th style="width:90px">key</th><th style="width:130px">{{ $t('名字') }}</th><th>{{ $t('场景块') }}</th><th style="width:150px">{{ $t('锚图') }}</th><th style="width:40px"></th></tr></thead>
          <tbody>
            <tr v-for="(s, i) in form.scenes" :key="i" data-test="scene-row">
              <td><input v-model="s.key" @input="touch" class="mono" style="width:100%" placeholder="yard" /></td>
              <td><input v-model="s.name" @input="touch" style="width:100%" :placeholder="$t('小狐狸的院子')" /></td>
              <td><textarea v-model="s.block" @input="touch" rows="2" style="width:100%"></textarea></td>
              <td>
                <div class="hint mono small" v-if="s.asset">{{ String(s.asset).split(/[\\/]/).pop() }}</div>
                <img v-if="assetUrl(s, 'scene')" :src="assetUrl(s, 'scene')" alt="" style="width:60px;aspect-ratio:3/4;object-fit:cover;border-radius:3px;border:1px solid var(--border);display:block;margin:4px 0" />
                <label class="btn sm" style="cursor:pointer">{{ s.asset ? $t('换图') : $t('上传锚图') }}<input type="file" accept="image/*" style="display:none" @change="upload($event, 'scene', s)" /></label>
              </td>
              <td><button class="btn sm" :title="$t('删除')" @click="form.scenes.splice(i, 1); touch()">✕</button></td>
            </tr>
          </tbody>
        </table>
        <p v-if="!form.scenes.length" class="hint" style="margin-top:6px">{{ $t('还没有场景锚（可以没有）。') }}</p>
      </div>
    </div>
  </div>
</template>
