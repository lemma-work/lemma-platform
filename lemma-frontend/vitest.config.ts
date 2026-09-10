import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

const root = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
    resolve: {
        alias: {
            '@': root,
        },
    },
    test: {
        environment: 'node',
        include: [
            'components/**/*.{test,spec}.{ts,tsx}',
            'lib/**/*.{test,spec}.{ts,tsx}',
            'app/**/*.{test,spec}.{ts,tsx}',
        ],
    },
});
