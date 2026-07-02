"""Node 3 — Scale Node.

4 sliders for manual jogging.  Every slider movement publishes a
`servo_cmd <a1> <a2> <a3> <a4>` message → the hardware_node sweeps to that
target, so you can position the arm in real time before recording a pose.

Also includes:
  • +/- 1 and +/- 10 nudge buttons per axis
  • Home button
  • Record button (appends current pose to selected CSV)
  • Clear-file button (with confirmation)
  • Live preview of the selected CSV
"""

import csv
import os
import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk
import zmq

from utils.zmq_config import (
    PORT_SERVO_CMD, TOPIC_SERVO_CMD,
    HOME_POSE, SERVO_LIMITS, SERVO_PINS,
    DATA_DIR, RECORD_OPTIONS, ADDR_CONN,
)
from utils.node_log import banner, make_logger, ready

NODE_NAME = "SCALE NODE — Jog & Record"
# logger กลางของ node นี้ — พิมพ์ log ลง terminal (cmd / powershell) แบบ real-time
_term = make_logger("scale")


class TeachingNode:
    def __init__(self, root: ctk.CTk):
        self.root = root
        root.title("Scale Node — Jog & Record")
        root.geometry("640x780")

        os.makedirs(DATA_DIR, exist_ok=True)

        self.ctx = zmq.Context.instance()
        self.pub = self.ctx.socket(zmq.PUB)
        self.pub.connect(ADDR_CONN.format(port=PORT_SERVO_CMD))

        self.scales   = []
        self.val_lbls = []
        self._build_ui()

    # ---------------- UI ----------------
    def _build_ui(self):
        ctk.CTkLabel(self.root, text="Servo Jog",
                     font=("Tahoma", 20, "bold")).pack(pady=4)

        for i in range(4):
            lo, hi = SERVO_LIMITS[i]
            f = ctk.CTkFrame(self.root, corner_radius=6)
            f.pack(fill="x", padx=8, pady=2)

            ctk.CTkLabel(f, text=f"S{i+1}  pin {SERVO_PINS[i]}  [{lo}–{hi}]",
                         font=("Tahoma", 16)).pack(side="left", padx=(6, 2))

            # Label แสดงค่าปัจจุบันของ slider (อยู่ขวาสุด)
            val_lbl = ctk.CTkLabel(f, text=str(HOME_POSE[i]),
                                   width=36, font=("Consolas", 18, "bold"))
            val_lbl.pack(side="right", padx=(0, 6))

            # lbl=val_lbl จำเป็นเพื่อให้ lambda จำ label ของตัวเองไว้ใน loop
            # (Python closure capture — ถ้าไม่ทำ ทุก slider จะชี้ไป label เดียวกัน)
            sc = ctk.CTkSlider(f, from_=lo, to=hi,
                               number_of_steps=hi - lo,
                               width=220,
                               command=lambda v, lbl=val_lbl: self._on_slider_moved(v, lbl))
            sc.set(HOME_POSE[i])
            sc.pack(side="left", padx=4, pady=4)

            # i=i และ d=delta จำเป็นเพื่อให้แต่ละปุ่มจำ axis และ delta ของตัวเอง
            for txt, delta in [("-1", -1), ("+1", 1), ("-10", -10), ("+10", 10)]:
                ctk.CTkButton(f, text=txt, width=36,
                              font=("Tahoma", 16),
                              command=lambda i=i, d=delta: self._nudge(i, d)
                              ).pack(side="left", padx=1)

            self.scales.append(sc)
            self.val_lbls.append(val_lbl)

        # File select
        sel = ctk.CTkFrame(self.root, fg_color="transparent")
        sel.pack(pady=5)
        ctk.CTkLabel(sel, text="File: record-", font=("Tahoma", 18)).grid(row=0, column=0)
        self.file_var = tk.StringVar(value=RECORD_OPTIONS[0])
        # Keep ttk.Combobox for clean width control
        ttk.Combobox(sel, textvariable=self.file_var,
                     values=RECORD_OPTIONS, width=5,
                     state="readonly").grid(row=0, column=1)
        ctk.CTkLabel(sel, text=".csv", font=("Tahoma", 18)).grid(row=0, column=2)
        # โหลด preview ใหม่ทุกครั้งที่เลือกไฟล์
        self.file_var.trace_add("write", lambda *_: self._load_preview())

        # Action buttons
        btns = ctk.CTkFrame(self.root, fg_color="transparent")
        btns.pack(pady=4)
        ctk.CTkButton(btns, text="Home", width=90,
                      font=("Tahoma", 18, "bold"),
                      command=self._home).grid(row=0, column=0, padx=3)
        ctk.CTkButton(btns, text="Record Pose", width=110,
                      font=("Tahoma", 18, "bold"),
                      command=self._record).grid(row=0, column=1, padx=3)
        ctk.CTkButton(btns, text="Clear File", width=90,
                      font=("Tahoma", 18, "bold"),
                      command=self._clear).grid(row=0, column=2, padx=3)

        # Status label
        self.status_lbl = ctk.CTkLabel(self.root, text="Idle",
                                       font=("Tahoma", 18))
        self.status_lbl.pack(pady=2)

        # Preview
        ctk.CTkLabel(self.root, text="File preview:",
                     font=("Tahoma", 18, "bold")).pack(anchor="w", padx=10)
        self.preview = ctk.CTkTextbox(self.root, height=120, width=440,
                                      font=("Consolas", 16),
                                      state="disabled")
        self.preview.pack(pady=3, padx=8)

        info = f"PUB :{PORT_SERVO_CMD} ({TOPIC_SERVO_CMD})"
        ctk.CTkLabel(self.root, text=info,
                     font=("Consolas", 14)).pack(pady=2)

        self._load_preview()

    # ---------------- Helpers ----------------
    def _set_status(self, text: str):
        """อัปเดต status label ด้วยข้อความที่กำหนด"""
        _term(text, "STATE")
        self.status_lbl.configure(text=text)

    def _on_slider_moved(self, value, label):
        """เรียกเมื่อผู้ใช้ drag slider — อัปเดต label และส่งคำสั่งไปยัง servo"""
        label.configure(text=str(int(value)))
        self._publish()

    def _nudge(self, i, delta):
        """ขยับ servo แกน i ไปอีก delta องศา (จำกัดอยู่ใน SERVO_LIMITS)"""
        lo, hi  = SERVO_LIMITS[i]
        # max(lo, min(hi, ...)) จำกัดค่าไม่ให้เกิน limit: lo <= value <= hi
        new_val = max(lo, min(hi, self.scales[i].get() + delta))
        self.scales[i].set(new_val)
        self.val_lbls[i].configure(text=str(int(new_val)))
        self._publish()

    def _publish(self):
        """ส่ง servo position ปัจจุบันทั้ง 4 แกน ผ่าน ZMQ ไปยัง arduino_node"""
        vals = []
        for i in range(4):
            vals.append(self.scales[i].get())
        msg = f"{TOPIC_SERVO_CMD} {vals[0]} {vals[1]} {vals[2]} {vals[3]}"
        try:
            self.pub.send_string(msg)
        except Exception as e:
            _term(f"pub err: {e}", "ERROR")
        self._set_status(f"Sent → {vals}")

    def _home(self):
        """ส่ง servo ทุกตัวกลับตำแหน่ง HOME และอัปเดต UI"""
        for i in range(4):
            self.scales[i].set(HOME_POSE[i])
            # CTkSlider.set() ไม่ fire command callback → ต้องอัปเดต label เอง
            self.val_lbls[i].configure(text=str(HOME_POSE[i]))
        self._publish()

    def _path(self):
        """คืน path ของไฟล์ CSV ที่เลือกอยู่ตอนนี้"""
        return os.path.join(DATA_DIR, f"record-{self.file_var.get()}.csv")

    def _record(self):
        """บันทึก pose ปัจจุบันต่อท้ายไฟล์ CSV ที่เลือกไว้"""
        vals = []
        for i in range(4):
            vals.append(self.scales[i].get())
        path        = self._path()
        is_new_file = not os.path.isfile(path)
        with open(path, 'a', newline='') as f:
            writer = csv.writer(f)
            if is_new_file:
                writer.writerow(["Servo1", "Servo2", "Servo3", "Servo4"])
            writer.writerow(vals)
        self._set_status(f"Recorded {vals} → {os.path.basename(path)}")
        self._load_preview()

    def _clear(self):
        """ล้างไฟล์ CSV ที่เลือก (เหลือแค่ header row)"""
        path = self._path()
        if not messagebox.askyesno("Confirm",
                                   f"Clear {os.path.basename(path)}?\n"
                                   "(file will be reset to header only)"):
            return
        with open(path, 'w', newline='') as f:
            csv.writer(f).writerow(["Servo1", "Servo2", "Servo3", "Servo4"])
        self._set_status(f"Cleared {os.path.basename(path)}")
        self._load_preview()

    def _load_preview(self):
        """โหลดและแสดงเนื้อหาไฟล์ CSV ที่เลือกในกล่อง preview"""
        path = self._path()
        self.preview.configure(state="normal")
        self.preview.delete(1.0, "end")
        if os.path.isfile(path):
            with open(path) as f:
                self.preview.insert("end", f.read())
        else:
            self.preview.insert("end",
                "(file not found — will be created on Record)")
        self.preview.configure(state="disabled")

    # ---------------- Lifecycle ----------------
    def shutdown(self):
        try:
            self.pub.close(0)   # 0 = linger: ปิด socket ทันที ไม่รอ message ค้าง
        except Exception:
            pass


def main():
    banner(NODE_NAME, [
        f"PUB :{PORT_SERVO_CMD} ({TOPIC_SERVO_CMD})  -> arduino_node",
        f"data dir: {DATA_DIR}",
    ])
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    node = TeachingNode(root)
    ready(NODE_NAME)

    def on_close():
        _term("window closed -> shutting down", "STATE")
        node.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
