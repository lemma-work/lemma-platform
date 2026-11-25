"use client";

import {
    createContext,
    useContext,
    useEffect,
    useMemo,
    useRef,
    useState,
    useSyncExternalStore,
    type ReactNode,
} from "react";
import { usePathname } from "next/navigation";
import { CloseIcon, MenuIcon, SearchIcon } from "@/ui/icons";
import { usePanelFocus } from "@/ui/focus-trap";
import type { DocsNavGroup } from "./docs-content";
import type { DocsHit } from "./docs-search";
import { DocsNav } from "./docs-nav";
import { DocsSearchDialog } from "./docs-search-dialog";

/* What every docs page sits in: the site's header, the guide list at the
   left, and the search over all of it, on ⌘K or Ctrl+K from anywhere. Below
   960px only the reading column shows, and the guide list opens over it. */

type DocsShellApi = { openSearch: () => void; openGuides: () => void };

const DocsShellContext = createContext<DocsShellApi>({
    openSearch: () => {},
    openGuides: () => {},
});

export function DocsShell({
    header,
    nav,
    index,
    children,
}: {
    header: ReactNode;
    nav: DocsNavGroup[];
    index: DocsHit[];
    children: ReactNode;
}) {
    const [search, setSearch] = useState(false);
    const [guides, setGuides] = useState(false);
    const pathname = usePathname() ?? "/docs";
    const current = pathname.replace(/^\/docs\/?/, "").replace(/\/$/, "");
    const list = useRef<HTMLElement>(null);

    useEffect(() => {
        const onKey = (event: KeyboardEvent) => {
            if (
                (event.metaKey || event.ctrlKey) &&
                !event.altKey &&
                event.key.toLowerCase() === "k"
            ) {
                event.preventDefault();
                setGuides(false);
                setSearch((open) => !open);
            }
        };
        document.addEventListener("keydown", onKey);
        return () => document.removeEventListener("keydown", onKey);
    }, []);

    /* The current page, in view in the list's own scroll when it would
       otherwise sit below it. */
    useEffect(() => {
        const box = list.current;
        const link = box?.querySelector<HTMLElement>('[aria-current="page"]');
        if (!box || !link) return;
        const top = link.offsetTop;
        if (top < box.scrollTop || top > box.scrollTop + box.clientHeight - 48)
            box.scrollTop = Math.max(0, top - box.clientHeight / 3);
    }, [current]);

    const api = useMemo<DocsShellApi>(
        () => ({
            openSearch: () => {
                setGuides(false);
                setSearch(true);
            },
            openGuides: () => setGuides(true),
        }),
        [],
    );

    return (
        <DocsShellContext.Provider value={api}>
            {header}
            <div className="docs-grid">
                <aside ref={list} className="docs-guides" aria-label="All pages">
                    <DocsNav nav={nav} current={current} />
                </aside>
                {children}
            </div>
            {guides && (
                <GuidesDrawer onClose={() => setGuides(false)}>
                    <DocsNav
                        nav={nav}
                        current={current}
                        onNavigate={() => setGuides(false)}
                    />
                </GuidesDrawer>
            )}
            {search && (
                <DocsSearchDialog
                    index={index}
                    onClose={() => setSearch(false)}
                />
            )}
        </DocsShellContext.Provider>
    );
}

function GuidesDrawer({
    onClose,
    children,
}: {
    onClose: () => void;
    children: ReactNode;
}) {
    const panel = useRef<HTMLDivElement>(null);
    const keys = usePanelFocus(panel, onClose);
    return (
        <div
            className="docs-drawer-scrim"
            onMouseDown={(event) => {
                if (event.target === event.currentTarget) onClose();
            }}
        >
            <div
                ref={panel}
                className="docs-drawer"
                role="dialog"
                aria-modal="true"
                aria-label="All pages"
                onKeyDown={keys}
            >
                <div className="docs-drawer__head">
                    <span>All pages</span>
                    <button
                        type="button"
                        className="b b--s b--quiet b--icon"
                        aria-label="Close the list of pages"
                        onClick={onClose}
                    >
                        <CloseIcon aria-hidden="true" />
                    </button>
                </div>
                {children}
            </div>
        </div>
    );
}

const never = () => () => {};
const onApple = () => /Mac|iPhone|iPad/.test(navigator.userAgent);

/** "⌘K" where the key is ⌘, "Ctrl K" elsewhere. The page is drawn ahead of
 *  time with ⌘ and corrected once it is read in the browser. */
function useShortcut(): string {
    const apple = useSyncExternalStore(never, onApple, () => true);
    return apple ? "⌘K" : "Ctrl K";
}

/** The header's way into search. */
export function DocsSearchButton() {
    const { openSearch } = useContext(DocsShellContext);
    const shortcut = useShortcut();
    return (
        <button
            type="button"
            className="docs-head-search"
            onClick={openSearch}
            aria-haspopup="dialog"
            aria-keyshortcuts="Meta+K Control+K"
        >
            <SearchIcon aria-hidden="true" />
            <span className="docs-head-search__t">Search the docs</span>
            <kbd>{shortcut}</kbd>
        </button>
    );
}

/** The docs home's large search field, which opens the same search. */
export function DocsBigSearch() {
    const { openSearch } = useContext(DocsShellContext);
    const shortcut = useShortcut();
    return (
        <button
            type="button"
            className="docs-big-search"
            onClick={openSearch}
            aria-haspopup="dialog"
            aria-keyshortcuts="Meta+K Control+K"
        >
            <SearchIcon aria-hidden="true" />
            <span>Search guides, commands and pages</span>
            <kbd>{shortcut}</kbd>
        </button>
    );
}

/** Above the title, where only the reading column shows: the guide list and
 *  search, which the wide layout keeps at the side and in the header. */
export function DocsPhoneBar() {
    const { openSearch, openGuides } = useContext(DocsShellContext);
    return (
        <div className="docs-phonebar">
            <button
                type="button"
                className="b b--s b--secondary"
                onClick={openGuides}
                aria-haspopup="dialog"
            >
                <MenuIcon aria-hidden="true" />
                Pages
            </button>
            <button
                type="button"
                className="b b--s b--secondary"
                onClick={openSearch}
                aria-haspopup="dialog"
            >
                <SearchIcon aria-hidden="true" />
                Search
            </button>
        </div>
    );
}
