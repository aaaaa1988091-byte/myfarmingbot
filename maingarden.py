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
    get_current_pet, switch_pet_rod, pet_cd_monitor,
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
token = "MTE2NDEzMTE4ODczMTAzMTYwMg.G-i_Yc.JJDZZ0WQEE2In1EGTsCwLv_rzv8EiO4dhJLWso"

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
_chat_pest_enabled = False

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

def wait_for_position(timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            pos = minescript.player_position()
            if any(abs(c) > 1 for c in pos): return True
        except:
            pass
        time.sleep(0.5)
    return False

def stop_farm_keys():
    stop_mouse_hold()
    minescript.player_press_forward(False)
    minescript.player_press_sprint(False)
    minescript.player_press_left(False)
    minescript.player_press_right(False)

def start_farm_keys():
    focus_minecraft(); time.sleep(0.1)
    minescript.player_inventory_select_slot(HOE_SLOT); time.sleep(0.1)
    minescript.player_press_forward(True); time.sleep(0.05)
    minescript.player_press_sprint(True);  time.sleep(0.05)
    if farm_dir == "left": minescript.player_press_left(True)
    else:                  minescript.player_press_right(True)
    time.sleep(0.1)
    start_mouse_hold()

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
                farm_state = "off"; stats["reset_count"] += 1
                threading.Thread(target=reset_and_restart, args=(my_gen,), daemon=True).start()
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
    # 蹲下確保落地 + 視角道具修正（兩次）
    minescript.player_press_sneak(True); time.sleep(0.5)
    minescript.player_inventory_select_slot(4); time.sleep(0.3)
    lclick_once()
    time.sleep(random.uniform(3.0, 4.0))
    lclick_once(); time.sleep(0.3)
    minescript.player_press_sneak(False)
    if _bot_generation != my_gen: return
    farm_state = "on"; stats["start_time"] = time.time(); update_button(True)
    start_farm_keys()
    threading.Thread(target=farm_monitor, daemon=True).start()

# ────────────────────────────────────────────────
#  農業開關
# ────────────────────────────────────────────────
def toggle_farm():
    global farm_state, reset_attempts
    if farm_state == "off":
        if patrol_bot.state != PatrolState.IDLE:
            minescript.echo("§e[Bot] 除蟲中，先停止再啟動農業")
            patrol_bot.stop(); patrol_bot._done_event.wait(timeout=5)
        reset_attempts = 0; farm_state = "on"
        stats["start_time"] = time.time(); update_button(True)
        # 確保玫瑰龍
        if "Rose" not in get_current_pet() and \
                time.time() - g2._pet_switch_cooldown > PET_SWITCH_COOLDOWN:
            threading.Thread(target=lambda: switch_pet_rod("開農業切玫瑰龍"), daemon=True).start()
        start_farm_keys()
        threading.Thread(target=farm_monitor, daemon=True).start()
        log("農業啟動")
    else:
        stop_everything("F8 停止")

# ────────────────────────────────────────────────
#  /pest 指令（手動 + Discord 共用）
# ────────────────────────────────────────────────
def pest_run():
    global farm_state, _pest_busy
    my_gen = _bot_generation
    with _pest_lock:
        if _pest_busy: log("⚠️ /pest 已在執行中"); return
        _pest_busy = True
    try:
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
            if _bot_generation != my_gen: return
            minescript.echo("§a[/pest] 恢復農業"); log("/pest：恢復農業")
            farm_state = "on"; stats["start_time"] = time.time(); update_button(True)
            start_farm_keys()
            threading.Thread(target=farm_monitor, daemon=True).start()
        else:
            minescript.echo("§a[/pest] 除蟲流程結束")
    except Exception as e:
        minescript.echo(f"§c[/pest] 出錯: {e}"); log(f"/pest 出錯: {e}")
    finally:
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
            if _bot_generation != my_gen: return
            farm_state = "on"; stats["start_time"] = time.time(); update_button(True)
            start_farm_keys()
            threading.Thread(target=farm_monitor, daemon=True).start()
            minescript.echo("§a[ChatPest] 農業已恢復")
    except Exception as e:
        minescript.echo(f"§c[ChatPest] 出錯: {e}"); log(f"ChatPest 出錯: {e}")
    finally:
        _pest_busy = False
        tk_root.after(0, lambda: pest_btn.config(text="🪲 /pest", bg=C["yel"], fg="#1a2e1a"))

def chat_listener_loop():
    import queue as _queue
    from minescript import EventQueue, EventType
    pat_yuck   = re.compile(r"YUCK", re.IGNORECASE)
    pat_autopet = re.compile(r"Autopet equipped your \[Lvl \d+\] (.+?)!", re.IGNORECASE)
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
                m_ap  = pat_autopet.search(clean)
                if m_ap:
                    minescript.echo(f"§7[寵物] Autopet 確認: {m_ap.group(1).strip()}"
                                    f"（tablist: {get_current_pet()}）")
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
                # 蟲生成 → 切玫瑰龍
                if farm_state == "on":
                    cur = get_current_pet()
                    if "Rose" not in cur and \
                            time.time() - g2._pet_switch_cooldown > PET_SWITCH_COOLDOWN:
                        threading.Thread(
                            target=lambda: switch_pet_rod("蟲出現切玫瑰龍"), daemon=True
                        ).start()
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
            cd_var.set(tb.get("pest_cooldown") or "---")
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
