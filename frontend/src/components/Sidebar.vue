<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { store, loadEpisodes, moduleById } from '../store.js'
import { del, ep } from '../api.js'
import { archiveBook, progressOf } from '../picturebook.js'
import { IMAGESET_MODULES, archiveSet, progressText, setRoute } from '../imageset.js'
import { archiveClip, clipRoute, progressText as clipProgressText } from '../clip.js'
import { archiveMusic, musicRoute, progressText as musicProgressText } from '../music.js'
import { archivePodcast, podcastRoute, progressText as podcastProgressText } from '../podcast.js'
import { archiveScript, scriptRoute, progressText as scriptProgressText } from '../scriptlab.js'
import { confirm, notice, toast } from '../ui.js'
import { t, lang, setLang, LANGS } from '../i18n.js'

const route = useRoute()
const router = useRouter()

// ---- 模块手风琴（各模块的按钮 + 列表一路往下堆会很难看）
// 侧栏只列「可用」的模块，一次只展开一个：跟着路由走（在 /drama 下就展开短剧），别的模块收成一行（图标 + 名字 + 数量）。
// 列表短的时候搜索框只是噪音，侧栏也显得太重；到这个条数才出现
const SEARCH_MIN = 8
const MODULE_LABEL = { drama: '短剧', picturebook: '绘本', sticker: '表情与头像', portrait: '纪念写真', image: '图片工作台', clip: '单条视频', music: '配乐', podcast: 'AI 播客', script: '剧本工作台' }
const MODULE_HOME = { drama: '/drama', picturebook: '/picturebook', sticker: '/sticker', portrait: '/portrait', image: '/image', clip: '/clip', music: '/music', podcast: '/podcast', script: '/script' }
const readyModules = computed(() => {
  const list = (store.modules || []).filter(m => m.available && MODULE_HOME[m.id])
  if (!list.some(m => m.id === 'drama')) list.unshift({ id: 'drama', name: '短剧', icon: '🎬', available: true })
  return list
})
const routeModule = computed(() => {
  for (const id of Object.keys(MODULE_HOME)) if (route.path === MODULE_HOME[id] || route.path.startsWith(MODULE_HOME[id] + '/')) return id
  return null
})
function storedOpen() { try { return localStorage.getItem('sidebar.open') || null } catch { return null } }
const open = ref(routeModule.value || storedOpen() || 'drama')
watch(routeModule, (v) => { if (v) open.value = v })
watch(open, (v) => { try { if (v) localStorage.setItem('sidebar.open', v) } catch { /* 隐私模式等 */ } })
function toggle(id) { open.value = open.value === id ? null : id }
const moduleLabel = (m) => t(MODULE_LABEL[m.id] || m.name)
function countOf(id) {
  if (id === 'drama') return dramaAll.value.length
  if (id === 'picturebook') return (store.books || []).length
  if (id === 'clip') return (store.clips || []).length
  if (id === 'music') return (store.musics || []).length
  if (id === 'podcast') return (store.podcasts || []).length
  if (id === 'script') return (store.scripts || []).length
  if (IMAGESET_MODULES[id]) return (store.sets[id] || []).length
  return ''
}

// ---- 配乐（docs/music-spec.md §5）：列表来自 GET /api/music（store.musics）
const mq = ref('')
const musics = computed(() => {
  const s = mq.value.trim().toLowerCase()
  return (store.musics || []).filter(r => !s || String(r.id || '').toLowerCase().includes(s) || String(r.title || '').toLowerCase().includes(s))
})
const currentMusicId = computed(() => (route.path.startsWith('/music/m/') && route.params.id) || null)
const musicSub = (r) => `${r.id} · ${musicProgressText(r)}`
async function removeMusic(r, event) {
  event.preventDefault(); event.stopPropagation()
  const ok = await confirm(t('删除「{name}」？\n\n项目文件和生成过的产物会一起收进回收站，删除后可在「回收站」里恢复。', { name: r.title || r.id }), { confirmLabel: t('删除'), danger: true })
  if (!ok) return
  try {
    await archiveMusic(r.id)
    toast(t('已删除「{name}」（可在「回收站」里恢复）', { name: r.title || r.id }))
    if (currentMusicId.value === r.id) router.push('/music')
  } catch (err) { await notice(err.message) }
}

// ---- AI 播客（docs/podcast-spec.md §6）：列表来自 GET /api/podcast（store.podcasts）
const pq = ref('')
const podcasts = computed(() => {
  const s = pq.value.trim().toLowerCase()
  return (store.podcasts || []).filter(r => !s || String(r.id || '').toLowerCase().includes(s) || String(r.title || '').toLowerCase().includes(s))
})
const currentPodcastId = computed(() => (route.path.startsWith('/podcast/p/') && route.params.id) || null)
const podcastSub = (r) => `${r.id} · ${podcastProgressText(r)}`
async function removePodcast(r, event) {
  event.preventDefault(); event.stopPropagation()
  const ok = await confirm(t('删除「{name}」？\n\n项目文件和生成过的产物会一起收进回收站，删除后可在「回收站」里恢复。', { name: r.title || r.id }), { confirmLabel: t('删除'), danger: true })
  if (!ok) return
  try {
    await archivePodcast(r.id)
    toast(t('已删除「{name}」（可在「回收站」里恢复）', { name: r.title || r.id }))
    if (currentPodcastId.value === r.id) router.push('/podcast')
  } catch (err) { await notice(err.message) }
}

// ---- 剧本工作台（docs/scriptlab-spec.md §9）：列表来自 GET /api/script（store.scripts）
const scq = ref('')
const scripts = computed(() => {
  const s = scq.value.trim().toLowerCase()
  return (store.scripts || []).filter(r => !s || String(r.id || '').toLowerCase().includes(s) || String(r.title || '').toLowerCase().includes(s))
})
const currentScriptId = computed(() => (route.path.startsWith('/script/p/') && route.params.id) || null)
const scriptSub = (r) => `${r.id} · ${scriptProgressText(r)}`
async function removeScript(r, event) {
  event.preventDefault(); event.stopPropagation()
  const ok = await confirm(t('删除「{name}」？\n\n项目文件和生成过的产物会一起收进回收站，删除后可在「回收站」里恢复。', { name: r.title || r.id }), { confirmLabel: t('删除'), danger: true })
  if (!ok) return
  try {
    await archiveScript(r.id)
    toast(t('已删除「{name}」（可在「回收站」里恢复）', { name: r.title || r.id }))
    if (currentScriptId.value === r.id) router.push('/script')
  } catch (err) { await notice(err.message) }
}

// ---- 单条视频（docs/clip-spec.md §6）：列表来自 GET /api/clip（store.clips）
const cq = ref('')
const clips = computed(() => {
  const s = cq.value.trim().toLowerCase()
  return (store.clips || []).filter(r => !s || String(r.id || '').toLowerCase().includes(s) || String(r.title || '').toLowerCase().includes(s))
})
const currentClipId = computed(() => (route.path.startsWith('/clip/c/') && route.params.id) || null)
const clipSub = (r) => `${r.id} · ${clipProgressText(r)}`
async function removeClip(r, event) {
  event.preventDefault(); event.stopPropagation()
  const ok = await confirm(t('删除「{name}」？\n\n项目文件和生成过的产物会一起收进回收站，删除后可在「回收站」里恢复。', { name: r.title || r.id }), { confirmLabel: t('删除'), danger: true })
  if (!ok) return
  try {
    await archiveClip(r.id)
    toast(t('已删除「{name}」（可在「回收站」里恢复）', { name: r.title || r.id }))
    if (currentClipId.value === r.id) router.push('/clip')
  } catch (err) { await notice(err.message) }
}

// ---- 图片集模块（docs/imageset-spec.md §5、docs/imagestudio-spec.md §4）：表情与头像 / 纪念写真 / 图片工作台
// 共用一块（新建 + 搜索 + 列表），列表来自 GET /api/{m}（store.sets[m]）
const sq = reactive({ sticker: '', portrait: '', image: '' })
function setsOf(id) {
  const s = String(sq[id] || '').trim().toLowerCase()
  return (store.sets[id] || []).filter(r => !s || String(r.id || '').toLowerCase().includes(s) || String(r.title || '').toLowerCase().includes(s))
}
const currentSetId = computed(() => (/^\/(sticker|portrait|image)\/s\//.test(route.path) && route.params.id) || null)
const setSub = (r) => `${r.id} · ${progressText(r)}`
async function removeSet(id, r, event) {
  event.preventDefault(); event.stopPropagation()
  const ok = await confirm(t('删除「{name}」？\n\n项目文件和生成过的产物会一起收进回收站，删除后可在「回收站」里恢复。', { name: r.title || r.id }), { confirmLabel: t('删除'), danger: true })
  if (!ok) return
  try {
    await archiveSet(id, r.id)
    toast(t('已删除「{name}」（可在「回收站」里恢复）', { name: r.title || r.id }))
    if (currentSetId.value === r.id && routeModule.value === id) router.push(`/${id}`)
  } catch (err) { await notice(err.message) }
}

// ---- 短剧：项目列表只列短剧模块的项目（DESIGN §38）；列表行没有 module 字段的老形状按短剧算
const q = ref('')
const dramaAll = computed(() => store.episodes.filter(e => !e.module || e.module === 'drama'))
const items = computed(() => {
  const s = q.value.trim().toLowerCase()
  return dramaAll.value.filter(e => !s || e.id.toLowerCase().includes(s) || (e.title || '').toLowerCase().includes(s))
})
const currentId = computed(() => (route.path.startsWith('/drama/p/') && route.params.id) || null)
// 第二行永远带 id：同一个示例复制出来的项目标题相同，只靠标题分不清
function sub(e) {
  if (e.status === 'error') return t('{id} · 文件解析失败', { id: e.id })
  const p = e.progress
  if (!p) return t('{id} · {n} 镜', { id: e.id, n: e.shot_count })
  if (p.final) return t('{id} · 已有成片', { id: e.id })
  if (!e.locked) return t('{id} · 草稿', { id: e.id })
  return t('{id} · 首帧 {a}/{n} · 视频 {b}/{n}', { id: e.id, a: p.frames, b: p.videos, n: p.total })
}
// 删除 = 归档：spec 和已生成的首帧/视频一起搬进 .webui_deleted/，不是 os.remove（入口就放在项目行上，好找）
// 找回走全局的「回收站」页（/trash；短剧分组底下的「已删除的项目」也指向它）：归档时文件被改成固定名（spec.json / output）、
// 账本记录也一起收进了归档，照以前「手动搬回来」的字面做法搬不回一个能继续制作的项目
async function remove(e, event) {
  event.preventDefault(); event.stopPropagation()
  const ok = await confirm(t('删除「{name}」？\n\n项目文件、剧本、角色锚图、已生成的首帧/视频和账本记录会一起收进回收站，删除后可在「回收站」里恢复。', { name: e.title || e.id }), { confirmLabel: t('删除'), danger: true })
  if (!ok) return
  try {
    const r = await del(ep(e.id))
    await loadEpisodes()
    toast(t('已删除「{name}」（可在「回收站」里恢复）', { name: e.title || e.id }))
    if (currentId.value === e.id) router.push('/drama')
    // 归档了、但有东西没搬（项目文件里的输出目录在数据目录外 / 账本记录没能一起归档）：说清楚，别只报成功
    if (r?.warning) await notice(r.warning)
  } catch (err) { await notice(err.message) }
}

// ---- 绘本 / 有声书（docs/picturebook-spec.md §7）：注册表说它可用才有这一块；书的列表来自 GET /api/picturebook（store.books）
const bookModuleReady = computed(() => !!moduleById('picturebook')?.available)
const bq = ref('')
const books = computed(() => {
  const s = bq.value.trim().toLowerCase()
  return (store.books || []).filter(b => !s || String(b.id || '').toLowerCase().includes(s) || String(b.title || b.title_zh || '').toLowerCase().includes(s))
})
const currentBookId = computed(() => (route.path.startsWith('/picturebook/b/') && route.params.id) || null)
const bookTitle = (b) => b.title || b.title_zh || b.id
function bookSub(b) {
  const p = progressOf(b, b.page_count ?? b.pages)
  const n = p.pages.total || b.page_count || b.pages || 0
  if (p.final) return t('{id} · 已有成片', { id: b.id })
  if (!p.pages.done && !p.narration.done) return t('{id} · {n} 页', { id: b.id, n })
  return t('{id} · 插画 {a}/{n} · 朗读 {b}/{n}', { id: b.id, a: p.pages.done, b: p.narration.done, n })
}
async function removeBook(b, event) {
  event.preventDefault(); event.stopPropagation()
  const ok = await confirm(t('删除「{name}」？\n\n项目文件和生成过的产物会一起收进回收站，删除后可在「回收站」里恢复。', { name: bookTitle(b) }), { confirmLabel: t('删除'), danger: true })
  if (!ok) return
  try {
    await archiveBook(b.id)
    toast(t('已删除「{name}」（可在「回收站」里恢复）', { name: bookTitle(b) }))
    if (currentBookId.value === b.id) router.push('/picturebook')
  } catch (err) { await notice(err.message) }
}
</script>

<template>
  <aside class="sidebar">
    <!-- 全局：工作台首页 + 共享服务（任务中心、模型设置、音色库、回收站）+ 语言（DESIGN §38） -->
    <div class="top global" data-test="sidebar-global">
      <router-link class="btn block" to="/" style="text-align:center;text-decoration:none">{{ t('🏠 工作台首页') }}</router-link>
      <router-link class="btn block" to="/tasks" style="text-align:center;text-decoration:none">{{ t('📋 任务中心') }}</router-link>
      <router-link class="btn block" to="/settings" style="text-align:center;text-decoration:none">{{ t('🔌 模型设置') }}</router-link>
      <router-link class="btn block" to="/voices" style="text-align:center;text-decoration:none">{{ t('🎙 音色库') }}</router-link>
      <!-- 回收站：各模块删除的项目都在这里恢复 / 彻底删除 -->
      <router-link class="btn block" to="/trash" data-test="sidebar-trash" style="text-align:center;text-decoration:none">{{ t('🗑 回收站') }}</router-link>
      <select :value="lang" @change="setLang($event.target.value)" :title="t('界面语言')" style="margin-top:2px;padding:7px 10px;border:1px solid var(--border);border-radius:var(--r-sm);background:var(--panel2);color:var(--dim);font-size:12px">
        <option v-for="l in LANGS" :key="l.id" :value="l.id">{{ l.label }}</option>
      </select>
    </div>

    <!-- 模块手风琴：一行一个模块，展开的那个才显示它的动作与列表 -->
    <nav class="modules" data-test="sidebar-modules">
      <section v-for="m in readyModules" :key="m.id" class="mod" :class="{ open: open === m.id }" :data-test="`sidebar-module-${m.id}`">
        <div class="mod-head" data-test="module-header" :aria-expanded="open === m.id" @click="toggle(m.id)">
          <span class="mod-icon">{{ m.icon || '▪' }}</span>
          <router-link class="mod-name" :to="MODULE_HOME[m.id]" @click.stop="open = m.id">{{ moduleLabel(m) }}</router-link>
          <span class="mod-count" v-if="countOf(m.id) !== ''">{{ countOf(m.id) }}</span>
          <span class="mod-chev">{{ open === m.id ? '▾' : '▸' }}</span>
        </div>

        <div class="mod-body" v-show="open === m.id">
          <template v-if="m.id === 'drama'">
            <div class="mod-actions">
              <router-link class="btn block" to="/drama/libraries" style="text-align:center;text-decoration:none">{{ t('🎞 镜头库与风格库') }}</router-link>
              <input v-if="dramaAll.length >= SEARCH_MIN" v-model="q" :placeholder="t('搜索项目…')" data-test="project-search" />
            </div>
            <div class="list" data-test="project-list">
              <router-link v-for="e in items" :key="e.id" class="proj" :class="{ active: e.id === currentId }" :to="`/drama/p/${e.id}/overview`">
                <span class="cover"><img v-if="e.cover_url" :src="e.cover_url" alt="" /><span v-else>—</span></span>
                <span class="meta">
                  <span class="name">{{ e.title || e.id }}</span>
                  <span class="sub">{{ sub(e) }}</span>
                </span>
                <button class="del" :title="t('删除项目（归档）')" :aria-label="t('删除项目（归档）')" @click="remove(e, $event)">✕</button>
              </router-link>
              <router-link v-if="store.episodesLoaded && !dramaAll.length" class="hint empty-link" to="/drama">{{ t('还没有项目 · 去短剧首页新建 →') }}</router-link>
            </div>
            <!-- 已删除的短剧：指向全局回收站页，只看短剧（以前在这里就地展开列表，只有短剧能恢复） -->
            <div class="trash" data-test="drama-trash" style="padding:4px 14px 8px;border-top:1px solid var(--border-soft)">
              <router-link class="hint" data-test="drama-trash-link" style="text-decoration:none" :to="{ path: '/trash', query: { module: 'storyvideo' } }">🗑 {{ t('已删除的项目') }} →</router-link>
            </div>
          </template>

          <template v-else-if="m.id === 'picturebook' && bookModuleReady">
            <div class="mod-actions">
              <input v-if="(store.books || []).length >= SEARCH_MIN" v-model="bq" :placeholder="t('搜索绘本…')" data-test="book-search" />
            </div>
            <div class="list" data-test="book-list">
              <router-link v-for="b in books" :key="b.id" class="proj book" :class="{ active: b.id === currentBookId }" :to="`/picturebook/b/${b.id}/overview`">
                <span class="cover"><img v-if="b.cover_url" :src="b.cover_url" alt="" /><span v-else>📖</span></span>
                <span class="meta">
                  <span class="name">{{ bookTitle(b) }}</span>
                  <span class="sub">{{ bookSub(b) }}</span>
                </span>
                <button class="del" :title="t('删除绘本（归档）')" :aria-label="t('删除绘本（归档）')" @click="removeBook(b, $event)">✕</button>
              </router-link>
              <router-link v-if="store.booksLoaded && !books.length" class="hint empty-link" to="/picturebook">{{ t('还没有 · 去模块首页新建 →') }}</router-link>
            </div>
          </template>

          <template v-else-if="IMAGESET_MODULES[m.id]">
            <div class="mod-actions">
              <input v-if="(store.sets[m.id] || []).length >= SEARCH_MIN" v-model="sq[m.id]" :placeholder="t('搜索…')" :data-test="`set-search-${m.id}`" />
            </div>
            <div class="list" :data-test="`set-list-${m.id}`">
              <router-link v-for="r in setsOf(m.id)" :key="r.id" class="proj" :class="{ active: r.id === currentSetId && routeModule === m.id }" :to="setRoute(m.id, r.id)">
                <span class="cover"><img v-if="r.cover_url" :src="r.cover_url" alt="" /><span v-else>{{ IMAGESET_MODULES[m.id].icon }}</span></span>
                <span class="meta">
                  <span class="name">{{ r.title || r.id }}</span>
                  <span class="sub">{{ setSub(r) }}</span>
                </span>
                <button class="del" :title="t('删除（归档）')" :aria-label="t('删除（归档）')" @click="removeSet(m.id, r, $event)">✕</button>
              </router-link>
              <router-link v-if="store.setsLoaded[m.id] && !setsOf(m.id).length" class="hint empty-link" :to="`/${m.id}`">{{ t('还没有 · 去模块首页新建 →') }}</router-link>
            </div>
          </template>

          <template v-else-if="m.id === 'clip'">
            <div class="mod-actions">
              <input v-if="(store.clips || []).length >= SEARCH_MIN" v-model="cq" :placeholder="t('搜索片子…')" data-test="clip-search" />
            </div>
            <div class="list" data-test="clip-list">
              <router-link v-for="r in clips" :key="r.id" class="proj" :class="{ active: r.id === currentClipId }" :to="clipRoute(r.id)">
                <span class="cover"><span>🎞</span></span>
                <span class="meta">
                  <span class="name">{{ r.title || r.id }}</span>
                  <span class="sub">{{ clipSub(r) }}</span>
                </span>
                <button class="del" :title="t('删除（归档）')" :aria-label="t('删除（归档）')" @click="removeClip(r, $event)">✕</button>
              </router-link>
              <router-link v-if="store.clipsLoaded && !clips.length" class="hint empty-link" to="/clip">{{ t('还没有 · 去模块首页新建 →') }}</router-link>
            </div>
          </template>

          <template v-else-if="m.id === 'music'">
            <div class="mod-actions">
              <input v-if="(store.musics || []).length >= SEARCH_MIN" v-model="mq" :placeholder="t('搜索配乐…')" data-test="music-search" />
            </div>
            <div class="list" data-test="music-list">
              <router-link v-for="r in musics" :key="r.id" class="proj" :class="{ active: r.id === currentMusicId }" :to="musicRoute(r.id)">
                <span class="cover"><span>🎵</span></span>
                <span class="meta">
                  <span class="name">{{ r.title || r.id }}</span>
                  <span class="sub">{{ musicSub(r) }}</span>
                </span>
                <button class="del" :title="t('删除（归档）')" :aria-label="t('删除（归档）')" @click="removeMusic(r, $event)">✕</button>
              </router-link>
              <router-link v-if="store.musicsLoaded && !musics.length" class="hint empty-link" to="/music">{{ t('还没有 · 去模块首页新建 →') }}</router-link>
            </div>
          </template>

          <template v-else-if="m.id === 'podcast'">
            <div class="mod-actions">
              <input v-if="(store.podcasts || []).length >= SEARCH_MIN" v-model="pq" :placeholder="t('搜索播客…')" data-test="podcast-search" />
            </div>
            <div class="list" data-test="podcast-list">
              <router-link v-for="r in podcasts" :key="r.id" class="proj" :class="{ active: r.id === currentPodcastId }" :to="podcastRoute(r.id)">
                <span class="cover"><span>🎙</span></span>
                <span class="meta">
                  <span class="name">{{ r.title || r.id }}</span>
                  <span class="sub">{{ podcastSub(r) }}</span>
                </span>
                <button class="del" :title="t('删除（归档）')" :aria-label="t('删除（归档）')" @click="removePodcast(r, $event)">✕</button>
              </router-link>
              <router-link v-if="store.podcastsLoaded && !podcasts.length" class="hint empty-link" to="/podcast">{{ t('还没有 · 去模块首页新建 →') }}</router-link>
            </div>
          </template>

          <template v-else-if="m.id === 'script'">
            <div class="mod-actions">
              <input v-if="(store.scripts || []).length >= SEARCH_MIN" v-model="scq" :placeholder="t('搜索剧本…')" data-test="script-search" />
            </div>
            <div class="list" data-test="script-list">
              <router-link v-for="r in scripts" :key="r.id" class="proj" :class="{ active: r.id === currentScriptId }" :to="scriptRoute(r.id)">
                <span class="cover"><span>✍️</span></span>
                <span class="meta">
                  <span class="name">{{ r.title || r.id }}</span>
                  <span class="sub">{{ scriptSub(r) }}</span>
                </span>
                <button class="del" :title="t('删除（归档）')" :aria-label="t('删除（归档）')" @click="removeScript(r, $event)">✕</button>
              </router-link>
              <router-link v-if="store.scriptsLoaded && !scripts.length" class="hint empty-link" to="/script">{{ t('还没有 · 去模块首页新建 →') }}</router-link>
            </div>
          </template>
        </div>
      </section>
    </nav>
  </aside>
</template>
