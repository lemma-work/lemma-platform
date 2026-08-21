import { describe, expect, it } from 'vitest';

import {
    APP_EDITOR_MESSAGE,
    buildAppEditorPrefill,
    describeAppEditorSelection,
    parseAppEditorMessage,
    type AppEditorSelection,
} from '../app-editor';

const selection: AppEditorSelection = {
    app: { name: 'orders', id: 'app-1', podId: 'pod-1' },
    route: '/orders/42',
    source: { file: 'src/components/OrderRow.tsx', line: 42, column: 5 },
    component: 'OrderRow',
    componentChain: ['OrderRow', 'OrdersTable', 'OrdersPage'],
    tag: 'div',
    domId: null,
    className: 'order-row',
    domPath: 'main > div:nth-of-type(2) > div',
    text: 'Order #1042 · Shipped',
    html: '<div class="order-row">Order #1042</div>',
    rect: { x: 10, y: 20, width: 300, height: 48 },
    styles: { display: 'flex', 'font-size': '13px', 'z-index': '2' },
};

function selectionMessage(overrides: Record<string, unknown> = {}) {
    return {
        type: APP_EDITOR_MESSAGE.selection,
        selection: { ...selection, ...overrides },
    };
}

describe('parseAppEditorMessage', () => {
    it('parses a selection into the typed shape', () => {
        const message = parseAppEditorMessage(selectionMessage());

        expect(message).toEqual({ type: APP_EDITOR_MESSAGE.selection, selection });
    });

    it('rejects anything that is not one of the four message types', () => {
        expect(parseAppEditorMessage(null)).toBeNull();
        expect(parseAppEditorMessage('lemma-app-editor:selection')).toBeNull();
        expect(parseAppEditorMessage({ type: 'lemma-app-theme' })).toBeNull();
        expect(parseAppEditorMessage([selectionMessage()])).toBeNull();
    });

    it('rejects a selection with no element in it', () => {
        // A payload without a tag names nothing the agent could go and change.
        expect(parseAppEditorMessage(selectionMessage({ tag: null }))).toBeNull();
        expect(parseAppEditorMessage({ type: APP_EDITOR_MESSAGE.selection })).toBeNull();
    });

    it('drops fields of the wrong type instead of trusting the frame', () => {
        const message = parseAppEditorMessage(
            selectionMessage({
                route: 42,
                componentChain: ['OrderRow', 7, null],
                styles: { display: 'flex', padding: 12 },
                rect: { x: 'left', y: 20, width: 300, height: 48 },
                source: { file: 'src/App.tsx', line: 'twelve', column: 3 },
            }),
        );

        expect(message).not.toBeNull();
        const parsed = (message as { selection: AppEditorSelection }).selection;
        expect(parsed.route).toBe('');
        expect(parsed.componentChain).toEqual(['OrderRow']);
        expect(parsed.styles).toEqual({ display: 'flex' });
        expect(parsed.rect).toEqual({ x: 0, y: 20, width: 300, height: 48 });
        expect(parsed.source).toEqual({ file: 'src/App.tsx', line: null, column: 3 });
    });

    it('reads a source location with no file as no source at all', () => {
        const message = parseAppEditorMessage(selectionMessage({ source: { line: 3 } }));

        expect((message as { selection: AppEditorSelection }).selection.source).toBeNull();
    });

    it('parses the select-mode echo the app sends on Escape', () => {
        expect(parseAppEditorMessage({ type: APP_EDITOR_MESSAGE.selectMode })).toEqual({
            type: APP_EDITOR_MESSAGE.selectMode,
            active: false,
        });
        expect(
            parseAppEditorMessage({ type: APP_EDITOR_MESSAGE.selectMode, active: true }),
        ).toEqual({ type: APP_EDITOR_MESSAGE.selectMode, active: true });
    });
});

describe('describeAppEditorSelection', () => {
    it('names the component and where it is written', () => {
        expect(describeAppEditorSelection(selection)).toBe(
            'OrderRow — src/components/OrderRow.tsx:42',
        );
    });

    it('falls back to the tag when the app carries no source stamps', () => {
        expect(
            describeAppEditorSelection({ ...selection, component: null, source: null }),
        ).toBe('<div>');
    });
});

describe('buildAppEditorPrefill', () => {
    it('opens with the facts and leaves the request to the person', () => {
        const prefill = buildAppEditorPrefill(selection, 'orders');

        expect(prefill).toContain('Editing the "orders" app');
        expect(prefill).toContain('- Source: src/components/OrderRow.tsx:42:5');
        expect(prefill).toContain('- Component: OrderRow');
        expect(prefill).toContain('- Inside: OrdersTable → OrdersPage');
        expect(prefill).toContain('- Route: /orders/42');
        expect(prefill).toContain('- Text: Order #1042 · Shipped');
        expect(prefill).toContain('```html');
        // Ends on a blank line, so typing continues below the block.
        expect(prefill.endsWith('\n\n')).toBe(true);
    });

    it('reports only styles worth arguing about', () => {
        const prefill = buildAppEditorPrefill(selection, 'orders');

        expect(prefill).toContain('display: flex; font-size: 13px');
        expect(prefill).not.toContain('z-index');
    });

    it('leans on the DOM path when there is no source location', () => {
        const prefill = buildAppEditorPrefill(
            { ...selection, source: null, component: null, componentChain: [] },
            'orders',
        );

        expect(prefill).not.toContain('- Source:');
        expect(prefill).toContain('- DOM path: main > div:nth-of-type(2) > div');
    });
});
