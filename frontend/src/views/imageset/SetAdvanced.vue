<script setup>
// 高级：整份项目文件 JSON 直接改（PUT 整体保存，校验不过就报原因）；补传参考图；删除 = 归档到 .webui_deleted/<module>/。
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { put, post, fileToBase64 } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { confirm, notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { setUrl, specBody, refreshSet, archiveSet, setArtifact, baseName, moduleInfo } from '../../imageset.js'

const route = useRoute()
const router = useRouter()
const m = computed(() => route.meta.module || 'sticker')
const s = computed(() => store.set)
const text = ref('')
const dirty = ref(false)
const error = ref('')
function sync() { if (!s.value) return; text.value = JSON.stringify(specBody(s.value), null, 2); dirty.value = false }
watch(() => s.value, () => { if (!dirty.value) sync() }, { immediate: true, deep: false })
const assets = computed(() => (s.value?.subject?.assets || []).map(a => (typeof a === 'string' ? { file: a } : a)))
const assetUrl = (a) => a.url || setArtifact.asset(m.value, s.value.id, baseName(a.file || a.path))
const isWedding = computed(() => s.value?.template === 'wedding')

async function save() {
  error.value = ''
  let parsed
  try { parsed = JSON.parse(text.value) } catch (e) { error.value = t('不是合法的 JSON：{err}', { err: e.message }); return }
  await withBusy(async () => {
    try { await put(setUrl(m.value, s.value.id), parsed); dirty.value = false; await refreshSet(); toast(t('已保存')) }
    catch (e) { error.value = e.message }
  })
}
async function upload(ev) {
  const files = Array.from(ev.target.files || []); ev.target.value = ''
  if (!files.length) return
  await withBusy(async () => {
    for (const file of files) {
      try {
        const data_base64 = await fileToBase64(file)
        const body = { filename: file.name, data_base64 }
        if (isWedding.value) body.who = 'bride'
        const r = await post(`${setUrl(m.value, s.value.id)}/assets`, body)
        // 后端按内容认出是已有的那张（换了文件名的同一张照片）：没有多加一条，要说出来——否则用户以为传上了、却数不见
        toast(r?.existing ? t('「{name}」跟已有的参考图是同一张，没有重复添加', { name: file.name }) : t('已上传 {name}', { name: file.name }))
      } catch (e) { await notice(t('上传失败：{msg}', { msg: e.message })) }
    }
    await refreshSet()
  })
}
async function archive() {
  if (!await confirm(t('删除「{id}」？\n\n项目文件和生成过的产物会一起收进回收站，删除后可在「回收站」里恢复。', { id: s.value.id }), { confirmLabel: t('删除'), danger: true })) return
  await withBusy(async () => {
    try { await archiveSet(m.value, s.value.id); toast(t('已删除（可在「回收站」里恢复）')); router.push(`/${m.value}`) } catch (e) { await notice(e.message) }
  })
}
</script>

<template>
  <div class="ws-content" v-if="s">
    <div class="ws-center" style="max-width:860px" data-test="set-advanced">
      <h3 style="font-size:16px;margin-bottom:8px">{{ $t('高级') }}</h3>
      <div class="card">
        <div class="row between">
          <h3>{{ $t('项目文件（JSON）') }}</h3>
          <button class="btn primary sm" :disabled="!dirty" data-test="json-save" @click="save">{{ $t('保存') }}</button>
        </div>
        <p class="hint">{{ $t('整套图片集就是这一份文件（content/{m}/{id}.json）：主体、风格、模板选项、每张的提示词与历次产物、挑选。保存时后端校验，形状不对会报原因。', { m, id: s.id }) }}</p>
        <textarea v-model="text" @input="dirty = true" class="mono" style="width:100%;min-height:420px;margin-top:6px;font-size:12px" data-test="json-text"></textarea>
        <div v-if="error" class="note bad" style="margin-top:6px" data-test="json-error">{{ error }}</div>
      </div>
      <div class="card" data-test="set-assets">
        <h3>{{ $t('参考图') }} <span class="hint">{{ assets.length }}/6</span></h3>
        <div class="row" style="align-items:flex-start">
          <div v-for="(a, i) in assets" :key="i" style="width:96px" data-test="set-asset">
            <img :src="assetUrl(a)" alt="" style="width:96px;aspect-ratio:1;object-fit:cover;border-radius:3px;border:1px solid var(--border);display:block" />
            <div class="hint mono small" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ baseName(a.file || a.path) }}<span v-if="a.who"> · {{ a.who }}</span></div>
          </div>
          <label class="btn sm" style="cursor:pointer" v-if="assets.length < 6">{{ $t('补传参考图') }}<input type="file" accept="image/*" multiple style="display:none" data-test="asset-upload" @change="upload" /></label>
        </div>
        <p class="hint" style="margin-top:6px">{{ $t('上传不覆盖已有文件；要换图先在 JSON 里删掉旧条目。') }}</p>
      </div>
      <div class="card">
        <h3>{{ $t('产物目录') }}</h3>
        <p class="hint mono small">output/{{ m }}/{{ s.id }}/ · 01_raw · 05_post · 09_export</p>
      </div>
      <div class="card">
        <h3>{{ $t('删除（归档）') }}</h3>
        <p class="hint">{{ $t('从列表移走，文件保留。') }}</p>
        <div class="row" style="margin-top:8px"><button class="btn danger" data-test="set-archive" @click="archive">{{ $t('删除这套「{name}」', { name: $t(moduleInfo(m).name) }) }}</button></div>
      </div>
    </div>
  </div>
</template>
