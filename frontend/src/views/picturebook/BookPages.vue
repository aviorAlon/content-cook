<script setup>
// 分页表编辑器（docs/picturebook-spec.md §7）：一行一页——画面描述（含出场角色、场景锚）、中英文、翻页钩、这一页教什么、KB 方向、翻页方式；
// 增删、上下移；每行缩略图（有插画时）+「重出这一页」；保存走 PUT 整本，返回的 lint 提醒就地显示。
import { computed, ref, watch } from 'vue'
import { put } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { pb, pageImageUrl, pageRow, KB_DIRS, FLIPS, PAGE_KINDS, newPage, hasPageImage, refreshBook, toCards, lintWarnings, runBookTask } from '../../picturebook.js'

const KIND_NAMES = { cover: '封面', page: '正文', back: '封底' }
const b = computed(() => store.book)
const s = computed(() => store.bookSummary)
const pages = ref([])
const dirty = ref(false)
const saveError = ref('')
const lint = ref(null)     // 最近一次保存返回的 lint（{warnings, notes}）；进页时先用 /lint 的
const clone = (x) => JSON.parse(JSON.stringify(x))
function sync() { pages.value = clone(b.value?.pages || []); dirty.value = false }
watch(() => b.value?.id, () => { sync(); lint.value = store.bookLint; saveError.value = '' }, { immediate: true })
// 别的页面刷新了书（任务完成等）而这里没改过：跟着刷新
watch(() => b.value?.pages, () => { if (!dirty.value) sync() })

const characters = computed(() => Object.entries(b.value?.characters || {}).map(([key, c]) => ({ key, name: c.name || key })))
const scenes = computed(() => Object.entries(b.value?.scenes || {}).map(([key, c]) => ({ key, name: c.name || key })))
const cards = computed(() => toCards(lint.value))
const rowCards = (pid) => cards.value.filter(c => c.target === pid)
const bookCards = computed(() => cards.value.filter(c => !c.target))
const stamp = computed(() => s.value?.updated_at || b.value?.updated_at || 0)
const zhMax = computed(() => ({ '2-4': 20, '4-6': 40, '6-8': 70 })[b.value?.age_band] || 0)
const zhLen = (text) => String(text || '').replace(/[\s，。！？、；：「」『』“”‘’,.!?;:'"()（）…—-]/g, '').length

function touch() { dirty.value = true }
function nextId() {
  const used = new Set(pages.value.map(p => p.id))
  for (let i = 1; i < 1000; i++) { const id = `P${String(i).padStart(2, '0')}`; if (!used.has(id)) return id }
  return `P${Date.now() % 1000}`
}
function add(after = pages.value.length - 1) {
  const p = newPage(pages.value.length + 1, pages.value.length ? 'page' : 'cover')
  p.id = nextId()
  // 新页的 KB 方向跟前一页错开（lint 要求相邻页不同）
  const prev = pages.value[after]
  if (prev && prev.kb?.dir === p.kb.dir) p.kb.dir = KB_DIRS.find(d => d !== prev.kb.dir)
  pages.value.splice(after + 1, 0, p); touch()
}
function remove(i) { pages.value.splice(i, 1); touch() }
function move(i, delta) {
  const j = i + delta; if (j < 0 || j >= pages.value.length) return
  const [row] = pages.value.splice(i, 1); pages.value.splice(j, 0, row); touch()
}
function toggleChar(p, key) {
  const list = p.characters || (p.characters = [])
  const i = list.indexOf(key); if (i >= 0) list.splice(i, 1); else list.push(key)
  touch()
}
function body() {
  const { output_root: _o, project_root: _r, ...spec } = b.value
  return { ...spec, pages: pages.value.map(p => ({ ...p, scene_ref: p.scene_ref || null, characters: p.characters || [] })) }
}
async function save() {
  saveError.value = ''
  return withBusy(async () => {
    try {
      const r = await put(pb(b.value.id), body())
      lint.value = r?.lint || (r?.warnings ? r : lint.value)
      dirty.value = false
      await refreshBook()
      const n = lintWarnings(r).length
      toast(n ? t('已保存，{n} 条检查提醒', { n }) : t('已保存'))
      return true
    } catch (e) { saveError.value = e.message; return false }
  })
}
async function redo(p) {
  if (dirty.value && !(await save())) return
  await runBookTask(`${pb(b.value.id)}/pages/generate`, { only: [p.id], force: true }, t('重出 {id}', { id: p.id }))
}
async function generateMissing() {
  if (dirty.value && !(await save())) return
  await runBookTask(`${pb(b.value.id)}/pages/generate`, {}, t('生成插画'))
}
</script>

<template>
  <div class="ws-content" v-if="b">
    <div class="ws-center" data-test="book-pages">
      <div class="row between" style="margin-bottom:8px">
        <div>
          <h3 style="font-size:16px">{{ $t('分页表') }} <span class="hint">{{ $t('{n} 页', { n: pages.length }) }}</span></h3>
          <p class="hint">{{ $t('正文页中文每页 ≤{n} 字；相邻两页的 KB 方向要错开；隔一两页留一个翻页钩，最后一页不留；每页只画一个瞬间，插画里不放字。', { n: zhMax }) }}</p>
        </div>
        <div class="row">
          <button class="btn sm" data-test="page-add" @click="add()">{{ $t('+ 加一页') }}</button>
          <button class="btn sm spend" :disabled="!!store.gen || !pages.length" @click="generateMissing">{{ $t('生成缺的插画') }}</button>
          <button class="btn primary" :disabled="!dirty || !!store.gen" data-test="pages-save" @click="save">{{ $t('保存') }}</button>
        </div>
      </div>
      <div v-if="saveError" class="note bad" style="margin-bottom:8px" data-test="pages-error">{{ saveError }}</div>
      <div v-if="bookCards.length" class="note" style="margin-bottom:8px" data-test="pages-lint-book">
        <div v-for="(c, i) in bookCards" :key="i" class="small">⚠ {{ c.impact }}<span v-if="c.fix" class="hint"> → {{ c.fix }}</span></div>
      </div>
      <div style="overflow-x:auto">
        <table class="tbl" style="min-width:1180px" data-test="pages-table">
          <thead><tr>
            <th style="width:70px">#</th><th style="width:96px">{{ $t('插画') }}</th><th style="min-width:220px">{{ $t('画面描述') }}</th>
            <th style="min-width:160px">{{ $t('中文') }}</th><th style="min-width:160px">English</th>
            <th style="width:130px">{{ $t('翻页钩') }}</th><th style="width:110px">{{ $t('教育点') }}</th>
            <th style="width:84px">{{ $t('KB 方向') }}</th><th style="width:84px">{{ $t('翻页') }}</th><th style="width:70px"></th>
          </tr></thead>
          <tbody>
            <template v-for="(p, i) in pages" :key="p.id">
              <tr data-test="page-row">
                <td>
                  <div class="mono small"><b>{{ p.id }}</b></div>
                  <select v-model="p.kind" @change="touch" style="width:100%;margin-top:4px"><option v-for="k in PAGE_KINDS" :key="k" :value="k">{{ $t(KIND_NAMES[k]) }}</option></select>
                </td>
                <td>
                  <img v-if="hasPageImage(p, s)" :src="pageImageUrl(b.id, p, stamp)" alt="" data-test="page-thumb" :title="pageRow(p.id)?.image_stale ? $t('提示词改过，这张图是旧的') : ''" style="width:72px;aspect-ratio:3/4;object-fit:cover;border-radius:3px;border:1px solid var(--border);display:block" />
                  <div v-else class="hint" style="width:72px;aspect-ratio:3/4;display:flex;align-items:center;justify-content:center;border:1px dashed var(--border);border-radius:3px" :title="pageRow(p.id)?.image_error || ''">{{ pageRow(p.id)?.image_error ? '⚠' : '—' }}</div>
                  <button class="btn sm spend" style="margin-top:4px;width:72px" :disabled="!!store.gen" data-test="page-redo" @click="redo(p)">{{ hasPageImage(p, s) ? $t('重出') : $t('出图') }}</button>
                </td>
                <td>
                  <textarea v-model="p.scene" @input="touch" rows="3" style="width:100%" data-test="page-scene" :placeholder="$t('谁、在哪、做什么，一个动作；构图（全身 / 中景 / 特写）')"></textarea>
                  <div class="chips" style="margin-top:4px">
                    <label v-for="c in characters" :key="c.key" class="pill" style="cursor:pointer"><input type="checkbox" :checked="(p.characters || []).includes(c.key)" @change="toggleChar(p, c.key)" style="margin:0 3px 0 0" />{{ c.name }}</label>
                    <select :value="p.scene_ref || ''" @change="p.scene_ref = $event.target.value || null; touch()" style="font-size:11px">
                      <option value="">{{ $t('（无场景锚）') }}</option>
                      <option v-for="sc in scenes" :key="sc.key" :value="sc.key">{{ sc.name }}</option>
                    </select>
                  </div>
                </td>
                <td>
                  <textarea v-model="p.zh" @input="touch" rows="3" style="width:100%" data-test="page-zh"></textarea>
                  <div class="hint mono" :class="{ bad: zhMax && p.kind === 'page' && zhLen(p.zh) > zhMax }">{{ zhLen(p.zh) }}{{ zhMax ? '/' + zhMax : '' }}</div>
                </td>
                <td><textarea v-model="p.en" @input="touch" rows="3" style="width:100%" data-test="page-en"></textarea></td>
                <td><input v-model="p.hook" @input="touch" style="width:100%" data-test="page-hook" :placeholder="p.kind === 'back' ? $t('（结尾页留空）') : $t('红袜子飞到哪儿去了？')" /></td>
                <td><input v-model="p.education" @input="touch" style="width:100%" :placeholder="$t('词汇:风/wind')" /></td>
                <td><select v-model="p.kb.dir" @change="touch" data-test="page-kb"><option v-for="d in KB_DIRS" :key="d" :value="d">{{ d }}</option></select></td>
                <td><select v-model="p.flip" @change="touch"><option v-for="f in FLIPS" :key="f" :value="f">{{ $t(f === 'cut' ? '硬切' : '翻页') }}</option></select></td>
                <td>
                  <div class="row" style="gap:2px;flex-wrap:nowrap">
                    <button class="btn sm" :disabled="i === 0" :title="$t('上移')" data-test="page-up" @click="move(i, -1)">↑</button>
                    <button class="btn sm" :disabled="i === pages.length - 1" :title="$t('下移')" data-test="page-down" @click="move(i, 1)">↓</button>
                    <button class="btn sm" :title="$t('删除这一页')" data-test="page-remove" @click="remove(i)">✕</button>
                  </div>
                  <button class="btn sm" style="margin-top:4px;width:100%" :title="$t('在这页后面插入')" @click="add(i)">+</button>
                </td>
              </tr>
              <tr v-if="rowCards(p.id).length" data-test="page-lint">
                <td colspan="10" style="border-top:none;padding-top:0">
                  <div v-for="(c, k) in rowCards(p.id)" :key="k" class="small" style="color:var(--warn)">⚠ {{ c.impact }}<span v-if="c.fix" class="hint"> → {{ c.fix }}</span></div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
      <p v-if="!pages.length" class="hint" style="margin-top:8px">{{ $t('还没有页。点「加一页」：第一页默认封面、最后一页默认封底。') }}</p>
    </div>
  </div>
</template>

