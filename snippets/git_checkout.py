# Excerpt from Strata: strata/skills/dev_pipeline/checkout.py. Omitted: imports, the constants CLONE_SECONDS, PUSH_SECONDS and MAX_DIFF_CHARS, the classes SecretInChanges and Diff, github_url, and the Checkout methods exists, head, current_branch, start_branch, diff, reset and ahead_of. Source is private.
"""A checkout of a target repository for one dev job, and git on it.

Only the orchestrator side runs git; the coding worker only edits files in the
working tree. The worker can write anything in that folder, so nothing git
trusts lives there:

- The repository's git directory (config, hooks, objects) is kept outside the
  working tree (`git clone --separate-git-dir`), and every command names it
  and the working tree explicitly (`GIT_DIR`, `GIT_WORK_TREE`). The `.git`
  file in the working tree is never followed, so the worker cannot point git
  at a config of its own.
- Hooks are switched off for every command (`core.hooksPath=/dev/null`).
- The GitHub token reaches git only for clone, fetch and push, through that
  one command's environment: a credential helper for https://github.com set
  with `GIT_CONFIG_*` that reads the token from an environment variable. The
  token is never written to a file and never appears on a command line.
- Before anything is committed, the staged changes are checked for Strata's
  secrets; a change that contains one is not committed.
"""

GITHUB = "https://github.com"
TOKEN_VAR = "STRATA_GIT_TOKEN"
# A credential helper written inline, as git allows for a helper starting with "!".
HELPER = f'!f() {{ echo username=x-access-token; echo "password=${TOKEN_VAR}"; }}; f'


def config_env(entries: list[tuple[str, str]]) -> dict[str, str]:
    """Git configuration through the environment (git 2.31 and later)."""
    env = {"GIT_CONFIG_COUNT": str(len(entries))}
    for index, (key, value) in enumerate(entries):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def git_settings(token: str | None = None) -> dict[str, str]:
    """No hooks, and paths printed as they are, not quoted (a non-ASCII test file name must
    still read as a test file); with a token, only Strata's helper signs in to GitHub."""
    entries = [("core.hooksPath", "/dev/null"), ("core.quotePath", "false")]
    env: dict[str, str] = {}
    if token is not None:
        # An empty helper drops every helper configured before it (server-wide ones too).
        entries += [("credential.helper", ""), (f"credential.{GITHUB}.helper", HELPER)]
        env[TOKEN_VAR] = token
    return {**config_env(entries), **env}


class Checkout:
    """The working tree the worker edits, and the git directory only Strata touches."""

    def __init__(self, workdir: Path, gitdir: Path, token: str) -> None:
        self.workdir = workdir
        self.gitdir = gitdir
        self._token = token

    @classmethod
    async def clone(
        cls, url: str, workdir: Path, gitdir: Path, *, token: str, branch: str
    ) -> Checkout:
        workdir.parent.mkdir(parents=True, exist_ok=True)
        gitdir.parent.mkdir(parents=True, exist_ok=True)
        await run_git(
            workdir.parent,
            "clone",
            "--branch",
            branch,
            "--separate-git-dir",
            str(gitdir),
            "--",
            url,
            str(workdir),
            seconds=CLONE_SECONDS,
            extra_env=git_settings(token),
        )
        return cls(workdir, gitdir, token)

    async def git(
        self, *args: str, check: bool = True, sign_in: bool = False, seconds: float = 60.0
    ) -> tuple[int, str]:
        env = {
            "GIT_DIR": str(self.gitdir),
            "GIT_WORK_TREE": str(self.workdir),
            **git_settings(self._token if sign_in else None),
        }
        return await run_git(self.workdir, *args, check=check, seconds=seconds, extra_env=env)

    async def commit_all(self, message: str) -> str | None:
        """Commit everything the worker changed. Returns the commit, or None if nothing changed.

        Raises `SecretInChanges` (and unstages everything) if a secret is in the changes.
        """
        await self.git("add", "--all")
        # --text: a file the worker marks binary in .gitattributes is read all the same.
        _, staged = await self.git(
            "diff", "--cached", "--text", "--no-ext-diff", "--no-textconv", "--no-color"
        )
        if not staged.strip():
            return None
        if default_redactor().contains(staged):
            await self.git("reset", "--quiet")
            raise SecretInChanges(
                "The worker's changes contain a secret from .env; nothing was committed."
            )
        await self.git("commit", "--quiet", "--no-verify", "-m", redact(message))
        return await self.head()

    async def changes(self, base: str) -> tuple[str, str]:
        """Since `base`: the changed files (`--name-status`) and the whole diff without context.

        `--text`, so that a test file marked binary in .gitattributes is read all the same.
        """
        _, names = await self.git("diff", "--name-status", "-M", "--no-color", base)
        _, diff = await self.git("diff", "-U0", "--text", "--no-ext-diff", "--no-color", base)
        return names, diff

    async def push(self, branch: str) -> None:
        await self.git(
            "push",
            "--quiet",
            "origin",
            f"HEAD:refs/heads/{branch}",
            sign_in=True,
            seconds=PUSH_SECONDS,
        )
