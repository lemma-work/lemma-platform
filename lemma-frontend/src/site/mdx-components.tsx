import type { ReactNode } from "react";
type Piece = { children?: ReactNode; title?: string };
function Callout({ children, title }: Piece) {
    return (
        <aside className="site-card">
            <h3>{title}</h3>
            {children}
        </aside>
    );
}
function Card({ children, title, href }: Piece & { href?: string }) {
    return (
        <section className="site-card">
            <h3>{href ? <a href={href}>{title}</a> : title}</h3>
            {children}
        </section>
    );
}
function CardGroup({ children }: Piece) {
    return <div className="site-grid">{children}</div>;
}
function Steps({ children }: Piece) {
    return <ol>{children}</ol>;
}
function Step({ children, title }: Piece) {
    return (
        <li>
            <h3>{title}</h3>
            {children}
        </li>
    );
}
function Accordion({ children, title }: Piece) {
    return (
        <details>
            <summary>{title}</summary>
            {children}
        </details>
    );
}
function ParamField({
    children,
    name,
    type,
    required,
}: Piece & { name: string; type?: string; required?: boolean }) {
    return (
        <section>
            <h3>
                <code>{name}</code> {type} {required ? "(required)" : ""}
            </h3>
            {children}
        </section>
    );
}
export const contentComponents = {
    Callout,
    Card,
    CardGroup,
    Steps,
    Step,
    Accordion,
    ParamField,
};
