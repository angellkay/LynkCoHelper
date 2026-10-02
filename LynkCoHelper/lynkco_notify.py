# -*- coding: utf-8 -*-
"""领克每日任务通知工具：支持 Telegram（可选）。"""
import os
import requests
from lynkco_common import load_env_data

TELEGRAM_DEFAULT_BASE = "https://api.telegram.org"


def _extract_point(resp: dict) -> str:
    return str((resp.get("data") or {}).get("point", "?"))


def _extract_income_point(resp: dict) -> str:
    return str((resp.get("data") or {}).get("incomePoint", "?"))


def _extract_expire_point(resp: dict) -> str:
    return str((resp.get("data") or {}).get("expirePoint", "?"))


def _extract_growth(resp: dict) -> str:
    data = resp.get("data") or {}
    account_level = data.get("accountLevelVo") or {}
    return str(account_level.get("growth", "?"))


def _extract_energy_level(resp: dict) -> str:
    data = resp.get("data") or {}
    account_level = data.get("accountLevelVo") or {}
    name = account_level.get("name")
    num = account_level.get("num")
    if name:
        return str(name)
    if num is not None:
        return str(num)
    return "?"


def _extract_next_energy(resp: dict) -> str:
    data = resp.get("data") or {}
    return str(data.get("nextEnergyNum", "?"))


def _growth_delta(before_resp: dict, after_resp: dict):
    try:
        before = int(_extract_growth(before_resp))
        after = int(_extract_growth(after_resp))
        return after - before
    except (ValueError, TypeError):
        return None


def _project_days(next_energy: str, daily_growth):
    try:
        remaining = int(next_energy)
        if remaining <= 0:
            return 0
        if daily_growth is None or daily_growth <= 0:
            return None
        return (remaining + daily_growth - 1) // daily_growth
    except (ValueError, TypeError):
        return None


def _extract_flow_records(resp: dict) -> list:
    data = resp.get("data") or {}
    records = data if isinstance(data, list) else (data.get("records") or data.get("list") or data.get("rows") or [])
    return records if isinstance(records, list) else []


def _extract_flow_details(resp: dict) -> list:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    details = []
    for item in _extract_flow_records(resp):
        if not isinstance(item, dict):
            continue
        date_value = str(item.get("createTime") or item.get("time") or item.get("date") or "")
        if date_value and not date_value.startswith(today):
            continue
        amount = item.get("growth", item.get("energyNum", item.get("number", item.get("change"))))
        if amount is None:
            continue
        reason = item.get("businessName") or item.get("remark") or item.get("reason") or item.get("name") or item.get("title") or item.get("typeName") or "能量体变动"
        try:
            amount = int(amount)
        except (ValueError, TypeError):
            continue
        details.append((str(reason), amount))
    return details



def _extract_task_list(resp: dict) -> list:
    data = resp.get("data") or []
    return data if isinstance(data, list) else []


def _task_progress(task: dict):
    try:
        process = int(task.get("taskProcess"))
    except (ValueError, TypeError):
        return None
    import re
    match = re.search(r"(\\d+)", str(task.get("taskName") or ""))
    target = int(match.group(1)) if match else None
    return process, target


def _format_task_line(task: dict) -> str:
    name = str(task.get("taskName") or "签到任务")
    reward = "、".join(str(x) for x in (task.get("rewardContent") or []) if x is not None)
    progress = _task_progress(task)
    if progress and progress[1] is not None:
        process, target = progress
        remaining = max(target - process, 0)
        status = " ✅" if process >= target else f"（还差 {remaining}）"
        line = f"- {name}：**{process}/{target}**{status}"
    elif progress:
        line = f"- {name}：**{progress[0]}**"
    else:
        line = f"- {name}"
    if reward:
        line += f" · 奖励：{reward}"
    return line


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

    task_list = _extract_task_list(result.get("task_list") or {})
    if task_list:
        lines.append("\n### 📋 签到任务")
        for task in task_list:
            if isinstance(task, dict):
                lines.append(_format_task_line(task))

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
    income_point = _extract_income_point(result.get("energy_after") or {})
    expire_point = _extract_expire_point(result.get("energy_after") or {})
    growth = _extract_growth(result.get("energy_after") or {})
    energy_level = _extract_energy_level(result.get("energy_after") or {})
    next_energy = _extract_next_energy(result.get("energy_grade_after") or {})
    growth_before = _extract_growth(result.get("member_before") or {})
    growth_delta = _growth_delta(result.get("member_before") or {}, result.get("member_after") or {})
    projected_days = _project_days(next_energy, growth_delta)
    history = result.get("energy_history") or []
    history_values = [int(x.get("growth")) for x in history if isinstance(x, dict) and str(x.get("growth", "")).lstrip("-").isdigit()]
    avg_growth = (sum(history_values) / len(history_values)) if history_values else None
    avg_projected_days = _project_days(next_energy, avg_growth)
    flow_details = _extract_flow_details(result.get("energy_growth_flow") or {})

    lines.append("\n### 💰 Co积分")
    try:
        delta = int(point_after) - int(point_before)
        delta_str = f"（+{delta}）" if delta > 0 else (f"（{delta}）" if delta < 0 else "（无变化）")
    except (ValueError, TypeError):
        delta_str = ""
    lines.append(f"- {point_before} → **{point_after}** {delta_str}".rstrip())
    lines.append(f"- 累计获得：**{income_point}**")
    lines.append(f"- 待过期：**{expire_point}**")

    lines.append("\n### ⚡ 能量体")
    lines.append(f"- 当前：**{growth}**")
    if growth_delta is not None:
        sign = "+" if growth_delta >= 0 else ""
        lines.append(f"- 今日增加：**{sign}{growth_delta}**（{growth_before} → {growth}）")
    if energy_level != "?":
        lines.append(f"- 等级：**{energy_level}**")
    if next_energy != "?":
        lines.append(f"- 距离下一级：**{next_energy}**")
        if projected_days is not None:
            lines.append(f"- 按今日增幅预计：**约 {projected_days} 天**")
        elif growth_delta == 0:
            lines.append("- 按今日增幅预计：**今日无增加，暂无法估算**")
        elif growth_delta is not None and growth_delta < 0:
            lines.append("- 按今日增幅预计：**今日为负增长，暂无法估算**")
    if avg_growth is not None:
        lines.append(f"- 近7天平均：**+{avg_growth:.2f}/天**")
        if avg_projected_days is not None:
            lines.append(f"- 按近7天平均预计：**约 {avg_projected_days} 天**")
    if flow_details:
        lines.append("- 今日增加明细：")
        for reason, amount in flow_details:
            sign = "+" if amount >= 0 else ""
            lines.append(f"  - {reason}：**{sign}{amount}**")

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
