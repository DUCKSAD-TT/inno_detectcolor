"""Node 5 — Main Decision (Brain).

Integrates the CSV Player as a service thread.  State machine:

    IDLE
      |- (button N pressed) -->
    PLAYING_MAIN  (publishes each pose in record-N.csv with tunable delay)
      -->
    WAITING_IDLE  (waits BUSY -> IDLE on servo_status)
      -->
    CAPTURING     (publishes vision_cmd capture)
      -->
    WAITING_COLOR (waits color_result, with timeout)
      -->
    PLAYING_BRANCH (record-N{1|2|3|4}.csv based on color)
      -->
    WAITING_IDLE
      -->
    DONE -> IDLE

ALL RUN button:
    Loops button 1 -> 5 in order. When a button cannot determine a color
    (vision timeout or unknown class), it is SKIPPED and the loop continues with
    the next button.

Emergency STOP -> publishes "servo_cmd stop" (hardware overrides any sweep
and forces home), aborts any running sequence (single or ALL RUN).
"""

import csv
import os
import threading
import time
from tkinter import messagebox

import customtkinter as ctk
import zmq

from utils.zmq_config import (
    PORT_SERVO_CMD, PORT_SERVO_STATUS,
    PORT_VISION_CMD, PORT_VISION_RESULT,
    TOPIC_SERVO_CMD, TOPIC_SERVO_STATUS,
    TOPIC_VISION_CMD, TOPIC_VISION_RESULT,
    STATUS_IDLE, STATUS_BUSY,
    CMD_CAPTURE, CMD_STOP, CMD_HOME,
    COLORS, DATA_DIR, ADDR_CONN,
)
from utils.node_log import banner, make_logger, ready

NODE_NAME = "MAIN DECISION — The Sorter (Brain)"
# logger กลางของ node นี้ — พิมพ์ log ลง terminal (cmd / powershell) แบบ real-time
_term = make_logger("brain")


# ผลลัพธ์ที่เป็นไปได้จาก _run_one()
RES_DONE    = "DONE"
RES_SKIPPED = "SKIPPED"
RES_ABORTED = "ABORTED"
RES_ERROR   = "ERROR"


# ตาราง branching: button N -> ไฟล์ main, แล้ว color -> ไฟล์ branch
# key = หมายเลขปุ่ม (1-5), value = dict ของชื่อไฟล์ CSV ตาม route และสี
BRANCH = {
    1: {"main": "record-1.csv", "red": "record-11.csv", "blue": "record-12.csv", "green": "record-13.csv", "yellow": "record-14.csv"},
    2: {"main": "record-2.csv", "red": "record-21.csv", "blue": "record-22.csv", "green": "record-23.csv", "yellow": "record-24.csv"},
    3: {"main": "record-3.csv", "red": "record-31.csv", "blue": "record-32.csv", "green": "record-33.csv", "yellow": "record-34.csv"},
    4: {"main": "record-4.csv", "red": "record-41.csv", "blue": "record-42.csv", "green": "record-43.csv", "yellow": "record-44.csv"},
    5: {"main": "record-5.csv", "red": "record-51.csv", "blue": "record-52.csv", "green": "record-53.csv", "yellow": "record-54.csv"},
}


class Brain:
    def __init__(self, root: ctk.CTk):
        self.root = root
        root.title("Main Decision — The Sorter")
        root.geometry("780x820")

        # ZMQ ----------------------------------------------------------
        self.ctx = zmq.Context.instance()

        self.cmd_pub = self.ctx.socket(zmq.PUB)
        self.cmd_pub.connect(ADDR_CONN.format(port=PORT_SERVO_CMD))

        self.status_sub = self.ctx.socket(zmq.SUB)
        self.status_sub.connect(ADDR_CONN.format(port=PORT_SERVO_STATUS))
        self.status_sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_SERVO_STATUS)
        self.status_sub.RCVTIMEO = 100

        self.vision_pub = self.ctx.socket(zmq.PUB)
        self.vision_pub.connect(ADDR_CONN.format(port=PORT_VISION_CMD))

        self.vision_sub = self.ctx.socket(zmq.SUB)
        self.vision_sub.connect(ADDR_CONN.format(port=PORT_VISION_RESULT))
        self.vision_sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_VISION_RESULT)
        self.vision_sub.RCVTIMEO = 100

        # State --------------------------------------------------------
        self.servo_status   = STATUS_IDLE
        self.last_color     = None
        self.detected_slots = []
        self.color_event    = threading.Event()
        self.running      = True
        self.busy         = False
        # abort_flag ใช้ interrupt sequence กลางคัน เช่น กด STOP หรือ ALL RUN ถูก abort
        self.abort_flag   = threading.Event()

        self._build_ui()

        threading.Thread(target=self._status_loop, daemon=True).start()
        threading.Thread(target=self._vision_loop,  daemon=True).start()

        self.root.after(400, lambda: self._log("Brain online. Waiting for commands."))

    # ===================================================================
    # UI helpers
    # ===================================================================
    def _set_state(self, s: str):
        """อัปเดต state label — thread-safe (ส่งคำสั่งไป main thread)"""
        self.root.after(0, lambda: self.lbl_state.configure(text=s))

    def _set_servo_text(self, text: str):
        """อัปเดต servo status label — thread-safe"""
        self.root.after(0, lambda: self.lbl_servo.configure(text=text))

    def _set_color_text(self, text: str):
        """อัปเดต color label — thread-safe"""
        self.root.after(0, lambda: self.lbl_color.configure(text=text))

    # ===================================================================
    # UI
    # ===================================================================
    def _build_ui(self):
        ctk.CTkLabel(self.root, text="THE SORTER  —  Main Decision",
                     font=("Tahoma", 22, "bold")).pack(pady=4)

        # Tunables ------------------------------------------------------
        ctrl = ctk.CTkFrame(self.root, corner_radius=6)
        ctrl.pack(fill="x", padx=12)

        ctk.CTkLabel(ctrl, text="Timing", font=("Tahoma", 16, "bold")).grid(
            row=0, column=0, columnspan=6, sticky="w", padx=6, pady=(4, 0))

        ctk.CTkLabel(ctrl, text="Pose delay (s):",
                     font=("Tahoma", 16)).grid(row=1, column=0, padx=2, sticky="e")
        self.delay_var = ctk.DoubleVar(value=0.6)
        ctk.CTkEntry(ctrl, textvariable=self.delay_var, width=50,
                     font=("Tahoma", 16)).grid(row=1, column=1)

        ctk.CTkLabel(ctrl, text="IDLE timeout (s):",
                     font=("Tahoma", 16)).grid(row=1, column=2, padx=4, sticky="e")
        self.idle_to = ctk.DoubleVar(value=20.0)
        ctk.CTkEntry(ctrl, textvariable=self.idle_to, width=50,
                     font=("Tahoma", 16)).grid(row=1, column=3)

        ctk.CTkLabel(ctrl, text="Vision timeout (s):",
                     font=("Tahoma", 16)).grid(row=1, column=4, padx=4, sticky="e")
        self.vis_to = ctk.DoubleVar(value=1.5)
        ctk.CTkEntry(ctrl, textvariable=self.vis_to, width=50,
                     font=("Tahoma", 16)).grid(row=1, column=5, pady=(0, 6))

        # 5 main buttons
        btn_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        btn_frame.pack(pady=6)
        self.buttons = []
        for n in range(1, 6):
            # n=n จำเป็นเพื่อให้แต่ละปุ่มจำหมายเลข route ของตัวเอง
            b = ctk.CTkButton(btn_frame, text=f"Route {n}",
                              width=90, height=50,
                              font=("Tahoma", 18, "bold"),
                              command=lambda n=n: self._start_sequence(n))
            b.grid(row=0, column=n-1, padx=3)
            self.buttons.append(b)

        # ALL RUN button ------------------------------------------------
        self.all_run_btn = ctk.CTkButton(
            self.root,
            text=">>  ALL RUN  (1→5, skip if no color, home at end)  >>",
            font=("Tahoma", 20, "bold"), height=32,
            command=self._start_all_run)
        self.all_run_btn.pack(fill="x", padx=12, pady=(4, 2))

        # STOP button ---------------------------------------------------
        self.stop_btn = ctk.CTkButton(self.root,
                                      text="!!!  EMERGENCY STOP  !!!",
                                      fg_color="#d63031", text_color="white",
                                      hover_color="#a82020",
                                      font=("Tahoma", 24, "bold"), height=32,
                                      command=self._emergency_stop)
        self.stop_btn.pack(fill="x", padx=12, pady=4)

        # Status panel --------------------------------------------------
        stat = ctk.CTkFrame(self.root, corner_radius=6)
        stat.pack(fill="x", padx=12)

        ctk.CTkLabel(stat, text="Status", font=("Tahoma", 16, "bold")).pack(anchor="w", padx=6, pady=(4, 0))

        self.lbl_state = ctk.CTkLabel(stat, text="Idle, ready",
                                      font=("Tahoma", 18, "bold"))
        self.lbl_state.pack(anchor="w", padx=6)

        self.lbl_servo = ctk.CTkLabel(stat, text="Servo: IDLE",
                                      font=("Tahoma", 18))
        self.lbl_servo.pack(anchor="w", padx=6)

        self.lbl_color = ctk.CTkLabel(stat, text="Color: --",
                                      font=("Tahoma", 18))
        self.lbl_color.pack(anchor="w", padx=6, pady=(0, 4))

        # Log -----------------------------------------------------------
        ctk.CTkLabel(self.root, text="Event log:",
                     font=("Tahoma", 18, "bold")).pack(anchor="w", padx=12, pady=(4, 0))
        self.log = ctk.CTkTextbox(self.root, height=160,
                                  font=("Consolas", 16))
        self.log.pack(fill="both", expand=True, padx=12, pady=3)

    # ===================================================================
    # ZMQ listeners
    # ===================================================================
    def _status_loop(self):
        while self.running:
            try:
                msg = self.status_sub.recv_string()
            except zmq.Again:
                continue
            except Exception:
                break
            parts = msg.split()
            if len(parts) >= 2:
                self.servo_status = parts[1]
                # ส่งคำสั่ง update UI กลับไปทำบน main thread
                self.root.after(0, lambda s=self.servo_status:
                                self._set_servo_text(f"Servo: {s}"))

    def _vision_loop(self):
        while self.running:
            try:
                msg = self.vision_sub.recv_string()
            except zmq.Again:
                continue
            except Exception:
                break
            parts = msg.split()
            if len(parts) >= 2:
                self.detected_slots = [p.lower() for p in parts[1:]]
                self.last_color = self.detected_slots[0] if self.detected_slots else "none"
                summary = ", ".join(self.detected_slots)
                # ส่งคำสั่ง update UI กลับไปทำบน main thread
                self.root.after(0, lambda s=summary:
                                self._set_color_text(f"Slots: {s}"))
                self.color_event.set()

    # ===================================================================
    # Helpers
    # ===================================================================
    def _log(self, msg: str):
        # พิมพ์ลง terminal (cmd / powershell) แบบ real-time พร้อม timestamp + tag
        _term(msg)

        ts   = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"

        # ต้องสร้าง nested function เพราะ after() ต้องการ callable ไม่มี argument
        # และต้องการ capture ค่า line ณ เวลานี้
        def _append():
            self.log.insert("end", line)
            # see("end") ทำให้ log เลื่อนลงอัตโนมัติเมื่อมีบรรทัดใหม่
            self.log.see("end")

        self.root.after(0, _append)

    def _set_buttons(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for b in self.buttons:
            self.root.after(0, lambda b=b, s=state: b.configure(state=s))
        if hasattr(self, "all_run_btn"):
            self.root.after(0, lambda s=state: self.all_run_btn.configure(state=s))

    # ===================================================================
    # Actions
    # ===================================================================
    def _emergency_stop(self):
        self.abort_flag.set()
        self._log("EMERGENCY STOP pressed - sending stop + homing")
        try:
            self.cmd_pub.send_string(f"{TOPIC_SERVO_CMD} {CMD_STOP}")
        except Exception as e:
            self._log(f"stop pub error: {e}")
        self._set_state("STOPPED - homing")

    def _start_sequence(self, n: int):
        if self.busy:
            messagebox.showwarning("Busy",
                "A sequence is already running. Press STOP to abort first.")
            return

        def _wrap():
            self.busy = True
            self.abort_flag.clear()
            self._set_buttons(False)
            try:
                self._run_one(n, allow_skip=False)
            finally:
                self.busy = False
                self._set_buttons(True)

        threading.Thread(target=_wrap, daemon=True).start()

    def _start_all_run(self):
        if self.busy:
            messagebox.showwarning("Busy",
                "A sequence is already running. Press STOP to abort first.")
            return
        threading.Thread(target=self._run_all_sequence, daemon=True).start()

    def _capture_slots(self) -> list[str] | None:
        """Sends capture command to camera node and waits for slot colors."""
        self.color_event.clear()
        self.detected_slots = []
        self._set_state("CAPTURING photo")
        self._log("Requesting single-photo capture...")
        try:
            self.vision_pub.send_string(f"{TOPIC_VISION_CMD} {CMD_CAPTURE}")
        except Exception as e:
            self._log(f"vision pub error: {e}")
            return None

        # Wait for the results
        got = self.color_event.wait(timeout=float(self.vis_to.get()))
        if self.abort_flag.is_set():
            return None
        if not got or not self.detected_slots:
            self._log("Vision capture timeout or no result")
            return None

        self._log(f"Detected slots: {self.detected_slots}")
        return self.detected_slots

    def _run_all_sequence(self):
        self.busy = True
        self.abort_flag.clear()
        self._set_buttons(False)
        results = {}
        try:
            self._log("====== ALL RUN started: capture one photo ======")
            
            # Take one photo at the start (from home position)
            slots = self._capture_slots()
            if slots is None:
                self._set_state("ERROR - capture failed")
                self._log("ALL RUN aborted: capture failed")
                return

            for n in range(1, 6):
                if self.abort_flag.is_set():
                    self._log("ALL RUN aborted by STOP")
                    break

                self._set_state(f"ALL RUN  ->  button {n}/5")
                
                # Check color at slot n (n-1 index)
                color = slots[n - 1]
                if color == "none" or color not in COLORS:
                    self._log(f"Slot {n}: no color detected (skipped)")
                    results[n] = RES_SKIPPED
                    continue

                self._log(f"------ ALL RUN: starting button {n} (color: {color}) ------")
                
                # Run the sequence for slot n with the pre-detected color
                res = self._run_slot_sequence(n, color)
                results[n] = res
                self._log(f"   button {n} result = {res}")
                if res == RES_ABORTED:
                    self._log("ALL RUN aborted")
                    break
                if res == RES_ERROR:
                    self._log(f"button {n} ERROR - stopping ALL RUN")
                    break

            # สร้าง summary แสดงผลของแต่ละปุ่ม
            parts = []
            for n in range(1, 6):
                parts.append(f"#{n}:{results.get(n, '--')}")
            summary = "  |  ".join(parts)

            self._log(f"====== ALL RUN DONE  [{summary}] ======")
            self._set_state(f"ALL RUN DONE  [{summary}]")
        except Exception as e:
            self._log(f"Exception in ALL RUN: {e}")
            self._set_state(f"ERROR - {e}")
        finally:
            self.busy = False
            self._set_buttons(True)

    def _run_one(self, n: int, allow_skip: bool = False) -> str:
        """Run one button's sequence by taking one photo and checking slot n."""
        # 1. Capture photo first (from home)
        slots = self._capture_slots()
        if slots is None:
            self._set_state("ERROR - capture failed")
            return RES_ERROR

        # Check if slot n (1-indexed, so slots[n-1]) has a color
        if n <= 0 or n > len(slots):
            self._log(f"Invalid route number: {n}")
            return RES_ERROR

        color = slots[n - 1]
        if color == "none" or color not in COLORS:
            self._log(f"No color detected at Slot {n} (got '{color}')" + (" - skipping" if allow_skip else ""))
            if allow_skip:
                return self._safe_skip()
            self._set_state(f"ERROR - no color at Slot {n}")
            return RES_ERROR

        return self._run_slot_sequence(n, color)

    def _run_slot_sequence(self, n: int, color: str) -> str:
        """Runs the movement for slot n with a pre-detected color."""
        try:
            files     = BRANCH[n]
            main_file = os.path.join(DATA_DIR, files["main"])

            self._set_state(f"PLAYING_MAIN  ({files['main']})")
            self._log(f"Button {n}: playing {files['main']}")
            if not self._play_csv(main_file):
                return RES_ABORTED if self.abort_flag.is_set() else RES_ERROR

            self._set_state("WAITING_IDLE")
            if not self._wait_idle():
                return RES_ABORTED if self.abort_flag.is_set() else RES_ERROR

            branch_name = files[color]
            branch_file = os.path.join(DATA_DIR, branch_name)
            self._set_state(f"PLAYING_BRANCH  ({branch_name})")
            self._log(f"Branch by '{color}' -> {branch_name}")
            if not self._play_csv(branch_file):
                return RES_ABORTED if self.abort_flag.is_set() else RES_ERROR

            self._set_state("WAITING_IDLE")
            if not self._wait_idle():
                return RES_ABORTED if self.abort_flag.is_set() else RES_ERROR

            return RES_DONE
        except Exception as e:
            self._log(f"Exception in slot {n} sequence: {e}")
            return RES_ERROR

    def _safe_skip(self) -> str:
        """ข้ามปุ่มนี้ (โดยไม่ส่งแขนกลับ home)"""
        self._log("   skipping this button")
        if self.abort_flag.is_set():
            return RES_ABORTED
        return RES_SKIPPED

    def _play_csv(self, path: str) -> bool:
        if not os.path.isfile(path):
            self._log(f"Missing file: {path}")
            self._set_state(f"ERROR - missing {os.path.basename(path)}")
            return False
        delay = float(self.delay_var.get())
        with open(path) as f:
            reader = csv.reader(f)
            try:
                next(reader)  # ข้าม header row (Servo1, Servo2, Servo3, Servo4)
            except StopIteration:
                self._log(f"Empty file: {os.path.basename(path)}")
                return True
            for row in reader:
                if self.abort_flag.is_set():
                    self._log("Aborted during playback")
                    return False
                if len(row) != 4:
                    continue
                msg = f"{TOPIC_SERVO_CMD} {row[0]} {row[1]} {row[2]} {row[3]}"
                try:
                    self.cmd_pub.send_string(msg)
                except Exception as e:
                    self._log(f"pub err: {e}")
                self._log(f"   pose -> [{row[0]}, {row[1]}, {row[2]}, {row[3]}]")
                if self.abort_flag.wait(timeout=delay):
                    self._log("Aborted between poses")
                    return False
        return True

    def _wait_idle(self) -> bool:
        """รอจนกว่า servo จะกลับสู่สถานะ IDLE

        Phase 1: รอให้ servo เริ่ม BUSY ก่อน (สูงสุด 1.5 วินาที)
                 เพื่อให้แน่ใจว่า servo ได้รับคำสั่งและเริ่มเคลื่อนแล้ว
        Phase 2: รอให้ servo กลับมา IDLE (สูงสุด idle_timeout วินาที)
        """
        timeout = float(self.idle_to.get())

        # Phase 1 — รอให้ servo เริ่มขยับ (สูงสุด 1.5 วินาที)
        busy_deadline = time.time() + 1.5
        while time.time() < busy_deadline:
            if self.abort_flag.is_set():
                return False
            if self.servo_status == STATUS_BUSY:
                break
            time.sleep(0.05)

        # Phase 2 — รอให้ servo หยุดและกลับ IDLE
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.abort_flag.is_set():
                return False
            if self.servo_status == STATUS_IDLE:
                return True
            time.sleep(0.05)

        self._log("IDLE timeout")
        self._set_state("ERROR - idle timeout")
        return False

    def shutdown(self):
        self.running = False
        for s in (self.cmd_pub, self.status_sub,
                  self.vision_pub, self.vision_sub):
            try:
                s.close(0)  # 0 = linger: ปิด socket ทันที ไม่รอ message ค้าง
            except Exception:
                pass


def main():
    banner(NODE_NAME, [
        f"PUB :{PORT_SERVO_CMD} ({TOPIC_SERVO_CMD})   -> arduino_node",
        f"SUB :{PORT_SERVO_STATUS} ({TOPIC_SERVO_STATUS})  <- IDLE/BUSY",
        f"PUB :{PORT_VISION_CMD} ({TOPIC_VISION_CMD})   -> camera_node",
        f"SUB :{PORT_VISION_RESULT} ({TOPIC_VISION_RESULT})  <- color",
    ])
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    brain = Brain(root)
    ready(NODE_NAME)

    def on_close():
        _term("window closed -> shutting down", "STATE")
        brain.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
