import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Local dev: backend runs on :8000, UI on :5173. Proxy keeps API calls
  // same-origin so api.js needs no hardcoded host (Docker serves both
  // from :8000 with no proxy needed).
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/files": "http://127.0.0.1:8000",
    },
  },
})
