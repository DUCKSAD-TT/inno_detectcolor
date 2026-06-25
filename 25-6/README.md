# 🦾 The Sorter — Robotic Arm Color Sorter

> **Innovedex Learning Project**
> ระบบแขนกล 4 แกน Servo ที่ใช้ AI (YOLOv8) ตรวจจับสีของวัตถุแบบ Real-time
> แล้วเคลื่อนย้ายวัตถุไปยังช่องจัดเรียงตามสีที่ตรวจพบ
> ควบคุมผ่าน Arduino และสื่อสารระหว่าง Node ด้วย ZeroMQ

---

## 📋 สารบัญ

1. [ภาพรวมโปรเจกต์](#-ภาพรวมโปรเจกต์)
2. [ความต้องการของระบบ](#-ความต้องการของระบบ)
3. [การติดตั้ง Step-by-Step](#-การติดตั้ง-step-by-step)
4. [โครงสร้างไฟล์โปรเจกต์](#-โครงสร้างไฟล์โปรเจกต์)
5. [สถาปัตยกรรม ZMQ](#-สถาปัตยกรรม-zmq)
6. [รายละเอียดแต่ละ Node](#-รายละเอียดแต่ละ-node)
7. [State Machine ของระบบ](#-state-machine-ของระบบ)
8. [การ Teaching — บันทึก Pose](#-การ-teaching--บันทึก-pose)
9. [การ Running — เล่น Sequence](#-การ-running--เล่น-sequence)
10. [การเปิดโปรแกรมด้วย launch_all.py](#-การเปิดโปรแกรมด้วย-launch_allpy)
11. [CSV Naming Convention](#-csv-naming-convention)
12. [การแก้ไขปัญหาเบื้องต้น](#-การแก้ไขปัญหาเบื้องต้น)
13. [ทางเลือก: ใช้ uv แทน Python + pip](#-ทางเลือก-ใช้-uv-แทน-python--pip)

---

## 🔍 ภาพรวมโปรเจกต์

The Sorter คือระบบ Robotic Arm แบบ Multi-Node ที่แต่ละส่วนทำงานเป็น process แยกกัน
และสื่อสารกันด้วย ZeroMQ (ZMQ) — ทำให้ระบบมีความยืดหยุ่นสูง แต่ละส่วนสามารถ
พัฒนา ทดสอบ และ debug ได้อิสระ

```
  วัตถุวางบนจุดหยิบ
         │
         ▼
  [camera_node] ─── YOLO ตรวจสี ──► สีของวัตถุ
         │
         ▼
  [main_decision] ─── ตัดสินใจ ──► เลือก CSV branch ตามสี
         │
         ▼
  [arduino_node] ─── ส่งคำสั่ง ──► Servo เคลื่อนแขนไปวางในช่องสีนั้น
```

**สีที่รองรับ:** 🔴 Red &nbsp; 🔵 Blue &nbsp; 🟢 Green &nbsp; 🟡 Yellow

---

## 💻 ความต้องการของระบบ

| รายการ | ข้อกำหนด |
|---|---|
| **ระบบปฏิบัติการ** | Windows 10 / 11 (64-bit) |
| **Python** | **3.14** (ดาวน์โหลดที่ [python.org](https://www.python.org/downloads/)) |
| **Arduino Board** | Arduino Uno หรือรุ่นที่รัน StandardFirmata ได้ |
| **กล้อง** | Webcam USB หรือ Built-in (รองรับ OpenCV) |
| **YOLO Model** | `4color-detection.pt` — ต้องวางใน root folder |
| **RAM** | 4 GB ขึ้นไป (แนะนำ 8 GB สำหรับ YOLO) |

---

## 🚀 การติดตั้ง Step-by-Step

### ขั้นตอนที่ 1 — ดาวน์โหลดโปรเจกต์

```powershell
# Clone จาก GitHub
git clone https://github.com/Innovedex/innovedex2026sorter.git

# เข้าไปในโฟลเดอร์โปรเจกต์
cd innovedex2026sorter-main
```

หรือดาวน์โหลดเป็น ZIP แล้ว Extract ออกมา จากนั้นเปิด PowerShell ใน folder นั้น

---

### ขั้นตอนที่ 2 — ติดตั้ง Python 3.14

1. ไปที่ [https://www.python.org/downloads/](https://www.python.org/downloads/)
2. ดาวน์โหลด **Python 3.14.x** สำหรับ Windows (64-bit)
3. รัน installer — **ติ๊ก "Add Python to PATH"** ก่อนกด Install
4. ตรวจสอบว่า Python ติดตั้งสำเร็จ:

```powershell
py -3.14 --version
# ควรเห็น: Python 3.14.x
```

---

### ขั้นตอนที่ 3 — สร้าง Virtual Environment

Virtual Environment คือพื้นที่ Python แยกเฉพาะโปรเจกต์นี้
เพื่อไม่ให้ package ชนกับโปรเจกต์อื่นในเครื่อง

```powershell
# สร้าง venv ด้วย Python 3.14
py -3.14 -m venv venv
```

หลังจากรัน จะเกิดโฟลเดอร์ `venv\` ขึ้นใน project folder

---

### ขั้นตอนที่ 4 — Activate Virtual Environment

```powershell
.\venv\Scripts\activate
```

เมื่อ activate สำเร็จ จะเห็น `(venv)` นำหน้า prompt ใน terminal:

```
(venv) C:\path\to\innovedex2026sorter-main>
```

> **💡 ทุกครั้งที่เปิด Terminal ใหม่** ต้อง activate venv ก่อนรัน script เสมอ
> ยกเว้นถ้าใช้ `launch_all.py` ซึ่งจะ activate ให้อัตโนมัติ

---

### ขั้นตอนที่ 5 — ติดตั้ง Dependencies

```powershell
pip install -r requirements.txt
```

รอจนติดตั้งเสร็จ (อาจใช้เวลา 2-5 นาที ขึ้นกับ internet)

**รายละเอียด Package ที่ติดตั้ง:**

| Package | เวอร์ชัน | หน้าที่ในโปรเจกต์ |
|---|---|---|
| `opencv-python` | latest | อ่าน video frame จากกล้อง และวาด bounding box |
| `pyzmq` | latest | ส่ง/รับ message ระหว่าง Node ผ่าน ZeroMQ |
| `pyFirmata2` | latest | สั่งงาน Servo บน Arduino จาก Python |
| `ultralytics` | latest | รัน YOLOv8 model ตรวจจับสีของวัตถุ |
| `customtkinter` | latest | สร้าง UI หน้าต่างที่ดูทันสมัย |
| `pillow` | latest | แปลง frame จาก OpenCV ให้แสดงใน tkinter ได้ |

---

### ขั้นตอนที่ 6 — เตรียม Arduino

**6.1 อัปโหลด StandardFirmata**

StandardFirmata คือ firmware ที่ทำให้ Python สั่งงาน pin ของ Arduino ได้โดยตรง

1. เปิด **Arduino IDE**
2. ไปที่ `File → Examples → Firmata → StandardFirmata`
3. เลือก Board และ Port ที่ถูกต้อง
4. กด **Upload** รอจนขึ้น "Done uploading"

**6.2 ต่อ Servo ตาม Pinout**

```
Arduino Uno
┌──────────────────────────┐
│  Pin 8  ──► S1 (Signal)  │  แกนหมุนฐาน (Base Rotation)
│  Pin 9  ──► S2 (Signal)  │  แกนแขนล่าง (Lower Arm)
│  Pin 10 ──► S3 (Signal)  │  แกนแขนบน  (Upper Arm)
│  Pin 11 ──► S4 (Signal)  │  Gripper (หัวจับ)
│  4.8V     ──► Servo (VCC)  │
│  GND    ──► Servo (GND)  │
└──────────────────────────┘
```

> **⚠️ ข้อควรระวัง:** ถ้า Servo หลายตัวดึงกระแสมาก ควรจ่ายไฟ 5V แยกจาก Power Supply
> ภายนอก แทนการใช้ 5V จาก Arduino โดยตรง และต่อ GND ร่วมกัน

**Servo Limits ที่กำหนดใน zmq_config.py:**

| Servo | Pin | Range (องศา) | หมายเหตุ |
|---|---|---|---|
| S1 | 8 | 0 – 180 | แกนหมุนหลัก |
| S2 | 9 | 0 – 180 | แขนล่าง |
| S3 | 10 | 0 – 180 | แขนบน |
| S4 | 11 | **40 – 85** | Gripper (จำกัดพิเศษ ป้องกันเสียหาย) |

---

### ขั้นตอนที่ 7 — วาง YOLO Model

วางไฟล์ `4color-detection.pt` ไว้ที่ root folder ของโปรเจกต์
(ระดับเดียวกับ `launch_all.py`)

```
innovedex2026sorter-main/
├── 4color-detection.pt   ← วางไว้ที่นี่
├── launch_all.py
└── ...
```

---

## 📁 โครงสร้างไฟล์โปรเจกต์

```
innovedex2026sorter-main/
│
├── launch_all.py              ← เปิด Node ทั้งหมดพร้อมกัน (จุดเริ่มต้นหลัก)
│
├── arduino_node.py            ← Node A: ควบคุม Servo ผ่าน Arduino
├── camera_node.py             ← Node C: กล้อง + YOLO ตรวจจับสี
├── scale_node.py              ← Node S: Slider + Nudge สำหรับ Jog แขนด้วยมือ
├── main_decision_node.py      ← Node M: สมอง — รัน sequence + ตัดสินใจ
├── csv_pose_tester.py         ← Node T: โหลด + ทดสอบไฟล์ CSV pose
│
├── utils/
│   └── zmq_config.py          ← Config กลาง: Ports, Topics, Limits, Home Pose
│
├── data/                      ← เก็บไฟล์ CSV pose (สร้างอัตโนมัติเมื่อ Record)
│   ├── record-1.csv           ← Route 1: ท่าเข้าหยิบวัตถุ
│   ├── record-11.csv          ← Route 1 + Red: ท่าวางในช่อง Red
│   ├── record-12.csv          ← Route 1 + Blue
│   ├── record-13.csv          ← Route 1 + Green
│   ├── record-14.csv          ← Route 1 + Yellow
│   ├── record-2.csv           ← Route 2: ท่าเข้าหยิบวัตถุ
│   └── ...                    ← (ทำซ้ำสำหรับ Route 1-5)
│
├── 4color-detection.pt        ← YOLO model weights (ต้องวางเอง)
├── requirements.txt           ← รายการ Python packages
└── README.md                  ← ไฟล์นี้
```

---

## 🔌 สถาปัตยกรรม ZMQ

### หลักการทำงาน

ZeroMQ (ZMQ) คือ messaging library ที่ทำให้ process คุยกันแบบ Publish/Subscribe
แต่ละ Node เปิด socket รอรับหรือส่ง message โดยไม่ต้องรู้จักกันโดยตรง

**กฎสำคัญ: BIND ก่อน CONNECT เสมอ**

- **BIND** = เปิด socket รอรับ (เหมือนเปิดร้าน)
- **CONNECT** = เชื่อมไปหา socket ที่ bind อยู่ (เหมือนลูกค้าเข้าร้าน)
- ถ้า CONNECT ก่อน BIND → message จะหายไป

---

### แผนภาพการเชื่อมต่อ (rqt_graph style)

> **วิธีอ่าน (แบบเดียวกับ `rqt_graph` ใน ROS):**
> - **1 กล่อง = 1 Node = 1 process** — แต่ละ Node ปรากฏในแผนภาพ "เพียงกล่องเดียว"
>   (โดยเฉพาะ `main_decision_node` ที่เป็นโปรแกรมเดียวรับทั้งหน้าที่ส่งคำสั่งและรับผล
>   ไม่ใช่ 2 โปรเซสแยกกัน)
> - **ลูกศร `──topic @port──►`** = ทิศทางการไหลของข้อมูล (จากผู้ส่ง ไปยังผู้รับ)
> - **BIND** = ฝั่งที่เปิด socket รอ (เปิดพอร์ตนั้นเอง) · **connect** = ฝั่งที่เชื่อมเข้าหา

```
        servo_cmd channel  (port 5555 — arduino_node เป็นผู้ BIND, ที่เหลือ connect เข้า)
  ┌──────────────────┐
  │    scale_node    │──┐
  │  servo_cmd       │  │
  │  PUB connect 5555│  │
  └──────────────────┘  │
  ┌──────────────────┐  │   servo_cmd @5555 (PUB connect)   ┌────────────────────────────┐
  │  csv_pose_tester │──┼──────────────────────────────────►│        arduino_node         │
  │  servo_cmd       │  │                                    │                             │
  │  PUB connect 5555│  │                                    │  servo_cmd    : SUB BIND 5555│
  └──────────────────┘  │                                    │  servo_status : PUB BIND 5556│
  ┌──────────────────┐  │                                    └───────────────┬─────────────┘
  │                  │──┘                                                     │
  │ main_decision_   │◄──────────── servo_status @5556 (SUB connect) ─────────┘
  │     node         │
  │   (Brain)        │
  │ ★ 1 process ★    │──────────── vision_cmd @5557 (PUB connect) ───────────┐
  │                  │                                                        ▼
  │ servo_cmd  →5555 │                                    ┌────────────────────────────┐
  │ servo_status←5556│                                    │        camera_node          │
  │ vision_cmd →5557 │                                    │                             │
  │ color_res  ←5558 │◄──── color_result @5558 (PUB BIND)─│  vision_cmd   : SUB BIND 5557│
  │ (ทุก socket      │           (camera BIND, brain conn)│  color_result : PUB BIND 5558│
  │  เป็น connect)   │                                    └────────────────────────────┘
  └──────────────────┘
```

> `scale_node`, `csv_pose_tester`, และ `main_decision_node` ต่างก็ **PUB → connect** เข้าพอร์ต
> 5555 พร้อมกันได้ เพราะ `arduino_node` เป็นผู้ **BIND** พอร์ตนั้นไว้ฝั่งเดียว (many-publishers → one-binder)

---

### ตาราง ZMQ Port

| Port | Channel | ผู้ BIND | ผู้ CONNECT | ข้อความตัวอย่าง |
|---|---|---|---|---|
| **5555** | `servo_cmd` | arduino_node | scale_node, csv_pose_tester, main_decision | `servo_cmd 90 45 120 50` |
| **5556** | `servo_status` | arduino_node | main_decision | `servo_status IDLE` |
| **5557** | `vision_cmd` | camera_node | main_decision | `vision_cmd capture` |
| **5558** | `color_result` | camera_node | main_decision | `color_result red` |

---

## 🖥️ รายละเอียดแต่ละ Node

---

### Node A — `arduino_node.py` (Hardware Controller)

**หน้าที่:** รับคำสั่งมุมองศาจาก Node อื่นและขับ Servo ไปยังตำแหน่งนั้นแบบ smooth sweep

```
┌─────────────────────────────────────────────┐
│          arduino_node — Servo Driver         │
│                                             │
│  ● Arduino: CONNECTED       STATUS: IDLE    │
│                                             │
│  S1  pin 8   [0–180]                        │
│  ██████████████░░░░░░░░░░  cur: 90°  tgt: 90°│
│  [ −10 ] [ −1 ] [ +1 ] [ +10 ]              │
│                                             │
│  S2  pin 9   [0–180]   ...                  │
│  S3  pin 10  [0–180]   ...                  │
│  S4  pin 11  [30–75]   ...                  │
│                                             │
│  Initialising…                              │
└─────────────────────────────────────────────┘
```

**ฟีเจอร์:**
- **Progress bar** แสดงตำแหน่งปัจจุบัน (cur) และเป้าหมาย (tgt) ของแต่ละ Servo
- **Nudge buttons** `−10` `−1` `+1` `+10` สำหรับขยับ target ทีละน้อย
- **Sweep loop** ทำงานใน background thread — เคลื่อน Servo ทีละ 1°/tick (smooth)
- **Dry-run mode** ถ้าไม่มี Arduino ก็ยังรัน UI ได้ (จำลองการขยับ)

**Ports ที่ใช้:**
- SUB BIND `:5555` รับ `servo_cmd`
- PUB BIND `:5556` ส่ง `servo_status`

**คำสั่งที่รองรับ:**

| คำสั่ง | รูปแบบ | ตัวอย่าง |
|---|---|---|
| ขยับไปยัง pose | `servo_cmd S1 S2 S3 S4` | `servo_cmd 90 45 120 50` |
| กลับ Home | `servo_cmd home` | `servo_cmd home` |
| Emergency Stop | `servo_cmd stop` | `servo_cmd stop` |

---

### Node C — `camera_node.py` (Vision)

**หน้าที่:** แสดง live preview จากกล้องพร้อม bounding box และตรวจจับสีเมื่อถูกขอ

```
┌────────────────────────────────────────────┐
│        Camera Node — YOLOv8                │
│                                            │
│  Cam: [1▼]  Conf: ────●──────  [Test Capture]│
│                                            │
│  ┌─────────────────────────────────────┐   │
│  │                                     │   │
│  │   [Live Video + YOLO bounding box]  │   │
│  │                                     │   │
│  └─────────────────────────────────────┘   │
│                                            │
│  Detected color: RED                       │
│  Live: red (0.92)                          │
└────────────────────────────────────────────┘
```

**ฟีเจอร์:**
- **Live preview** 50fps พร้อม bounding box และ confidence score บน frame
- **Voting system** เก็บ 8 frames แล้วโหวตสีที่พบมากที่สุด (ป้องกัน false positive)
- **Confidence slider** ปรับ threshold YOLO (0.30 – 0.95)
- **Camera selector** สลับ camera index 0–5
- **Test Capture** ปุ่มทดสอบตรวจจับสีด้วยมือ

**Ports ที่ใช้:**
- SUB BIND `:5557` รับ `vision_cmd capture`
- PUB BIND `:5558` ส่ง `color_result red/blue/green/yellow/none`

---

### Node S — `scale_node.py` (Teaching / Manual Jog)

**หน้าที่:** ให้ผู้ใช้ขยับแขนกลด้วยมือและบันทึก pose ลงไฟล์ CSV

```
┌────────────────────────────────────────────────────┐
│              Scale Node — Jog & Record              │
│                                                    │
│  S1  pin 8  [0–180]  ──────●──────  [-1][+1][-10][+10]  45 │
│  S2  pin 9  [0–180]  ────────●────  [-1][+1][-10][+10]  124│
│  S3  pin 10 [0–180]  ──────●──────  [-1][+1][-10][+10]  51 │
│  S4  pin 11 [30–75]  ────●────────  [-1][+1][-10][+10]  37 │
│                                                    │
│  File: record- [1▼] .csv                           │
│                                                    │
│  [   Home   ] [Record Pose] [ Clear File ]         │
│                                                    │
│  Sent → [45, 124, 51, 37]                          │
│                                                    │
│  File preview:                                     │
│  Servo1,Servo2,Servo3,Servo4                       │
│  45,124,51,37                                      │
└────────────────────────────────────────────────────┘
```

**ฟีเจอร์:**
- **Slider** ขยับ Servo แต่ละแกนแบบ real-time
- **Nudge buttons** `−10` `−1` `+1` `+10` สำหรับ fine-tune ทีละน้อย
- **Home** ส่ง Servo ทุกตัวกลับตำแหน่ง Home (90, 90, 90, 50)
- **Record Pose** บันทึก pose ปัจจุบันต่อท้ายไฟล์ CSV ที่เลือก
- **Clear File** ล้างไฟล์ CSV (เหลือแค่ header)
- **File preview** แสดงเนื้อหา CSV ที่เลือกแบบ real-time

**Port ที่ใช้:**
- PUB CONNECT `:5555` ส่ง `servo_cmd`

---

### Node M — `main_decision_node.py` (Brain)

**หน้าที่:** สมองกลางของระบบ — รัน sequence จาก CSV ประสาน Vision และ Hardware

```
┌────────────────────────────────────────────────────────┐
│           THE SORTER  —  Main Decision                 │
│                                                        │
│  Timing                                                │
│  Pose delay (s): [2.0]  IDLE timeout (s): [20.0]  Vision timeout (s): [5.0]│
│                                                        │
│  [Route 1] [Route 2] [Route 3] [Route 4] [Route 5]    │
│                                                        │
│  [>>  ALL RUN  (1→5, skip if no color, home at end) >>]│
│                                                        │
│  [!!!         EMERGENCY STOP          !!!]             │
│                                                        │
│  Status                                                │
│  WAITING_COLOR                                         │
│  Servo: BUSY                                           │
│  Color: red                                            │
│                                                        │
│  Event log:                                            │
│  [12:34:56] Button 1: playing record-1.csv             │
│  [12:34:58] Requesting vision capture ...              │
│  [12:35:00] detected color = red                       │
└────────────────────────────────────────────────────────┘
```

**ฟีเจอร์:**
- **Route 1–5** รัน sequence ทีละปุ่ม
- **ALL RUN** รัน Route 1→5 ต่อเนื่อง ข้ามอัตโนมัติถ้าตรวจสีไม่ได้
- **Emergency STOP** หยุดทันทีและบังคับกลับ Home
- **Timing controls** ปรับ delay ระหว่าง pose, timeout รอ IDLE, timeout รอสี
- **Event log** บันทึกทุกการกระทำพร้อม timestamp

**Ports ที่ใช้:**
- PUB CONNECT `:5555` ส่ง `servo_cmd`
- SUB CONNECT `:5556` รับ `servo_status`
- PUB CONNECT `:5557` ส่ง `vision_cmd`
- SUB CONNECT `:5558` รับ `color_result`

---

### Node T — `csv_pose_tester.py` (Tester)

**หน้าที่:** โหลดและทดสอบไฟล์ CSV ก่อนนำไปใช้จริง

```
┌────────────────────────────────────────┐
│           CSV Pose Tester              │
│                                        │
│  File: [record-1.csv▼]  [Refresh]      │
│  Delay (s): [1.0]                      │
│                                        │
│  #   S1    S2    S3    S4              │
│  1   90    45    120   50              │
│  2   85    50    115   55   ◄ selected │
│  3   80    55    110   60              │
│                                        │
│  [TEST SELECTED] [PLAY ALL] [STOP]     │
│                                        │
│  Current pose: [85, 50, 115, 55]       │
└────────────────────────────────────────┘
```

**Port ที่ใช้:**
- PUB CONNECT `:5555` ส่ง `servo_cmd`

---

## ⚙️ State Machine ของระบบ

เมื่อกด **Route N** ใน `main_decision_node` ระบบจะทำงานตาม state machine นี้:

```
          กด [Route N]
                │
                ▼
    ┌───────────────────────┐
    │      PLAYING_MAIN     │  เล่น record-N.csv
    │  ส่ง pose ทีละ row    │  ส่ง servo_cmd ทุก (delay) วินาที
    └──────────┬────────────┘
               │ จบไฟล์แล้ว
               ▼
    ┌───────────────────────┐
    │      WAITING_IDLE     │  รอ servo_status = IDLE
    │  (timeout: idle_to)   │  ตรวจทุก 50ms
    └──────────┬────────────┘
               │ IDLE แล้ว
               ▼
    ┌───────────────────────┐
    │       CAPTURING       │  ส่ง vision_cmd capture
    │  ขอให้ camera ตรวจสี  │  ไปยัง camera_node
    └──────────┬────────────┘
               │
               ▼
    ┌───────────────────────┐
    │     WAITING_COLOR     │  รอ color_result จาก camera_node
    │  (timeout: vis_to)    │  (8 frames voting ~0.4 วินาที)
    └──────────┬────────────┘
               │
        ┌──────┴──────┐
        │             │
     ได้สี          timeout หรือไม่รู้จักสี
        │             │
        │         ┌───┴──────────────────────┐
        │         │ allow_skip=True (ALL RUN) │──► SKIPPED (ไปปุ่มถัดไป)
        │         │ allow_skip=False          │──► ERROR
        │         └──────────────────────────┘
        ▼
    ┌───────────────────────┐
    │    PLAYING_BRANCH     │  เล่น record-N{1|2|3|4}.csv
    │  ตามสีที่ตรวจพบ       │  ตาม color → 1=red, 2=blue, 3=green, 4=yellow
    └──────────┬────────────┘
               │ จบไฟล์แล้ว
               ▼
    ┌───────────────────────┐
    │      WAITING_IDLE     │  รอ servo กลับ IDLE
    └──────────┬────────────┘
               │
               ▼
             DONE ──────────────────► กลับสู่ IDLE พร้อมรับคำสั่งใหม่
```

**ในกรณี EMERGENCY STOP:**

```
    กด [STOP]
        │
        ▼
    abort_flag.set()  ──► ยกเลิก loop ทุกส่วนทันที
        │
        ▼
    ส่ง "servo_cmd stop" ──► arduino_node บังคับ Home ทันที (override ทุกอย่าง)
```

---

## 🎓 การ Teaching — บันทึก Pose

Teaching คือกระบวนการจัดท่า (pose) ของแขนกลในแต่ละ waypoint และบันทึกลง CSV

### Workflow

```
ขั้นตอนที่ 1: เปิด arduino_node + scale_node
     │
     ▼
ขั้นตอนที่ 2: ใน scale_node เลือกไฟล์ target เช่น "record-1"
     │
     ▼
ขั้นตอนที่ 3: ใช้ Slider หรือปุ่ม Nudge จัด pose ที่ต้องการ
              (มองดู arduino_node เพื่อดู cur/tgt ของแต่ละแกน)
     │
     ▼
ขั้นตอนที่ 4: กด [Record Pose] — บันทึก pose ปัจจุบันลง CSV
     │
     ▼
ขั้นตอนที่ 5: ทำซ้ำข้อ 3-4 จนครบทุก waypoint ของ route นี้
     │
     ▼
ขั้นตอนที่ 6: เปลี่ยนไฟล์เป็น "record-11" แล้วบันทึก branch สำหรับ red
              ทำซ้ำสำหรับ record-12 (blue), record-13 (green), record-14 (yellow)
     │
     ▼
ขั้นตอนที่ 7: ทำซ้ำ route 2–5 (record-2 ถึง record-54)
```

### เคล็ดลับ Teaching

- **เริ่มจาก Home เสมอ** — กด [Home] ก่อนเริ่ม record แต่ละไฟล์
- **บันทึก waypoint ย้อนกลับด้วย** — ถ้าแขนต้องกลับมา ให้ record ท่ากลับด้วย
- **ทดสอบด้วย csv_pose_tester** — ก่อน run จริงควรทดสอบ CSV ทุกไฟล์ก่อน

---

## ▶️ การ Running — เล่น Sequence

เมื่อ record pose ครบแล้ว พร้อม run ระบบอัตโนมัติ

### เปิด Node ตามลำดับ (Manual)

ต้องเปิดตามลำดับนี้เท่านั้น — เพราะ BIND ต้องพร้อมก่อน CONNECT:

```powershell
# Terminal 1 — เปิดก่อนสุด (BIND :5555 และ :5556)
.\venv\Scripts\activate
python arduino_node.py
```

```powershell
# Terminal 2 — เปิดที่สอง (BIND :5557 และ :5558)
.\venv\Scripts\activate
python camera_node.py
```

```powershell
# Terminal 3 — เปิดหลังจาก Node A และ C พร้อมแล้ว
.\venv\Scripts\activate
python scale_node.py
```

```powershell
# Terminal 4 — เปิดสุดท้าย (CONNECT ทุก port)
.\venv\Scripts\activate
python main_decision_node.py
```

### วิธีใช้ Main Decision

1. ตรวจสอบ `Servo: IDLE` แสดงใน status panel
2. ปรับ timing ตามต้องการ (Pose delay, IDLE timeout, Vision timeout)
3. กด **Route N** เพื่อทดสอบทีละ route
4. กด **ALL RUN** เพื่อรันทุก route ต่อเนื่อง (1 → 2 → 3 → 4 → 5)
5. กด **EMERGENCY STOP** ทันทีถ้ามีปัญหา

---

## 🚀 การเปิดโปรแกรมด้วย launch_all.py

เปิดทุก Node ในคลิกเดียว:

```powershell
# ไม่ต้อง activate venv ก่อน — script จะจัดการให้อัตโนมัติ
python launch_all.py
```

`launch_all.py` จะทำสิ่งต่อไปนี้อัตโนมัติ:

```
launch_all.py
     │
     ├── ตรวจว่ามี ./venv หรือไม่
     │       └── ถ้ามี → re-launch ตัวเองด้วย venv python
     │
     ├── เปิด arduino_node.py   (หน้าต่างใหม่)  ← รอ 1 วินาที
     ├── เปิด camera_node.py    (หน้าต่างใหม่)  ← รอ 1 วินาที
     ├── เปิด scale_node.py     (หน้าต่างใหม่)  ← รอ 1 วินาที
     ├── เปิด main_decision.py  (หน้าต่างใหม่)  ← รอ 1 วินาที
     └── เปิด csv_pose_tester.py (หน้าต่างใหม่)
          │
          └── รอทุก Node จบ (หรือกด Ctrl+C เพื่อปิดทั้งหมด)
```

> **หมายเหตุ:** delay 1 วินาทีระหว่าง Node มีไว้เพื่อให้ BIND socket พร้อมก่อน
> CONNECT เข้ามา ถ้าปิดไปเองแต่ละหน้าต่าง `launch_all.py` จะออกตาม

---

## 📄 CSV Naming Convention

```
data/
├── record-{route}.csv              ← ท่าหลัก (ก่อนตรวจสี)
└── record-{route}{color_idx}.csv   ← ท่าสาขา (หลังตรวจสีแล้ว)

color_idx:
  1 = red
  2 = blue
  3 = green
  4 = yellow
```

**ตาราง CSV ทั้งหมดในระบบ (Route 1–5):**

| ไฟล์ | ความหมาย |
|---|---|
| `record-1.csv` | Route 1 — ท่าเข้าหยิบวัตถุ |
| `record-11.csv` | Route 1 — วางในช่อง Red |
| `record-12.csv` | Route 1 — วางในช่อง Blue |
| `record-13.csv` | Route 1 — วางในช่อง Green |
| `record-14.csv` | Route 1 — วางในช่อง Yellow |
| `record-2.csv` | Route 2 — ท่าเข้าหยิบวัตถุ |
| `record-21.csv` → `record-24.csv` | Route 2 สาขาตามสี |
| … | (ทำซ้ำสำหรับ Route 3, 4, 5) |

**โครงสร้างภายใน CSV:**

```csv
Servo1,Servo2,Servo3,Servo4
90,45,120,50
85,50,115,55
80,55,110,60
```

- Row แรก = Header (Servo1–Servo4)
- Row ถัดไป = แต่ละ waypoint (หน่วย: องศา)
- `main_decision_node` จะส่ง pose ทีละ row ด้วย delay ที่กำหนด

---

## 🔧 การแก้ไขปัญหาเบื้องต้น

| ปัญหา | สาเหตุที่เป็นไปได้ | วิธีแก้ |
|---|---|---|
| `Arduino: FAILED` | สาย USB หลวม หรือยังไม่ได้อัปโหลด StandardFirmata | ตรวจสาย → อัปโหลด Firmata ใหม่ |
| กล้องไม่มีภาพ | Camera index ผิด | เปลี่ยน Cam dropdown 0 → 1 → 2 ทีละตัว |
| Servo ไม่ขยับเมื่อกด | `arduino_node` ยังไม่เปิด หรือเปิดทีหลัง | ปิดหมด แล้วเปิด `arduino_node` ก่อนสุด |
| `color_result` ไม่มา | `camera_node` ยังไม่เปิด | เปิด `camera_node` ก่อน `main_decision` |
| ตรวจสีไม่ออก | แสงไม่พอ หรือ Confidence สูงเกินไป | เพิ่มแสง + ลด Conf slider |
| IDLE timeout | Servo เคลื่อนไม่ถึงเป้าทันเวลา | เพิ่มค่า IDLE timeout ใน main_decision |
| Nudge/Slider ขยับแต่มอเตอร์ไม่หมุน | `arduino_node` ยังไม่ CONNECTED | รอ `arduino_node` แสดง CONNECTED ก่อน |
| `ModuleNotFoundError` | ยังไม่ได้ activate venv | รัน `.\venv\Scripts\activate` ก่อน |
| ไฟล์ CSV ไม่พบ | ยังไม่ได้ Record หรือบันทึกผิดชื่อ | ตรวจชื่อไฟล์ใน `data/` folder |

---

## 📐 ปรับแต่งค่าระบบ

ค่าทั้งหมดที่ปรับได้อยู่ใน `utils/zmq_config.py`:

```python
HOME_POSE    = [90, 90, 90, 50]              # ตำแหน่ง Home ของแต่ละ Servo (องศา)
SERVO_LIMITS = [(0,180),(0,180),(0,180),(40,85)]  # (min, max) ต่อ Servo
SWEEP_STEP_DEG = 1.0                         # ความเร็ว sweep (องศา/tick)
SWEEP_TICK_SEC = 0.02                        # ระยะเวลาต่อ tick (วินาที)
```

---

## ⚡ ทางเลือก: ใช้ `uv` แทน Python + pip

[`uv`](https://docs.astral.sh/uv/) คือเครื่องมือจัดการ Python project + dependency
ที่เร็วมาก (เขียนด้วย Rust) ใช้แทนการสร้าง `venv` เองและ `pip install` ได้เลย
โปรเจกต์นี้มี `pyproject.toml` + `uv.lock` อยู่แล้ว จึงพร้อมใช้กับ `uv` ทันที

### 1️⃣ ติดตั้ง `uv`

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2️⃣ เพิ่ม `uv` เข้า PATH

ตัวติดตั้งจะวาง `uv` ไว้ที่ `~/.local/bin` (`%USERPROFILE%\.local\bin` บน Windows)

- **ปิดแล้วเปิด terminal ใหม่** 1 ครั้ง เพื่อให้ PATH อัปเดต
- ตรวจสอบว่าใช้งานได้:

```bash
uv --version
```

> ถ้าพิมพ์ `uv` แล้วยัง "command not found" ให้เพิ่ม path เองชั่วคราว:
> - PowerShell: `$env:Path += ";$env:USERPROFILE\.local\bin"`
> - macOS / Linux: `export PATH="$HOME/.local/bin:$PATH"`

### 3️⃣ เข้าไปที่โฟลเดอร์โปรเจกต์ แล้ว sync dependency

```bash
cd innovedex2026sorter
uv sync
```

`uv sync` จะอ่าน `pyproject.toml` + `uv.lock` แล้ว:
- สร้าง virtual environment ให้อัตโนมัติที่ `.venv/`
- ติดตั้ง Python 3.14 และ dependency ทั้งหมดให้ตรงเวอร์ชันที่ระบุไว้

### 4️⃣ เปิดทุก Node ด้วย `uv run`

```bash
uv run launch_all.py
```

`uv run` จะรัน `launch_all.py` ภายใน environment ของ `uv` โดยไม่ต้อง activate venv เอง
แล้ว `launch_all.py` จะเปิดทั้ง 5 Node แต่ละหน้าต่างของตัวเอง

> **เทียบกับวิธีเดิม:** `python -m venv venv` + `pip install -r requirements.txt`
> + `.\venv\Scripts\activate` + `python launch_all.py`
> ทั้งหมดนี้ยุบเหลือแค่ `uv sync` แล้ว `uv run launch_all.py`

จะรัน Node เดี่ยวๆ ก็ได้เช่นกัน เช่น:

```bash
uv run arduino_node.py
uv run camera_node.py
```

---

*สร้างและดูแลโดยทีม **Innovedex** — สื่อสารเรียนรู้ Robotics & AI สำหรับทุกคน*
