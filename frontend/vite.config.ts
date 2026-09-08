/// <reference types="vitest" />
import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

/**
 * Vite configuration.
 *
 * In development the front end and the API live on different ports, so Vite
 * proxies `/api` to uvicorn. That mirrors what nginx does in production: the
 * browser only ever talks to one origin, and no CORS preflight is involved in
 * either environment. `VITE_API_BASE` exists as an escape hatch for pointing a
 * local UI at a remote backend.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  // TODO(R18): un seul chunk de ~590 kB, dominé par Recharts. Un import
  // dynamique du ChartRenderer + manualChunks règlent le problème.
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_DEV_API_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
        // Server-Sent Events must not be buffered by the dev proxy either.
        ws: false,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    css: false,
  },
})
