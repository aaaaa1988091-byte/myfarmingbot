# maingarden.py — 主程式入口
# 農業邏輯、Tkinter UI、Discord Bot、熱鍵監聽
# 除蟲 / 巡邏 / tablist / AOTE / 寵物 等功能由 garden2 提供

import minescript
import tkinter as tk
import threading
import time
import math
import random
import re

from tkinter import ttk, messagebox
import win32api
import win32gui
import discord
from discord import app_commands
import asyncio

# ── 載入函式庫 ──────────────────────────────────
import garden2 as g2
try:
    import example
except Exception as e:
    example = None
    print(f"無法載入 example.py: {e}")
from garden2 import (
    # 常數
    FARM_RANGE, FARM_ORIGIN, HOE_SLOT, STATUS_INTERVAL,
    MAX_RESET_ATTEMPTS, RESET_BASE_DELAY,
    PET_SWITCH_COOLDOWN,
    # 工具
    release_all, start_mouse_hold, stop_mouse_hold,
    start_rclick_hold, stop_rclick_hold, lclick_once,
    capture_minecraft, clamp, dist3,
    aote_navigate_to, aote_smooth_look,
    # tablist
    tablist_updater, get_tablist_cached,
    # 寵物
    get_current_pet, switch_pet_rod, pet_cd_monitor, pet_name_matches,
    # 除蟲
    PatrolBot, PatrolState,
    # 聊天工具
    _strip_mc, _extract_plot_num,
    # 格式化
    get_pest_info,
)

# ────────────────────────────────────────────────
#  Discord / 頻道設定
# ────────────────────────────────────────────────
report_channel_id = 1282381209472860272
status_channel_id = 1479084761845858406
token = ""
# ────────────────────────────────────────────────
#  共享可變狀態
# ────────────────────────────────────────────────
farm_state     = "off"
farm_dir       = "left"     # "left" = A, "right" = D
player_pos     = None
stuck_count    = 0
reset_attempts = 0
hotkey_vk      = {"toggle": 0x77}   # 預設 F8
stats          = {"start_time": None, "total_seconds": 0, "turn_count": 0, "reset_count": 0}
turn_times     = []
_bot_generation = 0

_pest_busy        = False
_pest_lock        = threading.Lock()
_major_action_lock = threading.Lock()
_chat_pest_enabled = False
_pest_cd_switch_done = False
_gear_switching = False
_pest_cd_last_seen = None
_current_equipment_set = None
_pest_cd_block_logged = False
_last_equipment_switch_at = 0.0
PET_TABLIST_SETTLE_SECONDS = 3.0
EQUIPMENT_SETTLE_SECONDS = 3.0
PEST_SWITCH_THRESHOLD_SEC = 170  # 2m50s
PEST_SWITCH_RESET_SEC = 190       # 回到較高冷卻時，允許下一輪再次切換

target_hwnd = None

# ────────────────────────────────────────────────
#  注入回呼給 garden2（避免循環 import）
# ────────────────────────────────────────────────
def _get_farm_state():   return farm_state
def _get_pest_busy():    return _pest_busy
def _get_patrol_state(): return patrol_bot.state

# patrol_bot 在下方建立後再呼叫 set_callbacks
patrol_bot = PatrolBot()

# ────────────────────────────────────────────────
#  Tkinter UI（先宣告，log / update_button 需要）
# ────────────────────────────────────────────────
C  = {"bg":  "#1a2e1a", "bg2": "#223322", "bg3": "#2e4a2e",
      "fg":  "#e8ff60", "dim": "#7db87d", "acc": "#aaff00",
      "grn": "#39d353", "red": "#ff4f4f", "yel": "#ffe53b", "pur": "#c8ff00"}
FM = ("Consolas", 8)
FL = ("Consolas", 9, "bold")
FB = ("Consolas", 9, "bold")
FT = ("Consolas", 11, "bold")

tk_root = tk.Tk()
tk_root.title("Garden + Pest Bot")
tk_root.geometry("420x500")
tk_root.configure(bg=C["bg"])
tk_root.attributes("-topmost", True)
tk_root.resizable(False, False)

# ── 頂部標題列 ──────────────────────────────────
top = tk.Frame(tk_root, bg=C["bg"], pady=6); top.pack(fill="x", padx=10)
tk.Label(top, text="🌱 Garden + 🐛 Pest Bot",
         font=("Consolas", 12, "bold"), bg=C["bg"], fg=C["acc"]).pack(side="left")
dot_lbl = tk.Label(top, text="●", font=("Consolas", 14, "bold"), bg=C["bg"], fg=C["dim"])
dot_lbl.pack(side="right", padx=2)
tk.Frame(tk_root, bg=C["acc"], height=2).pack(fill="x")

# ── 視窗選擇列 ──────────────────────────────────
wf = tk.Frame(tk_root, bg=C["bg"], pady=5); wf.pack(fill="x", padx=10)
window_cb = ttk.Combobox(wf, width=28, state="readonly", font=FM); window_cb.pack(side="left")
style = ttk.Style(); style.theme_use("clam")
style.configure("TCombobox",
    fieldbackground=C["bg2"], background=C["bg2"], foreground=C["fg"],
    selectbackground=C["bg3"], bordercolor=C["acc"])

def on_refresh():
    wins = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t: wins.append(f"{t} | HWND: {hwnd}")
    win32gui.EnumWindows(cb, None)
    f = [w for w in wins if "Minecraft" in w or "Skyblocker" in w]
    window_cb["values"] = f if f else wins
    if window_cb["values"]: window_cb.current(0)

def on_lock():
    global target_hwnd
    try:
        target_hwnd = int(window_cb.get().split("| HWND: ")[1])
        log(f"鎖定 HWND:{target_hwnd}"); lock_btn.config(bg="#27ae60")
    except:
        messagebox.showerror("錯誤", "解析失敗")

def rnd_btn(parent, text, command, bg, fg, width=9, height=2, font=None):
    f = font or FB
    return tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
        relief="flat", cursor="hand2", font=f, width=width, height=height,
        padx=8, pady=4, borderwidth=0, highlightthickness=2,
        highlightbackground=C["acc"], highlightcolor=C["acc"],
        activebackground=C["acc"], activeforeground="#1a2e1a")

refresh_btn = rnd_btn(wf, "↺", on_refresh, C["bg3"], C["acc"], width=2, height=1)
refresh_btn.pack(side="left", padx=2)
lock_btn = rnd_btn(wf, "Lock", on_lock, C["bg3"], C["yel"], width=5, height=1)
lock_btn.pack(side="left")
tk.Frame(tk_root, bg=C["acc"], height=2).pack(fill="x")

# ── 狀態列 ──────────────────────────────────────
sf = tk.Frame(tk_root, bg=C["bg2"], pady=6, padx=10); sf.pack(fill="x")
pos_var    = tk.StringVar(value="---"); dir_var    = tk.StringVar(value="←A")
time_var   = tk.StringVar(value="00:00:00"); stat_var = tk.StringVar(value="T0 R0")
pest_var   = tk.StringVar(value="IDLE"); area_var  = tk.StringVar(value="---")
server_var = tk.StringVar(value="---"); speed_var  = tk.StringVar(value="---")
ff_var     = tk.StringVar(value="---"); bank_var   = tk.StringVar(value="---")
copper_var = tk.StringVar(value="---"); gems_var   = tk.StringVar(value="---")
pet_var    = tk.StringVar(value="---"); cd_var     = tk.StringVar(value="---")

def _stat_row(parent, label, var):
    f = tk.Frame(parent, bg=C["bg2"]); f.pack(side="left", padx=(0, 14))
    tk.Label(f, text=label, bg=C["bg2"], fg=C["dim"], font=FM).pack(anchor="w")
    tk.Label(f, textvariable=var, bg=C["bg2"], fg=C["fg"],  font=FL).pack(anchor="w")

ra = tk.Frame(sf, bg=C["bg2"]); ra.pack(fill="x", pady=(0, 4))
_stat_row(ra, "POS", pos_var); _stat_row(ra, "DIR", dir_var); _stat_row(ra, "PEST", pest_var)
rb = tk.Frame(sf, bg=C["bg2"]); rb.pack(fill="x", pady=(0, 4))
_stat_row(rb, "TIME", time_var); _stat_row(rb, "STAT", stat_var)
rc = tk.Frame(sf, bg=C["bg2"]); rc.pack(fill="x", pady=(0, 4))
_stat_row(rc, "AREA", area_var); _stat_row(rc, "SRV", server_var)
_stat_row(rc, "PET",  pet_var);  _stat_row(rc, "CD",  cd_var)
rd = tk.Frame(sf, bg=C["bg2"]); rd.pack(fill="x")
_stat_row(rd, "SPD", speed_var); _stat_row(rd, "FF", ff_var)
_stat_row(rd, "BANK", bank_var); _stat_row(rd, "Cu", copper_var); _stat_row(rd, "GEM", gems_var)
tk.Frame(tk_root, bg=C["acc"], height=2).pack(fill="x")

# ── 控制按鈕列（先預留，後面定義函數後再綁定）──
cf = tk.Frame(tk_root, bg=C["bg"], pady=8); cf.pack(padx=10)

# ── 日誌框 ──────────────────────────────────────
lf  = tk.Frame(tk_root, bg=C["bg"], pady=4, padx=8); lf.pack(fill="both", expand=True)
sb  = tk.Scrollbar(lf, bg=C["bg3"], troughcolor=C["bg"], highlightthickness=0, width=8)
sb.pack(side="right", fill="y")
log_box = tk.Text(lf, bg=C["bg2"], fg=C["acc"], font=FM, state="disabled",
                  relief="flat", wrap="none", yscrollcommand=sb.set)
log_box.pack(fill="both", expand=True); sb.config(command=log_box.yview)
tk.Label(tk_root,
    text="Garden+Pest Bot  ·  F8=農業切換  ·  Discord /pest=除蟲  ·  v1.0.0",
    bg=C["bg"], fg=C["dim"], font=("Consolas", 7)).pack(pady=(0, 3))

# ────────────────────────────────────────────────
#  UI 工具函數
# ────────────────────────────────────────────────
def focus_minecraft():
    if target_hwnd:
        try:
            win32gui.SetForegroundWindow(target_hwnd); time.sleep(0.15)
        except:
            pass

def log(msg):
    def _do():
        log_box.config(state="normal")
        ts = time.strftime("%H:%M:%S")
        log_box.insert("end", f"{ts}  {msg}\n")
        log_box.see("end"); log_box.config(state="disabled")
    tk_root.after(0, _do)

def update_button(running: bool):
    if running:
        tk_root.after(0, lambda: (
            toggle_btn.config(text="■ STOP", bg=C["red"], fg="white"),
            dot_lbl.config(text="●", fg=C["grn"])
        ))
    else:
        tk_root.after(0, lambda: (
            toggle_btn.config(text="▶ START", bg=C["grn"], fg="#1a2e1a"),
            dot_lbl.config(text="●", fg=C["dim"])
        ))

def fmt_time(secs):
    h, r = divmod(int(secs), 3600); m, s_ = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s_:02d}"

# 注入回呼給 garden2
g2.set_callbacks(
    get_farm_state   = _get_farm_state,
    get_pest_busy    = _get_pest_busy,
    get_patrol_state = _get_patrol_state,
    log_fn           = log,
    update_button_fn = update_button,
    tk_after_fn      = tk_root.after,
)

# ────────────────────────────────────────────────
#  農業輔助函數
# ────────────────────────────────────────────────
def is_in_farm():
    try:
        x, y, z = (round(c) for c in minescript.player_position())
        return (FARM_RANGE["x"][0] <= x <= FARM_RANGE["x"][1] and
                FARM_RANGE["y"][0] <= y <= FARM_RANGE["y"][1] and
                FARM_RANGE["z"][0] <= z <= FARM_RANGE["z"][1])
    except:
        return False

def wait_for_position(timeout=12, min_delta=0.75, settle_seconds=0.35, poll_interval=0.05):
    """等伺服器真正更新座標並穩定，避免傳送延遲造成誤判。"""
    try:
        start_pos = tuple(minescript.player_position())
    except:
        start_pos = None
    deadline = time.time() + timeout
    last_pos = None
    stable_since = None
    while time.time() < deadline:
        try:
            pos = tuple(minescript.player_position())
        except:
            time.sleep(poll_interval)
            continue
        if start_pos is not None and dist3(pos, start_pos) >= min_delta:
            if last_pos == pos:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= settle_seconds:
                    return True
            else:
                last_pos = pos
                stable_since = None
        time.sleep(poll_interval)
    return False

def stop_farm_keys():
    stop_mouse_hold()
    minescript.player_press_forward(False)
    minescript.player_press_sprint(False)
    minescript.player_press_left(False)
    minescript.player_press_right(False)

def start_farm_keys():
    deadline = time.time() + 5.0
    while _gear_switching and time.time() < deadline:
        time.sleep(0.05)
    if _gear_switching:
        log("農業按鍵啟動被換裝流程阻擋")
        return
    focus_minecraft(); time.sleep(0.1)
    minescript.player_inventory_select_slot(HOE_SLOT); time.sleep(0.1)
    minescript.player_press_forward(True); time.sleep(0.05)
    minescript.player_press_sprint(True);  time.sleep(0.05)
    if farm_dir == "left": minescript.player_press_left(True)
    else:                  minescript.player_press_right(True)
    time.sleep(0.1)
    start_mouse_hold()

def perform_farm_entry_actions():
    minescript.player_press_sneak(True)
    time.sleep(0.5)
    minescript.player_inventory_select_slot(4)
    time.sleep(0.3)
    lclick_once()
    time.sleep(random.uniform(3.0, 4.0))
    lclick_once()
    time.sleep(0.3)
    minescript.player_press_sneak(False)

def _parse_cd_seconds(raw_cd):
    if raw_cd is None:
        return None
    s = str(raw_cd).strip()
    if not s:
        return None
    if "READY" in s.upper():
        return 0
    total = 0
    found = False
    m = re.search(r"(\d+)\s*m", s, re.IGNORECASE)
    if m:
        total += int(m.group(1)) * 60
        found = True
    m = re.search(r"(\d+)\s*s", s, re.IGNORECASE)
    if m:
        total += int(m.group(1))
        found = True
    return total if found else None


def _pet_target_label(target_pet: str) -> str:
    target = (target_pet or "").lower()
    if target == "mosquito":
        return "Mosquito"
    if target == "dragon":
        return "Rose"
    return target_pet


def _normalize_equipment_set(name: str) -> str:
    return (name or "").strip().lower()


def _mark_equipment_set(set_name: str):
    global _current_equipment_set, _last_equipment_switch_at
    _current_equipment_set = _normalize_equipment_set(set_name) or None
    _last_equipment_switch_at = time.time()


def _equipment_settle_remaining() -> float:
    elapsed = time.time() - _last_equipment_switch_at
    return max(0.0, EQUIPMENT_SETTLE_SECONDS - elapsed)


def _is_blossom_equipped() -> bool:
    return _normalize_equipment_set(_current_equipment_set) == "blossom"


def _pet_target_to_equip_set(target_pet: str) -> str:
    target = (target_pet or "").lower()
    if target in {"dragon", "rose", "blossom"}:
        return "blossom"
    if target in {"mosquito", "pest", "pesthunters"}:
        return "pesthunters"
    return "all"



def _normalize_pet_name_from_autopet(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return ""
    low = s.lower()
    if "rose dragon" in low or low.endswith("dragon") or "blossom" in low:
        return "Rose"
    if "mosquito" in low or "pest" in low:
        return "Mosquito"
    return s


def _chat_confirmed_pet_since(start_time: float, target_label: str = "") -> str:
    meta = g2.get_current_pet_meta()
    if meta.get("source") != "chat" or meta.get("updated_at", 0.0) < start_time:
        return ""
    pet = meta.get("name") or ""
    if target_label and not pet_name_matches(pet, target_label):
        return pet
    return pet

def _extract_pet_name_from_chat(clean: str) -> str:
    text = re.sub(r"\s+", " ", clean or " ").strip()
    low = text.lower()
    if not any(key in low for key in ("autopet equipped your", "you summoned your", "you equipped your", "summoned", "equipped")):
        return ""
    m = re.search(r"\[\s*Lvl\s+\d+\s*\]\s*(.+?)(?:!|$)", text, re.IGNORECASE)
    if m:
        return _normalize_pet_name_from_autopet(m.group(1))
    for pat in (
        r"Autopet equipped your\s+(.+?)(?:!|$)",
        r"You (?:summoned|equipped) your\s+(.+?)(?:!|$)",
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return _normalize_pet_name_from_autopet(m.group(1))
    return ""

def _wait_for_current_pet(target_label: str, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    target = (target_label or "").strip().lower()
    while time.time() < deadline:
        cur = get_current_pet()
        if target and pet_name_matches(cur, target_label):
            return True
        time.sleep(0.05)
    return False

def _switch_pet_and_equip(
    target_pet: str,
    reason: str = "",
    resume_farm: bool = False,
    respect_pet_switch_cooldown: bool = True,
) -> bool:
    global _gear_switching
    target_label = _pet_target_label(target_pet)
    current = get_current_pet()
    needs_switch = not pet_name_matches(current, target_label)

    if respect_pet_switch_cooldown and needs_switch and time.time() - g2._pet_switch_cooldown <= PET_SWITCH_COOLDOWN:
        log(f"寵物切換冷卻中，暫停：{target_label}")
        return False

    if example is None:
        minescript.echo("§c[寵物] example.py 無法載入，略過換裝")
        log("example.py 無法載入，略過換裝")
        return False

    _gear_switching = True
    try:
        release_all()
        stop_farm_keys()

        if needs_switch:
            detected = False
            last_pet = current
            for attempt in range(1, 4):
                switch_started = time.time()
                before_pet = get_current_pet()
                switch_pet_rod(reason or f"切換 {target_label}")
                deadline = switch_started + max(4.0, PET_TABLIST_SETTLE_SECONDS + 1.0)
                while time.time() < deadline:
                    chat_pet = _chat_confirmed_pet_since(switch_started, target_label)
                    if chat_pet:
                        last_pet = chat_pet
                        if pet_name_matches(chat_pet, target_label):
                            detected = True
                            minescript.echo(f"§a[寵物] chat 確認 {chat_pet}，開始換裝")
                        else:
                            minescript.echo(f"§e[寵物] chat 確認目前是 {chat_pet}，不是 {target_label}，重試")
                        break
                    if time.time() - switch_started >= PET_TABLIST_SETTLE_SECONDS:
                        tab_pet = get_current_pet()
                        if pet_name_matches(tab_pet, target_label):
                            last_pet = tab_pet
                            detected = True
                            minescript.echo(f"§a[寵物] tablist 確認 {tab_pet}，開始換裝")
                            break
                    time.sleep(0.05)
                if detected:
                    break
                if not last_pet:
                    last_pet = get_current_pet() or "未知"
                minescript.echo(f"§e[寵物] 第 {attempt}/3 次沒有收到 {target_label} 的 chat 確認（目前 {last_pet}），準備重試")
                log(f"寵物切換 chat 未確認目標：attempt={attempt}, target={target_label}, before={before_pet}, after={last_pet}")
                time.sleep(0.25)
            if not detected:
                minescript.echo(f"§c[寵物] 沒有收到 {target_label} 的 chat 確認（目前 {last_pet}），停止換裝避免錯寵物")
                log(f"寵物切換失敗：未收到目標 chat 確認 target={target_label}, current={last_pet}")
                return False
            time.sleep(0.2)

        target_set = _pet_target_to_equip_set(target_pet)
        try:
            example.click_equipment_set(target_set)
            _mark_equipment_set(target_set)
        except Exception as e:
            minescript.echo(f"§c[寵物] 換裝失敗: {e}")
            log(f"換裝失敗: {e}")
            return False

        try:
            example.sell_vinyl()
        except Exception as e:
            minescript.echo(f"§e[寵物] 唱片清理失敗，繼續下一步: {e}")
            log(f"唱片清理失敗: {e}")

    finally:
        _gear_switching = False

    if resume_farm and farm_state == "on":
        start_farm_keys()

    return True


def get_pest_cd_display():
    if not _is_blossom_equipped():
        return "---"
    try:
        ui_cd = cd_var.get()
        if ui_cd and ui_cd != "---":
            return ui_cd
    except Exception:
        pass
    raw_cd = g2.get_tablist_cached().get("pest_cooldown")
    return raw_cd or "---"


def _get_pest_cd_text():
    if not _is_blossom_equipped():
        return None
    try:
        ui_cd = cd_var.get()
        if ui_cd and ui_cd != "---":
            return ui_cd
    except Exception:
        pass
    return g2.get_tablist_cached().get("pest_cooldown")


def pet_cd_monitor():
    global _pest_cd_switch_done, _pest_cd_last_seen, _pest_cd_block_logged
    while True:
        try:
            farm_on = farm_state == "on"
            pest_idle = not _pest_busy
            patrol_ok = patrol_bot.state == PatrolState.IDLE
            action_idle = not g2._pet_switching and not _gear_switching and not major_action_busy()

            if farm_on and not _is_blossom_equipped():
                _pest_cd_last_seen = None
                current_pet = get_current_pet()
                if _pest_cd_switch_done or "Mosquito" in current_pet:
                    if not _pest_cd_block_logged:
                        log("pest cooldown 已切換 Pesthunters，暫停偵測直到重新穿回 blossom 套")
                        _pest_cd_block_logged = True
                    time.sleep(0.5)
                    continue
                if pet_name_matches(current_pet, "Rose"):
                    _mark_equipment_set("blossom")
                    log("pest cooldown 偵測：目前寵物為 Rose，標記 blossom 套並等待狀態穩定")
                    time.sleep(0.5)
                    continue
                _pest_cd_switch_done = False
                if not _pest_cd_block_logged:
                    log("pest cooldown 偵測暫停：目前穿戴不是 blossom 套")
                    _pest_cd_block_logged = True
                time.sleep(0.5)
                continue
            _pest_cd_block_logged = False

            settle_wait = _equipment_settle_remaining()
            if settle_wait > 0:
                time.sleep(min(0.5, settle_wait))
                continue

            raw_cd = _get_pest_cd_text()
            raw_text = str(raw_cd or "")
            cd = _parse_cd_seconds(raw_cd)

            if not (farm_on and pest_idle and patrol_ok and action_idle):
                _pest_cd_switch_done = False
                _pest_cd_last_seen = None
                time.sleep(0.5)
                continue

            if cd is None:
                _pest_cd_switch_done = False
                _pest_cd_last_seen = None
                time.sleep(0.5)
                continue

            current_pet = get_current_pet()
            ready_now = "READY" in raw_text.upper()
            if cd >= PEST_SWITCH_RESET_SEC:
                _pest_cd_switch_done = False
                _pest_cd_last_seen = cd
                time.sleep(0.5)
                continue

            if _pest_cd_last_seen != cd:
                log(f"pest cooldown raw={raw_cd!r}, parsed={cd}")
                _pest_cd_last_seen = cd

            if ready_now and not _pest_cd_switch_done:
                if "Mosquito" not in current_pet:
                    minescript.echo("§e[寵物] pest cooldown READY，切換蚊子+Pesthunters")
                    log("寵物切換：READY，切換蚊子+Pesthunters")
                    if _switch_pet_and_equip(
                        "mosquito",
                        "READY切蚊子",
                        resume_farm=True,
                        respect_pet_switch_cooldown=False,
                    ):
                        _pest_cd_switch_done = True
                else:
                    _pest_cd_switch_done = True
            elif (
                cd <= PEST_SWITCH_THRESHOLD_SEC
                and cd > 0
                and not _pest_cd_switch_done
                and "Mosquito" not in current_pet
            ):
                minescript.echo(f"§e[寵物] pest cooldown 到點（CD={cd}s），切換蚊子+Pesthunters")
                log(f"寵物切換：CD={cd}s 到點，切換蚊子+Pesthunters")
                if _switch_pet_and_equip(
                    "mosquito",
                    "CD切蚊子",
                    resume_farm=True,
                    respect_pet_switch_cooldown=False,
                ):
                    _pest_cd_switch_done = True
        except Exception:
            pass
        time.sleep(0.5)

# ────────────────────────────────────────────────
#  全局停止
# ────────────────────────────────────────────────
def stop_everything(reason="手動停止"):
    global farm_state, _pest_busy, _bot_generation
    _bot_generation += 1
    minescript.echo(f"§c[Bot] 全局停止：{reason} (gen={_bot_generation})")
    log(f"全局停止：{reason}")
    if farm_state == "on":
        farm_state = "off"
        if stats["start_time"]:
            stats["total_seconds"] += int(time.time() - stats["start_time"])
            stats["start_time"] = None
        stop_farm_keys()
        update_button(False)
    patrol_bot.stop()
    release_all()
    stop_mouse_hold(); stop_rclick_hold()
    for fn in [minescript.player_press_forward, minescript.player_press_jump,
               minescript.player_press_sneak]:
        try: fn(False)
        except: pass
    tk_root.after(0, lambda: pest_btn.config(text="🪲 /pest", bg=C["yel"], fg="#1a2e1a"))

# ────────────────────────────────────────────────
#  換向保護
# ────────────────────────────────────────────────
def check_turn_protection():
    global turn_times, farm_state
    now = time.time()
    turn_times.append(now)
    turn_times = [t for t in turn_times if now - t <= 60]
    if len(turn_times) >= 3:
        last3 = sorted(turn_times)[-3:]
        if last3[2] - last3[0] <= 20:
            minescript.echo("§c[保護] 連續換向觸發保護機制，緊急停止！")
            log("⚠️ 保護觸發：連續換向過快")
            turn_times = []
            threading.Thread(target=emergency_hub, daemon=True).start()

def emergency_hub():
    stop_everything("保護觸發")
    minescript.echo("§c[保護] 執行 /hub...")
    minescript.execute("/hub"); time.sleep(3)
    focus_minecraft()
    minescript.player_press_forward(True); time.sleep(2)
    minescript.player_press_forward(False)
    minescript.echo("§e[保護] 已到大廳並向前走"); log("保護：/hub 完成")
    async def _notify():
        ch = bot.get_channel(report_channel_id)
        if ch:
            await ch.send("⚠️ **保護觸發**：連續快速換向，已執行 `/hub` 緊急停止")
    if bot.loop and bot.loop.is_running():
        asyncio.run_coroutine_threadsafe(_notify(), bot.loop)

# ────────────────────────────────────────────────
#  農業監控 & 重置
# ────────────────────────────────────────────────
def farm_monitor():
    global farm_state, player_pos, stuck_count, farm_dir
    my_gen   = _bot_generation
    player_pos = tuple(round(c) for c in minescript.player_position())
    while farm_state == "on" and _bot_generation == my_gen:
        try:
            if _gear_switching or major_action_busy() or _pest_busy:
                stuck_count = 0
                player_pos = tuple(round(c) for c in minescript.player_position())
                time.sleep(0.5)
                continue

            curr = tuple(round(c) for c in minescript.player_position())
            x, y, z = curr
            stuck_count = stuck_count + 1 if player_pos == curr else 0
            player_pos  = curr
            if stuck_count >= 3:
                minescript.echo("§e[偵測] 堵塞，執行換向...")
                stuck_count = 0; stats["turn_count"] += 1
                minescript.player_press_left(False); minescript.player_press_right(False)
                delay = random.uniform(0.3, 1.2); time.sleep(delay)
                farm_dir = "right" if farm_dir == "left" else "left"
                if farm_dir == "left": minescript.player_press_left(True)
                else:                  minescript.player_press_right(True)
                log(f"換向 #{stats['turn_count']} → {farm_dir}")
                check_turn_protection()
            if not (FARM_RANGE["x"][0] <= x <= FARM_RANGE["x"][1] and
                    FARM_RANGE["y"][0] <= y <= FARM_RANGE["y"][1] and
                    FARM_RANGE["z"][0] <= z <= FARM_RANGE["z"][1]):
                minescript.echo("§c超出範圍，重置...")
                if major_action_busy():
                    time.sleep(1)
                    continue
                if not _major_action_lock.acquire(blocking=False):
                    time.sleep(1)
                    continue
                farm_state = "off"; stats["reset_count"] += 1
                threading.Thread(target=reset_and_restart_guarded, args=(my_gen, True), daemon=True).start()
                break
            time.sleep(1)
        except Exception as e:
            print(f"農業監控錯誤: {e}"); break

def reset_and_restart(my_gen):
    global farm_state, reset_attempts
    if _bot_generation != my_gen:
        minescript.echo("§7[重置] 已過期，取消"); return
    stop_farm_keys()
    if stats["start_time"]:
        stats["total_seconds"] += int(time.time() - stats["start_time"])
        stats["start_time"] = None
    if reset_attempts >= MAX_RESET_ATTEMPTS:
        msg = f"⛔ 重置連續失敗 {reset_attempts} 次，Bot 已停止"
        minescript.echo(f"§c{msg}"); log(msg); farm_state = "off"; update_button(False)
        async def _n():
            ch = bot.get_channel(report_channel_id)
            if ch: await ch.send(msg)
        if bot.loop and bot.loop.is_running():
            asyncio.run_coroutine_threadsafe(_n(), bot.loop)
        return
    if reset_attempts > 0:
        wait = min(RESET_BASE_DELAY * (2 ** (reset_attempts - 1)), 120)
        minescript.echo(f"§e[重置] 等待 {wait}s..."); time.sleep(wait)
    if _bot_generation != my_gen:
        minescript.echo("§7[重置] 等待期間被停止，取消"); return
    reset_attempts += 1
    minescript.echo("§e[重置] /warp garden...")
    minescript.execute("/warp garden"); wait_for_position(10); time.sleep(2)
    if _bot_generation != my_gen: return
    if not is_in_farm():
        minescript.execute("/l"); time.sleep(7)
        minescript.execute("/skyblock"); time.sleep(8)
        minescript.execute("/warp garden"); time.sleep(9)
        if _bot_generation != my_gen: return
        if not is_in_farm():
            farm_state = "off"; update_button(False)
            threading.Thread(target=reset_and_restart, args=(my_gen,), daemon=True).start()
            return
    if _bot_generation != my_gen: return
    reset_attempts = 0
    minescript.execute("/plottp 13"); wait_for_position(10); time.sleep(1.5)
    if _bot_generation != my_gen: return
    minescript.echo("§b[重置] AOTE 導航至田道原點...")
    aote_navigate_to(FARM_ORIGIN)
    if _bot_generation != my_gen: return
    perform_farm_entry_actions()
    if _bot_generation != my_gen: return
    farm_state = "on"; stats["start_time"] = time.time(); update_button(True)
    start_farm_keys()
    threading.Thread(target=farm_monitor, daemon=True).start()

# ────────────────────────────────────────────────
#  農業開關
# ────────────────────────────────────────────────
def wait_for_stable_position(stable_seconds=2.0, timeout=15.0, poll_interval=0.1):
    """持續蹲下，直到座標連續 stable_seconds 沒有變化。"""
    deadline = time.time() + timeout
    last_pos = None
    stable_since = None
    minescript.player_press_sneak(True)
    while time.time() < deadline:
        try:
            curr_pos = tuple(round(c, 2) for c in minescript.player_position())
        except:
            time.sleep(poll_interval)
            continue
        if curr_pos == last_pos:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= stable_seconds:
                return True
        else:
            last_pos = curr_pos
            stable_since = None
        time.sleep(poll_interval)
    return False

def major_action_busy():
    return _major_action_lock.locked()

def reset_and_restart_guarded(my_gen, preacquired=False):
    global farm_state, reset_attempts
    owned_lock = preacquired
    try:
        if not owned_lock:
            if not _major_action_lock.acquire(blocking=False):
                minescript.echo("§7[重置] 其他主要動作進行中，取消")
                return
            owned_lock = True
        if _bot_generation != my_gen:
            return

        stop_farm_keys()
        if stats["start_time"]:
            stats["total_seconds"] += int(time.time() - stats["start_time"])
            stats["start_time"] = None

        if reset_attempts >= MAX_RESET_ATTEMPTS:
            msg = f"⛔ 重置連續失敗 {reset_attempts} 次，Bot 已停止"
            minescript.echo(f"§c{msg}")
            log(msg)
            farm_state = "off"
            update_button(False)
            async def _n():
                ch = bot.get_channel(report_channel_id)
                if ch:
                    await ch.send(msg)
            if bot.loop and bot.loop.is_running():
                asyncio.run_coroutine_threadsafe(_n(), bot.loop)
            return

        if reset_attempts > 0:
            wait = min(RESET_BASE_DELAY * (2 ** (reset_attempts - 1)), 120)
            minescript.echo(f"§e[重置] 等待 {wait}s...")
            time.sleep(wait)

        if _bot_generation != my_gen:
            return

        reset_attempts += 1
        minescript.echo("§e[重置] /warp garden...")
        minescript.execute("/warp garden")
        wait_for_position(10)
        time.sleep(2)
        if _bot_generation != my_gen:
            return

        if not is_in_farm():
            minescript.execute("/l")
            time.sleep(7)
            minescript.execute("/skyblock")
            time.sleep(8)
            minescript.execute("/warp garden")
            time.sleep(9)
            if _bot_generation != my_gen:
                return
            if not is_in_farm():
                farm_state = "off"
                update_button(False)
                return reset_and_restart_guarded(my_gen, preacquired=True)

        if _bot_generation != my_gen:
            return

        reset_attempts = 0
        minescript.execute("/plottp 13")
        wait_for_position(10)
        time.sleep(1.5)
        if _bot_generation != my_gen:
            return

        minescript.echo("§b[重置] AOTE 導航至田道原點...")
        aote_navigate_to(FARM_ORIGIN)
        if _bot_generation != my_gen:
            return

        perform_farm_entry_actions()
        if _bot_generation != my_gen:
            return

        farm_state = "on"
        stats["start_time"] = time.time()
        update_button(True)
        start_farm_keys()
        threading.Thread(target=farm_monitor, daemon=True).start()
    finally:
        if owned_lock:
            try:
                _major_action_lock.release()
            except:
                pass

def toggle_farm():
    global farm_state, reset_attempts
    if farm_state == "off":
        if major_action_busy():
            minescript.echo("§e[Bot] 其他主要動作進行中，暫停啟動農業")
            log("農業啟動被主要動作鎖住")
            return
        if not _major_action_lock.acquire(blocking=False):
            minescript.echo("§e[Bot] 啟動農業時被其他任務搶佔")
            log("農業啟動失敗：主要動作鎖被占用")
            return
        try:
            if patrol_bot.state != PatrolState.IDLE:
                minescript.echo("§e[Bot] 除蟲中，先停止再啟動農業")
                patrol_bot.stop(); patrol_bot._done_event.wait(timeout=5)
            reset_attempts = 0; farm_state = "on"
            stats["start_time"] = time.time(); update_button(True)
            # 確保玫瑰龍
            _switch_pet_and_equip("dragon", "開農業切玫瑰龍")
            minescript.execute("/plottp 13"); wait_for_position(10); time.sleep(1.5)
            aote_navigate_to(FARM_ORIGIN)
            if not wait_for_stable_position(2.0, timeout=15.0):
                log("農業起始等待座標穩定逾時")
            perform_farm_entry_actions()
            start_farm_keys()
            threading.Thread(target=farm_monitor, daemon=True).start()
            log("農業啟動")
        finally:
            try:
                _major_action_lock.release()
            except:
                pass
    else:
        stop_everything("F8 停止")

# ────────────────────────────────────────────────
#  /pest 指令（手動 + Discord 共用）
# ────────────────────────────────────────────────
def pest_run():
    global farm_state, _pest_busy
    my_gen = _bot_generation
    owned_major = False
    with _pest_lock:
        if _pest_busy: log("⚠️ /pest 已在執行中"); return
        _pest_busy = True
    try:
        if not _major_action_lock.acquire(blocking=False):
            minescript.echo("§7[/pest] 其他主要動作進行中，取消")
            log("/pest：主要動作忙碌")
            return
        owned_major = True
        was_farming = (farm_state == "on")
        if was_farming:
            minescript.execute("/setspawn"); time.sleep(0.3)
            minescript.echo("§e[/pest] 暫停農業，準備除蟲...")
            log("/pest：暫停農業")
            farm_state = "off"; stop_farm_keys(); update_button(False)
            if stats["start_time"]:
                stats["total_seconds"] += int(time.time() - stats["start_time"])
                stats["start_time"] = None
            time.sleep(0.5)
            if _bot_generation != my_gen: return
            _switch_pet_and_equip(
                "dragon",
                "???????",
                respect_pet_switch_cooldown=False,
            )
        minescript.echo("§b[/pest] 開始除蟲..."); log("/pest：開始除蟲")
        if not patrol_bot.start():
            minescript.echo("§c[/pest] PatrolBot 無法啟動"); return
        patrol_bot._done_event.wait(timeout=300)
        if _bot_generation != my_gen: return
        log("/pest：除蟲完畢")
        if was_farming and _bot_generation == my_gen:
            minescript.echo("§e[/pest] /warp garden 回原位...")
            minescript.execute("/warp garden")
            wait_for_position(12); time.sleep(1.0)
            minescript.player_press_sneak(True)
            time.sleep(random.uniform(1.0, 1.5))
            minescript.player_press_sneak(False); time.sleep(0.3)
            farm_state = "on"; stats["start_time"] = time.time(); update_button(True)
            start_farm_keys()
            threading.Thread(target=farm_monitor, daemon=True).start()
        else:
            minescript.echo("§a[/pest] 除蟲流程結束")
    except Exception as e:
        minescript.echo(f"§c[/pest] 出錯: {e}"); log(f"/pest 出錯: {e}")
    finally:
        if owned_major:
            try:
                _major_action_lock.release()
            except:
                pass
        _pest_busy = False
        tk_root.after(0, lambda: pest_btn.config(text="🪲 /pest", bg=C["yel"], fg="#1a2e1a"))

# ────────────────────────────────────────────────
#  ChatPest（聊天欄 YUCK 自動除蟲）
# ────────────────────────────────────────────────
def chat_pest_run(plot_num):
    global farm_state, _pest_busy
    my_gen = _bot_generation
    with _pest_lock:
        if _pest_busy: log(f"⚠️ chat pest: 已忙碌，跳過 Plot {plot_num}"); return
        _pest_busy = True
    try:
        if not _major_action_lock.acquire(blocking=False):
            minescript.echo(f"§7[ChatPest] 其他主要動作進行中，跳過 Plot {plot_num}")
            log(f"ChatPest：主要動作忙碌 Plot {plot_num}")
            return
        owned_major = True
        delay = random.uniform(5, 10)
        minescript.echo(f"§e[ChatPest] 偵測到 Plot {plot_num} 有蟲，{delay:.1f}s 後暫停農業除蟲")
        log(f"ChatPest：Plot {plot_num}，等待 {delay:.1f}s")
        time.sleep(delay)
        if _bot_generation != my_gen: return
        was_farming = (farm_state == "on")
        if was_farming:
            minescript.execute("/setspawn"); time.sleep(0.3)
            farm_state = "off"; stop_farm_keys(); update_button(False)
            if stats["start_time"]:
                stats["total_seconds"] += int(time.time() - stats["start_time"])
                stats["start_time"] = None
            time.sleep(0.5)
            if _bot_generation != my_gen: return
            _switch_pet_and_equip(
                "dragon",
                "???????",
                respect_pet_switch_cooldown=False,
            )
        minescript.echo(f"§b[ChatPest] 前往 Plot {plot_num} 除蟲...")
        if not patrol_bot.start_single_plot(plot_num):
            minescript.echo("§c[ChatPest] PatrolBot 忙碌"); return
        patrol_bot._done_event.wait(timeout=300)
        if _bot_generation != my_gen: return
        log(f"ChatPest：Plot {plot_num} 完畢")
        if was_farming and _bot_generation == my_gen:
            minescript.echo("§e[ChatPest] /warp garden 回原位...")
            minescript.execute("/warp garden")
            wait_for_position(12); time.sleep(1.0)
            minescript.player_press_sneak(True)
            time.sleep(random.uniform(1.0, 1.5))
            minescript.player_press_sneak(False); time.sleep(0.3)
            farm_state = "on"; stats["start_time"] = time.time(); update_button(True)
            start_farm_keys()
            threading.Thread(target=farm_monitor, daemon=True).start()
            minescript.echo("§a[ChatPest] 農業已恢復")
    except Exception as e:
        minescript.echo(f"§c[ChatPest] 出錯: {e}"); log(f"ChatPest 出錯: {e}")
    finally:
        if owned_major:
            try:
                _major_action_lock.release()
            except:
                pass
        _pest_busy = False
        tk_root.after(0, lambda: pest_btn.config(text="🪲 /pest", bg=C["yel"], fg="#1a2e1a"))

def chat_listener_loop():
    import queue as _queue
    from minescript import EventQueue, EventType
    pat_yuck   = re.compile(r"YUCK", re.IGNORECASE)
    with EventQueue() as eq:
        eq.register_chat_listener()
        minescript.echo("§7[ChatPest] 聊天監聽已啟動")
        while True:
            try:
                ev = eq.get(timeout=2.0)
            except _queue.Empty:
                continue
            except Exception as e:
                minescript.echo(f"§c[ChatPest] EventQueue 錯誤: {e}")
                time.sleep(1.0); continue
            try:
                from minescript import EventType as ET
                if ev.type != ET.CHAT: continue
                raw   = str(ev.message)
                if len(raw) > 300: continue
                clean = _strip_mc(raw)
                pet_name = _extract_pet_name_from_chat(clean)
                if pet_name:
                    g2.set_current_pet(pet_name, source="chat")
                    minescript.echo(f"§7[寵物] chat 確認: {pet_name}（目前: {get_current_pet()}）")
                if not pat_yuck.search(raw): continue
                plot_num = _extract_plot_num(raw)
                if plot_num is None:
                    minescript.echo(f"§e[ChatPest] YUCK 偵測到，但無法解析 Plot 編號"
                                    f"（raw: {raw[:80]}）")
                    continue
                if not _chat_pest_enabled:
                    minescript.echo(f"§7[ChatPest] 開關已關閉，忽略 Plot {plot_num}")
                    continue
                minescript.echo(f"§a[ChatPest] Plot {plot_num} 有蟲！準備除蟲")
                log(f"ChatPest：Plot {plot_num}")
                # 蟲生成 → 一律走共用切寵/換裝確認流程，避免快取寵物資訊誤判而漏切。
                _switch_pet_and_equip(
                    "dragon",
                    "蟲出現切玫瑰龍",
                    respect_pet_switch_cooldown=False,
                )
                threading.Thread(target=chat_pest_run, args=(plot_num,), daemon=True).start()
            except Exception as e:
                minescript.echo(f"§c[ChatPest] 處理訊息出錯: {e}")

# ────────────────────────────────────────────────
#  Discord 推播
# ────────────────────────────────────────────────
def send_status_to_discord():
    async def _send():
        ch = bot.get_channel(status_channel_id)
        if not ch: return
        elapsed = stats["total_seconds"]
        if stats["start_time"] and farm_state == "on":
            elapsed += int(time.time() - stats["start_time"])
        try:   pos = [round(c) for c in minescript.player_position()]
        except: pos = "N/A"
        pp  = patrol_bot.current_plot
        tb  = get_tablist_cached()
        pet_str = f"Lv{tb.get('pet_level')} {tb.get('pet_name')}" if tb.get('pet_name') else "---"
        text = (
            f"```\n{'🔄 除蟲中' if _pest_busy else '🟢 農業中'}\n"
            f"除蟲 : {patrol_bot.state}" + (f" P{pp}" if pp else "") + "\n"
            f"座標 : {pos}\n方向 : {'←L' if farm_dir == 'left' else '→R'}\n"
            f"時間 : {fmt_time(elapsed)}\n換向 : {stats['turn_count']} 次\n重置 : {stats['reset_count']} 次\n"
            f"區域 : {tb.get('area') or '---'}\n伺服器 : {tb.get('server') or '---'}\n"
            f"速度 : {tb.get('speed') or '---'}\n農業幸運 : {tb.get('farming_fortune') or '---'}\n"
            f"寵物 : {pet_str}\n銀行 : {tb.get('bank') or '---'}\n"
            f"銅幣 : {tb.get('copper') or '---'}\n寶石 : {tb.get('gems') or '---'}\n```"
        )
        screenshot = capture_minecraft()
        if screenshot:
            await ch.send(content=text, file=discord.File(screenshot, filename="farm.png"))
        else:
            await ch.send(content=text)
    if bot.loop and bot.loop.is_running():
        asyncio.run_coroutine_threadsafe(_send(), bot.loop)

def status_loop():
    while True:
        time.sleep(STATUS_INTERVAL)
        send_status_to_discord()

# ────────────────────────────────────────────────
#  狀態顯示更新迴圈
# ────────────────────────────────────────────────
def update_status_loop():
    while True:
        def _do():
            try:
                pos = tuple(round(c) for c in minescript.player_position())
                pos_var.set(f"{pos[0]},{pos[1]},{pos[2]}")
            except:
                pos_var.set("---")
            dir_var.set("→R" if farm_dir == "right" else "←L")
            elapsed = stats["total_seconds"]
            if stats["start_time"] and farm_state == "on":
                elapsed += int(time.time() - stats["start_time"])
            time_var.set(fmt_time(elapsed))
            ps = patrol_bot.state; pp = patrol_bot.current_plot
            pest_var.set(f"{ps}" + (f" P{pp}" if pp else ""))
            stat_var.set(f"T{stats['turn_count']} R{stats['reset_count']}")
            tb = get_tablist_cached()
            area_var.set(tb.get("area")   or "---")
            server_var.set(tb.get("server") or "---")
            speed_var.set(str(tb.get("speed")) if tb.get("speed") else "---")
            ff_var.set(str(tb.get("farming_fortune")) if tb.get("farming_fortune") else "---")
            def _fmt(v):
                if not v: return "---"
                if v >= 1_000_000_000: return f"{v/1_000_000_000:.1f}b"
                if v >= 1_000_000:     return f"{v/1_000_000:.1f}m"
                if v >= 1_000:         return f"{v/1_000:.1f}k"
                return str(v)
            bank_var.set(_fmt(tb.get("bank")))
            copper_var.set(_fmt(tb.get("copper")))
            gems_var.set(_fmt(tb.get("gems")))
            pn = tb.get("pet_name"); pl = tb.get("pet_level")
            pet_var.set(f"Lv{pl} {pn}" if pn else "---")
            cd_var.set(get_pest_cd_display())
        tk_root.after(0, _do)
        time.sleep(1)

# ────────────────────────────────────────────────
#  Discord Bot
# ────────────────────────────────────────────────
intents = discord.Intents.default(); intents.message_content = True
bot  = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

@bot.event
async def on_ready():
    await tree.sync()
    threading.Thread(target=status_loop, daemon=True).start()
    log(f"Discord: {bot.user}")

@tree.command(name="chatpest", description="開關聊天欄害蟲自動除蟲")
async def cmd_chatpest(interaction: discord.Interaction):
    global _chat_pest_enabled
    _chat_pest_enabled = not _chat_pest_enabled
    col = "✅" if _chat_pest_enabled else "🔴"
    await interaction.response.send_message(
        f"{col} ChatPest 已{'啟用' if _chat_pest_enabled else '停用'}"
    )

@tree.command(name="start", description="啟動農業 Bot")
async def cmd_start(interaction: discord.Interaction):
    if farm_state == "on":
        await interaction.response.send_message("⚠️ 農業已在運行"); return
    if _pest_busy:
        await interaction.response.send_message("⚠️ 除蟲進行中，請稍後"); return
    threading.Thread(target=toggle_farm, daemon=True).start()
    await interaction.response.send_message("✅ 農業已啟動")

@tree.command(name="stop", description="停止所有動作（農業 + 除蟲）")
async def cmd_stop(interaction: discord.Interaction):
    if farm_state == "off" and patrol_bot.state == PatrolState.IDLE and not _pest_busy:
        await interaction.response.send_message("⚠️ 已全部停止"); return
    threading.Thread(target=stop_everything, args=("Discord /stop",), daemon=True).start()
    await interaction.response.send_message("🛑 已停止所有動作（農業 + 除蟲）")

@tree.command(name="pest", description="暫停農業→除蟲→/plottp→繼續農業")
async def cmd_pest(interaction: discord.Interaction):
    if _pest_busy:
        await interaction.response.send_message("⚠️ /pest 已在執行中"); return
    if patrol_bot.state != PatrolState.IDLE:
        await interaction.response.send_message("⚠️ 除蟲 Bot 忙碌中"); return
    tk_root.after(0, lambda: pest_btn.config(text="🔄 除蟲中", bg="#8b3a3a"))
    threading.Thread(target=pest_run, daemon=True).start()
    await interaction.response.send_message(
        "🪲 `/pest` 已啟動\n```\n① 暫停農業（若啟動中）\n② 除蟲所有 Plot\n"
        "③ /plottp 回程\n④ 恢復農業（若之前啟動）\n```"
    )

# ────────────────────────────────────────────────
#  熱鍵
# ────────────────────────────────────────────────
_hotkey_listening = True

def hotkey_listener():
    prev = 0
    while _hotkey_listening:
        vk = hotkey_vk.get("toggle", 0)
        if vk:
            st = win32api.GetAsyncKeyState(vk)
            if st & 0x8000 and not (prev & 0x8000):
                threading.Thread(target=toggle_farm, daemon=True).start()
            prev = st
        time.sleep(0.1)

def _vk_name(vk):
    names = {
        0x70: "F1", 0x71: "F2", 0x72: "F3", 0x73: "F4",
        0x74: "F5", 0x75: "F6", 0x76: "F7", 0x77: "F8",
        0x78: "F9", 0x79: "F10", 0x7A: "F11", 0x7B: "F12",
        0x1B: "ESC", 0x20: "SPC", 0x0D: "ENT",
    }
    if vk in names:    return names[vk]
    if 0x41 <= vk <= 0x5A: return chr(vk)
    return f"0x{vk:02X}"

# ────────────────────────────────────────────────
#  控制按鈕（綁定在此，因為需要已定義的函數）
# ────────────────────────────────────────────────
toggle_btn = rnd_btn(
    cf, "▶ START",
    lambda: threading.Thread(target=toggle_farm, daemon=True).start(),
    C["grn"], "#1a2e1a", width=9, height=2, font=("Consolas", 10, "bold")
)
toggle_btn.pack(side="left", padx=(0, 4))

pest_btn = rnd_btn(
    cf, "🪲 /pest",
    lambda: (
        pest_btn.config(text="🔄 除蟲中", bg=C["yel"], fg="#1a2e1a"),
        threading.Thread(target=pest_run, daemon=True).start()
    ),
    C["yel"], "#1a2e1a", width=9, height=2
)
pest_btn.pack(side="left", padx=(0, 4))

_waiting_hotkey = False
def _capture_loop():
    global _waiting_hotkey
    while _waiting_hotkey:
        for vk in range(0x08, 0xFF):
            if win32api.GetAsyncKeyState(vk) & 0x8000:
                hotkey_vk["toggle"] = vk; name = _vk_name(vk); _waiting_hotkey = False
                tk_root.after(0, lambda n=name: (
                    hk_btn.config(text=f"HK:{n}", bg="#8e44ad"),
                    log(f"熱鍵→{n}")
                ))
                return
        time.sleep(0.05)

def on_hk():
    global _waiting_hotkey; _waiting_hotkey = True
    hk_btn.config(text="按鍵...", bg="#e67e22")
    threading.Thread(target=_capture_loop, daemon=True).start()

hk_btn = rnd_btn(cf, f"HK:{_vk_name(hotkey_vk['toggle'])}", on_hk,
                 C["bg3"], C["pur"], width=7, height=2)
hk_btn.pack(side="left", padx=(0, 4))

def reset_stats():
    stats["total_seconds"] = 0; stats["turn_count"] = 0; stats["reset_count"] = 0
    if stats["start_time"]: stats["start_time"] = time.time()
    log("統計重置")

rnd_btn(cf, "RST\nSTAT", reset_stats, C["bg3"], C["dim"], width=5, height=2).pack(side="left")

def on_push_status():
    send_status_to_discord(); log("手動推播狀態")

rnd_btn(cf, "📊\nPUSH", on_push_status, C["bg3"], C["acc"], width=5, height=2).pack(side="left", padx=(4, 0))

def toggle_chat_pest():
    global _chat_pest_enabled
    _chat_pest_enabled = not _chat_pest_enabled
    col  = C["grn"]  if _chat_pest_enabled else C["bg3"]
    txt_ = "🐛 ON"  if _chat_pest_enabled else "🐛 OFF"
    fg_  = "#1a2e1a" if _chat_pest_enabled else C["dim"]
    chat_btn.config(text=txt_, bg=col, fg=fg_)
    log(f"ChatPest {'啟用' if _chat_pest_enabled else '停用'}")

chat_btn = rnd_btn(cf, "🐛 OFF", toggle_chat_pest, C["bg3"], C["dim"], width=5, height=2)
chat_btn.pack(side="left", padx=(4, 0))

# ────────────────────────────────────────────────
#  啟動所有背景執行緒
# ────────────────────────────────────────────────
on_refresh()
threading.Thread(target=hotkey_listener,    daemon=True).start()
threading.Thread(target=update_status_loop, daemon=True).start()
threading.Thread(target=tablist_updater,    daemon=True).start()
threading.Thread(target=pet_cd_monitor,     daemon=True).start()
threading.Thread(target=chat_listener_loop, daemon=True).start()
threading.Thread(target=lambda: bot.run(token), daemon=True).start()

log("Garden + Pest Bot 就緒")
tk_root.mainloop()
