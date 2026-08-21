import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { execSync } from 'node:child_process'
import { lemmaSourceLoc } from './lemma-source-loc'

// Dev-only auth: resolve the current Lemma access token from the CLI and seed it
// into localStorage *before* the app bundle runs, so `npm run dev` is logged in
// in ANY browser (your own Chrome or the agent's browser) as whoever the CLI is
// logged in as. The token is resolved live on every page load (so it picks up CLI
// refreshes), is never written to a file, and — because the plugin only applies
// during `serve` — never runs in `vite build`, so it never ships in a deployed
// bundle. Override the resolver with VITE_LEMMA_DEV_TOKEN_CMD; set it empty to
// disable.
function lemmaDevAuth(env: Record<string, string>): Plugin {
  const command = env.VITE_LEMMA_DEV_TOKEN_CMD ?? 'lemma auth print-token'
  return {
    name: 'lemma-dev-auth',
    apply: 'serve',
    transformIndexHtml() {
      if (!command.trim()) return
      let token = ''
      try {
        token = execSync(command, {
          encoding: 'utf8',
          stdio: ['ignore', 'pipe', 'ignore'],
        }).trim()
      } catch {
        // Not logged in / CLI unavailable: leave the page unauthenticated so the
        // app's normal auth redirect still applies.
        return
      }
      if (!token) return
      return [
        {
          tag: 'script',
          injectTo: 'head-prepend',
          children: `try{localStorage.setItem("lemma_token",${JSON.stringify(
            token,
          )})}catch(e){}`,
        },
      ]
    },
  }
}

// Apps are served by the host at the root of their subdomain, so the base
// path is '/' in dev and resolved from VITE_LEMMA_APP_BASE_PATH in prod.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const sourceLocEnabled = (env.LEMMA_APP_SOURCE_LOC ?? '1') !== '0'

  // Optional same-origin dev proxy (opt in with `lemma apps init --proxy`). When
  // LEMMA_DEV_PROXY_TARGET is set, the SDK talks to a same-origin '/api' path and
  // Vite forwards it to the backend, so there is no cross-origin CORS in dev.
  const proxyTarget = env.LEMMA_DEV_PROXY_TARGET
  const server = proxyTarget
    ? {
        proxy: {
          '/api': {
            target: proxyTarget,
            changeOrigin: true,
            secure: false,
            rewrite: (path: string) => path.replace(/^\/api/, ''),
          },
        },
      }
    : undefined

  return {
    base: mode === 'production' ? env.VITE_LEMMA_APP_BASE_PATH || '/' : '/',
    plugins: [
      // `data-lemma-loc` / `data-lemma-component` on every host element, so the
      // Lemma app editor's picker resolves a click to a file and a line instead
      // of to anonymous minified markup. See ./lemma-source-loc.ts. Set
      // LEMMA_APP_SOURCE_LOC=0 to ship without the stamps (the picker still
      // works, but it can only report DOM, not source).
      react(
        sourceLocEnabled ? { babel: { plugins: [lemmaSourceLoc] } } : undefined,
      ),
      lemmaDevAuth(env),
    ],
    server,
    resolve: {
      // Force a single copy of React (and friends). When the SDK is linked from a
      // local checkout (`lemma apps init --sdk-path` / a `file:` dependency), that
      // checkout ships its own node_modules/react; without dedupe, Vite bundles two
      // React instances and the SDK's hooks hit a null dispatcher ("Cannot read
      // properties of null (reading 'useState')") at runtime.
      dedupe: ['react', 'react-dom', '@tanstack/react-query'],
    },
  }
})
