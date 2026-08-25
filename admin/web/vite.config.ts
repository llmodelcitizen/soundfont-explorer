import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['test/unit/**/*.test.ts'],
    // the views build real DOM nodes (no framework), so the unit tests run against happy-dom
    environment: 'happy-dom',
  },
});
