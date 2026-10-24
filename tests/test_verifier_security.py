from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


TASK_ROOT = Path(__file__).resolve().parents[1]
TEST_SH = TASK_ROOT / "tests" / "test.sh"
TEST_PY = TASK_ROOT / "tests" / "test_outputs.py"
APP_ROOT = TASK_ROOT / "environment" / "app"


def test_verifier_uses_trusted_execution_chain() -> None:
    text = TEST_SH.read_text(encoding="utf-8")
    required = (
        "TRUSTED_PYTHON=/usr/local/bin/python3",
        "TRUSTED_TIMEOUT=/usr/bin/timeout",
        "env -i",
        "PYTHONNOUSERSITE=1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
        'PYTHONPATH="$SITE_PACKAGES:/tests"',
        "-p no:cacheprovider",
        "--junitxml",
    )
    assert all(value in text for value in required)
    assert "$TRUSTED_PYTHON\" -S -P -B" in text


def test_candidate_visible_tree_has_no_startup_hooks() -> None:
    forbidden = [
        path
        for path in APP_ROOT.rglob("*")
        if path.name in {"sitecustomize.py", "usercustomize.py", "pytest.py"}
        or path.suffix == ".pth"
    ]
    assert forbidden == []


def test_candidate_subprocesses_use_isolated_python() -> None:
    text = TEST_PY.read_text(encoding="utf-8")
    assert '"-S", "-P", "-B", ENTRY' in text
    assert "env=SAFE_ENV" in text


def test_sitecustomize_is_ignored_in_isolated_python(tmp_path: Path) -> None:
    marker = tmp_path / "startup-ran"
    (tmp_path / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('unsafe')\n",
        encoding="utf-8",
    )
    environment = {
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    completed = subprocess.run(
        [sys.executable, "-S", "-P", "-B", "-c", "print('ok')"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "ok"
    assert not marker.exists()
