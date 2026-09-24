// 插卡 id 的即时提示（postlayers.insertIdProblem）：跟后端 post.py 的 _validate_inserts 同一条规则——中文照收，
// 路径分隔符、Windows 文件名保留字符、% 和控制字符、超过 64 字的在输入框下面先说（2026-09-23 发布前自查 F1 / F6）。
import { describe, it, expect } from 'vitest'
import { insertIdProblem } from '../src/postlayers.js'

describe('insertIdProblem', () => {
  it('能用的 id 不提示', () => {
    for (const ok of ['UI_2', '来电', '片头 卡', 'card-1', '', null, undefined]) expect(insertIdProblem(ok)).toBe('')
  })

  it('路径分隔符、盘符、%、保留字符、控制字符、超长都提示', () => {
    for (const bad of ['../x', '..\\x', 'D:/p/f', 'card%d', 'a*b', 'a?b', 'a"b', 'a<b', 'a>b', 'a|b', 'a\tb', 'x'.repeat(65)]) {
      expect(insertIdProblem(bad)).toContain('UI_2')
    }
  })
})
