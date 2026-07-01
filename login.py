import os
import platform
import time
import random
import re
from typing import List, Dict, Optional, Tuple

import requests
from seleniumbase import SB
from pyvirtualdisplay import Display

LOGIN_URL = "https://betadash.lunes.host/login?next=/"
HOME_URL = "https://betadash.lunes.host/"
LOGOUT_URL = "https://betadash.lunes.host/logout"
SERVER_URL_TPL = "https://betadash.lunes.host/servers/{server_id}"

SCREENSHOT_DIR = "screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

EMAIL_SEL = "#email"
PASS_SEL = "#password"
SUBMIT_SEL = 'button.submit-btn[type="submit"]'
LOGOUT_SEL = 'a[href="/logout"].action-btn.ghost'
NOW_MANAGING_XPATH = 'xpath=//p[contains(normalize-space(.), "Now managing")]'
SERVER_CARD_LINK_SEL = 'a.server-card[href^="/servers/"]'


def mask_email_keep_domain(email: str) -> str:
    e = (email or "").strip()
    if "@" not in e:
        return "***"
    name, domain = e.split("@", 1)
    if len(name) <= 1:
        name_mask = name or "*"
    elif len(name) == 2:
        name_mask = name[0] + name[1]
    else:
        name_mask = name[0] + ("*" * (len(name) - 2)) + name[-1]
    return f"{name_mask}@{domain}"


def setup_xvfb():
    if platform.system().lower() == "linux" and not os.environ.get("DISPLAY"):
        display = Display(visible=False, size=(1920, 1080))
        display.start()
        os.environ["DISPLAY"] = display.new_display_var
        print("🖥️ Xvfb 已启动")
        return display
    return None


def screenshot(sb, name: str):
    path = f"{SCREENSHOT_DIR}/{name}"
    sb.save_screenshot(path)
    print(f"📸 {path}")


def tg_send(text: str, token: Optional[str] = None, chat_id: Optional[str] = None):
    token = (token or "").strip()
    chat_id = (chat_id or "").strip()
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=15,
        )
        resp.raise_for_status()
        print("📨 TG 推送成功")
    except Exception as e:
        print(f"⚠️ TG 发送失败：{e}")


def build_accounts_from_env() -> List[Dict[str, str]]:
    batch = (os.getenv("ACCOUNTS_BATCH") or "").strip()
    if not batch:
        raise RuntimeError("❌ 缺少环境变量：ACCOUNTS_BATCH")

    accounts: List[Dict[str, str]] = []
    for idx, raw in enumerate(batch.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split(",")]

        if len(parts) not in (2, 4):
            raise RuntimeError(
                f"❌ 第 {idx} 行格式不对（必须是 email,password 或 "
                f"email,password,tg_bot_token,tg_chat_id）：{raw!r}"
            )

        email, password = parts[0], parts[1]
        tg_token = parts[2] if len(parts) == 4 else ""
        tg_chat = parts[3] if len(parts) == 4 else ""

        if not email or not password:
            raise RuntimeError(f"❌ 第 {idx} 行存在空字段：{raw!r}")

        accounts.append({
            "email": email,
            "password": password,
            "tg_token": tg_token,
            "tg_chat": tg_chat,
        })

    if not accounts:
        raise RuntimeError("❌ ACCOUNTS_BATCH 里没有有效账号行")

    return accounts


def _has_cf_clearance(sb: SB) -> bool:
    try:
        cookies = sb.get_cookies()
        cf_clearance = next((c["value"] for c in cookies if c.get("name") == "cf_clearance"), None)
        print("🧩 cf_clearance:", "OK" if cf_clearance else "NONE")
        return bool(cf_clearance)
    except Exception:
        return False


def _try_click_captcha(sb: SB, stage: str):
    try:
        sb.uc_gui_click_captcha()
        time.sleep(3)
    except Exception as e:
        print(f"⚠️ captcha 点击异常（{stage}）：{e}")


def _is_logged_in(sb: SB) -> Tuple[bool, Optional[str]]:
    welcome_text = None
    try:
        if sb.is_element_visible("h1.hero-title"):
            welcome_text = (sb.get_text("h1.hero-title") or "").strip()
            if "welcome back" in welcome_text.lower():
                return True, welcome_text
    except Exception:
        pass
    try:
        if sb.is_element_visible(LOGOUT_SEL):
            return True, welcome_text
    except Exception:
        pass
    return False, welcome_text


def _extract_server_id_from_href(href: str) -> Optional[str]:
    if not href:
        return None
    m = re.search(r"/servers/(\d+)", href)
    return m.group(1) if m else None


def _find_server_id_and_go_server_page(sb: SB) -> Tuple[Optional[str], bool]:
    try:
        sb.wait_for_element_visible(SERVER_CARD_LINK_SEL, timeout=25)
    except Exception:
        screenshot(sb, f"server_card_not_found_{int(time.time())}.png")
        return None, False

    try:
        href = sb.get_attribute(SERVER_CARD_LINK_SEL, "href") or ""
    except Exception:
        href = ""

    server_id = _extract_server_id_from_href(href)
    if not server_id:
        screenshot(sb, f"server_id_extract_failed_{int(time.time())}.png")
        return None, False

    # 直接 open URL，跳过 click
    try:
        server_url = SERVER_URL_TPL.format(server_id=server_id)
        print(f"🧭 server_id={server_id}，打开：{server_url}")
        sb.open(server_url)
        sb.wait_for_element_visible(NOW_MANAGING_XPATH, timeout=30)
        return server_id, True
    except Exception:
        screenshot(sb, f"goto_server_failed_{int(time.time())}.png")
        return server_id, False


def _post_login_visit_then_logout(sb: SB) -> Tuple[Optional[str], bool]:
    server_id, entered_ok = _find_server_id_and_go_server_page(sb)
    if not entered_ok:
        return server_id, False

    stay1 = random.randint(4, 6)
    print(f"⏳ 服务器页停留 {stay1} 秒...")
    time.sleep(stay1)

    try:
        print(f"↩️ 返回首页：{HOME_URL}")
        sb.open(HOME_URL)
        sb.wait_for_element_visible("body", timeout=30)
    except Exception:
        screenshot(sb, f"back_home_failed_{int(time.time())}.png")
        return server_id, False

    stay2 = random.randint(3, 5)
    print(f"⏳ 首页停留 {stay2} 秒...")
    time.sleep(stay2)

    # 直接访问 /logout URL
    try:
        print(f"🚪 退出：{LOGOUT_URL}")
        sb.open(LOGOUT_URL)
        time.sleep(2)
        url_now = (sb.get_current_url() or "").lower()
        if "/login" in url_now or "/logout" in url_now:
            return server_id, True
        # 兜底：检查登录表单是否出现
        if sb.is_element_visible(EMAIL_SEL):
            return server_id, True
        screenshot(sb, f"logout_verify_failed_{int(time.time())}.png")
        return server_id, False
    except Exception as e:
        print(f"⚠️ 退出异常：{e}")
        screenshot(sb, f"logout_error_{int(time.time())}.png")
        return server_id, False


def login_then_flow_one_account(email: str, password: str) -> Tuple[str, Optional[str], bool, str, Optional[str], bool]:
    with SB(uc=True, locale="en", test=True) as sb:
        print("🚀 浏览器启动（UC Mode）")
        sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=5.0)
        time.sleep(2)

        try:
            sb.wait_for_element_visible(EMAIL_SEL, timeout=25)
            sb.wait_for_element_visible(PASS_SEL, timeout=25)
            sb.wait_for_element_visible(SUBMIT_SEL, timeout=25)
        except Exception:
            url_now = sb.get_current_url() or ""
            screenshot(sb, f"login_form_not_found_{int(time.time())}.png")
            return "FAIL", None, _has_cf_clearance(sb), url_now, None, False

        sb.clear(EMAIL_SEL)
        sb.type(EMAIL_SEL, email)
        sb.clear(PASS_SEL)
        sb.type(PASS_SEL, password)

        _try_click_captcha(sb, "提交前")
        sb.click(SUBMIT_SEL)
        sb.wait_for_element_visible("body", timeout=30)
        time.sleep(2)
        _try_click_captcha(sb, "提交后")

        has_cf = _has_cf_clearance(sb)
        current_url = (sb.get_current_url() or "").strip()

        welcome_text = None
        logged_in = False
        for _ in range(10):
            logged_in, welcome_text = _is_logged_in(sb)
            if logged_in:
                break
            time.sleep(1)

        if not logged_in:
            screenshot(sb, f"login_check_failed_{int(time.time())}.png")
            return "FAIL", welcome_text, has_cf, current_url, None, False

        server_id, logout_ok = _post_login_visit_then_logout(sb)

        try:
            current_url = (sb.get_current_url() or "").strip()
        except Exception:
            pass

        return "OK", welcome_text, has_cf, current_url, server_id, logout_ok


def main():
    accounts = build_accounts_from_env()
    display = setup_xvfb()

    ok = 0
    fail = 0
    logout_ok_count = 0
    tg_dests = set()

    try:
        for i, acc in enumerate(accounts, start=1):
            email = acc["email"]
            password = acc["password"]
            tg_token = (acc.get("tg_token") or "").strip()
            tg_chat = (acc.get("tg_chat") or "").strip()
            if tg_token and tg_chat:
                tg_dests.add((tg_token, tg_chat))

            safe_email = mask_email_keep_domain(email)

            print("\n" + "=" * 70)
            print(f"👤 [{i}/{len(accounts)}] 账号：{safe_email}")
            print("=" * 70)

            try:
                status, welcome_text, has_cf, url_now, server_id, logout_ok = login_then_flow_one_account(
                    email, password
                )

                if status == "OK":
                    ok += 1
                    if logout_ok:
                        logout_ok_count += 1
                    msg = (
                        f"✅ Lunes BetaDash 登录成功\n"
                        f"账号：{safe_email}\n"
                        f"server_id：{server_id or '未提取到'}\n"
                        f"welcome：{welcome_text or '未读取到'}\n"
                        f"退出：{'✅ 成功' if logout_ok else '❌ 失败'}\n"
                        f"当前页：{url_now}\n"
                        f"cf_clearance：{'OK' if has_cf else 'NONE'}"
                    )
                else:
                    fail += 1
                    msg = (
                        f"❌ Lunes BetaDash 登录失败\n"
                        f"账号：{safe_email}\n"
                        f"welcome：{welcome_text or '未检测到'}\n"
                        f"当前页：{url_now}\n"
                        f"cf_clearance：{'OK' if has_cf else 'NONE'}"
                    )

                print(msg)
                tg_send(msg, tg_token, tg_chat)

            except Exception as e:
                fail += 1
                msg = f"❌ Lunes BetaDash 脚本异常\n账号：{safe_email}\n错误：{e}"
                print(msg)
                tg_send(msg, tg_token, tg_chat)

            # 账号之间随机停顿，避免请求节奏过于规律
            if i < len(accounts):
                gap = random.randint(6, 12)
                print(f"⏳ 距下一账号等待 {gap} 秒...")
                time.sleep(gap)

        summary = f"📌 本次批量完成：登录成功 {ok} / 失败 {fail} | 退出成功 {logout_ok_count}/{ok}"
        print("\n" + summary)
        for token, chat in sorted(tg_dests):
            tg_send(summary, token, chat)

    finally:
        if display:
            display.stop()


if __name__ == "__main__":
    main()
