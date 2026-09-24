import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// 视图级冒烟测试：jsdom 里挂载组件，断言渲染出的文字和交互，不起后端（fetch 用替身）。
export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.js'],
    globals: true,
    setupFiles: ['tests/setup.js'],
  },
})
