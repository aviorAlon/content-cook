// 交付面遮挡区（DESIGN §27）：首帧上的斜纹遮罩 + 后期页的预设/自定义编辑器
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import OverlayMask from '../src/components/OverlayMask.vue'
import OverlayRegionsEditor from '../src/components/OverlayRegionsEditor.vue'

const PRESETS = [{ id: 'none', label: '无（不预留）' }, { id: 'douyin', label: '抖音（竖屏）' }]
const DOUYIN = [
  { label: '顶部状态与标题栏', x: 0, y: 0, w: 1, h: 0.09 },
  { label: '右侧互动栏', x: 0.84, y: 0.42, w: 0.16, h: 0.36 },
  { label: '底部文案与进度条', x: 0, y: 0.78, w: 1, h: 0.22 },
]

describe('OverlayMask', () => {
  it('比例坐标换成百分比定位，带标签；show=false 什么都不画', () => {
    const w = mount(OverlayMask, { props: { regions: DOUYIN } })
    const boxes = w.findAll('[data-test=overlay-region]')
    expect(boxes.length).toBe(3)
    expect(boxes[1].attributes('style')).toContain('left: 84%')
    expect(boxes[1].attributes('style')).toContain('top: 42%')
    expect(boxes[2].text()).toBe('底部文案与进度条')
    const hidden = mount(OverlayMask, { props: { regions: DOUYIN, show: false } })
    expect(hidden.findAll('[data-test=overlay-region]').length).toBe(0)
  })
})

describe('OverlayRegionsEditor', () => {
  it('预设模式：保存发预设 id、空数组；预览画后端给的区域', async () => {
    const w = mount(OverlayRegionsEditor, { props: { overlays: { preset: 'douyin', custom: false, regions: DOUYIN, presets: PRESETS } } })
    expect(w.find('[data-test=overlay-mode]').element.value).toBe('douyin')
    expect(w.findAll('[data-test=overlay-region]').length).toBe(3)
    await w.find('button.btn.primary').trigger('click')
    expect(w.emitted('save')[0][0]).toEqual({ overlay_preset: 'douyin', overlay_regions: [] })
    // 切回「无」：预设发空串
    await w.find('[data-test=overlay-mode]').setValue('none')
    await w.find('button.btn.primary').trigger('click')
    expect(w.emitted('save')[1][0]).toEqual({ overlay_preset: '', overlay_regions: [] })
  })
  it('自定义模式：从预设拷起点，百分比换成 0–1，越界时禁止保存', async () => {
    const w = mount(OverlayRegionsEditor, { props: { overlays: { preset: 'douyin', custom: false, regions: DOUYIN, presets: PRESETS } } })
    await w.find('[data-test=overlay-mode]').setValue('custom')
    expect(w.findAll('[data-test=overlay-row]').length).toBe(3)
    expect(w.text()).toContain('自定义')
    const rows = w.findAll('[data-test=overlay-row]')
    const inputs = rows[2].findAll('input')
    expect(Number(inputs[2].element.value)).toBe(78)   // y = 0.78 → 78%
    await inputs[4].setValue('30')                      // h 22% → 30%：y + h = 108 > 100
    expect(w.text()).toContain('超出了画面')
    expect(w.find('button.btn.primary').attributes('disabled')).toBeDefined()
    await inputs[4].setValue('22')
    expect(w.find('button.btn.primary').attributes('disabled')).toBeUndefined()
    await w.find('button.btn.primary').trigger('click')
    const payload = w.emitted('save')[0][0]
    expect(payload.overlay_preset).toBe('')
    expect(payload.overlay_regions.length).toBe(3)
    expect(payload.overlay_regions[2]).toEqual({ label: '底部文案与进度条', x: 0, y: 0.78, w: 1, h: 0.22 })
  })
  it('后端已是自定义：直接进自定义模式并回填行', () => {
    const w = mount(OverlayRegionsEditor, { props: { overlays: { preset: 'none', custom: true, regions: [DOUYIN[2]], presets: PRESETS } } })
    expect(w.find('[data-test=overlay-mode]').element.value).toBe('custom')
    expect(w.findAll('[data-test=overlay-row]').length).toBe(1)
    expect(w.findAll('[data-test=overlay-region]').length).toBe(1)
  })
})
