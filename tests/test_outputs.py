from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
from pathlib import Path


ENTRY = os.environ.get("HVAC_IDENTIFIER", "/app/identify_hvac.py")

SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
}


def _run(payload: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-S", "-P", "-B", ENTRY, str(payload), str(output)],
        text=True,
        capture_output=True,
        env=SAFE_ENV,
        timeout=30,
        check=False,
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _plant(index: int) -> tuple[float, float]:
    return 1.55 + 0.13 * index, 11.0 + 1.15 * index


def _payload(
    zone_count: int = 10,
    sample_seconds: int = 30,
    step_start: float = 10.0,
) -> tuple[dict[str, object], dict[str, str], dict[str, dict[str, float]]]:
    zones = [
        {
            "zone_id": f"ZONE-{index:02d}Q",
            "sensor_id": f"SENS-{index:02d}R",
            "ambient_c": 19.5 + 0.09 * index,
        }
        for index in range(zone_count)
    ]
    order = [(index * 3 + 1) % zone_count for index in range(zone_count)]
    dt_minutes = sample_seconds / 60.0
    magnitude = 2.0 + 0.15 * (zone_count - 8)
    tests = []
    expected_pairing: dict[str, str] = {}
    expected_plants: dict[str, dict[str, float]] = {}
    for test_index, zone_index in enumerate(order):
        gain, tau = _plant(zone_index)
        ambient = zones[zone_index]["ambient_c"]
        primary_values = []
        for sample_index in range(121):
            time = sample_index * dt_minutes
            elapsed = max(0.0, time - step_start)
            primary_values.append(
                ambient + gain * magnitude * (1.0 - math.exp(-elapsed / tau))
            )
        samples = [
            {
                "sensor_id": zone["sensor_id"],
                "temperatures": primary_values
                if zone["sensor_id"] == zones[zone_index]["sensor_id"]
                else [zone["ambient_c"]] * 121,
            }
            for zone in zones
        ]
        valve_id = f"VALVE-{test_index:02d}V"
        tests.append(
            {
                "actuator_id": valve_id,
                "step_start_min": step_start,
                "step_magnitude": magnitude,
                "samples": samples,
            }
        )
        expected_pairing[valve_id] = zones[zone_index]["zone_id"]
        expected_plants[valve_id] = {
            "gain_c_per_unit": round(gain, 6),
            "tau_minutes": round(tau, 6),
        }
    payload = {
        "schema": "hvac-id-v1",
        "sample_seconds": sample_seconds,
        "zones": zones,
        "actuator_tests": tests,
        "controller_spec": {
            "setpoint_c": 24.0,
            "overshoot_limit_c": 0.3,
            "settle_minutes": 95.0,
            "max_supply_c": 8.0,
        },
    }
    return payload, dict(sorted(expected_pairing.items())), dict(sorted(expected_plants.items()))


def _closed_loop(
    ambient: float,
    plant: dict[str, float],
    gains: dict[str, float],
    spec: dict[str, float],
    sample_seconds: int,
) -> tuple[float, float]:
    dt = sample_seconds / 60.0
    temperature = ambient
    integral = 0.0
    peak = temperature
    last_outside = 0.0
    for step_index in range(int(160.0 / dt)):
        error = spec["setpoint_c"] - temperature
        integral += error * dt
        control = gains["kp"] * error + gains["ki"] * integral
        control = min(spec["max_supply_c"], max(0.0, control))
        derivative = (
            plant["gain_c_per_unit"] * control - (temperature - ambient)
        ) / plant["tau_minutes"]
        temperature += dt * derivative
        peak = max(peak, temperature)
        if abs(temperature - spec["setpoint_c"]) > 0.05:
            last_outside = (step_index + 1) * dt
    return peak, last_outside


def _metrics(
    payload: dict[str, object],
    result: dict[str, object],
) -> list[tuple[float, float]]:
    zones = {item["zone_id"]: item for item in payload["zones"]}
    values = []
    for valve_id, zone_id in result["pairing"].items():
        ambient = zones[zone_id]["ambient_c"]
        values.append(
            _closed_loop(
                ambient,
                result["plants"][valve_id],
                result["controller_gains"][valve_id],
                payload["controller_spec"],
                payload["sample_seconds"],
            )
        )
    return values


def _duplicated_response_payload() -> dict[str, object]:
    payload, _, _ = _payload(8)
    payload["zones"][1]["ambient_c"] = payload["zones"][0]["ambient_c"]
    duplicate = copy.deepcopy(payload["actuator_tests"][0])
    duplicate["actuator_id"] = "VALVE-01V"
    source_values = copy.deepcopy(duplicate["samples"][0]["temperatures"])
    for sample in duplicate["samples"]:
        if sample["sensor_id"] == payload["zones"][1]["sensor_id"]:
            sample["temperatures"] = source_values
    payload["actuator_tests"][1] = duplicate
    return payload


def _shift_sensor_baseline(
    payload: dict[str, object],
    sensor_id: str,
    delta_c: float,
) -> None:
    for test in payload["actuator_tests"]:
        for sample in test["samples"]:
            sample["temperatures"] = [
                value + delta_c if sample["sensor_id"] == sensor_id else value
                for value in sample["temperatures"]
            ]


def test_eight_valves_are_paired_to_hidden_zones(tmp_path: Path) -> None:
    """题面：
    「阶跃试验，只记录各传感器温度序列。现在要恢复阀区对应关系、估计一阶热参数，并给出」
    「能按期稳定到设定值的 PI 参数；只做一个平均增益会让相邻热区在夜间互相拖拽。」
    「对应关系与模型参数同时唯一时，结果写入 `status`、`pairing`、`plants`、`controller_gains`。`status` 为」
    """

    payload, expected, _ = _payload(8)
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert result["status"] == "ok"
    assert result["pairing"] == expected, "CASE-ZONES-01 CASE-AC-02 CASE-PAIR-01 CASE-OUT-02"


def test_ten_valve_pairing_preserves_unique_channels(tmp_path: Path) -> None:
    """题面：
    「`"ok"`；三个映射都以全部阀号为键。`pairing` 的值是热区号，`plants` 只含」
    """

    payload, expected, _ = _payload(10)
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert completed.returncode == 0
    assert result["pairing"] == expected
    assert len(set(result["pairing"].values())) == 10, "CASE-ZONES-02 CASE-ACT-01 CASE-OUT-03 CASE-SAFE-01 CASE-SAFE-02"


def test_step_response_fit_recovers_gain_and_time_constant(tmp_path: Path) -> None:
    """题面：
    「单热区模型为 `dT/dt = (gain_c_per_unit * u - (T - ambient_c)) / tau_minutes`。」
    """

    payload, _, expected = _payload(8)
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    observed = result["plants"]
    assert completed.returncode == 0
    assert abs(observed["VALVE-00V"]["gain_c_per_unit"] - expected["VALVE-00V"]["gain_c_per_unit"]) < 1e-5
    assert abs(observed["VALVE-06V"]["tau_minutes"] - expected["VALVE-06V"]["tau_minutes"]) < 1e-5, "CASE-REC-01 CASE-IDS-02 CASE-ACT-02 CASE-SAMPLE-02 CASE-OUT-04"


def test_zero_step_start_is_accepted(tmp_path: Path) -> None:
    """题面：
    「阶跃开始时间必须有限且不小于 0，其中 0 表示试验开始时立即施加阶跃，负值必须拒收」
    """

    # CASE-VALVE-04
    payload, expected, _ = _payload(8, step_start=0.0)
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert payload["actuator_tests"][0]["step_start_min"] == 0.0
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert result["pairing"] == expected


def test_negative_step_start_is_refused(tmp_path: Path) -> None:
    """题面：
    「阶跃开始时间必须有限且不小于 0，其中 0 表示试验开始时立即施加阶跃，负值必须拒收」
    """

    # CASE-VALVE-05
    payload, _, _ = _payload(8)
    payload["actuator_tests"][0]["step_start_min"] = -0.01
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.startswith("REFUSED: ")
    assert not output.exists()


def test_sixty_second_sampling_boundary_is_accepted(tmp_path: Path) -> None:
    """题面：
    「`sample_seconds` 是 10 到 60 的整数。」
    """

    payload, expected, _ = _payload(8, 60)
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert payload["sample_seconds"] == 60
    assert completed.returncode == 0
    assert result["pairing"] == expected, "CASE-SCHEMA-01 CASE-VALVE-02 CASE-SAMPLE-01 CASE-SPEC-01"


def test_identical_valve_responses_report_ambiguity(tmp_path: Path) -> None:
    """题面：
    「若两个阀的响应在数值上完全相同，阀区对应关系不唯一。」
    """

    payload = _duplicated_response_payload()
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert completed.returncode == 0
    assert set(result) == {"status", "candidates"}
    assert result["status"] == "ambiguous"
    assert result["candidates"] >= 2, "CASE-IDS-01 CASE-VALVE-01 CASE-AMBIG-01 CASE-AMBIG-02"


def test_sixty_one_second_sampling_is_refused(tmp_path: Path) -> None:
    """题面：
    「`sample_seconds` 是 10 到 60 的整数。」
    """

    payload, _, _ = _payload(8)
    payload["sample_seconds"] = 61
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.startswith("REFUSED: ")
    assert not output.exists(), "CASE-LIMIT-01"


def test_ambient_at_setpoint_is_refused(tmp_path: Path) -> None:
    """题面：
    「四个值均为正有限数，其中环境温度低于设定值。」
    """

    # CASE-AC-03
    payload, _, _ = _payload(8)
    zone = payload["zones"][0]
    delta_c = payload["controller_spec"]["setpoint_c"] - zone["ambient_c"]
    zone["ambient_c"] = payload["controller_spec"]["setpoint_c"]
    _shift_sensor_baseline(payload, zone["sensor_id"], delta_c)
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    report = tmp_path / "result-report.json"
    summary = tmp_path / "result-summary.json"
    _write_json(source, payload)
    completed = _run(source, output)
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.startswith("REFUSED: ")
    assert not output.exists()
    assert not report.exists()
    assert not summary.exists()


def test_zero_step_magnitude_is_refused(tmp_path: Path) -> None:
    """题面：
    「阶跃开始时间必须有限且不小于 0，其中 0 表示试验开始时立即施加阶跃，负值必须拒收；阶跃幅值为」
    """

    # CASE-VALVE-03
    payload, _, _ = _payload(8)
    payload["actuator_tests"][0]["step_magnitude"] = 0.0
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    report = tmp_path / "result-report.json"
    journal = tmp_path / "result-journal.jsonl"
    _write_json(source, payload)
    completed = _run(source, output)
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.startswith("REFUSED: ")
    assert not output.exists()
    assert not report.exists()
    assert not journal.exists()


def test_extra_top_level_key_is_refused(tmp_path: Path) -> None:
    """题面：
    「输入顶层只能是 `schema`、`sample_seconds`、`zones`、`actuator_tests`、」
    """

    # CASE-AC-01 HVAC-FAIL-03 HVAC-FAIL-04
    payload, _, _ = _payload(8)
    payload["planner"] = "weekend"
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    assert completed.returncode == 2
    assert completed.stderr.startswith("REFUSED: ")
    assert not output.exists(), "CASE-AC-01"


def test_success_output_is_compact_sorted_and_newline_terminated(tmp_path: Path) -> None:
    """题面：
    「磁盘上的结果去掉空白，键字母升序，行尾补换行。」
    """

    payload, _, _ = _payload(8)
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    raw = output.read_bytes()
    parsed = json.loads(raw)
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    assert completed.returncode == 0
    assert raw == canonical, "CASE-OUT-01 CASE-SERIAL-01"


def test_pi_gains_settle_each_zone_before_deadline(tmp_path: Path) -> None:
    """题面：
    「并须在 `settle_minutes` 内进入设定值上下」
    """

    payload, _, _ = _payload(10)
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    metrics = _metrics(payload, result)
    assert completed.returncode == 0
    assert all(settled <= payload["controller_spec"]["settle_minutes"] for _, settled in metrics), "CASE-CONTROL-01 CASE-CONTROL-02 CASE-SPEC-02 CASE-SPEC-03 CASE-SAT-01 CASE-OVERSHOOT-02 CASE-GAIN-01"


def test_gate_controllers_stay_below_overshoot_envelope(tmp_path: Path) -> None:
    """题面：
    「整定后温度不得超过」
    """

    payload, _, _ = _payload(10)
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    metrics = _metrics(payload, result)
    ceiling = payload["controller_spec"]["setpoint_c"] + payload["controller_spec"]["overshoot_limit_c"]
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert all(peak <= ceiling for peak, _ in metrics), "CASE-OVERSHOOT-01"
