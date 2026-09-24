<script setup>
// 高级（docs/clip-spec.md §6）：整份 JSON 直接改（PUT 整体保存，校验不过就报原因）；删除 = 归档到 .webui_deleted/clip/。
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { put } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { confirm, notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { clipUrl, refreshClip, archiveClip, specBody } from '../../clip.js'

const router = useRouter()
const c = computed(() => store.clip)
const text = ref('')
const dirty = ref(false)
const error = ref('')
function sync() {
  if (!c.value) return
  text.value = JSON.stringify(specBody(c.value), null, 2)
  dirty.value = false
}
watch(() => c.value, () => { if (!dirty.value) sync() }, { immediate: true, deep: false })

async function save() {
  error.value = ''
  let parsed
  try { parsed = JSON.parse(text.value) } catch (e) { error.value = t('不是合法的 JSON：{err}', { err: e.message }); return }
  await withBusy(async () => {
    try {
      const r = await put(clipUrl(c.value.id), parsed)
      dirty.value = false
      await refreshClip()
      const n = (r.warnings || []).length
      toast(n ? t('已保存，{n} 条检查提醒', { n }) : t('已保存'))
    } catch (e) { error.value = e.message }
  })
}
async function archive() {
  if (!await confirm(t('删除「{id}」？\n\n项目文件和生成过的产物会一起收进回收站，删除后可在「回收站」里恢复。', { id: c.value.id }), { confirmLabel: t('删除'), danger: true })) return
  await withBusy(async () => {
    try { await archiveClip(c.value.id); toast(t('已删除（可在「回收站」里恢复）')); router.push('/clip') } catch (e) { await notice(e.message) }
  })
}
</script>

<template>
  <div class="ws-content" v-if="c">
    <div class="ws-center" style="max-width:860px" data-test="clip-advanced">
      <h3 style="font-size:16px;margin-bottom:8px">{{ $t('高级') }}</h3>
      <div class="card">
        <div class="row between">
          <h3>{{ $t('项目文件（JSON）') }}</h3>
          <button class="btn primary sm" :disabled="!dirty" data-test="json-save" @click="save">{{ $t('保存') }}</button>
        </div>
        <p class="hint">{{ $t('这条片子就是这一份文件（content/clip/{id}.json）。保存时后端校验 + 检查，形状不对会报原因。', { id: c.id }) }}</p>
        <textarea v-model="text" @input="dirty = true" class="mono" style="width:100%;min-height:420px;margin-top:6px;font-size:12px" data-test="json-text"></textarea>
        <div v-if="error" class="note bad" style="margin-top:6px" data-test="json-error">{{ error }}</div>
      </div>
      <div class="card">
        <h3>{{ $t('产物目录') }}</h3>
        <p class="hint mono small">output/clip/{{ c.id }}/ · 00_frame · 01_video</p>
      </div>
      <div class="card">
        <h3>{{ $t('删除这条片子（归档）') }}</h3>
        <p class="hint">{{ $t('从列表移走，文件保留。') }}</p>
        <div class="row" style="margin-top:8px"><button class="btn danger" data-test="clip-archive" @click="archive">{{ $t('删除') }}</button></div>
      </div>
    </div>
  </div>
</template>
