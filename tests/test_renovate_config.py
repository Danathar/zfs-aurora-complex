"""
Script: tests/test_renovate_config.py
What: Runs `renovate.json`'s own regexes against the tracked tree and joins every pin it claims to track to the file that actually holds the pin.
Doing: Compiles each custom manager's `managerFilePatterns` and `matchStrings`, requires exactly one match in the files they select, and cross-checks the captured values against the duplicate copies of the same pin elsewhere in the repository.
Why: Renovate reads this file; nothing in this repository did. A regex that stops matching is not an error to Renovate -- the dependency silently drops off the dashboard while the pin sits in the tree looking tracked.
Goal: Make a renamed key, a reindented value, a moved file, a deleted manager, a dropped group or a second uncoordinated copy of a pin fail here instead of quietly freezing an update.

`renovate.json` is configuration for a third-party service, so its failure mode
is not an exception anywhere: it is silence. Every one of the four custom
managers below is a hand-written regex aimed at a hand-written literal in
another file. If `ci/defaults.json` gains two spaces of indentation, or
`DEFAULT_BUILD_CONTAINER_IMAGE` is renamed, or the Chunkah default moves out of
the action input block, the regex matches nothing, Renovate finds no
dependency, and the pin stops being offered for update. The tree still contains
the pin. The dashboard just stops mentioning it, and nobody notices until the
pinned digest is a year old.

So the checks here never restate what the pins are. They execute the config:

  * Each `managerFilePatterns` entry is compiled and resolved against
    `git ls-files`. A pattern that selects no tracked file is a dead manager.
  * Each `matchStrings` regex is run over every file its manager selects and
    must match exactly once. "Exactly" is the load-bearing part in both
    directions: zero means the regex stopped matching, and two means the file
    grew a second copy that Renovate would rewrite together with the first.
  * The captured `currentValue`/`currentDigest` are then joined to the other
    places the same literal is written by hand. The devcontainer digest is in
    three files; Renovate's built-in `github-actions` manager updates the two
    workflow copies and the custom manager here updates the third. Those are
    two different Renovate updates for one digest, so the package rule that
    groups them into one pull request is held here too (#133): without it each
    half fails tests/test_workflow_build_container.py on its own and neither
    pull request can merge, which is what happened with #76 and #77.

Renovate's regexes are JavaScript-flavoured, so named groups are spelled
`(?<name>...)` rather than Python's `(?P<name>...)`. `js_regex` rewrites just
that spelling and nothing else -- the point is to run the operator's regex, not
a paraphrase of it, so anything beyond the named-group syntax is left alone and
compiles as written or fails the test.

No PyYAML: nothing here needs a YAML parser -- the joins are over literal image
refs and version pins, which are exactly the text these files are grepped for.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RENOVATE_JSON = REPO_ROOT / "renovate.json"
DEFAULTS_JSON = REPO_ROOT / "ci" / "defaults.json"
RECHUNK_ACTION = REPO_ROOT / ".github" / "actions" / "rechunk-native-image" / "action.yml"
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture-overview.md"

# Files that write `ruff==<version>` by hand. All of them say what CI installs
# today, so the custom manager must select every one.
RUFF_PIN_FILES = {
    ".github/workflows/test.yml",
    ".github/workflows/nightly-compliance.yml",
    "CONTRIBUTING.md",
}

# Files that write the privileged build container's digest by hand. Renovate's
# built-in github-actions manager owns the two workflow copies; the custom
# manager in renovate.json owns the ci/defaults.json copy.
DEVCONTAINER_DIGEST_FILES = {
    ".github/workflows/build.yml",
    ".github/workflows/build-branch.yml",
    "ci/defaults.json",
}

# Every custom manager, keyed by the dependency it declares, mapped to the
# tracked files its managerFilePatterns must select. Set equality against this
# table is what makes deleting a manager fail: a manager that is simply gone
# breaks no regex and matches no file, so nothing else here would notice.
EXPECTED_MANAGERS = {
    "quay.io/coreos/chunkah": {
        ".github/actions/rechunk-native-image/action.yml",
        "docs/architecture-overview.md",
    },
    "openzfs/zfs": {"ci/defaults.json"},
    "ghcr.io/ublue-os/devcontainer": {"ci/defaults.json"},
    "ruff": RUFF_PIN_FILES,
}


def js_regex(pattern: str) -> re.Pattern[str]:
    """Compile a Renovate (JavaScript) regex under Python's `re`.

    Only the named-group spelling differs for the patterns this repository
    uses: JavaScript writes `(?<name>...)`, Python writes `(?P<name>...)`.
    Lookbehind (`(?<=`, `(?<!`) shares the prefix and must not be rewritten.
    """
    return re.compile(re.sub(r"\(\?<(?![=!])", "(?P<", pattern))


def file_pattern(pattern: str) -> re.Pattern[str]:
    """Compile one `managerFilePatterns` entry.

    Renovate accepts either a glob or a `/regex/` string here. Every entry in
    this repository uses the regex form, and that is asserted rather than
    guessed: a glob silently reaching a different set of files is precisely the
    drift these tests exist to catch.
    """
    if not (pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 2):
        raise AssertionError(f"managerFilePatterns entry is not in /regex/ form: {pattern!r}")
    return js_regex(pattern[1:-1])


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [name for name in result.stdout.split("\n") if name]


def read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def load_config() -> dict:
    return json.loads(RENOVATE_JSON.read_text(encoding="utf-8"))


def managers_by_dep(config: dict) -> dict[str, dict]:
    by_dep: dict[str, dict] = {}
    for manager in config["customManagers"]:
        dep = manager["depNameTemplate"]
        if dep in by_dep:
            raise AssertionError(f"two custom managers declare depNameTemplate {dep!r}")
        by_dep[dep] = manager
    return by_dep


def selected_files(manager: dict, tracked: list[str]) -> set[str]:
    patterns = [file_pattern(entry) for entry in manager["managerFilePatterns"]]
    return {name for name in tracked if any(pattern.search(name) for pattern in patterns)}


def captures(manager: dict, name: str) -> list[dict[str, str | None]]:
    """Every match of every matchString of `manager` in tracked file `name`."""
    found: list[dict[str, str | None]] = []
    text = read(name)
    for match_string in manager["matchStrings"]:
        found.extend(match.groupdict() for match in js_regex(match_string).finditer(text))
    return found


def sole_capture(manager: dict, name: str) -> dict[str, str | None]:
    found = captures(manager, name)
    if len(found) != 1:
        raise AssertionError(
            f"{manager['depNameTemplate']}: expected exactly one match in {name}, got {len(found)}"
        )
    return found[0]


class CustomManagerTests(unittest.TestCase):
    """The four regex managers must select real files and match them exactly once."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.tracked = tracked_files()
        cls.managers = managers_by_dep(cls.config)

    def test_the_set_of_tracked_dependencies_is_the_expected_one(self) -> None:
        """A deleted manager breaks nothing and matches nothing, so name them all here."""
        self.assertEqual(set(self.managers), set(EXPECTED_MANAGERS))

    def test_every_manager_selects_the_tracked_files_it_is_aimed_at(self) -> None:
        """A pattern that resolves to no tracked file is a manager that does nothing."""
        for dep, expected in EXPECTED_MANAGERS.items():
            with self.subTest(dep=dep):
                self.assertEqual(selected_files(self.managers[dep], self.tracked), expected)

    def test_every_manager_declares_a_regex_type_and_a_datasource(self) -> None:
        for dep, manager in self.managers.items():
            with self.subTest(dep=dep):
                self.assertEqual(manager["customType"], "regex")
                self.assertTrue(manager["datasourceTemplate"])
                self.assertTrue(manager["description"].strip())

    def test_every_matchstring_matches_its_file_exactly_once(self) -> None:
        """Zero matches means the pin went untracked; two means one bump would rewrite both."""
        for dep, expected in EXPECTED_MANAGERS.items():
            for name in expected:
                with self.subTest(dep=dep, file=name):
                    self.assertEqual(len(captures(self.managers[dep], name)), 1)

    def test_every_manager_captures_a_version_or_a_digest(self) -> None:
        """Renovate has nothing to compare against if neither group is captured."""
        for dep, expected in EXPECTED_MANAGERS.items():
            for name in expected:
                with self.subTest(dep=dep, file=name):
                    groups = sole_capture(self.managers[dep], name)
                    self.assertTrue(
                        groups.get("currentValue") or groups.get("currentDigest"),
                        f"{dep} in {name} captured neither currentValue nor currentDigest",
                    )

    def test_a_digest_only_manager_names_the_tag_whose_digest_it_follows(self) -> None:
        """Renovate needs currentValueTemplate when the ref carries no version to read."""
        for dep, expected in EXPECTED_MANAGERS.items():
            for name in expected:
                groups = sole_capture(self.managers[dep], name)
                if groups.get("currentValue"):
                    continue
                with self.subTest(dep=dep, file=name):
                    self.assertTrue(
                        self.managers[dep].get("currentValueTemplate"),
                        f"{dep} captures only a digest but names no currentValueTemplate",
                    )


class PinJoinTests(unittest.TestCase):
    """What each manager captures, joined to the other hand-written copies of it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.tracked = tracked_files()
        cls.managers = managers_by_dep(cls.config)
        cls.defaults = json.loads(DEFAULTS_JSON.read_text(encoding="utf-8"))

    def test_the_devcontainer_digest_is_one_digest_in_all_three_files(self) -> None:
        """Two different mechanisms update these copies; they must land on one digest."""
        found: dict[str, set[str]] = {}
        for name in self.tracked:
            digests = set(
                re.findall(
                    r"ghcr\.io/ublue-os/devcontainer@(sha256:[0-9a-f]{64})",
                    read(name),
                )
            )
            if digests:
                found[name] = digests
        self.assertEqual(set(found), DEVCONTAINER_DIGEST_FILES)
        self.assertEqual(
            {digest for digests in found.values() for digest in digests},
            {self.defaults["DEFAULT_BUILD_CONTAINER_IMAGE"].split("@", 1)[1]},
        )

    def test_the_devcontainer_manager_captures_the_digest_defaults_json_holds(self) -> None:
        groups = sole_capture(self.managers["ghcr.io/ublue-os/devcontainer"], "ci/defaults.json")
        self.assertEqual(
            groups["currentDigest"],
            self.defaults["DEFAULT_BUILD_CONTAINER_IMAGE"].split("@", 1)[1],
        )

    def test_the_build_container_is_digest_pinned_not_tag_pinned(self) -> None:
        """The manager captures a digest, which is only meaningful if the ref has one."""
        self.assertRegex(self.defaults["DEFAULT_BUILD_CONTAINER_IMAGE"], r"@sha256:[0-9a-f]{64}$")

    def test_the_chunkah_manager_captures_the_action_input_default(self) -> None:
        groups = sole_capture(
            self.managers["quay.io/coreos/chunkah"],
            RECHUNK_ACTION.relative_to(REPO_ROOT).as_posix(),
        )
        default = re.search(
            r"default:\s*(quay\.io/coreos/chunkah:\S+)",
            RECHUNK_ACTION.read_text(encoding="utf-8"),
        )
        self.assertIsNotNone(default, "the rechunk action declares no chunkah default")
        self.assertEqual(
            default.group(1),
            f"quay.io/coreos/chunkah:{groups['currentValue']}@{groups['currentDigest']}",
        )

    def test_the_zfs_manager_captures_the_configured_minor_line(self) -> None:
        groups = sole_capture(self.managers["openzfs/zfs"], "ci/defaults.json")
        self.assertEqual(groups["currentValue"], self.defaults["DEFAULT_ZFS_MINOR_VERSION"])

    def test_the_zfs_extract_template_reads_the_tags_this_repo_parses(self) -> None:
        """Two hand-written regexes over one upstream naming scheme, run against it.

        `ci_tools/zfs_release.py` decides which upstream tags are releases;
        `extractVersionTemplate` decides which part of a tag Renovate compares.
        A template narrowed to three components would extract nothing from the
        real tags and the minor line would stop being offered.
        """
        from ci_tools.zfs_release import RELEASE_TAG_RE

        template = js_regex(self.managers["openzfs/zfs"]["extractVersionTemplate"])
        minor = self.defaults["DEFAULT_ZFS_MINOR_VERSION"]
        for patch in ("0", "3", "11"):
            tag = f"zfs-{minor}.{patch}"
            with self.subTest(tag=tag):
                self.assertIsNotNone(RELEASE_TAG_RE.match(tag))
                extracted = template.search(tag)
                self.assertIsNotNone(extracted, f"extractVersionTemplate misses {tag}")
                self.assertEqual(extracted.group("version"), minor)

    def test_every_ruff_pin_in_the_tree_is_a_known_copy(self) -> None:
        """A new copy has to be added to the manager, not silently left to go stale."""
        found = {name for name in self.tracked if re.search(r"ruff==\d+\.\d+\.\d+", read(name))}
        self.assertEqual(found, RUFF_PIN_FILES)

    def test_the_ruff_pins_all_name_one_version(self) -> None:
        """Renovate updates these copies together, and they must name one version."""
        versions = {
            name: set(re.findall(r"ruff==(\d+\.\d+\.\d+)", read(name)))
            for name in sorted(RUFF_PIN_FILES)
        }
        self.assertEqual(len({v for values in versions.values() for v in values}), 1, versions)

    def test_the_ruff_manager_captures_the_version_test_yml_installs(self) -> None:
        groups = sole_capture(self.managers["ruff"], ".github/workflows/test.yml")
        installed = set(re.findall(r"ruff==(\d+\.\d+\.\d+)", read(".github/workflows/test.yml")))
        self.assertEqual({groups["currentValue"]}, installed)


class PackageRuleTests(unittest.TestCase):
    """The rules that decide what may merge itself, what moves together, and what is untracked."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.rules = cls.config["packageRules"]
        cls.managers = managers_by_dep(cls.config)

    def test_nothing_in_the_config_turns_automerge_on(self) -> None:
        """Merging to main builds, signs and promotes; no unattended path may exist."""
        self.assertNotIn("automerge", self.config)
        for rule in self.rules:
            with self.subTest(rule=rule["description"][:60]):
                self.assertIsNot(rule.get("automerge"), True)

    def test_every_rule_says_why_it_exists(self) -> None:
        for index, rule in enumerate(self.rules):
            with self.subTest(index=index):
                self.assertTrue(rule.get("description", "").strip())

    def test_pin_and_pindigest_updates_are_held_back_from_automerge(self) -> None:
        matching = [
            rule
            for rule in self.rules
            if {"pin", "pinDigest"} <= set(rule.get("matchUpdateTypes", []))
        ]
        self.assertEqual(len(matching), 1, "no single rule covers both pin and pinDigest")
        self.assertIs(matching[0]["automerge"], False)

    def test_every_matchpackagenames_rule_names_a_dependency_a_manager_actually_produces(
        self,
    ) -> None:
        """`matchPackageNames` is matched exactly; a near-miss name silences the rule."""
        matching = [rule for rule in self.rules if "matchPackageNames" in rule]
        expected = {"openzfs/zfs", "ghcr.io/ublue-os/devcontainer"}
        seen = {tuple(rule["matchPackageNames"]) for rule in matching}
        self.assertEqual(seen, {(name,) for name in expected})
        for rule in matching:
            with self.subTest(rule=rule["description"][:60]):
                self.assertIn(rule["matchPackageNames"][0], self.managers)

    def test_an_openzfs_minor_line_bump_is_never_automerged(self) -> None:
        """A new line can activate pool features the previous image cannot import."""
        matching = [rule for rule in self.rules if rule.get("matchPackageNames") == ["openzfs/zfs"]]
        self.assertEqual(len(matching), 1)
        self.assertIn("automerge", matching[0])
        self.assertIs(matching[0]["automerge"], False)

    def test_both_kinds_of_devcontainer_digest_update_move_in_one_pr(self) -> None:
        """Two managers update the three copies; only a shared group puts them in one PR.

        Each half alone fails test_container_literals_match_the_checked_in_default,
        so two PRs for one bump could never merge (#76 and #77). The rule must
        reach both managers, so it may not narrow itself to one manager or file.
        """
        matching = [
            rule
            for rule in self.rules
            if rule.get("matchPackageNames") == ["ghcr.io/ublue-os/devcontainer"]
        ]
        self.assertEqual(len(matching), 1)
        self.assertTrue(matching[0].get("groupName", "").strip())
        for narrowing in ("matchManagers", "matchFileNames", "matchDepTypes", "matchUpdateTypes"):
            with self.subTest(key=narrowing):
                self.assertNotIn(narrowing, matching[0])

    def test_the_dockerfile_manager_is_disabled_outright(self) -> None:
        """`enabled` must be present and false -- an absent key is not a disabled manager."""
        matching = [rule for rule in self.rules if rule.get("matchManagers") == ["dockerfile"]]
        self.assertEqual(len(matching), 1)
        self.assertIn("enabled", matching[0])
        self.assertIs(matching[0]["enabled"], False)

    def test_the_disabled_dockerfile_rule_describes_a_containerfile_that_exists(self) -> None:
        """The rule's reason is that this repo's own resolver supplies the base image."""
        args = set(re.findall(r"^ARG\s+([A-Z_]+)=", read("Containerfile"), re.MULTILINE))
        self.assertIn("BASE_IMAGE", args)
        self.assertIn("DEFAULT_BASE_IMAGE", read("ci_tools/resolve_build_inputs.py"))

    def test_a_docker_match_with_no_digest_is_exempt_from_digest_pinning(self) -> None:
        """`config:best-practices` digest-pins every docker dependency a manager reports.

        A match that captures no `currentDigest` has nowhere to write one, so
        Renovate queues a pinDigest update that can never apply (#173). Every
        such file must be named by a `pinDigests: false` rule, and every file a
        rule exempts must still hold a digest-less match, so the exemption cannot
        outlive its reason or spread to a real digest pin.
        """
        tracked = tracked_files()
        digestless: set[str] = set()
        for manager in self.managers.values():
            if manager["datasourceTemplate"] != "docker":
                continue
            for name in selected_files(manager, tracked):
                if any(not found.get("currentDigest") for found in captures(manager, name)):
                    digestless.add(name)
        exemptions = [rule for rule in self.rules if rule.get("pinDigests") is False]
        exempt = {name for rule in exemptions for name in rule.get("matchFileNames", [])}
        self.assertEqual(digestless, exempt)
        for rule in exemptions:
            with self.subTest(rule=rule["description"][:60]):
                self.assertEqual(set(rule), {"description", "matchFileNames", "pinDigests"})


class PresetTests(unittest.TestCase):
    """The inherited presets, and the property in the tree that proves one is applied."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.tracked = tracked_files()

    def test_the_inherited_presets_are_the_expected_ones(self) -> None:
        self.assertEqual(
            self.config["extends"],
            ["config:best-practices", "schedule:weekly"],
        )

    def test_every_third_party_action_is_pinned_to_a_full_commit(self) -> None:
        """`config:best-practices` pins action digests; this is that rule's visible effect.

        Local (`./`) actions have no upstream to pin. Everything else must carry
        a 40-character commit SHA -- a moved tag is the one way an unreviewed
        change reaches a job that builds and signs an image.
        """
        unpinned: list[str] = []
        for name in self.tracked:
            if not name.startswith((".github/workflows/", ".github/actions/")):
                continue
            for number, line in enumerate(read(name).splitlines(), start=1):
                match = re.search(r"^\s*(?:-\s*)?uses:\s*(\S+)", line)
                if not match:
                    continue
                ref = match.group(1)
                if ref.startswith("./"):
                    continue
                if not re.search(r"@[0-9a-f]{40}$", ref):
                    unpinned.append(f"{name}:{number}: {ref}")
        self.assertEqual(unpinned, [])


class DocumentationJoinTests(unittest.TestCase):
    """docs/architecture-overview.md restates these pins in prose; hold it to them."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.managers = managers_by_dep(cls.config)
        cls.doc = ARCHITECTURE_DOC.read_text(encoding="utf-8")

    def test_every_chunkah_version_the_doc_names_is_the_pinned_one(self) -> None:
        """The paragraph names the version once, where Renovate's manager rewrites it.

        Renovate's chunkah update (#78) moved the action to v0.7.0 and left
        this paragraph saying v0.6.0. The paragraph used to name the version
        twice; it now names it once, inside the manager's doc matchString, so
        a Renovate bump rewrites the doc in the same pull request.
        """
        start = self.doc.find("The Chunkah container image (currently `")
        self.assertNotEqual(start, -1, "the architecture overview stopped naming a Chunkah version")
        end = self.doc.find("\n\n", start)
        paragraph = self.doc[start : end if end != -1 else len(self.doc)]
        stated = re.findall(r"`(v\d+\.\d+\.\d+)`", paragraph)
        manager = self.managers["quay.io/coreos/chunkah"]
        action = sole_capture(manager, ".github/actions/rechunk-native-image/action.yml")
        doc = sole_capture(manager, "docs/architecture-overview.md")
        self.assertEqual(stated, [doc["currentValue"]], paragraph)
        self.assertEqual(doc["currentValue"], action["currentValue"])

    def test_the_dependencies_the_doc_claims_are_tracked_have_managers(self) -> None:
        """The doc tells a reader ruff and the OpenZFS line are Renovate's job."""
        self.assertIn("`ruff` version pinned for CI", self.doc)
        self.assertIn("OpenZFS minor release line", self.doc)
        for dep in ("ruff", "openzfs/zfs"):
            with self.subTest(dep=dep):
                self.assertIn(dep, self.managers)

    def test_the_documented_untracked_pin_is_still_untracked(self) -> None:
        """The doc forbids annotating this pin with a version Renovate would chase."""
        action = read(".github/actions/prepare-rechunk-host/action.yml")
        uses = [
            line
            for line in action.splitlines()
            if "ublue-os/remove-unwanted-software" in line and "uses:" in line
        ]
        self.assertEqual(len(uses), 1)
        self.assertRegex(uses[0].strip(), r"^uses: ublue-os/remove-unwanted-software@[0-9a-f]{40}$")
        self.assertIn("remove-unwanted-software", self.doc)


if __name__ == "__main__":
    unittest.main()
