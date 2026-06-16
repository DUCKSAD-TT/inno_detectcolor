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
from tkinter import ttk

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
        root.geometry("560x540")

        # ของหนัก — จะถูก initialize ใน background thread (_init_heavy)
        self.model      = None
        self.cap        = None
        self.cam_idx    = DEFAULT_CAMERA_INDEX
        self.confidence = DEFAULT_CONFIDENCE
        self.running    = True
        self.ready      = threading.Event()
        self.cap_lock   = threading.Lock()

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
        self.lbl_video = tk.Label(video_frame, width=400, height=300)
        self.lbl_video.pack()

        # ขึ้น "Loading YOLO model..." ตอนแรก จะถูกอัปเดตใน _init_heavy
        self.lbl_status = ctk.CTkLabel(self.root, text="Loading YOLO model...",
                                       font=("Tahoma", 22, "bold"))
        self.lbl_status.pack(pady=2)

        self.lbl_last = ctk.CTkLabel(self.root, text="Live: starting up…",
                                     font=("Tahoma", 18))
        self.lbl_last.pack()

        info = (f"SUB :{PORT_VISION_CMD} ({TOPIC_VISION_CMD})   "
                f"PUB :{PORT_VISION_RESULT} ({TOPIC_VISION_RESULT})")
        ctk.CTkLabel(self.root, text=info,
                     font=("Consolas", 14)).pack(pady=2)

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
            height, width = frame.shape[:2]
            col_width = width / 5.0

            # Draw slot boundaries (5 columns of equal width)
            for i in range(1, 5):
                x = int(i * col_width)
                cv2.line(frame, (x, 0), (x, height), (255, 255, 255), 1, cv2.LINE_AA)
                cv2.putText(frame, f"Slot {i}", (x - int(col_width / 2) - 20, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(frame, "Slot 5", (width - int(col_width / 2) - 20, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)

            results    = self.model(frame, verbose=False)[0]
            best_label = None
            best_conf  = 0.0

            for box in results.boxes:
                conf = float(box.conf[0])
                if conf >= self.confidence:
                    cls_id = int(box.cls[0])
                    label  = self.model.names[cls_id].lower()

                    # วาด bounding box และ label บน frame
                    coords          = box.xyxy[0]
                    x1, y1, x2, y2 = int(coords[0]), int(coords[1]), int(coords[2]), int(coords[3])
                    
                    cx = (x1 + x2) / 2.0
                    slot_num = int(cx / col_width) + 1
                    slot_num = max(1, min(5, slot_num))
                    
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
                    cv2.putText(frame, f"{label} {conf:.2f} (Slot {slot_num})",
                                (x1, max(y1 - 8, 12)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

                    if label in COLORS and conf > best_conf:
                        best_conf  = conf
                        best_label = f"{label} (Slot {slot_num})"

            if best_label:
                live_label = f"Live: {best_label} ({best_conf:.2f})"
            else:
                live_label = "Live: no color above threshold"

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
    def _capture_slots_from_photo(self):
        """ดึง 1 frame จากกล้อง ตรวจจับวัตถุและวิเคราะห์สีในแต่ละ 5 columns (ซ้ายไปขวา)"""
        ret, frame = self._read_frame()
        slots = ["none"] * 5
        if not ret or frame is None:
            _term("Failed to read frame", "ERROR")
            return slots

        height, width = frame.shape[:2]
        col_width = width / 5.0

        results = self.model(frame, verbose=False)[0]
        slot_confs = [0.0] * 5

        for box in results.boxes:
            conf = float(box.conf[0])
            if conf >= self.confidence:
                cls_id = int(box.cls[0])
                label  = self.model.names[cls_id].lower()
                if label in COLORS:
                    coords = box.xyxy[0]
                    cx     = (float(coords[0]) + float(coords[2])) / 2.0
                    slot_idx = int(cx / col_width)
                    slot_idx = max(0, min(4, slot_idx))
                    
                    if conf > slot_confs[slot_idx]:
                        slot_confs[slot_idx] = conf
                        slots[slot_idx] = label

        _term(f"Photo slot analysis -> {slots}", "VOTE")
        return slots

    def _publish_result(self, slots):
        """ส่งผลสีของทั้ง 5 slots ผ่าน ZMQ และอัปเดต status label"""
        # slots is a list of 5 string colors
        result = " ".join(slots)
        try:
            self.pub.send_string(f"{TOPIC_VISION_RESULT} {result}")
            _term(f"published color_result -> {result}", "PUB")
        except Exception as e:
            _term(f"pub err: {e}", "ERROR")
        self._set_status(f"Detected slots: {result}")

    def _zmq_loop(self):
        while self.running:
            try:
                msg = self.sub.recv_string()
            except zmq.Again:
                continue
            except Exception:
                break
            parts = msg.split()
            if len(parts) >= 2 and parts[1] == CMD_CAPTURE:
                _term("capture command received", "CMD")
                if not self.ready.is_set():
                    # capture ถูกร้องขอก่อน YOLO โหลดเสร็จ — ตอบกลับ ["none"] * 5 เพื่อไม่ให้
                    # main_decision_node ค้างรอ
                    _term("capture requested before model is ready", "WARN")
                    self._publish_result(["none"] * 5)
                    continue
                slots = self._capture_slots_from_photo()
                self._publish_result(slots)

    def _manual_capture(self):
        """กดปุ่ม Test Capture — รัน capture ใน background thread"""
        def do_capture():
            slots = self._capture_slots_from_photo()
            self._publish_result(slots)

        t = threading.Thread(target=do_capture, daemon=True)
        t.start()

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
