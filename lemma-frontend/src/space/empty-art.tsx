import type { EmptyArt } from "./empty-copy";

/** What a place will look like once something is in it, drawn small and in
 *  the page's greys, with the one colour its kind wears in the list — so the
 *  picture is a preview of the page rather than a decoration on it. No faces:
 *  a teammate's face appears once per altitude, and this is not one.
 *
 *  Colour comes from classes in `empty.css`, never from here. */
export function EmptyPicture({ art }: { art: EmptyArt }) {
    return (
        <svg className="eart" data-art={art} viewBox={art === "rows" ? "0 0 176 72" : "0 0 176 112"} aria-hidden="true" focusable="false">
            {PICTURES[art]}
        </svg>
    );
}

const PICTURES: Record<EmptyArt, React.ReactNode> = {
    pages: (
        <>
            <rect className="eart__card eart__card--back" x="62" y="8" width="92" height="90" rx="8" />
            <rect className="eart__line" x="74" y="22" width="40" height="5" rx="2.5" />
            <rect className="eart__line" x="74" y="34" width="66" height="4" rx="2" />
            <g className="eart__front">
                <rect className="eart__card" x="24" y="18" width="100" height="88" rx="8" />
                <rect className="eart__ink" x="36" y="31" width="52" height="6" rx="3" />
                <rect className="eart__line" x="36" y="45" width="76" height="4" rx="2" />
                <rect className="eart__line" x="36" y="54" width="68" height="4" rx="2" />
                <rect className="eart__line" x="36" y="63" width="54" height="4" rx="2" />
                <rect className="eart__fill" x="36" y="76" width="9" height="9" rx="2.5" />
                <path className="eart__tick" d="M38.3 80.6l1.8 1.8 3.4-3.6" />
                <rect className="eart__line" x="50" y="78.5" width="40" height="4" rx="2" />
                <rect className="eart__box" x="36.5" y="90.5" width="8" height="8" rx="2.5" />
                <rect className="eart__line" x="50" y="92.5" width="32" height="4" rx="2" />
            </g>
        </>
    ),
    apps: (
        <>
            <rect className="eart__card" x="14" y="8" width="148" height="96" rx="10" />
            <circle className="eart__dot" cx="26" cy="19" r="2.5" />
            <circle className="eart__dot" cx="34" cy="19" r="2.5" />
            <circle className="eart__dot" cx="42" cy="19" r="2.5" />
            <path className="eart__rule" d="M14 29h148" />
            <path className="eart__soft" d="M14 29h36v65a10 10 0 0 1-10 10H24a10 10 0 0 1-10-10z" />
            <rect className="eart__fill" x="22" y="39" width="20" height="4" rx="2" />
            <rect className="eart__line" x="22" y="49" width="16" height="4" rx="2" />
            <rect className="eart__line" x="22" y="59" width="18" height="4" rx="2" />
            <rect className="eart__tile" x="58" y="38" width="46" height="24" rx="5" />
            <rect className="eart__ink" x="64" y="44" width="18" height="6" rx="3" />
            <rect className="eart__line" x="64" y="54" width="30" height="3" rx="1.5" />
            <rect className="eart__tile" x="110" y="38" width="44" height="24" rx="5" />
            <rect className="eart__ink" x="116" y="44" width="14" height="6" rx="3" />
            <rect className="eart__line" x="116" y="54" width="28" height="3" rx="1.5" />
            <rect className="eart__fill eart__fill--quiet" x="60" y="82" width="11" height="14" rx="2" />
            <rect className="eart__fill eart__fill--quiet" x="76" y="74" width="11" height="22" rx="2" />
            <rect className="eart__fill eart__fill--quiet" x="92" y="78" width="11" height="18" rx="2" />
            <rect className="eart__fill eart__fill--quiet" x="108" y="70" width="11" height="26" rx="2" />
            <rect className="eart__fill" x="124" y="66" width="11" height="30" rx="2" />
            <rect className="eart__fill eart__fill--quiet" x="140" y="76" width="11" height="20" rx="2" />
        </>
    ),
    tables: (
        <>
            <rect className="eart__card" x="14" y="12" width="148" height="88" rx="9" />
            <path className="eart__soft" d="M14 32V21a9 9 0 0 1 9-9h130a9 9 0 0 1 9 9v11z" />
            <path className="eart__rule" d="M14 32h148M14 55h148M14 78h148M62 12v88M112 12v88" />
            <rect className="eart__ink" x="24" y="19.5" width="26" height="5" rx="2.5" />
            <rect className="eart__ink" x="72" y="19.5" width="22" height="5" rx="2.5" />
            <rect className="eart__ink" x="122" y="19.5" width="18" height="5" rx="2.5" />
            <rect className="eart__line" x="24" y="41.5" width="30" height="4" rx="2" />
            <rect className="eart__line" x="72" y="41.5" width="26" height="4" rx="2" />
            <rect className="eart__pill" x="122" y="38.5" width="30" height="10" rx="5" />
            <rect className="eart__line" x="24" y="64.5" width="24" height="4" rx="2" />
            <rect className="eart__line" x="72" y="64.5" width="32" height="4" rx="2" />
            <rect className="eart__pill eart__pill--other" x="122" y="61.5" width="24" height="10" rx="5" />
            <rect className="eart__line" x="24" y="87.5" width="28" height="4" rx="2" />
            <rect className="eart__line" x="72" y="87.5" width="20" height="4" rx="2" />
            <rect className="eart__pill" x="122" y="84.5" width="30" height="10" rx="5" />
        </>
    ),
    files: (
        <>
            <g transform="rotate(-8 50 96)">
                <path className="eart__card" d="M28 32h30l12 12v48a6 6 0 0 1-6 6H28a6 6 0 0 1-6-6V38a6 6 0 0 1 6-6z" />
                <rect className="eart__line" x="30" y="56" width="30" height="4" rx="2" />
                <rect className="eart__line" x="30" y="65" width="24" height="4" rx="2" />
                <rect className="eart__tag eart__tag--doc" x="30" y="80" width="18" height="8" rx="2" />
            </g>
            <g transform="rotate(8 126 96)">
                <path className="eart__card" d="M110 32h30l12 12v48a6 6 0 0 1-6 6h-36a6 6 0 0 1-6-6V38a6 6 0 0 1 6-6z" />
                <path className="eart__rule" d="M112 56h32M112 66h32M112 76h32M128 50v34" />
                <rect className="eart__tag eart__tag--sheet" x="112" y="84" width="18" height="8" rx="2" />
            </g>
            <g className="eart__front">
                <path className="eart__card" d="M68 18h32l14 14v58a6 6 0 0 1-6 6H68a6 6 0 0 1-6-6V24a6 6 0 0 1 6-6z" />
                <path className="eart__fold" d="M100 18v10a4 4 0 0 0 4 4h10" />
                <rect className="eart__line" x="70" y="42" width="34" height="4" rx="2" />
                <rect className="eart__line" x="70" y="51" width="30" height="4" rx="2" />
                <rect className="eart__line" x="70" y="60" width="36" height="4" rx="2" />
                <rect className="eart__fill" x="70" y="78" width="20" height="9" rx="2.5" />
            </g>
        </>
    ),
    folder: (
        <>
            <path className="eart__soft eart__edge" d="M30 30a7 7 0 0 1 7-7h28l9 10h65a7 7 0 0 1 7 7v53a7 7 0 0 1-7 7H37a7 7 0 0 1-7-7z" />
            <g className="eart__front">
                <path className="eart__card" d="M62 20h36l12 12v42H62z" />
                <rect className="eart__line" x="70" y="38" width="30" height="4" rx="2" />
                <rect className="eart__line" x="70" y="47" width="24" height="4" rx="2" />
            </g>
            <path className="eart__card" d="M26 52a7 7 0 0 1 7-7h110a7 7 0 0 1 7 7v41a7 7 0 0 1-7 7H33a7 7 0 0 1-7-7z" />
            <rect className="eart__fill" x="40" y="84" width="22" height="6" rx="3" />
        </>
    ),
    workflows: (
        <>
            <path className="eart__wire" d="M52 25.5v8M52 52v7M52 79v7.5" />
            <circle className="eart__tile" cx="52" cy="16" r="9" />
            <path className="eart__hands" d="M52 11.5V16l3.2 2" />
            <rect className="eart__ink" x="70" y="13" width="44" height="6" rx="3" />
            <circle className="eart__tile" cx="52" cy="42.5" r="9" />
            <rect className="eart__fill" x="48.5" y="39" width="7" height="7" rx="2" />
            <rect className="eart__line" x="70" y="40" width="64" height="5" rx="2.5" />
            <circle className="eart__wait" cx="52" cy="69" r="10" />
            <circle className="eart__wait-ink" cx="52" cy="66" r="3.2" />
            <path className="eart__wait-ink" d="M45.8 75.4a6.4 6.4 0 0 1 12.4 0z" />
            <rect className="eart__wait-bar" x="70" y="66" width="54" height="6" rx="3" />
            <circle className="eart__tile" cx="52" cy="96" r="9" />
            <path className="eart__check" d="M48 96.2l2.6 2.6 5.2-5.4" />
            <rect className="eart__line" x="70" y="93.5" width="50" height="5" rx="2.5" />
        </>
    ),
    chats: (
        <>
            <rect className="eart__bubble" x="72" y="12" width="90" height="26" rx="13" />
            <rect className="eart__line" x="84" y="23" width="62" height="4" rx="2" />
            <rect className="eart__ink" x="14" y="50" width="30" height="5" rx="2.5" />
            <rect className="eart__line" x="14" y="62" width="120" height="4" rx="2" />
            <rect className="eart__line" x="14" y="71" width="104" height="4" rx="2" />
            <rect className="eart__tile" x="14" y="82" width="96" height="22" rx="6" />
            <rect className="eart__fill" x="21" y="88" width="10" height="10" rx="2.5" />
            <rect className="eart__line" x="37" y="91" width="50" height="4" rx="2" />
        </>
    ),
    rows: (
        <>
            <rect className="eart__card" x="14" y="6" width="148" height="60" rx="8" />
            <path className="eart__soft" d="M14 24v-10a8 8 0 0 1 8-8h132a8 8 0 0 1 8 8v10z" />
            <path className="eart__rule" d="M14 24h148M62 6v18M112 6v18" />
            <rect className="eart__ink" x="24" y="12.5" width="24" height="5" rx="2.5" />
            <rect className="eart__ink" x="72" y="12.5" width="20" height="5" rx="2.5" />
            <rect className="eart__ink" x="122" y="12.5" width="18" height="5" rx="2.5" />
            <rect className="eart__slot" x="22" y="32" width="132" height="24" rx="6" />
            <path className="eart__plus" d="M88 38.5v11M82.5 44h11" />
        </>
    ),
};
