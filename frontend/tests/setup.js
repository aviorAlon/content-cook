// 测试固定中文界面（jsdom 的 navigator.language 是 en-US，localStorage 也可能不可写）；i18n 自己的测试里再切换语言。
// main.js 把 $t / $lang 注册成全局属性，测试里挂载组件时同样要有，否则模板里的 $t 会是 undefined。
import { config } from '@vue/test-utils'
import { t, lang, setLang } from '../src/i18n.js'

setLang('zh')
config.global.mocks = { ...(config.global.mocks || {}), $t: t, $lang: lang }
