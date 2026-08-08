import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    // Convenience proxy: /api and /health are forwarded to the FastAPI
    // backend so the app also works from http://localhost:5173 without CORS.
    // The API service layer points at http://127.0.0.1:8000 by default; the
    // proxy is a fallback for browser environments that strip request bodies
    // on GET (see src/services/api.js).
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
