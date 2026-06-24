# workflow_blocks.py — 可儲存的積木式工作流設定
# 不直接操作 Minecraft；實際動作由 maingarden.py 註冊，避免循環 import。

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable

WORKFLOW_FILE = os.path.join(os.path.dirname(__file__), "workflows.json")


@dataclass(frozen=True)
class BlockSpec:
    key: str
    label: str
    category: str
    description: str = ""


@dataclass
class WorkflowBlock:
    action: str
    args: dict = field(default_factory=dict)


DEFAULT_BLOCKS = [
    BlockSpec("stop_farm_keys", "停止農業按鍵", "農業", "放開攻擊/移動等農業按鍵"),
    BlockSpec("start_farm_keys", "開始農業按鍵", "農業", "選鋤頭並按住農業按鍵"),
    BlockSpec("farm_entry_actions", "農業入場動作", "農業", "蹲下、切欄位並執行原本入場點擊"),
    BlockSpec("switch_dragon", "切玫瑰龍+Blossom", "換裝+寵物", "切換寵物並穿 Blossom"),
    BlockSpec("switch_mosquito", "切蚊子+Pesthunters", "換裝+寵物", "切換寵物並穿 Pesthunters"),
    BlockSpec("sell_vinyl", "賣唱片", "清理", "呼叫 example.sell_vinyl"),
    BlockSpec("pest_all", "執行 /pest 除蟲", "除蟲", "執行原本完整 /pest 流程"),
    BlockSpec("wait_1", "等待 1 秒", "時間", "暫停 1 秒"),
    BlockSpec("wait_5", "等待 5 秒", "時間", "暫停 5 秒"),
]

DEFAULT_WORKFLOWS = {
    "主工作流": [
        {"action": "switch_dragon"},
        {"action": "farm_entry_actions"},
        {"action": "start_farm_keys"},
        {"action": "stop_farm_keys"},
        {"action": "switch_mosquito"},
        {"action": "sell_vinyl"},
    ],
}

def load_workflows(path: str = WORKFLOW_FILE) -> dict:
    if not os.path.exists(path):
        return {name: list(blocks) for name, blocks in DEFAULT_WORKFLOWS.items()}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {
                str(name): [b for b in blocks if isinstance(b, dict) and b.get("action")]
                for name, blocks in data.items()
                if isinstance(blocks, list)
            } or {name: list(blocks) for name, blocks in DEFAULT_WORKFLOWS.items()}
    except Exception:
        pass
    return {name: list(blocks) for name, blocks in DEFAULT_WORKFLOWS.items()}


def save_workflows(workflows: dict, path: str = WORKFLOW_FILE):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(workflows, fh, ensure_ascii=False, indent=2)


def block_label(action: str, specs: list[BlockSpec] = DEFAULT_BLOCKS) -> str:
    for spec in specs:
        if spec.key == action:
            return f"{spec.category}｜{spec.label}"
    return action


def run_workflow(name: str, blocks: list[dict], actions: dict[str, Callable], log: Callable[[str], None] = print):
    log(f"工作流開始：{name}（{len(blocks)} blocks）")
    for idx, block in enumerate(blocks, 1):
        action = block.get("action")
        fn = actions.get(action)
        if not fn:
            log(f"工作流略過未知動作 #{idx}: {action}")
            continue
        log(f"工作流 #{idx}: {block_label(action)}")
        fn(**block.get("args", {}))
        time.sleep(0.05)
    log(f"工作流完成：{name}")
