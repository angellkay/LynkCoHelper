# -*- coding: utf-8 -*-
"""ChatAnywhere provider for short, grounded comments."""

import ipaddress
import re
from urllib.parse import urlsplit

import requests

from lynkco_common import AI_PROMPT_MAX_CHARS, AI_TIMEOUT, MAX_COMMENT_CHARS


API_URL = "https://api.chatanywhere.tech/v1/chat/completions"
SYSTEM_PROMPT = (
    f"你在阅读一条领克社区动态，写一句简洁、自然、像真实车友留言一样的中文评论，控制在25到{AI_PROMPT_MAX_CHARS}字。"
    "第一句或主要内容必须引用动态文字里的一个可核对事实（例如车型、里程、明确配置或具体场景）；图片只能补充你能直接看见的内容。"
    "不要根据车型常识自行推断动力、底盘、油电切换、续航、舒适性或操控等未被动态明确说出或图片直接呈现的信息。"
    "只依据动态文字和图片中能确认的内容，不编造自己的用车体验。"
    "评论必须针对这篇动态本身，不要写任何汽车帖子都能套用的夸奖。"
    "避免‘听起来很不错’、‘很详细’、‘很到位’、‘确实更轻松’、‘兼顾了舒适性和实用性’、‘希望继续保持好状态’等空泛套话。"
    "不提及AI或分析过程；只输出评论正文，不要引号、前缀或解释；信息不足时宁可简短，也不要编造。"
)
_META_PHRASES = (
    "作为AI模型", "作为一个AI", "请提供更多信息", "无法查看图片", "无法看到图片",
    "下面是评论", "忽略以上指令", "系统提示", "作为助手", "根据指令",
)
_PROMO_PHRASES = (
    "关注", "点赞", "转发", "购买", "下单", "扫码", "优惠", "折扣", "领取",
    "点击", "私信", "加微信", "加群", "加我", "联系", "拨打", "咨询", "链接",
)
_CONTACT_PATTERN = re.compile(
    r"https?://|www\.|(?:[a-z0-9-]+\.)+[a-z]{2,}(?![a-z0-9-])|@[\w.-]+|(?<!\d)1[3-9]\d{9}(?!\d)",
    re.IGNORECASE,
)
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z", re.IGNORECASE)
_CHINESE_PAIR = re.compile(r"[\u4e00-\u9fff]{2,}")
_ASCII_ANCHOR = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", re.IGNORECASE)
_COMMON_WORDS = {"the", "and", "for", "you", "this", "that", "with", "from", "are"}


def _ascii_anchors(text: str) -> set:
    tokens = (token.casefold() for token in _ASCII_ANCHOR.findall(text))
    return {token for token in tokens if token not in _COMMON_WORDS and
            (len(token) >= 3 or len(token) >= 2 and any(char.isdigit() for char in token))}


class CommentGenerationError(Exception):
    """The post cannot be analyzed or the model did not return a usable comment."""


def _response_text(response, api_key: str) -> str:
    """Return a bounded response body with the request key removed."""
    text = getattr(response, "text", "")
    if not isinstance(text, str):
        return ""
    if api_key:
        text = text.replace(api_key, "<redacted>")
    return text[:4000]


def generate_comment(post: dict, api_key: str, model: str = "gpt-4o-mini", session=None) -> str:
    """Call ChatAnywhere once; reject incomplete or ungrounded-looking output."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise CommentGenerationError("缺少模型 API Key")
    if not isinstance(model, str) or not model.strip():
        raise CommentGenerationError("模型名称无效")
    if not isinstance(post, dict):
        raise CommentGenerationError("动态数据无效")

    title = post.get("title") or ""
    content = post.get("text") or ""
    images = post.get("images") or []
    if not isinstance(title, str) or not isinstance(content, str) or not isinstance(images, list):
        raise CommentGenerationError("动态内容格式无效")
    for url in images[:3]:
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            hostname = parsed.hostname if parsed else None
            port = parsed.port if parsed else None
        except ValueError:
            parsed = None
            hostname = None
        if not parsed or parsed.scheme != "https" or not hostname or parsed.username or parsed.password or \
                any(char.isspace() or ord(char) < 32 for char in url):
            raise CommentGenerationError("动态图片地址无效")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            labels = hostname.rstrip(".").split(".")
            if len(labels) < 2 or hostname.endswith(".local") or \
                    any(not _HOST_LABEL.fullmatch(label) for label in labels):
                raise CommentGenerationError("动态图片地址无效")
        else:
            if not address.is_global:
                raise CommentGenerationError("动态图片地址无效")
    if not title.strip() and not content.strip() and not images:
        raise CommentGenerationError("动态没有可分析的图文内容")

    parts = [{"type": "text", "text": f"标题：{title[:200]}\n正文：{content[:3000]}"}]
    parts.extend({"type": "image_url", "image_url": {"url": url}} for url in images[:3])
    def _request_completion(current_parts, max_tokens=96):
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": current_parts},
            ],
            "max_tokens": max_tokens,
            "stream": False,
        }
        try:
            response = request_session.post(
                API_URL, json=payload,
                headers={"Authorization": f"Bearer {api_key}"}, timeout=AI_TIMEOUT,
            )
        except requests.RequestException as exc:
            print(f"[AI] request error={type(exc).__name__}: {exc}", flush=True)
            raise CommentGenerationError("模型请求失败") from None
        print(f"[AI] response status={response.status_code}", flush=True)
        if response.status_code == 429:
            print(f"[AI] response body={_response_text(response, api_key)}", flush=True)
            raise CommentGenerationError("模型服务繁忙（HTTP 429），请稍后重试")
        if response.status_code in (401, 403):
            print(f"[AI] response body={_response_text(response, api_key)}", flush=True)
            raise CommentGenerationError("模型 API Key 无效或无权限")
        if response.status_code != 200:
            print(f"[AI] response body={_response_text(response, api_key)}", flush=True)
            raise CommentGenerationError("模型服务返回失败")
        try:
            choice = response.json()["choices"][0]
            finish_reason = choice["finish_reason"]
            result = choice["message"]["content"]
        except (ValueError, TypeError, KeyError, IndexError):
            raise CommentGenerationError("模型响应格式无效") from None
        if finish_reason != "stop" or not isinstance(result, str):
            raise CommentGenerationError("模型输出未完成")
        return result.strip()

    request_session = session or requests.Session()
    endpoint = getattr(request_session, "endpoint", API_URL)
    print(f"[AI] request endpoint={endpoint} model={model} images={len(images)}", flush=True)
    comment = _request_completion(parts)

    # 模型偶尔会忽略字数要求。不要直接把一次过长结果判废：
    # 再用一次更强的短评指令重生成，仍超限才真正跳过。
    if len(comment) > AI_PROMPT_MAX_CHARS:
        print(f"[AI] generated chars={len(comment)} exceeds {AI_PROMPT_MAX_CHARS}; retry with stricter length", flush=True)
        retry_parts = list(parts) + [{
            "type": "text",
            "text": (
                f"重新生成，只输出一条自然车友评论，20到40个汉字左右，绝不能超过{AI_PROMPT_MAX_CHARS}字。"
                "只回应动态中明确出现的一项具体事实，不要总结多个卖点，不要使用“很详细”“很到位”等套话。"
            ),
        }]
        comment = _request_completion(retry_parts, max_tokens=80)
    if (not comment or len(comment) > MAX_COMMENT_CHARS or
            any(phrase.casefold() in comment.casefold() for phrase in _META_PHRASES) or
            any(phrase in comment for phrase in _PROMO_PHRASES) or
            _CONTACT_PATTERN.search(comment) or
            any(ord(char) < 32 for char in comment)):
        print(f"[AI] rejected response={comment}", flush=True)
        raise CommentGenerationError("模型评论内容无效")
    # 始终要求评论与标题/正文存在可核对的文字锚点；图片可以补充细节，但不能绕过文字依据校验。
    source_text = f"{title}\n{content}"
    source_pairs = {text[index:index + 2] for text in _CHINESE_PAIR.findall(source_text)
                    for index in range(len(text) - 1)}
    comment_pairs = {text[index:index + 2] for text in _CHINESE_PAIR.findall(comment)
                     for index in range(len(text) - 1)}
    if not source_pairs.intersection(comment_pairs) and not \
            _ascii_anchors(source_text).intersection(_ascii_anchors(comment)):
        raise CommentGenerationError("模型评论缺少原文依据")
    if len(comment) > AI_PROMPT_MAX_CHARS:
        raise CommentGenerationError(f"模型评论超过{AI_PROMPT_MAX_CHARS}字")
    print(f"[AI] generated chars={len(comment)} content={comment}", flush=True)
    return comment


__all__ = ["API_URL", "MAX_COMMENT_CHARS", "CommentGenerationError", "generate_comment"]
