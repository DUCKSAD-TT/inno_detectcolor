"""Node 1 — Vision Node.

Real-time YOLOv8 preview, but only PUBLISHES a color result when explicitly
asked (via vision_cmd "capture"), or when the user clicks the local Test
Capture button.

When asked to capture: collects ~8 frames over ~0.5 s, picks the highest-
confidence detection in each frame whose class is in COLORS, and votes the
most common color → robust against single-frame false positives.

ZMQ:
    SUB binds  PORT_VISION_CMD     ("vision_cmd capture")
    PUB binds  PORT_VISION_RESULT  ("color_result <red|blue|green|yellow|none>")
"""

import os
# ข้ามการเช็ค update ของ ultralytics ตอน startup — ทำให้เปิดเร็วขึ้นและทำงาน offline ได้
os.environ.setdefault("YOLO_OFFLINE", "True")

import threading
import time
from collections import Counter
import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk
import cv2
from PIL import Image, ImageTk
import zmq
# ultralytics import แบบ lazy ใน _init_heavy เพื่อให้หน้าต่างโผล่ก่อน

from utils.zmq_config import (
    PORT_VISION_CMD, PORT_VISION_RESULT,
    TOPIC_VISION_CMD, TOPIC_VISION_RESULT,
    CMD_CAPTURE, COLORS, ADDR_BIND,
    DEFAULT_CAMERA_INDEX, DEFAULT_CONFIDENCE,
    SLOT_CENTERS,
)
from utils.node_log import banner, make_logger, ready

MODEL_PATH = "4color-detection.pt"

NODE_NAME = "CAMERA NODE — YOLOv8 Vision"
# logger กลางของ node นี้ — พิมพ์ log ลง terminal (cmd / powershell) แบบ real-time
_term = make_logger("camera")


class VisionNode:
    def __init__(self, root: ctk.CTk):
        self.root = root
        root.title("Camera Node — YOLOv8")
        root.geometry("580x880")

        # ของหนัก — จะถูก initialize ใน background thread (_init_heavy)
        self.model      = None
        self.cap        = None
        self.cam_idx    = DEFAULT_CAMERA_INDEX
        self.confidence = DEFAULT_CONFIDENCE
        self.running    = True
        self.ready      = threading.Event()
        self.cap_lock   = threading.Lock()
        self.vote_frames = 4
        self.vote_gap    = 0.03
        self.frame_w     = 640
        self.frame_h     = 480

        # ZMQ — bind ทันทีตั้งแต่หน้าต่างขึ้นมา เพื่อให้ node อื่นเชื่อมเข้ามาได้
        # ทันที แม้ว่า YOLO ยังโหลดไม่เสร็จ
        self.ctx = zmq.Context.instance()
        self.sub = self.ctx.socket(zmq.SUB)
        self.sub.bind(ADDR_BIND.format(port=PORT_VISION_CMD))
        self.sub.setsockopt_string(zmq.SUBSCRIBE, TOPIC_VISION_CMD)
        self.sub.RCVTIMEO = 100   # timeout 100ms เพื่อให้ loop ออกได้เมื่อ running=False

        self.pub = self.ctx.socket(zmq.PUB)
        self.pub.bind(ADDR_BIND.format(port=PORT_VISION_RESULT))

        self._build_ui()   # หน้าต่างโผล่ทันที

        # โหลด YOLO + เปิดกล้อง ใน background — main thread จะ free รันต่อ
        threading.Thread(target=self._init_heavy, daemon=True).start()
        threading.Thread(target=self._zmq_loop,   daemon=True).start()

    # ---------------- Background warm-up ----------------
    def _init_heavy(self):
        """โหลด YOLO + เปิดกล้อง นอก main thread เพื่อให้หน้าต่างไม่ค้าง"""
        self._set_status("Loading YOLO model...")
        from ultralytics import YOLO   # lazy import — pulls in torch (~5-15s ครั้งแรก)
        model = YOLO(MODEL_PATH)

        self._set_status("Opening camera...")
        cap = self._make_capture(self.cam_idx)

        with self.cap_lock:
            self.model = model
            self.cap   = cap

        self.ready.set()
        self._set_status("Detected color: —")
        # กลับ main thread เพื่อ enable controls และ start preview loop
        self.root.after(0, self._on_ready)

    def _on_ready(self):
        self.cam_dd.configure(state="readonly")
        self.btn_test.configure(state="normal")
        self._update_frame()

    def _make_capture(self, idx: int):
        # CAP_DSHOW (DirectShow) มักเปิดกล้อง USB เร็วกว่า Media Foundation
        # backend ปกติ 2-3 เท่าบน Windows
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        return cap

    # ---------------- UI ----------------
    def _build_ui(self):
        top = ctk.CTkFrame(self.root, fg_color="transparent")
        top.pack(fill="x", pady=4, padx=8)

        ctk.CTkLabel(top, text="Cam:", font=("Tahoma", 18)).pack(side="left")
        self.cam_var = tk.StringVar(value=str(self.cam_idx))
        # Keep ttk.Combobox for <<ComboboxSelected>> binding
        # disabled จนกว่า _init_heavy จะเสร็จ (กันสลับกล้องก่อน YOLO โหลดเสร็จ)
        self.cam_dd = ttk.Combobox(top, textvariable=self.cam_var,
                                   values=["0", "1", "2", "3", "4", "5"],
                                   width=3, state="disabled")
        self.cam_dd.pack(side="left", padx=3)
        self.cam_dd.bind("<<ComboboxSelected>>",
                         lambda e: self._open_camera(int(self.cam_var.get())))

        ctk.CTkLabel(top, text="  Conf:", font=("Tahoma", 18)).pack(side="left")
        self.conf_var = tk.DoubleVar(value=self.confidence)
        conf_slider = ctk.CTkSlider(top, from_=0.30, to=0.95,
                                    number_of_steps=65,
                                    variable=self.conf_var,
                                    width=140,
                                    command=self._on_conf)
        conf_slider.pack(side="left", padx=3)

        # disabled จนกว่า model จะพร้อม
        self.btn_test = ctk.CTkButton(top, text="Test Capture", width=100,
                                      font=("Tahoma", 18, "bold"),
                                      state="disabled",
                                      command=self._manual_capture)
        self.btn_test.pack(side="right", padx=4)

        # Video label kept as tk.Label for ImageTk.PhotoImage compatibility
        video_frame = ctk.CTkFrame(self.root, corner_radius=4)
        video_frame.pack(pady=4)
        self.lbl_video = tk.Label(video_frame, width=400, height=300, borderwidth=0, highlightthickness=0)
        self.lbl_video.pack()
        self.lbl_video.bind("<Button-1>", self._on_video_click)

        # ขึ้น "Loading YOLO model..." ตอนแรก จะถูกอัปเดตใน _init_heavy
        self.lbl_status = ctk.CTkLabel(self.root, text="Loading YOLO model...",
                                       font=("Tahoma", 22, "bold"))
        self.lbl_status.pack(pady=2)

        self.lbl_last = ctk.CTkLabel(self.root, text="Live: starting up…",
                                     font=("Tahoma", 18))
        self.lbl_last.pack()

        # แผงควบคุมจำนวนการตรวจจับเพื่อการประมวลผลที่รวดเร็วขึ้น
        perf_frame = ctk.CTkFrame(self.root, corner_radius=6)
        perf_frame.pack(fill="x", padx=8, pady=4)
        
        ctk.CTkLabel(perf_frame, text="Vision Performance (Voting)", font=("Tahoma", 16, "bold")).pack(anchor="w", padx=8, pady=(4, 0))
        
        perf_row = ctk.CTkFrame(perf_frame, fg_color="transparent")
        perf_row.pack(fill="x", padx=8, pady=2)
        
        ctk.CTkLabel(perf_row, text="Voting Frames:", font=("Tahoma", 15)).pack(side="left")
        self.frames_lbl = ctk.CTkLabel(perf_row, text="4 frames", font=("Consolas", 15, "bold"))
        self.frames_lbl.pack(side="right", padx=4)
        
        self.frames_var = tk.IntVar(value=4)
        self.frames_slider = ctk.CTkSlider(
            perf_row, from_=1, to=15, number_of_steps=14,
            variable=self.frames_var,
            command=self._on_frames_change
        )
        self.frames_slider.pack(fill="x", expand=True, padx=8)

        # Slot Calibration & Mode Control Panel
        cal_frame = ctk.CTkFrame(self.root, corner_radius=6)
        cal_frame.pack(fill="x", padx=8, pady=4)
        
        ctk.CTkLabel(cal_frame, text="Slot Calibration & Mode Control", font=("Tahoma", 16, "bold")).pack(anchor="w", padx=8, pady=(4, 0))
        
        # Mode selector row
        mode_row = ctk.CTkFrame(cal_frame, fg_color="transparent")
        mode_row.pack(fill="x", padx=8, pady=3)
        ctk.CTkLabel(mode_row, text="Detect Mode:", font=("Tahoma", 15)).pack(side="left")
        
        self.detect_mode = tk.StringVar(value="YOLO (AI)")
        self.mode_btn = ctk.CTkSegmentedButton(
            mode_row, values=["YOLO (AI)", "Pixel (HSV)"],
            variable=self.detect_mode,
            font=("Tahoma", 14, "bold")
        )
        self.mode_btn.pack(side="right", padx=4)

        # Slot selection row (S1-S5)
        slot_row = ctk.CTkFrame(cal_frame, fg_color="transparent")
        slot_row.pack(fill="x", padx=8, pady=3)
        ctk.CTkLabel(slot_row, text="Active Slot to Set:", font=("Tahoma", 15)).pack(side="left")
        
        self.active_slot = tk.StringVar(value="S1")
        self.slot_btn = ctk.CTkSegmentedButton(
            slot_row, values=["S1", "S2", "S3", "S4", "S5"],
            variable=self.active_slot,
            font=("Tahoma", 14, "bold")
        )
        self.slot_btn.pack(side="right", padx=4)

        # Coordinates display row
        coords_row = ctk.CTkFrame(cal_frame, fg_color="transparent")
        coords_row.pack(fill="x", padx=8, pady=3)
        
        self.coord_labels = []
        for i in range(5):
            lbl = ctk.CTkLabel(coords_row, text=f"S{i+1}: ({SLOT_CENTERS[i][0]},{SLOT_CENTERS[i][1]})", font=("Consolas", 12))
            lbl.pack(side="left", expand=True)
            self.coord_labels.append(lbl)

        # Save coordinates button
        save_row = ctk.CTkFrame(cal_frame, fg_color="transparent")
        save_row.pack(fill="x", padx=8, pady=4)
        
        self.btn_save_cal = ctk.CTkButton(
            save_row, text="💾 Save Coordinates Calibration",
            height=34, font=("Tahoma", 15, "bold"),
            command=self._save_calibration
        )
        self.btn_save_cal.pack(fill="x")

        info = (f"SUB :{PORT_VISION_CMD} ({TOPIC_VISION_CMD})   "
                f"PUB :{PORT_VISION_RESULT} ({TOPIC_VISION_RESULT})")
        ctk.CTkLabel(self.root, text=info,
                     font=("Consolas", 14)).pack(pady=2)

    def _on_frames_change(self, val):
        self.vote_frames = int(val)
        self.frames_lbl.configure(text=f"{self.vote_frames} frames" + (" (Fastest)" if self.vote_frames == 1 else ""))

    def _on_conf(self, _val):
        self.confidence = float(self.conf_var.get())

    # ---------------- UI helpers ----------------
    def _set_status(self, text: str):
        """อัปเดต status label — thread-safe (ส่งคำสั่งไป main thread)"""
        _term(text, "STATE")
        self.root.after(0, lambda: self.lbl_status.configure(text=text))

    def _set_live(self, text: str):
        """อัปเดต live detection label — เรียกจาก main thread เท่านั้น"""
        self.lbl_last.configure(text=text)

    # ---------------- Camera ----------------
    def _open_camera(self, idx: int):
        with self.cap_lock:
            if self.cap is not None:
                try:
                    self.cap.release()
                except Exception:
                    pass
            self.cap = self._make_capture(idx)
            self.cam_idx = idx
            _term(f"camera switched to index {idx}")

    def _read_frame(self):
        with self.cap_lock:
            if self.cap is None:
                return False, None
            return self.cap.read()

    # ---------------- Live preview ----------------
    def _update_frame(self):
        """อัปเดต video frame และ detection ทุก 20ms — เรียกตัวเองซ้ำผ่าน after()"""
        if not self.running or self.model is None:
            return

        ret, frame = self._read_frame()
        live_label = "Live: (no camera)"

        if ret:
            self.frame_h, self.frame_w = frame.shape[:2]
            best_label = None
            best_conf  = 0.0

            # รันโมเดล YOLO เฉพาะเมื่อเลือกโหมด YOLO (AI) เท่านั้น
            slot_colors_this_frame = ["none"] * 5
            if self.detect_mode.get() == "YOLO (AI)":
                results = self.model(frame, verbose=False)[0]
                for box in results.boxes:
                    conf = float(box.conf[0])
                    if conf >= self.confidence:
                        cls_id = int(box.cls[0])
                        label  = self.model.names[cls_id].lower()

                        if label in COLORS:
                            coords          = box.xyxy[0]
                            x1, y1, x2, y2 = int(coords[0]), int(coords[1]), int(coords[2]), int(coords[3])
                            cx = (x1 + x2) / 2.0
                            cy = (y1 + y2) / 2.0

                            # Find nearest slot and distance
                            slot_idx = min(range(5), key=lambda i: (cx - SLOT_CENTERS[i][0])**2 + (cy - SLOT_CENTERS[i][1])**2)
                            dist_sq = (cx - SLOT_CENTERS[slot_idx][0])**2 + (cy - SLOT_CENTERS[slot_idx][1])**2

                            mapped_text = ""
                            if dist_sq <= 120**2:
                                slot_colors_this_frame[slot_idx] = label
                                mapped_text = f" -> S{slot_idx+1}"
                                # Draw a line from box center to slot center to show visual mapping
                                cv2.line(frame, (int(cx), int(cy)), (int(SLOT_CENTERS[slot_idx][0]), int(SLOT_CENTERS[slot_idx][1])), (255, 0, 0), 1)

                            # วาด bounding box และ label บน frame
                            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
                            cv2.putText(frame, f"{label} {conf:.2f}{mapped_text}",
                                        (x1, max(y1 - 8, 12)),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                
                # Update live_label to show slot colors
                detected_slots = []
                for idx, c in enumerate(slot_colors_this_frame):
                    if c != "none":
                        detected_slots.append(f"S{idx+1}:{c}")
                if detected_slots:
                    live_label = f"Live (AI): " + ", ".join(detected_slots)
                else:
                    live_label = "Live (AI): no color inside slots"
            else:
                live_label = "Live (Pixel): HSV color reading active"

            # วาดจุดกึ่งกลางของ 5 slots เพื่อช่วยในระบบ visual calibration
            for i, (x, y) in enumerate(SLOT_CENTERS):
                cv2.circle(frame, (int(x), int(y)), 5, (0, 255, 0), -1)  # solid green dot
                cv2.line(frame, (int(x) - 10, int(y)), (int(x) + 10, int(y)), (0, 255, 0), 2)
                cv2.line(frame, (int(x), int(y) - 10), (int(x), int(y) + 10), (0, 255, 0), 2)
                
                # ถ้ารันโหมด Pixel ให้ดีเทคสีจุดนั้นแสดงใน preview ทันที
                if self.detect_mode.get() == "Pixel (HSV)":
                    c_det = self._detect_pixel_color(frame, int(x), int(y))
                    label_text = f"S{i+1}: {c_det.upper()}"
                    color_marker = (0, 0, 255) if c_det == "red" else (0, 255, 0)
                else:
                    c_det = slot_colors_this_frame[i]
                    label_text = f"S{i+1}: {c_det.upper()}" if c_det != "none" else f"S{i+1}"
                    color_marker = (0, 0, 255) if c_det == "red" else (0, 255, 0)

                cv2.putText(frame, label_text, (int(x) - 25, int(y) - 12),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color_marker, 1, cv2.LINE_AA)

            img   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            imgtk = ImageTk.PhotoImage(image=Image.fromarray(img).resize((400, 300)))

            # ต้องเก็บ reference ไว้ใน self ไม่งั้น Python จะลบ image ออกจาก memory
            # ก่อนที่ Tkinter จะแสดงผล ทำให้ภาพหายไป
            self.lbl_video.imgtk = imgtk
            self.lbl_video.configure(image=imgtk)

        self._set_live(live_label)
        # เรียกตัวเองอีกครั้งใน 20ms เพื่อให้ได้ประมาณ 50fps
        self.root.after(20, self._update_frame)

    # ---------------- Capture-on-request ----------------
    def _capture_and_vote(self, n_frames: int = 8, gap_s: float = 0.05, slot_idx: int = 0, mode_override: str = None):
        """เก็บ n_frames frames ห่างกัน gap_s วินาที (รวม ~0.4 วินาที)
        แล้วเลือกสีที่ได้รับการโหวตมากที่สุด"""
        mode = mode_override if mode_override else self.detect_mode.get()
        # หากรันโหมด Pixel (HSV)
        if mode == "Pixel (HSV)":
            votes = []
            for _ in range(n_frames):
                ret, frame = self._read_frame()
                if not ret:
                    continue
                x, y = SLOT_CENTERS[slot_idx]
                color = self._detect_pixel_color(frame, x, y)
                votes.append(color)
                time.sleep(gap_s)
            if not votes:
                return None
            top_results = Counter(votes).most_common(1)
            winner      = top_results[0][0]
            _term(f"Pixel vote {dict(Counter(votes))} -> {winner} for slot {slot_idx+1}", "VOTE")
            return winner

        # โหมด YOLO (AI)
        votes = []
        for _ in range(n_frames):
            ret, frame = self._read_frame()
            if not ret:
                continue
            results    = self.model(frame, verbose=False)[0]
            best_label = "none"
            best_conf  = 0.0

            for box in results.boxes:
                conf = float(box.conf[0])
                if conf >= self.confidence:
                    cls_id = int(box.cls[0])
                    label  = self.model.names[cls_id].lower()
                    if label in COLORS:
                        coords = box.xyxy[0]
                        cx = (float(coords[0]) + float(coords[2])) / 2.0
                        cy = (float(coords[1]) + float(coords[3])) / 2.0

                        # Find nearest slot and distance
                        closest_slot_idx = min(range(5), key=lambda i: (cx - SLOT_CENTERS[i][0])**2 + (cy - SLOT_CENTERS[i][1])**2)
                        dist_sq = (cx - SLOT_CENTERS[closest_slot_idx][0])**2 + (cy - SLOT_CENTERS[closest_slot_idx][1])**2

                        if closest_slot_idx == slot_idx and dist_sq <= 120**2:
                            if conf > best_conf:
                                best_conf  = conf
                                best_label = label

            votes.append(best_label)
            time.sleep(gap_s)

        if not votes:
            return None

        # หาสีที่พบมากที่สุดจากการโหวต
        top_results = Counter(votes).most_common(1)
        winner      = top_results[0][0]
        _term(f"AI vote {dict(Counter(votes))} -> {winner} for slot {slot_idx+1}", "VOTE")
        return winner

    def _publish_result(self, color):
        """ส่งผลสีที่ตรวจพบผ่าน ZMQ และอัปเดต status label"""
        result = color if color else "none"
        try:
            self.pub.send_string(f"{TOPIC_VISION_RESULT} {result}")
            _term(f"published color_result -> {result}", "PUB")
        except Exception as e:
            _term(f"pub err: {e}", "ERROR")
        self._set_status(f"Detected color: {result}")

    def _capture_and_vote_all(self, n_frames: int = 4, gap_s: float = 0.03, mode_override: str = None):
        """เก็บ n_frames frames ห่างกัน gap_s วินาที
        แล้วเลือกสีที่ได้รับการโหวตมากที่สุดสำหรับแต่ละ slot จาก 5 slots (แบ่งตาม SLOT_CENTERS)"""
        slot_votes = [[] for _ in range(5)]
        
        mode = mode_override if mode_override else self.detect_mode.get()
        # หากรันโหมด Pixel (HSV)
        if mode == "Pixel (HSV)":
            for _ in range(n_frames):
                ret, frame = self._read_frame()
                if not ret:
                    continue
                for i in range(5):
                    x, y = SLOT_CENTERS[i]
                    color = self._detect_pixel_color(frame, x, y)
                    slot_votes[i].append(color)
                time.sleep(gap_s)
        else:
            # โหมด YOLO (AI)
            for _ in range(n_frames):
                ret, frame = self._read_frame()
                if not ret:
                    continue
                results = self.model(frame, verbose=False)[0]
                
                # หา best detection สำหรับแต่ละ slot ใน frame นี้
                frame_best = {}  # slot_idx -> (label, conf)
                
                for box in results.boxes:
                    conf = float(box.conf[0])
                    if conf >= self.confidence:
                        cls_id = int(box.cls[0])
                        label  = self.model.names[cls_id].lower()
                        if label in COLORS:
                            coords = box.xyxy[0]
                            cx = (float(coords[0]) + float(coords[2])) / 2.0
                            cy = (float(coords[1]) + float(coords[3])) / 2.0
                            
                            # หา slot ที่ใกล้ที่สุด (2D distance)
                            slot_idx = min(range(5), key=lambda i: (cx - SLOT_CENTERS[i][0])**2 + (cy - SLOT_CENTERS[i][1])**2)
                            dist_sq = (cx - SLOT_CENTERS[slot_idx][0])**2 + (cy - SLOT_CENTERS[slot_idx][1])**2
                            
                            if dist_sq <= 120**2:
                                if slot_idx not in frame_best or conf > frame_best[slot_idx][1]:
                                    frame_best[slot_idx] = (label, conf)
                
                # โหวตให้ผลลัพธ์ของแต่ละ slot
                for i in range(5):
                    if i in frame_best:
                        slot_votes[i].append(frame_best[i][0])
                    else:
                        slot_votes[i].append("none")
                    
                time.sleep(gap_s)
            
        winners = []
        for i in range(5):
            votes = slot_votes[i]
            if not votes:
                winners.append("none")
            else:
                top_results = Counter(votes).most_common(1)
                winners.append(top_results[0][0])
                
        _term(f"vote all results: {winners}", "VOTE")
        return winners

    def _publish_result_all(self, colors):
        formatted_colors = [c if c else "none" for c in colors]
        msg = f"color_result_all {' '.join(formatted_colors)}"
        try:
            self.pub.send_string(msg)
            _term(f"published color_result_all -> {formatted_colors}", "PUB")
        except Exception as e:
            _term(f"pub all err: {e}", "ERROR")
        self._set_status(f"All slots: {' '.join(formatted_colors)}")

    # ---------------- ZMQ + manual ----------------
    def _zmq_loop(self):
        while self.running:
            try:
                msg = self.sub.recv_string()
            except zmq.Again:
                continue
            except Exception:
                break
            parts = msg.split()
            if len(parts) >= 2:
                cmd = parts[1]
                if cmd == CMD_CAPTURE:
                    _term("capture command received", "CMD")
                    if not self.ready.is_set():
                        # capture ถูกร้องขอก่อน YOLO โหลดเสร็จ — ตอบกลับ "none" เพื่อไม่ให้
                        # main_decision_node ค้างรอ
                        _term("capture requested before model is ready", "WARN")
                        self._publish_result(None)
                        continue
                    
                    # Parse slot_idx from ZMQ arguments if available
                    slot_idx = 0
                    if len(parts) >= 3:
                        try:
                            slot_idx = int(parts[2]) - 1
                        except ValueError:
                            pass
                    
                    # Parse mode override
                    mode_override = None
                    if len(parts) >= 4:
                        mode_arg = parts[3].lower()
                        if mode_arg in ["yolo", "pixel"]:
                            mode_override = "YOLO (AI)" if mode_arg == "yolo" else "Pixel (HSV)"
                            self.root.after(0, lambda m=mode_override: self.detect_mode.set(m))
                            _term(f"Temporarily switched detect mode to {mode_override} via command", "CMD")
                            
                    color = self._capture_and_vote(n_frames=self.vote_frames, gap_s=self.vote_gap, slot_idx=slot_idx, mode_override=mode_override)
                    self._publish_result(color)
                elif cmd == "capture_all":
                    _term("capture_all command received", "CMD")
                    if not self.ready.is_set():
                        _term("capture_all requested before model is ready", "WARN")
                        self._publish_result_all([None]*5)
                        continue
                    
                    # Parse mode override
                    mode_override = None
                    if len(parts) >= 3:
                        mode_arg = parts[2].lower()
                        if mode_arg in ["yolo", "pixel"]:
                            mode_override = "YOLO (AI)" if mode_arg == "yolo" else "Pixel (HSV)"
                            self.root.after(0, lambda m=mode_override: self.detect_mode.set(m))
                            _term(f"Temporarily switched detect mode to {mode_override} via command", "CMD")
                            
                    colors = self._capture_and_vote_all(n_frames=self.vote_frames, gap_s=self.vote_gap, mode_override=mode_override)
                    self._publish_result_all(colors)

    def _manual_capture(self):
        """กดปุ่ม Test Capture — รัน capture ใน background thread สำหรับ Active Slot ปัจจุบัน"""
        # หา active slot จาก GUI (เช่น "S1" -> index 0)
        active_str = self.active_slot.get()
        try:
            slot_idx = int(active_str[1]) - 1
        except Exception:
            slot_idx = 0
            
        x, y = SLOT_CENTERS[slot_idx]
        
        def do_capture():
            color = self._capture_and_vote(n_frames=self.vote_frames, gap_s=self.vote_gap, slot_idx=slot_idx)
            self._publish_result(color)
            
            result_str = color if color else "none"
            msg = f"Test Capture {active_str} at ({x}, {y}) detected color: {result_str.upper()}"
            self._set_status(msg)
            _term(msg, "TEST")
            
            # Popup dialog to tell user coordinate, slot, and color
            def show_popup():
                messagebox.showinfo(
                    "ผลการทดสอบจับภาพ (Test Result)",
                    f"Slot (ช่อง): {active_str}\n"
                    f"Coordinate (พิกัด): ({x}, {y})\n"
                    f"Color (สีที่ตรวจพบ): {result_str.upper()}"
                )
            self.root.after(0, show_popup)

        t = threading.Thread(target=do_capture, daemon=True)
        t.start()

    def _on_video_click(self, event):
        """เมื่อคลิกที่วิดีโอ ให้พิกัดที่คลิกนั้นอัปเดตเป็นค่าของ Active Slot ปัจจุบัน"""
        orig_x = int(event.x * (self.frame_w / 400.0))
        orig_y = int(event.y * (self.frame_h / 300.0))
        
        # หา active slot index (S1 -> 0, S2 -> 1, ...)
        active_str = self.active_slot.get()
        try:
            slot_idx = int(active_str[1]) - 1
        except Exception:
            slot_idx = 0
        
        # อัปเดต SLOT_CENTERS ในหน่วยความจำ
        SLOT_CENTERS[slot_idx] = (orig_x, orig_y)
        
        # อัปเดตข้อความบน GUI
        self.coord_labels[slot_idx].configure(text=f"{active_str}: ({orig_x},{orig_y})")
        self._set_status(f"Calibrated {active_str} to ({orig_x}, {orig_y})")
        _term(f"Calibrated {active_str} -> ({orig_x}, {orig_y})", "CALIBRATE")
        
        # ขยับไป slot ถัดไปอัตโนมัติ (S1 -> S2 -> S3 -> S4 -> S5) เพื่อให้จิ้มต่อเนื่องได้ง่าย
        next_idx = (slot_idx + 1) % 5
        self.active_slot.set(f"S{next_idx+1}")

    def _save_calibration(self):
        import json
        from utils.zmq_config import SLOT_CENTERS_FILE
        try:
            with open(SLOT_CENTERS_FILE, "w") as f:
                json.dump(SLOT_CENTERS, f)
            self._set_status("Calibration saved to slot_config.json!")
            _term("Saved coordinates to slot_config.json", "CALIBRATE")
        except Exception as e:
            self._set_status(f"Error saving: {e}")
            _term(f"Save error: {e}", "ERROR")

    def _detect_pixel_color(self, frame, x, y):
        """วิเคราะห์สีบริเวณจุดพิกัด (x, y) ในภาพ โดยใช้โมเดลสี HSV (ไม่ใช้ AI)"""
        h_img, w_img = frame.shape[:2]
        if not (0 <= x < w_img and 0 <= y < h_img):
            return "none"
            
        # ดึงบริเวณรอบๆ จุดพิกัด (ขนาด 7x7 พิกเซล) เพื่อหลีกเลี่ยง noise จากพิกเซลเดียว
        half = 3
        x1 = max(0, x - half)
        x2 = min(w_img - 1, x + half)
        y1 = max(0, y - half)
        y2 = min(h_img - 1, y + half)
        
        patch = frame[y1:y2+1, x1:x2+1]
        if patch.size == 0:
            return "none"
            
        hsv_patch = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        
        avg_h = hsv_patch[:, :, 0].mean()
        avg_s = hsv_patch[:, :, 1].mean()
        avg_v = hsv_patch[:, :, 2].mean()
        
        # หากความอิ่มตัวสี (S) หรือความสว่าง (V) ต่ำเกินไป จะมองเป็นไม่มีสีเด่นชัด (none)
        if avg_s < 45 or avg_v < 40:
            return "none"
            
        # เงื่อนไขช่วงค่าสี HSV
        if (0 <= avg_h <= 8) or (172 <= avg_h <= 180):
            return "red"
        elif 9 <= avg_h <= 34:
            return "yellow"
        elif 35 <= avg_h <= 85:
            return "green"
        elif 86 <= avg_h <= 130:
            return "blue"
        else:
            return "none"

    # ---------------- Lifecycle ----------------
    def shutdown(self):
        self.running = False
        try:
            with self.cap_lock:
                if self.cap:
                    self.cap.release()
        except Exception:
            pass
        for s in (self.sub, self.pub):
            try:
                s.close(0)  # 0 = linger: ปิด socket ทันที ไม่รอ message ค้าง
            except Exception:
                pass


def main():
    banner(NODE_NAME, [
        f"SUB :{PORT_VISION_CMD} ({TOPIC_VISION_CMD})  <- capture",
        f"PUB :{PORT_VISION_RESULT} ({TOPIC_VISION_RESULT})  -> color",
        f"model: {MODEL_PATH} (YOLO loads in background ...)",
    ])
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    node = VisionNode(root)
    ready(NODE_NAME)

    def on_close():
        _term("window closed -> shutting down", "STATE")
        node.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
