# Excerpt from Strata: tests/test_dev_ci.py. Omitted: imports, the five tests of the log excerpt, the fence and the follow-up text, and six more cases: a skip that only moved, a content line that looks like a diff header, a rename to a name the runner skips, removed Go, Java and Rust tests, pytest settings, and Rust tests inside the source. Source is private.
"""The CI helpers: the log excerpt the worker gets, and the guard on CI fixes."""


def diff(path: str, removed: list[str], added: list[str]) -> str:
    lines = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}", "@@ -1 +1 @@"]
    return "\n".join([*lines, *(f"-{line}" for line in removed), *(f"+{line}" for line in added)])


def test_a_real_fix_passes_the_guard() -> None:
    change = diff("app.py", ["    return 'bye'"], ["    return 'goodbye'"])
    assert weakened("M\tapp.py", change) == []


def test_a_moved_or_renamed_test_file_passes_the_guard() -> None:
    assert weakened("R100\ttests/test_a.py\ttests/unit/test_a.py", "") == []


def test_the_guard_finds_what_a_ci_fix_must_not_do() -> None:
    names = "D\ttests/test_api.py\nM\t.github/workflows/ci.yml\nM\ttests/test_app.py"
    changes = "\n".join(
        [
            diff(
                "tests/test_app.py",
                ["def test_one():", "def test_two():"],
                ["@pytest.mark.skip(reason='flaky')", "def test_one():"],
            ),
            diff("web/app.test.js", [], ["  it.skip('renders', () => {"]),
        ]
    )

    found = weakened(names, changes)

    assert found == [
        Finding("deleted", "tests/test_api.py"),
        Finding("ci_config", ".github/workflows/ci.yml"),
        Finding("skip", "tests/test_app.py", "@pytest.mark.skip(reason='flaky')"),
        Finding("removed", "tests/test_app.py", "1"),
        Finding("skip", "web/app.test.js", "it.skip('renders', () => {"),
    ]


def test_a_test_file_renamed_away_by_extension_or_folder_counts_as_deleted() -> None:
    names = "\n".join(
        [
            "R100\ttests/test_api.py\ttests/test_api.py.disabled",
            "R100\ttests/test_db.py\ttests/test_db.txt",
            "R100\ttests/test_ui.py\told/test_ui.py",
            "R100\tpkg/app_test.go\tpkg/app_test.bak",
        ]
    )

    assert weakened(names, "") == [
        Finding("deleted", "tests/test_api.py"),
        Finding("deleted", "tests/test_db.py"),
        Finding("deleted", "tests/test_ui.py"),
        Finding("deleted", "pkg/app_test.go"),
    ]


def test_focused_tests_and_other_ways_to_skip_are_found() -> None:
    added = [
        "  it.only('logs in', () => {",
        "fit('logs in', () => {",
        'pytest.importorskip("numpy")',
        '        self.skipTest("later")',
        '#[ignore = "slow"]',
        "\tpytest --ignore=tests/test_api.py",
        "npm test || true",
    ]
    names = "\n".join(f"M\tstep{n}.txt" for n in range(len(added)))
    changes = "\n".join(diff(f"step{n}.txt", [], [line]) for n, line in enumerate(added))

    assert [finding.kind for finding in weakened(names, changes)] == ["skip"] * len(added)


def test_a_method_called_fit_is_not_a_focused_test() -> None:
    change = diff("model.py", [], ["    model.fit(features, labels)"])
    assert weakened("M\tmodel.py", change) == []


def test_a_change_to_how_the_runner_starts_counts_as_ci_configuration() -> None:
    changes = "\n".join(
        [
            diff("package.json", ['"test": "jest",'], ['"test": "jest --passWithNoTests",']),
            diff("pyproject.toml", [], ['testpaths = ["tests/unit"]']),
        ]
    )
    found = weakened("M\tpackage.json\nM\tpyproject.toml", changes)

    assert found == [Finding("ci_config", "package.json"), Finding("ci_config", "pyproject.toml")]
