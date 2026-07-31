import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

const root = dirname(fileURLToPath(import.meta.url));

// Pure-logic unit tests only (no component/DOM stack). Keep the include tight so
// vitest never tries to load broad Next/React component surfaces.
export default defineConfig({
    resolve: {
        alias: {
            '@': root,
        },
    },
    test: {
        environment: 'node',
        include: [
            'components/agents/agent-runtime-helpers.{test,spec}.ts',
            'components/auth/portal/auth/**/*.{test,spec}.ts',
            'lib/**/*.{test,spec}.ts',
        ],
    },
});
