# -*- coding: utf-8 -*-
"""ChatAnywhere provider for short, grounded comments."""

import ipaddress
import re
from urllib.parse import urlsplit

import requests

from lynkco_common import AI_PROMPT_MAX_CHARS, AI_TIMEOUT, MAX_COMMENT_CHARS


API_URL = "https://api.chatanywhere.tech/v1/chat/completions"
FALLBACK_API_URL = "https://api.chatanywhere.org/v1/chat/completions"
AI_RETRY_DELAYS = (0, 2, 5)
SYSTEM_PROMPT = (
    f"你是领克社区里一位普通车友，刚看完这条动态，顺手留一句中文评论，控制在15到{AI_PROMPT_MAX_CHARS}字。"
    "目标不是写得漂亮，而是像真人在车友群里聊天：自然、具体、有一点个人语气，不端着，不像宣传文案。"
    "先理解作者真正分享的内容，再挑一个最值得回应的细节；优先用口语表达，可以有短句、轻微停顿或自然感叹，但不要刻意装随意。"
    "评论要能看出你确实看了这篇动态，尽量点到标题或正文中的车型、里程、用车场景、改装细节、路线或作者的具体感受。"
    "不要只是复述标题，也不要把几个卖点逐项总结；围绕一个细节说一句真实反应，必要时补充一个贴题的小观察，或自然地问一个确实值得问的问题。"
    "每条评论根据内容决定语气和句式：有时简短回应，有时聊细节，有时提出问题；不要每次都用‘确实’‘看来’‘这波’开头，不要每条都夸赞或都以问句结尾。"
    "避免像评测文章、销售话术或官方宣传：不要堆砌‘颜值在线’‘实力出众’‘值得入手’‘兼顾舒适与实用’等套话，也不要过度热情、硬凑梗或滥用感叹号和表情。"
    "不要虚构自己开过这辆车、做过同样改装或有过相同经历；不要臆测动力、底盘、续航、油耗、舒适性等帖子未明确表达的事实。"
    "图片只能用于描述直接可见的细节，不能据此猜测看不出的配置或性能。内容不足时就自然地短回应，不要硬编观点。"
    "只输出一条评论正文，不加引号、前缀、解释，不提及AI或分析过程。"
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
        endpoints = [getattr(request_session, "endpoint", API_URL) or API_URL]
        for endpoint in (API_URL, FALLBACK_API_URL):
            if endpoint not in endpoints:
                endpoints.append(endpoint)
        last_error = None
        for endpoint in endpoints:
            for attempt, delay in enumerate(AI_RETRY_DELAYS):
                if delay:
                    import time
                    print(f"[AI] retrying endpoint={endpoint} after {delay}s (attempt {attempt + 1}/{len(AI_RETRY_DELAYS)})", flush=True)
                    time.sleep(delay)
                try:
                    response = request_session.post(
                        endpoint, json=payload,
                        headers={"Authorization": f"Bearer {api_key}"}, timeout=AI_TIMEOUT,
                    )
                    print(f"[AI] response endpoint={endpoint} status={response.status_code}", flush=True)
                    if response.status_code == 429:
                        print(f"[AI] response body={_response_text(response, api_key)}", flush=True)
                        if attempt + 1 < len(AI_RETRY_DELAYS):
                            continue
                        raise CommentGenerationError("模型服务繁忙（HTTP 429），请稍后重试")
                    if response.status_code in (401, 403):
                        print(f"[AI] response body={_response_text(response, api_key)}", flush=True)
                        raise CommentGenerationError("模型 API Key 无效或无权限")
                    if response.status_code != 200:
                        print(f"[AI] response body={_response_text(response, api_key)}", flush=True)
                        if attempt + 1 < len(AI_RETRY_DELAYS):
                            continue
                        break
                    try:
                        choice = response.json()["choices"][0]
                        finish_reason = choice["finish_reason"]
                        result = choice["message"]["content"]
                    except (ValueError, TypeError, KeyError, IndexError):
                        raise CommentGenerationError("模型响应格式无效") from None
                    if finish_reason != "stop" or not isinstance(result, str):
                        raise CommentGenerationError("模型输出未完成")
                    return result.strip()
                except CommentGenerationError:
                    raise
                except requests.RequestException as exc:
                    last_error = exc
                    print(f"[AI] request error endpoint={endpoint} attempt={attempt + 1}/{len(AI_RETRY_DELAYS)}: {type(exc).__name__}: {exc}", flush=True)
                    if attempt + 1 == len(AI_RETRY_DELAYS):
                        break
        raise CommentGenerationError("模型请求失败") from None

    request_session = session or requests.Session()
    endpoint = getattr(request_session, "endpoint", API_URL) or API_URL
    print(f"[AI] request endpoints={endpoint} -> {API_URL} -> {FALLBACK_API_URL} model={model} images={len(images)}", flush=True)
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


__all__ = ["API_URL", "FALLBACK_API_URL", "MAX_COMMENT_CHARS", "CommentGenerationError", "generate_comment"]
