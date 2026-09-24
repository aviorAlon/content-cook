<script setup>
// 新建播客（docs/podcast-spec.md §6）：标题、形式下拉（切换时按人数预置主播行）、选题 / 角度 / 听众 / 要点 / 素材 / 语气 / 避免、
// 目标分钟、主播表（名字 / 角色 / 人设 / 立场 / 音色下拉，音色来自 GET /api/voices）。POST /api/podcast → 进「脚本」页。
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { post } from '../../api.js'
import { store, withBusy, loadVoices } from '../../store.js'
import { notice } from '../../ui.js'
import { t } from '../../i18n.js'
import {
  loadFormats, findFormat, emptyBrief, emptyHost, splitList, loadPodcasts, podcastRoute,
  HOST_ROLES, HOSTS_MIN, HOSTS_MAX, MINUTES_MIN, MINUTES_MAX,
} from '../../podcast.js'

const router = useRouter()
const form = reactive({ title: '', format: 'duo', brief: emptyBrief(), target_minutes: 12, hosts: [emptyHost(0, 'host'), emptyHost(1, 'cohost')] })
const limits = ref({ target_minutes: [MINUTES_MIN, MINUTES_MAX], hosts: [HOSTS_MIN, HOSTS_MAX] })
const chipInput = reactive({ takeaways: '', avoid: '' })
const status = ref('')
const formats = ref([])   // 形式表是模块级缓存，这里存一份 ref 让下拉跟着渲染
const voices = computed(() => (store.voices || []).filter(v => v.id))
// 只有文字描述、没有 TTS 音色（也没克隆好）的档案不能出声：标出来并禁选（配音页同一条规则）
const descriptionOnly = (v) => typeof v.voice === 'string' && !v.voice.trim() && !(v.clone && v.clone.status === 'ready' && v.clone.voice_id)

onMounted(async () => {
  const data = await loadFormats()
  formats.value = data.formats
  limits.value = data.limits
  form.target_minutes = data.defaults.target_minutes ?? 12
  if (!store.voices) await loadVoices()
})

// 切换形式：按该形式的最少人数预置主播行（多了裁掉、少了补上），访谈的第二位默认是嘉宾
function applyFormat() {
  const f = findFormat(form.format)
  if (!f) return
  const [lo, hi] = f.hosts
  while (form.hosts.length < lo) form.hosts.push(emptyHost(form.hosts.length, defaultRole(form.hosts.length)))
  if (form.hosts.length > hi) form.hosts.splice(hi)
  form.hosts.forEach((h, i) => { h.role = defaultRole(i) })
}
function defaultRole(i) {
  if (i === 0) return 'host'
  return form.format === 'interview' ? 'guest' : 'cohost'
}
const hostRange = computed(() => findFormat(form.format)?.hosts || limits.value.hosts)
function addHost() {
  if (form.hosts.length >= hostRange.value[1]) return
  form.hosts.push(emptyHost(form.hosts.length, defaultRole(form.hosts.length)))
}
function dropHost(i) {
  if (form.hosts.length <= hostRange.value[0]) return
  form.hosts.splice(i, 1)
  form.hosts.forEach((h, k) => { h.id = `h${k + 1}` })
}

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
  if (form.hosts.some(h => !String(h.name || '').trim())) { status.value = t('每位主播都要有名字'); return }
  const body = {
    title: form.title.trim(),
    format: form.format,
    language: 'zh',
    brief: { ...form.brief },
    target_minutes: Number(form.target_minutes),
    hosts: form.hosts.map(h => ({ ...h, name: h.name.trim() })),
    segments: [],
  }
  status.value = t('创建中…')
  await withBusy(async () => {
    try {
      const r = await post('/api/podcast', body)
      await loadPodcasts()
      status.value = ''
      router.push(podcastRoute(r.id))
    } catch (e) { status.value = t('失败：{msg}', { msg: e.message }); await notice(e.message) }
  })
}
</script>

<template>
  <div class="page narrow" data-test="podcast-new">
    <h2 style="font-size:20px">{{ $t('新建播客') }}</h2>
    <p class="hint">{{ $t('先定「聊什么、从哪个角度、说给谁听」，再把素材（数据、案例、亲历）贴进来——起草时必须用上，不许编。这一步不花钱，起草才发模型请求。') }}</p>

    <div class="card">
      <div class="field">
        <label>{{ $t('标题') }}</label>
        <input v-model="form.title" data-test="podcast-title" :placeholder="$t('例如：为什么我们戒不掉短视频')" />
      </div>
      <div class="row">
        <div class="field">
          <label>{{ $t('形式') }}</label>
          <select v-model="form.format" data-test="podcast-format" @change="applyFormat">
            <option v-for="f in formats" :key="f.id" :value="f.id">{{ $t(f.name) }}</option>
          </select>
          <div class="hint" v-if="findFormat(form.format)?.note" data-test="podcast-format-note">{{ findFormat(form.format).note }}</div>
        </div>
        <div class="field">
          <label>{{ $t('目标分钟（{lo}–{hi}）', { lo: limits.target_minutes[0], hi: limits.target_minutes[1] }) }}</label>
          <input v-model="form.target_minutes" type="number" data-test="podcast-minutes" :min="limits.target_minutes[0]" :max="limits.target_minutes[1]" />
        </div>
      </div>
    </div>

    <div class="card">
      <h3>{{ $t('选题（brief）') }}</h3>
      <div class="field">
        <label>{{ $t('选题（聊什么）') }}</label>
        <input v-model="form.brief.topic" data-test="brief-topic" :placeholder="$t('例如：刷短视频停不下来，到底是意志力差还是产品设计使然')" />
      </div>
      <div class="field">
        <label>{{ $t('角度') }}</label>
        <input v-model="form.brief.angle" data-test="brief-angle" :placeholder="$t('例如：不做道德批判，从产品机制和个人经历两边聊')" />
      </div>
      <div class="field">
        <label>{{ $t('听众') }}</label>
        <input v-model="form.brief.audience" data-test="brief-audience" :placeholder="$t('例如：25–35 岁、自己也在刷的上班族')" />
      </div>
      <div v-for="key in ['takeaways', 'avoid']" :key="key" class="field" :data-test="`brief-${key}`">
        <label>{{ $t({ takeaways: '要点（听完带走什么）', avoid: '避免' }[key]) }}</label>
        <div class="chips">
          <span v-for="v in form.brief[key]" :key="v" class="pill" data-test="brief-chip" style="cursor:pointer" @click="dropChip(key, v)">{{ v }} ✕</span>
        </div>
        <div class="row" style="margin-top:4px">
          <input v-model="chipInput[key]" data-test="brief-chip-input" :placeholder="$t('用顿号或逗号分隔，回车添加')" @keyup.enter="addChips(key)" />
          <button class="btn sm" data-test="brief-chip-add" @click="addChips(key)">{{ $t('添加') }}</button>
        </div>
      </div>
      <div class="field">
        <label>{{ $t('素材（数据、案例、亲历——起草时必须用上）') }}</label>
        <textarea v-model="form.brief.materials" data-test="brief-materials" rows="4" :placeholder="$t('例如：老周去年戒了三个月又复发；一份 2025 年的屏幕时间数据……')"></textarea>
      </div>
      <div class="field">
        <label>{{ $t('语气') }}</label>
        <input v-model="form.brief.tone" data-test="brief-tone" :placeholder="$t('例如：轻松但不水，允许互相拆台')" />
      </div>
    </div>

    <div class="card" data-test="podcast-hosts">
      <div class="row between">
        <h3>{{ $t('主播') }} <span class="hint">{{ form.hosts.length }}/{{ hostRange[1] }}</span></h3>
        <button class="btn sm" data-test="host-add" :disabled="form.hosts.length >= hostRange[1]" @click="addHost">{{ $t('+ 加一位') }}</button>
      </div>
      <p class="hint">{{ $t('人设和立场决定每个人怎么说话：两人观点一致到分不清谁在说，就不像真人了。音色可以先留空（占位音色免费，能出声但分不清人），之后在「配音」页换。') }}</p>
      <div v-for="(h, i) in form.hosts" :key="h.id" class="card" style="margin-top:8px;padding:10px 12px" data-test="host-row">
        <div class="row">
          <div class="field" style="flex:1">
            <label>{{ $t('名字') }}</label>
            <input v-model="h.name" data-test="host-name" :placeholder="$t('例如：老周')" />
          </div>
          <div class="field">
            <label>{{ $t('主播角色') }}</label>
            <select v-model="h.role" data-test="host-role">
              <option v-for="(label, role) in HOST_ROLES" :key="role" :value="role">{{ $t(label) }}</option>
            </select>
          </div>
          <div class="field" style="flex:1">
            <label>{{ $t('音色') }}</label>
            <select v-model="h.voice" data-test="host-voice">
              <option value="">{{ $t('占位音色（免费，可稍后换）') }}</option>
              <option v-for="v in voices" :key="v.id" :value="v.id" :disabled="descriptionOnly(v)">{{ v.name || v.id }}{{ descriptionOnly(v) ? ` · ${$t('只有描述，不能出声')}` : '' }}</option>
            </select>
          </div>
          <button class="btn sm" style="align-self:flex-end" data-test="host-remove" :disabled="form.hosts.length <= hostRange[0]" @click="dropHost(i)">✕</button>
        </div>
        <div class="row">
          <div class="field" style="flex:1">
            <label>{{ $t('人设') }}</label>
            <input v-model="h.persona" data-test="host-persona" :placeholder="$t('例如：做了十年产品，爱举例子，说话慢，喜欢反问')" />
          </div>
          <div class="field" style="flex:1">
            <label>{{ $t('立场') }}</label>
            <input v-model="h.stance" data-test="host-stance" :placeholder="$t('例如：认为是设计的锅')" />
          </div>
        </div>
      </div>
      <p class="hint" v-if="!voices.length">{{ $t('音色库是空的——') }}<router-link to="/voices">{{ $t('去音色库建一个档案') }}</router-link></p>
    </div>

    <div class="row" style="margin-top:10px">
      <button class="btn primary" data-test="podcast-create" @click="create">{{ $t('创建') }}</button>
      <button class="btn" @click="router.push('/podcast')">{{ $t('取消') }}</button>
      <span v-if="status" class="status">{{ status }}</span>
    </div>
  </div>
</template>
