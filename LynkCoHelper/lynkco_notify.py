# -*- coding: utf-8 -*-
"""领克每日任务通知工具：支持 Telegram（可选）。"""
import os
import requests
from lynkco_common import load_env_data

TELEGRAM_DEFAULT_BASE = "https://api.telegram.org"


def _extract_point(energy_resp: dict) -> str:
    return str((energy_resp.get("data") or {}).get("point", "?"))


def build_markdown_report(result: dict) -> str:
    lines = []
    if result.get("already_signed"):
        lines.append("### ℹ️ 签到")
        lines.append("- 今日已签到，无需重复签到")
    else:
        sign_result = result.get("sign_result") or {}
        sign_ok = bool(sign_result.get("success"))
        sign_data = sign_result.get("data") or {}
        if sign_ok:
            lines.append("### ✅ 签到成功")
            reward = sign_data.get("rewardEnergyNumber")
            if reward is not None:
                lines.append(f"- 本次奖励能量体：**+{reward}**")
        else:
            lines.append("### ❌ 签到失败")
            lines.append(f"- {sign_result.get('message', '未知错误')}")

    continue_data = (result.get("continue_info") or {}).get("data") or {}
    continue_days = continue_data.get("continueDays")
    sign_card = continue_data.get("signCardNumber")
    if continue_days is not None:
        lines.append(f"- 连续签到：**{continue_days} 天**")
    if sign_card is not None:
        lines.append(f"- 签到卡剩余：**{sign_card} 张**")

    share_result = result.get("share_result")
    if share_result is not None:
        lines.append("\n### 🔗 分享任务")
        if share_result.get("ok"):
            lines.append("- 状态：**上报成功**")
            article_title = share_result.get("articleTitle")
            if article_title:
                lines.append(f"- 分享文章：{article_title}")
        else:
            detail = share_result.get("detail") or {}
            lines.append(f"- 状态：**失败**（{detail.get('message', '详情见日志')}）")

    point_before = _extract_point(result.get("energy_before") or {})
    point_after = _extract_point(result.get("energy_after") or {})
    lines.append("\n### 💰 积分变化")
    try:
        delta = int(point_after) - int(point_before)
        delta_str = f"（+{delta}）" if delta > 0 else (f"（{delta}）" if delta < 0 else "（无变化）")
    except (ValueError, TypeError):
        delta_str = ""
    lines.append(f"- {point_before} → **{point_after}** {delta_str}".rstrip())
    return "\n".join(lines)


def send_telegram_notification(title: str, markdown_body: str,
                               bot_token: str = None,
                               chat_id: str = None) -> dict:
    """通过 Telegram Bot 发送 Markdown 通知；未配置则跳过。"""
    bot_token = (
        bot_token
        or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        or load_env_data().get("notify", {}).get("telegramBotToken", "").strip()
    )
    chat_id = (
        chat_id
        or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        or load_env_data().get("notify", {}).get("telegramChatId", "").strip()
    )
    if not bot_token or not chat_id:
        print("[提示] 未完整配置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，跳过 Telegram 推送。")
        return {"skipped": True}

    text = f"*{title}*\n\n{markdown_body}"
    url = f"{TELEGRAM_DEFAULT_BASE}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram 返回异常: {data}")
    return data
