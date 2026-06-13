# garden2.py — 功能函式庫（除蟲、巡邏、AOTE、寵物、tablist 等）
# 由 maingarden.py import 使用，請勿單獨執行

import minescript
import threading
import time
import math
import random
import re
import io

from java import JavaClass
from PIL import Image

# ────────────────────────────────────────────────
#  全局常數
# ────────────────────────────────────────────────
FARM_RANGE  = {"x": (-240, -40), "y": (64, 69), "z": (-244, -140)}
FARM_ORIGIN = (-50, 67, -145)          # 田道原點，供 reset_and_restart AOTE 導航使用
HOE_SLOT    = 2
STATUS_INTERVAL    = 30
MAX_RESET_ATTEMPTS = 5
RESET_BASE_DELAY   = 15

TARGET_HEALTH       = 600.0
TARGET_NAME_PATTERN = "Silverfish"
SCAN_RADIUS         = 1000
PLAYER_EYE_HEIGHT   = 1.62
REACTION_DELAY_MIN  = 0.30
REACTION_DELAY_MAX  = 0.80
FOV_THRESHOLD_DEG   = 110.0
JITTER_FREQ         = 1.8
ARRIVE_BOX          = 4.5
STUCK_TIMEOUT       = 3.0
YAW_OK              = 18.0
PITCH_OK            = 22.0
VERT_THRESHOLD      = 2.5
AIM_TICK_SEC        = 0.016
WANDER_BOX_YAW      = 30.0
WANDER_BOX_PITCH    = 20.0
WANDER_NOISE_Y      = 0.20
WANDER_NOISE_P      = 0.10
SPRING_K            = 85.0
SPRING_DAMP         = 19.0
WANDER_INTERVAL_MIN = 1.8
WANDER_INTERVAL_MAX = 4.5
FLY_HEIGHT          = 78.0
FLY_Y_MIN           = 68.0   # 落地保護門檻
VACUUM_SLOT         = 1
PATROL_SNEAK_SEC    = 3.0
PLOT_ARRIVE_TOL     = 1.5
PLOT_STUCK_SEC      = 15.0
PET_SWITCH_COOLDOWN = 4.0    # tablist 3 秒刷新 + 1 秒緩衝

# ────────────────────────────────────────────────
#  共享可變狀態（由 maingarden 讀寫，garden2 輔助讀）
#  maingarden 在 import 後會把自己的 state_ref dict 傳進來
# ────────────────────────────────────────────────
# 這些是 garden2 內部私有狀態
_tablist_cache: dict = {}
_tablist_lock         = threading.Lock()
_pet_switch_cooldown  = 0.0
_pet_switching        = False
_mosquito_switch_done = False   # 本週期是否已切過蚊子
_current_pet_name     = ""
_current_pet_updated_at = 0.0
_current_pet_source   = ""

# maingarden 注入的回呼（避免循環 import）
# maingarden 呼叫 set_callbacks() 完成注入
_cb_get_farm_state   = lambda: "off"
_cb_get_pest_busy    = lambda: False
_cb_get_patrol_state = lambda: PatrolState.IDLE   # 前向宣告，實際在類別定義後使用
_cb_log              = lambda msg: None
_cb_update_button    = lambda running: None
_cb_tk_after         = lambda fn: None

def set_callbacks(
    get_farm_state,
    get_pest_busy,
    get_patrol_state,
    log_fn,
    update_button_fn,
    tk_after_fn,
):
    """由 maingarden.py 在啟動時呼叫，注入所需回呼"""
    global _cb_get_farm_state, _cb_get_pest_busy, _cb_get_patrol_state
    global _cb_log, _cb_update_button, _cb_tk_after
    _cb_get_farm_state   = get_farm_state
    _cb_get_pest_busy    = get_pest_busy
    _cb_get_patrol_state = get_patrol_state
    _cb_log              = log_fn
    _cb_update_button    = update_button_fn
    _cb_tk_after         = tk_after_fn


# ────────────────────────────────────────────────
#  按鍵工具
# ────────────────────────────────────────────────
def release_all():
    for fn in [
        minescript.player_press_forward, minescript.player_press_backward,
        minescript.player_press_left,    minescript.player_press_right,
        minescript.player_press_jump,    minescript.player_press_sneak,
        minescript.player_press_sprint,  minescript.player_press_attack,
        minescript.player_press_use,
    ]:
        try:
            fn(False)
        except:
            pass

def start_mouse_hold():  minescript.player_press_attack(True)
def stop_mouse_hold():   minescript.player_press_attack(False)
def start_rclick_hold(): minescript.player_press_use(True)
def stop_rclick_hold():  minescript.player_press_use(False)

def lclick_once():
    minescript.player_press_attack(True)
    time.sleep(random.uniform(0.04, 0.08))
    minescript.player_press_attack(False)

# ────────────────────────────────────────────────
#  螢幕截圖
# ────────────────────────────────────────────────
def capture_minecraft():
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=True)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf
    except:
        return None

# ────────────────────────────────────────────────
#  數學工具
# ────────────────────────────────────────────────
def clamp(val, lo, hi): return max(lo, min(hi, val))
def angle_diff(a, b):   return (b - a + 180) % 360 - 180

def dist3(a, b):
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)

def perlin_noise_1d(t, seed=0):
    t  = t + seed * 1000.0
    i  = int(t); f = t - i; u = f * f * (3 - 2 * f)
    r0 = random.Random(i).uniform(-1, 1)
    r1 = random.Random(i+1).uniform(-1, 1)
    return r0 + (r1 - r0) * u

def in_fov(player_yaw, target_pos, player_pos_):
    dx = target_pos[0] - player_pos_[0]
    dz = target_pos[2] - player_pos_[2]
    ty = math.degrees(math.atan2(-dx, dz))
    return abs(angle_diff(player_yaw, ty)) <= FOV_THRESHOLD_DEG

def calc_yaw_pitch(from_pos, to_pos, eye_height=PLAYER_EYE_HEIGHT):
    dx = to_pos[0] - from_pos[0]
    dy = to_pos[1] - (from_pos[1] + eye_height)
    dz = to_pos[2] - from_pos[2]
    yaw     = math.degrees(math.atan2(-dx, dz))
    dist_xz = math.sqrt(dx**2 + dz**2)
    pitch   = math.degrees(math.atan2(-dy, dist_xz))
    return yaw, pitch

def get_entity_pos(entity_id, retries=3):
    for attempt in range(retries):
        try:
            for e in minescript.entities(max_distance=SCAN_RADIUS):
                if e and e.id == entity_id and e.position is not None:
                    return tuple(e.position)
        except:
            pass
        if attempt < retries - 1:
            time.sleep(0.05)
    return None

def find_all_silverfish_nearby(max_dist=300):
    try:
        return [
            e for e in minescript.entities(max_distance=max_dist)
            if e and e.position and (
                e.health == TARGET_HEALTH or TARGET_NAME_PATTERN in e.name
            )
        ]
    except:
        return []

# ────────────────────────────────────────────────
#  Tablist 工具
# ────────────────────────────────────────────────
def get_tablist_info():
    data = {
        "area": None, "server": None, "speed": None,
        "farming_fortune": None, "bank": None,
        "copper": None, "gems": None,
        "pet_name": None, "pet_level": None,
        "pest_cooldown": None,
    }

    def _extract_num(text):
        text = text.strip(); n = ""
        for c in text:
            if c.isdigit() or c in ",.":\
                n += c
            elif n:
                break
        if not n: return 0
        val = float(n.replace(",", ""))
        low = text.lower()
        if "b" in low:   val *= 1_000_000_000
        elif "m" in low: val *= 1_000_000
        elif "k" in low: val *= 1_000
        return int(val)

    try:
        with minescript.script_loop:
            mc   = JavaClass("net.minecraft.client.Minecraft").getInstance()
            conn = mc.getConnection()
            if not conn: return data
            for entry in conn.getOnlinePlayers():
                disp = entry.getTabListDisplayName()
                if not disp: continue
                text = disp.getString().strip()
                try:
                    if   "Area:"            in text: data["area"]            = text.split("Area:")[1].strip()
                    elif "Server:"          in text: data["server"]          = text.split("Server:")[1].strip()
                    elif "Speed:"           in text: data["speed"]           = _extract_num(text.split("Speed:")[1])
                    elif "Farming Fortune:" in text: data["farming_fortune"] = _extract_num(text.split("Farming Fortune:")[1])
                    elif "Cooldown:"        in text:
                        import re as _re
                        raw = text.split("Cooldown:")[1].strip()
                        data["pest_cooldown"] = _re.sub(r"§[0-9a-fk-orA-FK-OR]", "", raw).strip()
                    elif "Bank:"   in text: data["bank"]   = _extract_num(text.split("Bank:")[1])
                    elif "Copper:" in text: data["copper"] = _extract_num(text.split("Copper:")[1])
                    elif "Gems:"   in text: data["gems"]   = _extract_num(text.split("Gems:")[1])
                    elif "[Lvl "   in text:
                        ls = text.find("[Lvl "); le = text.find("]", ls)
                        if le != -1:
                            data["pet_level"] = int(text[ls+5:le])
                            data["pet_name"]  = text[le+2:].strip()
                except:
                    continue
    except:
        pass
    return data

def tablist_updater():
    """背景執行緒：持續更新 tablist 快取"""
    while True:
        try:
            info = get_tablist_info()
            with _tablist_lock:
                _tablist_cache.update(info)
            pet_name = info.get("pet_name") or ""
            if pet_name:
                set_current_pet(pet_name, source="tablist")
        except:
            pass
        time.sleep(0.1)

def get_tablist_cached() -> dict:
    with _tablist_lock:
        return _tablist_cache.copy()

def normalize_pet_name(name: str) -> str:
    """把 tablist/chat 裡不同格式的寵物名稱正規化，方便切換偵測比對。"""
    s = re.sub(r"§[0-9a-fk-orA-FK-OR]", "", str(name or "")).strip()
    s = re.sub(r"^\[?Lvl\s+\d+\]?\s*", "", s, flags=re.IGNORECASE).strip()
    low = s.lower()
    if "mosquito" in low or "pest" in low:
        return "Mosquito"
    if "rose dragon" in low or "rose" in low or "dragon" in low or "blossom" in low:
        return "Rose"
    return s

def pet_name_matches(current: str, target: str) -> bool:
    cur = normalize_pet_name(current).lower()
    tgt = normalize_pet_name(target).lower()
    return bool(tgt and (tgt in cur or cur in tgt))

def set_current_pet(name: str, source: str = "manual"):
    global _current_pet_name, _current_pet_updated_at, _current_pet_source
    normalized = normalize_pet_name(name)
    if not normalized:
        return
    _current_pet_name = normalized
    _current_pet_updated_at = time.time()
    _current_pet_source = source

def get_current_pet_meta() -> dict:
    return {
        "name": _current_pet_name,
        "updated_at": _current_pet_updated_at,
        "source": _current_pet_source,
    }

def wait_for_pet_detection(target: str = "", previous: str = "", timeout: float = 5.0, poll_interval: float = 0.1) -> bool:
    """等待 tablist/chat 確認寵物切換。target 有值時等待指定寵物；否則等待寵物與 previous 不同。"""
    deadline = time.time() + timeout
    prev_norm = normalize_pet_name(previous).lower()
    while time.time() < deadline:
        cur = get_current_pet()
        if target:
            if pet_name_matches(cur, target):
                return True
        else:
            cur_norm = normalize_pet_name(cur).lower()
            if cur_norm and cur_norm != prev_norm:
                return True
        time.sleep(poll_interval)
    return False

def _parse_cd_seconds(cd_str):
    """解析 CD 字串為秒數，READY 回傳 0，解析失敗回傳 None"""
    if not cd_str: return None
    s = cd_str.strip()
    if "READY" in s.upper(): return 0
    total = 0; found = False
    m = re.search(r"(\d+)\s*m", s, re.IGNORECASE)
    if m: total += int(m.group(1)) * 60; found = True
    m = re.search(r"(\d+)\s*s", s, re.IGNORECASE)
    if m: total += int(m.group(1)); found = True
    return total if found else None

# ────────────────────────────────────────────────
#  寵物切換
# ────────────────────────────────────────────────
def get_current_pet() -> str:
    cached = _current_pet_name.strip()
    if cached:
        return cached
    return get_tablist_cached().get("pet_name") or ""

def switch_pet_rod(reason=""):
    """切欄位 4（index=3）右鍵一下切換寵物"""
    global _pet_switching, _pet_switch_cooldown
    if _pet_switching: return
    _pet_switching = True
    try:
        was_farming = (_cb_get_farm_state() == "on")
        if was_farming:
            minescript.player_press_attack(False)
            time.sleep(random.uniform(0.05, 0.1))
        minescript.player_inventory_select_slot(3)
        time.sleep(random.uniform(0.1, 0.15))
        minescript.player_press_use(True)
        time.sleep(random.uniform(0.04, 0.08))
        minescript.player_press_use(False)
        _pet_switch_cooldown = time.time()
        if reason:
            minescript.echo(f"§b[寵物] {reason} → 切換完成")
        # 不要清空目前寵物快取：tablist/chat 可能延遲，清空會讓後續偵測完全失去基準。
        time.sleep(random.uniform(0.1, 0.15))
        minescript.player_inventory_select_slot(2)
        if was_farming:
            time.sleep(random.uniform(0.05, 0.1))
            minescript.player_press_attack(True)
    finally:
        _pet_switching = False

def pet_cd_monitor():
    """背景執行緒：CD≤10s → 切蚊子"""
    global _mosquito_switch_done
    while True:
        try:
            raw_cd = get_tablist_cached().get("pest_cooldown")
            cd     = _parse_cd_seconds(raw_cd)
            farm_on   = _cb_get_farm_state() == "on"
            pest_idle = not _cb_get_pest_busy()
            patrol_ok = _cb_get_patrol_state() == PatrolState.IDLE
            if farm_on and pest_idle and patrol_ok and not _pet_switching:
                if cd is None:
                    time.sleep(0.5); continue
                if cd > 10:
                    _mosquito_switch_done = False
                elif cd == 0:
                    if not _mosquito_switch_done:
                        cur = get_current_pet()
                        if "Mosquito" not in cur and time.time() - _pet_switch_cooldown > PET_SWITCH_COOLDOWN:
                            _mosquito_switch_done = True
                            _cb_log("寵物切換：蚊子（READY）")
                            threading.Thread(target=lambda: switch_pet_rod("READY切蚊子"), daemon=True).start()
                        elif "Mosquito" in cur:
                            _mosquito_switch_done = True
                elif cd <= 10 and not _mosquito_switch_done:
                    cur = get_current_pet()
                    if "Mosquito" not in cur and time.time() - _pet_switch_cooldown > PET_SWITCH_COOLDOWN:
                        _mosquito_switch_done = True
                        _cb_log(f"寵物切換：蚊子（CD={cd}s）")
                        threading.Thread(target=lambda: switch_pet_rod("CD切蚊子"), daemon=True).start()
                    elif "Mosquito" in cur:
                        _mosquito_switch_done = True
        except Exception:
            pass
        time.sleep(0.5)

# ────────────────────────────────────────────────
#  Plot 位置表
# ────────────────────────────────────────────────
_MIN_X, _MAX_X = -239, 239
_MIN_Z, _MAX_Z = -239, 239
_SZ   = (_MAX_X - _MIN_X) / 5
_HALF = _SZ / 2
_LAYOUT = [
    [21, 13,  9, 14, 22],
    [15,  5,  1,  6, 16],
    [10,  2, 25,  3, 11],
    [17,  7,  4,  8, 18],
    [23, 19, 12, 20, 24],
]
PLOT_CENTERS: dict = {}
for _r, _row in enumerate(_LAYOUT):
    for _c, _plot in enumerate(_row):
        PLOT_CENTERS[_plot] = (_MIN_X + _c * _SZ + _HALF, _MIN_Z + _r * _SZ + _HALF)

# ────────────────────────────────────────────────
#  Tablist 害蟲資訊
# ────────────────────────────────────────────────
def get_pest_info():
    try:
        with minescript.script_loop:
            mc   = JavaClass("net.minecraft.client.Minecraft").getInstance()
            conn = mc.getConnection()
            if not conn: return 0, []
            pest_count = 0; pest_plots = []
            found_alive = False; found_plots = False
            for player in conn.getOnlinePlayers():
                if found_alive and found_plots: break
                disp = player.getTabListDisplayName()
                if not disp: continue
                text = disp.getString()
                if not found_alive and "Alive" in text:
                    m = re.search(r"Alive:\s*(\d+)", text)
                    if m: pest_count = int(m.group(1)); found_alive = True
                if not found_plots and "Plots" in text:
                    m = re.search(r"Plots:\s*(.*)", text)
                    if m:
                        pest_plots  = [p.strip() for p in m.group(1).split(",") if p.strip()]
                        found_plots = True
            return pest_count, pest_plots
    except Exception as e:
        minescript.echo(f"§c[Patrol] tablist 讀取失敗: {e}")
        return 0, []

# ────────────────────────────────────────────────
#  AOTE 平滑視角 & 導航
# ────────────────────────────────────────────────
def aote_smooth_look(ty, tp, steps=35):
    sy, sp = minescript.player_orientation()
    dy  = (ty - sy + 180) % 360 - 180; dp = tp - sp
    cx  = sy + dy * 0.45 + random.uniform(-3,  3)
    cpx = sp + dp * 0.45 + random.uniform(-1.5, 1.5)
    for i in range(1, steps + 1):
        t   = i / steps; te = t * t * (3 - 2 * t)
        jy  = perlin_noise_1d(t * 4 + random.random() * 0.01, 0) * 0.15
        jp  = perlin_noise_1d(t * 4 + random.random() * 0.01, 1) * 0.07
        curr_y = (1-te)**2 * sy + 2*(1-te)*te * cx  + te**2 * ty  + jy
        curr_p = (1-te)**2 * sp + 2*(1-te)*te * cpx + te**2 * tp  + jp
        minescript.player_set_orientation(curr_y, curr_p)
        time.sleep(0.007)

def wait_for_position_change(reference_pos=None, timeout=3.0, min_delta=0.75, poll_interval=0.05):
    """等待伺服器座標真的更新，避免傳送延遲造成誤判。"""
    if reference_pos is None:
        try:
            reference_pos = minescript.player_position()
        except:
            reference_pos = None
    if reference_pos is None:
        return None

    deadline = time.time() + timeout
    ref = tuple(reference_pos)
    while time.time() < deadline:
        try:
            curr = tuple(minescript.player_position())
        except:
            time.sleep(poll_interval)
            continue
        if math.sqrt((curr[0] - ref[0]) ** 2 + (curr[1] - ref[1]) ** 2 + (curr[2] - ref[2]) ** 2) >= min_delta:
            return curr
        time.sleep(poll_interval)
    return None

def wait_for_position_stable(stable_seconds=0.4, timeout=3.0, poll_interval=0.05):
    """等待座標在傳送後穩定下來。"""
    deadline = time.time() + timeout
    last = None
    stable_since = None
    while time.time() < deadline:
        try:
            curr = tuple(minescript.player_position())
        except:
            time.sleep(poll_interval)
            continue
        if curr == last:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= stable_seconds:
                return True
        else:
            last = curr
            stable_since = None
        time.sleep(poll_interval)
    return False

def _at_farm_origin(pos):
    """田道原點到達判定"""
    return int(pos[0]) <= -49 and int(pos[1]) == 67 and int(pos[2]) in (-145, -144)

def aote_navigate_to(goal, aote_slot=0, arrive_dist=0.9, max_iter=200):
    tx, ty_g, tz  = goal[0] + 0.5, goal[1] + 0.1, goal[2] + 0.5
    use_origin_check = (goal == FARM_ORIGIN)
    stuck = 0
    minescript.echo(f"§b[AOTE] 導航至 ({int(tx)},{int(ty_g)},{int(tz)})...")
    for _ in range(max_iter):
        curr = minescript.player_position()
        if use_origin_check and _at_farm_origin(curr):
            minescript.echo("§a[AOTE] 到達田道原點 ✓"); return True
        dx_ = tx  - curr[0]
        dy_ = ty_g - (curr[1] + 1.62)
        dz_ = tz  - curr[2]
        dist = math.sqrt(dx_**2 + (ty_g - curr[1])**2 + dz_**2)
        if not use_origin_check and dist < arrive_dist:
            confirm_start = time.time(); confirm_dur = random.uniform(0.5, 1.0)
            minescript.echo(f"§a[AOTE] 接近目標，確認站穩 {confirm_dur:.1f}s...")
            while time.time() - confirm_start < confirm_dur:
                c2 = minescript.player_position()
                d2 = math.sqrt((tx-c2[0])**2 + (ty_g-c2[1])**2 + (tz-c2[2])**2)
                if d2 > arrive_dist * 2.5: break
                time.sleep(0.05)
            else:
                minescript.echo("§a[AOTE] 到達目標 ✓"); return True
        yaw   = math.degrees(math.atan2(-dx_, dz_))
        pitch = math.degrees(math.atan2(-dy_, math.sqrt(dx_**2 + dz_**2)))
        aote_smooth_look(yaw, pitch)
        minescript.player_inventory_select_slot(aote_slot)
        time.sleep(random.uniform(0.02, 0.05))
        minescript.player_press_use(True); time.sleep(random.uniform(0.02, 0.04))
        minescript.player_press_use(False)
        new_curr = wait_for_position_change(curr, timeout=2.5, min_delta=0.6) or minescript.player_position()
        moved = math.sqrt(
            (new_curr[0]-curr[0])**2 + (new_curr[1]-curr[1])**2 + (new_curr[2]-curr[2])**2
        )
        if moved < 0.3:
            stuck += 1
            if stuck >= 2:
                side_yaw = yaw + random.choice([-90, 90]) + random.uniform(-15, 15)
                aote_smooth_look(side_yaw, random.uniform(3, 6))
                minescript.player_press_use(True); time.sleep(random.uniform(0.02, 0.04))
                minescript.player_press_use(False)
                stuck = 0; time.sleep(random.uniform(0.08, 0.15))
        else:
            stuck = 0
    minescript.echo("§c[AOTE] 導航逾時"); return False

# ────────────────────────────────────────────────
#  WanderingAimer
# ────────────────────────────────────────────────
class WanderingAimer:
    def __init__(self, seed):
        self._seed = seed
        self._aim_yaw = self._aim_pitch = None
        self._next_refresh = 0.0
        self._py = self._pp = None
        self._vy = self._vp = 0.0
        self._lt = None

    def _new_target(self, bug_yaw, bug_pitch):
        self._aim_yaw   = bug_yaw   + random.uniform(-WANDER_BOX_YAW   * 0.85, WANDER_BOX_YAW   * 0.85)
        self._aim_pitch = bug_pitch + random.uniform(-WANDER_BOX_PITCH  * 0.75, WANDER_BOX_PITCH  * 0.75)
        self._next_refresh = time.time() + random.uniform(WANDER_INTERVAL_MIN, WANDER_INTERVAL_MAX)

    def tick(self, cy, cp, target_pos, p_pos):
        bug_yaw, bug_pitch = calc_yaw_pitch(p_pos, target_pos)
        now = time.time()
        if self._aim_yaw is None or now >= self._next_refresh:
            self._new_target(bug_yaw, bug_pitch)
        if abs(angle_diff(cy, bug_yaw)) > WANDER_BOX_YAW or abs(bug_pitch - cp) > WANDER_BOX_PITCH:
            self._new_target(bug_yaw, bug_pitch)
        if self._py is None:
            self._py = cy; self._pp = cp; self._vy = self._vp = 0.0; self._lt = now
        dt = clamp(now - self._lt, 0.001, 0.05); self._lt = now
        noise_y = perlin_noise_1d(now * JITTER_FREQ,       self._seed)     * WANDER_NOISE_Y
        noise_p = perlin_noise_1d(now * JITTER_FREQ * 1.3, self._seed + 1) * WANDER_NOISE_P
        ty_ = self._aim_yaw + noise_y; tp_ = self._aim_pitch + noise_p
        fy  = angle_diff(self._py, ty_) * SPRING_K - self._vy * SPRING_DAMP
        fp  = (tp_ - self._pp)          * SPRING_K - self._vp * SPRING_DAMP
        self._vy += fy * dt; self._vp += fp * dt
        self._py += self._vy * dt; self._pp += self._vp * dt
        return self._py, clamp(self._pp, -90, 90)

    def reset(self):
        self._aim_yaw = self._aim_pitch = None
        self._next_refresh = 0.0
        self._py = self._pp = None
        self._vy = self._vp = 0.0
        self._lt = None

# ────────────────────────────────────────────────
#  PatrolBot（除蟲核心）
# ────────────────────────────────────────────────
class PatrolState:
    IDLE       = "IDLE"
    TRAVELING  = "TRAVELING"
    VACUUMING  = "VACUUMING"


class PatrolBot:
    def __init__(self):
        self.state        = PatrolState.IDLE
        self._stop_flag   = False
        self._lock        = threading.Lock()
        self._aimer       = WanderingAimer(random.randint(0, 9999))
        self.current_plot = None
        self.last_plot    = None
        self._done_event  = threading.Event()

    # ── 公開介面 ─────────────────────────────
    def start_single_plot(self, plot_num):
        with self._lock:
            if self.state != PatrolState.IDLE: return False
            release_all(); stop_rclick_hold()
            self._stop_flag = False; self._done_event.clear()
            threading.Thread(target=self._single_plot_loop, args=(plot_num,), daemon=True).start()
            return True

    def start(self):
        with self._lock:
            if self.state != PatrolState.IDLE: return False
            release_all(); stop_rclick_hold()
            self._stop_flag = False; self._done_event.clear()
            threading.Thread(target=self._patrol_loop, daemon=True).start()
            return True

    def toggle(self):
        with self._lock:
            if self.state != PatrolState.IDLE: self._do_stop()
            else:
                self._stop_flag = False; self._done_event.clear()
                threading.Thread(target=self._patrol_loop, daemon=True).start()

    def stop(self):
        with self._lock: self._do_stop()

    def _do_stop(self):
        self._stop_flag = True
        release_all(); stop_rclick_hold()
        for fn in [minescript.player_press_forward, minescript.player_press_jump,
                   minescript.player_press_sneak]:
            try: fn(False)
            except: pass

    # ── 單 Plot 迴圈 ─────────────────────────
    def _single_plot_loop(self, plot_num):
        release_all(); stop_rclick_hold()
        self.state = PatrolState.TRAVELING
        pending     = [plot_num]
        retry_count = {}
        try:
            while pending and not self._stop_flag:
                current = pending.pop(0)
                retries = retry_count.get(current, 0)
                if retries == 0:
                    minescript.echo(f"§d[Patrol] 單 Plot 除蟲: {current}")
                else:
                    minescript.echo(f"§e[Patrol] Plot {current} 重試第 {retries} 次...")
                self.current_plot = current
                self._travel_to_plot(current)
                if self._stop_flag: break
                self.state  = PatrolState.VACUUMING
                result = self._vacuum_all_in_plot()
                if result == "retry":
                    minescript.echo(f"§e[Patrol] Plot {current} 需要重試...")
                    retry_count[current] = retries + 1
                    pending.insert(0, current)
                    self.state = PatrolState.TRAVELING
                    continue
                retry_count.pop(current, None)
                minescript.echo(f"§a[Patrol] Plot {current} 清除完畢")
                if not self._stop_flag:
                    _, pest_plots = get_pest_info()
                    extra = [
                        int(p) for p in pest_plots
                        if p.strip().isdigit() and int(p) != 25 and int(p) not in pending
                    ]
                    if extra:
                        minescript.echo(f"§e[Patrol] tablist 發現殘蟲 Plot: {extra}，繼續清...")
                        pending.extend(extra)
                    else:
                        minescript.echo("§a[Patrol] tablist 確認無殘蟲，除蟲完畢")
        except Exception as e:
            minescript.echo(f"§c[Patrol] 單 Plot 出錯: {e}")
        finally:
            self._cleanup_patrol()

    # ── 全 Plot 巡邏迴圈 ─────────────────────
    def _patrol_loop(self):
        release_all(); stop_rclick_hold()
        self.state = PatrolState.TRAVELING
        try:
            _, pest_plots = get_pest_info()
            if not pest_plots:
                minescript.echo("§7[Patrol] 目前無蟲 Plot，結束"); return
            minescript.echo(f"§b[Patrol] 發現蟲 Plot: {pest_plots}")
            for plot_str in pest_plots:
                if self._stop_flag: break
                try:   plot_num = int(plot_str)
                except ValueError:
                    minescript.echo(f"§c[Patrol] 無效 Plot: {plot_str}，跳過"); continue
                if plot_num == 25:
                    minescript.echo("§7[Patrol] 跳過穀倉 Plot 25"); continue
                self.current_plot = plot_num
                minescript.echo(f"§d[Patrol] 前往 Plot {plot_num}...")
                self._travel_to_plot(plot_num)
                if self._stop_flag: break
                self.state = PatrolState.VACUUMING
                minescript.echo(f"§b[Patrol] Plot {plot_num} 開始清蟲...")
                self._vacuum_all_in_plot()
            if not self._stop_flag:
                minescript.echo("§a[Patrol] 所有 Plot 清除完畢")
        except Exception as e:
            minescript.echo(f"§c[Patrol] 出錯: {e}")
        finally:
            self._cleanup_patrol()

    def _cleanup_patrol(self):
        release_all(); stop_rclick_hold()
        for fn in [minescript.player_press_forward, minescript.player_press_jump,
                   minescript.player_press_sneak]:
            try: fn(False)
            except: pass
        self.last_plot    = self.current_plot
        self.state        = PatrolState.IDLE
        self.current_plot = None
        self._done_event.set()

    # ── 移動至 Plot ──────────────────────────
    def _travel_to_plot(self, plot_num):
        self.state = PatrolState.TRAVELING
        before_tp = minescript.player_position()
        minescript.execute(f"/plottp {plot_num}")
        wait_for_position_change(before_tp, timeout=6.0, min_delta=0.8)
        wait_for_position_stable(stable_seconds=0.3, timeout=3.0)
        minescript.player_press_sneak(True)
        stable_secs = random.uniform(1.0, 2.0)
        base_y = self._wait_for_y_stable(stable_secs, timeout=10.0)
        minescript.player_press_sneak(False)
        if self._stop_flag: return
        if base_y is None:
            minescript.echo("§e[Patrol] 等待落地穩定逾時，繼續嘗試")
            base_y = minescript.player_position()[1]
        target_h = base_y + 3.0
        for _retry in range(5):
            if self._stop_flag: return
            ok = self._fly_to_height(target_h)
            if ok: break
            minescript.echo(f"§e[Patrol] 飛行失敗，重試... ({_retry+1}/5)")
            time.sleep(0.5)
        if self._stop_flag: return
        if not self._wait_for_y_stable(random.uniform(1.0, 2.0), timeout=10.0):
            minescript.echo("§e[Patrol] 飛至目標高度後 Y 未穩定，繼續流程")
        if plot_num in PLOT_CENTERS:
            tx, tz = PLOT_CENTERS[plot_num]
            minescript.echo(f"§7[Patrol] AOTE 至中心 X={tx:.0f} Z={tz:.0f}")
            self._aote_to_center(tx, tz)

    def _wait_for_y_stable(self, stable_seconds, timeout=10.0, tolerance=0.05):
        deadline = time.time() + timeout if timeout else None
        last_y = None
        stable_since = None
        while not self._stop_flag:
            y = minescript.player_position()[1]
            now = time.time()
            if last_y is None or abs(y - last_y) > tolerance:
                last_y = y
                stable_since = now
            elif stable_since is not None and now - stable_since >= stable_seconds:
                return y
            if deadline is not None and now > deadline:
                return None
            time.sleep(0.05)
        return None

    def _fly_to_height(self, target_h, tolerance=0.5):
        y = minescript.player_position()[1]
        if y >= target_h - tolerance: return True
        for _ in range(2):
            minescript.player_press_jump(True); time.sleep(0.1)
            minescript.player_press_jump(False); time.sleep(0.1)
        minescript.player_press_jump(True)
        time.sleep(0.3)
        try:
            prev_y = minescript.player_position()[1]
            while not self._stop_flag:
                time.sleep(0.05)
                curr_y = minescript.player_position()[1]
                if curr_y >= target_h - tolerance: return True
                if curr_y < prev_y:
                    minescript.echo(f"§c[Patrol] 飛行失敗：Y 下降 {prev_y:.2f}→{curr_y:.2f}，中止")
                    return False
                prev_y = curr_y
        finally:
            minescript.player_press_jump(False)
        return False

    def _aote_to_center(self, tx, tz):
        if self._stop_flag:
            return
        minescript.player_press_forward(False)
        minescript.player_press_sprint(False)
        minescript.player_press_jump(False)
        minescript.player_press_sneak(False)
        p_pos = minescript.player_position()
        ty_aim, tp_aim = calc_yaw_pitch(p_pos, (tx, p_pos[1], tz))
        aote_smooth_look(ty_aim, tp_aim, steps=8)
        stop_rclick_hold()
        minescript.player_inventory_select_slot(0)
        minescript.player_press_sprint(True)
        minescript.player_press_use(True)
        time.sleep(random.uniform(0.03, 0.06))
        minescript.player_press_use(False)
        time.sleep(random.uniform(0.02, 0.04))
        minescript.player_inventory_select_slot(VACUUM_SLOT)
        start_rclick_hold()

    def _move_to_xz(self, tx, tz, tolerance=4.5, timeout=20.0):
        deadline = time.time() + timeout
        while not self._stop_flag:
            if time.time() > deadline:
                minescript.echo("§e[Patrol] 移動至中心逾時，直接開始除蟲"); break
            pos = minescript.player_position()
            dx  = tx - pos[0]; dz = tz - pos[2]
            if math.sqrt(dx**2 + dz**2) <= tolerance: break
            minescript.player_set_orientation(-math.degrees(math.atan2(dx, dz)), 0)
            minescript.player_press_sprint(True)
            minescript.player_press_forward(True); time.sleep(0.05)
        minescript.player_press_forward(False)
        minescript.player_press_sprint(False)

    # ── 左鍵技能（偽裝探查）────────────────
    def _do_skill_disguise(self):
        minescript.echo("§e[Patrol] 視角外，左鍵探查偽裝...")
        minescript.player_press_sneak(False)
        time.sleep(random.uniform(0.08, 0.14))
        lclick_once()
        time.sleep(random.uniform(0.08, 0.14))
        lclick_once()
        time.sleep(random.uniform(0.6, 0.9))

    # ── 清除 Plot 內所有蟲 ──────────────────
    def _vacuum_all_in_plot(self):
        minescript.player_inventory_select_slot(VACUUM_SLOT)
        time.sleep(0.05)
        self._aimer.reset()
        start_rclick_hold()
        locked_once = False
        no_mob_since = None
        NO_MOB_TIMEOUT = 20.0
        try:
            while not self._stop_flag:
                sfs = find_all_silverfish_nearby()
                if not sfs:
                    if locked_once:
                        if no_mob_since is None: no_mob_since = time.time()
                        elif time.time() - no_mob_since >= NO_MOB_TIMEOUT:
                            minescript.echo(f"§c[Patrol] 找不到蟲超過 {NO_MOB_TIMEOUT:.0f}s，觸發重試...")
                            return "retry"
                    confirm = 0
                    for _ in range(3):
                        time.sleep(0.4)
                        if self._stop_flag: break
                        if find_all_silverfish_nearby(): confirm = -1; break
                        confirm += 1
                    if confirm < 0: continue
                    minescript.echo("§a[Patrol] Plot 清空確認"); return "done"
                no_mob_since = None
                p_pos   = minescript.player_position()
                target  = min(sfs, key=lambda e: dist3(p_pos, e.position))
                tid     = target.id; t_start = time.time()
                locked_once = True
                ori = minescript.player_orientation()
                if ori and not in_fov(ori[0], target.position, p_pos):
                    self._do_skill_disguise()
                    if self._stop_flag: break
                minescript.echo(f"§7[Patrol] 鎖定 {target.name} id={tid}")
                result = self._approach_with_wandering_aim(tid, t_start)
                if   result == "dead":  continue
                elif result == "stuck": minescript.echo(f"§e[Patrol] 蟲 {tid} 超時，跳過"); continue
                else: break
            return "done"
        finally:
            minescript.player_press_forward(False); minescript.player_press_sprint(False)
            minescript.player_press_jump(False);    minescript.player_press_sneak(False)
            stop_rclick_hold()
            minescript.player_press_forward(False); minescript.player_press_sprint(False)
            minescript.player_press_jump(False);    minescript.player_press_sneak(False)

    # ── 追蟲（三段式 AOTE + 人工飛行 + 吸蟲）
    def _approach_with_wandering_aim(self, entity_id, start_time):
        self._aimer.reset()
        last_rp = None; stuck_start = None; in_range = False
        _noise_seed = random.randint(0, 9999)
        _phase_log  = ""

        def _drop_recover():
            minescript.player_press_forward(False)
            minescript.player_press_sprint(False)
            minescript.player_press_jump(False)
            minescript.player_press_sneak(False)
            stop_rclick_hold()
            minescript.echo("§c[Patrol] 掉落，重飛...")
            ok = self._fly_to_height(FLY_HEIGHT)
            if not ok:
                minescript.echo("§c[Patrol] 重飛失敗，放棄此蟲")
                return False
            minescript.player_inventory_select_slot(VACUUM_SLOT)
            start_rclick_hold()
            self._aimer.reset()
            return True

        minescript.player_inventory_select_slot(VACUUM_SLOT)
        start_rclick_hold()

        while not self._stop_flag:
            if time.time() - start_time > PLOT_STUCK_SEC: return "stuck"
            p_pos = minescript.player_position()

            # 掉落保護
            if p_pos[1] < FLY_Y_MIN:
                if not _drop_recover(): return "dead"
                target_pos = get_entity_pos(entity_id, retries=5)
                if target_pos is None: return "dead"
                continue

            target_pos = get_entity_pos(entity_id)
            if target_pos is None:
                time.sleep(random.uniform(0.10, 0.18))
                target_pos = get_entity_pos(entity_id, retries=5)
                if target_pos is None: return "dead"

            dx = target_pos[0] - p_pos[0]
            dy = target_pos[1] - p_pos[1]
            dz = target_pos[2] - p_pos[2]
            dist3d = math.sqrt(dx**2 + dy**2 + dz**2)

            rp = (round(p_pos[0]), round(p_pos[2]))
            if rp == last_rp:
                if stuck_start is None: stuck_start = time.time()
                elif time.time() - stuck_start > STUCK_TIMEOUT: return "stuck"
            else:
                stuck_start = None
            last_rp = rp

            ori = minescript.player_orientation()
            if not ori: return "stop"
            cy, cp = ori[0], ori[1]
            approach_stop_box = ARRIVE_BOX + 1.5

            # 近距：吸蟲
            if dist3d < ARRIVE_BOX:
                if not in_range:
                    in_range = True
                    minescript.player_press_forward(False)
                    minescript.player_press_sprint(False)
                    minescript.player_press_jump(False)
                    minescript.player_press_sneak(False)
                    minescript.player_inventory_select_slot(VACUUM_SLOT)
                    start_rclick_hold()
                    minescript.echo("§b[Patrol] 進入吸取範圍")
                ny, np_ = self._aimer.tick(cy, cp, target_pos, p_pos)
                minescript.player_set_orientation(ny, np_)
                time.sleep(AIM_TICK_SEC + random.uniform(0, 0.004))
                continue

            in_range = False

            # 接近距離：先停車，讓吸取範圍慢慢吃進去，避免衝過頭重複 AOTE
            if dist3d <= approach_stop_box:
                minescript.player_press_forward(False)
                minescript.player_press_sprint(False)
                minescript.player_press_jump(False)
                minescript.player_press_sneak(False)
                if _phase_log != "close":
                    minescript.echo(f"§7[Patrol] 接近停車 dist={dist3d:.1f}")
                    _phase_log = "close"
                ny, np_ = self._aimer.tick(cy, cp, target_pos, p_pos)
                minescript.player_set_orientation(ny, np_)
                time.sleep(AIM_TICK_SEC + random.uniform(0, 0.004))
                continue

            # 遠距：AOTE 傳送
            if dist3d > 12:
                if _phase_log != "aote":
                    minescript.echo(f"§7[Patrol] AOTE 接近 dist={dist3d:.1f}")
                    _phase_log = "aote"
                length   = dist3d if dist3d > 0 else 1
                unit_x   = dx / length; unit_y = dy / length; unit_z = dz / length
                shot_dist = random.uniform(9.0, 10.0)
                aim_x = p_pos[0] + unit_x * shot_dist
                aim_y = p_pos[1] + unit_y * shot_dist
                aim_z = p_pos[2] + unit_z * shot_dist
                noise_y = perlin_noise_1d(time.time() * 0.7, _noise_seed)     * 2.0
                noise_p = perlin_noise_1d(time.time() * 0.7, _noise_seed + 1) * 2.0
                ty_aim, tp_aim = calc_yaw_pitch(p_pos, (aim_x, aim_y, aim_z))
                ty_aim += noise_y; tp_aim = clamp(tp_aim + noise_p, -70, 70)
                aote_smooth_look(ty_aim, tp_aim, steps=8)
                minescript.player_press_forward(False)
                minescript.player_press_sprint(False)
                minescript.player_press_jump(False)
                minescript.player_press_sneak(False)
                stop_rclick_hold()
                minescript.player_inventory_select_slot(0)
                minescript.player_press_use(True);  time.sleep(random.uniform(0.03, 0.06))
                minescript.player_press_use(False); time.sleep(random.uniform(0.02, 0.04))
                minescript.player_inventory_select_slot(VACUUM_SLOT)
                start_rclick_hold()
                continue

            # 中距：人工飛行
            if _phase_log != "human":
                minescript.echo(f"§7[Patrol] 人工接近 dist={dist3d:.1f}")
                _phase_log = "human"
            minescript.player_inventory_select_slot(VACUUM_SLOT)
            start_rclick_hold()
            ny, np_ = self._aimer.tick(cy, cp, target_pos, p_pos)
            t_now   = time.time()
            noise_y = perlin_noise_1d(t_now * JITTER_FREQ,       _noise_seed)     * WANDER_NOISE_Y * 2
            noise_p = perlin_noise_1d(t_now * JITTER_FREQ * 1.3, _noise_seed + 1) * WANDER_NOISE_P * 2
            minescript.player_set_orientation(ny + noise_y, clamp(np_ + noise_p, -80, 80))
            minescript.player_press_sprint(True)
            minescript.player_press_forward(True)
            target_y = target_pos[1] + random.uniform(1.0, 2.5)
            if   p_pos[1] < target_y - 1.0:
                minescript.player_press_jump(True);  minescript.player_press_sneak(False)
            elif p_pos[1] > target_y + 1.5:
                minescript.player_press_sneak(True); minescript.player_press_jump(False)
            else:
                minescript.player_press_jump(False); minescript.player_press_sneak(False)
            if random.random() < 0.03:
                time.sleep(random.uniform(0.05, 0.12))
            time.sleep(AIM_TICK_SEC + random.uniform(0, 0.004))

        return "stop"


# ────────────────────────────────────────────────
#  Chat 訊息工具
# ────────────────────────────────────────────────
def _strip_mc(text):
    s = re.sub(r"[§&][0-9a-fk-orA-FK-OR]", "", str(text))
    return "".join(c if 32 <= ord(c) < 127 else " " for c in s)

def _extract_plot_num(raw):
    m = re.search(r'b(\d+)', raw, re.IGNORECASE)
    if m: return int(m.group(1))
    stripped = _strip_mc(raw)
    m = re.search(r'plot\s*[-\u2013]?\s*(\d+)', stripped, re.IGNORECASE)
    if m: return int(m.group(1))
    return None
