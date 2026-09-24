<script setup>
// 生成页（docs/music-spec.md §5）：brief 表单 + 编译出的提示词预览 + 生成按钮（写清 N 条请求）
// + 历次条目 <audio> 播放与采用 + 「放进项目」下拉（目标 + 短剧的起始秒 / 播客的槽）。
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { put, post } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { confirm, notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import {
  mu, specBody, refreshMusic, runMusicTask, attemptRows, splitList, loadTargets, useInProject, podcastSlotLabel,
  COUNT_MIN, COUNT_MAX, PODCAST_SLOT_OPTIONS, PODCAST_DEFAULT_SLOT,
} from '../../music.js'

const s = computed(() => store.music)
const d = computed(() => store.musicDetail)
const form = reactive({ brief: null, duration: 60, count: 1, prompt_language: 'zh' })
const chipInput = reactive({ mood: '', instruments: '', avoid: '' })
const dirty = ref(false)
const saving = ref('')
const targets = ref([])
const drop = reactive({ key: '', slot: '', podcastSlot: PODCAST_DEFAULT_SLOT })
const dropping = ref('')

function sync() {
  if (!s.value) return
  form.brief = JSON.parse(JSON.stringify(s.value.brief || {}))
  form.duration = s.value.duration
  form.count = s.value.count
  form.prompt_language = s.value.prompt_language || 'zh'
  dirty.value = false
}
watch(s, () => { if (!dirty.value) sync() }, { immediate: true })
onMounted(async () => { targets.value = await loadTargets() })

const prompt = computed(() => d.value?.prompt || d.value?.prompts?.[form.prompt_language] || '')
const attempts = computed(() => attemptRows(s.value?.id, d.value, s.value))
const okAttempts = computed(() => attempts.value.filter(a => a.ok))
const busy = computed(() => !!d.value?.busy?.generate)
const limits = computed(() => d.value?.limits || { count: [COUNT_MIN, COUNT_MAX], duration: [5, 600], bpm: [30, 240] })
const usedIn = computed(() => d.value?.used_in || s.value?.used_in || [])
// 配乐来源：内置来源不覆盖这一档，得装一个提供它的插件（/api/health 的 roles.music.sources 是 0）；
// 选过的来源没装上时 unavailable 是一句人话。两种情况「生成」都不可点（点了也只是后端 400），brief 和提示词照样能写
const musicRole = computed(() => store.health?.roles?.music || null)
const noMusicSource = computed(() => !!musicRole.value && musicRole.value.sources === 0 && !musicRole.value.adapter)
const musicUnavailable = computed(() => musicRole.value?.unavailable || '')
const musicBlocked = computed(() => noMusicSource.value || !!musicUnavailable.value)

function touch() { dirty.value = true }
function addChips(key) {
  const items = splitList(chipInput[key])
  if (!items.length) return
  form.brief[key] = [...(form.brief[key] || []), ...items.filter(x => !(form.brief[key] || []).includes(x))]
  chipInput[key] = ''
  touch()
}
function dropChip(key, value) { form.brief[key] = (form.brief[key] || []).filter(x => x !== value); touch() }

async function save() {
  saving.value = t('保存中…')
  const bpm = form.brief.bpm === '' || form.brief.bpm == null ? null : Number(form.brief.bpm)
  const body = { ...specBody(s.value), brief: { ...form.brief, bpm }, duration: Number(form.duration), count: Number(form.count), prompt_language: form.prompt_language }
  await withBusy(async () => {
    try { await put(mu(s.value.id), body); dirty.value = false; await refreshMusic(); saving.value = ''; toast(t('已保存')) }
    catch (e) { saving.value = ''; await notice(e.message) }
  })
}

// 生成：写清这次要发几条请求（花钱），确认后起后台任务
async function generate({ force = false } = {}) {
  if (dirty.value) await save()
  const n = Number(form.count) || 1
  const ok = await confirm(t('这次要发 {n} 次配乐请求（每次出 1 条），会花钱。继续？', { n }), { confirmLabel: t('生成'), localRole: 'music' })
  if (!ok) return
  await runMusicTask(`${mu(s.value.id)}/generate`, force ? { count: n, force: true } : { count: n }, t('出配乐'))
}

async function adopt(a) {
  await withBusy(async () => {
    try { await post(`${mu(s.value.id)}/pick`, { attempt: a.file }); await refreshMusic(); toast(t('已采用')) }
    catch (e) { await notice(e.message) }
  })
}

// 「放进项目」：目标下拉（短剧剧集 + 绘本 + 播客）+ 短剧的可选起始秒 / 播客的槽（片头 / 垫乐 / 片尾，默认垫乐）
const dropTarget = computed(() => targets.value.find(x => `${x.module}:${x.id}` === drop.key) || null)
const dropIsDrama = computed(() => dropTarget.value?.module === 'drama')
const dropIsPodcast = computed(() => dropTarget.value?.module === 'podcast')
const dropSlot = computed(() => (dropIsDrama.value ? drop.slot : dropIsPodcast.value ? drop.podcastSlot : null))
// 已投放药丸里的位置：短剧是起始秒，播客是槽的中文名
const usedSlotText = (u) => (u.module === 'podcast' ? t(podcastSlotLabel(u.slot)) : `${u.slot}s`)
async function dropIn() {
  const target = dropTarget.value
  if (!target) { dropping.value = t('先选一个目标项目'); return }
  dropping.value = t('投放中…')
  await withBusy(async () => {
    try {
      const r = await useInProject(s.value.id, { module: target.module, target: target.id, slot: dropSlot.value })
      dropping.value = ''
      toast(r.message || t('已放进《{name}》的配乐层', { name: r.target_title || target.title }), { ttl: 6000 })
      targets.value = await loadTargets()
      await refreshMusic()
    } catch (e) { dropping.value = ''; await notice(e.message) }
  })
}
</script>

<template>
  <div class="ws-content" v-if="s && form.brief">
    <div class="ws-center" style="max-width:920px" data-test="music-generate">
      <!-- brief 表单 -->
      <div class="card">
        <div class="row between">
          <h3>{{ $t('意图（brief）') }}</h3>
          <button class="btn sm" :disabled="!dirty" data-test="music-save" @click="save">{{ $t('保存') }}</button>
        </div>
        <div class="field">
          <label>{{ $t('用途（一句话，进提示词开头）') }}</label>
          <input v-model="form.brief.usage" data-test="brief-usage" @input="touch" />
        </div>
        <div class="field">
          <label>{{ $t('体裁') }}</label>
          <input v-model="form.brief.genre" data-test="brief-genre" @input="touch" />
        </div>
        <div v-for="key in ['mood', 'instruments', 'avoid']" :key="key" class="field" :data-test="`brief-${key}`">
          <label>{{ $t({ mood: '情绪', instruments: '乐器', avoid: '不要（避免）' }[key]) }}</label>
          <div class="chips">
            <span v-for="v in (form.brief[key] || [])" :key="v" class="pill" data-test="brief-chip" style="cursor:pointer" @click="dropChip(key, v)">{{ v }} ✕</span>
          </div>
          <div class="row" style="margin-top:4px">
            <input v-model="chipInput[key]" data-test="brief-chip-input" :placeholder="$t('用顿号或逗号分隔，回车添加')" @keyup.enter="addChips(key)" />
            <button class="btn sm" data-test="brief-chip-add" @click="addChips(key)">{{ $t('添加') }}</button>
          </div>
        </div>
        <div class="row">
          <div class="field">
            <label>{{ $t('速度（BPM，可留空）') }}</label>
            <input v-model="form.brief.bpm" type="number" data-test="brief-bpm" @input="touch" />
          </div>
          <div class="field">
            <label>{{ $t('时长（秒）') }}</label>
            <input v-model="form.duration" type="number" data-test="music-duration" :min="limits.duration[0]" :max="limits.duration[1]" @input="touch" />
          </div>
          <div class="field">
            <label>{{ $t('一次出几条（{lo}–{hi}）', { lo: limits.count[0], hi: limits.count[1] }) }}</label>
            <input v-model="form.count" type="number" data-test="music-count" :min="limits.count[0]" :max="limits.count[1]" @input="touch" />
          </div>
        </div>
        <div class="field">
          <label>{{ $t('结构') }}</label>
          <input v-model="form.brief.structure" data-test="brief-structure" @input="touch" />
        </div>
        <div v-if="saving" class="status">{{ saving }}</div>
      </div>

      <!-- 提示词预览 -->
      <div class="card">
        <h3>{{ $t('编译出的提示词') }}</h3>
        <p class="hint">{{ $t('这段字就是发给配乐模型的全部内容（按提示词语言取中文或英文那一版）。改上面的 brief 后保存，这里会跟着变。') }}</p>
        <pre class="mono" style="white-space:pre-wrap;font-size:12px;margin-top:6px" data-test="music-prompt">{{ prompt }}</pre>
        <div v-if="noMusicSource" class="note" style="margin-top:8px" data-test="music-no-source">
          {{ $t('「模型设置」里没有可用的配乐来源：内置来源不覆盖配乐，要装一个提供它的插件。装好之前不能生成，brief 和提示词照样能写。') }}
        </div>
        <div v-else-if="musicUnavailable" class="note" style="margin-top:8px" data-test="music-source-unavailable">{{ musicUnavailable }}</div>
        <div class="row" style="margin-top:8px">
          <button class="btn primary" :disabled="busy || musicBlocked" data-test="music-generate-btn" @click="generate()">
            {{ $t('生成（{n} 条请求）', { n: form.count }) }}
          </button>
          <button class="btn" :disabled="busy || musicBlocked || !okAttempts.length" data-test="music-regenerate" @click="generate({ force: true })">
            {{ $t('全部重出') }}
          </button>
          <span v-if="busy" class="status">{{ $t('正在出配乐…') }}</span>
        </div>
      </div>

      <!-- 历次：播放 + 采用 -->
      <div class="card" data-test="music-attempts">
        <h3>{{ $t('历次') }} <span class="hint">{{ attempts.length }}</span></h3>
        <div v-if="!attempts.length" class="hint">{{ $t('还没出过。点上面的「生成」。') }}</div>
        <div v-for="a in attempts" :key="a.key" class="row between" style="align-items:center;padding:6px 0;border-bottom:1px solid var(--border)" data-test="music-attempt">
          <div style="flex:1;min-width:0">
            <span class="mono small">{{ a.file || '—' }}</span>
            <span v-if="a.current" class="pill ok" data-test="music-attempt-current">{{ $t('已采用') }}</span>
            <div v-if="a.error" class="note bad small" data-test="music-attempt-error">{{ a.error }}</div>
            <audio v-else-if="a.url" controls preload="none" :src="a.url" data-test="music-audio" style="width:100%;margin-top:4px"></audio>
          </div>
          <button class="btn sm" v-if="a.ok" :disabled="a.current" data-test="music-adopt" @click="adopt(a)">{{ $t('采用') }}</button>
        </div>
      </div>

      <!-- 投放 -->
      <div class="card" data-test="music-use">
        <h3>{{ $t('放进项目') }}</h3>
        <p class="hint">{{ $t('把当前采用的那条拷进目标项目的素材目录，并写进它的配乐层（短剧 = 后期的配乐轨，绘本 = BGM，播客 = 片头 / 垫乐 / 片尾三个槽之一）。拷贝而不是引用：之后删掉这个配乐项目，目标项目照常能出片。') }}</p>
        <div class="row" style="margin-top:6px;align-items:flex-end">
          <div class="field" style="flex:1">
            <label>{{ $t('目标项目') }}</label>
            <select v-model="drop.key" data-test="use-target">
              <option value="">{{ $t('（选一个项目）') }}</option>
              <option v-for="x in targets" :key="`${x.module}:${x.id}`" :value="`${x.module}:${x.id}`">
                {{ x.group }} · {{ x.title }}（{{ x.id }}）
              </option>
            </select>
          </div>
          <div class="field" v-if="dropIsDrama" data-test="use-slot-field">
            <label>{{ $t('起始秒（可留空 = 从头）') }}</label>
            <input v-model="drop.slot" type="number" min="0" data-test="use-slot" />
          </div>
          <div class="field" v-else-if="dropIsPodcast" data-test="use-podcast-slot-field">
            <label>{{ $t('放到哪个槽') }}</label>
            <select v-model="drop.podcastSlot" data-test="use-podcast-slot">
              <option v-for="o in PODCAST_SLOT_OPTIONS" :key="o.id" :value="o.id">
                {{ dropTarget.slots.includes(o.id) ? $t('{slot}（已有，会覆盖）', { slot: $t(o.label) }) : $t(o.label) }}
              </option>
            </select>
          </div>
          <button class="btn primary" :disabled="!drop.key" data-test="use-submit" @click="dropIn">{{ $t('放进项目') }}</button>
        </div>
        <div v-if="dropping" class="status" data-test="use-status">{{ dropping }}</div>
        <div v-if="usedIn.length" class="chips" style="margin-top:8px" data-test="music-used-in">
          <span v-for="(u, i) in usedIn" :key="i" class="pill">{{ u.module }} · {{ u.id }}<span v-if="u.slot"> · {{ usedSlotText(u) }}</span></span>
        </div>
      </div>
    </div>
  </div>
</template>
