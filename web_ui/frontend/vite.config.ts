import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// API calls are always relative in browser code. This proxy exists only for the
// Vite development server, so remote browsers never need to reach localhost.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendTarget = env.VITE_BACKEND_TARGET || 'http://127.0.0.1:8001'

  return {
    plugins: [react()],
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
