"""The destructive-operation screen: what asks for permission before running.
It is a guard, not a sandbox — but the common spellings must not slip past."""

import pytest

from tools import danger_match


@pytest.mark.parametrize("command", [
    "rm -rf build", "rm -fr ~", "rm -r -f /tmp/x", "rm -Rf x", "rm x -r",
    "rm --recursive --force x", "find . -delete", "find . -name x -exec rm {} +",
    "git clean -fdx", "shred -u secrets.txt", "dd if=/dev/zero of=/dev/sda",
    "echo x > /dev/sda", "Remove-Item C:/x -Recurse -Force", "import shutil; shutil.rmtree(p)",
])
def test_destructive_commands_are_flagged(command):
    assert danger_match(command)


@pytest.mark.parametrize("command", [
    "rm file.txt", "rm -f file.txt", "rm --force file.txt", "rm -v a.txt", "ls -la",
    "grep -r foo .", "npm run build", "find . -name '*.py'", "git clean -n", "echo rm",
])
def test_everyday_commands_are_not_flagged(command):
    assert danger_match(command) is None
