# End-to-end tests

Everything in `tests/` above this directory imports the module under test and
mocks its external calls. That is the right design for a suite that runs in a
quarter of a second without network or container tooling, and
[`CONTRIBUTING.md`](../../CONTRIBUTING.md#coverage) is explicit about what it
costs: a module at 100% unit coverage may never have had its real-world path
exercised.

This directory mocks nothing. Each test runs `python3 -m ci_tools.cli <command>`
as a real subprocess and reads what it actually wrote.

## Why that is a different question

Workflow YAML does not call `main()`. It runs a process and then depends on
three things that no in-process test observes:

| The contract | Where a workflow depends on it |
|---|---|
| **Exit status** | Every step runs under `set -e`. A guard that raises but exits 0 is a guard that does not stop the build. |
| **The `GITHUB_OUTPUT` file format** | GitHub parses `name<<DELIM` / value / `DELIM`. A unit test asserting `assertIn("image_name<<", text)` passes on a file GitHub would reject. |
| **The real `ci/defaults.json`** | `export-repo-defaults` is the one command whose job is to read a checked-in file. Replacing that file with a fixture tests the loader and not the file. |

So these tests parse `GITHUB_OUTPUT` with the protocol GitHub actually
implements, compare the values against the real `ci/defaults.json`, and assert
on exit status and stderr rather than on a raised exception.

## What is deliberately not here

**Anything needing a network, a registry, `cosign`, `podman`, or `git`.** The
suite has to stay runnable with nothing installed, so the only commands ever
dispatched here are the ones whose work is env-in, file-out:
`export-repo-defaults`, `compute-candidate-tag`, `export-registry-context`, and
`write-build-inputs-manifest`.

That constraint is stricter than it looks, and the command-wiring test is where
it bites. Checking "does the CLI accept this name?" by running the name would
dispatch it, and `akmods-build-and-publish` with no environment set falls
through to `just build`, `just login`, `just push` and `just manifest` whenever
`/tmp/akmods` exists — an absolute path, so no choice of working directory
isolates it. A test must not be able to publish anything (AGENTS.md section 0
rule 6). So that test provokes an *invalid* choice instead and reads the
accepted names out of argparse's error, which is produced during parsing,
before any handler is reached.

`sign-image`, `promote-stable`, `check-akmods-cache` and the akmods commands
are in CONTRIBUTING's second tier — real production execution, uninstrumented.
This directory does not change that, and it is not a substitute for it.

## These tests add no coverage number

`pytest-cov` measures the test process. The code under test here runs in a
child process, so none of it is counted. That is expected and is not a
regression: the point of this tier is the process boundary, and the boundary is
exactly what an in-process measurement cannot see.

## Running them

They are collected by the normal invocation, so CI runs them on every pull
request with no extra step:

```bash
python3 -m pytest tests/ -v          # everything
python3 -m pytest tests/e2e -v       # this tier only
```

`__init__.py` in this directory is a discovery marker, not a package boundary.
`unittest discover` only recurses into a subdirectory that is importable, so
without it `python3 -m unittest discover -s tests` -- the no-dependencies path
[`CONTRIBUTING.md`](../../CONTRIBUTING.md#tests) documents -- would silently
skip every test in this directory and report OK.
