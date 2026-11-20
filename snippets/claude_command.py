# Excerpt from Strata: strata/engines/claude_code.py. Omitted: the module docstring, imports, the logger, the other constants, the Process protocol, the Spawn alias, the CostLedger class, every method of ClaudeCodeEngine except command, subscription and child_env, and the helpers _kill_if_running, _json_result and _classify. Source is private.
WORKER_TOOL = "claude"  # the tool name the launcher knows
# Inherited variables that could change how the child signs in or what it loads.
_CLAUDE_ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE_")


class ClaudeCodeEngine:
    def command(
        self, session_id: str | None, limits: RunLimits, context_file: Path | None
    ) -> list[str]:
        argv = [self.binary, "-p", "--output-format", "json", "--model", limits.model]
        argv.append("--safe-mode" if self.subscription else "--bare")
        # Restricted: no tool that runs commands or code (read-only ones such
        # as `cat` would otherwise run unasked in every mode), file tools stay
        # in the working directory, and no user or project settings load, so
        # a checkout's `.claude/settings.json` adds nothing. Only the tools
        # this run lists exist, and anything else is denied, never auto mode.
        argv += ["--restricted", "--tools", ",".join(limits.allowed_tools)]
        argv += ["--permission-mode", "dontAsk"]
        if limits.max_turns:
            argv += ["--max-turns", str(limits.max_turns)]
        if session_id:
            argv += ["--resume", session_id]
        if self.mcp_config is not None:
            argv += ["--mcp-config", str(self.mcp_config)]
        if context_file is not None:
            argv += ["--append-system-prompt-file", str(context_file)]
        if limits.allowed_tools:
            argv += ["--allowedTools", ",".join(limits.allowed_tools)]
        if self.isolation is not None:
            return self.isolation.command(self.workdir, WORKER_TOOL, argv[1:])
        return argv

    @property
    def subscription(self) -> bool:
        return self.auth == "subscription"

    def child_env(self) -> dict[str, str]:
        """The child's environment: nothing secret but the one sign-in key."""
        key = CLAUDE_CODE_AUTH[self.auth]
        secret = getattr(self.settings, key.lower())
        if secret is None or not secret.get_secret_value():
            raise NotConfiguredError(f"{self.name}: {key} is not set in .env (auth: {self.auth})")
        env = {
            name: value
            for name, value in subprocess_env(self.settings).items()
            if not name.upper().startswith(_CLAUDE_ENV_PREFIXES)
        }
        env[key] = secret.get_secret_value()
        if self.isolation is not None:
            # The worker user keeps its own config directory; sudo and the
            # launcher pass on only the sign-in key anyway.
            return env
        if self.config_dir is not None:
            # Session transcripts live here: readable by the service user only.
            self.config_dir.mkdir(parents=True, exist_ok=True)
            self.config_dir.chmod(0o700)
            env["CLAUDE_CONFIG_DIR"] = str(self.config_dir)
        return env
