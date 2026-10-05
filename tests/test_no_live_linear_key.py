"""Pins tests/conftest.py: every test starts with CI's Linear key, whatever the shell exported (DRE-5846).

An engineer agent runs this suite with the fleet's Linear key in its environment; without the conftest
override every test that reaches Linear spends (or writes with) that key. Run this file with a live-looking
key exported and it must still see only CI's placeholder.
"""
import os
import subprocess
import sys
from pathlib import Path


def test_a_test_sees_only_cis_linear_key():
    assert os.environ.get("LINEAR_API_KEY") == "test-key"
    for name in ("LINEAR_API_KEY_FALLBACK", "LINEAR_PLANNER_KEY", "LINEAR_RELEASE_KEY"):
        assert name not in os.environ, f"{name} reached a test"


def test_a_subprocess_a_test_starts_inherits_cis_key_too():
    """The scripts a test runs as a subprocess read the key from the environment they inherit."""
    out = subprocess.run([sys.executable, "-c", "import os; print(os.environ.get('LINEAR_API_KEY'))"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == "test-key"


def test_the_override_holds_against_a_live_looking_key_exported_by_the_shell(tmp_path):
    """Run a probe test in a child pytest with a live-looking key exported: the conftest must replace it."""
    probe = tmp_path / "test_probe.py"
    probe.write_text("import os\n\ndef test_probe():\n    assert os.environ['LINEAR_API_KEY'] == 'test-key'\n"
                     "    assert 'LINEAR_API_KEY_FALLBACK' not in os.environ\n")
    conftest = Path(__file__).with_name("conftest.py")
    (tmp_path / "conftest.py").write_text(conftest.read_text())
    env = {**os.environ, "LINEAR_API_KEY": "lin_api_" + "x" * 40, "LINEAR_API_KEY_FALLBACK": "lin_api_" + "y" * 40}
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(probe)],
                         cwd=tmp_path, env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
