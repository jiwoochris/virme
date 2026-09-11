import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    allowedHosts: ['deck-macmini.tailb3396d.ts.net'],
    proxy: {
      '/api/stylegan': {
        target: 'http://127.0.0.1:8766',
        changeOrigin: false,
        rewrite: (path) => path.replace(/^\/api\/stylegan/, ''),
      },
      '/api/core': {
        target: 'http://127.0.0.1:8767',
        changeOrigin: false,
        rewrite: (path) => path.replace(/^\/api\/core/, ''),
      },
    },
  },
})
