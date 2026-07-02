"""Central ZMQ + system configuration for The Sorter project.

ZMQ topology (each port has exactly ONE bind):
    PORT 5555  -- servo_cmd      : SUB binds on hardware_node, PUB connect from
                                    teaching_node + main.py
    PORT 5556  -- servo_status   : PUB binds on hardware_node, SUB connect from
                                    main.py  (IDLE / BUSY)
    PORT 5557  -- vision_cmd     : SUB binds on vision_node, PUB connect from
                                    main.py  (capture)
    PORT 5558  -- color_result   : PUB binds on vision_node, SUB connect from
                                    main.py
"""

# --- Ports ---
PORT_SERVO_CMD     = 5555
PORT_SERVO_STATUS  = 5556
PORT_VISION_CMD    = 5557
PORT_VISION_RESULT = 5558

# --- Address templates ---
ADDR_BIND = "tcp://*:{port}"
ADDR_CONN = "tcp://localhost:{port}"

# --- Topics (used as ZMQ subscription prefixes) ---
TOPIC_SERVO_CMD     = "servo_cmd"
TOPIC_SERVO_STATUS  = "servo_status"
TOPIC_VISION_CMD    = "vision_cmd"
TOPIC_VISION_RESULT = "color_result"

# --- Status values ---
STATUS_IDLE = "IDLE"
STATUS_BUSY = "BUSY"

# --- Commands ---
CMD_CAPTURE = "capture"
CMD_STOP    = "stop"
CMD_HOME    = "home"

# --- Robot defaults ---
# ตำแหน่ง home ของแต่ละ servo (องศา): S1, S2, S3, S4
HOME_POSE  = [160, 140, 90, 50]
SERVO_PINS = [8, 9, 10, 11]    # Arduino digital pins

# (min, max) องศาต่อ servo
# S4 มี range พิเศษ (30-75) เพราะเป็น gripper ที่หมุนได้น้อยกว่าข้อต่ออื่น
SERVO_LIMITS = [(0, 180), (0, 180), (0, 180), (40, 100)]

# --- Vision ---
COLORS               = ["red", "blue", "green", "yellow"]
DEFAULT_CAMERA_INDEX = 1
DEFAULT_CONFIDENCE   = 0.75
import json
import os

SLOT_CENTERS_FILE = os.path.join(os.path.dirname(__file__), "../slot_config.json")

def load_slot_centers():
    default_centers = [
        (100, 350),  # Slot 1
        (200, 200),  # Slot 2
        (320, 150),  # Slot 3
        (440, 200),  # Slot 4
        (540, 350),  # Slot 5
    ]
    if os.path.isfile(SLOT_CENTERS_FILE):
        try:
            with open(SLOT_CENTERS_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list) and len(data) == 5:
                    return [tuple(pt) for pt in data]
        except Exception:
            pass
    return default_centers

SLOT_CENTERS = load_slot_centers()

# --- Sweep tuning ---
# servo เคลื่อนทีละ SWEEP_STEP_DEG องศา ทุก SWEEP_TICK_SEC วินาที
SWEEP_STEP_DEG = 5.0
SWEEP_TICK_SEC = 0.01


# --- Data dir ---
DATA_DIR = "./data"

# --- ชื่อไฟล์ที่บันทึกได้ ---
# route หลัก: "1" ถึง "5"
main_routes  = [str(n) for n in range(1, 6)]

# branch ตามสี: "11","12","13","14", "21","22",...,"54"
# รูปแบบ: (route)(color_index)  โดย 1=red, 2=blue, 3=green, 4=yellow
branch_files = [
    f"{route}{color_idx}"
    for route in range(1, 6)
    for color_idx in range(1, 5)
]

RECORD_OPTIONS = main_routes + branch_files
