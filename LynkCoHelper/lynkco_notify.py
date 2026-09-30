# -*- coding: utf-8 -*-
"""
领克每日任务通知工具。

支持：
    - Bark：LYNKCO_BARK_KEY（可选）
    - PushPlus 微信：PUSHPLUS_TOKEN（可选）

未配置对应推送渠道时自动跳过，不影响签到/分享任务。
"""
import os

import requests

from lynkco_common import load_env_data

BARK_DEFAULT_BASE = "https://api.day.app"
PUSHPLUS_DEFAULT_BASE = "https://www.pushplus.plus/send"


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


def send_bark_notification(title: str, markdown_body: str, group: str = "LynkCo签到",
                            icon: str = None, level: str = "active",
                            bark_key: str = None) -> dict:
    """通过 Bark 发送 Markdown 通知。未配置 Bark Key 时跳过。"""
    bark_key = (
        bark_key
        or os.environ.get("LYNKCO_BARK_KEY", "").strip()
        or load_env_data().get("notify", {}).get("barkKey", "").strip()
    )
    if not bark_key:
        print("[提示] 未配置 LYNKCO_BARK_KEY，跳过 Bark 推送。")
        return {"skipped": True}

    url = f"{BARK_DEFAULT_BASE}/{bark_key}"
    payload = {
        "title": title,
        "markdown": markdown_body,
        "group": group,
        "level": level,
    }
    if icon:
        payload["icon"] = icon

    resp = requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def send_pushplus_notification(title: str, markdown_body: str,
                               pushplus_token: str = None) -> dict:
    """
    通过 PushPlus 微信渠道发送 Markdown 通知。
    token 不传时读取 PUSHPLUS_TOKEN，未配置则跳过。
    """
    pushplus_token = (
        pushplus_token
        or os.environ.get("PUSHPLUS_TOKEN", "").strip()
        or load_env_data().get("notify", {}).get("pushplusToken", "").strip()
    )
    if not pushplus_token:
        print("[提示] 未配置 PUSHPLUS_TOKEN，跳过 PushPlus 推送。")
        return {"skipped": True}

    payload = {
        "token": pushplus_token,
        "title": title,
        "content": markdown_body,
        "template": "markdown",
        "channel": "wechat",
    }
    resp = requests.post(
        PUSHPLUS_DEFAULT_BASE,
        json=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 200:
        raise RuntimeError(f"PushPlus 返回异常: {data}")
    return data
