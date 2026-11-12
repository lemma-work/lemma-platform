import { useQuery } from "@tanstack/react-query";
import { useCacheKey } from "../../../data/hooks";
import { adminKey, useAdminSource } from "../hooks";
import type { AboutLine } from "../types";
import { readSkills, skillsLine } from "./model";

/** This part's reads, through react-query. */
export function useSkillsSource() {
    return useAdminSource().skills;
}

/** How long a Lemma's skills stay fresh: a skill is a file somebody edits
 *  deliberately, not a run's status (the web's `SKILLS_FRESH`). */
const SKILLS_FRESH = 5 * 60_000;

/** A Lemma's skills, each read from its `SKILL.md`: one listing, then one
 *  read a skill, together. */
export function useSkills(podId: string) {
    const source = useSkillsSource();
    const key = useCacheKey();
    return useQuery({
        queryKey: adminKey(key, "skills", podId),
        queryFn: () => readSkills((directory, page) => source.list(podId, directory, page), (path) => source.read(podId, path)),
        staleTime: SKILLS_FRESH,
    });
}

/** The row's live line on a Lemma's Settings tab: "4 skills", or "Nothing yet". */
export function useSkillsLine(podId: string): AboutLine {
    const skills = useSkills(podId);
    return skillsLine(skills.isSuccess ? skills.data : null);
}
