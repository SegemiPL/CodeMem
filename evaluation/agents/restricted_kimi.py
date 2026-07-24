from harbor.agents.installed.kimi_cli import KimiCli

from evaluation.agents.restricted import RestrictedAgentMixin


class RestrictedKimiCli(RestrictedAgentMixin, KimiCli):
    """Run Kimi CLI as the unprivileged CodeMem agent user."""
