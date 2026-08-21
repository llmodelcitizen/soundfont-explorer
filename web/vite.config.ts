import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  base: '/',
  worker: { format: 'es' },
  resolve: {
    alias: {
      // never ship the optional 4 MB ML-enhancement decoder (speechQualityEnhancement is never set)
      '@wasm-audio-decoders/opus-ml': fileURLToPath(new URL('./src/shims/opus-ml-empty.ts', import.meta.url)),
    },
  },
  build: {
    target: 'es2022',
    sourcemap: false,
    // never inline assets as data: URLs — the CSP only allows connect-src 'self' (the decode probe is fetched)
    assetsInlineLimit: 0,
  },
  server: {
    port: 5173,
    // During development the manifests/audio come from `python3 -m http.server` on out/public
    proxy: {
      '^/songs\\.json$': 'http://127.0.0.1:8000',
      '^/c/': 'http://127.0.0.1:8000',
      '^/s/': 'http://127.0.0.1:8000',
      '^/a/': 'http://127.0.0.1:8000',
    },
  },
  test: {
    include: ['test/unit/**/*.test.ts'],
    environment: 'node',
  },
});
