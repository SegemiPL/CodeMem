from __future__ import annotations

import unittest

try:
    from evaluation.agents.memory_codex import MemoryCodex
except ModuleNotFoundError as exc:
    if exc.name != "harbor":
        raise
    MemoryCodex = None  # type: ignore[assignment,misc]


@unittest.skipIf(MemoryCodex is None, "Harbor is not installed in this test environment")
class MemoryCodexTest(unittest.TestCase):
    def test_setup_enables_and_restores_memories(self) -> None:
        command = 'ln -sf /tmp/auth.json "$CODEX_HOME/auth.json"'
        augmented = MemoryCodex.augment_command(command)
        self.assertIn("[features]", augmented)
        self.assertIn("memories = true", augmented)
        self.assertIn("[memories]", augmented)
        self.assertIn("/logs/agent/memories", augmented)
        self.assertIn('cp -R /logs/agent/memories "$CODEX_HOME/memories"', augmented)

    def test_run_enables_memories_feature(self) -> None:
        command = "codex exec --enable unified_exec -- prompt"
        augmented = MemoryCodex.augment_command(command)
        self.assertIn("--enable unified_exec --enable memories", augmented)

    def test_cleanup_copies_memories_before_deleting_home(self) -> None:
        command = 'rm -rf /tmp/codex-secrets "$CODEX_HOME"'
        augmented = MemoryCodex.augment_command(command)
        copy_index = augmented.index(
            'cp -R "$CODEX_HOME/memories" /logs/agent/memories'
        )
        cleanup_index = augmented.index(command)
        self.assertLess(copy_index, cleanup_index)
        self.assertNotIn("auth.json", augmented[:cleanup_index])


if __name__ == "__main__":
    unittest.main()
