"use client";

import type { PublicReach } from "@/data";

/** What a group's people from outside the space can be answered from.
 *
 *  "From what is Public" alone left the owner guessing, and guessing wrong:
 *  a file shared with anyone with a Lemma account is Public too, so a
 *  changelog sent to one colleague became something the bot reads out to
 *  strangers. The list is read the way the stranger's run reads the space,
 *  so what it shows is what the bot can reach. */
export function GroupPublic({ reach, space }: { reach: PublicReach; space: string }) {
    /* `more` with nothing listed is still something Public, not nothing. */
    const empty = reach.files.length === 0 && reach.tables.length === 0 && !reach.more;
    return (
        <div className="gpublic">
            <p className="gside__label">What they can be told</p>
            {empty ? (
                <p className="gside__text">Nothing in {space} is Public, so they are answered only from what is said here.</p>
            ) : (
                <ul className="gpublic__list">
                    {reach.files.map((file) => (
                        <li key={file.path ?? file.name}>
                            <span>{file.name}</span>
                            <small>{where(file.path)}</small>
                        </li>
                    ))}
                    {reach.tables.map((table) => (
                        <li key={"table:" + table}>
                            <span>{table}</span>
                            <small>Table</small>
                        </li>
                    ))}
                    {reach.more && <li className="gpublic__more">and more marked Public</li>}
                </ul>
            )}
            <p className="gside__fine">A file shared with anyone with a Lemma account is Public too.</p>
        </div>
    );
}

/** The folder a file sits in, or whose it is when the path is not ours to show. */
function where(path: string | null): string {
    if (path === null) return "Someone’s personal files";
    const folder = path.slice(0, path.lastIndexOf("/"));
    return folder ? folder : "Top of the space";
}
