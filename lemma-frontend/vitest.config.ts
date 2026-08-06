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
            // What the Computers card says about this machine, extracted from the
            // card for the same reason as the helpers above: the logic is pure,
            // the component around it is not.
            'components/agents/this-computer-status.{test,spec}.ts',
            'components/auth/portal/auth/**/*.{test,spec}.ts',
            // Which kinds need an address typed in, and how an install reads
            // back as a line of text. Pure predicates over the catalog.
            'components/connectors/connector-utils.{test,spec}.ts',
            // Step ordering for local onboarding. Named file by file, like the
            // agent-runtime helpers above, so this stays a list of pure-logic
            // modules rather than becoming a glob over components.
            'components/onboarding/local-setup.{test,spec}.ts',
            'lib/**/*.{test,spec}.ts',
        ],
    },
});
