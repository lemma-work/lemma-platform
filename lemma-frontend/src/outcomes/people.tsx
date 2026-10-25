"use client";

import type { Member, Pod } from "@/data";
import { outcomeView, type Go } from "./model";
import { Avatar, Empty, to } from "./parts";

interface Person {
    key: string;
    member: Member;
    outcomes: { pod: Pod; role: string }[];
}

/** Everyone in at least one Lemma, once each, with where they work. */
export function People({ pods, go }: { pods: readonly Pod[]; go: Go }) {
    const people = new Map<string, Person>();
    for (const pod of pods) {
        for (const member of pod.members) {
            if (member.kind !== "person") continue;
            const key = member.userId ?? member.email ?? member.id;
            const known = people.get(key) ?? { key, member, outcomes: [] };
            known.outcomes.push({ pod, role: member.role });
            people.set(key, known);
        }
    }
    const list = [...people.values()].sort((a, b) => b.outcomes.length - a.outcomes.length || a.member.name.localeCompare(b.member.name));
    return (
        <div className="ox-page">
            <h1 className="ox-title">People</h1>
            <p className="ox-sub">Everyone in a Lemma you can open, and which ones they work in.</p>
            {list.length === 0 ? <Empty title="Loading who is where…" /> : (
                <section className="ox-list">
                    {list.map((person) => (
                        <div key={person.key} className="ox-row ox-row--person">
                            <Avatar name={person.member.name} size={28} />
                            <span className="ox-row__t">{person.member.name}{person.member.email && <span className="ox-row__email">{person.member.email}</span>}</span>
                            <span className="ox-row__chips">
                                {person.outcomes.map(({ pod, role }) => (
                                    <a key={pod.id} className="ox-tag ox-tag--link" title={role} {...to(outcomeView(pod.id), go)}>{pod.name}</a>
                                ))}
                            </span>
                        </div>
                    ))}
                </section>
            )}
        </div>
    );
}
