import { ThirdParty } from "./supertokens";
import { PORTAL_PATH, siteOrigin } from "./config";
import { pendingDestination, rememberDestination } from "./redirects";

export async function continueWithProvider(thirdPartyId: string): Promise<void> {
    rememberDestination(pendingDestination(window.location.search));
    const url = await ThirdParty.getAuthorisationURLWithQueryParamsAndSetState({
        thirdPartyId,
        frontendRedirectURI: siteOrigin() + PORTAL_PATH + "/callback/" + thirdPartyId,
    });
    window.location.assign(url);
}
