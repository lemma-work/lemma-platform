// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';

/**
 * The explorer's own two jobs, neither of which is drawing a tree.
 *
 * `react-arborist` virtualizes, so it wants a pixel height rather than a
 * flexbox one, and the explorer has to measure its container and hand one
 * over. That measurement is the part that broke: it was taken once, on the
 * first commit, from a container that is not always there on the first
 * commit. And the tree it draws has to be able to say "this folder could not
 * be read", because the alternative -- drawing nothing -- is how a broken
 * folder comes to look like an empty one.
 */

/**
 * jsdom has no `ResizeObserver`, and jsdom lays nothing out, so a real one
 * would never fire either. What matters here is only *whether the explorer
 * attached one to the container that eventually appeared*, so the stub
 * records its target and lets the test drive it.
 */
class FakeResizeObserver {
    readonly callback: ResizeObserverCallback;
    constructor(callback: ResizeObserverCallback) {
        this.callback = callback;
        observers.push(this);
    }
    observe(target: Element) {
        this.observed.push(target);
    }
    unobserve() {}
    disconnect() {
        this.disconnected = true;
    }
    readonly observed: Element[] = [];
    disconnected = false;
}
const observers: FakeResizeObserver[] = [];
vi.stubGlobal('ResizeObserver', FakeResizeObserver);

/**
 * Only the explorer's own observer.
 *
 * `react-resizable-panels` makes two of its own for the split, so counting
 * every construction would measure that library rather than this component.
 */
const measuring = () =>
    observers.filter((observer) =>
        observer.observed.some((target) =>
            target.className.includes('overflow-hidden'),
        ),
    );

/** A `Tree` that reports the geometry it was given and draws its rows. */
vi.mock('react-arborist', () => ({
    Tree: ({
        data,
        height,
        width,
        children,
    }: {
        data: Array<{ id: string; name: string }>;
        height: number;
        width: number;
        children: (props: unknown) => React.ReactNode;
    }) => (
        <div data-testid="tree" data-height={height} data-width={width}>
            {data.map((node) => (
                <div key={node.id}>
                    {children({
                        node: {
                            data: node,
                            isOpen: false,
                            isSelected: false,
                            isLeaf: true,
                            toggle: () => {},
                            select: () => {},
                            activate: () => {},
                        },
                        style: {},
                    })}
                </div>
            ))}
        </div>
    ),
}));

vi.mock('@monaco-editor/react', () => ({ default: () => <div /> }));
vi.mock('next-themes', () => ({ useTheme: () => ({ resolvedTheme: 'light' }) }));

const tree = vi.hoisted(() => ({
    value: {} as Record<string, unknown>,
}));
vi.mock('@/lib/hooks/use-directory-tree', () => ({
    useDirectoryTree: () => tree.value,
}));
vi.mock('@/lib/hooks/use-workspace-files', () => ({
    useWorkspaceFile: () => ({ data: undefined, isPending: false, error: null }),
    WORKSPACE_ROOT: '/home/user/lemma',
    HOME_ROOT: '/home/user',
}));

import { FileExplorer } from './file-explorer';

const state = (over: Record<string, unknown> = {}) => ({
    data: [],
    onToggle: () => {},
    sleeping: false,
    loading: false,
    error: null,
    missing: false,
    ...over,
});

afterEach(() => {
    observers.length = 0;
    cleanup();
});

describe('measuring a container that is not there yet', () => {
    it('attaches to the tree container when a sleeping computer wakes', async () => {
        // The bug: the measurement lived in an effect with `[]` deps, so it
        // ran once on the first commit and read a ref that was null --
        // because a sleeping sandbox renders a sentence, not the tree. Waking
        // it mounted the container with nothing watching it, the measured
        // height stayed 0, and the tree -- which will not render without one
        // -- stayed blank for the rest of the page's life.
        tree.value = state({ sleeping: true });
        const { rerender } = render(
            <FileExplorer selected={null} onSelect={() => {}} />,
        );
        expect(measuring()).toHaveLength(0);

        tree.value = state({ data: [{ id: '/a', name: 'a', kind: 'file', sizeBytes: 1 }] });
        rerender(<FileExplorer selected={null} onSelect={() => {}} />);

        await waitFor(() => expect(measuring()).toHaveLength(1));
        expect(measuring()[0].observed).toHaveLength(1);
    });

    it('draws the tree once its container reports a height', async () => {
        tree.value = state({ data: [{ id: '/a', name: 'a', kind: 'file', sizeBytes: 1 }] });
        render(<FileExplorer selected={null} onSelect={() => {}} />);
        await waitFor(() => expect(measuring()).toHaveLength(1));

        measuring()[0].callback(
            [{ contentRect: { width: 300, height: 600 } } as ResizeObserverEntry],
            {} as ResizeObserver,
        );

        await waitFor(() => expect(screen.getByTestId('tree')).toBeTruthy());
        expect(screen.getByTestId('tree').getAttribute('data-height')).toBe('600');
    });
});

describe('saying what is wrong instead of showing nothing', () => {
    it('reports a root it could not read', () => {
        tree.value = state({ error: new Error('sandbox unreachable') });
        render(<FileExplorer selected={null} onSelect={() => {}} />);

        expect(screen.getByText(/could not be read/)).toBeTruthy();
    });

    it('tells a missing root from an empty one', () => {
        tree.value = state({ missing: true });
        render(<FileExplorer selected={null} onSelect={() => {}} />);

        expect(screen.getByText(/no folder at/)).toBeTruthy();
    });

    it('draws a failed subfolder as a sentence, not as a file', async () => {
        tree.value = state({
            data: [
                {
                    id: '/a#notice',
                    name: 'This folder could not be read.',
                    kind: 'file',
                    sizeBytes: 0,
                    problem: 'This folder could not be read.',
                },
            ],
        });
        render(<FileExplorer selected={null} onSelect={() => {}} />);
        await waitFor(() => expect(measuring()).toHaveLength(1));
        measuring()[0].callback(
            [{ contentRect: { width: 300, height: 600 } } as ResizeObserverEntry],
            {} as ResizeObserver,
        );

        await waitFor(() =>
            expect(screen.getByText('This folder could not be read.')).toBeTruthy(),
        );
        // Not a button: there is nothing to open, and a row that looks
        // clickable and does nothing is worse than a plain line of text.
        expect(screen.queryByRole('button')).toBeNull();
    });
});
