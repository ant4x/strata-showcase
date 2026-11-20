# Excerpt from Strata: tests/test_dev_ci.py. Omitted: imports, the five tests of the log excerpt, the fence and the follow-up text, and two edge cases (a skip that only moved, a content line that looks like a diff header). Source is private.
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


def test_a_test_file_renamed_out_of_the_runners_reach_counts_as_deleted() -> None:
    names = "R100\ttests/test_api.py\ttests/api_old.py\nR100\tpkg/app_test.go\tpkg/app_old.go"
    assert weakened(names, "") == [
        Finding("deleted", "tests/test_api.py"),
        Finding("deleted", "pkg/app_test.go"),
    ]


def test_removed_go_java_and_rust_tests_are_found() -> None:
    names = "M\tpkg/app_test.go\nM\tsrc/test/java/AppTest.java\nM\ttests/api.rs"
    changes = "\n".join(
        [
            diff("pkg/app_test.go", ["func TestOne(t *testing.T) {"], []),
            diff("src/test/java/AppTest.java", ["    @Test"], []),
            diff("tests/api.rs", ["#[test]"], []),
        ]
    )

    assert weakened(names, changes) == [
        Finding("removed", "pkg/app_test.go", "1"),
        Finding("removed", "src/test/java/AppTest.java", "1"),
        Finding("removed", "tests/api.rs", "1"),
    ]


def test_tests_left_out_by_the_runners_settings_are_found() -> None:
    names = "M\tpyproject.toml\nM\tconftest.py"
    changes = "\n".join(
        [
            diff("pyproject.toml", [], ['addopts = "--deselect tests/test_api.py::test_login"']),
            diff("conftest.py", [], ['collect_ignore = ["tests/test_api.py"]']),
        ]
    )

    assert [finding.kind for finding in weakened(names, changes)] == ["skip", "skip"]
