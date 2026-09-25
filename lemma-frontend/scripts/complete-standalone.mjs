/* Turn `.next/standalone` into a tree that runs on its own.
 *
 * `LEMMA_STANDALONE=1 npm run build` has Next trace every route and copy what
 * those routes import. It cannot see the custom server: `server.mjs` and the
 * voice gateways beside it are not routes, nothing Next builds imports them,
 * and so neither they nor `ws` and `@google/genai` -- which only they use --
 * reach the standalone output. A tree without them starts Next's generated
 * `server.js`, which serves pages and answers every voice call with a 404.
 *
 * So they are traced here with the same tracer Next uses, and copied beside
 * the server Next wrote. `public/` and `.next/static` are copied too: Next
 * leaves both out on purpose (a CDN is expected to serve them), and the
 * desktop app has no CDN. The result is what the host pack copies whole.
 *
 * Usage: node scripts/complete-standalone.mjs   (after a LEMMA_STANDALONE=1 build)
 */
import { cpSync, existsSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const standalone = path.join(project, '.next/standalone');
/* The tracing root, which `next.config.ts` pins to the repository through
   `turbopack.root`: Next writes every traced path relative to it, so the
   server lands under the project's own directory name. */
const root = path.dirname(project);
const target = path.join(standalone, path.relative(root, project));

if (!existsSync(path.join(target, 'server.js'))) {
    console.error(`No standalone server at ${target}. Build with LEMMA_STANDALONE=1 first.`);
    process.exit(1);
}

/* Next's own copy of @vercel/nft, rather than a second dependency that could
   disagree with it about how a package resolves. */
const { nodeFileTrace } = createRequire(import.meta.url)('next/dist/compiled/@vercel/nft');

const entries = ['server.mjs', 'server/voice-gateway.mjs', 'server/live-gateway.mjs']
    .map((file) => path.join(project, file));
const { fileList, warnings } = await nodeFileTrace(entries, {
    base: root,
    /* `next` is already in the tree, traced by Next from the routes that
       actually run -- and following `server.mjs`'s import of it would copy the
       whole compiler, which no request ever loads. */
    ignore: (file) => /(^|\/)node_modules\/next\//.test(file),
});
for (const warning of warnings) {
    /* Optional peers the SDKs probe for and cope without. A missing module
       that matters fails loudly the first time the server starts. */
    if (!/Cannot find module/.test(String(warning))) console.warn(String(warning));
}

let copied = 0;
for (const file of fileList) {
    const destination = path.join(standalone, file);
    mkdirSync(path.dirname(destination), { recursive: true });
    cpSync(path.join(root, file), destination, { dereference: true });
    copied += 1;
}
cpSync(path.join(project, 'public'), path.join(target, 'public'), { recursive: true });
cpSync(path.join(project, '.next/static'), path.join(target, '.next/static'), { recursive: true });
console.log(`standalone: ${copied} files traced from the custom server into ${path.relative(project, target)}`);
