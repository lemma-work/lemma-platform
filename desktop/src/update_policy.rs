use semver::Version;

pub fn candidate_allowed(channel: &str, current: &Version, candidate: &Version) -> bool {
    if !candidate.cmp_precedence(current).is_gt() {
        return false;
    }
    match channel {
        "stable" => candidate.pre.is_empty(),
        "nightly" => {
            let mut parts = candidate.pre.as_str().split('.');
            parts.next() == Some("nightly")
                && matches!(parts.clone().count(), 1 | 2)
                && parts.all(|part| part.parse::<u64>().is_ok_and(|number| number > 0))
        }
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ordered_nightlies_and_reruns_use_the_normal_semver_comparison() {
        for (current, candidate, allowed) in [
            ("0.7.2-nightly.9", "0.7.2-nightly.10.1", true),
            ("0.7.2-nightly.10.1", "0.7.2-nightly.10.2", true),
            ("0.7.2-nightly.10.2", "0.7.2-nightly.10.1", false),
            ("0.7.2-nightly.10.1", "0.7.2-nightly.10.1", false),
            ("0.7.2-nightly.10.1", "0.7.3-nightly.11.1", true),
            ("0.7.2-nightly.10.1+a", "0.7.2-nightly.10.1+z", false),
        ] {
            assert_eq!(
                candidate_allowed(
                    "nightly",
                    &current.parse().unwrap(),
                    &candidate.parse().unwrap()
                ),
                allowed
            );
        }
    }

    #[test]
    fn wrong_channel_or_development_candidates_never_become_updates() {
        let current: Version = "0.7.1".parse().unwrap();
        for (channel, candidate, allowed) in [
            ("stable", "0.7.2", true),
            ("stable", "0.7.2-nightly.1.1", false),
            ("nightly", "0.7.2", false),
            ("nightly", "0.7.2-beta.1", false),
            ("nightly", "0.7.2-nightly.other", false),
            ("nightly", "0.7.2-nightly.1.1.extra", false),
            ("dev", "0.7.2", false),
        ] {
            assert_eq!(
                candidate_allowed(channel, &current, &candidate.parse().unwrap()),
                allowed
            );
        }
    }
}
