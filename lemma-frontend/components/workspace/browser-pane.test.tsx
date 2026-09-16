// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/react';
import { BrowserPane } from './browser-pane';

const sent = vi.hoisted(() => ({ messages: [] as Record<string, unknown>[] }));

vi.mock('@/lib/workspace/browser-view', async (importOriginal) => {
    const actual =
        await importOriginal<typeof import('@/lib/workspace/browser-view')>();
    return {
        ...actual,
        openBrowserView: (options: {
            onState: (s: string) => void;
            onFrame: (f: unknown) => void;
        }) => {
            // Live, with a frame, so the canvas has a size to hit-test against.
            options.onState('live');
            options.onFrame({
                pictureWidth: 949,
                pictureHeight: 720,
                bitmap: { naturalWidth: 949, naturalHeight: 720 },
            });
            return { send: (m: Record<string, unknown>) => sent.messages.push(m), close: vi.fn() };
        },
    };
});

afterEach(() => {
    sent.messages = [];
    cleanup();
});

const mouseMessages = () => sent.messages.filter((m) => m.type === 'input_mouse');

describe('what the pane tells the page about the mouse', () => {
    it('reports a held button while dragging and none while hovering', () => {
        // `buttons` is which buttons are held *now*, not which button the event
        // is about. Both hand-written answers were wrong in opposite
        // directions: a constant 1 said the button was still down on release,
        // so a click never completed; 1-on-press-only reported no button held
        // during a move, which is a drag reported as a hover -- no text
        // selection, no slider, no drag-and-drop.
        const { container } = render(<BrowserPane origin="https://example.com" autoControl />);
        const canvas = container.querySelector('canvas');
        expect(canvas).toBeTruthy();

        fireEvent.mouseDown(canvas!, { clientX: 10, clientY: 10, button: 0, buttons: 1 });
        fireEvent.mouseMove(canvas!, { clientX: 40, clientY: 40, button: 0, buttons: 1 });
        fireEvent.mouseUp(canvas!, { clientX: 40, clientY: 40, button: 0, buttons: 0 });
        fireEvent.mouseMove(canvas!, { clientX: 60, clientY: 60, button: 0, buttons: 0 });

        const held = mouseMessages().map((m) => [m.eventType, m.buttons]);
        expect(held).toEqual([
            ['mousePressed', 1],
            ['mouseMoved', 1], // the drag
            ['mouseReleased', 0], // nothing held after a release
            ['mouseMoved', 0], // the hover
        ]);
    });
});
