# workflow_blocks.py — 可儲存的積木式工作流設定
# 不直接操作 Minecraft；實際動作由 maingarden.py 註冊，避免循環 import。

import inspect
import json
import os
import time
from dataclasses import dataclass
from typing import Callable

WORKFLOW_FILE = os.path.join(os.path.dirname(__file__), "workflows.json")


@dataclass(frozen=True)
class BlockSpec:
    key: str
    label: str
    category: str
    kind: str = "action"  # action / condition / trigger
    description: str = ""


DEFAULT_BLOCKS = [
    BlockSpec("trigger_chat_pest", "當 chat 害蟲生成", "觸發", "trigger", "YUCK 訊息解析到 plot 時"),
    BlockSpec("trigger_pest_ready", "當冷卻 READY", "觸發", "trigger", "pest cooldown READY 或本地預測 READY"),
    BlockSpec("if_farm_on", "如果農業中", "條件", "condition", "farm_state == on"),
    BlockSpec("if_chat_pest_enabled", "如果 ChatPest 開啟", "條件", "condition", "ChatPest 開關為 ON"),
    BlockSpec("if_pest_idle", "如果沒有除蟲中", "條件", "condition", "目前沒有 pest 任務"),
    BlockSpec("if_has_pest_plot", "如果偵測到 Plot", "條件", "condition", "觸發 context 有 plot_num"),
    BlockSpec("if_pet_not_mosquito", "如果不是蚊子", "條件", "condition", "目前寵物不是 Mosquito"),
    BlockSpec("stop_farm_keys", "停止農業按鍵", "農業", "action", "放開攻擊/移動等農業按鍵"),
    BlockSpec("start_farm_keys", "開始農業按鍵", "農業", "action", "選鋤頭並按住農業按鍵"),
    BlockSpec("farm_entry_actions", "農業入場動作", "農業", "action", "蹲下、切欄位並執行原本入場點擊"),
    BlockSpec("farm_begin", "農業開始", "農業", "action", "切玫瑰龍、入場並開始農業按鍵"),
    BlockSpec("farm_continue", "繼續農業", "農業", "action", "除蟲後回花園並恢復農業"),
    BlockSpec("switch_dragon", "切玫瑰龍+Blossom", "換裝+寵物", "action", "切換寵物並穿 Blossom"),
    BlockSpec("switch_mosquito", "切蚊子+Pesthunters", "換裝+寵物", "action", "切換寵物並穿 Pesthunters"),
    BlockSpec("sell_vinyl", "賣唱片", "清理", "action", "呼叫 example.sell_vinyl"),
    BlockSpec("pest_all", "執行 /pest 除蟲", "除蟲", "action", "執行原本完整 /pest 流程"),
    BlockSpec("chat_pest_prepare", "偵測除蟲後暫停農業", "除蟲", "action", "記住農業狀態、停農業並準備除蟲"),
    BlockSpec("chat_pest_start", "除蟲開始", "除蟲", "action", "使用觸發 context 的 plot_num 前往除蟲並等待完成"),
    BlockSpec("wait_1", "等待 1 秒", "時間", "action", "暫停 1 秒"),
    BlockSpec("wait_5", "等待 5 秒", "時間", "action", "暫停 5 秒"),
]

DEFAULT_WORKFLOWS = {
    "農業→除蟲→繼續": [
        {"action": "farm_begin"},
        {"trigger": "trigger_chat_pest"},
        {"if": "if_chat_pest_enabled", "then": [
            {"if": "if_has_pest_plot", "then": [
                {"if": "if_pest_idle", "then": [
                    {"action": "chat_pest_prepare"},
                    {"action": "chat_pest_start"},
                    {"action": "farm_continue"},
                ]},
            ]},
        ]},
    ],
    "Chat害蟲生成事件": [
        {"trigger": "trigger_chat_pest"},
        {"if": "if_chat_pest_enabled", "then": [
            {"if": "if_has_pest_plot", "then": [
                {"if": "if_pest_idle", "then": [
                    {"action": "chat_pest_prepare"},
                    {"action": "chat_pest_start"},
                    {"action": "farm_continue"},
                ]},
            ]},
        ]},
    ],
}


def _valid_block(block):
    return isinstance(block, dict) and (block.get("action") or block.get("if") or block.get("trigger"))


def _clean_blocks(blocks):
    clean = []
    for block in blocks if isinstance(blocks, list) else []:
        if not _valid_block(block):
            continue
        item = {k: v for k, v in block.items() if k in {"action", "if", "trigger", "then", "else", "args"}}
        if "then" in item:
            item["then"] = _clean_blocks(item.get("then"))
        if "else" in item:
            item["else"] = _clean_blocks(item.get("else"))
        clean.append(item)
    return clean


def load_workflows(path: str = WORKFLOW_FILE) -> dict:
    if not os.path.exists(path):
        return {name: list(blocks) for name, blocks in DEFAULT_WORKFLOWS.items()}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            loaded = {str(name): _clean_blocks(blocks) for name, blocks in data.items() if isinstance(blocks, list)}
            return loaded or {name: list(blocks) for name, blocks in DEFAULT_WORKFLOWS.items()}
    except Exception:
        pass
    return {name: list(blocks) for name, blocks in DEFAULT_WORKFLOWS.items()}


def save_workflows(workflows: dict, path: str = WORKFLOW_FILE):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(workflows, fh, ensure_ascii=False, indent=2)


def find_spec(key: str, specs: list[BlockSpec] = DEFAULT_BLOCKS):
    for spec in specs:
        if spec.key == key:
            return spec
    return None


def block_key(block: dict) -> str:
    return block.get("action") or block.get("if") or block.get("trigger") or ""


def block_label(block_or_action, specs: list[BlockSpec] = DEFAULT_BLOCKS) -> str:
    if isinstance(block_or_action, dict):
        key = block_key(block_or_action)
    else:
        key = str(block_or_action or "")
    spec = find_spec(key, specs)
    if not spec:
        return key
    prefix = {"condition": "如果", "trigger": "當", "action": spec.category}.get(spec.kind, spec.category)
    return f"{prefix}｜{spec.label}"


def _call_action(fn: Callable, args: dict, context: dict):
    try:
        sig = inspect.signature(fn)
        if "context" in sig.parameters:
            return fn(context=context, **args)
    except Exception:
        pass
    return fn(**args)


def run_blocks(blocks: list[dict], actions: dict[str, Callable], log: Callable[[str], None], context: dict | None = None, depth: int = 0):
    context = context or {}
    indent = "  " * depth
    for idx, block in enumerate(blocks, 1):
        key = block_key(block)
        if block.get("trigger"):
            log(f"{indent}觸發點 #{idx}: {block_label(block)}")
            continue
        if block.get("if"):
            fn = actions.get(key)
            result = bool(_call_action(fn, block.get("args", {}), context)) if fn else False
            log(f"{indent}條件 #{idx}: {block_label(block)} → {result}")
            branch = block.get("then") if result else block.get("else", [])
            run_blocks(branch or [], actions, log, context, depth + 1)
            continue
        fn = actions.get(key)
        if not fn:
            log(f"{indent}工作流略過未知動作 #{idx}: {key}")
            continue
        log(f"{indent}動作 #{idx}: {block_label(block)}")
        _call_action(fn, block.get("args", {}), context)
        time.sleep(0.05)


def run_workflow(name: str, blocks: list[dict], actions: dict[str, Callable], log: Callable[[str], None] = print, context: dict | None = None):
    log(f"工作流開始：{name}（{len(blocks)} blocks）")
    run_blocks(blocks, actions, log, context or {})
    log(f"工作流完成：{name}")
