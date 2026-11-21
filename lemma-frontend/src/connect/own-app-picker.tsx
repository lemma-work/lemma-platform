import { useMemo } from "react";
import type { Connector } from "@/data";
import { ownAppKinds } from "./install";
import { ServicePicker } from "./service-picker";

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
    const eligible = useMemo(
        () => connectors.filter((connector) => ownAppKinds(connector).length > 0),
        [connectors],
    );
    return (
        <ServicePicker
            title="Your own OAuth app"
            subtitle="Sign people in through an app your organization registers"
            lead="Which service is the app for? You will get the redirect URL to register with it, then paste its client ID and secret."
            empty="No connector here takes an app of your own."
            services={eligible}
            onPick={onPick}
            onClose={onClose}
        />
    );
}
