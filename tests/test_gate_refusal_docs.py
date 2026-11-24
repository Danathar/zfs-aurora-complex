"""
Script: tests/test_gate_refusal_docs.py
What: Joins the refusal classes in tests/fixtures/gate-refusal-corpus.json to the page that
explains the Bash gate, docs/SECURITY-AI.md.
Doing: Reads the `class` of every row whose verdict is `refuse`, looks each one up in
CLASS_KEYWORDS, and fails naming every class whose keywords the page does not carry, or that
has no keywords declared here at all.
Why: The gate and its explanation are separate files. The hook went on gaining refusals
(`--shallow-file`, stand-alone `HOME` and `PATH` assignments, `printf -v`, `read`, `let` and
`for` assignments) while the page kept describing the gate as it was, and nothing joined the
corpus or the hook to the sentence a reader trusts about them (the same gap
Danathar/zfs-kinoite-complex#612 found, closed there by #613).
Goal: A refusal class the corpus adds, or a class whose keyword the page loses, fails here
until the page says it.

A keyword is not proof the page explains the class well. It is the smallest thing that stops
the page going silent: a class with no mention at all is what this catches, and a reviewer
still reads the sentence.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/gate-refusal-corpus.json"
SECURITY_AI = ROOT / "docs/SECURITY-AI.md"

# Per refusal class, the words the page must carry for it. Every keyword listed must appear
# (case-insensitive, line wraps ignored), so a class with several spellings lists each one.
# A new class in the corpus has no entry here and fails until someone declares what the page
# says about it.
CLASS_KEYWORDS: dict[str, tuple[str, ...]] = {
    "compound command": ("compound",),
    "configuration override": ("safe.bareRepository", "--config-env"),
    "configuration reach": (
        "HOME=",
        "XDG_CONFIG_HOME",
        "printf -v",
        "read HOME",
        "mapfile",
        "for HOME",
    ),
    "configuration removal": (
        "unset",
        "export -n",
        "+x",
        "env -u",
        "env -i",
        "--ignore-environment",
        "exec -c",
        "GIT_CONFIG",
    ),
    "environment": ("GIT_EXTERNAL_DIFF",),
    "gh --jq environment": ("--jq", "$ENV"),
    "local ref": ("git branch -D",),
    "name spelling": ("extglob",),
    "named repository": ("--git-dir", "--bare", "--work-tree", "--shallow-file"),
    "option that reads a file": ("--contents", "--registries-conf", "--shallow-file"),
    "plain-file read": ("plain file", "--no-index"),
    "push": ("git push",),
    "redirection": ("redirection",),
    "shell expansion": ("brace", "ANSI-C", "tilde"),
    "unlisted program": ("setarch", "script -qc"),
    "wrapper": ("wrapper", "xargs", "find -exec"),
    "write through git": ("--output=",),
}


def refusal_classes(corpus: dict) -> list[str]:
    """The distinct `class` of every row the gate has to refuse, in sorted order."""

    return sorted({row["class"] for row in corpus["rows"] if row["verdict"] == "refuse"})


def undocumented(corpus: dict, doc: str, keywords: dict[str, tuple[str, ...]]) -> list[str]:
    """One message per refusal class that `doc` does not explain, naming the class.

    A class with no entry in `keywords` is reported as well: silence about a class the gate
    refuses is the failure this exists to catch, whether the page or this table is the one
    that fell behind.
    """

    flowed = " ".join(doc.split()).lower()
    problems = []
    for name in refusal_classes(corpus):
        if name not in keywords:
            problems.append(f"{name!r}: no keywords declared in CLASS_KEYWORDS")
            continue
        missing = [word for word in keywords[name] if word.lower() not in flowed]
        if missing:
            problems.append(f"{name!r}: docs/SECURITY-AI.md does not mention {missing}")
    return problems


class GateRefusalsAreDocumentedTests(unittest.TestCase):
    """docs/SECURITY-AI.md names every class of command the gate refuses."""

    def test_every_refusal_class_in_the_corpus_is_in_security_ai(self) -> None:
        corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
        doc = SECURITY_AI.read_text(encoding="utf-8")
        self.assertEqual(undocumented(corpus, doc, CLASS_KEYWORDS), [])


class UndocumentedCheckTests(unittest.TestCase):
    """The check above fails for the reasons it claims to."""

    CORPUS: ClassVar[dict] = {
        "rows": [
            {"class": "configuration removal", "verdict": "refuse"},
            {"class": "wrapper", "verdict": "refuse"},
            {"class": "ordinary use", "verdict": "allow"},
        ]
    }
    KEYWORDS: ClassVar[dict] = {
        "configuration removal": ("env -i", "exec -c"),
        "wrapper": ("wrapper",),
    }
    DOC = "A wrapper is refused. So are env -i and\nexec -c."

    def test_a_page_that_carries_every_keyword_passes(self) -> None:
        self.assertEqual(undocumented(self.CORPUS, self.DOC, self.KEYWORDS), [])

    def test_a_class_the_page_never_mentions_is_named(self) -> None:
        problems = undocumented(self.CORPUS, "env -i and exec -c only.", self.KEYWORDS)
        self.assertEqual(len(problems), 1)
        self.assertIn("'wrapper'", problems[0])

    def test_one_missing_keyword_of_a_class_is_named(self) -> None:
        problems = undocumented(self.CORPUS, "A wrapper. Also env -i.", self.KEYWORDS)
        self.assertEqual(len(problems), 1)
        self.assertIn("'configuration removal'", problems[0])
        self.assertIn("exec -c", problems[0])
        self.assertNotIn("env -i'", problems[0])

    def test_a_class_with_no_declared_keywords_is_named(self) -> None:
        problems = undocumented(self.CORPUS, self.DOC, {"wrapper": ("wrapper",)})
        self.assertEqual(len(problems), 1)
        self.assertIn("'configuration removal'", problems[0])
        self.assertIn("no keywords declared", problems[0])

    def test_an_allowed_class_needs_no_mention(self) -> None:
        self.assertEqual(refusal_classes(self.CORPUS), ["configuration removal", "wrapper"])

    def test_the_keywords_cover_exactly_the_classes_the_corpus_refuses(self) -> None:
        # CLASS_KEYWORDS is the half of this join a corpus sync cannot update. A class it
        # still lists after the corpus drops it is a stale promise about the page.
        corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
        self.assertEqual(sorted(CLASS_KEYWORDS), refusal_classes(corpus))


if __name__ == "__main__":
    unittest.main()
