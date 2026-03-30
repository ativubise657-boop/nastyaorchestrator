import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Проксируем API-запросы на backend
export default defineConfig({
  plugins: [react()],
  server: {
    watch: {
      usePolling: true,
      interval: 1000,
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8781',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
