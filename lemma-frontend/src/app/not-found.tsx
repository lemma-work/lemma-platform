import Link from "next/link";
import { Calm } from "@/outcomes/calm";

/* An address nothing here answers. Said the way the frame says a missing
   thing: what is gone, and where to go instead. */
export default function NotFound() {
    return (
        <main className="kstate">
            <Calm
                icon="search"
                title="This page isn’t here"
                actions={<Link className="b b--m b--primary" href="/home">Open Lemma</Link>}
            >
                <p>The link may be old, or it came from somewhere else.</p>
            </Calm>
        </main>
    );
}
