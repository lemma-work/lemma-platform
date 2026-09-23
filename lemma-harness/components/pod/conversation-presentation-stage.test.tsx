// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import { ConversationPresentationStage } from './conversation-presentation-stage';

/**
 * Making the stage bigger without leaving the page.
 *
 * "Full screen" used to be a link to a route of its own, opened in a new
 * browser tab. That is the wrong trade for this pane: looking through a
 * machine's files, or watching its browser, is a thing you do *about* the
 * conversation beside it, and being thrown into a second tab to do it loses
 * the thing you were reading. The stage already collapses to exactly this
 * shape on a narrow container, so expanding is the same layout asked for on
 * purpose.
 *
 * The standalone link stays, as the small icon it always was. Some people do
 * want a tab; it is just no longer the only way to get room.
 */

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('next/link', () => ({
    default: ({ children, ...rest }: { children: React.ReactNode }) => (
        <a {...rest}>{children}</a>
    ),
}));
vi.mock('@/components/app/app-context', () => ({
    useAppPage: () => ({ page: null, isResolving: false }),
}));
vi.mock('@/components/app/app-launch', () => ({ AppFrame: () => null }));

afterEach(cleanup);

const renderStage = () =>
    render(
        <ConversationPresentationStage
            podId="pod-7"
            resourceHref=""
            stageTitle="Your computer"
            stageStandaloneHref="/pod/pod-7/computer"
            stageBodyOverride={<div data-testid="stage-body" />}
            onClose={() => {}}
        >
            <div data-testid="conversation" />
        </ConversationPresentationStage>,
    );

const layout = (container: HTMLElement) =>
    container.querySelector('.conversation-presentation-layout')!;

describe('giving the stage the whole page', () => {
    it('starts beside the conversation', () => {
        const { container } = renderStage();

        expect(layout(container).className).not.toContain('is-stage-expanded');
        expect(screen.getByTestId('conversation')).toBeTruthy();
    });

    it('takes the conversation’s half when asked, and gives it back', () => {
        const { container } = renderStage();

        fireEvent.click(screen.getByRole('button', { name: 'Full screen' }));
        expect(layout(container).className).toContain('is-stage-expanded');

        fireEvent.click(screen.getByRole('button', { name: 'Show the conversation' }));
        expect(layout(container).className).not.toContain('is-stage-expanded');
    });

    it('never unmounts the body, so a live view keeps its socket', () => {
        // The reason this is a class on the layout rather than a different
        // tree: the browser pane holds an open WebSocket to the sandbox, and
        // expanding must not cost a reconnect and a fresh Chrome.
        const { container } = renderStage();
        const before = screen.getByTestId('stage-body');

        fireEvent.click(screen.getByRole('button', { name: 'Full screen' }));

        expect(screen.getByTestId('stage-body')).toBe(before);
        expect(layout(container).className).toContain('is-stage-expanded');
    });

    it('still offers a tab of its own for anybody who wants one', () => {
        renderStage();

        expect(
            screen.getByRole('link', { name: 'Open in new tab' }).getAttribute('href'),
        ).toBe('/pod/pod-7/computer');
    });
});
