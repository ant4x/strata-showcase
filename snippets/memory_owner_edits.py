# Excerpt from Strata: strata/memory/store.py. Omitted: imports, the logger, the constants PRIVATE, LEARNED and UPDATE_TRIES, the classes MemoryPathError, MemoryChanged, _Unset (with UNSET) and Place, the helpers _locked, _write_atomic, _section, _insert_fact, _without_fact, _one_line and _short, and every method of Memory except write and _owner_edited. Source is private.
"""Read and write memory, and commit every change to the right repository.

Memory is one folder (one Obsidian vault) holding two git repositories:

- the work repository at the root (style, brands, topics, channels, drafts,
  ideas), which may be backed up to a private GitHub repository;
- `private/`, the personal repository (profile, notes, conversation summaries,
  incidents), which never leaves the server.

Paths are always relative to the root and must stay inside it. Everything
written is redacted first and gets `updated` and `source` in its frontmatter.
Each repository's `INDEX.md` (`strata.memory.index`) is rebuilt on every
commit and goes into the same commit; a new note may not take a name another
indexed note already has.

The owner's edits win. The owner edits on the laptop and pushes into the
server's repository, so a note can change under Strata at any moment.
- `update` is for edits that keep the rest of the note (a fact under
  `## Learned`, the playbook's research section, a topic) and for edits the
  owner asked for. If the note changes between reading and writing it, the
  edit is applied again to the new text.
- `write` replaces a whole note. It does not replace one the owner ever
  edited (a commit not by Strata, or a change not committed yet), nor one
  that changed since the caller read it (`expected`): Strata's text goes
  into that note's `## Proposed by Strata` section instead.
When a folder is not a git repository (memory not initialised yet, or the
local fallback when MEMORY_REPO_PATH is empty), files are still written but
nothing is committed.
"""

PROPOSED = "## Proposed by Strata"


class Memory:
    async def write(
        self,
        relative: str,
        text: str,
        *,
        message: str,
        source: str,
        commit: bool = True,
        expected: str | _Unset | None = UNSET,
        owner: bool = False,
    ) -> Path:
        """Redact, stamp the frontmatter, write atomically, commit.

        Replaces the whole note, unless the owner's version must be kept (see
        the module's docstring): then Strata's text becomes a proposal in it.
        `expected` is the text the caller read (None: no file). `owner=True`
        for a change the owner asked for, which replaces whatever is there.
        `commit=False` leaves the commit to the caller, to group several files.
        """
        place = self.place(relative)
        async with _locked(place.repo):
            current = self.read(relative)
            if not owner and current is not None:
                stale = expected is not UNSET and current != expected
                if stale or (
                    notes_index.kind_of(relative) is not None and await self._owner_edited(place)
                ):
                    note = (
                        f"Strata's version, {self.today()} ({source}). Your text above is "
                        "kept: take what you need from here, then delete this section."
                    )
                    text = _proposal(current, redact(text), note)
                    message = f"{message} (a proposal: the owner's version is kept)"
                    log.info("%s: the owner's version is kept; Strata's is a proposal", relative)
            path = await self._put(relative, text, source=source)
        if commit:
            await self.commit([relative], message)
        return path

    async def _owner_edited(self, place: Place) -> bool:
        """Whether anyone but Strata wrote this note: a commit, or a change not committed.

        When git fails, the note counts as the owner's, so it is kept. A folder that
        is not a git repository has no history to ask, and Strata writes as usual.
        """
        if not is_repo(place.repo):
            return False
        code, status = await run_git(
            place.repo, "status", "--porcelain", "--", place.path, check=False
        )
        if code != 0 or status.strip():
            return True
        code, authors = await run_git(
            place.repo, "log", "--follow", "--format=%ae", "--", place.path, check=False
        )
        if code != 0:
            return True
        return any(author.strip() != EMAIL for author in authors.split() if author.strip())


def _proposal(current: str, proposed: str, note: str) -> str:
    """The owner's note as it is, with Strata's version in its `## Proposed by Strata`.

    Frontmatter keys the owner's note lacks are added from Strata's version;
    the owner's values stay. The proposal is a callout (`> ` lines), so its
    own headings cannot end the section. A newer proposal replaces Strata's
    callout and nothing else: whatever the owner wrote in the section stays,
    below the new callout.
    """
    head, body_text = frontmatter.split(frontmatter.with_missing(current, proposed))
    lines = body_text.rstrip("\n").split("\n") if body_text.strip() else []
    quoted = [
        f"> {line}" if line.strip() else ">"
        for line in frontmatter.body(proposed).strip().split("\n")
    ]
    section = [PROPOSED, "", f"> [!note] {note}", ">", *quoted]
    start = next((i for i, line in enumerate(lines) if line.strip() == PROPOSED), None)
    if start is None:
        lines += ["", *section] if lines else section
    else:
        end = next((i for i in range(start + 1, len(lines)) if HEADING.match(lines[i])), len(lines))
        own = _without_callout(lines[start + 1 : end])
        after = [""] if end < len(lines) else []
        lines[start:end] = section + (["", *own] if own else []) + after
    return frontmatter.join(head, "\n".join(lines) + "\n")
