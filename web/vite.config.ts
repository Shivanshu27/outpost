import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Built assets are served by FastAPI from `src/outpost/resources/web` in
// production mode — one process, one port (ADR-0010). `_mount_ui` in
// api/app.py looks for `index.html` and an `assets/` directory there.
//
// `base: './'` keeps asset URLs relative so the bundle does not care what path
// it is mounted at.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    outDir: '../src/outpost/resources/web',
    // The output dir lives outside Vite's project root, so this must be
    // explicit or Vite refuses to clean it between builds.
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8420',
        changeOrigin: false,
      },
    },
  },
});
