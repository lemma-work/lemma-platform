import { connectLinkLabel, failureOf } from "@/data/pod-connectors";
import { Problem } from "@/outcomes/calm";

/** A failed call, said, with the link that fixes it when the server gave one.
 *
 *  A pod connector that cannot run for this person answers with a typed code
 *  and a `connect_url` — "connect your GitHub account to use `github` here".
 *  Printing only the sentence leaves them knowing what is wrong and nowhere to
 *  go about it, so the link sits beside it. Every other failure reads as its
 *  message, as it did before. */
export function ProblemLine({ error, fallback, className, as = "p" }: {
    error: unknown;
    fallback: string;
    className?: string;
    /** `small` or `span` where the line sits inside a row. */
    as?: "p" | "small" | "span";
}) {
    const said = failureOf(error, fallback);
    const Tag = as;
    return (
        <Tag className={className} role="alert">
            {said.message}
            {said.connectUrl && (
                <>
                    {" "}
                    <a className="linkish" href={said.connectUrl}>{connectLinkLabel(said.code)}</a>
                </>
            )}
        </Tag>
    );
}

/** The same, as the kit says a failure where it happened (`Problem`): under
 *  the field or above the button that sent it. */
export function ProblemHere({ error, fallback }: { error: unknown; fallback: string }) {
    const said = failureOf(error, fallback);
    return (
        <Problem>
            {said.message}
            {said.connectUrl && (
                <>
                    {" "}
                    <a className="linkish" href={said.connectUrl}>{connectLinkLabel(said.code)}</a>
                </>
            )}
        </Problem>
    );
}
