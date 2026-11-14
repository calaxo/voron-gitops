import asyncio
import json

import pytest

from voron_mcp.config import Config
from voron_mcp.server import build
from voron_mcp.tools.leveling import compute_correction
from voron_mcp.tools.motion import interleaved_force_moves


def home_all(fake):
    fake.status["toolhead"]["homed_axes"] = "xyz"


# ------------------------------------------------------------------ arming
async def test_read_tools_work_disarmed(T, fake):
    st = await T.printer_status()
    assert st["ok"] and st["temperatures"]["radiateur_hotend"] == 30.0
    assert st["fans"]["heater_fan hotend_fan"]["speed"] == 1.0
    assert st["buttons"]["probe_switch"] == "RELEASED"
    assert st["pressure_advance"]["extruder"]["pa"] == 0.04
    assert st["input_shaper_config"]["shaper_freq_x"] == 50.0
    pos = await T.get_position()
    assert pos["position"]["mcu"]["stepper_x"] == 175 * 80
    assert (await T.query_endstops())["endstops"]["z"] == "TRIGGERED"
    assert (await T.query_button(name="probe_switch"))["state"] == "RELEASED"


async def test_act_refused_when_disarmed(T, fake, cfg):
    res = await T.move(x=10)
    assert res["ok"] is False and "DISARMED" in res["error"]
    res = await T.gcode(script="G1 X10")
    assert res["ok"] is False and "DISARMED" in res["error"]
    # read-only raw gcode allowed
    assert (await T.gcode(script="GET_POSITION"))["ok"]
    # emergency stop always allowed (tool and raw M112)
    assert (await T.emergency_stop())["ok"]
    assert (await T.gcode(script="M112"))["emergency_stop"]
    assert fake.estop == 2
    # every non-read call is audited, including refusals
    entries = [json.loads(l) for l in cfg.audit_log.read_text().splitlines()]
    assert [e["tool"] for e in entries][:2] == ["move", "gcode"]
    assert entries[0]["ok"] is False


async def test_arm_ttl_and_disarm(T):
    s = await T.arm(minutes=5, reason="test")
    assert s["armed"] and 4.9 <= s["armed_minutes_left"] <= 5
    assert (await T.disarm())["armed"] is False


async def test_dangerous_needs_confirm(T, fake):
    await T.arm()
    r = await T.gcode(script="SET_KINEMATIC_POSITION Z=10")
    assert r["ok"] is False and "confirm" in r["error"]
    r = await T.gcode(script="SET_KINEMATIC_POSITION Z=10", confirm=True)
    assert r["ok"]
    r = await T.set_kinematic_position(z=5)
    assert r["ok"] is False


# ------------------------------------------------------------------ motion guards
async def test_soft_limits_and_zmax(T, fake):
    await T.arm()
    home_all(fake)
    r = await T.move(x=400)
    assert r["ok"] is False and "soft limits" in r["error"]
    r = await T.move(z=320)
    assert r["ok"] is False and "Z_MAX_SAFE" in r["error"]
    r = await T.move(z=-20, relative=True)
    assert r["ok"] is False
    r = await T.move(x=100, y=100, z=20)
    assert r["ok"] and fake.status["toolhead"]["position"][:3] == [100.0, 100.0, 20.0]
    assert fake.status["gcode_move"]["absolute_coordinates"] is True


async def test_keepout_segment(T, fake):
    await T.arm()
    home_all(fake)
    fake.status["toolhead"]["position"] = [175.0, 175.0, 260.0, 0.0]
    fake.status["gcode_move"]["position"] = [175.0, 175.0, 260.0, 0.0]
    fake.status["gcode_move"]["gcode_position"] = [175.0, 175.0, 260.0, 0.0]
    r = await T.move(y=10)
    assert r["ok"] is False and "keep-out" in r["error"]
    # path crossing the box (start and end outside, both high) is caught too
    r = await T.gcode(script="G1 X175 Y-1 F3000")
    assert r["ok"] is False
    # low Z is fine
    r = await T.gcode(script="G1 Z100 F600\nG1 Y10 F3000")
    assert r["ok"], r


async def test_raw_relative_after_g28_refused(T, fake):
    await T.arm()
    r = await T.gcode(script="G28\nG91\nG1 Z5")
    assert r["ok"] is False and "split the script" in r["error"]


# ------------------------------------------------------------------ homing single-flight
async def test_home_refused_when_busy(T, fake):
    await T.arm()
    fake.status["idle_timeout"]["state"] = "Printing"
    r = await T.home()
    assert r["ok"] is False and "busy" in r["error"]
    assert not any("G28" in s for s in fake.scripts)


async def test_home_single_flight_concurrent(T, fake, app):
    await T.arm()
    gate = asyncio.Event()
    orig = app.client.gcode

    async def slow_gcode(script, timeout=None):
        if "G28" in script:
            await gate.wait()
        return await orig(script, timeout=timeout)

    app.client.gcode = slow_gcode
    first = asyncio.create_task(T.home())
    await asyncio.sleep(0.05)
    second = await T.home(axes="z")
    assert second["ok"] is False and "single-flight" in second["error"]
    gate.set()
    r1 = await first
    assert r1["ok"] and r1["homed_axes"] == "xyz"
    assert sum("G28" in s for s in fake.scripts) == 1


# ------------------------------------------------------------------ Z helpers
def test_interleaved_force_moves():
    lines = interleaved_force_moves(["a", "b"], {"a": 2.5, "b": 1.0}, 1.0, 5, 0)
    assert [l.split()[1] for l in lines] == ["STEPPER=a", "STEPPER=b", "STEPPER=a", "STEPPER=a"]
    assert "DISTANCE=0.5" in lines[-1]


async def test_move_all_z_together(T, fake):
    await T.arm()
    r = await T.move_all_z_together(distance=3)
    assert r["ok"] and r["commands"] == 12
    assert set(fake.z_mcu.values()) == {240}
    r = await T.move_all_z_together(distance=50)
    assert r["ok"] is False


async def test_gantry_correction(T, fake, app):
    await T.arm()
    home_all(fake)
    # simulate contacts: rear corners touch 0.1 mm / 0.2 mm higher in steps
    contacts = {"front_left": 0, "rear_left": 8, "rear_right": 16, "front_right": 0}
    steppers = {"front_left": "stepper_z", "rear_left": "stepper_z1",
                "rear_right": "stepper_z2", "front_right": "stepper_z3"}
    for corner, extra in contacts.items():
        saved = dict(fake.z_mcu)
        fake.z_mcu[steppers[corner]] += extra
        assert (await T.record_contact_point(label=corner))["ok"]
        fake.z_mcu = saved
    corr = await T.compute_gantry_correction()
    assert corr["ok"], corr
    # stepper_z: 40/(200*16*5) = 0.0025 mm/step
    assert corr["mm_per_step"] == pytest.approx(0.0025)
    assert corr["raise_steps"] == {"stepper_z": 0, "stepper_z1": 8, "stepper_z2": 16, "stepper_z3": 0}
    assert corr["raise_mm"]["stepper_z2"] == pytest.approx(0.04)
    before = dict(fake.z_mcu)
    r = await T.apply_gantry_correction(step_mm=0.01)
    assert r["ok"] and set(r["applied"]) == {"stepper_z1", "stepper_z2"}
    # fake has 80 steps/mm: 4 interleaved FORCE_MOVEs of 0.01 mm, each rounded to 1 step
    assert fake.z_mcu["stepper_z2"] - before["stepper_z2"] == 4
    assert (await T.apply_gantry_correction(raise_mm={"stepper_z": -0.1}))["ok"] is False


def test_compute_correction_lever():
    pts = {c: {"mcu": {"z0": v, "z1": v, "z2": v, "z3": v}} for c, v in
           {"fl": 100, "rl": 110, "rr": 120, "fr": 100}.items()}
    corr = compute_correction(pts, {"fl": "z0", "rl": "z1", "rr": "z2", "fr": "z3"}, 1.5, 0.01)
    assert corr["raise_steps"] == {"z0": 0, "z1": 15, "z2": 30, "z3": 0}


# ------------------------------------------------------------------ speed test
async def test_speed_test_detects_loss_and_restores(T, fake):
    await T.arm()
    home_all(fake)
    fake.loss_above_velocity = 350
    r = await T.speed_test(pairs="300:5000, 400:8000, 500:10000", iterations=1)
    assert r["ok"], r
    assert [x["lost_steps"] for x in r["results"]] == [False, True]
    assert r["first_loss"] == {"velocity": 400.0, "accel": 8000.0}
    assert r["last_good"] == {"velocity": 300.0, "accel": 5000.0}
    assert r["results"][1]["difference"]["stepper_x"] == -32
    assert fake.status["toolhead"]["max_velocity"] == 300.0
    assert fake.status["toolhead"]["max_accel"] == 5000.0


async def test_speed_test_respects_keepout(T, fake):
    await T.arm()
    home_all(fake)
    fake.status["toolhead"]["position"][2] = 260.0
    fake.status["gcode_move"]["position"][2] = 260.0
    fake.status["gcode_move"]["gcode_position"][2] = 260.0
    r = await T.speed_test(pairs=["300:5000"], iterations=1)
    assert r["ok"] is False and "keep-out" in r["error"]
    assert fake.status["toolhead"]["max_velocity"] == 300.0


# ------------------------------------------------------------------ reads
async def test_temperature_history(T, fake):
    fake.temp_store = {"extruder": {"temperatures": [200.0] * 100 + [210.0] * 100,
                                    "targets": [210.0] * 200, "powers": [0.5] * 200},
                       "temperature_sensor radiateur_hotend": {"temperatures": [40.0, 42.0, 44.0]}}
    r = await T.temperature_history(window_s=150)
    e = r["sensors"]["extruder"]
    assert e["min"] == 200.0 and e["max"] == 210.0 and e["samples"] == 150
    assert r["sensors"]["temperature_sensor radiateur_hotend"]["mean"] == 42.0


async def test_log_tail_and_console(T, fake):
    fake.log_text = "\n".join(f"line {i}" for i in range(50)) + "\nMCU 'mcu' shutdown: Timer too close\n"
    r = await T.klippy_log_tail(lines=5, contains="shutdown")
    assert r["lines"] == ["MCU 'mcu' shutdown: Timer too close"]
    await T.get_position()
    c = await T.console_history(count=10, contains="mcu:")
    assert c["entries"] and c["entries"][-1]["type"] == "response"


async def test_dump_tmc_tool(T, fake):
    fake.responders["DUMP_TMC"] = lambda line: ["// DRV_STATUS: 80000000 s2gb=1 stst=1"]
    r = await T.dump_tmc(stepper="stepper_x")
    assert r["errors"] == ["DRV_STATUS.s2gb"]


# ------------------------------------------------------------------ thermal / watchdog
async def test_heater_limits(T, fake):
    assert (await T.set_heater(heater="extruder", target=200))["ok"] is False  # disarmed
    assert (await T.set_heater(heater="extruder", target=0))["ok"]  # off always ok
    await T.arm()
    assert (await T.set_heater(heater="heater_bed", target=150))["ok"] is False
    assert (await T.set_heater(heater="extruder", target=240))["ok"]


async def test_pid_calibrate(T, fake):
    await T.arm()
    fake.responders["PID_CALIBRATE"] = lambda line: ["PID parameters: pid_Kp=20.1 pid_Ki=1.2 pid_Kd=80.5"]
    r = await T.pid_calibrate(heater="extruder", target=230)
    assert r["pid"] == {"Kp": 20.1, "Ki": 1.2, "Kd": 80.5}


async def test_watchdog_pauses_print(T, fake):
    fake.status["print_stats"]["state"] = "printing"
    r = await T.watchdog_start(interval_s=0.01)
    assert r["started"]
    fake.status["temperature_sensor radiateur_hotend"]["temperature"] = 61.0
    for _ in range(100):
        await asyncio.sleep(0.01)
        if fake.actions:
            break
    assert fake.actions == [("pause", None)]
    assert any("HEATER=extruder TARGET=0" in s for s in fake.scripts)


# ------------------------------------------------------------------ streaming
async def test_stream_gcode_layers(T, fake, cfg):
    await T.arm()
    home_all(fake)
    cfg.gcode_dir.mkdir(parents=True, exist_ok=True)
    body = "".join(f";LAYER_CHANGE\nG1 X{100 + i} Y100 F3000 ; move\nG1 Z{0.2 * (i + 1):.1f} F600\n" for i in range(5))
    (cfg.gcode_dir / "t.gcode").write_text("; header\nG90\n" + body)
    r = await T.stream_gcode(local_path="t.gcode", chunk_lines=3, background=False)
    assert r["ok"] and r.get("complete"), r
    assert r["layers_total"] == 5 and r["layer"] == 5 and r["lines_sent"] == 11


async def test_stream_refuses_keepout_and_trips_radiator(T, fake, cfg):
    await T.arm()
    home_all(fake)
    cfg.gcode_dir.mkdir(parents=True, exist_ok=True)
    (cfg.gcode_dir / "bad.gcode").write_text("G90\nG1 Z260 F600\nG1 Y5 F3000\n")
    r = await T.stream_gcode(local_path="bad.gcode", chunk_lines=10, background=False)
    assert "keep-out" in (r["error"] or "")
    (cfg.gcode_dir / "hot.gcode").write_text("G90\n" + "G1 X100 F3000\n" * 10)
    fake.status["temperature_sensor radiateur_hotend"]["temperature"] = 70.0
    r = await T.stream_gcode(local_path="hot.gcode", chunk_lines=2, background=False)
    assert r["tripped"]["trip"] and r["lines_sent"] == 0


async def test_stream_stop(T, fake, cfg, app):
    await T.arm()
    orig = app.client.gcode

    async def slow_gcode(script, timeout=None):
        await asyncio.sleep(0.005)
        return await orig(script, timeout=timeout)

    app.client.gcode = slow_gcode
    home_all(fake)
    cfg.gcode_dir.mkdir(parents=True, exist_ok=True)
    (cfg.gcode_dir / "long.gcode").write_text("G90\n" + "G1 X100 F3000\nG1 X120 F3000\n" * 200)
    r = await T.stream_gcode(local_path="long.gcode", chunk_lines=1, background=True)
    assert r["started"]
    await asyncio.sleep(0.02)
    assert (await T.stream_stop())["stop_requested"]
    for _ in range(100):
        st = await T.stream_status()
        if not st["running"]:
            break
        await asyncio.sleep(0.01)
    assert st.get("stopped") and st["lines_sent"] < 401


async def test_generate_pa_save_and_upload(T, fake, cfg):
    r = await T.generate_pa_test(output="upload")
    assert r["ok"] and r["upload"]["ok"] and fake.uploads
    assert (cfg.gcode_dir / r["filename"]).exists()


# ------------------------------------------------------------------ dock
async def test_dock_step_by_step(T, fake):
    await T.arm()
    home_all(fake)
    args = dict(x=50, approach_y=60, dock_y=5, dock_z=280, release_z=290)
    plan = await T.tool_dock(**args, dry_run=True)
    assert len(plan["plan"]) == 4
    r = await T.tool_dock(**args, step_by_step=True)
    assert r["step"] == 1 and "next" in r, r
    assert fake.status["toolhead"]["position"][:3] == [50.0, 60.0, 280.0]
    r = await T.sequence_next()
    assert r["ok"] and fake.status["toolhead"]["position"][1] == 5.0  # inside keep-out: allowed
    await T.sequence_next()
    r = await T.sequence_next()
    assert r["complete"] and fake.status["toolhead"]["position"][:3] == [50.0, 60.0, 290.0]
    # plain move into the dock area stays forbidden
    assert (await T.move(y=10))["ok"] is False


# ------------------------------------------------------------------ server wiring
async def test_fastmcp_registration(tmp_path):
    mcp, app = build(Config(data_dir=tmp_path))
    names = {t.name for t in await mcp.list_tools()}
    for n in ("printer_status", "gcode", "home", "move", "emergency_stop", "speed_test",
              "tool_dock", "stream_gcode", "arm", "generate_pa_test", "compute_gantry_correction"):
        assert n in names
    tool = next(t for t in await mcp.list_tools() if t.name == "move")
    assert "speed_mm_s" in tool.inputSchema["properties"]
    await app.client.aclose()
