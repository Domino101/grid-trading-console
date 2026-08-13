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
      proxy: {
        '/api': { target: backendTarget, changeOrigin: true },
        '/ws': { target: backendTarget, ws: true, changeOrigin: true },
      },
    },
  }
})
