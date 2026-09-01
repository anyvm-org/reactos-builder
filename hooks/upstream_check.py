#!/usr/bin/env python3
# Print the current ReactOS release version, e.g. "0.4.15". Empty output
# means "nothing detected" and is not an error; a non-zero exit means
# detection itself is broken (network error, HTTP error, or a payload that
# no longer matches the expected shape) and must be reported by the caller,
# never swallowed. A failure must NEVER print a plausible-but-wrong version
# -- the version is only printed after every step below has succeeded.
#
# Source of truth: the GitHub releases API for reactos/reactos.
#
# Why NOT releases.atom, which would need no JSON parsing: that feed mixes
# published releases with bare git tags, and ReactOS has plenty of the
# latter. Checked by hand 2026-07-29, the feed's newest entries were
# 0.4.16-RC2, 0.4.17-dev and 0.4.16 -- while
# api.github.com/repos/reactos/reactos/releases/tags/0.4.16 answers 404,
# i.e. 0.4.16 is a tag with no release and no downloadable media at all.
# A watcher trusting the feed would have "detected" 0.4.16 and landed a
# conf pointing at nothing.
#
# So a candidate has to clear three bars, not one:
#   * draft = false and prerelease = false;
#   * it carries an installable asset -- a .zip that is not a press kit,
#     a live image or a symbol/debug drop. (Do NOT narrow this to a
#     "-iso.zip" suffix: 0.4.16 renamed the asset to
#     ReactOS-0.4.16-i386.zip and such a filter reports the PREVIOUS
#     release forever, silently.) The
#     0.4.11 .. 0.4.14 releases are real and final yet ship no media at
#     all through GitHub, and a release this builder cannot download is
#     not a release it can build;
#   * the tag parses as a plain dotted version, optionally with the
#     "-release" suffix ReactOS used up to 0.4.15.
#
# KNOWN LIMIT, deliberate: the asset filename embeds a git-describe suffix
# ("ReactOS-0.4.15-release-1-gdbb43bbaeb2-x86-iso.zip") that cannot be
# derived from the version number. base-builder/watch.py builds the new
# VM_ISO_LINK by substituting into the old one and then HEADs the result,
# so the next ReactOS release WILL fail that URL gate and land nothing.
# That is the designed outcome -- a loud stop beats a conf pointing at a
# 404 -- and the fix is a hand-written conf, the same situation
# ghostbsd-builder is in.
#
# stdlib only (urllib.request, json, re, sys, os) -- no external
# dependencies.

import json
import os
import re
import sys
import urllib.request

URL = "https://api.github.com/repos/reactos/reactos/releases?per_page=30"
TIMEOUT = 60
USER_AGENT = "anyvm-org-upstream-watcher/1.0"

TAG_RE = re.compile(r"^(\d+(?:\.\d+)+)(?:-release)?$")
# Installable media, under EITHER naming convention upstream has used:
#   0.4.15 and earlier: ReactOS-0.4.15-release-1-gdbb43bbaeb2-x86-iso.zip
#   0.4.16 and later:   ReactOS-0.4.16-i386.zip
# Keying on the old "-iso.zip" suffix alone made 0.4.16 invisible for
# three days after release: the hook fell back to the newest release that
# still had that suffix and reported 0.4.15, i.e. a SILENT false negative.
# So the rule is now "a .zip that is not one of the known non-media
# artefacts" -- which still rejects 0.4.10's PressKit.zip and still
# rejects 0.4.11..0.4.14, which ship no assets at all.
ASSET_RE = re.compile(r"\.zip$", re.I)
ASSET_REJECT_RE = re.compile(r"(presskit|-live|symbols|debug)", re.I)


def resolve_natural_key():
    """Return the engine's own natural_key, or fail loudly.

    watch.yml clones base-builder INTO the builder repo root, so at
    detection time it sits at "base-builder/" (relative to this hook's
    cwd, the builder repo root). A local checkout instead has it as a
    sibling, "../base-builder". Try both, in that order.

    There is deliberately NO local fallback copy. Ordering must be the
    single rule the engine uses -- a per-hook duplicate would have to be
    kept in sync by hand across every builder and would drift silently,
    and a hook that ranks versions differently from watch.py is worse
    than one that refuses to run.
    """
    for candidate in ("base-builder", os.path.join("..", "base-builder")):
        if not os.path.isdir(candidate):
            continue
        path = os.path.abspath(candidate)
        if path not in sys.path:
            sys.path.insert(0, path)
        try:
            import gendata
            return gendata.natural_key
        except ImportError:
            continue
    raise ImportError(
        "base-builder/gendata.py not importable from %s; expected it at "
        "./base-builder (CI) or ../base-builder (local checkout)"
        % os.getcwd())


def fetch(url):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    }
    # Unauthenticated API calls are rate-limited to 60/hour per IP, which
    # hosted runners share. A token costs nothing to use when the workflow
    # already has one, and its absence is not an error.
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer %s" % token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def main():
    try:
        key = resolve_natural_key()
    except ImportError as e:
        sys.stderr.write("upstream_check: %s\n" % e)
        return 1
    try:
        body = fetch(URL)
    except Exception as e:
        sys.stderr.write("upstream_check: fetch of %s failed: %s\n" % (URL, e))
        return 1
    try:
        releases = json.loads(body)
    except ValueError as e:
        sys.stderr.write("upstream_check: %s did not return JSON: %s\n"
                         % (URL, e))
        return 1
    if not isinstance(releases, list):
        sys.stderr.write("upstream_check: %s returned %s, expected a list; "
                         "API shape may have changed\n"
                         % (URL, type(releases).__name__))
        return 1

    versions = []
    for rel in releases:
        if not isinstance(rel, dict):
            continue
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name") or ""
        m = TAG_RE.match(tag)
        if not m:
            continue
        assets = rel.get("assets") or []
        if not any(ASSET_RE.search(a.get("name") or "")
                   and not ASSET_REJECT_RE.search(a.get("name") or "")
                   for a in assets):
            continue
        versions.append(m.group(1))

    if not versions:
        sys.stderr.write(
            "upstream_check: no final release with a *-iso.zip asset found "
            "in %s; API shape or ReactOS's publishing habits may have "
            "changed\n" % URL)
        return 1

    print(sorted(set(versions), key=key)[-1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
