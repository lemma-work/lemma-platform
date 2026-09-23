import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, joinChoices, joinSaid, saidAbout, sayJoinWords, type JoinPolicy } from "@/data";
import { CheckIcon, ChevronDownIcon, GlobeIcon, LockIcon, OrgIcon, WarningIcon } from "@/ui/icons";
import { usePicker } from "@/ui/picker";

/** Who may let themselves in.
 *
 *  It sits under the roster rather than in a settings screen, and that is the
 *  same argument "Runs on" makes: the list of who is here raises the question
 *  of who else could be, and an access rule read anywhere other than beside
 *  the people it admits is a rule nobody checks.
 *
 *  The rule is stated as an answer, not as a policy name. "INVITE_ONLY" is
 *  true and tells you nothing about what happens next, so each row carries
 *  what the rule *does* underneath what it is called — which is the part that
 *  decides between the two open rungs, where the difference is not who is
 *  allowed but whether anybody gets asked first.
 */

const MARKS: Record<JoinPolicy, typeof LockIcon> = {
    invited: LockIcon,
    org: OrgIcon,
    anyone: GlobeIcon,
};

export function WhoCanJoin({ podId, orgName }: { podId: string; orgName: string }) {
    const queryClient = useQueryClient();
    const { open, setOpen, wrap, menuClass } = usePicker();

    const current = useQuery({
        queryKey: ["pod-join", podId],
        queryFn: () => source.getPodJoin(podId),
    });

    const set = useMutation({
        mutationFn: (policy: JoinPolicy) => source.setPodJoin(podId, policy),
        onSuccess: (_answer, policy) => {
            queryClient.setQueryData(["pod-join", podId], policy);
            void queryClient.invalidateQueries({ queryKey: ["pod-join", podId] });
        },
    });

    if (current.isError) {
        return <p className="joins__unreadable">Couldn’t load teammate access settings.</p>;
    }

    /* Optimistic while a write is in flight: a door is the one control where
       the answer on screen and the answer in force being different for half a
       second is worth avoiding, and the mutation puts the real one back. */
    const policy: JoinPolicy = set.isPending && set.variables ? set.variables : (current.data ?? "invited");
    const said = joinSaid(policy, orgName);
    const Mark = MARKS[policy];

    return (
        <div className="joins">
            <p className="joins__ask">Who else can join</p>
            <div className="pick" ref={wrap}>
                <button
                    className="pick__face"
                    aria-expanded={open}
                    aria-haspopup="menu"
                    aria-label={"Who else can join: " + said.who}
                    disabled={current.isPending}
                    onClick={() => setOpen((was) => !was)}
                >
                    <span className="pick__mark"><Mark size={15} /></span>
                    <span className="pick__said">{current.isPending ? "Reading…" : said.who}</span>
                    <ChevronDownIcon size={14} />
                </button>

                {open && (
                    <div className={menuClass} role="menu">
                        {joinChoices(orgName).map((choice) => {
                            const RowMark = MARKS[choice.policy];
                            return (
                                <button
                                    className="pick__row"
                                    role="menuitem"
                                    key={choice.policy}
                                    onClick={() => { set.mutate(choice.policy); setOpen(false); }}
                                >
                                    <span className="pick__tick">
                                        {choice.policy === policy && <CheckIcon size={13} />}
                                    </span>
                                    <span className="pick__body">
                                        <span className="joins__who"><RowMark size={14} />{choice.who}</span>
                                        <small>{choice.then}</small>
                                    </span>
                                </button>
                            );
                        })}
                    </div>
                )}
            </div>
            <p className="joins__then">{current.isPending ? "" : said.then}</p>
            {/* What the platform said, not what this app would have guessed.
                A refusal here is almost always a rule somebody can do
                something about — an organization that is not public yet, a
                role that may not open a door — and the sentence explaining
                which is already in the response. */}
            {set.isError && (
                <p className="pick__note" role="alert">
                    <WarningIcon size={13} />
                    <span>{sayJoinWords(saidAbout(set.error, "That could not be saved."), orgName)}</span>
                </p>
            )}
        </div>
    );
}
