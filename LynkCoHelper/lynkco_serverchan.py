# -*- coding: utf-8 -*-
"""Server酱推送工具（支持 Turbo SCT 与 Server酱³ SC3）。"""
import os
import re

import requests

from lynkco_common import load_env_data


def send_serverchan_notification(title: str, markdown_body: str,
                                 sendkey: str = None) -> dict:
    """发送 Server酱通知；未配置 SendKey 时跳过。密钥不会写入日志。"""
    if sendkey is None:
        sendkey = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
        if not sendkey:
            try:
                sendkey = (
                    load_env_data().get("notify", {}).get("serverChanSendKey", "").strip()
                )
            except Exception:
                sendkey = ""

    if not sendkey:
        print("[提示] 未配置 SERVERCHAN_SENDKEY，跳过 Server酱推送。")
        return {"skipped": True}

    # Server酱 Turbo 使用 SCT；Server酱³ 使用 sctp{uid}t... 格式。
    if sendkey.startswith("sctp"):
        match = re.match(r"^sctp(\d+)t", sendkey)
        if not match:
            raise ValueError("Server酱³ SendKey 格式不正确（应为 sctp{uid}t...）")
        url = f"https://{match.group(1)}.push.ft07.com/send/{sendkey}.send"
    else:
        url = f"https://sctapi.ftqq.com/{sendkey}.send"

    # 标题不允许换行；正文支持 Markdown。
    safe_title = " ".join(str(title).splitlines()).strip() or "领克App每日任务"
    try:
        response = requests.post(
            url,
            data={"title": safe_title, "desp": markdown_body},
            timeout=(10, 20),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != 0:
            # 不回显响应原文，避免第三方错误信息意外包含请求地址或凭据。
            message = payload.get("message", "未知错误") if isinstance(payload, dict) else "响应格式异常"
            raise RuntimeError(f"Server酱返回失败（code={payload.get('code') if isinstance(payload, dict) else '未知'}）：{message}")
        return {"success": True, "code": 0}
    except requests.RequestException as exc:
        # requests 异常字符串可能包含含 SendKey 的 URL，故只输出异常类型。
        raise RuntimeError(f"Server酱网络请求失败：{type(exc).__name__}") from None
