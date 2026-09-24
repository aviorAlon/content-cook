<script setup>
// 高级（docs/podcast-spec.md §6）：整份项目文件 JSON 直接改（PUT 整体保存，校验不过就报原因）；删除 = 归档到 .webui_deleted/podcast/。
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { put } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { confirm, notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { pc, specBody, refreshPodcast, archivePodcast } from '../../podcast.js'

const router = useRouter()
const s = computed(() => store.podcast)
// 有任务在跑（起草 / 合成 / 审稿 / 导出）时不让删：服务端也会 409，这里只是别让人点了再看报错
const anyBusy = computed(() => Object.values(store.podcastDetail?.busy || {}).some(Boolean))
const text = ref('')
const dirty = ref(false)
const error = ref('')
function sync() { if (!s.value) return; text.value = JSON.stringify(specBody(s.value), null, 2); dirty.value = false }
watch(s, () => { if (!dirty.value) sync() }, { immediate: true })

async function save() {
  error.value = ''
  let parsed
  try { parsed = JSON.parse(text.value) } catch (e) { error.value = t('不是合法的 JSON：{err}', { err: e.message }); return }
  await withBusy(async () => {
    try { await put(pc(s.value.id), parsed); dirty.value = false; await refreshPodcast(); toast(t('已保存')) }
    catch (e) { error.value = e.message }
  })
}

async function archive() {
  if (!await confirm(t('删除「{id}」？\n\n项目文件和生成过的产物会一起收进回收站，删除后可在「回收站」里恢复。', { id: s.value.id }), { confirmLabel: t('删除'), danger: true })) return
  await withBusy(async () => {
    try { await archivePodcast(s.value.id); toast(t('已删除（可在「回收站」里恢复）')); router.push('/podcast') } catch (e) { await notice(e.message) }
  })
}
</script>

<template>
  <div class="ws-content" v-if="s">
    <div class="ws-center" style="max-width:860px" data-test="podcast-advanced">
      <h3 style="font-size:16px;margin-bottom:8px">{{ $t('高级') }}</h3>
      <div class="card">
        <div class="row between">
          <h3>{{ $t('项目文件（JSON）') }}</h3>
          <button class="btn primary sm" :disabled="!dirty" data-test="json-save" @click="save">{{ $t('保存') }}</button>
        </div>
        <p class="hint">{{ $t('整期播客就是这一份文件（content/podcast/{id}.json）：选题、主播、环节与台词、音乐、停顿、最近一次起草。保存时后端校验，说话人不在人物表、形式与人数对不上都会报原因，不落盘。', { id: s.id }) }}</p>
        <textarea v-model="text" @input="dirty = true" class="mono" style="width:100%;min-height:420px;margin-top:6px;font-size:12px" data-test="json-text"></textarea>
        <div v-if="error" class="note bad" style="margin-top:6px" data-test="json-error">{{ error }}</div>
      </div>
      <div class="card">
        <h3>{{ $t('产物目录') }}</h3>
        <p class="hint mono small">output/podcast/{{ s.id }}/07_audio · 08_mix</p>
      </div>
      <div class="card">
        <h3>{{ $t('删除（归档）') }}</h3>
        <p class="hint">{{ $t('从列表移走，项目文件和已出的音频一起搬进归档目录，不是真删。') }}</p>
        <div class="row" style="margin-top:8px">
          <button class="btn danger" data-test="podcast-archive" :disabled="anyBusy" @click="archive">{{ $t('删除这期播客') }}</button>
          <span v-if="anyBusy" class="hint small" data-test="podcast-archive-busy">{{ $t('有任务在跑，等它结束再删') }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
