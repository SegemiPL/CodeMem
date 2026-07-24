from harbor.agents.installed.claude_code import ClaudeCode

from evaluation.agents.restricted import RestrictedAgentMixin


class RestrictedClaudeCode(RestrictedAgentMixin, ClaudeCode):
    """Run Claude Code as the unprivileged CodeMem agent user."""
