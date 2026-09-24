import { describe, it, expect } from 'vitest'
import { T, fmtSeconds, confirm, prompt, notice, custom, modal, toast, toasts } from '../src/ui.js'

describe('ui helpers', () => {
  it('术语表只用产品词，不出现工程词', () => {
    expect(T.qc.retake).toBe('待重拍')
    expect(T.qc.waive).toBe('已跳过')
    expect(Object.values(T.qc).join('')).not.toMatch(/QC|revision|amend/i)
    expect(T.role.image).toBe('生图')
  })
  it('fmtSeconds 分钟与秒', () => {
    expect(fmtSeconds(32)).toBe('32 秒')
    expect(fmtSeconds(95)).toBe('1 分 35 秒')
  })
  it('弹窗服务是一个 promise 队列：resolve 后清空', async () => {
    const p = confirm('x', { confirmLabel: '好' })
    expect(modal.current.kind).toBe('confirm')
    expect(modal.current.confirmLabel).toBe('好')
    modal.current.resolve(true)
    await expect(p).resolves.toBe(true)
    expect(modal.current).toBeNull()
    const q = prompt('id?', { multiline: false, defaultValue: 'A-1' })
    expect(modal.current.kind).toBe('prompt')
    modal.current.resolve('B-2')
    await expect(q).resolves.toBe('B-2')
    const n = notice('hi')
    modal.current.resolve(true)
    await n
    const c = custom({ view: 'estimate', estimate: {} })
    expect(modal.current.view).toBe('estimate')
    modal.current.resolve(false)
    await expect(c).resolves.toBe(false)
  })
  it('toast 会自动消失', async () => {
    toast('done', { ttl: 5 })
    expect(toasts.length).toBe(1)
    await new Promise(r => setTimeout(r, 20))
    expect(toasts.length).toBe(0)
  })
})
