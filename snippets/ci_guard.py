# Excerpt from Strata: strata/skills/dev_pipeline/ci.py. Omitted: imports, the constants FAILED, PASSED, MAX_JOBS, LOG_LINES, LOG_CHARS and LINE_CHARS, the log patterns _TIMESTAMP, _ANSI and _GROUP, the Failure class (one failed CI job and the end of its log), clean_log, which trims a CI log for the agent, and fence and followup, which give it the failed logs fenced as data with the rules for its round. Source is private.
"""The pull request's CI: what failed, the log excerpt the worker gets, the guard.

Tests run only in the repository's CI on GitHub, never on Strata's server
(owner decision: the worker never runs commands). When a check fails, the
worker gets the end of the failed jobs' logs as data and one round to fix the
cause. Before such a fix is reviewed or pushed, `weakened` looks at it
without any AI: a fix that deletes a test file or renames it out of the test
runner's reach, adds a skip, an expected failure, a focused test or a pytest
option that leaves tests out, removes test functions, or touches CI
configuration or how the test runner starts is not sent. These are pattern
checks: they catch the usual shortcuts, not every way to weaken a test (a
weaker assertion passes them).
"""

# A test that no longer runs or no longer counts: Python, JavaScript, Java, Go, Rust;
# a focused test, which leaves the others out; pytest options that leave tests out; and
# a command made to pass whatever happens.
_SKIP = re.compile(
    r"pytest\.mark\.(skip|skipif|xfail)\b|pytest\.(skip|xfail|importorskip)\("
    r"|@unittest\.(skip\w*|expectedFailure)|\.skipTest\("
    r"|\b(it|test|describe)\.(skip|todo|failing|only)\(|\bx(it|test|describe)\("
    r"|(^|[^.\w])f(it|describe)\("
    r"|@(Disabled|Ignore)\b|\bt\.Skip(Now|f)?\(|#\[ignore\b"
    r"|--deselect\b|\bpytest\b.*--ignore(-glob)?\b|\bcollect_ignore(_glob)?\b|\|\|\s*true\b"
)
# A test function: Python, JavaScript, Go, Java (JUnit), Rust.
_TEST_DEF = re.compile(
    r"^\s*(async\s+)?def\s+test\w*\s*\(|^\s*(it|test)\s*\(\s*['\"`]"
    r"|^\s*func\s+Test\w*\s*\(|^\s*@Test\b|^\s*#\[test\]"
)
# A file the test runner collects by its name: pytest, Go, Jest and Vitest, JUnit, Rust, RSpec.
_TEST_FILE = re.compile(
    r"(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.[cm]?[jt]sx?$"
    r"|(^|/)__tests__/[^/]+\.[cm]?[jt]sx?$|(^|/)(Test\w*|\w*Tests?|\w*TestCase)\.java$"
    r"|(^|/)tests/[^/]+\.rs$|_spec\.rb$"
)
_TEST_DIR = re.compile(r"(^|/)(tests?|__tests__|specs?)/")
# How the test runner starts: the npm test script and pytest's settings.
_RUNNER = re.compile(r'^\s*"test"\s*:|^\s*(addopts|testpaths|python_files|norecursedirs)\s*=')
_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|specs?)/|(^|/)test_[^/]+$|_test\.\w+$|\.(test|spec)\.\w+$"
    r"|(^|/)conftest\.py$"
)
_CI_PATH = re.compile(r"^\.github/|(^|/)\.gitlab-ci\.yml$")


@dataclass(frozen=True)
class Finding:
    """Something a CI fix must not do."""

    kind: str  # deleted | skip | removed | ci_config
    path: str
    detail: str = ""


def _still_collected(old: str, new: str) -> bool:
    """Whether the runner still collects a test file renamed from `old` to `new`."""
    if not _TEST_FILE.search(new):
        return False
    return not _TEST_DIR.search(old) or _TEST_DIR.search(new) is not None


def weakened(name_status: str, diff: str) -> list[Finding]:
    """What a CI fix did to the tests or the CI configuration that it must not.

    `name_status` is `git diff --name-status -M` and `diff` is `git diff -U0`,
    both of the fix alone. A test file renamed or moved to where the runner still
    collects it is fine; renamed out of its reach, it counts as deleted.
    """
    findings = []
    for line in name_status.splitlines():
        status, _, paths = line.partition("\t")
        changed = paths.split("\t")
        gone = status.startswith("D") and _TEST_PATH.search(changed[0]) is not None
        renamed_away = (
            status.startswith("R")
            and _TEST_FILE.search(changed[0]) is not None
            and not _still_collected(changed[0], changed[-1])
        )
        if gone or renamed_away:
            findings.append(Finding("deleted", changed[0]))
        for path in changed:
            if _CI_PATH.search(path):
                findings.append(Finding("ci_config", path))
                break
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    path, header = "", False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            header = True
        elif header and line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
        elif line.startswith("@@"):
            header = False
        elif header:
            continue
        elif line.startswith("+"):
            added.setdefault(path, []).append(line[1:])
        elif line.startswith("-"):
            removed.setdefault(path, []).append(line[1:])
    for path in sorted(set(added) | set(removed)):
        plus, minus = added.get(path, []), removed.get(path, [])
        skips = [line for line in plus if _SKIP.search(line)]
        if len(skips) > sum(1 for line in minus if _SKIP.search(line)):
            findings.append(Finding("skip", path, skips[0].strip()[:120]))
        if _TEST_PATH.search(path) or path.endswith(".rs"):  # Rust keeps unit tests in the code
            lost = sum(1 for line in minus if _TEST_DEF.match(line)) - sum(
                1 for line in plus if _TEST_DEF.match(line)
            )
            if lost > 0:
                findings.append(Finding("removed", path, str(lost)))
        if any(_RUNNER.match(line) for line in plus + minus):
            findings.append(Finding("ci_config", path))
    return findings
