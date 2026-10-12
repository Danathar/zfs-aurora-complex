"""
Script: tests/test_labeler_config.py
What: Joins `.github/labeler.yml` to the safety-critical file list the agent docs and docs/risk-tiers.md name.
Doing: Resolves every glob against the tracked tree, and checks both ways that the Tier 3 list and `area/safety-critical` cover the same files.
Why: The label is the input to a blocking review gate, and a path that stops matching fails open -- silently, with a green build.
Goal: Make a rename, a moved file, or a re-namespaced label fail here instead of on a pull request nobody reviewed properly.

`area/safety-critical` is not decoration. `CONTRIBUTING.md` item 3 requires a
statement of what could reach a booted machine when one of the seven files
`AGENTS.md` section 0 rule 2 names changes, and `docs/review-rubric.md` section
2 treats a missing statement as *blocking*. The reviewer learns which tier a
pull request is in from the label. So the failure mode is not "a label is
missing" -- it is that a Tier 3 change arrives looking like a Tier 1 change.

Nothing enforced that list before this file. Two mutations to the committed
tree that the suite accepted:

  * deleting `ci_tools/promote_stable.py` from the `area/safety-critical`
    globs -- a signing-path file that no longer gets the label;
  * renaming `area/tests` to `testing` -- which, per this repository's own
    `.github/labeler.yml` header and `docs/SECURITY-AI.md` ("Labels carry
    authority"), is an auto-merge approval signal to the external Hive system.
    A path-based matcher would then hand that signal to every pull request
    touching `tests/`, with no human approving anything.

Both are one-line edits and both stayed green. That is the argument for
checking the file mechanically rather than trusting review to notice.

PyYAML is imported the way the other workflow tests here import it -- used when
present, skipped when not. The CI job installs it by name; pytest does not bring
it, so the skip is for a checkout with nothing installed, not for CI.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
LABELER = REPO_ROOT / ".github" / "labeler.yml"
LABELER_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "labeler.yml"
RISK_TIERS = REPO_ROOT / "docs" / "risk-tiers.md"

SAFETY_LABEL = "area/safety-critical"

# The three agent instruction files that each spell out section 0 rule 2. They
# are meant to say the same thing; this is the line they say it on.
RULE_2_DOCS = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
RULE_2_MARKER = "Treat the build, promotion, and signing path as safety-critical"

# Start of the next list item, in either the numbered (AGENTS/CLAUDE) or the
# bulleted (GEMINI) rendering.
NEXT_ITEM_RE = re.compile(r"^\s*(?:\d+\.|[-*])\s+\*\*")

BACKTICKED_RE = re.compile(r"`([^`]+)`")

# Labels the external Hive system reads as an approval to auto-merge on green
# CI, per the header of .github/labeler.yml and docs/SECURITY-AI.md. A
# path-based labeler must never be able to attach one of these: a glob would be
# granting merge authority that no reviewer granted.
AUTHORITY_LABELS = ("quality", "security", "testing")
AUTHORITY_PREFIXES = ("hive/", "agent/")


def tracked_files() -> list[str]:
    listing = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.split()
    return listing


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """The actions/labeler v5+ glob subset this repository actually uses.

    `**/` crosses any number of directories, `**` the rest of the path, `*` and
    `?` stop at a separator. Anything else is a literal.
    """

    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append(r"(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(r".*")
            i += 2
        elif pattern[i] == "*":
            out.append(r"[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def label_globs(config: dict) -> dict[str, list[str]]:
    """{label: [glob, ...]} from the actions/labeler v5 `changed-files` schema."""

    globs: dict[str, list[str]] = {}
    for label, rules in config.items():
        collected: list[str] = []
        for rule in rules:
            for matcher in rule["changed-files"]:
                for key, value in matcher.items():
                    if not key.startswith("any-glob") and not key.startswith("all-glob"):
                        raise AssertionError(f"{label}: unhandled matcher {key!r}")
                    collected.extend(value if isinstance(value, list) else [value])
        globs[label] = collected
    return globs


def rule_2_paths(doc: str) -> list[str]:
    """The backticked paths section 0 rule 2 names in one agent doc."""

    lines = (REPO_ROOT / doc).read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if RULE_2_MARKER in line)
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if NEXT_ITEM_RE.match(line):
            break
        block.append(line)
    found = BACKTICKED_RE.findall("\n".join(block))
    return [token for token in found if "/" in token or token.endswith((".py", ".yml"))]


def tier_3_paths() -> list[str]:
    """The backticked paths docs/risk-tiers.md lists under Tier 3.

    A bullet runs on through its indented continuation lines, which is how
    tests/test_risk_tiers_doc.py reads it too: a path named only in a bullet's
    prose is still in the tier (#288).
    """

    lines = RISK_TIERS.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("### Tier 3"))
    block = []
    in_bullet = False
    for line in lines[start + 1 :]:
        if line.startswith("###"):
            break
        if line.lstrip().startswith("- "):
            in_bullet = True
        elif not (in_bullet and line.startswith(" ") and line.strip()):
            in_bullet = False
        if in_bullet:
            block.append(line)
    found = BACKTICKED_RE.findall("\n".join(block))
    # `cosign.pub` has no slash and no .py/.yml suffix, so the shape filter
    # alone drops it; a token naming a committed top-level file counts too.
    return [
        token
        for token in found
        if "/" in token or token.endswith((".py", ".yml")) or (REPO_ROOT / token).is_file()
    ]


def resolve(name: str, tracked: list[str]) -> str:
    """A path as a doc writes it -> the path as the tree stores it.

    Rule 2 says `build.yml`; the tree says `.github/workflows/build.yml`. The
    unique-suffix requirement is the point: a second `build.yml` anywhere in the
    tree makes the short name ambiguous, and this should say so rather than pick
    one.
    """

    name = name.rstrip("/")
    if (REPO_ROOT / name).exists():
        return name
    suffix = "/" + name
    candidates = sorted({f for f in tracked if f == name or f.endswith(suffix)})
    # A directory named in a doc is not itself tracked; accept it when files
    # live under it.
    if not candidates:
        under = sorted({f for f in tracked if f.startswith(name + "/")})
        if under:
            return name
    if len(candidates) != 1:
        raise AssertionError(
            f"{name!r} names {len(candidates)} tracked paths: {candidates}"
        )
    return candidates[0]


def files_under(path: str, tracked: list[str]) -> list[str]:
    if path in tracked:
        return [path]
    return sorted(f for f in tracked if f.startswith(path + "/"))


@unittest.skipIf(yaml is None, "PyYAML not installed")
class LabelerConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = yaml.safe_load(LABELER.read_text(encoding="utf-8"))
        cls.globs = label_globs(cls.config)
        cls.tracked = tracked_files()

    def test_the_scan_finds_the_labels(self) -> None:
        # Guard the guard: every assertion below reports an empty list on
        # success, and an empty parse produces the same empty list.
        self.assertGreaterEqual(len(self.globs), 7)
        self.assertIn(SAFETY_LABEL, self.globs)
        self.assertGreater(len(self.globs[SAFETY_LABEL]), 5)
        self.assertGreater(len(self.tracked), 100)

    def test_every_label_stays_in_the_area_namespace(self) -> None:
        # The header of .github/labeler.yml states this as a security decision,
        # not a naming convention: `testing` and `quality` are exactly what a
        # path-based labeler would attach to a change under tests/, and both
        # mean "auto-merge on green" to something outside this repository.
        for label in self.globs:
            with self.subTest(label=label):
                self.assertTrue(
                    label.startswith("area/"),
                    f"{label!r} is outside the area/ namespace",
                )
                self.assertNotIn(label, AUTHORITY_LABELS)
                self.assertFalse(label.startswith(AUTHORITY_PREFIXES))

    def test_every_glob_matches_something_in_the_tree(self) -> None:
        # A renamed or deleted file leaves a glob that matches nothing. The
        # label then quietly stops firing for that path, which for
        # area/safety-critical means a Tier 3 change arrives unlabelled.
        dead = []
        for label, patterns in self.globs.items():
            for pattern in patterns:
                regex = glob_to_regex(pattern)
                if not any(regex.match(f) for f in self.tracked):
                    dead.append(f"{label}: {pattern}")
        self.assertEqual(dead, [], f"globs matching no tracked file: {dead}")

    def test_the_three_agent_docs_name_the_same_seven_files(self) -> None:
        lists = {doc: rule_2_paths(doc) for doc in RULE_2_DOCS}
        for doc, paths in lists.items():
            with self.subTest(doc=doc):
                self.assertEqual(
                    len(paths), 7, f"{doc} rule 2 names {len(paths)} paths: {paths}"
                )
        first, *rest = RULE_2_DOCS
        for doc in rest:
            self.assertEqual(
                sorted(lists[doc]),
                sorted(lists[first]),
                f"{doc} and {first} disagree about the safety-critical files",
            )

    def test_risk_tiers_tier_3_covers_every_rule_2_file(self) -> None:
        # docs/risk-tiers.md is what a reviewer reads to find out what
        # area/safety-critical obliges them to do. It may name more than rule 2
        # (it adds cosign.pub and ci/defaults.json on purpose) but never fewer.
        rule_2 = {resolve(p, self.tracked) for p in rule_2_paths("AGENTS.md")}
        tier_3 = {resolve(p, self.tracked) for p in tier_3_paths()}
        self.assertEqual(
            sorted(rule_2 - tier_3),
            [],
            "named by AGENTS.md rule 2 but absent from docs/risk-tiers.md Tier 3",
        )

    def test_every_tier_3_path_earns_the_safety_critical_label(self) -> None:
        # The join this file exists for. Every file the docs call Tier 3 has to
        # be matched by an area/safety-critical glob -- every file, not just one
        # per directory, so a glob that covers part of publish-native-image/
        # fails here.
        regexes = [glob_to_regex(p) for p in self.globs[SAFETY_LABEL]]
        unlabelled = []
        for named in tier_3_paths():
            path = resolve(named, self.tracked)
            members = files_under(path, self.tracked)
            self.assertNotEqual(members, [], f"{named} resolves to no tracked file")
            for member in members:
                if not any(r.match(member) for r in regexes):
                    unlabelled.append(f"{named} -> {member}")
        self.assertEqual(
            unlabelled,
            [],
            f"Tier 3 paths no area/safety-critical glob matches: {unlabelled}",
        )

    def test_every_safety_critical_file_is_named_in_tier_3(self) -> None:
        # The other direction of the join above. docs/risk-tiers.md is where a
        # reviewer learns why a pull request carries area/safety-critical, and
        # the labeler's own comments say "docs/risk-tiers.md puts it in Tier 3".
        # tests/test_risk_tiers_doc.py cannot see a bullet dropped from the
        # page, because it counts a labelled file as tiered through the label.
        # So every file a safety-critical glob matches has to sit under a path
        # the Tier 3 list names.
        named = set()
        for token in tier_3_paths():
            named.update(files_under(resolve(token, self.tracked), self.tracked))
        regexes = [glob_to_regex(p) for p in self.globs[SAFETY_LABEL]]
        labelled = {f for f in self.tracked if any(r.match(f) for r in regexes)}
        self.assertGreater(len(labelled), 5)
        self.assertIn("cosign.pub", named)
        self.assertEqual(
            sorted(labelled - named),
            [],
            "area/safety-critical files docs/risk-tiers.md Tier 3 does not name",
        )


@unittest.skipIf(yaml is None, "PyYAML not installed")
class LabelerWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.safe_load(LABELER_WORKFLOW.read_text(encoding="utf-8"))
        # `on:` is YAML 1.1 true; PyYAML resolves the bare key to the boolean.
        cls.triggers = cls.workflow.get("on", cls.workflow.get(True))
        cls.steps = [
            step
            for job in cls.workflow["jobs"].values()
            for step in job.get("steps", [])
        ]

    def test_the_scan_finds_the_job(self) -> None:
        self.assertGreater(len(self.steps), 0)
        self.assertIn("pull_request_target", self.triggers)

    def test_the_job_never_runs_pull_request_code(self) -> None:
        # This is why pull_request_target is safe here. The job holds a
        # pull-requests: write token and runs the *base* branch's workflow; the
        # moment it checks out the head ref or runs a script from the pull
        # request, that token is reachable from a fork. Adding a checkout is a
        # one-line change and looks harmless in review.
        for step in self.steps:
            with self.subTest(step=step.get("name", step.get("uses"))):
                self.assertNotIn("run", step, "a run: step would execute repo code")
                self.assertNotIn(
                    "checkout", step.get("uses", ""), "checkout under a write token"
                )
                self.assertNotIn("ref", step.get("with", {}) or {})

    def test_the_action_only_adds_labels(self) -> None:
        # sync-labels: true would strip a label a human attached by hand --
        # including area/safety-critical, added deliberately to a change the
        # globs do not match.
        labeler_steps = [s for s in self.steps if "actions/labeler" in s.get("uses", "")]
        self.assertEqual(len(labeler_steps), 1)
        self.assertIs(labeler_steps[0]["with"]["sync-labels"], False)

    def test_the_action_is_pinned_to_a_commit(self) -> None:
        for step in self.steps:
            uses = step.get("uses", "")
            if not uses:
                continue
            with self.subTest(uses=uses):
                self.assertRegex(
                    uses.partition("@")[2],
                    r"\A[0-9a-f]{40}\Z",
                    "third-party action must be pinned to a full commit SHA",
                )


if __name__ == "__main__":
    unittest.main()
