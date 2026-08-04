import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': new URL('./src', import.meta.url).pathname },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    // Same-origin API calls in dev; no CORS juggling in the app code.
    proxy: {
      '/api': { target: process.env.BACKEND_URL ?? 'http://localhost:8000', changeOrigin: true },
    },
  },
})
