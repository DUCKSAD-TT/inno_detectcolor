"""Node 2 — Hardware Node.

Subscribes to `servo_cmd` (SUB binds on PORT_SERVO_CMD) so multiple publishers
(teaching_node, main.py) can connect in.

Publishes IDLE / BUSY on `servo_status` (PUB binds on PORT_SERVO_STATUS).

A single sweep worker continuously chases the most recent target so rapid
slider drags don't pile up — the latest command always wins.

Command formats received on servo_cmd:
    "servo_cmd <a1> <a2> <a3> <a4>"   -> sweep to those angles (clamped)
    "servo_cmd home"                  -> sweep to HOME_POSE
    "servo_cmd stop"                  -> emergency: force home, override abort
"""

import threading
import time
import tkinter as tk

import customtkinter as ctk
import zmq

try:
    import pyfirmata2
    _HAS_FIRMATA = True
except Exception:
    _HAS_FIRMATA = False

from utils.zmq_config import (
    PORT_SERVO_CMD, PORT_SERVO_STATUS,
    TOPIC_SERVO_CMD, TOPIC_SERVO_STATUS,
    STATUS_IDLE, STATUS_BUSY,
    CMD_HOME, CMD_STOP,
    HOME_POSE, SERVO_PINS, SERVO_LIMITS,
    SWEEP_STEP_DEG, SWEEP_TICK_SEC,
    ADDR_BIND,
)
from utils.node_log import banner, make_logger, ready

NODE_NAME = "ARDUINO NODE — Servo Driver"
# logger กลางของ node นี้ — พิมพ์ log ลง terminal (cmd / powershell) แบบ real-time
_term = make_logger("arduino")

# ขา LED บนบอร์ด (Arduino Uno มี LED on-board ที่ขา 13) ใช้สำหรับปุ่มทดสอบบอร์ด
# แยกจาก SERVO_PINS [8,9,10,11] จึงไม่กระทบการขับ servo เดิม
LED_PIN          = 13
LED_FLASH_COUNT  = 3      # กระพริบ 3 ครั้ง
LED_FLASH_ON_SEC = 0.08   # ติด/ดับครั้งละ 80ms -> รัวๆ


class HardwareNode:
    def __init__(self, root: ctk.CTk):
        self.root = root
        root.title("Arduino Node — Servo Driver")
        root.geometry("480x690")
        root.resizable(False, False)

        # สถานะ servo ปัจจุบัน (องศา) — เริ่มต้นที่ HOME_POSE
        self.current = []
        self.target  = []
        for a in HOME_POSE:
            self.current.append(float(a))
            self.target.append(float(a))

        # target_lock ป้องกัน 2 threads แก้ target พร้อมกัน (thread-safe)
        self.target_lock = threading.Lock()
        self.last_status = None
        self.connected   = False
        self.board       = None
        self.servos      = []
        self.running     = True
        self.emergency   = False   # set เมื่อรับคำสั่ง STOP
        self.led_pin     = None    # digital output pin 13 (ตั้งค่าใน _connect_arduino)
        self.flashing    = False   # กัน flash ซ้อนกันถ้ากดปุ่มรัวๆ

        # ZMQ
        self.ctx = zmq.Context.instance()
        self.sub = self.ctx.socket(zmq.SUB)
        self.sub.bind(ADDR_BIND.format(port=PORT_SERVO_CMD))
        self.sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_SERVO_CMD)
        self.sub.RCVTIMEO = 100   # timeout 100ms เพื่อให้ loop ออกได้เมื่อ running=False

        self.pub = self.ctx.socket(zmq.PUB)
        self.pub.bind(ADDR_BIND.format(port=PORT_SERVO_STATUS))

        self._build_ui()
        self._connect_arduino()

        threading.Thread(target=self._zmq_loop,   daemon=True).start()
        threading.Thread(target=self._sweep_loop, daemon=True).start()
        # ส่ง heartbeat ครั้งแรกให้ subscriber รู้ว่า node นี้พร้อมแล้ว
        self.root.after(300, lambda: self._publish_status(STATUS_IDLE))

    # ---------------- UI ----------------
    def _build_ui(self):
        top = ctk.CTkFrame(self.root, fg_color="transparent")
        top.pack(fill="x", pady=5, padx=8)

        self.lbl_conn = ctk.CTkLabel(top, text="● Arduino: Disconnected",
                                     font=("Tahoma", 18, "bold"))
        self.lbl_conn.pack(side="left")

        self.lbl_status = ctk.CTkLabel(top, text="STATUS: —",
                                       font=("Tahoma", 18, "bold"))
        self.lbl_status.pack(side="right")

        self.bars     = []
        self.lbls     = []
        self.tgt_lbls = []

        for i in range(4):
            lo, hi = SERVO_LIMITS[i]
            f = ctk.CTkFrame(self.root, corner_radius=6)
            f.pack(fill="x", padx=8, pady=2)

            title_lbl = ctk.CTkLabel(f,
                                     text=f"S{i+1}  pin {SERVO_PINS[i]}  [{lo}–{hi}]",
                                     font=("Tahoma", 16))
            title_lbl.grid(row=0, column=0, columnspan=4, sticky="w", padx=6, pady=(4, 0))

            bar = ctk.CTkProgressBar(f, width=220, height=10, corner_radius=4)
            bar.set(HOME_POSE[i] / 180.0)
            bar.grid(row=1, column=0, columnspan=4, padx=6, pady=2, sticky="ew")

            ctk.CTkLabel(f, text="cur:",
                         font=("Tahoma", 16)).grid(row=2, column=0, sticky="e")
            lbl = ctk.CTkLabel(f, text=f"{HOME_POSE[i]}°", width=40,
                               font=("Consolas", 18, "bold"))
            lbl.grid(row=2, column=1, sticky="w")

            ctk.CTkLabel(f, text="tgt:",
                         font=("Tahoma", 16)).grid(row=2, column=2, sticky="e", padx=(8, 0))
            tlbl = ctk.CTkLabel(f, text=f"{HOME_POSE[i]}°", width=40,
                                font=("Consolas", 18, "bold"))
            tlbl.grid(row=2, column=3, sticky="w", pady=(0, 4))

            # ปุ่ม nudge: กดเพื่อขยับ target ทีละ 1 หรือ 10 องศา
            # ax=i และ d=delta จำเป็นเพื่อให้แต่ละปุ่มจำ axis และ delta ของตัวเอง
            nudge_row = ctk.CTkFrame(f, fg_color="transparent")
            nudge_row.grid(row=3, column=0, columnspan=4, pady=(0, 4))
            for label, delta in [("−10", -10), ("−1", -1), ("+1", 1), ("+10", 10)]:
                ctk.CTkButton(
                    nudge_row, text=label, width=52, height=28,
                    font=("Tahoma", 15, "bold"),
                    command=lambda ax=i, d=delta: self._nudge(ax, d)
                ).pack(side="left", padx=2)

            self.bars.append(bar)
            self.lbls.append(lbl)
            self.tgt_lbls.append(tlbl)

        # ปุ่มทดสอบบอร์ด: สั่ง LED ขา 13 กระพริบรัวๆ 3 ครั้ง
        # ใช้ตรวจว่าบอร์ดยังคุยกับคอมพิวเตอร์ได้ดี โดยไม่ยุ่งกับ servo
        test_row = ctk.CTkFrame(self.root, fg_color="transparent")
        test_row.pack(fill="x", padx=8, pady=(4, 2))
        self.btn_led = ctk.CTkButton(
            test_row, text="💡 LED 13 Flash (Test Board)",
            height=34, font=("Tahoma", 16, "bold"),
            command=self._flash_led)
        self.btn_led.pack(fill="x")

        info = (f"SUB :{PORT_SERVO_CMD} ({TOPIC_SERVO_CMD})   "
                f"PUB :{PORT_SERVO_STATUS} ({TOPIC_SERVO_STATUS})")
        ctk.CTkLabel(self.root, text=info,
                     font=("Consolas", 14)).pack(pady=2)

        self.log_lbl = ctk.CTkLabel(self.root, text="Initialising…",
                                    wraplength=360, justify="left",
                                    font=("Tahoma", 16))
        self.log_lbl.pack(pady=2, padx=8)

    # ---------------- Hardware ----------------
    def _connect_arduino(self):
        if not _HAS_FIRMATA:
            self._log("pyfirmata2 not installed → DRY-RUN mode")
            return
        try:
            port        = pyfirmata2.Arduino.AUTODETECT
            self.board  = pyfirmata2.Arduino(port)
            self.servos = [self.board.get_pin(f'd:{p}:s') for p in SERVO_PINS]
            for i, ang in enumerate(HOME_POSE):
                self.servos[i].write(ang)
            self.connected = True
            self.lbl_conn.configure(text="● Arduino: CONNECTED", text_color="#2ecc71")
            self._log("Arduino connected. Homed.")

            # ตั้งขา LED 13 เป็น digital output (แยก try เพื่อไม่ให้กระทบ servo
            # ถ้าตั้งค่าไม่สำเร็จ — ปุ่ม flash จะตกไปอยู่โหมด DRY-RUN เอง)
            try:
                self.led_pin = self.board.get_pin(f'd:{LED_PIN}:o')
                self.led_pin.write(0)
                self._log(f"LED pin {LED_PIN} ready for board test.")
            except Exception as e:
                self.led_pin = None
                self._log(f"LED pin {LED_PIN} setup failed: {e}", "WARN")
        except Exception as e:
            self.lbl_conn.configure(text="● Arduino: FAILED", text_color="#d63031")
            self._log(f"Arduino error: {e}\nRunning in DRY-RUN mode (no hardware).")

    # ---------------- Nudge ----------------
    def _nudge(self, axis: int, delta: float):
        """ขยับ target ของ servo แกน axis ไปอีก delta องศา
        max(lo, min(hi, ...)) จำกัดค่าไม่ให้เกิน limit: lo <= value <= hi"""
        with self.target_lock:
            lo, hi      = SERVO_LIMITS[axis]
            new_t       = list(self.target)
            new_t[axis] = max(lo, min(hi, new_t[axis] + delta))
        self._set_target(new_t)

    # ---------------- LED flash (board self-test) ----------------
    def _flash_led(self):
        """กดปุ่ม -> สั่ง LED ขา 13 กระพริบรัวๆ 3 ครั้ง ใน background thread
        (ไม่บล็อก GUI และไม่ยุ่งกับ servo / sweep loop)"""
        if self.flashing:
            self._log("LED flash still running — ignored", "WARN")
            return
        threading.Thread(target=self._flash_worker, daemon=True).start()

    def _flash_worker(self):
        self.flashing = True
        try:
            mode = "board" if (self.connected and self.led_pin is not None) else "DRY-RUN"
            self._log(f"LED{LED_PIN} triple-flash ({mode}) ...", "TEST")
            for _ in range(LED_FLASH_COUNT):
                if not self.running:
                    break
                self._led_write(1)
                time.sleep(LED_FLASH_ON_SEC)
                self._led_write(0)
                time.sleep(LED_FLASH_ON_SEC)
            self._led_write(0)   # กันค้างติด
            self._log(f"LED{LED_PIN} flash done ({mode}) — board link OK", "TEST")
        finally:
            self.flashing = False

    def _led_write(self, value: int):
        """เขียนค่าไปขา LED — เงียบไว้ถ้าไม่มีบอร์ด (DRY-RUN)"""
        if self.led_pin is None:
            return
        try:
            self.led_pin.write(value)
        except Exception as e:
            self._log(f"LED write err: {e}", "ERROR")

    # ---------------- Helpers ----------------
    def _log(self, msg: str, level: str = "INFO"):
        self.log_lbl.configure(text=msg)
        _term(msg, level)

    def _publish_status(self, status: str):
        # ไม่ส่ง ZMQ ซ้ำถ้า status ไม่เปลี่ยน เพื่อลด noise บน network
        if status == self.last_status:
            return
        self.last_status = status
        try:
            self.pub.send_string(f"{TOPIC_SERVO_STATUS} {status}")
        except Exception:
            pass
        _term(f"status -> {status}", "STATE")
        color = "#2ecc71" if status == STATUS_IDLE else "#f39c12"
        # ส่งคำสั่ง update UI กลับไปทำบน main thread
        self.root.after(0, lambda: self.lbl_status.configure(
            text=f"STATUS: {status}", text_color=color))

    def _set_target(self, new_target):
        """อัปเดต target ทั้ง 4 แกน — thread-safe"""
        with self.target_lock:
            for i in range(4):
                lo, hi         = SERVO_LIMITS[i]
                self.target[i] = max(lo, min(hi, float(new_target[i])))
        # ส่งคำสั่ง update UI กลับไปทำบน main thread
        self.root.after(0, self._refresh_targets)

    def _refresh_ui(self):
        for i in range(4):
            self.bars[i].set(self.current[i] / 180.0)
            self.lbls[i].configure(text=f"{int(self.current[i])}°")

    def _refresh_targets(self):
        for i in range(4):
            self.tgt_lbls[i].configure(text=f"{int(self.target[i])}°")

    # ---------------- ZMQ loop ----------------
    def _zmq_loop(self):
        while self.running:
            try:
                msg = self.sub.recv_string()
            except zmq.Again:
                continue
            except Exception:
                break
            try:
                parts = msg.split()
                if len(parts) == 2 and parts[1] == CMD_HOME:
                    self.emergency = False
                    self._set_target(list(HOME_POSE))
                    self._log(f"cmd: HOME -> {HOME_POSE}")
                elif len(parts) == 2 and parts[1] == CMD_STOP:
                    self.emergency = True
                    self._set_target(list(HOME_POSE))
                    self._log("cmd: EMERGENCY STOP -> forcing home")
                elif len(parts) == 5:
                    new_t = [float(x) for x in parts[1:5]]
                    self._set_target(new_t)
                    self._log(f"cmd: target -> {new_t}")
                else:
                    self._log(f"ignored: {msg!r}")
            except Exception as e:
                self._log(f"bad cmd '{msg}': {e}")

    # ---------------- Sweep loop ----------------
    def _sweep_loop(self):
        """เคลื่อน servo จาก current ไปหา target ทีละ SWEEP_STEP_DEG องศา
        ทำงานใน background thread ตลอดเวลา"""
        step = SWEEP_STEP_DEG
        tick = SWEEP_TICK_SEC

        while self.running:
            with self.target_lock:
                tgt = list(self.target)

            # ถ้าต่างกันน้อยกว่า 0.5 องศา ถือว่าถึงเป้าแล้ว
            diffs  = [abs(self.current[i] - tgt[i]) for i in range(4)]
            moving = any(d > 0.5 for d in diffs)

            if moving:
                self._publish_status(STATUS_BUSY)
                for i in range(4):
                    diff = tgt[i] - self.current[i]
                    if abs(diff) <= step:
                        self.current[i] = tgt[i]
                    else:
                        self.current[i] += step if diff > 0 else -step

                    # ถ้าไม่มี Arduino จะ skip การส่งคำสั่งจริง (dry-run mode)
                    if self.connected:
                        try:
                            self.servos[i].write(self.current[i])
                        except Exception:
                            pass

                # ส่งคำสั่ง update UI กลับไปทำบน main thread
                self.root.after(0, self._refresh_ui)
                time.sleep(tick)
            else:
                self._publish_status(STATUS_IDLE)
                time.sleep(0.05)   # รอ 50ms ก่อน check ใหม่เมื่อไม่มี movement

    # ---------------- Lifecycle ----------------
    def shutdown(self):
        self.running = False
        try:
            self.pub.close(0)   # 0 = linger: ปิด socket ทันที ไม่รอ message ค้าง
        except Exception:
            pass
        try:
            self.sub.close(0)
        except Exception:
            pass
        # ปิด LED ก่อนปล่อยบอร์ด เพื่อไม่ให้ค้างติด
        self._led_write(0)
        if self.board:
            try:
                self.board.exit()
            except Exception:
                pass


def main():
    banner(NODE_NAME, [
        f"SUB :{PORT_SERVO_CMD} ({TOPIC_SERVO_CMD})  <- คำสั่ง servo",
        f"PUB :{PORT_SERVO_STATUS} ({TOPIC_SERVO_STATUS})  -> IDLE/BUSY",
        f"firmata: {'available' if _HAS_FIRMATA else 'NOT installed -> DRY-RUN'}",
    ])
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    node = HardwareNode(root)
    ready(NODE_NAME)

    def on_close():
        _term("window closed -> shutting down", "STATE")
        node.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
