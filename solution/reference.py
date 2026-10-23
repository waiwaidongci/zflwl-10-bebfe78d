#!/usr/bin/env python3
"""Reference HVAC identification and PI synthesis solver."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any


TOP_KEYS = {"schema", "sample_seconds", "zones", "actuator_tests", "controller_spec"}
SENSOR_ID = re.compile(r"SENS-[A-Z0-9]{3}\Z")
VALVE_ID = re.compile(r"VALVE-[A-Z0-9]{3}\Z")
ZONE_ID = re.compile(r"ZONE-[A-Z0-9]{3}\Z")


class Refused(ValueError):
    pass


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Refused(f"{label} is not numeric")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0.0):
        raise Refused(f"{label} is outside its domain")
    return result


def parse_payload(path: Path) -> dict[str, Any]:
    # 结构或值域不合规时统一拒收，避免坏件进入辨识。
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Refused("input JSON is unreadable") from exc
    if not isinstance(payload, dict) or set(payload) != TOP_KEYS:
        raise Refused("top-level keys are invalid")
    if payload.get("schema") != "hvac-id-v1":
        raise Refused("schema is unsupported")
    sample_seconds = payload.get("sample_seconds")
    if (
        isinstance(sample_seconds, bool)
        or not isinstance(sample_seconds, int)
        or not 10 <= sample_seconds <= 60
    ):
        raise Refused("sample_seconds must be an integer from 10 to 60")
    raw_zones = payload.get("zones")
    if not isinstance(raw_zones, list) or not 8 <= len(raw_zones) <= 12:
        raise Refused("zones must contain 8 to 12 entries")
    zones: list[dict[str, Any]] = []
    sensor_ids: set[str] = set()
    zone_ids: set[str] = set()
    for item in raw_zones:
        if not isinstance(item, dict) or set(item) != {
            "zone_id",
            "sensor_id",
            "ambient_c",
        }:
            raise Refused("zone keys are invalid")
        zone_id = item.get("zone_id")
        if not isinstance(zone_id, str) or ZONE_ID.fullmatch(zone_id) is None:
            raise Refused("zone id is invalid")
        if zone_id in zone_ids:
            raise Refused("zone ids must be unique")
        zone_ids.add(zone_id)
        sensor_id = item.get("sensor_id")
        if not isinstance(sensor_id, str) or SENSOR_ID.fullmatch(sensor_id) is None:
            raise Refused("sensor id is invalid")
        if sensor_id in sensor_ids:
            raise Refused("sensor ids must be unique")
        sensor_ids.add(sensor_id)
        zones.append(
            {
                "zone_id": zone_id,
                "sensor_id": sensor_id,
                "ambient_c": _number(item.get("ambient_c"), "ambient_c"),
            }
        )

    raw_tests = payload.get("actuator_tests")
    if not isinstance(raw_tests, list) or len(raw_tests) != len(zones):
        raise Refused("actuator test count must match zones")
    tests: list[dict[str, Any]] = []
    valve_ids: set[str] = set()
    expected_ids = sensor_ids
    for item in raw_tests:
        if not isinstance(item, dict) or set(item) != {
            "actuator_id",
            "step_start_min",
            "step_magnitude",
            "samples",
        }:
            raise Refused("actuator test keys are invalid")
        valve_id = item.get("actuator_id")
        if not isinstance(valve_id, str) or VALVE_ID.fullmatch(valve_id) is None:
            raise Refused("valve id is invalid")
        if valve_id in valve_ids:
            raise Refused("valve ids must be unique")
        valve_ids.add(valve_id)
        start = _number(item.get("step_start_min"), "step_start_min")
        magnitude = _number(
            item.get("step_magnitude"), "step_magnitude", positive=True
        )
        raw_samples = item.get("samples")
        if not isinstance(raw_samples, list) or len(raw_samples) != len(zones):
            raise Refused("samples must cover every sensor")
        samples: dict[str, list[float]] = {}
        for sample in raw_samples:
            if not isinstance(sample, dict) or set(sample) != {
                "sensor_id",
                "temperatures",
            }:
                raise Refused("sample keys are invalid")
            sensor_id = sample.get("sensor_id")
            if not isinstance(sensor_id, str) or sensor_id not in expected_ids:
                raise Refused("sample sensor id is unknown")
            if sensor_id in samples:
                raise Refused("sensor samples must be unique")
            raw_values = sample.get("temperatures")
            if not isinstance(raw_values, list) or len(raw_values) != 121:
                raise Refused("temperature series must contain 121 samples")
            samples[sensor_id] = [
                _number(value, "temperature") for value in raw_values
            ]
        if set(samples) != expected_ids:
            raise Refused("samples do not cover the exact sensor set")
        tests.append(
            {
                "actuator_id": valve_id,
                "step_start_min": start,
                "step_magnitude": magnitude,
                "samples": samples,
            }
        )

    spec = payload.get("controller_spec")
    if not isinstance(spec, dict) or set(spec) != {
        "setpoint_c",
        "overshoot_limit_c",
        "settle_minutes",
        "max_supply_c",
    }:
        raise Refused("controller_spec keys are invalid")
    parsed_spec = {
        "setpoint_c": _number(spec.get("setpoint_c"), "setpoint_c", positive=True),
        "overshoot_limit_c": _number(
            spec.get("overshoot_limit_c"), "overshoot_limit_c", positive=True
        ),
        "settle_minutes": _number(
            spec.get("settle_minutes"), "settle_minutes", positive=True
        ),
        "max_supply_c": _number(
            spec.get("max_supply_c"), "max_supply_c", positive=True
        ),
    }
    if any(zone["ambient_c"] >= parsed_spec["setpoint_c"] for zone in zones):
        raise Refused("all ambient temperatures must be below the setpoint")
    return {
        "sample_seconds": sample_seconds,
        "zones": zones,
        "tests": tests,
        "controller_spec": parsed_spec,
    }


def identify(
    sample_seconds: int,
    zones: list[dict[str, Any]],
    tests: list[dict[str, Any]],
) -> dict[str, Any]:
    zone_by_sensor = {zone["sensor_id"]: zone for zone in zones}
    mapping: dict[str, str] = {}
    plants: dict[str, dict[str, float]] = {}
    dt_minutes = sample_seconds / 60.0
    for test in tests:
        deltas = {
            sensor_id: values[-1] - values[0]
            for sensor_id, values in test["samples"].items()
        }
        ranked = sorted(deltas.items(), key=lambda item: (-item[1], item[0]))
        if len(ranked) > 1 and abs(ranked[0][1] - ranked[1][1]) <= 1e-9:
            return {"candidates": 2, "status": "ambiguous"}
        sensor_id, delta = ranked[0]
        zone = zone_by_sensor[sensor_id]
        values = test["samples"][sensor_id]
        times = [index * dt_minutes for index in range(len(values))]

        def fit(tau: float) -> tuple[float, float, float]:
            template = [
                test["step_magnitude"]
                * (1.0 - math.exp(-max(0.0, time - test["step_start_min"]) / tau))
                for time in times
            ]
            centered = [
                value - zone["ambient_c"] for value in values
            ]
            denominator = math.fsum(value * value for value in template)
            gain = math.fsum(
                left * right for left, right in zip(centered, template)
            ) / denominator
            error = math.fsum(
                (centered[index] - gain * template[index]) ** 2
                for index in range(len(values))
            )
            return error, gain, tau

        best = min(fit(index * 0.1) for index in range(10, 601))
        refined = min(
            fit(best[2] + index * 0.001)
            for index in range(-100, 101)
            if best[2] + index * 0.001 > 0.0
        )
        _, gain, tau = min(best, refined)
        if not math.isfinite(gain) or gain <= 0.0:
            raise Refused("step test did not identify a positive plant gain")
        if not math.isfinite(tau) or tau <= 0.0:
            raise Refused("identified time constant is invalid")
        valve_id = test["actuator_id"]
        mapping[valve_id] = zone["zone_id"]
        plants[valve_id] = {
            "gain_c_per_unit": round(gain, 6),
            "tau_minutes": round(tau, 6),
        }
    return {
        "status": "ok",
        "pairing": dict(sorted(mapping.items())),
        "plants": dict(sorted(plants.items())),
    }


def synthesize(plants: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    gains: dict[str, dict[str, float]] = {}
    for valve_id, plant in plants.items():
        gain = plant["gain_c_per_unit"]
        tau = plant["tau_minutes"]
        kp = 3.0 / gain
        ki = 9.0 / (4.0 * tau * gain)
        gains[valve_id] = {"kp": round(kp, 6), "ki": round(ki, 6)}
    return dict(sorted(gains.items()))


def main() -> int:
    if len(sys.argv) != 3:
        print("REFUSED: expected input and output paths", file=sys.stderr)
        return 2
    try:
        payload = parse_payload(Path(sys.argv[1]))
        identified = identify(
            payload["sample_seconds"], payload["zones"], payload["tests"]
        )
        if identified["status"] == "ambiguous":
            result = identified
        else:
            result = {
                "status": "ok",
                "pairing": identified["pairing"],
                "plants": identified["plants"],
                "controller_gains": synthesize(identified["plants"]),
            }
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    Path(sys.argv[2]).write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
