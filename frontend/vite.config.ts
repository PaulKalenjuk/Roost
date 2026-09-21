import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build straight into the Django project so one container serves both the API
// and the SPA. During `npm run dev`, Vite proxies API/admin/media to Django.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../backend/spa',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8686',
      '/admin': 'http://127.0.0.1:8686',
      '/media': 'http://127.0.0.1:8686',
    },
  },
})
