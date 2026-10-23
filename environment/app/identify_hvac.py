#!/usr/bin/env python3
"""Partial HVAC identification and control entry point."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("REFUSED: expected input and output paths", file=sys.stderr)
        return 2
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    payload = json.loads(source.read_text(encoding="utf-8"))
    actuators = [item["actuator_id"] for item in payload.get("actuator_tests", [])]
    zones = [item["sensor_id"] for item in payload.get("zones", [])]
    result = {
        "status": "ok",
        "pairing": {
            actuator: zones[index % len(zones)]
            for index, actuator in enumerate(actuators)
        },
        "plants": {
            actuator: {"gain_c_per_unit": 1.0, "tau_minutes": 10.0}
            for actuator in actuators
        },
        "controller_gains": {
            actuator: {"kp": 1.0, "ki": 0.1}
            for actuator in actuators
        },
    }
    target.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
