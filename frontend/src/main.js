import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router.js'
import './styles.css'
import { t, lang } from './i18n.js'

const app = createApp(App)
app.config.globalProperties.$t = t
app.config.globalProperties.$lang = lang
app.use(router).mount('#app')
document.documentElement.lang = lang.value === 'zh' ? 'zh-CN' : 'en'
