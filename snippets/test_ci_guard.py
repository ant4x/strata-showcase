# Excerpt from Strata: tests/test_dev_ci.py. Omitted: imports and the five tests of the log excerpt, the fence and the follow-up text. Source is private.
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


def test_a_skip_that_was_already_there_and_moved_is_not_new() -> None:
    change = diff("tests/test_app.py", ["@pytest.mark.skip"], ["@pytest.mark.skip"])
    assert weakened("M\ttests/test_app.py", change) == []


def test_a_content_line_that_looks_like_a_header_is_not_one() -> None:
    change = diff("app.py", [], ["++ not a header, a line of content"])
    assert weakened("M\tapp.py", change) == []
