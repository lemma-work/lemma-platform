import React from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthGuard, useCurrentUser, useTableList } from 'lemma-sdk/react'
import { Boxes, RefreshCw, Sparkles } from 'lucide-react'
import { lemmaClient } from './lemma-client'
import './styles.css'

const APP_TITLE = '__LEMMA_APP_TITLE__'

// One QueryClient per app. The generated `lemma-sdk/react` hooks (useTableList,
// useRecordList, useRecordCreate, ...) are TanStack-Query hooks: they cache and dedupe
// reads, and — because the backend declares it — a mutation like useRecordCreate
// auto-refreshes the matching list with no manual refetch. Wrap the app once.
const queryClient = new QueryClient()

function asErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function getItems<T>(value: unknown): T[] {
  if (
    value &&
    typeof value === 'object' &&
    'items' in value &&
    Array.isArray((value as { items: unknown }).items)
  ) {
    return (value as { items: T[] }).items
  }
  return []
}

function App() {
  const { user } = useCurrentUser({ client: lemmaClient })

  // Generated hook: fetches + caches this pod's tables. No useState/useEffect needed.
  // `lemmaClient.podId` is the host-injected pod at runtime (VITE_LEMMA_POD_ID is a
  // dev-only fallback), so the same build works on any pod.
  const tablesQuery = useTableList(lemmaClient, lemmaClient.podId)
  const tables = getItems<{ name?: string }>(tablesQuery.data)
    .map((table) => table.name ?? '')
    .filter(Boolean)

  return (
    <main className="app-shell">
      <header className="hero">
        <div className="brand">
          <Boxes size={22} />
          <div>
            <p>{APP_TITLE}</p>
            <span>Lemma app</span>
          </div>
        </div>
        <button
          className="utility"
          type="button"
          onClick={() => void tablesQuery.refetch()}
          disabled={tablesQuery.isFetching}
        >
          <RefreshCw size={16} />
          Refresh
        </button>
      </header>

      <section className="panel">
        <div className="section-title">
          <Sparkles size={18} />
          You are connected
        </div>
        <p className="greeting">
          Signed in as <strong>{user?.email ?? 'your account'}</strong>.
        </p>
        {tablesQuery.error ? <div className="alert">{asErrorMessage(tablesQuery.error)}</div> : null}
        {tablesQuery.isLoading ? (
          <p className="muted">Loading tables...</p>
        ) : tables.length ? (
          <>
            <p className="muted">Tables in this pod:</p>
            <ul className="table-list">
              {tables.map((table) => (
                <li key={table}>{table}</li>
              ))}
            </ul>
          </>
        ) : (
          <p className="muted">
            No tables yet. Add tables to your pod, then build real views with the{' '}
            <code>lemma-sdk/react</code> hooks — e.g. <code>useRecordList</code> to read and{' '}
            <code>useRecordCreate</code> to write (creates auto-refresh the list).
          </p>
        )}
      </section>

      <p className="footnote">
        Edit <code>src/main.tsx</code> to build your operator workflow. Deploy with{' '}
        <code>lemma apps deploy</code>.
      </p>
    </main>
  )
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthGuard
        client={lemmaClient}
        loadingFallback={
          <main className="app-shell">
            <section className="panel">Checking access...</section>
          </main>
        }
      >
        <App />
      </AuthGuard>
    </QueryClientProvider>
  </React.StrictMode>,
)
