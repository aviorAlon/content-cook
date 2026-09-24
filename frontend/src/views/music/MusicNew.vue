<script setup>
// 新建配乐（docs/music-spec.md §5）：标题、预设 brief 下拉、用途一句话、情绪 / 乐器 chips、BPM、结构、避免、时长、条数。
// 选了预设就把整份 brief 填进表单，之后随便改。POST /api/music → 进项目的「生成」页。
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { post } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { notice } from '../../ui.js'
import { t, isEn } from '../../i18n.js'
import { loadBriefs, findBrief, emptyBrief, splitList, loadMusics, musicRoute, COUNT_MIN, COUNT_MAX } from '../../music.js'

const router = useRouter()
const form = reactive({ title: '', preset: '', brief: emptyBrief(), duration: 60, count: 2, prompt_language: 'zh' })
const limits = ref({ count: [COUNT_MIN, COUNT_MAX], duration: [5, 600], bpm: [30, 240] })
const chipInput = reactive({ mood: '', instruments: '', avoid: '' })
const status = ref('')
const briefs = ref([])   // 预设 brief 是模块级缓存，这里存一份 ref 让下拉跟着渲染

onMounted(async () => {
  const data = await loadBriefs()
  briefs.value = data.briefs
  limits.value = data.limits
  form.duration = data.defaults.duration ?? 60
})

function applyPreset() {
  const preset = findBrief(form.preset)
  if (!preset) { form.brief = emptyBrief(); return }
  form.brief = { ...emptyBrief(), ...preset.brief }
  if (preset.duration) form.duration = preset.duration
  if (!form.title) form.title = presetName(preset)
}
// 预设库自带英文名（english）：英文界面下拉和默认标题都用它；name 不在中间件的翻译键里，不挑就是中文
const presetName = (b) => (isEn() && b.english) || b.name

function addChips(key) {
  const items = splitList(chipInput[key])
  if (!items.length) return
  form.brief[key] = [...form.brief[key], ...items.filter(x => !form.brief[key].includes(x))]
  chipInput[key] = ''
}
function dropChip(key, value) {
  form.brief[key] = form.brief[key].filter(x => x !== value)
}

const canCreate = computed(() => !!form.title.trim())

async function create() {
  if (!canCreate.value) { status.value = t('先填一个标题'); return }
  const bpm = form.brief.bpm === '' || form.brief.bpm == null ? null : Number(form.brief.bpm)
  const body = {
    title: form.title.trim(),
    brief: { ...form.brief, bpm },
    duration: Number(form.duration),
    count: Number(form.count),
    prompt_language: form.prompt_language,
  }
  status.value = t('创建中…')
  await withBusy(async () => {
    try {
      const r = await post('/api/music', body)
      await loadMusics()
      status.value = ''
      router.push(musicRoute(r.id))
    } catch (e) { status.value = t('失败：{msg}', { msg: e.message }); await notice(e.message) }
  })
}
</script>

<template>
  <div class="page narrow" data-test="music-new">
    <h2 style="font-size:20px">{{ $t('新建配乐') }}</h2>
    <p class="hint">{{ $t('先说清「这段音乐给什么用」，再补情绪、乐器、速度、结构和不要什么。这一步不花钱，出配乐才花。') }}</p>

    <div class="card">
      <div class="field">
        <label>{{ $t('标题') }}</label>
        <input v-model="form.title" data-test="music-title" :placeholder="$t('例如：睡前摇篮曲')" />
      </div>
      <div class="field">
        <label>{{ $t('预设 brief') }}</label>
        <select v-model="form.preset" data-test="music-preset" @change="applyPreset">
          <option value="">{{ $t('（不用预设，自己写）') }}</option>
          <option v-for="b in briefs" :key="b.id" :value="b.id">{{ presetName(b) }}</option>
        </select>
        <!-- 预设库自带 note_en：英文界面直接用它，不走后端翻译（后端 tr 对这些库文案没规则，原样中文） -->
        <div class="hint" v-if="findBrief(form.preset)" data-test="music-preset-note">{{ (isEn() && findBrief(form.preset).note_en) || findBrief(form.preset).note }}</div>
      </div>
    </div>

    <div class="card">
      <h3>{{ $t('意图（brief）') }}</h3>
      <div class="field">
        <label>{{ $t('用途（一句话，进提示词开头）') }}</label>
        <input v-model="form.brief.usage" data-test="brief-usage" :placeholder="$t('例如：绘本睡前朗读的底噪')" />
      </div>
      <div class="field">
        <label>{{ $t('体裁') }}</label>
        <input v-model="form.brief.genre" data-test="brief-genre" :placeholder="$t('例如：摇篮曲')" />
      </div>
      <div v-for="key in ['mood', 'instruments', 'avoid']" :key="key" class="field" :data-test="`brief-${key}`">
        <label>{{ $t({ mood: '情绪', instruments: '乐器', avoid: '不要（避免）' }[key]) }}</label>
        <div class="chips">
          <span v-for="v in form.brief[key]" :key="v" class="pill" data-test="brief-chip" style="cursor:pointer" @click="dropChip(key, v)">{{ v }} ✕</span>
        </div>
        <div class="row" style="margin-top:4px">
          <input v-model="chipInput[key]" data-test="brief-chip-input" :placeholder="$t('用顿号或逗号分隔，回车添加')" @keyup.enter="addChips(key)" />
          <button class="btn sm" data-test="brief-chip-add" @click="addChips(key)">{{ $t('添加') }}</button>
        </div>
      </div>
      <div class="field">
        <label>{{ $t('速度（BPM，可留空）') }}</label>
        <input v-model="form.brief.bpm" type="number" data-test="brief-bpm" :min="limits.bpm[0]" :max="limits.bpm[1]" />
      </div>
      <div class="field">
        <label>{{ $t('结构') }}</label>
        <input v-model="form.brief.structure" data-test="brief-structure" :placeholder="$t('例如：前 8 秒渐入，中段平稳，最后 3 秒渐弱到无')" />
      </div>
    </div>

    <div class="card">
      <h3>{{ $t('参数') }}</h3>
      <div class="row">
        <div class="field">
          <label>{{ $t('时长（秒）') }}</label>
          <input v-model="form.duration" type="number" data-test="music-duration" :min="limits.duration[0]" :max="limits.duration[1]" />
        </div>
        <div class="field">
          <label>{{ $t('一次出几条（{lo}–{hi}）', { lo: limits.count[0], hi: limits.count[1] }) }}</label>
          <input v-model="form.count" type="number" data-test="music-count" :min="limits.count[0]" :max="limits.count[1]" />
        </div>
        <div class="field">
          <label>{{ $t('提示词语言') }}</label>
          <select v-model="form.prompt_language" data-test="music-language">
            <option value="zh">{{ $t('中文') }}</option>
            <option value="en">English</option>
          </select>
        </div>
      </div>
      <p class="hint">{{ $t('时长写进提示词里表达——音乐生成接口大多没有时长参数（见 docs/music-spec.md §7）。') }}</p>
    </div>

    <div class="row" style="margin-top:10px">
      <button class="btn primary" data-test="music-create" @click="create">{{ $t('创建') }}</button>
      <button class="btn" @click="router.push('/music')">{{ $t('取消') }}</button>
      <span v-if="status" class="status">{{ status }}</span>
    </div>
  </div>
</template>
