import json

from harbor.agents.installed.kimi_cli import KimiCli

from evaluation.agents.restricted import RestrictedAgentMixin
from evaluation.common.network_isolation import (
    INFERENCE_RELAY_DUMMY_KEY,
    LOOPBACK_DIRECT_ENV,
)


class RestrictedKimiCli(RestrictedAgentMixin, KimiCli):
    """Run Kimi CLI as the unprivileged CodeMem agent user."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if (
            self._base_url is None
            and self.model_name
            and self.model_name.startswith("openai/")
        ):
            self._base_url = self._get_env("OPENAI_BASE_URL")

    def network_gateway_urls(self) -> tuple[str, ...]:
        explicit = (
            self._get_env("CODEMEM_MODEL_GATEWAY_URL")
            or self._base_url
        )
        if explicit:
            return (explicit,)
        provider = (self.model_name or "").split("/", 1)[0]
        defaults = {
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com",
            "moonshot": "https://api.moonshot.cn/v1",
            "kimi": "https://api.kimi.com/coding/v1",
            "gemini": "https://generativelanguage.googleapis.com",
            "google": "https://generativelanguage.googleapis.com",
            "openrouter": "https://openrouter.ai/api/v1",
        }
        try:
            return (defaults[provider],)
        except KeyError as exc:
            raise ValueError(
                f"No network gateway is defined for Kimi provider {provider!r}"
            ) from exc

    def inference_api_key(self) -> str:
        provider = (self.model_name or "").split("/", 1)[0]
        return self._resolve_api_key(provider)

    def inference_auth_mode(self) -> str:
        provider = (self.model_name or "").split("/", 1)[0]
        return "x-api-key" if provider == "anthropic" else "bearer"

    def inference_models(self) -> tuple[str, ...]:
        parts = (self.model_name or "").split("/", 1)
        return (parts[-1],)

    def agent_relay_environment(self) -> dict[str, str]:
        return {
            **LOOPBACK_DIRECT_ENV,
            "HARBOR_KIMI_API_KEY": INFERENCE_RELAY_DUMMY_KEY,
        }

    def _build_config_json(self, provider: str, model: str) -> str:
        config = json.loads(super()._build_config_json(provider, model))
        if self._network_isolation_active:
            config["providers"]["harbor"]["base_url"] = self.inference_relay_url()
        return json.dumps(config)
