"""Shared console-logging helper used by every node.

แต่ละ Node ถูกเปิดในหน้าต่าง terminal ของตัวเอง (ดู launch_all.py) helper นี้
ทำให้ทุกหน้าต่างมี log บรรทัดเดียวกันรูปแบบเดียวกัน — มี timestamp + ชื่อ Node
พร้อมแบนเนอร์ตอนเริ่มทำงาน เพื่อให้รู้ได้ทันทีว่า Node กำลังทำงานตั้งแต่เริ่ม start
และเห็นกิจกรรมต่างๆ ของ Node นั้นแบบ real-time บน cmd / powershell

print(..., flush=True) สำคัญมาก: ถ้าไม่ flush เมื่อ stdout ไม่ได้ต่อกับ TTY
(เช่นถูกเปิดผ่าน subprocess) บรรทัด log จะถูก buffer ไว้และโผล่เป็นชุดๆ
หรือไม่โผล่เลยจนกว่าโปรแกรมจะปิด
"""

import datetime
import os
import sys

# console ของ Windows (cmd / powershell) มักตั้ง encoding เป็น cp1252 ทำให้
# print ตัวอักษรไทยหรือสัญลักษณ์ (—, ✓) แล้ว UnicodeEncodeError -> node crash
# ตอน start จึงบังคับ stdout/stderr เป็น UTF-8 และใช้ errors="replace" กันพัง
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def banner(name: str, info_lines: list[str] | None = None) -> None:
    """พิมพ์แบนเนอร์ตอน Node เริ่ม boot — บอกว่า Node กำลังเริ่มทำงาน"""
    bar = "=" * 64
    print(bar, flush=True)
    print(f"  {name}  —  STARTING ...", flush=True)
    print(f"  PID {os.getpid()}   {datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
          flush=True)
    for line in info_lines or []:
        print(f"  {line}", flush=True)
    print(bar, flush=True)


def make_logger(tag: str):
    """คืน function log(msg, level="INFO") ที่พิมพ์บรรทัด log แบบมี timestamp
    และชื่อ Node กำกับ ลงใน console ของ Node นั้น"""
    def log(msg: str, level: str = "INFO") -> None:
        print(f"[{_ts()}] [{tag}] {level:<5} {msg}", flush=True)
    return log


def ready(name: str) -> None:
    """พิมพ์บรรทัดยืนยันว่า Node พร้อมทำงานและกำลังรอ activity แล้ว"""
    print(f"[{_ts()}] [READY] {name} is now RUNNING — waiting for activity ...",
          flush=True)
