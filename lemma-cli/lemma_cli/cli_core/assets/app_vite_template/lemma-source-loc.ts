import { relative } from 'node:path'

// Babel plugin: stamp every host element with where it was written.
//
// Lemma's app editor lets someone click an element in the running app and hand
// it to the agent to change. For that to be an *edit* rather than a guess, the
// click has to resolve to a file and a line — and a production bundle has
// neither: esbuild mangles component names and React's automatic runtime only
// records source locations in dev. So the location is written into the markup
// itself, at build time, where it survives minification:
//
//   <div data-lemma-loc="src/OrderRow.tsx:42:5" data-lemma-component="OrderRow">
//
// Host elements only. An attribute added to `<OrderRow />` would arrive as a
// prop that the component is free to drop, so it would describe the call site
// of something that may never reach the DOM. Host elements *are* the DOM, which
// is what the picker hit-tests.
//
// `data-lemma-component` names the component whose body wrote the element, not
// the element's own tag. That is what lets the picker snap a click to a
// component boundary — the nearest ancestor written by a *different* component
// — instead of selecting whichever nested `<div>` happened to be under the
// cursor.

const LOCATION_ATTRIBUTE = 'data-lemma-loc'
const COMPONENT_ATTRIBUTE = 'data-lemma-component'

interface BabelTypes {
  jsxAttribute: (name: unknown, value: unknown) => unknown
  jsxIdentifier: (name: string) => unknown
  stringLiteral: (value: string) => unknown
}

// Loosely typed on purpose: the template does not depend on @babel/core, and
// `tsc -b` only covers `src`, so pulling in Babel's AST types would cost the
// scaffolded app a dependency it otherwise never needs.
/* eslint-disable @typescript-eslint/no-explicit-any */
type BabelPath = any

function isHostElement(name: any): boolean {
  // `<div>` yes, `<OrderRow>` no, `<Foo.Bar>` no — see the note above.
  return name?.type === 'JSXIdentifier' && /^[a-z]/.test(name.name ?? '')
}

function hasAttribute(node: any, attributeName: string): boolean {
  return (node.attributes ?? []).some(
    (attribute: any) =>
      attribute.type === 'JSXAttribute' && attribute.name?.name === attributeName,
  )
}

/** The nearest enclosing function that reads as a React component. */
function enclosingComponentName(path: BabelPath): string | null {
  let current = path.getFunctionParent()
  while (current) {
    const node = current.node
    const candidate =
      node.id?.name ??
      (current.parentPath?.isVariableDeclarator()
        ? current.parentPath.node.id?.name
        : null) ??
      (node.type === 'ClassMethod' ? current.parentPath?.parentPath?.node?.id?.name : null)
    if (typeof candidate === 'string' && /^[A-Z]/.test(candidate)) return candidate
    current = current.getFunctionParent()
  }
  return null
}

export function lemmaSourceLoc({ types: t }: { types: BabelTypes }) {
  return {
    name: 'lemma-source-loc',
    visitor: {
      JSXOpeningElement(path: BabelPath, state: any) {
        const node = path.node
        if (!isHostElement(node.name)) return
        if (hasAttribute(node, LOCATION_ATTRIBUTE)) return

        const filename: string | undefined = state.filename
        const start = node.loc?.start
        if (!filename || !start) return

        const root: string = state.file?.opts?.root ?? state.cwd ?? process.cwd()
        // Posix separators so the value reads the same on every OS — the agent
        // uses it as a repo-relative path, not a local filesystem path.
        const location = relative(root, filename).split('\\').join('/')
        // Babel counts columns from 0; editors count from 1.
        const stamp = `${location}:${start.line}:${start.column + 1}`

        node.attributes.push(
          t.jsxAttribute(t.jsxIdentifier(LOCATION_ATTRIBUTE), t.stringLiteral(stamp)),
        )

        const component = enclosingComponentName(path)
        if (component && !hasAttribute(node, COMPONENT_ATTRIBUTE)) {
          node.attributes.push(
            t.jsxAttribute(
              t.jsxIdentifier(COMPONENT_ATTRIBUTE),
              t.stringLiteral(component),
            ),
          )
        }
      },
    },
  }
}
