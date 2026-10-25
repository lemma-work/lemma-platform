/** The tour (PARITY.md, G): who is signed in and how new the account is,
 *  which decides whether the tour offers itself (`offersTour`). */
export interface TourAccount {
    id: string | null;
    /** When the account was made, ISO; null when the server did not say. */
    createdAt: string | null;
}

export interface TourSource {
    account(): Promise<TourAccount>;
}
