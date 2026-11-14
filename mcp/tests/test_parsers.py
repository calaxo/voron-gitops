from voron_mcp.gcode_gen import flow_squares_test, pa_line_test
from voron_mcp.parsers import (
    parse_accelerometer, parse_dump_tmc, parse_get_position, parse_pid, parse_shaper,
)
from voron_mcp.safety import axis_params, classify, command_of


def test_get_position():
    lines = ["// mcu: stepper_x:2752 stepper_y:-1568 stepper_z:12000 stepper_z1:12004",
             "// stepper: stepper_x:42.500000 stepper_y:-12.250000",
             "// toolhead: X:175.000000 Y:175.000000 Z:10.000000 E:0.000000",
             "// gcode homing: X:0.000000 Y:0.000000 Z:-0.012000"]
    p = parse_get_position(lines)
    assert p["mcu"]["stepper_z1"] == 12004
    assert p["toolhead"]["X"] == 175.0
    assert p["gcode homing"]["Z"] == -0.012


def test_dump_tmc_flags_faults():
    lines = ["========== Write-only registers ==========",
             "// CHOPCONF: 34030053 toff=3 hstrt=5 mres=3(32usteps)",
             "========== Queried registers ==========",
             "// GSTAT: 00000001 reset=1(Reset)",
             "// DRV_STATUS: c0140000 ot=1 s2ga=0 stst=1 cs_actual=20",
             "// IFCNT: 00000012 ifcnt=18"]
    p = parse_dump_tmc(lines)
    assert p["registers"]["CHOPCONF"]["fields"]["mres"] == 3
    assert "DRV_STATUS.ot" in p["errors"]
    assert "GSTAT.reset" in p["warnings"]
    assert p["registers"]["DRV_STATUS"]["section"] == "queried"


def test_pid_and_accel():
    assert parse_pid(["PID parameters: pid_Kp=22.865 pid_Ki=1.306 pid_Kd=100.083",
                      "The SAVE_CONFIG command will update"]) == {"Kp": 22.865, "Ki": 1.306, "Kd": 100.083}
    s = parse_accelerometer(["// accelerometer values (x, y, z): 470.719200, 941.438400, 9728.196800"])
    assert s[0]["z"] == 9728.1968 and 9700 < s[0]["norm"] < 9800


def test_shaper():
    lines = [
        "Calculating the best input shaper parameters for x axis",
        "Fitted shaper 'zv' frequency = 59.4 Hz (vibrations = 12.3%, smoothing ~= 0.052)",
        "To avoid too much smoothing with 'zv', suggested max_accel <= 13000 mm/sec^2",
        "Fitted shaper 'mzv' frequency = 53.8 Hz (vibrations = 1.6%, smoothing ~= 0.071)",
        "To avoid too much smoothing with 'mzv', suggested max_accel <= 9900 mm/sec^2",
        "Recommended shaper_type_x = mzv, shaper_freq_x = 53.8 Hz",
        "Shaper calibration data written to /tmp/calibration_data_x_20260101.csv file",
    ]
    p = parse_shaper(lines)
    assert p["axes"]["x"]["recommended"] == {"type": "mzv", "freq": 53.8, "max_accel": 9900.0}
    assert p["axes"]["x"]["fitted"]["zv"]["vibrations_pct"] == 12.3
    assert p["files_on_host"] == ["/tmp/calibration_data_x_20260101.csv"]


def test_classify():
    assert command_of("g01 x10") == "G1"
    assert command_of("G1X10Y5") == "G1"
    assert axis_params("G1X10Y5 F600") == {"X": 10.0, "Y": 5.0, "F": 600.0}
    info = classify("GET_POSITION\nM114 ; comment", ["G28"])
    assert info.readonly and not info.dangerous
    info = classify("G28\nFORCE_MOVE STEPPER=stepper_z DISTANCE=1 VELOCITY=5", ["G28"])
    assert info.dangerous == ["FORCE_MOVE"] and info.single_flight == ["G28"]
    assert classify("M112", []).emergency


def test_pa_generator():
    g = pa_line_test(175, 175)
    pas = [l for l in g.splitlines() if l.startswith("SET_PRESSURE_ADVANCE ADVANCE=") and "restore" not in l]
    assert len(pas) == 15
    assert pas[0].endswith("=0") and pas[-1].endswith("=0.07")
    assert "; PA=0.035" in g
    # extrusion never NaN/negative on print moves
    assert "nan" not in g.lower()


def test_flow_generator():
    g = flow_squares_test(175, 175, flows=[0.95, 1.0, 1.05], layers=2)
    assert g.count(";LAYER_CHANGE") == 2
    assert "M221 S95" in g and "M221 S105" in g and g.rstrip().endswith("M400")
