# Excerpt from Strata: tests/test_memory_owner_edits.py. Omitted: imports and three tests of section edits (an edit applied again after a push, a note that keeps changing, facts and topics). Source is private.
"""The owner's edits win: proposals instead of rewrites, edits applied again after a push."""

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
CARD = "projects/stratoscope.md"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def owner_commits(repo: Path, relative: str, text: str) -> None:
    """The owner edits a note on the laptop and pushes it to the server."""
    (repo / relative).parent.mkdir(parents=True, exist_ok=True)
    (repo / relative).write_text(text)
    git(repo, "add", relative)
    git(repo, "-c", "user.name=Owner", "-c", "user.email=owner@laptop", "commit", "-qm", "edit")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    memory = tmp_path / "memory"
    subprocess.run(
        ["bash", str(ROOT / "scripts" / "init_memory.sh"), str(memory)],
        capture_output=True,
        check=True,
    )
    return memory


@pytest.fixture
def memory(root: Path) -> Memory:
    return Memory(root, clock=lambda: NOW)


STRATAS = "---\ntype: project\ndescription: Telescope app\nstatus: active\n---\n# Stratoscope\n"


async def test_a_note_only_strata_wrote_is_replaced(memory: Memory, root: Path) -> None:
    await memory.write(CARD, STRATAS + "\nOld plan.\n", message="card", source="assistant")
    await memory.write(CARD, STRATAS + "\nNew plan.\n", message="card", source="assistant")

    text = (root / CARD).read_text()
    assert "New plan." in text and "Old plan." not in text and PROPOSED not in text


async def test_a_note_the_owner_edited_gets_a_proposal_instead(memory: Memory, root: Path) -> None:
    owner_commits(root, CARD, "---\ndescription: My telescope\n---\n# Stratoscope\n\nMY TEXT\n")
    proposed = STRATAS + "\n## Log\n\n- 2026-10-05 Launched.\n"

    await memory.write(CARD, proposed, message="memory: card", source="assistant")

    text = (root / CARD).read_text()
    assert text.split(PROPOSED)[0].rstrip().endswith("# Stratoscope\n\nMY TEXT")
    fields = frontmatter.fields(text)
    assert fields["description"] == "My telescope", "the owner's value stays"
    assert fields["type"] == "project" and fields["status"] == "active", "missing keys added"
    assert text.endswith(
        "## Proposed by Strata\n\n> [!note] Strata's version, 2026-10-05 (assistant). Your "
        "text above is kept: take what you need from here, then delete this section.\n>\n"
        "> # Stratoscope\n>\n> ## Log\n>\n> - 2026-10-05 Launched.\n"
    )
    assert git(root, "log", "-1", "--format=%s %ae") == (
        "memory: card (a proposal: the owner's version is kept) strata@localhost"
    )

    await memory.write(CARD, STRATAS + "\nSecond try.\n", message="again", source="assistant")

    text = (root / CARD).read_text()
    assert text.count(PROPOSED) == 1 and "Second try." in text and "Launched" not in text
    assert "MY TEXT" in text, "Strata's own commit since does not make the note Strata's"


async def test_a_change_not_committed_yet_is_the_owners_too(memory: Memory, root: Path) -> None:
    await memory.write(CARD, STRATAS, message="card", source="assistant")
    (root / CARD).write_text("# Stratoscope\n\nTyped on the server, not committed\n")

    await memory.write(CARD, STRATAS + "\nNew.\n", message="card", source="assistant")

    assert "Typed on the server" in (root / CARD).read_text()


async def test_the_owners_own_request_and_strata_files_are_replaced(
    memory: Memory, root: Path
) -> None:
    owner_commits(root, CARD, "# Stratoscope\n\nMY TEXT\n")
    owner_commits(root, "drafts/2026-10-05/a.md", "draft by hand")

    await memory.write(CARD, STRATAS, message="approved", source="ideas", owner=True)
    await memory.write("drafts/2026-10-05/a.md", "new draft", message="d", source="t")

    assert "MY TEXT" not in (root / CARD).read_text(), "the owner asked for it"
    assert "new draft" in (root / "drafts/2026-10-05/a.md").read_text(), "not an indexed note"


async def test_a_note_that_changed_since_it_was_read_gets_a_proposal(
    memory: Memory, root: Path
) -> None:
    await memory.write(CARD, STRATAS, message="card", source="assistant")
    read = memory.read(CARD)
    (root / CARD).write_text(STRATAS + "\nA push landed meanwhile.\n")
    git(root, "-c", "user.name=Strata", "-c", "user.email=strata@localhost", "commit", "-qam", "x")

    await memory.write(CARD, STRATAS + "\nStale.\n", message="c", source="t", expected=read)

    text = (root / CARD).read_text()
    assert "A push landed meanwhile." in text.split(PROPOSED)[0] and "> Stale." in text
