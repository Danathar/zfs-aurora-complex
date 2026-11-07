"""
Script: tests/test_install_and_verify_doc.py
What: Joins the host trust step and the first switch in docs/install-and-verify.md, and the same steps in README.md, to the signing-policy helper, the image build and the promotion that publish what they name.
Doing: Recomputes the key path, the registries.d file name and contents, and the policy.json rule by running files/scripts/configure_signing_policy.py, and checks the page's commands write exactly those; checks the switch command enforces the signature policy on the tag promotion moves; and checks README.md prints the same three blocks as the install page.
Why: These are root commands an operator copies once per machine, before the switch that installs the whole operating system. A key installed under one name while the policy rule reads another, or a discovery file the policy engine never reads, does not make the switch fail: the host's default policy accepts the image unchecked, so a drift here silently removes the only signature check the first switch gets.
Goal: Make a host step that no longer matches what the image itself writes, or a README copy that drifted from the install page, fail on the pull request that causes it.
"""

from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_DOC = REPO_ROOT / "docs" / "install-and-verify.md"
README = REPO_ROOT / "README.md"
DEFAULTS = REPO_ROOT / "ci" / "defaults.json"
POLICY_SCRIPT = REPO_ROOT / "files" / "scripts" / "configure_signing_policy.py"
BUILD_IMAGE_SH = REPO_ROOT / "build_files" / "build-image.sh"
PROMOTE_STABLE = REPO_ROOT / "ci_tools" / "promote_stable.py"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

IMAGE_NAME = json.loads(DEFAULTS.read_text(encoding="utf-8"))["IMAGE_NAME"]
IMAGE_REPO = f"ghcr.io/danathar/{IMAGE_NAME}"
SIGNING_KEY_FILENAME = f"{IMAGE_NAME}.pub"

HEADING = re.compile(r"^(#{1,6}) (.+)$")
FENCE = re.compile(r"^```([A-Za-z0-9]*)\s*$")
HEREDOC = re.compile(r"<<'([A-Z]+)'$")


def _sections(text: str) -> dict[str, list[str]]:
    """Return `{heading text: body lines}` for every `##`-or-deeper heading in one page."""

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = HEADING.match(line)
        if match and len(match.group(1)) >= 2:
            current = match.group(2).strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _fenced_blocks(lines: list[str]) -> list[tuple[str, str]]:
    """Return `(language, body)` for every fenced code block in the given lines."""

    blocks: list[tuple[str, str]] = []
    language: str | None = None
    body: list[str] = []
    for line in lines:
        match = FENCE.match(line)
        if match and language is None:
            language = match.group(1)
            body = []
        elif line.strip() == "```" and language is not None:
            blocks.append((language, "\n".join(body)))
            language = None
        elif language is not None:
            body.append(line)
    return blocks


def _commands(block: str) -> list[tuple[list[str], list[str]]]:
    """
    Return `(words, heredoc body lines)` for each command in one shell block.

    Comment and blank lines are dropped. A command ending in a quoted heredoc marker
    (`<<'YAML'`) takes the lines up to its delimiter as its body; the quoting matters, because
    it is what keeps the shell from expanding anything in the file it writes.
    """

    commands: list[tuple[list[str], list[str]]] = []
    lines = iter(block.splitlines())
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        words = stripped.split()
        body: list[str] = []
        marker = HEREDOC.search(stripped)
        if marker:
            for body_line in lines:
                if body_line == marker.group(1):
                    break
                body.append(body_line)
            else:
                raise AssertionError(f"heredoc {marker.group(1)} is never closed")
        commands.append((words, body))
    return commands


def _load_policy_helper():
    spec = importlib.util.spec_from_file_location("configure_signing_policy_install_doc", POLICY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_page_lines() -> list[str]:
    """The two steps of Install And Rebase, in page order."""

    sections = _sections(INSTALL_DOC.read_text(encoding="utf-8"))
    host = [h for h in sections if h.startswith("Step 1:")]
    switch = [h for h in sections if h.startswith("Step 2:")]
    if len(host) != 1 or len(switch) != 1:
        raise AssertionError(f"install page has Step 1 headings {host} and Step 2 headings {switch}")
    return sections[host[0]] + sections[switch[0]]


def _readme_lines() -> list[str]:
    return _sections(README.read_text(encoding="utf-8"))["Install"]


# Each page that prints the host step, with the lines it prints them in.
PAGES = {
    "docs/install-and-verify.md": _install_page_lines,
    "README.md": _readme_lines,
}


class _Steps:
    """The three blocks one page prints: the host shell setup, the policy entry, the switch."""

    def __init__(self, lines: list[str]) -> None:
        self.prose = "\n".join(lines)
        blocks = _fenced_blocks(lines)
        self.order = [body for _, body in blocks]
        host = [body for language, body in blocks if language == "bash" and "registries.d" in body]
        policy = [body for language, body in blocks if language == "json"]
        switch = [body for language, body in blocks if language == "bash" and "bootc switch" in body]
        if len(host) != 1 or len(policy) != 1 or len(switch) != 1:
            raise AssertionError(
                f"expected one host block, one policy block, one switch block; got "
                f"{len(host)}, {len(policy)}, {len(switch)}"
            )
        self.host, self.policy, self.switch = host[0], policy[0], switch[0]
        self.host_commands = _commands(self.host)
        self.switch_commands = _commands(self.switch)

    def host_command(self, verb: str) -> tuple[list[str], list[str]]:
        found = [c for c in self.host_commands if c[0][:2] == ["sudo", verb] and "-d" not in c[0]]
        if len(found) != 1:
            raise AssertionError(f"expected one `sudo {verb}` file command, got {found}")
        return found[0]


class HostTrustStepTests(unittest.TestCase):
    """Step 1, on each page that prints it, against what the image writes at build time."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.helper = _load_policy_helper()
        cls.steps = {page: _Steps(read()) for page, read in PAGES.items()}
        cls.key_path = str(cls.helper.key_path_from_env(signing_key_filename=SIGNING_KEY_FILENAME))
        cls.registry_file = str(
            cls.helper.registry_file_path(
                image_repo=IMAGE_REPO, registries_dir=cls.helper.DEFAULT_REGISTRIES_DIR
            )
        )

    def test_the_key_filename_is_the_one_both_workflows_build_with(self) -> None:
        """`<image name>.pub` is derived from defaults.json through the workflow input, so derive it."""

        for workflow in ("build.yml", "build-pr.yml"):
            with self.subTest(workflow=workflow):
                text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
                self.assertIn("signing_key_filename: ${{ steps.defaults.outputs.image_name }}.pub", text)
        self.assertEqual(self.key_path, f"/etc/pki/containers/{IMAGE_NAME}.pub")

    def test_the_committed_key_is_installed_where_the_policy_rule_reads_it(self) -> None:
        """The key install and the keyPath in the rule are one path; either one alone drifting fails."""

        for page, steps in self.steps.items():
            with self.subTest(page=page):
                words, _ = steps.host_command("install")
                self.assertEqual(words, ["sudo", "install", "-Dm0644", "cosign.pub", self.key_path])
                rule = json.loads("{" + steps.policy + "}")[IMAGE_REPO]
                self.assertEqual(rule[0]["keyPath"], words[-1])

    def test_the_host_key_gets_the_mode_the_image_gives_its_own_copy(self) -> None:
        """build-image.sh installs the same committed file, at the same mode, under the same name."""

        build = BUILD_IMAGE_SH.read_text(encoding="utf-8")
        self.assertIn('install -m 0644 /ctx/cosign.pub "/etc/pki/containers/${SIGNING_KEY_FILENAME}"', build)
        self.assertIn("install -d -m 0755 /etc/pki/containers /etc/containers/registries.d", build)

    def test_the_registries_directory_is_created_before_the_file_is_written(self) -> None:
        for page, steps in self.steps.items():
            with self.subTest(page=page):
                words = [c[0] for c in steps.host_commands]
                mkdir = ["sudo", "install", "-d", "-m", "0755", str(self.helper.DEFAULT_REGISTRIES_DIR)]
                self.assertIn(mkdir, words)
                tee = next(i for i, w in enumerate(words) if w[:2] == ["sudo", "tee"])
                self.assertLess(words.index(mkdir), tee)

    def test_the_discovery_file_is_written_where_the_image_writes_its_own(self) -> None:
        """
        The file name is the helper's, not a guess: `/` in the repository becomes `-`.

        A file under any other name is still read by the policy engine, but a host that later
        boots the image would then carry two discovery entries for one repository.
        """

        for page, steps in self.steps.items():
            with self.subTest(page=page):
                words, _ = steps.host_command("tee")
                self.assertEqual(words[:3], ["sudo", "tee", self.registry_file])
                self.assertEqual(words[3:], [">/dev/null", "<<'YAML'"])

    def test_the_discovery_file_says_what_the_image_writes(self) -> None:
        """
        The heredoc is compared line for line to the bytes the helper writes.

        The helper's file opens with one comment line the page leaves out; every other line has
        to match exactly, so a dropped `use-sigstore-attachments: true` -- without which the
        policy engine never looks for the signature -- fails here.
        """

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "registry.yaml"
            self.helper.write_registry_discovery_file(image_repo=IMAGE_REPO, registry_file=target)
            written = [line for line in target.read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
        for page, steps in self.steps.items():
            with self.subTest(page=page):
                _, body = steps.host_command("tee")
                self.assertEqual(body, written)
                self.assertIn("    use-sigstore-attachments: true", body)

    def test_the_policy_entry_is_the_rule_the_helper_writes(self) -> None:
        """The entry is wrapped in braces and parsed, then compared to the helper's real output."""

        written = self.helper.update_policy(
            policy_data={}, image_repo=IMAGE_REPO, key_path=Path(self.key_path)
        )["transports"]["docker"]
        for page, steps in self.steps.items():
            with self.subTest(page=page):
                self.assertEqual(json.loads("{" + steps.policy + "}"), written)

    def test_the_entry_goes_in_the_map_and_file_the_helper_edits(self) -> None:
        """
        The prose places the entry under `transports.docker` in `/etc/containers/policy.json`.

        The helper is run on a policy that already has another repository's rule, because the
        page tells the operator to edit the file rather than replace it, and the image's own
        build does the same.
        """

        self.assertEqual(str(self.helper.DEFAULT_POLICY_FILE), "/etc/containers/policy.json")
        other = {"ghcr.io/example/other": [{"type": "reject"}]}
        merged = self.helper.update_policy(
            policy_data={"default": [{"type": "insecureAcceptAnything"}], "transports": {"docker": dict(other)}},
            image_repo=IMAGE_REPO,
            key_path=Path(self.key_path),
        )
        self.assertEqual(merged["transports"]["docker"]["ghcr.io/example/other"], other["ghcr.io/example/other"])
        self.assertIn(IMAGE_REPO, merged["transports"]["docker"])
        for page, steps in self.steps.items():
            with self.subTest(page=page):
                prose = " ".join(steps.prose.split())
                self.assertIn("to the `transports.docker` map in `/etc/containers/policy.json`", prose)
                self.assertIn("Edit the existing file rather than replacing it", prose)


class FirstSwitchTests(unittest.TestCase):
    """Step 2, on each page that prints it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.steps = {page: _Steps(read()) for page, read in PAGES.items()}

    def test_the_switch_enforces_the_policy_on_the_tag_promotion_moves(self) -> None:
        promote = PROMOTE_STABLE.read_text(encoding="utf-8")
        self.assertIn('stable_ref = f"docker://ghcr.io/{image_org}/{image_name}:latest"', promote)
        for page, steps in self.steps.items():
            with self.subTest(page=page):
                self.assertEqual(
                    [words for words, _ in steps.switch_commands],
                    [
                        ["sudo", "bootc", "switch", "--enforce-container-sigpolicy", f"{IMAGE_REPO}:latest"],
                        ["sudo", "systemctl", "reboot"],
                    ],
                )

    def test_the_host_step_comes_before_the_switch(self) -> None:
        """The whole point of the step is that it runs first; a page that reorders them is wrong."""

        for page, steps in self.steps.items():
            with self.subTest(page=page):
                self.assertLess(steps.order.index(steps.host), steps.order.index(steps.switch))
                self.assertLess(steps.order.index(steps.policy), steps.order.index(steps.switch))


class ReadmeCopyTests(unittest.TestCase):
    """README.md repeats the install page's steps; the two copies have to stay one text."""

    def test_the_readme_prints_the_same_three_blocks_as_the_install_page(self) -> None:
        page = _Steps(_install_page_lines())
        readme = _Steps(_readme_lines())
        self.assertEqual(readme.host, page.host)
        self.assertEqual(readme.policy, page.policy)
        self.assertEqual(readme.switch, page.switch)

    def test_the_readme_sends_the_reader_to_the_install_page(self) -> None:
        self.assertIn("[`docs/install-and-verify.md`](./docs/install-and-verify.md)", "\n".join(_readme_lines()))


if __name__ == "__main__":
    unittest.main()
