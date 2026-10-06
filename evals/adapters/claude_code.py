"""Claude Code adapter for live evals.

Invokes the `claude` CLI non-interactively against a case prompt inside a
rootless Bubblewrap sandbox (see ``isolation.py``) that exposes only the trial
directory, the Python runtime, the shipped package and the CLI. It needs an
explicit credential (``ANTHROPIC_API_KEY``, ``CLAUDE_CODE_OAUTH_TOKEN`` or
``--credential-file``) and a positive ``--max-budget-usd``, and refuses to run
unisolated. Only imported by `--mode live` runs, never by fixture CI.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from .base import AgentAdapter, AgentRunResult, parse_json_lines
from .isolation import Sandbox, unavailable_reason
from .routing import credentials

# Granted explicitly: the sandbox hides the host's Claude settings, and in
# print mode any tool without an allow rule is refused. The sandbox, not a
# per-command prompt, is what confines these tools.
ALLOWED_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep", "Bash", "Skill", "WebSearch", "WebFetch")

# Non-secret provider routing the operator may set (e.g. a workspace header
# that scopes spend); forwarded by name, recorded by name only.
PROVIDER_SETTINGS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS")


def _provider_failure(events: list[dict]) -> dict | None:
    """Recognize terminal API errors, never quoted/tool/assistant output."""
    event = next((event for event in reversed(events) if event.get("type") == "result"), {})
    if event.get("is_error") is not True:
        return None
    message = event.get("result")
    if not isinstance(message, str):
        return None
    api_error = re.match(r"API Error:\s*(\d{3})\b", message)
    if event.get("terminal_reason") != "api_error" and not api_error:
        return None
    status = event.get("api_error_status") or (int(api_error[1]) if api_error else None)
    lower = message.lower()
    category = None
    if any(text in lower for text in (
        "you have reached your specified workspace api usage limits",
        "you have reached your specified organization api usage limits",
        "credit balance is too low",
        "monthly spend limit",
    )):
        category = "quota_exhausted"
    elif status == 401:
        category = "authentication"
    elif status == 403:
        category = "permission_denied"
    if category is None:
        return None  # Rate limits, server errors and trial budgets stay local.
    return {"category": category, "scope": "provider", "retryable": False, "message": message[:2000]}


def _claude_observability(events: list[dict], requested_model: str | None) -> tuple[str | None, dict, float | None, str | None, bool]:
    result_event = next((event for event in reversed(events) if event.get("type") == "result"), None)
    resolved_model: str | None = None
    for event in events:
        if event.get("type") == "system" and isinstance(event.get("model"), str):
            resolved_model = event["model"]
        message = event.get("message")
        if isinstance(message, dict) and isinstance(message.get("model"), str):
            resolved_model = message["model"]

    usage: dict[str, int | float | None] = {}
    cost_usd: float | None = None
    final_message: str | None = None
    completed = False
    if isinstance(result_event, dict):
        completed = result_event.get("subtype") == "success" and not result_event.get("is_error", False)
        raw_usage = result_event.get("usage")
        if isinstance(raw_usage, dict):
            usage.update({key: value for key, value in raw_usage.items() if isinstance(value, (int, float)) and not isinstance(value, bool)})
        if isinstance(result_event.get("total_cost_usd"), (int, float)):
            cost_usd = float(result_event["total_cost_usd"])
        if isinstance(result_event.get("result"), str):
            final_message = result_event["result"]
        model_usage = result_event.get("modelUsage") or result_event.get("model_usage")
        if isinstance(model_usage, dict) and len(model_usage) == 1:
            resolved_model = next(iter(model_usage))

    if "total_tokens" not in usage:
        token_fields = (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
        )
        values = [usage.get(field) for field in token_fields]
        if any(isinstance(value, (int, float)) for value in values):
            usage["total_tokens"] = sum(value for value in values if isinstance(value, (int, float)))
    return resolved_model or requested_model, usage, cost_usd, final_message, completed


class ClaudeCodeAdapter(AgentAdapter):
    name = "claude_code"
    executable = "claude"

    def __init__(self, *, max_budget_usd: float | None = None, credential_file: Path | None = None) -> None:
        self.max_budget_usd = max_budget_usd
        self.credential_file = credential_file

    def _refusal(self, reason: str, workspace: Path, executable: str, seed: int | None) -> AgentRunResult:
        return AgentRunResult(
            agent=self.name,
            model=None,
            workspace=workspace,
            duration_s=0.0,
            success=False,
            returncode=None,
            command=[self.executable],
            stderr=reason,
            permissions={"mode": "acceptEdits", "session_persistence": False},
            metadata={"executable": executable, "requested_seed": seed},
        )

    def _preflight(self, executable_path: str | None) -> tuple[str | None, str, dict[str, str]]:
        """A refusal reason, or ``None`` with the credential name and host environment."""
        if executable_path is None:
            return "`claude` CLI not found on PATH", "", {}
        if self.max_budget_usd is None or self.max_budget_usd <= 0:
            return "a positive --max-budget-usd is required for a paid live trial", "", {}
        reason = unavailable_reason()
        if reason:
            return f"refusing an unisolated live run: {reason}", "", {}
        try:
            credential, host_environment = credentials("claude_code", self.credential_file)
        except ValueError as exc:
            return str(exc), "", {}
        if not host_environment.get(credential):
            return "no Claude credential: set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN, or pass --credential-file", "", {}
        return None, credential, host_environment

    def run(
        self,
        prompt: str,
        workspace: Path,
        fixture: Path | None = None,
        timeout_s: int = 900,
        model: str | None = None,
        seed: int | None = None,
    ) -> AgentRunResult:
        executable_path = shutil.which(self.executable)
        reason, credential, host_environment = self._preflight(executable_path)
        if reason or executable_path is None:
            return self._refusal(reason or "", workspace, executable_path or self.executable, seed)
        secret = host_environment[credential]

        if fixture is not None and fixture.exists():
            shutil.copytree(fixture, workspace, dirs_exist_ok=True)

        version = self.cli_version(executable_path)
        start = time.monotonic()
        command = [
            self.executable,
            "--safe-mode",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            ",".join(ALLOWED_TOOLS),
            "--no-session-persistence",
            "--no-chrome",
            "--max-budget-usd",
            str(self.max_budget_usd),
        ]
        if model:
            command.extend(["--model", model])
        sandbox = Sandbox.for_python_agent(self.trial_root or workspace, {"claude": Path(executable_path)})
        forwarded = {name: host_environment[name] for name in PROVIDER_SETTINGS if host_environment.get(name)}
        invocation = [*sandbox.argv(workspace), *command, prompt]
        recorded_command = [*command, "<PROMPT:prompt.md>"]
        timed_out = False
        try:
            proc = subprocess.run(
                invocation,
                env=sandbox.environment({**forwarded, credential: secret}),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
            success = proc.returncode == 0
            returncode = proc.returncode
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            success = False
            returncode = None
            timed_out = True
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
            stderr = f"timed out after {timeout_s}s"
        except OSError as exc:
            success = False
            returncode = None
            stdout = ""
            stderr = f"{type(exc).__name__}: {exc}"
        duration = time.monotonic() - start
        # Tool output can echo the environment; retain the stream shape only.
        stdout = stdout.replace(secret, "<REDACTED>")
        stderr = stderr.replace(secret, "<REDACTED>")
        events, unparsed_lines = parse_json_lines(stdout)
        resolved_model, usage, cost_usd, final_message, completed = _claude_observability(events, model)
        models_observed: set[str] = set()
        for event in events:
            if isinstance(event.get("model"), str):
                models_observed.add(event["model"])
            message = event.get("message")
            if isinstance(message, dict) and isinstance(message.get("model"), str):
                models_observed.add(message["model"])
            model_usage = event.get("modelUsage") or event.get("model_usage")
            if isinstance(model_usage, dict):
                models_observed.update(str(name) for name in model_usage)
        success = success and completed and bool(resolved_model) and bool(version)
        if returncode == 0 and not completed:
            stderr = (stderr + "\n" if stderr else "") + ("Claude Code exited successfully without a successful result event")
        if returncode == 0 and not resolved_model:
            stderr = (stderr + "\n" if stderr else "") + "Claude model identity is unresolved"
        if returncode == 0 and not version:
            stderr = (stderr + "\n" if stderr else "") + "Claude Code version is unresolved"

        return AgentRunResult(
            agent=self.name,
            model=resolved_model,
            workspace=workspace,
            duration_s=duration,
            success=success,
            returncode=returncode,
            command=recorded_command,
            stdout=stdout,
            stderr=stderr,
            version=version,
            events=events,
            usage=usage,
            cost_usd=cost_usd,
            final_message=final_message,
            failure=_provider_failure(events) if not success else None,
            permissions={
                "mode": "acceptEdits",
                "session_persistence": False,
                "chrome": False,
                "customizations": False,
                "allowed_tools": list(ALLOWED_TOOLS),
                "max_budget_usd": self.max_budget_usd,
                "credential": credential,
                "provider_settings": sorted(forwarded),
                "isolation": sandbox.evidence(),
            },
            metadata={
                "executable": executable_path,
                "requested_seed": seed,
                "timed_out": timed_out,
                "timeout_s": timeout_s,
                "requested_model": model,
                "models_observed": sorted(models_observed),
                "model_source": ("event" if models_observed else "requested" if model else "unresolved_cli_default"),
                "structured_completion": completed,
                "unparsed_stdout_lines": unparsed_lines,
                "cost_available": cost_usd is not None,
                "cost_is_estimate": cost_usd is not None,
            },
        )
