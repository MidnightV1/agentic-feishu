#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Interactive setup script for agentic-feishu.

Guides users through configuration:
1. Choose LLM provider
2. Enter API key
3. Configure Feishu bot credentials
4. Write config.yaml
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml


def _input(prompt: str, default: str = "") -> str:
    """Prompt user with optional default."""
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val if val else default


def _secret(prompt: str) -> str:
    """Prompt for a secret value (show masked feedback)."""
    import getpass
    val = getpass.getpass(f"{prompt}: ").strip()
    return val


def _yes(prompt: str, default: bool = True) -> bool:
    """Yes/no prompt."""
    suffix = " [Y/n]" if default else " [y/N]"
    val = input(f"{prompt}{suffix}: ").strip().lower()
    if not val:
        return default
    return val in ("y", "yes")


def main():
    print("\n🚀 agentic-feishu 配置向导\n")
    print("=" * 50)

    config: dict = {}

    # ── Step 1: LLM Provider ──────────────────────────────
    print("\n📦 第一步：选择 LLM 服务商\n")

    from providers.presets import PRESETS

    providers = list(PRESETS.keys())
    for i, name in enumerate(providers, 1):
        preset = PRESETS[name]
        print(f"  {i:2d}. {preset.display_name} ({name})")

    while True:
        choice = _input("\n选择序号", "1")
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(providers):
                provider_name = providers[idx]
                break
        except ValueError:
            pass
        print("  ⚠️  请输入有效序号")

    preset = PRESETS[provider_name]
    print(f"\n  ✅ 选择了 {preset.display_name}")

    # API Key
    api_key = _secret(f"\n  输入 {preset.display_name} API Key")
    if not api_key:
        print("  ⚠️  未输入 API Key，可稍后在 config.yaml 中配置")

    config["default_provider"] = provider_name
    config[provider_name] = {"api_key": api_key}

    # Model selection
    print(f"\n  可用模型：")
    models = list(preset.models.keys())
    for i, model in enumerate(models, 1):
        info = preset.models[model]
        caps = []
        if info.vision:
            caps.append("👁 vision")
        if info.file_input:
            caps.append("📎 file")
        if info.reasoning:
            caps.append("🧠 reasoning")
        caps_str = f" ({', '.join(caps)})" if caps else ""
        print(f"    {i}. {model} — {info.context_window // 1000}k ctx{caps_str}")

    default_idx = models.index(preset.default_model) + 1 if preset.default_model in models else 1
    model_choice = _input("  选择模型序号", str(default_idx))
    try:
        model_name = models[int(model_choice) - 1]
    except (ValueError, IndexError):
        model_name = preset.default_model
    config[provider_name]["model"] = model_name
    print(f"  ✅ 模型: {model_name}")

    # ── Step 2: Feishu ────────────────────────────────────
    print("\n\n🔗 第二步：配置飞书 Bot\n")
    if _yes("  是否配置飞书 Bot？"):
        feishu = {}
        feishu["app_id"] = _input("  App ID (cli_xxxxx)")
        feishu["app_secret"] = _secret("  App Secret")
        if feishu["app_id"] and feishu["app_secret"]:
            config["feishu"] = feishu
            print("  ✅ 飞书 Bot 已配置")
        else:
            print("  ⏭️  跳过飞书配置")
    else:
        print("  ⏭️  跳过飞书配置")

    # ── Step 3: Agent settings ────────────────────────────
    print("\n\n⚙️  第三步：Agent 参数\n")
    config["max_turns"] = int(_input("  最大对话轮数", "50"))
    config["max_budget_usd"] = float(_input("  单次预算上限 (USD)", "1.0"))
    config["temperature"] = float(_input("  Temperature", "0.7"))
    config["stream_output"] = _yes("  启用流式输出？", True)
    config["persona"] = _input("  人设模板", "default")

    # ── Write config ──────────────────────────────────────
    config_path = Path("config.yaml")
    print(f"\n\n📄 配置预览:\n")
    yaml_str = yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(yaml_str)

    if config_path.exists():
        if not _yes(f"⚠️  {config_path} 已存在，覆盖？", False):
            alt = _input("保存到其他路径", "config.new.yaml")
            config_path = Path(alt)

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"\n✅ 配置已保存到 {config_path}")
    print("\n🎉 设置完成！运行以下命令启动：")
    print(f"   python3 main.py")

    # Create data directory
    data_dir = Path(config.get("data_dir", "data"))
    data_dir.mkdir(exist_ok=True)
    print(f"\n   (数据目录 {data_dir}/ 已创建)")


if __name__ == "__main__":
    main()
