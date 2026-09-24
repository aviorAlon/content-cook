<script setup>
// 用（docs/scriptlab-spec.md §9）：选目标（播客期 / 短剧集）→ 每段一个落点下拉（环节 / 场次 / 镜号 / 新建）→
// 每个说话人一个角色映射下拉 →「投放」，把当前采用的那一版台词写进那边的项目；下面列已投放去向。
// 只投放 current 指向的那一版；剧集已锁定（prepare 过）的分镜落点由后端 400 拦，这里也提前提示。
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { post } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import {
  sc, refreshScript, loadTargets, targetRows, spotsOf, useModule, currentVersion, linesOf,
  USE_MODULES,
} from '../../scriptlab.js'

const MODES = { replace: '整段替换', append: '追加在后面' }

const s = computed(() => store.script)
const d = computed(() => store.scriptDetail)
// 投的是 current 指向的那一版，严格按 current 取：第一版会自动采用，但 current 仍可能是空的（整体保存时清掉了，契约 §3），
// 「有版本但还没挑一版」这时候后端收到的 current 是空的——不能像 currentVersion() 那样
// 回落到最后一版，否则界面上摆着版本号、「投放」是亮的，投过去却对不上屏幕上写的那一版。
const current = computed(() => String(d.value?.spec?.current ?? s.value?.current ?? ''))
const version = computed(() => {
  if (!current.value) return null
  const cur = currentVersion(d.value, s.value)      // 详情给的 current_version 带 blocks，优先用它
  if (cur?.id === current.value) return cur
  return (s.value?.versions || d.value?.spec?.versions || []).find(v => v.id === current.value) || null
})
const speakers = computed(() => s.value?.speakers || [])
const blocks = computed(() => s.value?.blocks || [])
const usedIn = computed(() => d.value?.used_in || s.value?.used_in || [])

const targets = ref({ podcast: [], drama: [] })
// 默认按项目的目标形态选去处（generic 没有对应的去处，落到播客）
const drop = reactive({ module: 'podcast', targetId: '', mode: 'replace', blocks: {}, mapping: {} })
const status = ref('')

onMounted(async () => {
  targets.value = await loadTargets()
  const init = USE_MODULES.find(m => m.id === s.value?.target)
  if (init) drop.module = init.id
})

const mod = computed(() => useModule(drop.module))
const rows = computed(() => targetRows(targets.value, drop.module))
const row = computed(() => rows.value.find(x => x.id === drop.targetId) || null)
const spots = computed(() => spotsOf(row.value, drop.module))
const roles = computed(() => row.value?.roles || [])
const lockedShot = computed(() => drop.module === 'drama_shot' && !!row.value?.locked)

// 换目标就重置落点与映射；映射先按同名对上，对不上按顺序填
watch([() => drop.module, () => drop.targetId], () => {
  drop.blocks = {}
  drop.mapping = {}
  const list = roles.value
  speakers.value.forEach((sp, i) => {
    const byName = list.find(r => r.name && r.name === sp.name)
    drop.mapping[sp.id] = byName?.id || list[i]?.id || ''
  })
})

const chosenBlocks = computed(() => Object.entries(drop.blocks).filter(([, v]) => v).map(([k, v]) => [k, v]))
const missingRole = computed(() => speakers.value.filter(sp => !drop.mapping[sp.id]).map(sp => sp.name || sp.id))
const canSubmit = computed(() => !!current.value && !!row.value && chosenBlocks.value.length > 0 && missingRole.value.length === 0 && !lockedShot.value)
const lineCount = (id) => linesOf(version.value, id).length

async function submit() {
  if (!canSubmit.value) { status.value = t('先选目标、落点和角色映射'); return }
  const body = {
    module: drop.module,
    target_id: row.value.id,
    blocks: Object.fromEntries(chosenBlocks.value),
    mapping: { ...drop.mapping },
    // 三种去处都带 mode：后端对环节 / 场次 / 镜都支持 replace / append，以前只给播客发，短剧就静默按 replace 把落点原有的台词整段覆盖了
    mode: drop.mode,
  }
  status.value = t('投放中…')
  await withBusy(async () => {
    try {
      const r = await post(`${sc(s.value.id)}/use`, body)
      status.value = ''
      toast(r?.message || t('已放进《{name}》', { name: r?.target_title || row.value.title }), { ttl: 6000 })
      targets.value = await loadTargets()
      await refreshScript()
    } catch (e) { status.value = ''; await notice(e.message) }
  })
}

const moduleLabel = (id) => t(useModule(id).label)
</script>

<template>
  <div class="ws-content" v-if="s">
    <div class="ws-center" style="max-width:980px" data-test="script-use">
      <div class="card">
        <h3>{{ $t('投放') }} <span class="pill mono" v-if="version" data-test="use-version">{{ version.id }}</span></h3>
        <p class="hint">{{ $t('投的是当前采用的那一版。段落一段一段落到那边的环节 / 场次 / 镜号上，说话人一一映射到那边的主播或人物。原项目改台词之后要不要重跑（配音过时、分镜要重来）按各模块自己的规矩来，工作台只负责写进去。') }}</p>
        <!-- 还没采用任何一版（一版都没有，或 current 被清空了）：这时候投过去是空的，先去「试」页挑一版 -->
        <div v-if="!current" class="note" data-test="use-no-version">{{ $t('还没采用任何一版，投过去会是空的。去「试」页挑一版（或者在「比」页比过再挑）。') }}</div>
        <div class="row" style="margin-top:6px">
          <div class="field">
            <label>{{ $t('投到哪儿') }}</label>
            <!-- 换去处就把项目也清掉：上一个模块选的项目 id 在新的清单里没有，下拉会显示空白而不是占位 -->
            <select v-model="drop.module" data-test="use-module" @change="drop.targetId = ''">
              <option v-for="m in USE_MODULES" :key="m.id" :value="m.id">{{ $t(m.label) }}</option>
            </select>
          </div>
          <div class="field" style="flex:1">
            <label>{{ $t('哪个项目') }}</label>
            <select v-model="drop.targetId" data-test="use-target">
              <option value="">{{ $t('（选一个项目）') }}</option>
              <option v-for="x in rows" :key="x.id" :value="x.id">{{ x.title }}（{{ x.id }}）{{ x.locked ? $t('（已开始制作）') : '' }}</option>
            </select>
          </div>
          <!-- 三种去处都给选：目标那边没有版本留痕，整段替换掉的台词找不回来，不能只让播客选、短剧默认替换 -->
          <div class="field" data-test="use-mode-field">
            <label>{{ $t('怎么写进去') }}</label>
            <select v-model="drop.mode" data-test="use-mode">
              <option v-for="(lab, k) in MODES" :key="k" :value="k">{{ $t(lab) }}</option>
            </select>
          </div>
        </div>
        <div v-if="lockedShot" class="note" data-test="use-locked">{{ $t('这一集已经开始制作，台词是生成字段——去项目里走改写重出，或者复制一个新 revision。') }}</div>
        <div v-if="!rows.length" class="hint" data-test="use-no-target">{{ $t('{name}那边还没有项目可投。', { name: moduleLabel(drop.module) }) }}</div>
      </div>

      <!-- 段落落点 -->
      <div class="card" v-if="row" data-test="use-blocks">
        <h3>{{ $t('每段放到哪个{spot}', { spot: $t(mod.spot) }) }}</h3>
        <p class="hint">{{ $t('不选就是不投这一段。') }}</p>
        <div v-for="b in blocks" :key="b.id" class="row" style="margin-top:6px;align-items:center" data-test="use-block-row">
          <span class="pill mono" style="flex:none">{{ b.id }}</span>
          <span style="flex:1;min-width:0">{{ b.title || $t('（没起名的一段）') }} <span class="hint small">{{ $t('{n} 句', { n: lineCount(b.id) }) }}</span></span>
          <select v-model="drop.blocks[b.id]" :data-test="`use-spot-${b.id}`" style="width:260px">
            <option value="">{{ $t('（不投这一段）') }}</option>
            <option v-if="mod.canNew" value="new">{{ $t('新建一个{spot}', { spot: $t(mod.spot) }) }}</option>
            <!-- 落点已有台词时按所选方式说清后果，不说「按上面的方式」这种要人抬头找的话 -->
            <option v-for="sp in spots" :key="sp.id" :value="sp.id">{{ sp.id }}{{ sp.title ? ` · ${sp.title}` : '' }}{{ sp.lines ? (drop.mode === 'append' ? $t('（已有 {n} 句，会追加在后面）', { n: sp.lines }) : $t('（已有 {n} 句，会被整段替换）', { n: sp.lines })) : '' }}</option>
          </select>
        </div>
        <p v-if="!blocks.length" class="hint">{{ $t('还没有段落。') }}</p>
      </div>

      <!-- 角色映射 -->
      <div class="card" v-if="row" data-test="use-mapping">
        <h3>{{ $t('角色映射') }}</h3>
        <p class="hint">{{ $t('工作台里的每个说话人都要对上那边的一个人，缺一个就投不了。') }}</p>
        <div v-for="sp in speakers" :key="sp.id" class="row" style="margin-top:6px;align-items:center" data-test="use-map-row">
          <span style="width:160px;flex:none"><b>{{ sp.name || sp.id }}</b> <span class="hint small mono">{{ sp.id }}</span></span>
          <span class="hint">→</span>
          <select v-model="drop.mapping[sp.id]" :data-test="`use-map-${sp.id}`" style="width:260px">
            <option value="">{{ $t('（还没对上）') }}</option>
            <option v-for="r in roles" :key="r.id" :value="r.id">{{ r.name || r.id }}</option>
          </select>
        </div>
        <p v-if="!roles.length" class="hint" data-test="use-no-roles">{{ $t('那边的项目还没有人物 / 主播，先去那边补上再回来投放。') }}</p>
        <div class="row" style="margin-top:10px">
          <button class="btn primary" :disabled="!canSubmit" data-test="use-submit" @click="submit">{{ $t('投放') }}</button>
          <span v-if="status" class="status" data-test="use-status">{{ status }}</span>
          <span v-else-if="missingRole.length" class="hint small" data-test="use-missing">{{ $t('还没对上：{names}', { names: missingRole.join('、') }) }}</span>
        </div>
      </div>

      <!-- 已投放去向 -->
      <div class="card" data-test="use-history">
        <h3>{{ $t('已投放去向') }} <span class="hint">{{ usedIn.length }}</span></h3>
        <p v-if="!usedIn.length" class="hint">{{ $t('还没投放过。') }}</p>
        <div class="chips" v-else>
          <span v-for="(u, i) in usedIn" :key="i" class="pill" data-test="use-history-row">
            {{ moduleLabel(u.module) }} · {{ u.id }} · {{ u.target }}<span v-if="u.at"> · {{ String(u.at).replace('T', ' ').slice(0, 16) }}</span>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
