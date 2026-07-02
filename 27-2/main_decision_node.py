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
    (vision timeout or unknown class), it is SKIPPED safely (arm goes home
    + waits IDLE) and the loop continues with the next button. After the
    last button, the arm returns to HOME pose.

Emergency STOP -> publishes "servo_cmd stop" (hardware overrides any sweep
and forces home), aborts any running sequence (single or ALL RUN).
"""

import csv
import os
import threading
import time
import tkinter as tk
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
    COLORS, DATA_DIR, ADDR_CONN, HOME_POSE,
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
        root.geometry("780x840")

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
        self.servo_status = STATUS_IDLE
        self.last_color   = None
        self.color_event  = threading.Event()
        self.last_colors_all = [None] * 5
        self.color_all_event = threading.Event()
        self.running      = True
        self.busy         = False
        # abort_flag ใช้ interrupt sequence กลางคัน เช่น กด STOP หรือ ALL RUN ถูก abort
        self.abort_flag   = threading.Event()
        self.last_sent_pose = None

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

        ctk.CTkLabel(ctrl, text="Timing & Detection", font=("Tahoma", 16, "bold")).grid(
            row=0, column=0, columnspan=6, sticky="w", padx=6, pady=(4, 0))

        # Define hidden delay_var for compatibility
        self.delay_var = ctk.DoubleVar(value=0.0)

        # Row 1: IDLE timeout & Vision timeout
        ctk.CTkLabel(ctrl, text="IDLE timeout (s):",
                     font=("Tahoma", 16)).grid(row=1, column=0, padx=4, sticky="e", pady=(4, 0))
        self.idle_to = ctk.DoubleVar(value=20.0)
        ctk.CTkEntry(ctrl, textvariable=self.idle_to, width=50,
                     font=("Tahoma", 16)).grid(row=1, column=1, pady=(4, 0))

        ctk.CTkLabel(ctrl, text="Vision timeout (s):",
                     font=("Tahoma", 16)).grid(row=1, column=2, padx=4, sticky="e", pady=(4, 0))
        self.vis_to = ctk.DoubleVar(value=1.2)
        ctk.CTkEntry(ctrl, textvariable=self.vis_to, width=50,
                     font=("Tahoma", 16)).grid(row=1, column=3, pady=(4, 0))

        # Row 2: Detect Mode & Sweep Speed
        ctk.CTkLabel(ctrl, text="Detect Mode:",
                     font=("Tahoma", 16, "bold")).grid(row=2, column=0, padx=2, sticky="e", pady=6)
        self.detect_mode_var = tk.StringVar(value="YOLO (AI)")
        self.detect_mode_btn = ctk.CTkSegmentedButton(
            ctrl, values=["YOLO (AI)", "Pixel (HSV)"],
            variable=self.detect_mode_var,
            font=("Tahoma", 14, "bold")
        )
        self.detect_mode_btn.grid(row=2, column=1, columnspan=3, sticky="w", padx=2, pady=6)

        ctk.CTkLabel(ctrl, text="Sweep Speed:",
                     font=("Tahoma", 16)).grid(row=2, column=4, padx=2, sticky="e", pady=6)
        self.sweep_speed_var = ctk.DoubleVar(value=8.0)
        ctk.CTkEntry(ctrl, textvariable=self.sweep_speed_var, width=50,
                     font=("Tahoma", 16)).grid(row=2, column=5, pady=6)

        # Row 3: Use Ease-out Checkbox & Speed Presets
        self.use_ease_var = tk.BooleanVar(value=True)
        self.use_ease_chk = ctk.CTkCheckBox(
            ctrl, text="Use Ease-out",
            variable=self.use_ease_var,
            font=("Tahoma", 15, "bold")
        )
        self.use_ease_chk.grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 6))

        ctk.CTkLabel(ctrl, text="Presets:", font=("Tahoma", 15, "bold")).grid(row=3, column=2, sticky="e", pady=(0, 6))
        
        self.preset_var = tk.StringVar(value="Normal")
        self.preset_btn = ctk.CTkSegmentedButton(
            ctrl, values=["Slow", "Normal", "Fast", "Max"],
            variable=self.preset_var,
            font=("Tahoma", 14, "bold"),
            command=self._apply_preset
        )
        self.preset_btn.grid(row=3, column=3, columnspan=3, sticky="w", padx=2, pady=(0, 6))

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
                topic = parts[0].lower()
                if topic == "color_result_all":
                    self.last_colors_all = [c.lower() for c in parts[1:6]]
                    while len(self.last_colors_all) < 5:
                        self.last_colors_all.append("none")
                    self.root.after(0, lambda c=self.last_colors_all:
                                    self._set_color_text(f"All Colors: {c}"))
                    self.color_all_event.set()
                elif topic == "color_result":
                    self.last_color = parts[1].lower()
                    self.root.after(0, lambda c=self.last_color:
                                    self._set_color_text(f"Color: {c}"))
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

    def _apply_preset(self, val):
        if val == "Slow":
            self.sweep_speed_var.set(3.0)
            self.use_ease_var.set(True)
        elif val == "Normal":
            self.sweep_speed_var.set(8.0)
            self.use_ease_var.set(True)
        elif val == "Fast":
            self.sweep_speed_var.set(20.0)
            self.use_ease_var.set(True)
        elif val == "Max":
            self.sweep_speed_var.set(90.0)
            self.use_ease_var.set(False)
        self._log(f"Applied speed preset: {val}")

    def _send_speed_settings(self):
        try:
            # ส่งความเร็ว
            speed_val = float(self.sweep_speed_var.get())
            self.cmd_pub.send_string(f"{TOPIC_SERVO_CMD} speed {speed_val}")
            
            # ส่ง Ease-out setting (1 = ใช้, 0 = ไม่ใช้)
            ease_val = "1" if self.use_ease_var.get() else "0"
            self.cmd_pub.send_string(f"{TOPIC_SERVO_CMD} ease {ease_val}")
            self._log(f"Synced settings: speed={speed_val}, ease={self.use_ease_var.get()}")
        except Exception as e:
            self._log(f"Failed to sync speed/ease: {e}")

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
                self._send_speed_settings()
                self._run_one(n, allow_skip=True)
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

    def _run_all_sequence(self):
        self.busy = True
        self.abort_flag.clear()
        self._set_buttons(False)
        self._send_speed_settings()
        results = {}
        start_time = time.time()
        mode_val = self.detect_mode_var.get()
        mode_cmd = "yolo" if "yolo" in mode_val.lower() else "pixel"
        try:
            self._log(f"====== ALL RUN started (all-at-once scan) [Mode: {mode_val}] ======")
            self._set_state("CAPTURING ALL slots")
            self._log(f"Requesting vision capture_all ({mode_cmd}) ...")
            self.color_all_event.clear()
            self.last_colors_all = [None] * 5
            
            try:
                self.vision_pub.send_string(f"{TOPIC_VISION_CMD} capture_all {mode_cmd}")
            except Exception as e:
                self._log(f"vision capture_all pub error: {e}")
                self._set_state("ERROR - vision pub")
                return

            # Wait for all colors
            got = self.color_all_event.wait(timeout=float(self.vis_to.get()))
            if self.abort_flag.is_set():
                self._log("ALL RUN aborted by STOP")
                return

            if not got:
                self._log("Vision capture_all timeout - stopping ALL RUN")
                self._set_state("ERROR - vision timeout")
                return

            colors = self.last_colors_all
            self._log(f"Scanned colors for slots 1->5: {colors}")

            for n in range(1, 6):
                if self.abort_flag.is_set():
                    self._log("ALL RUN aborted by STOP")
                    break
                
                self._set_state(f"ALL RUN  ->  slot {n}/5")
                self._log(f"------ ALL RUN: starting slot {n} ------")
                
                color = colors[n-1]
                if color in COLORS:
                    files = BRANCH[n]
                    branch_name = files[color]
                    branch_file = os.path.join(DATA_DIR, branch_name)
                    
                    self._set_state(f"PLAYING_BRANCH  ({branch_name})")
                    self._log(f"Branch by '{color}' -> {branch_name}")
                    
                    if not self._play_csv(branch_file):
                        results[n] = RES_ERROR
                        self._log(f"slot {n} ERROR - stopping ALL RUN")
                        break
                        
                    self._set_state("WAITING_IDLE")
                    if not self._wait_idle():
                        results[n] = RES_ERROR
                        self._log(f"slot {n} IDLE timeout - stopping ALL RUN")
                        break
                        
                    results[n] = RES_DONE
                    self._log(f"slot {n} complete (color {color})")
                else:
                    self._log(f"slot {n}: no usable color (got '{color}') - skipping slot")
                    results[n] = RES_SKIPPED

            # Final homing skipped per request
            self._log("ALL RUN finished - keeping final pose (no homing)")

            # สร้าง summary แสดงผลของแต่ละปุ่ม
            parts = []
            for n in range(1, 6):
                parts.append(f"#{n}:{results.get(n, '--')}")
            summary = "  |  ".join(parts)

            elapsed_time = time.time() - start_time
            self._log(f"====== ALL RUN DONE  [{summary}] ======")
            self._set_state(f"ALL RUN DONE ({elapsed_time:.2f}s) [{summary}]")
        except Exception as e:
            self._log(f"Exception in ALL RUN: {e}")
            self._set_state(f"ERROR - {e}")
        finally:
            elapsed_time = time.time() - start_time
            self._log(f"Total time elapsed: {elapsed_time:.2f} seconds")
            self.busy = False
            self._set_buttons(True)

    def _run_one(self, n: int, allow_skip: bool = False, play_main: bool = True) -> str:
        """Run one button's sequence.

        Returns one of RES_DONE / RES_SKIPPED / RES_ABORTED / RES_ERROR.

        If allow_skip is True, vision-timeout or unknown-color -> SKIPPED
        (and the arm is sent home + waits IDLE before returning, so the
        next sequence starts from a known-safe pose).
        """
        self.color_event.clear()
        self.last_color = None
        try:
            files     = BRANCH[n]

            if play_main:
                main_file = os.path.join(DATA_DIR, files["main"])
                self._set_state(f"PLAYING_MAIN  ({files['main']})")
                self._log(f"Button {n}: playing {files['main']}")
                if not self._play_csv(main_file):
                    return RES_ABORTED if self.abort_flag.is_set() else RES_ERROR

                self._set_state("WAITING_IDLE")
                if not self._wait_idle():
                    return RES_ABORTED if self.abort_flag.is_set() else RES_ERROR

                # ถอดดีเลย์หน่วงเวลาโฟกัสออกแล้ว ถ่ายภาพได้ทันทีเมื่อแขนกลหยุดเคลื่อนที่
                pass
            else:
                self._log(f"Button {n}: skipping main viewing file (static detection)")

            self._set_state("CAPTURING vision")
            self._log("Requesting vision capture ...")
            self.color_event.clear()
            self.last_color = None
            mode_val = self.detect_mode_var.get()
            mode_cmd = "yolo" if "yolo" in mode_val.lower() else "pixel"
            try:
                self.vision_pub.send_string(f"{TOPIC_VISION_CMD} {CMD_CAPTURE} {n} {mode_cmd}")
            except Exception as e:
                self._log(f"vision pub error: {e}")
                self._set_state("ERROR - vision pub")
                return RES_ERROR

            self._set_state("WAITING_COLOR")
            # got=True ถ้าได้รับสีก่อน timeout, False ถ้า timeout
            got = self.color_event.wait(timeout=float(self.vis_to.get()))
            if self.abort_flag.is_set():
                return RES_ABORTED

            # แปลงสีเป็น string ที่ใช้งานได้ (ถ้า timeout จะได้ "" แทน)
            if got and self.last_color:
                color = self.last_color.lower()
            else:
                color = ""

            if not got:
                self._log(f"Vision timeout (button {n})" + (" - skipping" if allow_skip else ""))
                if allow_skip:
                    return self._safe_skip()
                self._set_state("ERROR - vision timeout")
                return RES_ERROR

            self._log(f"   detected color = {color or '(none)'}")
            if color not in COLORS:
                self._log(f"No usable color (got '{color}')" + (" - skipping" if allow_skip else ""))
                if allow_skip:
                    return self._safe_skip()
                self._set_state("ERROR - unknown color")
                return RES_ERROR

            branch_name = files[color]
            branch_file = os.path.join(DATA_DIR, branch_name)
            self._set_state(f"PLAYING_BRANCH  ({branch_name})")
            self._log(f"Branch by '{color}' -> {branch_name}")
            
            if not self._play_csv(branch_file):
                return RES_ABORTED if self.abort_flag.is_set() else RES_ERROR

            self._set_state("WAITING_IDLE")
            if not self._wait_idle():
                return RES_ABORTED if self.abort_flag.is_set() else RES_ERROR

            self._set_state(f"DONE button {n}, color {color}")
            self._log(f"Button {n} complete (color {color})")
            return RES_DONE
        except Exception as e:
            self._log(f"Exception in button {n}: {e}")
            self._set_state(f"ERROR - {e}")
            return RES_ERROR

    def _safe_skip(self) -> str:
        """เงยแขนขึ้นเล็กน้อยแทนการกลับ home และรอ IDLE ก่อน return SKIPPED
        เพื่อป้องกันอาการสั่นและการเคลื่อนที่ไกลเกินไป"""
        self._set_state("SKIPPING - lifting arm up")
        self._log("   lifting arm up instead of going home")
        
        target_pose = list(HOME_POSE)
        if hasattr(self, "last_sent_pose") and self.last_sent_pose is not None:
            # ใช้มุมฐานเดิม (Servo 1) แต่ให้ไหล่ (Servo 2) และข้อศอก (Servo 3) เงยขึ้น 25 องศาเพื่อหลบสิ่งกีดขวาง
            s1, s2, s3, s4 = self.last_sent_pose
            s2_new = max(0.0, min(180.0, s2 + 25.0))
            s3_new = max(0.0, min(180.0, s3 + 25.0))
            target_pose = [s1, s2_new, s3_new, s4]
            
        try:
            self.servo_status = "PENDING"
            msg = f"{TOPIC_SERVO_CMD} {target_pose[0]:.1f} {target_pose[1]:.1f} {target_pose[2]:.1f} {target_pose[3]:.1f}"
            self.cmd_pub.send_string(msg)
        except Exception as e:
            self._log(f"skip lift pub err: {e}")
            
        self._wait_idle()
        if self.abort_flag.is_set():
            return RES_ABORTED
        return RES_SKIPPED

    def _play_csv(self, path: str) -> bool:
        if not os.path.isfile(path):
            self._log(f"Missing file: {path}")
            self._set_state(f"ERROR - missing {os.path.basename(path)}")
            return False
        with open(path) as f:
            reader = csv.reader(f)
            try:
                next(reader)  # ข้าม header row (Servo1, Servo2, Servo3, Servo4)
            except StopIteration:
                self._log(f"Empty file: {os.path.basename(path)}")
                return True
            
            rows = list(reader)
            for idx, row in enumerate(rows):
                if self.abort_flag.is_set():
                    self._log("Aborted during playback")
                    return False
                if len(row) != 4:
                    continue
                msg = f"{TOPIC_SERVO_CMD} {row[0]} {row[1]} {row[2]} {row[3]}"
                try:
                    self.servo_status = "PENDING"
                    self.cmd_pub.send_string(msg)
                    self.last_sent_pose = [float(row[0]), float(row[1]), float(row[2]), float(row[3])]
                except Exception as e:
                    self._log(f"pub err: {e}")
                self._log(f"   pose -> [{row[0]}, {row[1]}, {row[2]}, {row[3]}]")
                # สำหรับทุก pose ยกเว้น pose สุดท้าย ให้รอจนแขนกลถึงจุดจริง (IDLE)
                if idx < len(rows) - 1:
                    if not self._wait_idle():
                        self._log("Aborted or timed out waiting for pose")
                        return False
        return True

    def _wait_idle(self) -> bool:
        """รอจนกว่า servo จะกลับสู่สถานะ IDLE
        
        Phase 1: รอให้ servo_status ได้รับการอัปเดตจาก PENDING (สูงสุด 1.0 วินาที)
        Phase 2: รอให้ servo กลับมา IDLE (สูงสุด idle_timeout วินาที)
        """
        # หน่วงเวลาสั้นๆ (50ms) เพื่อป้องกันการอ่านสถานะ IDLE เก่า ก่อนที่โหนดจะอัปเดตสถานะเป็น BUSY
        time.sleep(0.05)
        
        timeout = float(self.idle_to.get())

        # Phase 1 — รอการตอบรับจาก Arduino (ไม่เป็น PENDING)
        wait_start = time.time()
        while self.servo_status == "PENDING" and (time.time() - wait_start) < 1.0:
            if self.abort_flag.is_set():
                return False
            time.sleep(0.01)

        # หากไม่มีการตอบรับ ให้ล็อกเตือนแต่ให้ทำต่อ (fallback)
        if self.servo_status == "PENDING":
            self._log("Warning: No status update from servo node, assuming IDLE")
            return True

        # Phase 2 — รอให้ servo เคลื่อนที่จนเสร็จและกลับสู่สถานะ IDLE
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.abort_flag.is_set():
                return False
            if self.servo_status == STATUS_IDLE:
                return True
            time.sleep(0.01)

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
