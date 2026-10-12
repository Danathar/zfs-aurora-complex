"""
Script: tests/test_issue_templates.py
What: Parses `.github/ISSUE_TEMPLATE/*.yml` and joins each form to the files, workflows, labels and prose it names.
Doing: Validates the issue-form schema GitHub silently enforces, then checks every cross-reference resolves in the tracked tree.
Why: A malformed form is dropped from the issue chooser without any error, and the reporting checklists here are hand-copied from CONTRIBUTING.md and AGENTS.md.
Goal: Make a broken form, a stale workflow name, a dead link, or a drifted priority list fail here instead of the first time someone tries to file.

Nothing in this repository opened these four files before. They are ordinary
configuration to `git`, so nothing lints them, and the failure mode is the
quiet one: GitHub does not report a schema error on an issue form. A form with
a duplicate `id`, a `required: "true"` string where a boolean belongs, or a
`markdown` block carrying an `id` simply stops appearing in the chooser. The
first person to notice is a reporter who cannot find the template, and they
file a blank issue instead -- which is precisely the outcome the forms exist to
prevent, since `config.yml` leaves blank issues enabled.

The second half of the file is the more interesting one. These forms are not
self-contained: they are the reporting end of rules written down somewhere
else, copied by hand.

  * `coverage-gap.yml`'s tier dropdown is the numbered list under
    CONTRIBUTING.md "Priority order for a real gap", retyped. Two copies of a
    ranking, and the form is the one contributors actually read.
  * `build-failure.yml`'s workflow dropdown is the contents of
    `.github/workflows/`, retyped. Rename a workflow and the dropdown offers a
    file that no longer exists.
  * `build-failure.yml` tells reporters a persistent akmods failure is already
    filed automatically under the `akmods-failure` label. That string is a
    literal in `akmods-failure-triage.yml`; if it changes there, this form
    sends people looking for a sticky issue under a label nothing applies.
  * Both `build-failure.yml` and `coverage-gap.yml` cite "AGENTS.md section 0
    rule 1" as a required acknowledgement, and `docs/safety-model.md`,
    `docs/install-and-verify.md`, `docs/glossary.md`,
    `docs/building-locally.md` and `CONTRIBUTING.md#coverage` are linked as the
    reading a reporter is sent to first.

Labels get their own check. A label is applied by GitHub the moment the issue
is opened, with no human in the loop, and other automation (the path labeler,
Hive's label-driven policy) may act on a label. So the label set of every form
is pinned here, which makes adding one a deliberate edit rather than a
copy-paste, and keeps every form out of the `area/` namespace reserved for
labels that describe where a pull request lands. The labels pinned are ones
that exist in this repository; `quality` and `testing`, which the sibling
repository uses, do not exist here and GitHub silently drops a label it cannot
find.

The link and marker joins run everywhere. The schema checks need a real YAML
parser, so PyYAML is imported the way the other configuration tests here import
it -- used when present, skipped when not. The `test.yml` job installs it by
name; pytest does not bring it, so the skip is for a checkout with nothing
installed, not for CI.
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
TEMPLATE_DIR = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"
TRIAGE_WORKFLOW = WORKFLOW_DIR / "akmods-failure-triage.yml"

CONFIG = TEMPLATE_DIR / "config.yml"
# config.yml is the chooser configuration, not a form; every other file is one.
FORM_NAMES = ("bug-report.yml", "build-failure.yml", "coverage-gap.yml")

BLOB_PREFIX = "https://github.com/Danathar/zfs-aurora-complex/blob/main/"
BLOB_RE = re.compile(re.escape(BLOB_PREFIX) + r"([^)`\"'\s]+)")

# Bare repository-relative paths named in prose, e.g. "see docs/building-locally.md".
BARE_PATH_RE = re.compile(
    r"\b(?:docs|ci_tools|shared|files|containerfiles|tests|build_files)"
    r"/[A-Za-z0-9_./-]+\.(?:md|py|sh|yml|json)\b"
)

# Workflow filenames named in prose or offered as dropdown options.
WORKFLOW_FILE_RE = re.compile(r"\b[a-z][a-z0-9-]*\.yml\b")

# The sticky label akmods-failure-triage.yml applies, as build-failure.yml
# describes it to a reporter.
STICKY_LABEL = "akmods-failure"
STICKY_LABEL_ASSIGNMENT = re.compile(r"const stickyLabel = '([^']+)';")

# AGENTS.md section 0 rule 1, the acknowledgement both fix-proposing forms
# require.
RULE_1_DOCS = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
RULE_1_MARKER = "Never weaken a fail-closed check to make something pass."

# CONTRIBUTING.md's ranked tier list, which coverage-gap.yml's dropdown copies.
PRIORITY_HEADING = "### Priority order for a real gap"
PRIORITY_ITEM_RE = re.compile(r"^\d+\.\s+\*\*(.+?)\*\*", re.MULTILINE)

# The `area/` namespace is reserved for labels describing where a pull request
# lands. No issue form may borrow it.
LABELER_NAMESPACE = "area/"

# What each form applies today. Pinned so that adding a label is an edit to
# this list too -- see the module docstring on labels that carry authority.
EXPECTED_LABELS = {
    "bug-report.yml": ["bug"],
    "build-failure.yml": ["bug"],
    "coverage-gap.yml": ["enhancement"],
}

EXPECTED_TITLE_PREFIXES = {
    "bug-report.yml": "[bug] ",
    "build-failure.yml": "[build] ",
    "coverage-gap.yml": "[quality] ",
}

# GitHub's issue-form element types. Anything else is a schema error, and a
# schema error means the form never renders.
VALID_TYPES = frozenset({"markdown", "input", "textarea", "dropdown", "checkboxes"})
# `markdown` is display-only: it takes no id and collects no answer.
INTERACTIVE_TYPES = frozenset({"input", "textarea", "dropdown", "checkboxes"})

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def workflow_paths() -> list[Path]:
    """Every workflow GitHub runs: each `.yml` and `.yaml` file in WORKFLOW_DIR."""
    return sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")])


def tracked_files() -> frozenset[str]:
    """Every path `git` tracks, as forward-slash strings relative to the root."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return frozenset(part for part in out.split("\0") if part)


def heading_anchors(markdown: str) -> frozenset[str]:
    """The fragment ids GitHub generates for a document's ATX headings."""
    anchors = set()
    for line in markdown.splitlines():
        match = re.match(r"^#{1,6}\s+(.*?)\s*$", line)
        if not match:
            continue
        text = match.group(1)
        text = re.sub(r"`([^`]*)`", r"\1", text)
        text = re.sub(r"\*+", "", text)
        slug = re.sub(r"[^\w\- ]", "", text.lower()).strip().replace(" ", "-")
        if slug:
            anchors.add(slug)
    return frozenset(anchors)


def template_paths() -> list[Path]:
    return sorted(TEMPLATE_DIR.glob("*.yml"))


def load_form(name: str) -> dict:
    return yaml.safe_load((TEMPLATE_DIR / name).read_text(encoding="utf-8"))


class TemplateDirectoryTests(unittest.TestCase):
    """The files this module claims to cover are the files that are there."""

    def test_directory_holds_exactly_the_expected_templates(self):
        found = {path.name for path in template_paths()}
        self.assertEqual(
            found,
            set(FORM_NAMES) | {CONFIG.name},
            "A template was added or removed. Every form needs its labels pinned in "
            "EXPECTED_LABELS and its title prefix in EXPECTED_TITLE_PREFIXES, or the "
            "checks below silently stop covering it.",
        )

    def test_no_template_uses_the_yaml_extension_github_ignores(self):
        # GitHub reads `.yml` and `.yaml` here, but the repository is uniformly
        # `.yml`; a stray `.yaml` would sit outside template_paths() and be
        # checked by nothing.
        strays = sorted(path.name for path in TEMPLATE_DIR.glob("*.yaml"))
        self.assertEqual(strays, [], f"Unchecked template files: {strays}")


class CrossReferenceTests(unittest.TestCase):
    """Every file, anchor, workflow and label a template names must exist.

    These run without PyYAML: a dead link is a text property of the file.
    """

    @classmethod
    def setUpClass(cls):
        cls.tracked = tracked_files()
        cls.sources = {path.name: path.read_text(encoding="utf-8") for path in template_paths()}

    def test_every_blob_link_resolves_to_a_tracked_file(self):
        seen = 0
        for name, text in self.sources.items():
            for target in BLOB_RE.findall(text):
                seen += 1
                path = target.split("#", 1)[0]
                self.assertIn(
                    path,
                    self.tracked,
                    f"{name} links to {path}, which git does not track.",
                )
        self.assertGreater(seen, 0, "Expected the templates to link into the repository.")

    def test_every_blob_link_anchor_resolves_to_a_heading(self):
        checked = 0
        for name, text in self.sources.items():
            for target in BLOB_RE.findall(text):
                if "#" not in target:
                    continue
                path, anchor = target.split("#", 1)
                checked += 1
                document = (REPO_ROOT / path).read_text(encoding="utf-8")
                self.assertIn(
                    anchor,
                    heading_anchors(document),
                    f"{name} links to {path}#{anchor}, but {path} has no such heading.",
                )
        self.assertEqual(
            checked,
            1,
            "coverage-gap.yml's CONTRIBUTING.md#coverage link is the anchored link "
            "this check exists for; a new one needs to be intentional.",
        )

    def test_every_bare_path_in_prose_resolves_to_a_tracked_file(self):
        seen = 0
        for name, text in self.sources.items():
            for path in BARE_PATH_RE.findall(text):
                seen += 1
                self.assertIn(
                    path,
                    self.tracked,
                    f"{name} tells a reporter to read {path}, which git does not track.",
                )
        self.assertGreater(seen, 0, "Expected the templates to name repository paths.")

    def test_every_workflow_filename_named_is_a_real_workflow(self):
        workflows = {path.name for path in workflow_paths()}
        seen = 0
        for name, text in self.sources.items():
            for candidate in WORKFLOW_FILE_RE.findall(text):
                if candidate in {name, CONFIG.name} or candidate in set(FORM_NAMES):
                    continue
                seen += 1
                self.assertIn(
                    candidate,
                    workflows,
                    f"{name} names the workflow {candidate}, which is not in "
                    f".github/workflows/. Renaming a workflow leaves this form "
                    f"offering a file that does not exist.",
                )
        self.assertGreater(seen, 0, "Expected the templates to name workflows.")

    def test_sticky_label_matches_the_triage_workflow(self):
        # build-failure.yml's opening note sends reporters to look for an
        # existing sticky issue "under the `akmods-failure` label".
        text = self.sources["build-failure.yml"]
        self.assertIn(
            f"`{STICKY_LABEL}` label",
            text,
            "build-failure.yml no longer names the sticky label; this join is stale.",
        )
        applied = set(
            STICKY_LABEL_ASSIGNMENT.findall(TRIAGE_WORKFLOW.read_text(encoding="utf-8"))
        )
        self.assertEqual(
            applied,
            {STICKY_LABEL},
            "akmods-failure-triage.yml applies a different sticky label than "
            "build-failure.yml tells reporters to search for.",
        )

    def test_rule_1_is_cited_and_says_what_the_forms_claim(self):
        citing = [
            name
            for name, text in self.sources.items()
            if "AGENTS.md section 0 rule 1" in text
        ]
        self.assertEqual(
            sorted(citing),
            ["build-failure.yml", "coverage-gap.yml"],
            "The two forms that invite a proposed fix are the ones that must "
            "require the fail-closed acknowledgement.",
        )
        for document in RULE_1_DOCS:
            body = (REPO_ROOT / document).read_text(encoding="utf-8")
            self.assertIn(
                RULE_1_MARKER,
                body,
                f"{document} no longer states rule 1 in the words these forms cite. "
                f"A renumbered or reworded rule leaves the forms demanding an "
                f"acknowledgement of something that is not written down.",
            )


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class FormSchemaTests(unittest.TestCase):
    """The schema GitHub enforces by silently refusing to render the form."""

    def test_every_template_is_valid_yaml_mapping(self):
        for path in template_paths():
            with self.subTest(template=path.name):
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertIsInstance(data, dict, f"{path.name} is not a YAML mapping.")

    def test_forms_declare_name_description_and_body(self):
        for name in FORM_NAMES:
            with self.subTest(form=name):
                form = load_form(name)
                for key in ("name", "description", "body"):
                    self.assertIn(key, form, f"{name} has no top-level `{key}`.")
                self.assertIsInstance(form["name"], str)
                self.assertIsInstance(form["description"], str)
                self.assertTrue(form["name"].strip(), f"{name} has an empty `name`.")
                self.assertTrue(
                    form["description"].strip(), f"{name} has an empty `description`."
                )
                self.assertIsInstance(form["body"], list)
                self.assertTrue(form["body"], f"{name} has an empty `body`.")

    def test_title_prefixes_are_the_ones_the_repository_uses(self):
        for name, prefix in EXPECTED_TITLE_PREFIXES.items():
            with self.subTest(form=name):
                self.assertEqual(load_form(name).get("title"), prefix)

    def test_labels_are_pinned_and_stay_out_of_the_labeler_namespace(self):
        for name, expected in EXPECTED_LABELS.items():
            with self.subTest(form=name):
                labels = load_form(name).get("labels")
                self.assertEqual(
                    labels,
                    expected,
                    "Issue-form labels are applied by automation with no human in "
                    "the loop, so a form's label set is pinned.",
                )
                for label in labels:
                    self.assertFalse(
                        label.startswith(LABELER_NAMESPACE),
                        f"{name} applies {label}; the `area/` namespace is reserved for "
                        f"labels describing where a pull request lands, which says "
                        f"nothing about an issue.",
                    )

    def test_body_elements_use_a_valid_type(self):
        for name in FORM_NAMES:
            for index, element in enumerate(load_form(name)["body"]):
                with self.subTest(form=name, index=index):
                    self.assertIsInstance(element, dict)
                    self.assertIn(
                        element.get("type"),
                        VALID_TYPES,
                        f"{name} body[{index}] has an unrecognised type; GitHub "
                        f"drops the whole form from the chooser.",
                    )

    def test_markdown_blocks_carry_a_value_and_nothing_answerable(self):
        for name in FORM_NAMES:
            for index, element in enumerate(load_form(name)["body"]):
                if element.get("type") != "markdown":
                    continue
                with self.subTest(form=name, index=index):
                    self.assertNotIn(
                        "id",
                        element,
                        "A markdown block collects no answer, so an `id` on it is a "
                        "schema error.",
                    )
                    self.assertNotIn("validations", element)
                    value = element.get("attributes", {}).get("value")
                    self.assertIsInstance(value, str)
                    self.assertTrue(value.strip())

    def test_interactive_elements_have_a_unique_id_and_a_label(self):
        for name in FORM_NAMES:
            seen: dict[str, int] = {}
            for index, element in enumerate(load_form(name)["body"]):
                if element.get("type") not in INTERACTIVE_TYPES:
                    continue
                with self.subTest(form=name, index=index):
                    element_id = element.get("id")
                    self.assertIsInstance(
                        element_id, str, f"{name} body[{index}] has no `id`."
                    )
                    self.assertRegex(element_id, ID_RE)
                    self.assertNotIn(
                        element_id,
                        seen,
                        f"{name} reuses the id {element_id!r}; a duplicate id is a "
                        f"schema error, so the form stops rendering entirely.",
                    )
                    seen[element_id] = index
                    label = element.get("attributes", {}).get("label")
                    self.assertIsInstance(label, str)
                    self.assertTrue(label.strip())

    def test_required_is_a_boolean_everywhere_it_appears(self):
        # `required: "true"` is a string, and GitHub rejects the form rather
        # than coercing it. The quoted form is easy to write and impossible to
        # see in review.
        checked = 0
        for name in FORM_NAMES:
            for index, element in enumerate(load_form(name)["body"]):
                validations = element.get("validations")
                if validations is not None:
                    with self.subTest(form=name, index=index, where="validations"):
                        self.assertEqual(set(validations), {"required"})
                        self.assertIsInstance(validations["required"], bool)
                        checked += 1
                for option_index, option in enumerate(
                    element.get("attributes", {}).get("options", []) or []
                ):
                    if isinstance(option, dict) and "required" in option:
                        with self.subTest(form=name, index=index, option=option_index):
                            self.assertIsInstance(option["required"], bool)
                            checked += 1
        self.assertGreater(checked, 0, "Expected the forms to mark fields required.")

    def test_dropdowns_offer_non_empty_string_options(self):
        for name in FORM_NAMES:
            for element in load_form(name)["body"]:
                if element.get("type") != "dropdown":
                    continue
                with self.subTest(form=name, dropdown=element.get("id")):
                    options = element.get("attributes", {}).get("options")
                    self.assertIsInstance(options, list)
                    self.assertGreater(len(options), 1, "A one-option dropdown asks nothing.")
                    for option in options:
                        self.assertIsInstance(option, str)
                        self.assertTrue(option.strip())
                    self.assertEqual(
                        len(set(options)), len(options), "Duplicate dropdown option."
                    )

    def test_checkbox_options_are_labelled(self):
        for name in FORM_NAMES:
            for element in load_form(name)["body"]:
                if element.get("type") != "checkboxes":
                    continue
                with self.subTest(form=name, checkboxes=element.get("id")):
                    options = element.get("attributes", {}).get("options")
                    self.assertIsInstance(options, list)
                    self.assertTrue(options)
                    for option in options:
                        self.assertIsInstance(option, dict)
                        self.assertIsInstance(option.get("label"), str)
                        self.assertTrue(option["label"].strip())
                        self.assertEqual(
                            set(option) - {"label", "required"},
                            set(),
                            "A checkbox option takes `label` and `required` only.",
                        )


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class FormContentJoinTests(unittest.TestCase):
    """What each form asks for, joined to the thing that makes the answer useful."""

    def test_build_failure_workflow_options_all_exist(self):
        options = self._dropdown("build-failure.yml", "workflow")
        workflows = {path.name for path in workflow_paths()}
        named = [option for option in options if option.endswith(".yml")]
        self.assertTrue(named, "The workflow dropdown offers no workflow.")
        for option in named:
            self.assertIn(
                option,
                workflows,
                f"build-failure.yml offers {option}, which is not in "
                f".github/workflows/.",
            )
        self.assertEqual(
            options[-1],
            "Something else",
            "The escape hatch must stay last, or a reporter whose workflow is not "
            "listed has nothing to pick and the field is required.",
        )

    def test_coverage_gap_tiers_match_contributing_priority_order(self):
        options = self._dropdown("coverage-gap.yml", "tier")
        body = CONTRIBUTING.read_text(encoding="utf-8")
        start = body.index(PRIORITY_HEADING)
        end = body.index("\n### ", start + len(PRIORITY_HEADING))
        ranked = [
            item.rstrip(".") for item in PRIORITY_ITEM_RE.findall(body[start:end])
        ]
        self.assertEqual(
            len(ranked),
            3,
            "CONTRIBUTING.md's priority order no longer has three ranked tiers.",
        )
        self.assertEqual(
            len(options),
            len(ranked),
            f"coverage-gap.yml offers {len(options)} tiers and CONTRIBUTING.md ranks "
            f"{len(ranked)}. The form is the copy contributors actually read.",
        )
        for position, (option, rank) in enumerate(zip(options, ranked), start=1):
            with self.subTest(rank=position):
                self.assertTrue(
                    option.startswith(rank),
                    f"coverage-gap.yml's tier {position} is {option!r}, but "
                    f"CONTRIBUTING.md ranks {rank!r} there. A dropdown in a "
                    f"different order than the prose it cites re-ranks findings.",
                )

    def test_coverage_gap_refuses_the_two_things_contributing_refuses(self):
        # "What not to file" and the form's opening note are the same rule,
        # written twice.
        text = (TEMPLATE_DIR / "coverage-gap.yml").read_text(encoding="utf-8")
        body = CONTRIBUTING.read_text(encoding="utf-8")
        self.assertIn("### What not to file", body)
        self.assertIn("the coverage percentage moving", text)
        self.assertIn("**The percentage itself.**", body)
        self.assertIn("weakening a fail-closed guard", text)
        self.assertIn("obtained by weakening it", body)

    def test_the_answers_a_triager_cannot_work_without_are_required(self):
        required = {
            "bug-report.yml": {"problem", "stage", "reproduce", "data"},
            "build-failure.yml": {"run", "workflow", "failure", "rules"},
            "coverage-gap.yml": {"path", "tier", "evidence", "consequence", "rules"},
        }
        for name, expected in required.items():
            with self.subTest(form=name):
                self.assertEqual(self._required_ids(name), expected)

    def test_every_acknowledgement_checkbox_is_required(self):
        # These are the rules the form makes a reporter agree to -- credentials
        # scrubbed, pool-import behaviour stated, no fix that weakens a guard.
        # An optional acknowledgement acknowledges nothing.
        counts = {"bug-report.yml": 2, "build-failure.yml": 1, "coverage-gap.yml": 2}
        for name, expected in counts.items():
            with self.subTest(form=name):
                options = [
                    option
                    for element in load_form(name)["body"]
                    if element.get("type") == "checkboxes"
                    for option in element["attributes"]["options"]
                ]
                self.assertEqual(len(options), expected)
                for option in options:
                    self.assertIs(
                        option.get("required"),
                        True,
                        f"{name}: {option['label'][:60]!r} is an acknowledgement a "
                        f"reporter can skip.",
                    )

    def test_bug_report_digest_stays_conditionally_required(self):
        # The field's own description says it is required for anything running
        # on a machine and blank only for a local build that never produced an
        # image. A form-level `required: true` would make that impossible to
        # honour, so the conditional wording and the absent validation have to
        # move together.
        element = self._element("bug-report.yml", "image")
        self.assertNotIn(
            "validations",
            element,
            "The digest field is conditionally required in prose; hard-requiring it "
            "locks out the local-build case the description carves out.",
        )
        description = element["attributes"]["description"]
        self.assertIn("leave blank only for a local", description)

    def test_evidence_fields_render_as_shell(self):
        # Pasted logs go through markdown otherwise, which eats the leading `#`
        # of a kernel message and reflows the columns of `zfs list`.
        for name, element_id in (
            ("bug-report.yml", "evidence"),
            ("build-failure.yml", "failure"),
        ):
            with self.subTest(form=name):
                self.assertEqual(self._element(name, element_id)["attributes"]["render"], "shell")

    def _element(self, name: str, element_id: str) -> dict:
        for element in load_form(name)["body"]:
            if element.get("id") == element_id:
                return element
        self.fail(f"{name} has no element with id {element_id!r}.")

    def _dropdown(self, name: str, element_id: str) -> list[str]:
        element = self._element(name, element_id)
        self.assertEqual(element["type"], "dropdown")
        return element["attributes"]["options"]

    def _required_ids(self, name: str) -> set[str]:
        """Ids a reporter cannot submit the form without answering.

        A checkboxes block carries no `validations`; each option is required
        individually, so the block counts as required when any option is.
        """
        ids = set()
        for element in load_form(name)["body"]:
            if element.get("validations", {}).get("required"):
                ids.add(element["id"])
                continue
            options = element.get("attributes", {}).get("options", []) or []
            if any(isinstance(option, dict) and option.get("required") for option in options):
                ids.add(element["id"])
        return ids


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class ChooserConfigTests(unittest.TestCase):
    """`config.yml` decides what the issue chooser shows next to the forms."""

    def setUp(self):
        self.config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))

    def test_blank_issues_setting_is_a_boolean(self):
        # A string here is not an error GitHub reports; it is a setting that
        # quietly does not take effect.
        self.assertIsInstance(self.config.get("blank_issues_enabled"), bool)

    def test_contact_links_are_complete(self):
        links = self.config.get("contact_links")
        self.assertIsInstance(links, list)
        self.assertTrue(links)
        for index, link in enumerate(links):
            with self.subTest(link=index):
                self.assertEqual(set(link), {"name", "url", "about"})
                for key in ("name", "url", "about"):
                    self.assertIsInstance(link[key], str)
                    self.assertTrue(link[key].strip())

    def test_contact_link_urls_resolve_to_tracked_documents(self):
        tracked = tracked_files()
        for link in self.config["contact_links"]:
            with self.subTest(link=link["name"]):
                url = link["url"]
                self.assertTrue(
                    url.startswith(BLOB_PREFIX),
                    f"{url} does not point into this repository; a chooser link that "
                    f"404s is the first thing a reporter sees.",
                )
                self.assertIn(url[len(BLOB_PREFIX) :].split("#", 1)[0], tracked)

    def test_no_contact_link_duplicates_a_form(self):
        # A contact link sits beside the forms in the chooser and is not an
        # issue; one that reads like a form sends reports nowhere.
        names = [link["name"].lower() for link in self.config["contact_links"]]
        form_names = {load_form(name)["name"].lower() for name in FORM_NAMES}
        self.assertEqual(
            set(names) & form_names, set(), "A contact link shares a form's name."
        )


if __name__ == "__main__":
    unittest.main()
