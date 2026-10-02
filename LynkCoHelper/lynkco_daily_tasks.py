# -*- coding: utf-8 -*-
"""领克App 每日任务编排入口。"""
import json
import os
import sys
import time

from lynkco_common import mask_sensitive
from lynkco_login import load_token
from lynkco_notify import build_markdown_report, send_telegram_notification
from lynkco_sign import LynkCoSignClient
from lynkco_share import LynkCoShareClient

ENERGY_REFRESH_DELAY_SECONDS = float(os.environ.get("LYNKCO_ENERGY_DELAY", "5"))
EP_MY_ENERGY = "/app/energy/myEnergy"
EP_MEMBER_INFO = "/app/member/service/memberInFo"
EP_ENERGY_GRADE_INFO = "/app/user/privilegePackage/energyGradeInfo"


def get_my_energy(client: LynkCoSignClient) -> dict:
    return client._request("GET", EP_MY_ENERGY).json()


def get_member_info(client: LynkCoSignClient) -> dict:
    return client._request("GET", EP_MEMBER_INFO).json()


def get_energy_grade_info(client: LynkCoSignClient) -> dict:
    return client._request("GET", EP_ENERGY_GRADE_INFO).json()


def run_daily_tasks(token: str, do_share: bool = True) -> dict:
    result = {}
    sign_client = LynkCoSignClient(token)
    result["energy_before"] = get_my_energy(sign_client)
    result["member_before"] = get_member_info(sign_client)

    day_info = sign_client.get_sign_day_info()
    result["day_info"] = day_info
    already_signed = (day_info.get("data") or {}).get("signStatus") == 1
    result["already_signed"] = already_signed
    result["sign_result"] = None if already_signed else sign_client.do_sign()
    result["continue_info"] = sign_client.get_continue_days()

    if do_share:
        share_client = LynkCoShareClient(token)
        try:
            result["share_result"] = share_client.do_share()
        except Exception as e:
            result["share_result"] = {"ok": False, "detail": {"message": f"分享任务异常: {e}"}}
    else:
        result["share_result"] = None

    if not already_signed or (do_share and (result.get("share_result") or {}).get("ok")):
        time.sleep(ENERGY_REFRESH_DELAY_SECONDS)

    result["energy_after"] = get_my_energy(sign_client)
    result["member_after"] = get_member_info(sign_client)
    result["energy_grade_after"] = get_energy_grade_info(sign_client)
    return result


def run_and_notify() -> dict:
    token = load_token()
    print("=== 执行每日任务（签到+分享）===")
    result = run_daily_tasks(token, do_share=True)
    print(json.dumps(mask_sensitive(result), ensure_ascii=False, indent=2))

    markdown_body = build_markdown_report(result)
    print("\n=== 推送内容预览 ===")
    print(markdown_body)

    notify_results = {}
    try:
        notify_results["telegram"] = send_telegram_notification(
            title="领克App · 每日任务",
            markdown_body=markdown_body,
        )
    except Exception as e:
        print(f"[警告] Telegram 推送失败（不影响签到/分享结果）: {e}")
        notify_results["telegram"] = {"skipped": True, "error": str(e)}

    print("\n=== 推送结果 ===")
    print(json.dumps(mask_sensitive(notify_results), ensure_ascii=False, indent=2))
    result["notify_result"] = notify_results
    return result


def main():
    try:
        run_and_notify()
    except RuntimeError as e:
        print(f"[错误] {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[错误] 执行失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
