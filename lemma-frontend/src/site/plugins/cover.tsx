import {
    Buildings,
    CalendarCheck,
    Cards,
    FileCode,
    GitPullRequest,
    Kanban,
    Lightbulb,
    PaintBrush,
    PresentationChart,
    Quotes,
    SunHorizon,
    Tray,
    Users,
} from "@phosphor-icons/react/dist/ssr";
import type { Icon } from "@phosphor-icons/react/dist/lib/types";
import type { AppPart, AppShape } from "./types";
import s from "./plugins.module.css";

/** One icon per app, by the app's name in its bundle. */
const ICON: Record<string, Icon> = {
    today: SunHorizon,
    people: Users,
    companies: Buildings,
    deals: Kanban,
    "brand-studio": PaintBrush,
    library: Quotes,
    spec: FileCode,
    factory: GitPullRequest,
    review: Cards,
    "roundtable-hq": Kanban,
    inbox: Tray,
    booking: CalendarCheck,
    decks: PresentationChart,
    studio: Lightbulb,
};

/**
 * An app's cover, drawn rather than photographed: a window on the app's
 * ground with the shape of its main screen inside. Covers never come from a
 * running app (a screenshot shows one person's rows), and none of these apps
 * ships its own `/.lemma/cover.png` yet, so this is the stand-in until they do.
 */
export function AppCover({ app, small }: { app: AppPart; small?: boolean }) {
    const Glyph = ICON[app.name] ?? Kanban;
    return (
        <div
            className={small ? `${s.cover} ${s.coverSmall}` : s.cover}
            role="img"
            aria-label={`${app.title}: ${app.screens[0] ?? app.line}`}
        >
            <div className={s.window}>
                <div className={s.windowBar}>
                    <Glyph size={small ? 12 : 14} weight="regular" aria-hidden />
                    <span>{app.title}</span>
                </div>
                <div className={s.windowBody} data-shape={app.shape}>
                    <Shape shape={app.shape} />
                </div>
            </div>
        </div>
    );
}

function bars(n: number, cls: string) {
    return Array.from({ length: n }, (_, i) => <i key={i} className={cls} />);
}

function Shape({ shape }: { shape: AppShape }) {
    switch (shape) {
        case "board":
        case "cards":
            return (
                <div className={s.cols}>
                    {[3, 2, 4, 1].map((n, i) => (
                        <div key={i} className={s.col}>
                            <i className={s.colHead} />
                            {bars(n, s.card)}
                        </div>
                    ))}
                </div>
            );
        case "canvas":
            return (
                <div className={s.canvas}>
                    <div className={s.swatches}>{bars(5, s.swatch)}</div>
                    <i className={s.typeLg} />
                    <i className={s.typeSm} />
                    <div className={s.tiles}>{bars(3, s.tile)}</div>
                </div>
            );
        case "feed":
            return <div className={s.feed}>{bars(6, s.quote)}</div>;
        case "mail":
            return (
                <div className={s.mail}>
                    <div className={s.mailList}>{bars(6, s.row)}</div>
                    <div className={s.mailRead}>{bars(5, s.line)}</div>
                </div>
            );
        case "calendar":
            return (
                <div className={s.calendar}>
                    {Array.from({ length: 5 }, (_, d) => (
                        <div key={d} className={s.day}>
                            {bars(4, s.slot)}
                        </div>
                    ))}
                </div>
            );
        case "deck":
            return (
                <div className={s.deck}>
                    <div className={s.slide}>
                        <i className={s.typeLg} />
                        <i className={s.typeSm} />
                    </div>
                    <div className={s.thumbs}>{bars(4, s.thumb)}</div>
                </div>
            );
        case "page":
            return <div className={s.doc}>{bars(7, s.line)}</div>;
        case "queue":
            return <div className={s.list}>{bars(5, s.queueRow)}</div>;
        default:
            return <div className={s.list}>{bars(6, s.row)}</div>;
    }
}
