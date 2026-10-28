import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  // Emit fonts/images as real files (never data: URIs) so the strict production CSP (font-src 'self') stays valid.
  build: { assetsInlineLimit: 0 },
  server: { port: 5173, proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false } } },
})
