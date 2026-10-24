#!/bin/bash
set -euo pipefail
umask 077

TRUSTED_PYTHON=/usr/local/bin/python3
TRUSTED_TIMEOUT=/usr/bin/timeout
LOG_DIR=/logs/verifier
REWARD_FILE="$LOG_DIR/reward.txt"
JUNIT_FILE="$LOG_DIR/junit.xml"
SCORE_REPORT="$LOG_DIR/scoring-report.json"
STATUS_FILE="$LOG_DIR/status.txt"

mkdir -p "$LOG_DIR"
SITE_PACKAGES="$("$TRUSTED_PYTHON" -S -P -B -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"

write_reward() {
  chmod 0644 "$REWARD_FILE" 2>/dev/null || true
  printf '%s\n' "$1" > "$REWARD_FILE"
  chmod 0444 "$REWARD_FILE"
}

write_incomplete() {
  write_reward '0.000'
  printf 'INCOMPLETE: %s\n' "$1" > "$STATUS_FILE"
}

write_reward '0.000'
printf 'INCOMPLETE: verifier not started\n' > "$STATUS_FILE"
cleanup() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    write_incomplete "verifier exited unexpectedly with status $rc"
  fi
}
trap cleanup EXIT
trap 'write_incomplete "verifier interrupted"; exit 0' INT TERM HUP

set +e
(
  cd /tests
  env -i \
    HOME=/tmp \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PYTHONNOUSERSITE=1 \
    PYTHONSAFEPATH=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    PYTHONPATH="$SITE_PACKAGES:/tests" \
    "$TRUSTED_TIMEOUT" -s TERM 150s \
    "$TRUSTED_PYTHON" -S -P -B -m pytest \
      -s -p no:cacheprovider \
      --junitxml="$JUNIT_FILE" \
      -q /tests/test_outputs.py
)
pytest_rc=$?
set -e

if [ "$pytest_rc" -ne 0 ] || [ ! -s "$JUNIT_FILE" ]; then
  write_incomplete "pytest did not produce a complete report"
  exit 0
fi

set +e
(
  cd /tests
  env -i \
    HOME=/tmp \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PYTHONNOUSERSITE=1 \
    PYTHONSAFEPATH=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    PYTHONPATH="$SITE_PACKAGES:/tests" \
    "$TRUSTED_TIMEOUT" -s TERM 10s \
    "$TRUSTED_PYTHON" -S -P -B - \
      "$JUNIT_FILE" /tests/scoring.json <<'PY'
import json
import sys
import xml.etree.ElementTree as ET

report = ET.parse(sys.argv[1]).getroot()
config = json.load(open(sys.argv[2], encoding="utf-8"))
observed = {
    "test_outputs.py::" + case.get("name", "")
    for case in report.iter("testcase")
}
expected = set(config["gate_tests"]) | set(config["tests"])
if observed != expected:
    raise SystemExit("JUnit testcase set does not match scoring config")
PY
)
report_rc=$?
set -e

if [ "$report_rc" -ne 0 ]; then
  write_incomplete "structured verifier report is incomplete"
  exit 0
fi

set +e
(
  cd /tests
  env -i \
    HOME=/tmp \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PYTHONNOUSERSITE=1 \
    PYTHONSAFEPATH=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    PYTHONPATH="$SITE_PACKAGES:/tests" \
    "$TRUSTED_TIMEOUT" -s TERM 20s \
    "$TRUSTED_PYTHON" -S -P -B /tests/score.py \
      --config /tests/scoring.json \
      --junit "$JUNIT_FILE" \
      --report-out "$SCORE_REPORT" \
      --reward-out "$REWARD_FILE"
)
score_rc=$?
set -e

if [ "$score_rc" -ne 0 ] || [ ! -s "$SCORE_REPORT" ]; then
  write_incomplete "weighted scorer did not complete"
  exit 0
fi

set +e
(
  cd /tests
  env -i \
    HOME=/tmp \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PYTHONNOUSERSITE=1 \
    PYTHONSAFEPATH=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    PYTHONPATH="$SITE_PACKAGES:/tests" \
    "$TRUSTED_TIMEOUT" -s TERM 10s \
    "$TRUSTED_PYTHON" -S -P -B - "$SCORE_REPORT" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
if report.get("status") != "COMPLETED":
    raise SystemExit("scoring report is incomplete")
PY
)
completion_rc=$?
set -e

if [ "$completion_rc" -ne 0 ]; then
  write_incomplete "scoring completion report is invalid"
  exit 0
fi

chmod 0644 "$REWARD_FILE"
chmod 0444 "$REWARD_FILE"
printf 'COMPLETED\n' > "$STATUS_FILE"
trap - EXIT INT TERM HUP
exit 0
