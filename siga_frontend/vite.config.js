import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Destino del proxy de desarrollo hacia el Core Gateway:
//  - Docker Compose define VITE_PROXY_TARGET=http://siga-core:8000
//  - Ejecucion nativa (start_local.ps1) usa el valor por defecto localhost
const proxyTarget = process.env.VITE_PROXY_TARGET || 'http://localhost:8000'

const proxied = (extra = {}) => ({ target: proxyTarget, changeOrigin: true, ...extra })

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    include: ['html5-qrcode']
  },
  server: {
    watch: {
      usePolling: true,
    },
    host: true, // needed for docker
    proxy: {
      '/api': proxied(),
      '/auth': proxied(),
      '/core': proxied(),
      '/ws': proxied({ ws: true })
    }
  }
})
