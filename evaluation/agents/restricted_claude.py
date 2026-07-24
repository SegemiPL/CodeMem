from harbor.agents.installed.claude_code import ClaudeCode

from evaluation.agents.restricted import RestrictedAgentMixin
from evaluation.common.network_isolation import (
    INFERENCE_RELAY_DUMMY_KEY,
    INFERENCE_RELAY_ORIGIN,
    LOOPBACK_DIRECT_ENV,
)


class RestrictedClaudeCode(RestrictedAgentMixin, ClaudeCode):
    """Run Claude Code as the unprivileged CodeMem agent user."""

    def __init__(self, *args, **kwargs):
        # These tools may execute provider-side and therefore are not stopped
        # by container egress rules.
        existing = str(kwargs.get("disallowed_tools") or "")
        denied = [item for item in existing.split(",") if item]
        for tool in ("WebSearch", "WebFetch"):
            if tool not in denied:
                denied.append(tool)
        kwargs["disallowed_tools"] = ",".join(denied)
        super().__init__(*args, **kwargs)

    def network_gateway_urls(self) -> tuple[str, ...]:
        return (
            self._get_env("CODEMEM_MODEL_GATEWAY_URL")
            or self._get_env("ANTHROPIC_BASE_URL")
            or "https://api.anthropic.com",
        )

    def inference_api_key(self) -> str:
        return (
            self._get_env("ANTHROPIC_API_KEY")
            or self._get_env("ANTHROPIC_AUTH_TOKEN")
            or ""
        )

    def inference_auth_mode(self) -> str:
        return "x-api-key"

    def inference_models(self) -> tuple[str, ...]:
        return (self.model_name or "",)

    def agent_relay_environment(self) -> dict[str, str]:
        model = self.model_name or ""
        return {
            **LOOPBACK_DIRECT_ENV,
            "ANTHROPIC_API_KEY": INFERENCE_RELAY_DUMMY_KEY,
            "ANTHROPIC_AUTH_TOKEN": "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            # Claude Code appends /v1/messages itself, unlike the OpenAI
            # clients which append an endpoint directly to their /v1 base.
            "ANTHROPIC_BASE_URL": INFERENCE_RELAY_ORIGIN,
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
            "CLAUDE_CODE_SUBAGENT_MODEL": model,
        }
