/// <reference types="vitest/config" />
// defineConfig comes from 'vite', not 'vitest/config': `vite build` is what the deploy path
// runs (admin/scripts/deploy.sh), and it must not need the test stack installed. The
// reference above is types-only, so the `test` block below still typechecks.
import { defineConfig } from 'vite';

export default defineConfig({
  test: {
    include: ['test/unit/**/*.test.ts'],
    // the views build real DOM nodes (no framework), so the unit tests run against happy-dom
    environment: 'happy-dom',
  },
});
