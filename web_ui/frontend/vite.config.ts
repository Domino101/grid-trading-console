import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// Root VERSION is the single source of truth. Inject it at build time so the
// sidebar can never drift from the backend app version.
const APP_VERSION = readFileSync(fileURLToPath(new URL('../../VERSION', import.meta.url)), 'utf8').trim()
if (!APP_VERSION) throw new Error('VERSION file is empty')

// API calls are always relative in browser code. This proxy exists only for the
// Vite development server, so remote browsers never need to reach localhost.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendTarget = env.VITE_BACKEND_TARGET || 'http://127.0.0.1:8001'

  return {
    plugins: [react()],
    define: {
      __APP_VERSION__: JSON.stringify(APP_VERSION),
    },
    server: {
      host: '0.0.0.0',
      // 允許雲端沙盒/預覽環境的代理主機（e2b 等）連入 dev server。
      // 生產以 vite build 靜態檔交付，此設定僅影響開發伺服器。
      allowedHosts: true,
      proxy: {
        '/api': { target: backendTarget, changeOrigin: true },
        '/ws': { target: backendTarget, ws: true, changeOrigin: true },
      },
    },
  }
})