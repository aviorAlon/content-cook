import { createRouter, createWebHashHistory } from 'vue-router'
import Home from './views/Home.vue'
import DramaHome from './views/DramaHome.vue'
import NewProject from './views/NewProject.vue'
import Settings from './views/Settings.vue'
import Tasks from './views/Tasks.vue'
import Trash from './views/Trash.vue'
import Voices from './views/Voices.vue'
import Libraries from './views/Libraries.vue'
import Project from './views/Project.vue'
import Overview from './views/project/Overview.vue'
import Cast from './views/project/Cast.vue'
import Shots from './views/project/Shots.vue'
import Screenplay from './views/project/Screenplay.vue'
import Post from './views/project/Post.vue'
import Final from './views/project/Final.vue'
import Advanced from './views/project/Advanced.vue'
import PbHome from './views/picturebook/PbHome.vue'
import PbNew from './views/picturebook/PbNew.vue'
import Book from './views/picturebook/Book.vue'
import BookOverview from './views/picturebook/BookOverview.vue'
import BookPages from './views/picturebook/BookPages.vue'
import BookCharacters from './views/picturebook/BookCharacters.vue'
import BookNarration from './views/picturebook/BookNarration.vue'
import BookFinal from './views/picturebook/BookFinal.vue'
import BookAdvanced from './views/picturebook/BookAdvanced.vue'
import SetHome from './views/imageset/SetHome.vue'
import SetNew from './views/imageset/SetNew.vue'
import Set from './views/imageset/Set.vue'
import SetItems from './views/imageset/SetItems.vue'
import SetOutputs from './views/imageset/SetOutputs.vue'
import SetAdvanced from './views/imageset/SetAdvanced.vue'
import ClipHome from './views/clip/ClipHome.vue'
import ClipNew from './views/clip/ClipNew.vue'
import Clip from './views/clip/Clip.vue'
import ClipWrite from './views/clip/ClipWrite.vue'
import ClipRender from './views/clip/ClipRender.vue'
import ClipAdvanced from './views/clip/ClipAdvanced.vue'
import MusicHome from './views/music/MusicHome.vue'
import MusicNew from './views/music/MusicNew.vue'
import Music from './views/music/Music.vue'
import MusicGenerate from './views/music/MusicGenerate.vue'
import MusicAdvanced from './views/music/MusicAdvanced.vue'
import PodcastHome from './views/podcast/PodcastHome.vue'
import PodcastNew from './views/podcast/PodcastNew.vue'
import Podcast from './views/podcast/Podcast.vue'
import PodcastScript from './views/podcast/PodcastScript.vue'
import PodcastAudio from './views/podcast/PodcastAudio.vue'
import PodcastAdvanced from './views/podcast/PodcastAdvanced.vue'
import ScriptHome from './views/script/ScriptHome.vue'
import ScriptNew from './views/script/ScriptNew.vue'
import Script from './views/script/Script.vue'
import ScriptWrite from './views/script/ScriptWrite.vue'
import ScriptTry from './views/script/ScriptTry.vue'
import ScriptCompare from './views/script/ScriptCompare.vue'
import ScriptUse from './views/script/ScriptUse.vue'

// 路由命名空间（DESIGN §38）：短剧是一个功能模块，它的页面都在 /drama 下；/settings、/voices 是共享服务，留在根上。
// 旧路径（/new、/p/:id/…、/libraries）保留重定向，已有的链接和书签照常能打开。
export const DRAMA = '/drama'
export const dramaProject = (id, sub = 'overview') => `${DRAMA}/p/${id}/${sub}`
// 绘本 / 有声书模块（docs/picturebook-spec.md §7）：/picturebook、/picturebook/new、/picturebook/b/:id/…
export const PICTUREBOOK = '/picturebook'
export const pbBook = (id, sub = 'overview') => `${PICTUREBOOK}/b/${id}/${sub}`
// 图片集模块（docs/imageset-spec.md §5、docs/imagestudio-spec.md §4）：表情与头像 /sticker、纪念写真 /portrait、
// 图片工作台 /image 共用一套页面，模块名放在路由 meta.module；/{m}、/{m}/new、/{m}/s/:id/{items,outputs,advanced}
// 单条视频模块（docs/clip-spec.md §6）：/clip、/clip/new、/clip/c/:id/{write,render,advanced}
export const CLIP = '/clip'
export const clipPage = (id, sub = 'write') => `${CLIP}/c/${id}/${sub}`
// 配乐模块（docs/music-spec.md §5）：/music、/music/new、/music/m/:id/{generate,advanced}
export const MUSIC = '/music'
export const musicPage = (id, sub = 'generate') => `${MUSIC}/m/${id}/${sub}`
// AI 播客模块（docs/podcast-spec.md §6）：/podcast、/podcast/new、/podcast/p/:id/{script,audio,advanced}
export const PODCAST = '/podcast'
export const podcastPage = (id, sub = 'script') => `${PODCAST}/p/${id}/${sub}`
// 剧本工作台模块（docs/scriptlab-spec.md §9）：/script、/script/new、/script/p/:id/{write,try,compare,use}
export const SCRIPT = '/script'
export const scriptPage = (id, sub = 'write') => `${SCRIPT}/p/${id}/${sub}`
export const IMAGESET_MODULES = ['sticker', 'portrait', 'image']
export const setPage = (m, id, sub = 'items') => `/${m}/s/${id}/${sub}`
function imagesetRoutes(m) {
  const meta = { module: m }
  return [
    { path: `/${m}`, component: SetHome, meta },
    { path: `/${m}/new`, component: SetNew, meta },
    {
      path: `/${m}/s/:id`,
      component: Set,
      meta,
      children: [
        { path: '', redirect: to => setPage(m, to.params.id) },
        { path: 'items', component: SetItems, meta },
        { path: 'outputs', component: SetOutputs, meta },
        { path: 'advanced', component: SetAdvanced, meta },
      ],
    },
  ]
}

function redirectProject(to) {
  const tail = Array.isArray(to.params.tail) ? to.params.tail.filter(Boolean).join('/') : (to.params.tail || '')
  return { path: `${DRAMA}/p/${to.params.id}${tail ? `/${tail}` : ''}`, query: to.query, hash: to.hash }
}

export const routes = [
  { path: '/', component: Home },
  { path: '/settings', component: Settings },
  { path: '/voices/:voiceId?', component: Voices },
  { path: '/tasks', component: Tasks },   // 任务中心：跨模块的后台任务（DESIGN §38）
  { path: '/trash', component: Trash },   // 回收站：各模块删除的项目（?module= 只看一个模块）
  // ---- 短剧模块
  { path: DRAMA, component: DramaHome },
  { path: `${DRAMA}/new`, component: NewProject },
  { path: `${DRAMA}/libraries`, component: Libraries },
  {
    path: `${DRAMA}/p/:id`,
    component: Project,
    children: [
      { path: '', redirect: to => dramaProject(to.params.id) },
      { path: 'overview', component: Overview },
      { path: 'cast/:castId?', component: Cast },
      { path: 'screenplay', component: Screenplay },
      { path: 'shots/:shotId?', component: Shots },
      { path: 'post', component: Post },
      { path: 'final', component: Final },
      { path: 'advanced', component: Advanced },
    ],
  },
  // ---- 绘本 / 有声书模块
  { path: PICTUREBOOK, component: PbHome },
  { path: `${PICTUREBOOK}/new`, component: PbNew },
  {
    path: `${PICTUREBOOK}/b/:id`,
    component: Book,
    children: [
      { path: '', redirect: to => pbBook(to.params.id) },
      { path: 'overview', component: BookOverview },
      { path: 'pages', component: BookPages },
      { path: 'characters', component: BookCharacters },
      { path: 'narration', component: BookNarration },
      { path: 'final', component: BookFinal },
      { path: 'advanced', component: BookAdvanced },
    ],
  },
  // ---- 图片集模块：表情与头像 / 纪念写真
  ...IMAGESET_MODULES.flatMap(imagesetRoutes),
  // ---- 单条视频模块
  { path: CLIP, component: ClipHome },
  { path: `${CLIP}/new`, component: ClipNew },
  {
    path: `${CLIP}/c/:id`,
    component: Clip,
    children: [
      { path: '', redirect: to => clipPage(to.params.id) },
      { path: 'write', component: ClipWrite },
      { path: 'render', component: ClipRender },
      { path: 'advanced', component: ClipAdvanced },
    ],
  },
  // ---- 配乐模块
  { path: MUSIC, component: MusicHome },
  { path: `${MUSIC}/new`, component: MusicNew },
  {
    path: `${MUSIC}/m/:id`,
    component: Music,
    children: [
      { path: '', redirect: to => musicPage(to.params.id) },
      { path: 'generate', component: MusicGenerate },
      { path: 'advanced', component: MusicAdvanced },
    ],
  },
  // ---- AI 播客模块
  { path: PODCAST, component: PodcastHome },
  { path: `${PODCAST}/new`, component: PodcastNew },
  {
    path: `${PODCAST}/p/:id`,
    component: Podcast,
    children: [
      { path: '', redirect: to => podcastPage(to.params.id) },
      { path: 'script', component: PodcastScript },
      { path: 'audio', component: PodcastAudio },
      { path: 'advanced', component: PodcastAdvanced },
    ],
  },
  // ---- 剧本工作台模块
  { path: SCRIPT, component: ScriptHome },
  { path: `${SCRIPT}/new`, component: ScriptNew },
  {
    path: `${SCRIPT}/p/:id`,
    component: Script,
    children: [
      { path: '', redirect: to => scriptPage(to.params.id) },
      { path: 'write', component: ScriptWrite },
      { path: 'try', component: ScriptTry },
      { path: 'compare', component: ScriptCompare },
      { path: 'use', component: ScriptUse },
    ],
  },
  // ---- 旧路径重定向
  { path: '/new', redirect: to => ({ path: `${DRAMA}/new`, query: to.query }) },
  { path: '/libraries', redirect: to => ({ path: `${DRAMA}/libraries`, query: to.query }) },
  { path: '/p/:id/:tail(.*)*', redirect: redirectProject },
]

// hash 路由：桌面壳里没有服务端路由，刷新/回退都只靠 URL 片段
export const router = createRouter({
  history: createWebHashHistory(),
  routes,
})
