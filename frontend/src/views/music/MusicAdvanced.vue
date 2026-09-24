<script setup>
// 高级（docs/music-spec.md §5）：整份项目文件 JSON 直接改（PUT 整体保存，校验不过就报原因）；
// 参考图上传（jpg / png，≤10）；删除 = 归档到 .webui_deleted/music/。
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { put, post, fileToBase64 } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { confirm, notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { mu, specBody, refreshMusic, archiveMusic } from '../../music.js'

const router = useRouter()
const s = computed(() => store.music)
const d = computed(() => store.musicDetail)
const text = ref('')
const dirty = ref(false)
const error = ref('')
function sync() { if (!s.value) return; text.value = JSON.stringify(specBody(s.value), null, 2); dirty.value = false }
watch(s, () => { if (!dirty.value) sync() }, { immediate: true })
const refs = computed(() => d.value?.references || (s.value?.reference_images || []).map(f => ({ file: f, exists: true })))
const maxRefs = computed(() => d.value?.limits?.max_references ?? 16)

async function save() {
  error.value = ''
  let parsed
  try { parsed = JSON.parse(text.value) } catch (e) { error.value = t('不是合法的 JSON：{err}', { err: e.message }); return }
  await withBusy(async () => {
    try { await put(mu(s.value.id), parsed); dirty.value = false; await refreshMusic(); toast(t('已保存')) }
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
        const r = await post(`${mu(s.value.id)}/references`, { filename: file.name, data_base64 })
        // 后端按内容认出是已有的那张（换了文件名的同一张图）：没有多加一条，要说出来——否则用户以为传上了、却数不见
        toast(r?.existing ? t('「{name}」跟已有的参考图是同一张，没有重复添加', { name: file.name }) : t('已上传 {name}', { name: file.name }))
      } catch (e) { await notice(t('上传失败：{msg}', { msg: e.message })) }
    }
    await refreshMusic()
  })
}

async function archive() {
  if (!await confirm(t('删除「{id}」？\n\n项目文件和生成过的产物会一起收进回收站，删除后可在「回收站」里恢复。', { id: s.value.id }), { confirmLabel: t('删除'), danger: true })) return
  await withBusy(async () => {
    try { await archiveMusic(s.value.id); toast(t('已删除（可在「回收站」里恢复）')); router.push('/music') } catch (e) { await notice(e.message) }
  })
}
</script>

<template>
  <div class="ws-content" v-if="s">
    <div class="ws-center" style="max-width:860px" data-test="music-advanced">
      <h3 style="font-size:16px;margin-bottom:8px">{{ $t('高级') }}</h3>
      <div class="card">
        <div class="row between">
          <h3>{{ $t('项目文件（JSON）') }}</h3>
          <button class="btn primary sm" :disabled="!dirty" data-test="json-save" @click="save">{{ $t('保存') }}</button>
        </div>
        <p class="hint">{{ $t('整个配乐项目就是这一份文件（content/music/{id}.json）：意图、时长条数、参考图、历次产物与采用、投放去向。保存时后端校验，形状不对会报原因。', { id: s.id }) }}</p>
        <textarea v-model="text" @input="dirty = true" class="mono" style="width:100%;min-height:420px;margin-top:6px;font-size:12px" data-test="json-text"></textarea>
        <div v-if="error" class="note bad" style="margin-top:6px" data-test="json-error">{{ error }}</div>
      </div>
      <div class="card" data-test="music-references">
        <h3>{{ $t('参考图（图生音乐）') }} <span class="hint">{{ refs.length }}/{{ maxRefs }}</span></h3>
        <p class="hint">{{ $t('可空。给了图，配乐模型会按画面的气质写；只接受 jpg / png。') }}</p>
        <div class="row" style="align-items:flex-start;margin-top:6px">
          <div v-for="(r, i) in refs" :key="i" class="pill mono small" data-test="music-reference">{{ r.file.split('/').pop() }}<span v-if="!r.exists"> · {{ $t('文件不在') }}</span></div>
          <label class="btn sm" style="cursor:pointer" v-if="refs.length < maxRefs">{{ $t('上传参考图') }}<input type="file" accept="image/png,image/jpeg" multiple style="display:none" data-test="reference-upload" @change="upload" /></label>
        </div>
      </div>
      <div class="card">
        <h3>{{ $t('产物目录') }}</h3>
        <p class="hint mono small">output/music/{{ s.id }}/01_raw</p>
      </div>
      <div class="card">
        <h3>{{ $t('删除（归档）') }}</h3>
        <p class="hint">{{ $t('从列表移走，文件保留。已经投放到别的项目里的那份是拷贝，不受影响。') }}</p>
        <div class="row" style="margin-top:8px"><button class="btn danger" data-test="music-archive" @click="archive">{{ $t('删除这个配乐') }}</button></div>
      </div>
    </div>
  </div>
</template>
