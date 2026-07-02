"""Launch all nodes at once.

Order matters — we start the BIND-side nodes first so the SUB/PUB sockets
they own are alive before the connect-side nodes try to publish.

Usage:
    python launch_all.py            # auto-activates ./venv if not already inside it
"""

import os
import subprocess
import sys
import time

# หาที่อยู่ของไฟล์นี้ เพื่อใช้เป็น project root
this_file = os.path.abspath(__file__)
ROOT      = os.path.dirname(this_file)
VENV_PY   = os.path.join(ROOT, "venv", "Scripts", "python.exe")


def ensure_venv():
    """ถ้ามี venv อยู่ในโปรเจกต์ และยังไม่ได้ activate อยู่
    ให้ re-launch script นี้ด้วย python ของ venv แทน"""
    if not os.path.isfile(VENV_PY):
        print("  ⚠  No venv found at ./venv — using system Python.")
        return

    # normcase ทำให้ตัวอักษรพิมพ์ใหญ่/เล็กไม่มีผลบน Windows (C:\ vs c:\)
    current_py = os.path.normcase(sys.executable)
    venv_py    = os.path.normcase(VENV_PY)

    if current_py == venv_py:
        print(f"  ✓  venv active: {VENV_PY}")
        return

    print(f"  ↻  venv not active — re-launching under: {VENV_PY}")
    cmd    = [VENV_PY] + sys.argv   # รัน script เดิม แต่ใช้ python ของ venv
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


ensure_venv()

# เลือก python interpreter ที่จะใช้ launch node
if os.path.isfile(VENV_PY):
    PY = VENV_PY
else:
    PY = sys.executable

# รายชื่อ node ที่จะเปิด (ชื่อแสดงผล, ชื่อไฟล์)
# Hardware + Vision ต้อง bind ก่อน จึงต้องเปิดเป็นลำดับแรก
NODES = [
    ("Arduino Node",    "arduino_node.py"),
    ("Camera Node",     "camera_node.py"),
    ("Scale Node",      "scale_node.py"),
    ("Main Decision",   "main_decision_node.py"),
    ("CSV Pose Tester", "csv_pose_tester.py"),
]


def launch_one(name, script):
    path = os.path.join(ROOT, script)
    if not os.path.isfile(path):
        print(f"  ✗ missing {script}")
        return None

    creationflags = 0   # 0 = ไม่มี flag พิเศษ (ใช้บน Linux/Mac)
    if os.name == "nt":
        # แต่ละ node ได้ console ของตัวเอง เพื่อให้ log ไม่ปนกัน
        creationflags = subprocess.CREATE_NEW_CONSOLE

    print(f"  ▶ launching {name}: {script}")
    return subprocess.Popen([PY, path], cwd=ROOT, creationflags=creationflags)


def main():
    print(f"Using Python: {PY}")
    print(f"Project root: {ROOT}\n")

    procs = []
    for name, script in NODES:
        p = launch_one(name, script)
        if p:
            procs.append((name, p))
        # หน่วงเวลา 1 วินาที เพื่อให้ socket bind ตัวก่อนหน้าพร้อมก่อนที่ตัวถัดไปจะ connect
        time.sleep(1.0)

    print("\nAll nodes launched.  Press Ctrl+C in this window to terminate them all,")
    print("or simply close each node window individually.\n")

    try:
        for name, p in procs:
            p.wait()
    except KeyboardInterrupt:
        print("\nTerminating all nodes…")
        for name, p in procs:
            try:
                p.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    main()
