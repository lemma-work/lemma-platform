#!/usr/bin/env bash
# Download named assets of a release by id: download_release_assets.sh TAG DIR NAME...
#
# `gh release download` resolves the release by tag, and GitHub has served a
# by-tag release with no assets for over half an hour after they uploaded,
# while the same release fetched by id listed all of them. So: resolve the id
# once, list assets by id, and wait briefly for any still missing.
set -euo pipefail

tag="$1"
dir="$2"
shift 2
repo="${GH_REPO:-${GITHUB_REPOSITORY:?GH_REPO or GITHUB_REPOSITORY must be set}}"

release_id="$(gh api "repos/${repo}/releases/tags/${tag}" --jq .id)"
mkdir -p "$dir"

for attempt in 1 2 3 4 5 6; do
  assets="$(gh api --paginate "repos/${repo}/releases/${release_id}/assets" \
    --jq '.[] | select(.state == "uploaded") | "\(.id)\t\(.name)"')"
  missing=()
  for name in "$@"; do
    awk -F'\t' -v n="$name" '$2 == n { found = 1 } END { exit !found }' <<<"$assets" \
      || missing+=("$name")
  done
  [ "${#missing[@]}" -eq 0 ] && break
  if [ "$attempt" -eq 6 ]; then
    echo "release ${tag} is missing: ${missing[*]}" >&2
    exit 1
  fi
  echo "release ${tag} does not list ${missing[*]} yet; retrying in $((attempt * 10))s" >&2
  sleep $((attempt * 10))
done

for name in "$@"; do
  asset_id="$(awk -F'\t' -v n="$name" '$2 == n { print $1 }' <<<"$assets")"
  gh api -H "Accept: application/octet-stream" \
    "repos/${repo}/releases/assets/${asset_id}" > "${dir}/${name}"
done
