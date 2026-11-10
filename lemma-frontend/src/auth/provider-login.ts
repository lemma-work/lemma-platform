import { ThirdParty } from "./supertokens";
import { PORTAL_PATH, siteOrigin, stUrl } from "./config";
import { pendingDestination, rememberDestination } from "./redirects";

export async function continueWithProvider(thirdPartyId: string): Promise<void> {
    rememberDestination(pendingDestination(window.location.search));
    const frontendRedirectURI = siteOrigin() + PORTAL_PATH + "/callback/" + thirdPartyId;
    const url = await ThirdParty.getAuthorisationURLWithQueryParamsAndSetState({
        thirdPartyId,
        frontendRedirectURI,
        /* Apple answers with a form POST, which a page cannot take. Its return
           URL is the API's own handler, registered with Apple, which reads the
           post and sends the browser on to the callback above with a 303. The
           others come straight back here. */
        redirectURIOnProviderDashboard: thirdPartyId === "apple" ? stUrl("/callback/apple") : frontendRedirectURI,
    });
    window.location.assign(url);
}
