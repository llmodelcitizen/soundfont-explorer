import { defineConfig } from 'vitest/config';

export default defineConfig({
  base: '/',
  worker: { format: 'es' },
  build: {
    target: 'es2022',
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          'opus-wasm': ['ogg-opus-decoder'],
        },
      },
    },
  },
  server: {
    port: 5173,
    // During development the manifests/audio come from `python3 -m http.server` on out/public
    proxy: {
      '/songs.json': 'http://127.0.0.1:8000',
      '/c': 'http://127.0.0.1:8000',
      '/s': 'http://127.0.0.1:8000',
      '/a': 'http://127.0.0.1:8000',
    },
  },
  test: {
    include: ['test/unit/**/*.test.ts'],
    environment: 'node',
  },
});
