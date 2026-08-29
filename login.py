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
LOGOUT_SEL = 'a[href="/logout"].action-btn.ghost'
NOW_MANAGING_XPATH = 'xpath=//p[contains(normalize-space(.), "Now managing")]'
SERVER_CARD_LINK_SEL = 'a.server-card[href^="/servers/"]'

# 跑完一次后是否退出登录。Cookie 登录模式下退出会让这个 Cookie 失效，
# 下次还得重新手动复制，所以默认不退出。如果你想保留原来"登出"的行为，
# 在仓库 Secrets/Variables 里加一个 LOGOUT_AFTER_RUN=1 即可。
LOGOUT_AFTER_RUN = (os.getenv("LOGOUT_AFTER_RUN") or "").strip() == "1"


def mask_cookie(cookie_str: str) -> str:
    s = (cookie_str or "").strip()
    if len(s) <= 12:
        return "***"
    return f"{s[:6]}...{s[-6:]} (len={len(s)})"


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


def parse_cookie_string(cookie_str: str) -> List[Dict[str, str]]:
    """把 'name1=value1; name2=value2' 形式的 Cookie 请求头拆成 selenium 需要的 dict 列表。"""
    cookies = []
    for part in (cookie_str or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookies.append({"name": name, "value": value})
    return cookies


def build_accounts_from_env() -> List[Dict[str, str]]:
    """
    ACCOUNTS_BATCH 每行一个账号，格式：

        cookie字符串
        cookie字符串||tg_bot_token||tg_chat_id

    cookie字符串就是浏览器里已登录状态下，发往 betadash.lunes.host 的请求头里
    完整的 Cookie 值（devtools -> Network -> 随便一个请求 -> Request Headers -> Cookie），
    形如：session=eyJhbGci...; cf_clearance=xxxx
    用 '||' 而不是逗号分隔 TG 字段，是因为 Cookie 本身可能包含逗号。
    """
    batch = (os.getenv("ACCOUNTS_BATCH") or "").strip()
    if not batch:
        raise RuntimeError("❌ 缺少环境变量：ACCOUNTS_BATCH")

    accounts: List[Dict[str, str]] = []
    for idx, raw in enumerate(batch.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split("||")]

        if len(parts) not in (1, 3):
            raise RuntimeError(
                f"❌ 第 {idx} 行格式不对（必须是 cookie 或 "
                f"cookie||tg_bot_token||tg_chat_id）：{raw!r}"
            )

        cookie_str = parts[0]
        tg_token = parts[1] if len(parts) == 3 else ""
        tg_chat = parts[2] if len(parts) == 3 else ""

        if not cookie_str or "=" not in cookie_str:
            raise RuntimeError(f"❌ 第 {idx} 行 cookie 为空或格式不对：{raw!r}")

        accounts.append({
            "cookie": cookie_str,
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


CF_INDICATORS = ["verify you are human", "确认您是真人", "just a moment", "checking your browser"]


def _cf_challenge_present(sb: SB) -> bool:
    """判断当前页面是否还存在【未通过】的 Cloudflare 验证码，避免在验证码
    已经自动通过的情况下继续盲点，误触发页面上其他元素。"""
    try:
        src = (sb.get_page_source() or "").lower()
    except Exception:
        return False

    if any(x in src for x in CF_INDICATORS):
        return True

    try:
        has_iframe = sb.is_element_present('iframe[src*="challenges.cloudflare.com"]')
    except Exception:
        has_iframe = False

    if has_iframe and "success" not in src:
        return True

    return False


def _try_click_captcha(sb: SB, stage: str):
    if not _cf_challenge_present(sb):
        print(f"ℹ️ 未检测到需要处理的验证码，跳过点击（{stage}）")
        return
    print(f"🔒 检测到未通过的验证码，尝试点击（{stage}）...")
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


def _post_login_visit_then_maybe_logout(sb: SB) -> Tuple[Optional[str], bool]:
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

    if not LOGOUT_AFTER_RUN:
        # Cookie 登录模式：默认不登出，保留会话，方便下次继续用同一个 cookie。
        print("ℹ️ 跳过登出（LOGOUT_AFTER_RUN 未开启），保留会话")
        return server_id, True

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


def login_then_flow_one_account(cookie_str: str) -> Tuple[str, Optional[str], bool, str, Optional[str], bool]:
    cookies = parse_cookie_string(cookie_str)
    if not cookies:
        return "FAIL", None, False, "", None, False

    with SB(uc=True, locale="en", test=True) as sb:
        print("🚀 浏览器启动（UC Mode）")

        # 必须先打开一次目标域名，才能往这个域名下注入 cookie
        sb.uc_open_with_reconnect(HOME_URL, reconnect_time=5.0)
        time.sleep(2)
        _try_click_captcha(sb, "注入 cookie 前")

        print(f"🍪 注入 {len(cookies)} 个 cookie")
        for c in cookies:
            try:
                sb.add_cookie(c)
            except Exception as e:
                print(f"⚠️ 添加 cookie {c['name']} 失败：{e}")

        # 注入完成后刷新，让登录态生效
        sb.uc_open_with_reconnect(HOME_URL, reconnect_time=5.0)
        sb.wait_for_element_visible("body", timeout=30)
        time.sleep(2)
        _try_click_captcha(sb, "刷新后")

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

        server_id, logout_ok = _post_login_visit_then_maybe_logout(sb)

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
            cookie_str = acc["cookie"]
            tg_token = (acc.get("tg_token") or "").strip()
            tg_chat = (acc.get("tg_chat") or "").strip()
            if tg_token and tg_chat:
                tg_dests.add((tg_token, tg_chat))

            safe_cookie = mask_cookie(cookie_str)

            print("\n" + "=" * 70)
            print(f"👤 [{i}/{len(accounts)}] cookie：{safe_cookie}")
            print("=" * 70)

            try:
                status, welcome_text, has_cf, url_now, server_id, logout_ok = login_then_flow_one_account(
                    cookie_str
                )

                if status == "OK":
                    ok += 1
                    if logout_ok:
                        logout_ok_count += 1
                    msg = (
                        f"✅ Lunes BetaDash 续期成功（Cookie 登录）\n"
                        f"cookie：{safe_cookie}\n"
                        f"server_id：{server_id or '未提取到'}\n"
                        f"welcome：{welcome_text or '未读取到'}\n"
                        f"退出：{'✅ 已登出' if LOGOUT_AFTER_RUN else '⏭️ 保留会话'}\n"
                        f"当前页：{url_now}\n"
                        f"cf_clearance：{'OK' if has_cf else 'NONE'}"
                    )
                else:
                    fail += 1
                    msg = (
                        f"❌ Lunes BetaDash 续期失败（Cookie 登录）\n"
                        f"cookie：{safe_cookie}\n"
                        f"welcome：{welcome_text or '未检测到'}\n"
                        f"当前页：{url_now}\n"
                        f"cf_clearance：{'OK' if has_cf else 'NONE'}\n"
                        f"⚠️ 大概率是 cookie 已过期，需要重新登录浏览器复制新的 Cookie"
                    )

                print(msg)
                tg_send(msg, tg_token, tg_chat)

            except Exception as e:
                fail += 1
                msg = f"❌ Lunes BetaDash 脚本异常\ncookie：{safe_cookie}\n错误：{e}"
                print(msg)
                tg_send(msg, tg_token, tg_chat)

            # 账号之间随机停顿，避免请求节奏过于规律
            if i < len(accounts):
                gap = random.randint(6, 12)
                print(f"⏳ 距下一账号等待 {gap} 秒...")
                time.sleep(gap)

        summary = f"📌 本次批量完成：续期成功 {ok} / 失败 {fail} | 登出 {logout_ok_count}/{ok}"
        print("\n" + summary)
        for token, chat in sorted(tg_dests):
            tg_send(summary, token, chat)

    finally:
        if display:
            display.stop()


if __name__ == "__main__":
    main()
