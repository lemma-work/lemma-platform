import { useMemo, useState } from "react";
import type { Connector } from "@/data";
import { Modal } from "@/shell/modal";
import { SearchIcon } from "@/ui/icons";
import { ownAppKinds } from "./install";

/** The services an organization can sign in to through an OAuth app of its
 *  own, rather than Lemma's.
 *
 *  "Use your own app" is a question about one connector, so it only ever
 *  lived inside that connector's dialog — reachable by somebody who already
 *  knew to open Gmail and look past the Continue button. This is the door for
 *  somebody who came to set up their app and has not yet said for what. */
export function OwnAppPicker({ connectors, onPick, onClose }: {
    connectors: Connector[];
    onPick: (connector: Connector) => void;
    onClose: () => void;
}) {
    const [query, setQuery] = useState("");
    const eligible = useMemo(
        () => connectors.filter((connector) => ownAppKinds(connector).length > 0),
        [connectors],
    );
    const term = query.trim().toLowerCase();
    const shown = term
        ? eligible.filter((connector) => connector.title.toLowerCase().includes(term) || connector.id.includes(term))
        : eligible;

    return (
        <Modal title="Your own OAuth app" subtitle="Sign people in through an app your organization registers" narrow onClose={onClose}>
            <div className="connect-form">
                {eligible.length === 0 ? (
                    <p role="status" className="connect-lead">
                        No connector here takes an app of your own.
                    </p>
                ) : (
                    <>
                        <p className="connect-lead">
                            Which service is the app for? You will get the redirect URL to register with it, then paste its client ID and secret.
                        </p>
                        {eligible.length > 6 && (
                            <label className="connectors__find">
                                <SearchIcon size={15} />
                                <input value={query} autoFocus placeholder={"Search " + eligible.length + " services…"}
                                    onChange={(event) => setQuery(event.target.value)} />
                            </label>
                        )}
                        <div className="connect-picks">
                            {shown.map((connector) => (
                                <button className="connect-pick" key={connector.id} onClick={() => onPick(connector)}>
                                    <span className="connect-pick__glyph">
                                        {connector.icon
                                            ? <img src={connector.icon} alt="" aria-hidden="true" width={20} height={20} />
                                            : connector.title.slice(0, 1)}
                                    </span>
                                    <span>
                                        <strong>{connector.title}</strong>
                                        {connector.description && <small>{connector.description}</small>}
                                    </span>
                                </button>
                            ))}
                            {shown.length === 0 && <p className="connect-lead">Nothing matches “{query}”.</p>}
                        </div>
                    </>
                )}
            </div>
        </Modal>
    );
}
