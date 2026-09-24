<script setup>
// 写（docs/scriptlab-spec.md §9）：当前采用的那一版逐段逐句编辑（说话人下拉、台词、cue、种类），加句 / 删句 / 上下移、
// 加段 / 删段；顶部估算与问题卡；「另存为新版本」把现在屏幕上的台词存成新的一版（source: "hand"），从不覆盖旧版。
// 段落骨架（标题 / 目标 / 备注）属于项目文件，改了走 PUT；台词属于版本，改了走 POST /versions。
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { post, put } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { confirm, notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import {
  sc, specBody, refreshScript, loadOptions, currentVersion, linesOf, nextBlockId, emptyBlock, emptyLine,
  estimateText, LINE_KINDS, CUES, BLOCK_KINDS,
} from '../../scriptlab.js'
// 问题卡分类名：全前端一份（problems.js，镜像 webui/problems.py 的 KIND_LABELS）。
// 这个模块复用的判据句子会落到 screenplay 这类没预料到的分类上，自己抄几个键就会把英文键名显示出来
import { problemKind } from '../../problems.js'

const s = computed(() => store.script)
const d = computed(() => store.scriptDetail)
const form = reactive({ blocks: null })
const skelDirty = ref(false)    // 段落骨架改过（标题 / 目标 / 备注 / 增删 / 顺序）→ PUT
const linesDirty = ref(false)   // 台词改过 → 另存为新版本
const saving = ref('')
const label = ref('')
const cues = ref(CUES)
const kinds = ref(Object.keys(LINE_KINDS))
// 正在看的哪一版：平时跟着「采用中」的那一版走；「另存为新版本」之后停在新出的那一版上——
// 第二版起新版本按设计不自动采用（第一版例外，契约 §3），不记住看的是哪一版的话，保存成功刷新完就把屏幕上刚写的台词换回旧版了
const viewingId = ref('')

const currentId = computed(() => String(d.value?.spec?.current ?? s.value?.current ?? ''))
const versionList = computed(() => s.value?.versions || d.value?.spec?.versions || [])
const version = computed(() => (viewingId.value && versionList.value.find(v => v.id === viewingId.value)) || currentVersion(d.value, s.value))
const speakers = computed(() => s.value?.speakers || [])
const estimate = computed(() => d.value?.estimate || null)
const problems = computed(() => d.value?.problems || [])
const dirty = computed(() => skelDirty.value || linesDirty.value)
// 起草任务在跑的时候不许保存：它写盘出的新版本在最近一次 GET 之后，这时 PUT 整份会把新版本从版本表里抹掉
const busyDraft = computed(() => !!d.value?.busy?.draft)

// 段落骨架 + 当前版本的台词拼成一张表；保存时再拆回两边
function sync() {
  if (!s.value) return
  const v = version.value
  form.blocks = (s.value.blocks || []).map(b => ({
    id: b.id, kind: b.kind || 'talk', title: b.title || '', goal: b.goal || '', note: b.note || '',
    lines: linesOf(v, b.id),
  }))
  skelDirty.value = false
  linesDirty.value = false
}
// 换了项目、或者在「试」页采用了别的版本 → 不再停在旧的那一版上，回到跟着「采用中」走
watch([() => s.value?.id, () => String(d.value?.spec?.current ?? s.value?.current ?? '')], () => { viewingId.value = '' })
watch([s, version], () => { if (!dirty.value) sync() }, { immediate: true })
onMounted(async () => { const o = await loadOptions(); cues.value = o.cues; kinds.value = o.kinds })

// 审稿卡里的段号 → 本页的段卡（hash 锚点 #block-B01）；hash 路由下浏览器不会自己跳，这里手动滚
const route = useRoute()
function scrollToHash(hash) {
  if (!hash?.startsWith('#block-') || typeof document === 'undefined') return
  document.getElementById(hash.slice(1))?.scrollIntoView?.({ block: 'start' })
}
// 挂载之后才找得到元素：immediate 的回调是在 setup 里同步跑的，那时候段卡还没渲染出来（从「试」页点段号过来正是这条路径）
onMounted(() => nextTick(() => scrollToHash(route.hash)))
watch(() => route.hash, scrollToHash, { flush: 'post' })

function touchSkel() { skelDirty.value = true }
function touchLines() { linesDirty.value = true }

// 台词的可保存形状：空 cue 与默认种类不落盘（跟播客同一规矩）
function lineBody(l) {
  const out = { who: l.who, text: String(l.text || '') }
  if (l.cue) out.cue = l.cue
  if (l.kind && l.kind !== 'talk') out.kind = l.kind
  return out
}

async function saveSkeleton() {
  saving.value = t('保存中…')
  // 删过的段也要从每一版里剔掉：后端要求每一版的段都在骨架里（那是「段号写错」的防线），而 GET 又把每一版按骨架
  // 补齐了所有段——版本原样带回去，只要项目有任何一版，删段的 PUT 就必撞「版本 V1 里的段「B02」不在段落骨架里」的 400
  const keep = new Set(form.blocks.map(b => b.id))
  const base = specBody(s.value)
  const body = {
    ...base,
    blocks: form.blocks.map(b => ({ id: b.id, kind: b.kind, title: b.title, goal: b.goal, note: b.note })),
    versions: (base.versions || []).map(v => ({ ...v, blocks: (v.blocks || []).filter(b => keep.has(b.id)) })),
  }
  let ok = false
  await withBusy(async () => {
    try { await put(sc(s.value.id), body); skelDirty.value = false; await refreshScript(); saving.value = ''; ok = true; toast(t('段落骨架已保存')) }
    catch (e) { saving.value = ''; await notice(e.message) }
  })
  return ok
}

// 另存为新版本：不覆盖任何旧版；项目还没采用任何一版时后端直接采用它，否则不自动顶替（去「试」页挑）
async function saveVersion() {
  if (skelDirty.value && !(await saveSkeleton())) return
  saving.value = t('保存中…')
  const body = { blocks: form.blocks.map(b => ({ id: b.id, lines: b.lines.map(lineBody) })) }
  const name = label.value.trim()
  if (name) body.label = name
  if (version.value?.id) body.from_version = version.value.id
  await withBusy(async () => {
    try {
      const r = await post(`${sc(s.value.id)}/versions`, body)
      // 停在刚存出来的那一版上：第二版起它不会自动采用，不这么记一下刷新完屏幕上就换回旧版了
      const vid = String(r?.version || r?.id || '')
      viewingId.value = vid
      linesDirty.value = false
      label.value = ''
      await refreshScript()
      saving.value = ''
      // 项目还没采用任何一版时后端会直接采用这一版（adopt_if_first，契约 §3）：toast 按刷新后的 current 说话，
      // 别对着已经采用的第一版说「旧版还在，去试页挑」
      toast(vid && currentId.value === vid
        ? t('已存成 {v}；项目之前没采用任何一版，这一版已直接采用', { v: vid })
        : t('已存成新的一版 {v}（旧版还在，去「试」页挑）', { v: vid }))
    } catch (e) { saving.value = ''; await notice(e.message) }
  })
}

// ---- 段增删移动
function addBlock() {
  // 段号要连已存的骨架一起往后排：删了 B02 还没保存就加一段，只看表单会又拿到 B02——saveSkeleton 按表单里剩下的段号
  // 过滤各版，被删那段的旧台词就会原样留在每一版里、挂到这个新段下（确认框刚说完「都会一起没了」），下次投放还会被带出去
  form.blocks.push({ ...emptyBlock(nextBlockId([...form.blocks, ...(s.value?.blocks || [])])), lines: [] })
  touchSkel()
}
async function removeBlock(i) {
  const b = form.blocks[i]
  // 骨架是项目级的：删段会把这一段从**每一版**里抹掉（saveSkeleton 按剩下的段过滤 versions，后端各版也按骨架对齐），
  // 所以数的是全部版本里的句数，再加上屏幕上刚写、还没另存的那几句；不能说「旧版本不受影响」——做不到
  const saved = versionList.value.reduce((sum, v) => sum + linesOf(v, b.id).length, 0)
  const unsaved = Math.max(0, (b.lines?.length || 0) - linesOf(version.value, b.id).length)
  const n = saved + unsaved
  const m = versionList.value.length
  // 一版都还没有、句子全是屏幕上刚写的：别说「全部 0 个版本」
  const msg = m
    ? t('删掉「{title}」？这一段在全部 {m} 个版本里的 {n} 句台词都会一起没了（骨架是项目级的，删段影响每一版），找不回来。', { title: b.title || b.id, m, n })
    : t('删掉「{title}」？这一段刚写的 {n} 句台词（还没另存）会一起没了，找不回来。', { title: b.title || b.id, n })
  if (n && !(await confirm(msg, { confirmLabel: t('删除'), danger: true }))) return
  form.blocks.splice(i, 1)
  touchSkel()
  touchLines()
}
function moveBlock(i, delta) {
  const j = i + delta
  if (j < 0 || j >= form.blocks.length) return
  const [b] = form.blocks.splice(i, 1)
  form.blocks.splice(j, 0, b)
  touchSkel()
}
// ---- 句增删移动：默认换一个人说（真人对话不会一个人连着念）
function addLine(block, after = null) {
  const prev = after === null ? block.lines[block.lines.length - 1] : block.lines[after]
  const ids = speakers.value.map(x => x.id)
  const next = prev && ids.length > 1 ? ids[(ids.indexOf(prev.who) + 1) % ids.length] : (ids[0] || '')
  const line = emptyLine(next)
  if (after === null) block.lines.push(line); else block.lines.splice(after + 1, 0, line)
  touchLines()
}
function removeLine(block, i) { block.lines.splice(i, 1); touchLines() }
function moveLine(block, i, delta) {
  const j = i + delta
  if (j < 0 || j >= block.lines.length) return
  const [l] = block.lines.splice(i, 1)
  block.lines.splice(j, 0, l)
  touchLines()
}
</script>

<template>
  <div class="ws-content" v-if="s && form.blocks">
    <div class="ws-center" style="max-width:960px" data-test="script-write">
      <div class="card">
        <div class="row between">
          <h3>
            {{ $t('写') }}
            <span class="pill mono" v-if="version" data-test="write-version">{{ version.id }}</span>
            <span class="pill" v-if="version && version.id !== currentId" data-test="write-not-current">{{ $t('这一版还没采用') }}</span>
          </h3>
          <div class="row">
            <span v-if="saving" class="status">{{ saving }}</span>
            <button class="btn sm" :disabled="!skelDirty || busyDraft" data-test="skeleton-save" @click="saveSkeleton">{{ $t('保存段落骨架') }}</button>
          </div>
        </div>
        <p class="hint">{{ $t('这里改的是当前采用的那一版。改完点「另存为新版本」：旧版一句不动，新的一版排在后面，要不要采用去「试」页挑。段落骨架（标题 / 目标 / 备注）是项目本身的，改了单独保存。') }}</p>
        <div v-if="estimate" class="hint" style="margin-top:8px" data-test="write-estimate">{{ estimateText(estimate) }}</div>
        <div v-if="!version" class="note" data-test="write-no-version">{{ $t('还没有任何一版台词。去「试」页点「生成一版」，或者在这里手写完再「另存为新版本」。') }}</div>
        <div v-if="busyDraft" class="note" data-test="write-busy">{{ $t('正在生成新的一版，先等它写完——这会儿保存会把它从版本表里抹掉。') }}</div>
        <div class="row" style="margin-top:8px;align-items:flex-end">
          <div class="field" style="flex:1;margin-bottom:0">
            <label>{{ $t('新版本的名字（可空）') }}</label>
            <input v-model="label" data-test="version-label" :placeholder="$t('例如：阿杰更硬气的一版')" />
          </div>
          <button class="btn primary" :disabled="busyDraft" data-test="version-save" @click="saveVersion">{{ $t('另存为新版本') }}</button>
        </div>
      </div>

      <!-- 问题卡（本地检查，不花钱） -->
      <div v-if="problems.length" class="card" data-test="write-problems">
        <h3>{{ $t('{n} 条检查提醒（不阻止投放，但通常值得看一眼）', { n: problems.length }) }}</h3>
        <div v-for="(c, i) in problems" :key="i" class="small" style="margin:4px 0;display:flex;gap:6px;align-items:flex-start" data-test="write-problem">
          <span class="pill mono" style="flex-shrink:0" data-test="problem-target">{{ c.scope === 'block' && c.target ? c.target : $t('全篇') }}</span>
          <span class="pill" style="flex-shrink:0">{{ $t(problemKind(c.kind)) }}</span>
          <span><b>{{ c.impact }}</b><template v-if="c.fix"> <span class="hint">→ {{ c.fix }}</span></template></span>
        </div>
      </div>

      <!-- 段 -->
      <div v-for="(b, i) in form.blocks" :key="b.id" :id="`block-${b.id}`" class="card" data-test="block">
        <div class="row between">
          <h3><span class="mono hint">{{ b.id }}</span> {{ b.title || $t('（没起名的一段）') }}</h3>
          <div class="row">
            <button class="btn sm" :disabled="i === 0" data-test="block-up" :title="$t('上移')" @click="moveBlock(i, -1)">↑</button>
            <button class="btn sm" :disabled="i === form.blocks.length - 1" data-test="block-down" :title="$t('下移')" @click="moveBlock(i, 1)">↓</button>
            <button class="btn sm danger" data-test="block-remove" @click="removeBlock(i)">{{ $t('删除') }}</button>
          </div>
        </div>
        <div class="row">
          <div class="field" style="flex:1">
            <label>{{ $t('标题') }}</label>
            <input v-model="b.title" data-test="block-title" @input="touchSkel" />
          </div>
          <!-- 段种类：只有播客稿用得上。标成听友问答，检查才判「回答太短 / 复述问题 / 没标回答」那三条 -->
          <div class="field" style="flex:none;width:130px" v-if="s.target === 'podcast'">
            <label>{{ $t('段种类') }}</label>
            <select v-model="b.kind" data-test="block-kind" @change="touchSkel">
              <option v-for="k in BLOCK_KINDS" :key="k.id" :value="k.id">{{ $t(k.label) }}</option>
            </select>
          </div>
          <div class="field" style="flex:2">
            <label>{{ $t('这一段要达到什么') }}</label>
            <input v-model="b.goal" data-test="block-goal" :placeholder="$t('例如：用一个具体场景把人拽进来')" @input="touchSkel" />
          </div>
          <div class="field" style="flex:2">
            <label>{{ $t('给模型的额外提示（可空）') }}</label>
            <input v-model="b.note" data-test="block-note" @input="touchSkel" />
          </div>
        </div>
        <div class="field">
          <label>{{ $t('台词') }} <span class="hint">{{ b.lines.length }}</span></label>
          <div v-if="!b.lines.length" class="hint">{{ $t('这一段还没有台词。去「试」页生成，或者「+ 加一句」手写。') }}</div>
          <div v-for="(l, li) in b.lines" :key="li" class="row" style="margin-top:4px;align-items:center" data-test="line-row">
            <select v-model="l.who" data-test="line-who" style="width:110px" @change="touchLines">
              <!-- 从项目导入带进来的、或者角色表里删过的人：列出来让人看得见、改得掉，不然下拉空白但坏 id 还在，存的时候后端才报错 -->
              <option v-if="l.who && !speakers.some(sp => sp.id === l.who)" :value="l.who" data-test="line-who-unknown">{{ l.who }}{{ $t('（这个人不在角色表里）') }}</option>
              <option v-for="sp in speakers" :key="sp.id" :value="sp.id">{{ sp.name || sp.id }}</option>
            </select>
            <input v-model="l.text" data-test="line-text" style="flex:1" @input="touchLines" />
            <select v-model="l.cue" data-test="line-cue" style="width:90px" :title="$t('cue：只标 TTS 与剪辑能用的')" @change="touchLines">
              <option value="">{{ $t('（无）') }}</option>
              <option v-for="c in cues" :key="c" :value="c">{{ $t(c) }}</option>
            </select>
            <select v-model="l.kind" data-test="line-kind" style="width:80px" @change="touchLines">
              <option v-for="k in kinds" :key="k" :value="k">{{ $t(LINE_KINDS[k] || k) }}</option>
            </select>
            <button class="btn sm" :disabled="li === 0" data-test="line-up" :title="$t('上移')" @click="moveLine(b, li, -1)">↑</button>
            <button class="btn sm" :disabled="li === b.lines.length - 1" data-test="line-down" :title="$t('下移')" @click="moveLine(b, li, 1)">↓</button>
            <button class="btn sm" :disabled="!speakers.length" data-test="line-insert" :title="$t('在这句后面插一句')" @click="addLine(b, li)">+</button>
            <button class="btn sm" data-test="line-remove" :title="$t('删掉这句')" @click="removeLine(b, li)">✕</button>
          </div>
          <button class="btn sm" style="margin-top:6px" :disabled="!speakers.length" data-test="line-add" @click="addLine(b)">{{ $t('+ 加一句') }}</button>
          <div v-if="!speakers.length" class="hint small" data-test="write-no-speaker">{{ $t('角色表是空的，加不了句：新建剧本时填「谁在说」，或者在首页用「从现有项目导入」把那边的主播 / 人物带进来。') }}</div>
        </div>
      </div>

      <div class="row" style="margin-top:6px">
        <button class="btn" data-test="block-add" @click="addBlock">{{ $t('+ 加一段') }}</button>
        <button class="btn primary" :disabled="busyDraft" data-test="version-save-bottom" @click="saveVersion">{{ $t('另存为新版本') }}</button>
      </div>
      <p class="hint" v-if="!form.blocks.length">{{ $t('还没有段落。先「+ 加一段」写好标题和目标，再去「试」页生成台词。') }}</p>
    </div>
  </div>
</template>
