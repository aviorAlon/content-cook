// 出图风格卡（docs/image-style-cards.md §5）：共享卡库只拉一次、按分类分组与「适合」筛选、选卡显示锚句全文、
// 「改成自写」切 custom 并把锚句当起点，以及四个接线点发出去的载荷（图片集新建 / 图片集 PUT / 绘本 PUT / 海报请求）。
// api 全部 mock；后端并行落地，这里只认契约里的接口形状。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
let library, templates, setDetail, book, posterState
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url === '/api/style-cards') return library
    if (/^\/api\/(sticker|portrait)\/templates$/.test(url)) return templates[url.split('/')[2]]
    if (/^\/api\/(sticker|portrait)$/.test(url)) return []
    if (/^\/api\/(sticker|portrait)\//.test(url)) return setDetail
    if (url.endsWith('/summary') || url.endsWith('/lint')) return null
    if (url.startsWith('/api/picturebook/')) return book
    if (url.endsWith('/poster')) return posterState
    if (url.endsWith('/export')) return { packages: [] }
    if (url.startsWith('/api/tasks/')) return { status: 'succeeded', result: {} }
    return {}
  }),
  post: vi.fn(async (url, body) => {
    calls.push(['post', url, body])
    if (url === '/api/sticker') return { id: 'ST-20260909-2', spec: { ...body } }
    if (url.endsWith('/poster/generate')) return { task_id: 't-poster' }
    return { ok: true }
  }),
  put: vi.fn(async (url, body) => { calls.push(['put', url, body]); return { id: body.id, spec: body } }),
  del: vi.fn(), ep: (id) => `/api/episodes/storyvideo/${id}`,
  pollTask: vi.fn(async (id) => { calls.push(['poll', id]); return { status: 'succeeded', result: {} } }),
  pollJob: vi.fn(), sleep: vi.fn(async () => {}), fileToBase64: vi.fn(async () => 'QUJD'),
}))
const confirmMock = vi.fn(async () => true)
vi.mock('../src/ui.js', () => ({
  prompt: vi.fn(), confirm: (...a) => confirmMock(...a), notice: vi.fn(), toast: vi.fn(), custom: vi.fn(), toasts: [], T: {},
}))

import { store } from '../src/store.js'
import { loadStyleCards, cachedStyleCards, styleCardById, groupCards, swatchStyle, dialectOf, dialectName } from '../src/stylecards.js'
import { loadSet, clearSet } from '../src/imageset.js'
import { loadBook } from '../src/picturebook.js'
import StyleCardPicker from '../src/components/StyleCardPicker.vue'
import SetNew from '../src/views/imageset/SetNew.vue'
import SetItems from '../src/views/imageset/SetItems.vue'
import BookCharacters from '../src/views/picturebook/BookCharacters.vue'
import Final from '../src/views/project/Final.vue'

const stub = { template: '<div/>' }
function stubRouter() {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: stub }, { path: '/settings', component: stub }, { path: '/voices', component: stub },
    { path: '/drama/p/:id/post', component: stub },
    { path: '/picturebook/b/:id/:sub', component: stub },
    ...['sticker', 'portrait'].flatMap(m => [
      { path: `/${m}`, component: stub, meta: { module: m } }, { path: `/${m}/new`, component: stub, meta: { module: m } },
      { path: `/${m}/s/:id/:sub`, component: stub, meta: { module: m } },
    ]),
  ] })
}
async function mountAt(Comp, path = '/', options = {}) {
  const r = stubRouter(); await r.push(path); await r.isReady()
  const w = mount(Comp, { global: { plugins: [r] }, ...options })
  await flushPromises()
  return { w, r }
}

// 卡库：三张角色卡命中表情包模板，一张海报卡，一张绘本卡，一张写真卡（分类里没写 illustration，归一化时补上）
function sampleLibrary() {
  return {
    _meta: { schema_version: 1 },
    categories: [
      { id: 'portrait', name: '人像写真', english: 'Portrait' },
      { id: 'character', name: '角色与表情', english: 'Character' },
      { id: 'poster', name: '海报与平面', english: 'Poster' },
    ],
    cards: [
      { id: 'studio_soft_portrait', name: '影楼柔光', english: 'Soft studio portrait', category: 'portrait',
        summary: '大柔光箱 45 度主光 + 白反补光，背景干净', anchor_zh: '影楼柔光棚拍：大柔光箱 45 度主光、白色反光板补光。',
        anchor_en: 'Soft studio portrait: a large softbox key at 45 degrees.', palette: ['米白', '暖灰', '淡杏', '浅木色', '柔粉'],
        lighting: '柔光箱主光 + 反光板补光', camera: '中长焦人像视角', constraints: ['不磨皮到失去毛孔'],
        fits: ['portrait.baby_100'], model_hint: 'any' },
      { id: 'flat_cute_sticker', name: '扁平可爱贴纸', english: 'Flat cute sticker', category: 'character',
        summary: '粗描边扁平色块，圆润形状', anchor_zh: '扁平可爱贴纸：粗描边、圆润形状、明快配色，白底。',
        palette: ['奶油白', '柔粉', '天蓝', '明黄', '苔绿', '雾松绿'], fits: ['sticker.emoji_pack', 'sticker.avatar_set'] },
      { id: 'line_doodle_sticker', name: '线稿涂鸦', category: 'character', summary: '单色细线，留白多',
        anchor_zh: '线稿涂鸦：单色细线勾勒，大量留白。', palette: ['黑', '纯白', '暖灰', '淡杏', '珊瑚红'],
        fits: ['sticker.emoji_pack'] },
      { id: 'minimal_type_poster', name: '极简大字', category: 'poster', summary: '大面积留白 + 一个主体',
        anchor_zh: '极简海报：大面积留白，一个主体，克制的色彩。', palette: ['米白', '炭黑', '砖红', '暖灰', '金'], fits: ['poster'] },
      { id: 'watercolor_book', name: '水彩绘本', category: 'illustration', summary: '水彩晕染，纸纹可见',
        anchor_zh: '水彩绘本插画：柔和晕染、圆润形状、纸纹可见。', palette: ['奶油白', '薄荷绿', '天蓝', '柔粉', '淡黄'],
        fits: ['picturebook'] },
    ],
  }
}
function sampleSpec() {
  return {
    id: 'ST-20260909-1', module: 'sticker', title: '小满表情包', template: 'emoji_pack',
    subject: { kind: 'character', name: '小满', block: '圆框眼镜', assets: [], fidelity: 85 },
    style: 'flat_cute', style_anchor: '', options: { expressions: ['开心'], with_caption: false },
    items: [{ id: 'I01', label: '开心', prompt: 'happy', aspect_ratio: '1:1', status: 'todo', current: null, attempts: [] }],
    picks: [],
  }
}
function sampleBook() {
  return {
    id: 'PB-20260909-1', spec: {
      id: 'PB-20260909-1', title: '小狐狸的三只袜子', age_band: '4-6', style_anchor: 'Soft watercolor', negative: 'text',
      characters: { fox: { name: '小狐狸', name_en: 'Fox', block: '橘红色小狐狸', asset: null } }, scenes: {}, pages: [],
    },
    pages: [], image_dialect: 'nano_banana',
  }
}

beforeEach(() => {
  calls.length = 0
  confirmMock.mockReset(); confirmMock.mockResolvedValue(true)
  library = sampleLibrary()
  templates = {
    sticker: { templates: [
      { id: 'emoji_pack', name: '表情包', description: '每个表情一张 1:1', styles: [{ id: 'flat_cute', name: '扁平可爱', anchor: 'flat cute vector sticker' }, { id: 'line_doodle', name: '线稿涂鸦', anchor: 'line doodle' }],
        options_schema: { expressions: { type: 'list', default: ['开心'] } } },
    ] },
    portrait: { templates: [{ id: 'baby_100', name: '百天', description: '百天', styles: [{ id: 'studio', name: '影楼柔光', anchor: 'soft studio light' }], options_schema: {} }] },
  }
  const spec = sampleSpec()
  setDetail = { id: spec.id, module: 'sticker', title: spec.title, template: spec.template, spec, image_dialect: 'gpt_image',
    items: spec.items.map(it => ({ id: it.id, status: it.status, current: null, attempts: [] })), exports: [] }
  book = sampleBook()
  posterState = { latest: null, history: [], suggested_title: '第七封信', suggested_subtitle: '', fonts: [], shots_with_frames: [], model: 'gpt-image-2' }
  store.styleCards = null
  store.sets = { sticker: [], portrait: [] }; store.setsLoaded = { sticker: false, portrait: false }
  clearSet(); store.gen = null; store.busy = 0
  store.book = null; store.bookDetail = null; store.bookId = null
  store.projectId = 'EP1'; store.detail = { id: 'EP1', final_url: null, has_post_layers: false }; store.summary = null
  store.health = { roles: { image: { configured: true } } }
})

describe('风格卡库', () => {
  it('整个库只拉一次并缓存在 store；分类缺席的卡自己补一组；byId 能按 id 取', async () => {
    const a = await loadStyleCards()
    const b = await loadStyleCards()
    await Promise.all([loadStyleCards(), loadStyleCards()])
    expect(calls.filter(c => c[1] === '/api/style-cards').length).toBe(1)
    expect(b).toBe(a)
    expect(store.styleCards).toBe(a)
    expect(a.cards.length).toBe(5)
    expect(a.categories.map(c => c.id)).toEqual(['portrait', 'character', 'poster', 'illustration'])
    expect(styleCardById('line_doodle_sticker').name).toBe('线稿涂鸦')
    expect(styleCardById('没有这张')).toBeNull()
    // 「适合」分组：命中表情包模板的只有两张角色卡；不筛时四组都在
    expect(groupCards('sticker.emoji_pack', { only: true }).map(g => [g.id, g.cards.length])).toEqual([['character', 2]])
    expect(groupCards('sticker.emoji_pack', { only: false }).map(g => g.id)).toEqual(['portrait', 'character', 'poster', 'illustration'])
    // 色板：认得出的给实色，认不出的给中性块（不猜十六进制）
    expect(swatchStyle('米白').background).toBe('#f5f0e3')
    expect(swatchStyle('雾松绿').background).toBe('var(--panel3)')
    // 接口挂了也不炸：库为空，界面只少了卡片区
    store.styleCards = null
    const { get } = await import('../src/api.js')
    get.mockImplementationOnce(async () => { throw new Error('404') })
    const empty = await loadStyleCards()
    expect(empty.cards).toEqual([])
    expect(cachedStyleCards().categories).toEqual([])
  })

  it('图像方言：项目文件里手写的优先，其次后端自动判出来的，都没有就是自动', () => {
    expect(dialectOf({ image_dialect: 'gpt_image' }, {})).toEqual({ override: '', detected: 'gpt_image', effective: 'gpt_image' })
    expect(dialectOf({ image_dialect: 'gpt_image' }, { image_dialect: 'nano_banana' }).effective).toBe('nano_banana')
    expect(dialectOf(null, null)).toEqual({ override: '', detected: '', effective: '' })
    expect(dialectName('nano_banana')).toBe('Nano Banana')
    expect(dialectName('')).toBe('自动')
  })
})

describe('StyleCardPicker', () => {
  const mountPicker = (props = {}) => mountAt(StyleCardPicker, '/', { props: { modelValue: '', presets: [], fits: 'sticker.emoji_pack', anchor: '', ...props } })

  it('按分类分组的卡片网格：名字 / 一句话 / 色板小色块 / 「适合」标签；「只看适合本模板的」默认开着，取消后全部露出', async () => {
    const { w } = await mountPicker()
    const groups = w.findAll('[data-test="style-group"]')
    expect(groups.length).toBe(1)
    expect(groups[0].attributes('data-group')).toBe('character')
    expect(groups[0].text()).toContain('角色与表情')
    const cards = w.findAll('[data-test="style-card"]')
    expect(cards.map(c => c.attributes('data-card'))).toEqual(['flat_cute_sticker', 'line_doodle_sticker'])
    expect(cards[0].text()).toContain('扁平可爱贴纸')
    expect(cards[0].text()).toContain('粗描边扁平色块')
    // 色板：6 个小色块，最后一个色名不认识 → 中性块
    const sw = cards[0].findAll('.sw')
    expect(sw.length).toBe(6)
    expect(sw.map(s => s.attributes('data-known'))).toEqual(['1', '1', '1', '1', '1', '0'])
    expect(cards[0].findAll('[data-test="style-fit"]').map(f => f.text())).toEqual(['sticker.emoji_pack', 'sticker.avatar_set'])
    expect(w.find('[data-test="style-hidden"]').text()).toContain('3')
    // 取消「只看适合本模板的」→ 四组全在
    await w.find('[data-test="style-fits-only"]').setValue(false)
    expect(w.findAll('[data-test="style-group"]').map(g => g.attributes('data-group'))).toEqual(['portrait', 'character', 'poster', 'illustration'])
    expect(w.findAll('[data-test="style-card"]').length).toBe(5)
    // 下拉：模板自带的预设排在最前，然后是分组的卡，最后是自写
    const { w: w2 } = await mountPicker({ presets: [{ id: 'flat_cute', name: '扁平可爱', anchor: 'flat cute' }] })
    const sel = w2.find('[data-test="style-select"]')
    expect(sel.findAll('optgroup').map(g => g.attributes('label'))).toEqual(['模板自带', '角色与表情'])
    expect(sel.findAll('option').map(o => o.attributes('value'))).toEqual(['flat_cute', 'flat_cute_sticker', 'line_doodle_sticker', 'custom'])
  })

  it('选一张卡：emit 卡 id，并把锚句全文只读摆出来；「改成自写」切 custom 并把锚句当起点', async () => {
    const { w } = await mountPicker()
    expect(w.find('[data-test="style-detail"]').exists()).toBe(false)
    await w.findAll('[data-test="style-card"]')[1].trigger('click')
    expect(w.emitted('update:modelValue')[0]).toEqual(['line_doodle_sticker'])
    expect(w.emitted('update:anchor')).toBeUndefined()   // 选卡不动自写锚
    // 父组件把选中值传回来 → 显示这张卡的锚句
    await w.setProps({ modelValue: 'line_doodle_sticker' })
    const detail = w.find('[data-test="style-detail"]')
    expect(detail.text()).toContain('线稿涂鸦')
    expect(detail.find('[data-test="style-card-anchor"]').element.value).toBe('线稿涂鸦：单色细线勾勒，大量留白。')
    expect(detail.find('[data-test="style-card-anchor"]').attributes('readonly')).toBeDefined()
    expect(detail.find('[data-test="style-card-palette"]').text()).toContain('黑、纯白')
    expect(w.find('[data-test="style-card"][data-card="line_doodle_sticker"]').classes()).toContain('picked')
    // 改成自写
    await detail.find('[data-test="style-to-custom"]').trigger('click')
    expect(w.emitted('update:modelValue').at(-1)).toEqual(['custom'])
    expect(w.emitted('update:anchor').at(-1)).toEqual(['线稿涂鸦：单色细线勾勒，大量留白。'])
    // 下拉里选中的卡即使被筛掉也留着（keep）
    const { w: w3 } = await mountPicker({ modelValue: 'minimal_type_poster' })
    expect(w3.find('[data-test="style-select"]').findAll('option').map(o => o.attributes('value'))).toContain('minimal_type_poster')
  })

  it('后端没给卡库时不炸：卡片区给一句话，模板自带的预设照常可选', async () => {
    library = { categories: [], cards: [] }
    const { w } = await mountPicker({ presets: [{ id: 'flat_cute', name: '扁平可爱', anchor: 'flat cute' }], modelValue: 'flat_cute' })
    expect(w.find('[data-test="style-empty"]').exists()).toBe(true)
    expect(w.findAll('[data-test="style-card"]').length).toBe(0)
    expect(w.find('[data-test="style-preset-note"]').text()).toContain('扁平可爱')
  })
})

describe('风格卡的四个接线点', () => {
  it('图片集新建：选卡后 style 发的是卡 id，自写锚清空', async () => {
    const { w } = await mountAt(SetNew, '/sticker/new')
    expect(w.find('[data-test="set-anchor"]').element.value).toBe('flat cute vector sticker')
    expect(w.findAll('[data-test="style-card"]').map(c => c.attributes('data-card'))).toEqual(['flat_cute_sticker', 'line_doodle_sticker'])
    await w.find('[data-test="style-card"][data-card="flat_cute_sticker"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="set-anchor"]').element.value).toBe('')
    expect(w.find('[data-test="style-card-anchor"]').element.value).toContain('扁平可爱贴纸：粗描边')
    await w.find('[data-test="set-title"]').setValue('小满表情包')
    await w.find('[data-test="set-create"]').trigger('click'); await flushPromises()
    const created = calls.find(c => c[0] === 'post' && c[1] === '/api/sticker')
    expect(created[2].style).toBe('flat_cute_sticker')
    expect(created[2].style_anchor).toBe('')
    // 「改成自写」→ style 回到 custom，锚句进文本框当起点
    const { w: w2 } = await mountAt(SetNew, '/sticker/new')
    await w2.find('[data-test="set-style"]').setValue('line_doodle_sticker')
    await w2.find('[data-test="style-to-custom"]').trigger('click'); await flushPromises()
    expect(w2.find('[data-test="set-anchor"]').element.value).toBe('线稿涂鸦：单色细线勾勒，大量留白。')
    await w2.find('[data-test="set-title"]').setValue('小满 2')
    await w2.find('[data-test="set-create"]').trigger('click'); await flushPromises()
    const c2 = calls.filter(c => c[0] === 'post' && c[1] === '/api/sticker').at(-1)
    expect(c2[2].style).toBe('custom')
    expect(c2[2].style_anchor).toBe('线稿涂鸦：单色细线勾勒，大量留白。')
  })

  it('图片集清单页：风格行显示当前风格与图像方言；换风格提示要重出，保存走 PUT 整份（带 image_dialect）', async () => {
    await loadSet('sticker', 'ST-20260909-1')
    const { w } = await mountAt(SetItems, '/sticker/s/ST-20260909-1/items')
    expect(w.find('[data-test="style-name"]').text()).toBe('扁平可爱')          // 模板自带的预设名
    expect(w.find('[data-test="dialect-name"]').text()).toBe('自动（GPT Image）')  // detail 里后端判出来的
    expect(w.find('[data-test="dialect-what"]').text()).toContain('五段带标签')
    expect(w.find('[data-test="style-editor"]').exists()).toBe(false)
    expect(w.find('[data-test="style-restage"]').exists()).toBe(false)
    await w.find('[data-test="style-change"]').trigger('click')
    await w.find('[data-test="style-select"]').setValue('flat_cute_sticker')
    expect(w.find('[data-test="style-name"]').text()).toBe('扁平可爱贴纸')
    expect(w.find('[data-test="style-restage"]').text()).toContain('换风格要重出才生效')
    await w.find('[data-test="dialect-select"]').setValue('nano_banana')
    expect(w.find('[data-test="dialect-name"]').text()).toBe('Nano Banana')
    expect(w.find('[data-test="dialect-what"]').text()).toContain('不写数字焦段光圈')
    await w.find('[data-test="style-save"]').trigger('click'); await flushPromises()
    const saved = calls.find(c => c[0] === 'put' && c[1] === '/api/sticker/ST-20260909-1')
    expect(saved[2].style).toBe('flat_cute_sticker')
    expect(saved[2].style_anchor).toBe('')
    expect(saved[2].image_dialect).toBe('nano_banana')
    expect(saved[2].items.length).toBe(1)
    expect(saved[2].title).toBe('小满表情包')
    expect(saved[2].output_root).toBeUndefined()
  })

  it('绘本角色页：挑卡进 style_card，自写风格锚照旧；方言手选也进整本 PUT', async () => {
    await loadBook('PB-20260909-1')
    const { w } = await mountAt(BookCharacters, '/picturebook/b/PB-20260909-1/characters')
    expect(w.find('[data-test="book-style-name"]').text()).toBe('自写风格锚')
    expect(w.find('[data-test="book-dialect-name"]').text()).toBe('自动（Nano Banana）')
    // 绘本的 fits 是 picturebook：只有水彩绘本那张
    expect(w.findAll('[data-test="style-card"]').map(c => c.attributes('data-card'))).toEqual(['watercolor_book'])
    await w.find('[data-test="style-card"][data-card="watercolor_book"]').trigger('click')
    expect(w.find('[data-test="book-style-name"]').text()).toBe('水彩绘本')
    expect(w.find('[data-test="style-card-anchor"]').element.value).toContain('水彩绘本插画')
    // 「改成自写」：不带 style_card，锚句进文本框当起点
    await w.find('[data-test="style-to-custom"]').trigger('click')
    expect(w.find('[data-test="book-style-name"]').text()).toBe('自写风格锚')
    expect(w.find('[data-test="style-anchor"]').element.value).toContain('水彩绘本插画')
    await w.find('[data-test="chars-save"]').trigger('click'); await flushPromises()
    const custom = calls.find(c => c[0] === 'put' && c[1] === '/api/picturebook/PB-20260909-1')
    expect(custom[2].style_card).toBeUndefined()
    expect(custom[2].style_anchor).toContain('水彩绘本插画')
    // 再挑一次卡 + 手选方言 → 整本 PUT 带上两个键
    calls.length = 0
    await w.find('[data-test="style-card"][data-card="watercolor_book"]').trigger('click')
    await w.find('[data-test="book-dialect-select"]').setValue('gpt_image')
    await w.find('[data-test="chars-save"]').trigger('click'); await flushPromises()
    const saved = calls.find(c => c[0] === 'put' && c[1] === '/api/picturebook/PB-20260909-1')
    expect(saved[2].style_card).toBe('watercolor_book')
    expect(saved[2].image_dialect).toBe('gpt_image')
    expect(saved[2].style_anchor).toBe('Soft watercolor')
    expect(Object.keys(saved[2].characters)).toEqual(['fox'])
  })

  it('成片页海报卡：只列适合海报的卡，选中后进 generate / render 请求的 style_card；不选就不带这个键', async () => {
    const { w } = await mountAt(Final, '/')
    const sel = w.find('[data-test="poster-style-card"]')
    expect(sel.findAll('option').map(o => o.attributes('value'))).toEqual(['', 'minimal_type_poster'])
    await w.find('[data-test="poster-generate"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1].endsWith('/poster/generate'))[2].style_card).toBeUndefined()
    calls.length = 0
    await sel.setValue('minimal_type_poster')
    expect(w.find('[data-test="poster-style-anchor"]').text()).toContain('大面积留白')
    posterState = { ...posterState, latest: { url: '/f/p1.png', poster: 'p1.png', title: '第七封信', created_at: 1757000000, source: 'model' } }
    await w.find('[data-test="poster-generate"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1].endsWith('/poster/generate'))[2].style_card).toBe('minimal_type_poster')
    // 只重排文字也带着（底图是哪张卡出的要能追）
    await w.find('[data-test="poster-rerender"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1].endsWith('/poster/render'))[2].style_card).toBe('minimal_type_poster')
  })
})
