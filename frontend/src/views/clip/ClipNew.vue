<script setup>
// 新建片子（docs/clip-spec.md §6）：标题、风格卡（或自写风格锚）、画幅、时长、视频模型、提示词语言。
// 建出来是一份只有【全片设定】的空项目，节拍去「写法」页写（新建不花钱）。
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { post } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { CLIP_API, ASPECT_RATIOS, DURATIONS, PROMPT_LANGUAGES, DIALECTS, blankClip, loadClips, clipRoute } from '../../clip.js'
import StyleCardPicker from '../../components/StyleCardPicker.vue'

const router = useRouter()
const form = reactive({
  title: '', style: 'custom', style_anchor: '', aspect_ratio: '16:9', duration: 15,
  model: '@video', prompt_language: 'zh', dialect: 'auto', look: '', palette: '',
})
const busy = ref(false)
const paletteList = computed(() => form.palette.split(/[、,，;；\n]/).map(s => s.trim()).filter(Boolean))

async function create() {
  if (!form.title.trim()) { await notice(t('先给这条片子起个名字')); return }
  if (form.style === 'custom' && !form.style_anchor.trim() && !form.look.trim()) {
    await notice(t('自写风格时，风格锚或「看起来像什么」至少写一句——它是提示词的第一句')); return
  }
  const body = blankClip(form.title.trim())
  body.style = form.style
  body.style_anchor = form.style_anchor.trim()
  body.prompt_language = form.prompt_language
  body.dialect = form.dialect
  body.global = { ...body.global, aspect_ratio: form.aspect_ratio, look: form.look.trim(), palette: paletteList.value }
  body.video_params = { ...body.video_params, model: form.model.trim() || '@video', duration: Number(form.duration) || 15 }
  busy.value = true
  await withBusy(async () => {
    try {
      const r = await post(CLIP_API, body)
      await loadClips()
      toast(t('已新建，去「写法」写时间轴'))
      router.push(clipRoute(r.id))
    } catch (e) { await notice(t('新建失败：{err}', { err: e.message })) } finally { busy.value = false }
  })
}
</script>

<template>
  <div class="page narrow" data-test="clip-new">
    <div class="card">
      <h3>{{ $t('新建单条视频') }}</h3>
      <p class="hint" style="margin-bottom:10px">{{ $t('先定死全片统一的那几件事（风格、色板、画幅、时长），节拍在「写法」页按秒写。新建不花钱。') }}</p>
      <div class="field"><label>{{ $t('片名') }}</label><input v-model="form.title" data-test="clip-title" :placeholder="$t('如「城市清晨短片」')" /></div>
      <div class="field">
        <label>{{ $t('风格') }}</label>
        <StyleCardPicker v-model="form.style" v-model:anchor="form.style_anchor" :show-grid="true" select-test="clip-style" />
      </div>
      <div class="field" v-if="form.style === 'custom'">
        <label>{{ $t('风格锚（提示词的第一句）') }}</label>
        <textarea v-model="form.style_anchor" data-test="clip-anchor" :placeholder="$t('如「手持跟拍的纪录片质感，清晨自然光，画面带一点颗粒」')"></textarea>
      </div>
      <div class="field"><label>{{ $t('看起来像什么（可选，进【全片设定】）') }}</label><input v-model="form.look" data-test="clip-look" /></div>
      <div class="field">
        <label>{{ $t('色板（5–7 个具体色名，顿号分开）') }}</label>
        <input v-model="form.palette" data-test="clip-palette" :placeholder="$t('奶白、橘红、墨绿、沙色、浅灰蓝')" />
        <div class="hint" data-test="clip-palette-hint">{{ $t('现在 {n} 个；少于 5 个「写法」页会提醒', { n: paletteList.length }) }}</div>
      </div>
      <div class="grid2">
        <div class="field">
          <label>{{ $t('画幅') }}</label>
          <select v-model="form.aspect_ratio" data-test="clip-aspect"><option v-for="a in ASPECT_RATIOS" :key="a" :value="a">{{ a }}</option></select>
          <div class="hint">{{ $t('提示词里会自动带上排除另一种画幅的那句与安全区') }}</div>
        </div>
        <div class="field">
          <label>{{ $t('时长（秒）') }}</label>
          <select v-model.number="form.duration" data-test="clip-duration"><option v-for="d in DURATIONS" :key="d" :value="d">{{ d }}</option></select>
        </div>
        <div class="field"><label>{{ $t('视频模型（@video = 用「模型设置」里的）') }}</label><input v-model="form.model" data-test="clip-model" /></div>
        <div class="field">
          <label>{{ $t('提示词语言') }}</label>
          <select v-model="form.prompt_language" data-test="clip-lang"><option v-for="l in PROMPT_LANGUAGES" :key="l.id" :value="l.id">{{ $t(l.label) }}</option></select>
        </div>
        <div class="field span2">
          <label>{{ $t('提示词方言') }}</label>
          <select v-model="form.dialect" data-test="clip-dialect"><option v-for="d in DIALECTS" :key="d.id" :value="d.id">{{ $t(d.label) }}</option></select>
        </div>
      </div>
      <div class="row">
        <button class="btn primary" :disabled="busy" data-test="clip-create" @click="create">{{ $t('新建') }}</button>
        <span class="hint">{{ $t('建好之后在「写法」页写节拍，在「出片」页花钱出片。') }}</span>
      </div>
    </div>
  </div>
</template>
