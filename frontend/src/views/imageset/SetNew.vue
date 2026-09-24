<script setup>
// 新建图片集（docs/imageset-spec.md §5）：标题、模板（/templates 给说明 + 风格预设 + options_schema）、风格锚可自写、
// 主体：角色 = 名字 + 身份块 + 定妆参考图（≤ 6 张，base64）；真人 = 参考照片（婚纱模板每张标 who：新娘 / 新郎）+ 相似度；
// 模板选项按 schema 渲染（字符串清单 = 可增删的 chips、布尔、字符串、整数、枚举）。
// 建 = POST /api/{m} → 逐张 POST /assets → POST /expand（不调模型）→ 进清单页。
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { post, fileToBase64 } from '../../api.js'
import { roleOk, withBusy } from '../../store.js'
import { confirm, notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { moduleInfo, setApi, setUrl, setRoute, loadSets, loadTemplates, cachedModelCandidates, defaultOptions, templateLabel, templateDescription, choiceLabel } from '../../imageset.js'
import { isStyleCardId, loadStyleCards } from '../../stylecards.js'
import StyleCardPicker from '../../components/StyleCardPicker.vue'
import LocalTip from '../../components/LocalTip.vue'

const MAX_ASSETS = 6
const WHO = [{ id: 'bride', name: '新娘' }, { id: 'groom', name: '新郎' }]
const route = useRoute()
const router = useRouter()
const m = computed(() => route.meta.module || 'sticker')
const info = computed(() => moduleInfo(m.value))
const templates = ref([])
const form = reactive({ title: '', template: '', style: '', style_anchor: '', name: '', block: '', fidelity: 85, options: {} })
const assets = ref([])        // [{filename, data_base64, who, preview}]
const chipInput = reactive({})  // 每个清单字段的输入框
const status = ref('')

const tpl = computed(() => templates.value.find(x => x.id === form.template) || null)
// 主体种类：模板自己说了算，否则按模块（sticker = character，portrait = photo）
const kind = computed(() => tpl.value?.subject_kind || info.value.kind)
const isWedding = computed(() => form.template === 'wedding')
// 风格：模板自带的预设 id、风格卡 id 都原样发给后端；剩下的（含模板没有预设时）算自写
const isPreset = (id) => !!tpl.value?.styles?.some(s => s.id === id)
const custom = computed(() => !form.style || form.style === 'custom' || (!isPreset(form.style) && !isStyleCardId(form.style)))
// 「适合」筛选的判据：<模块>.<模板>
const fitsKey = computed(() => (form.template ? `${m.value}.${form.template}` : ''))
// 主体可留空的模块（图片工作台）：不催上传参考图，提示词里也不会写身份锁
const subjectOptional = computed(() => !!info.value.optionalSubject)
loadStyleCards()

// ---- 多模型对比（docs/imagestudio-spec.md §1、§6.5）：`models` 这个清单选项渲染成多选，候选由后端的
// `model_candidates` 给（设置里配好的 → 模型目录缓存 → 本机在跑的服务，后端只读缓存、不发请求），按来源分组显示；
// 后端什么都没有时它退回模板默认那几个。目录里没有的 id 照样能手写加进来（进「手写」那一组）。
const modelCandidates = ref([])
const SOURCE_GROUPS = [
  { source: 'configured', label: '已配置' }, { source: 'catalog', label: '模型目录' },
  { source: 'local', label: '本机' }, { source: 'default', label: '模板默认' },
]
const modelGroups = computed(() => {
  const known = new Map()
  for (const c of modelCandidates.value) if (c?.id && !known.has(c.id)) known.set(c.id, c)
  const groups = SOURCE_GROUPS
    .map(g => ({ ...g, rows: [...known.values()].filter(c => (c.source || 'catalog') === g.source) }))
    .filter(g => g.rows.length)
  // 手写加进来的、或老项目里存着的 id：单独一组，不会因为不在候选里就选不上
  const extra = (form.options.models || []).filter(id => !known.has(id)).map(id => ({ id, label: id }))
  if (extra.length) groups.push({ source: 'custom', label: '手写', rows: extra })
  return groups
})

watch(m, async (mod) => {
  templates.value = await loadTemplates(mod)
  modelCandidates.value = cachedModelCandidates(mod)
  form.template = templates.value[0]?.id || ''
  assets.value = []
}, { immediate: true })
// 换模板：风格预设与选项默认值跟着换；标题空着时用模板名
watch(tpl, (tp) => {
  if (!tp) return
  form.style = tp.styles[0]?.id || 'custom'
  form.style_anchor = tp.styles[0]?.anchor || ''
  form.options = defaultOptions(tp.options)
  for (const f of tp.options) chipInput[f.key] = ''
})
// 换风格：预设换成预设自带的锚句；换成风格卡时清空自写锚（锚句由卡给，见 docs/image-style-cards.md §4）；
// 换成 custom 不动（「改成自写」会把卡的锚句填进来当起点）
watch(() => form.style, (id) => {
  const s = tpl.value?.styles.find(x => x.id === id)
  if (s) { form.style_anchor = s.anchor || form.style_anchor; return }
  if (isStyleCardId(id)) form.style_anchor = ''
})

function addChip(f) {
  const v = String(chipInput[f.key] || '').split(/[,，;；\n]/).map(s => s.trim()).filter(Boolean)
  if (!v.length) return
  const list = form.options[f.key] || (form.options[f.key] = [])
  for (const x of v) if (!list.includes(x)) list.push(x)
  chipInput[f.key] = ''
}
function removeChip(f, i) { form.options[f.key].splice(i, 1) }

async function pickFiles(ev) {
  const files = Array.from(ev.target.files || []); ev.target.value = ''
  for (const file of files) {
    if (assets.value.length >= MAX_ASSETS) { await notice(t('参考图最多 {n} 张', { n: MAX_ASSETS })); break }
    try {
      const data_base64 = await fileToBase64(file)
      assets.value.push({ filename: file.name, data_base64, who: isWedding.value ? WHO[0].id : '', preview: `data:${file.type || 'image/png'};base64,${data_base64}` })
    } catch (e) { await notice(t('读取 {name} 失败：{msg}', { name: file.name, msg: e.message })) }
  }
}
function removeAsset(i) { assets.value.splice(i, 1) }

function optionsBody() {
  const out = {}
  for (const f of tpl.value?.options || []) {
    const v = form.options[f.key]
    if (f.type === 'list') out[f.key] = (v || []).map(s => String(s).trim()).filter(Boolean)
    else if (f.type === 'bool') out[f.key] = !!v
    else if (f.type === 'int') out[f.key] = (v === '' || v == null) ? null : Number(v)
    else out[f.key] = v == null ? '' : String(v)
  }
  return out
}
function subjectBody() {
  const s = { kind: kind.value, name: form.name.trim(), block: form.block.trim(), assets: [], fidelity: Number(form.fidelity) || 85 }
  if (kind.value === 'photo') s.name = form.name.trim()
  return s
}
async function create() {
  if (!form.title.trim()) { await notice(t('先起个标题')); return }
  if (!form.template) { await notice(t('先选模板')); return }
  if (kind.value === 'photo' && !subjectOptional.value && !assets.value.length && !await confirm(t('还没有上传参考照片——写真的提示词要「和参考照片是同一个人」，没有照片出来的不是本人。先建着、之后再传？'), { confirmLabel: t('先建着') })) return
  status.value = t('创建中…')
  await withBusy(async () => {
    let created
    try {
      created = await post(setApi(m.value), { title: form.title.trim(), template: form.template, subject: subjectBody(), style: custom.value ? 'custom' : form.style, style_anchor: form.style_anchor.trim(), options: optionsBody() })
    } catch (e) { status.value = ''; await notice(t('新建失败：{err}', { err: e.message })); return }
    const id = created?.id || created?.spec?.id
    // 传不上的照片把原因逐张摆出来：纪念写真多是手机照片，HEIC 这类后端会说「请在手机上导出为 JPG」——
    // 只报「N 张上传失败」的话，用户不知道换什么格式再传
    const failures = []
    for (const a of assets.value) {
      const body = { filename: a.filename, data_base64: a.data_base64 }
      if (isWedding.value) body.who = a.who
      try { await post(`${setUrl(m.value, id)}/assets`, body) } catch (e) { failures.push(t('{name}：{msg}', { name: a.filename, msg: e.message })) }
    }
    if (failures.length) await notice(t('{n} 张参考图没传上去，进项目后可以在「高级」里补：\n{lines}', { n: failures.length, lines: failures.join('\n') }))
    try { await post(`${setUrl(m.value, id)}/expand`, {}) } catch (e) { toast(t('清单没展开：{msg}', { msg: e.message }), { kind: 'warn', ttl: 6000 }) }
    await loadSets(m.value)
    status.value = ''
    toast(t('已新建「{name}」', { name: form.title.trim() }))
    router.push(setRoute(m.value, id))
  })
}
</script>

<template>
  <div class="page narrow" data-test="set-new" :data-module="m">
    <div class="card">
      <h3>{{ $t('新建') }} · {{ $t(info.name) }}</h3>
      <p class="hint" style="margin-bottom:10px">{{ $t(info.blurb) }}</p>
      <div v-if="!roleOk('image')" class="note" style="margin-bottom:10px">{{ $t('还没配置生图模型——') }}<router-link to="/settings">{{ $t('去「模型设置」填好') }}</router-link><LocalTip role="image" /></div>
      <div class="field"><label>{{ $t('标题') }}</label><input v-model="form.title" data-test="set-title" :placeholder="$t(info.titleHint || '如「小满表情包」')" /></div>
      <div class="grid2">
        <div class="field">
          <label>{{ $t('模板') }}</label>
          <select v-model="form.template" data-test="set-template"><option v-for="x in templates" :key="x.id" :value="x.id">{{ templateLabel(x) }}</option></select>
          <div class="hint" data-test="set-template-desc">{{ templateDescription(tpl) }}</div>
        </div>
      </div>
      <div class="field" v-if="tpl">
        <label>{{ $t('风格') }}</label>
        <StyleCardPicker v-model="form.style" :presets="tpl.styles" :fits="fitsKey" :anchor="form.style_anchor"
                         select-test="set-style" @update:anchor="v => form.style_anchor = v" />
      </div>
      <div class="field"><label>{{ $t('风格锚（会进每一张的提示词，可改）') }}</label><textarea v-model="form.style_anchor" data-test="set-anchor" rows="2"></textarea></div>
    </div>

    <div class="card" data-test="set-subject">
      <h3>{{ kind === 'photo' ? $t('主体：真人参考照') : $t('主体：角色') }}<span v-if="subjectOptional" class="hint"> · {{ $t('可留空') }}</span></h3>
      <p v-if="subjectOptional" class="hint" data-test="subject-optional">{{ $t('整块留空就只按画面描述出图——提示词里不会出现身份块，也不会写「画面里只有这个人」。') }}</p>
      <template v-if="kind === 'character'">
        <p class="hint">{{ $t('身份块写清发型 / 眼镜 / 衣着等标志物，逐张点名保证同一角色；定妆图作参考图进每次生图请求。') }}</p>
        <div class="field"><label>{{ $t('名字') }}</label><input v-model="form.name" data-test="subject-name" :placeholder="$t('如「小满」')" /></div>
        <div class="field"><label>{{ $t('身份块') }}</label><textarea v-model="form.block" data-test="subject-block" rows="2" :placeholder="$t('二十六七岁，齐肩黑发低马尾，圆框眼镜，米白工装外套')"></textarea></div>
      </template>
      <template v-else>
        <p class="hint">{{ $t('1–6 张正脸清晰照；提示词写「和参考照片是同一个人，相貌相似度约 N%」，不改脸型、不过度美白，婴儿口鼻不加饰品。') }}</p>
        <div class="field"><label>{{ $t('称呼（可选，进相册标题）') }}</label><input v-model="form.name" data-test="subject-name" /></div>
      </template>
      <div class="field">
        <label>{{ $t('与参考图相似度 {n}%', { n: form.fidelity }) }}</label>
        <input type="range" min="50" max="100" v-model="form.fidelity" data-test="subject-fidelity" />
      </div>
      <div class="row" style="align-items:flex-start">
        <div v-for="(a, i) in assets" :key="i" class="card" style="padding:8px;width:120px" data-test="asset-row">
          <img :src="a.preview" alt="" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:3px;display:block" />
          <div class="hint mono small" style="margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ a.filename }}</div>
          <select v-if="isWedding" v-model="a.who" data-test="asset-who" style="width:100%;margin-top:4px"><option v-for="w in WHO" :key="w.id" :value="w.id">{{ $t(w.name) }}</option></select>
          <button class="btn sm" style="margin-top:4px;width:100%" data-test="asset-remove" @click="removeAsset(i)">✕</button>
        </div>
        <label class="btn" style="cursor:pointer" v-if="assets.length < MAX_ASSETS">{{ kind === 'photo' ? $t('上传参考照片') : $t('上传定妆图') }}<input type="file" accept="image/*" multiple style="display:none" data-test="asset-file" @change="pickFiles" /></label>
        <span class="hint">{{ $t('{a}/{n} 张', { a: assets.length, n: MAX_ASSETS }) }}</span>
      </div>
    </div>

    <div class="card" v-if="tpl?.options?.length" data-test="set-options">
      <h3>{{ $t('模板选项') }}</h3>
      <div v-for="f in tpl.options" :key="f.key" class="field" :data-test="`opt-${f.key}`">
        <label>{{ f.label }}<span v-if="f.description" class="hint"> · {{ f.description }}</span></label>
        <template v-if="f.type === 'list' && f.key === 'models'">
          <select multiple size="8" v-model="form.options[f.key]" data-test="opt-model-select" style="width:100%">
            <optgroup v-for="g in modelGroups" :key="g.source" :label="$t(g.label)" data-test="opt-model-group" :data-source="g.source">
              <option v-for="c in g.rows" :key="c.id" :value="c.id">{{ c.label || c.id }}</option>
            </optgroup>
          </select>
          <div class="hint">{{ $t('按住 Ctrl / Cmd 多选（2–6 个）；@image = 设置里配好的生图模型。已选 {n} 个', { n: (form.options[f.key] || []).length }) }}</div>
          <div class="row" style="flex-wrap:nowrap">
            <input v-model="chipInput[f.key]" data-test="opt-chip-input" :placeholder="$t('目录里没有的模型 id，手写加进来')" @keydown.enter.prevent="addChip(f)" />
            <button class="btn sm" data-test="opt-chip-add" @click="addChip(f)">+</button>
          </div>
        </template>
        <template v-else-if="f.type === 'list'">
          <div class="chips" style="margin-bottom:4px">
            <span v-for="(c, i) in form.options[f.key]" :key="i" class="chip" data-test="opt-chip" @click="removeChip(f, i)" :title="$t('点一下删掉')">{{ c }} ✕</span>
          </div>
          <div class="row" style="flex-wrap:nowrap">
            <input v-model="chipInput[f.key]" data-test="opt-chip-input" :placeholder="$t('加一项，回车或逗号分隔')" @keydown.enter.prevent="addChip(f)" />
            <button class="btn sm" data-test="opt-chip-add" @click="addChip(f)">+</button>
          </div>
        </template>
        <label v-else-if="f.type === 'bool'" class="row small" style="gap:6px"><input type="checkbox" v-model="form.options[f.key]" data-test="opt-bool" /> {{ $t('开启') }}</label>
        <input v-else-if="f.type === 'int'" type="number" v-model="form.options[f.key]" :min="f.min" :max="f.max" data-test="opt-int" />
        <select v-else-if="f.type === 'choice'" v-model="form.options[f.key]" data-test="opt-choice">
          <option v-for="c in f.choices" :key="c.id ?? c" :value="c.id ?? c">{{ choiceLabel(c) }}</option>
        </select>
        <input v-else v-model="form.options[f.key]" data-test="opt-string" />
      </div>
    </div>

    <div class="row" style="margin-top:12px">
      <button class="btn primary" :disabled="!!status" data-test="set-create" @click="create">{{ $t('新建并展开清单') }}</button>
      <span class="status">{{ status }}</span>
      <span class="hint">{{ $t('新建与展开都不花钱；出图在清单页点。') }}</span>
    </div>
  </div>
</template>
