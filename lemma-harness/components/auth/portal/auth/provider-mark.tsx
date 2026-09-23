import type { ThirdPartyId } from "@/components/auth/portal/auth/supertokens";

/**
 * The provider logos the prebuilt buttons used to draw for us.
 *
 * Needed at all because SuperTokens rendered them inside `getRoutingComponent`,
 * and a sign-in screen that stops calling it loses them -- a "Continue with
 * Google" button with no mark beside it is the clearest tell that two screens of
 * one product were built by different hands.
 *
 * Served as files rather than inlined as JSX because they are the one kind of
 * artwork whose colors are not ours to tokenize: these are Google's and
 * Microsoft's brand hexes, fixed by their sign-in guidelines, and a design
 * system has nothing to say about them.
 */
const MARKS: Record<ThirdPartyId, string> = {
  google: "/brand/google.svg",
  "active-directory": "/brand/microsoft.svg",
};

export function ProviderMark({ id }: { id: ThirdPartyId }) {
  // A fixed 18px mark needs no optimizer, and `next/image` would wrap it in a
  // span the button's flex row would then have to be taught about.
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={MARKS[id]} alt="" aria-hidden className="auth-provider-mark" />
  );
}
