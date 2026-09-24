<script setup>
// 新建剧本（docs/scriptlab-spec.md §9）：标题、目标形态（四选一，下面一句话说明各自的写法与检查）、语言、
// brief（选题 / 角度 / 听众 / 素材 / 语气 + 避免）、角色表（名字 / 人设 / 立场，1–6 人）、段落骨架（标题 / 目标，可先留一段）。
// POST /api/script → 进「写」页。这一步不花钱，生成才发模型请求。
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { post } from '../../api.js'
import { withBusy } from '../../store.js'
import { notice } from '../../ui.js'
import { t } from '../../i18n.js'
import {
  SCRIPT_API, loadOptions, loadScripts, scriptRoute, emptyBrief, emptySpeaker, emptyBlock, nextBlockId, splitList, targetName,
  BLOCK_KINDS,
  TARGETS, SPEAKERS_MIN, SPEAKERS_MAX,
} from '../../scriptlab.js'

const router = useRouter()
const form = reactive({
  title: '', target: 'podcast', language: 'zh', brief: emptyBrief(),
  speakers: [emptySpeaker(0), emptySpeaker(1)],
  blocks: [emptyBlock('B01')],
})
const targets = ref(TARGETS)
const limits = ref({ speakers: [SPEAKERS_MIN, SPEAKERS_MAX] })
const avoidInput = ref('')
const status = ref('')

onMounted(async () => {
  const data = await loadOptions()
  targets.value = data.targets
  limits.value = data.limits
})

const targetNote = computed(() => {
  const row = targets.value.find(x => x.id === form.target)
  return row?.note ? t(row.note) : ''
})

function addSpeaker() {
  if (form.speakers.length >= limits.value.speakers[1]) return
  form.speakers.push(emptySpeaker(form.speakers.length))
}
function dropSpeaker(i) {
  if (form.speakers.length <= limits.value.speakers[0]) return
  form.speakers.splice(i, 1)
  form.speakers.forEach((s, k) => { s.id = `s${k + 1}` })
}
function addBlock() { form.blocks.push(emptyBlock(nextBlockId(form.blocks))) }
function dropBlock(i) { if (form.blocks.length > 1) form.blocks.splice(i, 1) }

function addAvoid() {
  const items = splitList(avoidInput.value)
  if (!items.length) return
  form.brief.avoid = [...form.brief.avoid, ...items.filter(x => !form.brief.avoid.includes(x))]
  avoidInput.value = ''
}
function dropAvoid(v) { form.brief.avoid = form.brief.avoid.filter(x => x !== v) }

const canCreate = computed(() => !!form.title.trim())

async function create() {
  if (!canCreate.value) { status.value = t('先填一个标题'); return }
  if (form.speakers.some(s => !String(s.name || '').trim())) { status.value = t('每个说话的人都要有名字'); return }
  const body = {
    title: form.title.trim(),
    target: form.target,
    language: form.language,
    brief: { ...form.brief },
    speakers: form.speakers.map(s => ({ ...s, name: s.name.trim() })),
    blocks: form.blocks.map(b => ({ ...b, title: String(b.title || '').trim() })),
    versions: [],
  }
  status.value = t('创建中…')
  await withBusy(async () => {
    try {
      const r = await post(SCRIPT_API, body)
      await loadScripts()
      status.value = ''
      router.push(scriptRoute(r.id))
    } catch (e) { status.value = t('失败：{msg}', { msg: e.message }); await notice(e.message) }
  })
}
</script>

<template>
  <div class="page narrow" data-test="script-new">
    <h2 style="font-size:20px">{{ $t('新建剧本') }}</h2>
    <p class="hint">{{ $t('先定「写给哪儿用、聊什么、谁在说」，再把素材贴进来——生成时必须用上，不许编。这一步不花钱，生成才发模型请求。') }}</p>

    <div class="card">
      <div class="field">
        <label>{{ $t('标题') }}</label>
        <input v-model="form.title" data-test="script-title" :placeholder="$t('例如：下班后的消息，要不要回')" />
      </div>
      <div class="row">
        <div class="field" style="flex:1">
          <label>{{ $t('目标形态（决定用哪套写法与哪套检查）') }}</label>
          <select v-model="form.target" data-test="script-target">
            <!-- 形态名走 targetName：英文界面用后端给的 english，$t(x.name) 对「短剧剧本（场）」这种字典里没有的名字只会露中文 -->
            <option v-for="x in targets" :key="x.id" :value="x.id">{{ targetName(x.id) }}</option>
          </select>
        </div>
        <div class="field">
          <label>{{ $t('语言') }}</label>
          <select v-model="form.language" data-test="script-language">
            <option value="zh">{{ $t('中文') }}</option>
            <option value="en">{{ $t('英文') }}</option>
          </select>
        </div>
      </div>
      <div class="hint" v-if="targetNote" data-test="script-target-note">{{ targetNote }}</div>
    </div>

    <div class="card">
      <h3>{{ $t('选题（brief）') }}</h3>
      <div class="field">
        <label>{{ $t('选题（聊什么）') }}</label>
        <input v-model="form.brief.topic" data-test="brief-topic" :placeholder="$t('例如：下班后收到工作消息，回还是不回')" />
      </div>
      <div class="field">
        <label>{{ $t('角度') }}</label>
        <input v-model="form.brief.angle" data-test="brief-angle" :placeholder="$t('例如：不站队，两个处境不同的人各讲各的')" />
      </div>
      <div class="field">
        <label>{{ $t('说给谁听') }}</label>
        <input v-model="form.brief.audience" data-test="brief-audience" :placeholder="$t('例如：刚工作两三年的人')" />
      </div>
      <div class="field">
        <label>{{ $t('素材（数据、案例、亲历——生成时只能用这里的事实和数字，不许编）') }}</label>
        <textarea v-model="form.brief.materials" data-test="brief-materials" rows="4" :placeholder="$t('例如：阿杰上个月周六晚上十点被拉进新群……')"></textarea>
      </div>
      <div class="field">
        <label>{{ $t('语气') }}</label>
        <input v-model="form.brief.tone" data-test="brief-tone" :placeholder="$t('例如：像朋友下班后吐槽')" />
      </div>
      <div class="field" data-test="brief-avoid">
        <label>{{ $t('避免') }}</label>
        <div class="chips">
          <span v-for="v in form.brief.avoid" :key="v" class="pill" data-test="brief-chip" style="cursor:pointer" @click="dropAvoid(v)">{{ v }} ✕</span>
        </div>
        <div class="row" style="margin-top:4px">
          <input v-model="avoidInput" data-test="brief-chip-input" :placeholder="$t('用顿号或逗号分隔，回车添加')" @keyup.enter="addAvoid" />
          <button class="btn sm" data-test="brief-chip-add" @click="addAvoid">{{ $t('添加') }}</button>
        </div>
      </div>
    </div>

    <div class="card" data-test="script-speakers">
      <div class="row between">
        <h3>{{ $t('谁在说') }} <span class="hint">{{ form.speakers.length }}/{{ limits.speakers[1] }}</span></h3>
        <button class="btn sm" data-test="speaker-add" :disabled="form.speakers.length >= limits.speakers[1]" @click="addSpeaker">{{ $t('+ 加一位') }}</button>
      </div>
      <p class="hint">{{ $t('人设和立场决定每个人怎么说话：两个人观点一致到分不清谁在说，台词就不像真人了。这里的人是工作台自己的，投放时再一一映射到那边的主播或人物。') }}</p>
      <div v-for="(sp, i) in form.speakers" :key="sp.id" class="card" style="margin-top:8px;padding:10px 12px" data-test="speaker-row">
        <div class="row">
          <div class="field" style="flex:1">
            <label>{{ $t('名字') }}</label>
            <input v-model="sp.name" data-test="speaker-name" :placeholder="$t('例如：阿杰')" />
          </div>
          <div class="field" style="flex:2">
            <label>{{ $t('人设') }}</label>
            <input v-model="sp.persona" data-test="speaker-persona" :placeholder="$t('例如：大厂产品经理，说话慢')" />
          </div>
          <div class="field" style="flex:2">
            <label>{{ $t('立场') }}</label>
            <input v-model="sp.stance" data-test="speaker-stance" :placeholder="$t('例如：边界感是必须的')" />
          </div>
          <button class="btn sm" style="align-self:flex-end;margin-bottom:10px" data-test="speaker-remove" :disabled="form.speakers.length <= limits.speakers[0]" @click="dropSpeaker(i)">✕</button>
        </div>
      </div>
    </div>

    <div class="card" data-test="script-blocks">
      <div class="row between">
        <h3>{{ $t('段落骨架') }} <span class="hint">{{ form.blocks.length }}</span></h3>
        <button class="btn sm" data-test="block-add" @click="addBlock">{{ $t('+ 加一段') }}</button>
      </div>
      <p class="hint">{{ $t('一段对应播客的一个环节、短剧的一场或一镜，顺序就是播放顺序。可以先留一段，生成时让模型把骨架一起写出来。') }}</p>
      <div v-for="(b, i) in form.blocks" :key="b.id" class="row" style="margin-top:6px" data-test="block-row">
        <span class="pill mono" style="flex:none">{{ b.id }}</span>
        <div class="field" style="flex:1;margin-bottom:0">
          <input v-model="b.title" data-test="block-title" :placeholder="$t('这一段叫什么，例如：冷开场')" />
        </div>
        <div class="field" style="flex:2;margin-bottom:0">
          <input v-model="b.goal" data-test="block-goal" :placeholder="$t('这一段要达到什么，例如：用一个具体场景把人拽进来')" />
        </div>
        <!-- 段种类：播客稿才用得上（标成听友问答，检查才判回答那三条）；别的形态没有这个概念，不占位置 -->
        <select v-if="form.target === 'podcast'" v-model="b.kind" data-test="block-kind" style="flex:none;width:120px">
          <option v-for="k in BLOCK_KINDS" :key="k.id" :value="k.id">{{ $t(k.label) }}</option>
        </select>
        <button class="btn sm" data-test="block-remove" :disabled="form.blocks.length <= 1" @click="dropBlock(i)">✕</button>
      </div>
    </div>

    <div class="row" style="margin-top:10px">
      <button class="btn primary" data-test="script-create" @click="create">{{ $t('创建') }}</button>
      <button class="btn" @click="router.push('/script')">{{ $t('取消') }}</button>
      <span v-if="status" class="status">{{ status }}</span>
    </div>
  </div>
</template>
