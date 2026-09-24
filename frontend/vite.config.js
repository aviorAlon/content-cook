import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建产物直接落到 FastAPI 的静态目录：src/webui/static/app/，由 /app 挂载；
// 开发态用 vite dev server 反代 /api 到本地 FastAPI（storystudio --no-open --no-token --port 8765）。
export default defineConfig({
  plugins: [vue()],
  base: '/app/',
  build: {
    outDir: '../src/webui/static/app',
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8765' },
  },
})
