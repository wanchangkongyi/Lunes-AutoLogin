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
# 提交按钮的文案/样式可能会随验证码通过与否变化（比如从 "Sign in" 变成
# "Continue to dashboard"），所以做成候选列表，依次尝试。
SUBMIT_SELECTORS = [
    'button.submit-btn[type="submit"]',
    'button:contains("Continue to dashboard")',
    'button:contains("Sign in")',
    'button[type="submit"]',
]
LOGOUT_SEL = 'a[href="/logout"].action-btn.ghost'
NOW_MANAGING_XPATH = 'xpath=//p[contains(normalize-space(.), "Now managing")]'
SERVER_CARD_LINK_SEL = 'a.server-card[href^="/servers/"]'

# 代理配置：由 scripts/setup_proxy.sh 在 GitHub Actions 里通过 $GITHUB_ENV 注入，
# 本地跑的话可以自己 export IS_PROXY=true / PROXY_SERVER=socks5://127.0.0.1:1080
IS_PROXY = (os.getenv("IS_PROXY") or "false").strip().lower() == "true"
PROXY_SERVER = (os.getenv("PROXY_SERVER") or "").strip() or "socks5://127.0.0.1:1080"


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


def get_current_ip(proxy_server: str = "") -> str:
    """探测当前出口 IP，方便确认代理是否真的生效。"""
    proxies = {"http": proxy_server, "https": proxy_server} if proxy_server else None
    try:
        resp = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
        resp.raise_for_status()
        return resp.text.strip()
    except Exception as e:
        return f"获取失败：{e}"


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


CF_TURNSTILE_IFRAME_SEL = 'iframe[src*="challenges.cloudflare.com"]'
# Turnstile 验证通过后，跨域 iframe 会通过 postMessage 把 token 写进主文档
# 这个 hidden input 里——这是唯一能从主文档里可靠判断"验证是否真的通过"
# 的信号，比读页面文字/猜 iframe 状态准得多。
TURNSTILE_RESPONSE_SEL = 'input[name="cf-turnstile-response"]'


def _wait_turnstile_rendered(sb: SB, timeout: int = 12) -> bool:
    """等 Turnstile 的 iframe 真正渲染出来再动手，避免验证码组件还没加载
    完就被误判成'不存在'而漏点。"""
    try:
        sb.wait_for_element_present(CF_TURNSTILE_IFRAME_SEL, timeout=timeout)
        return True
    except Exception:
        return False


def _turnstile_token_present(sb: SB) -> bool:
    try:
        val = sb.get_attribute(TURNSTILE_RESPONSE_SEL, "value") or ""
        return len(val.strip()) > 0
    except Exception:
        return False


def _wait_turnstile_verified(sb: SB, timeout: int = 15) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if _turnstile_token_present(sb):
            return True
        time.sleep(1)
    return False


def _solve_captcha(sb: SB, stage: str):
    """挂了代理之后 Cloudflare 大多会自动验证通过，不需要真的去点。
    所以先等一等看它是否自动过了（轮询 token 是否已经写入），只有等
    超时还没通过，才尝试点一次兜底——这一步固定放在填表单之前，就算
    没点准，此时表单还是空的，也不会把已填内容冲掉。
    """
    print(f"⏳ 等待验证码自动通过（{stage}）...")
    if _wait_turnstile_verified(sb, timeout=15):
        print(f"✅ 验证码已自动通过（{stage}）")
        return

    if not _wait_turnstile_rendered(sb, timeout=8):
        print(f"ℹ️ 未检测到验证码 iframe（{stage}），可能本次不需要验证")
        return

    print(f"🔒 自动验证未通过，尝试点击一次兜底（{stage}）...")
    try:
        sb.uc_gui_click_captcha()
    except Exception as e:
        print(f"⚠️ captcha 点击异常（{stage}）：{e}")
    if _wait_turnstile_verified(sb, timeout=15):
        print(f"✅ 点击后验证码已通过（{stage}）")
    else:
        print(f"⚠️ 点击后仍未检测到验证码通过（{stage}），继续往下走，看提交结果")


def _fill_field_verified(sb: SB, selector: str, value: str, label: str, attempts: int = 3) -> bool:
    """输入并校验输入框确实有值，避免被验证码刷新/页面重渲染悄悄清空。"""
    for i in range(1, attempts + 1):
        try:
            sb.clear(selector)
            sb.type(selector, value)
        except Exception as e:
            print(f"⚠️ 填写 {label} 失败（第 {i} 次）：{e}")
            time.sleep(1)
            continue

        try:
            current = sb.get_value(selector) or ""
        except Exception:
            current = ""

        if current == value:
            return True

        print(f"⚠️ {label} 填写后校验不一致（第 {i} 次），重试...")
        time.sleep(1)

    return False


def _click_submit(sb: SB) -> bool:
    for selector in SUBMIT_SELECTORS:
        try:
            if sb.is_element_visible(selector):
                print(f"🖱️ 点击提交按钮：{selector}")
                sb.click(selector)
                return True
        except Exception:
            continue
    print("❌ 未找到可点击的提交按钮")
    return False


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


def _attempt_form_login(sb: SB, email: str, password: str, attempt_no: int) -> bool:
    """点验证码 → 填表单 → 提交，一次完整的尝试。"""
    print(f"\n--- 第 {attempt_no} 次登录尝试 ---")
    _solve_captcha(sb, f"第{attempt_no}次-登录前")

    email_ok = _fill_field_verified(sb, EMAIL_SEL, email, "邮箱")
    pass_ok = _fill_field_verified(sb, PASS_SEL, password, "密码")

    if not (email_ok and pass_ok):
        screenshot(sb, f"fill_form_failed_attempt{attempt_no}_{int(time.time())}.png")
        return False

    if not _click_submit(sb):
        screenshot(sb, f"submit_not_found_attempt{attempt_no}_{int(time.time())}.png")
        return False

    sb.wait_for_element_visible("body", timeout=30)
    time.sleep(3)
    return True


def login_then_flow_one_account(email: str, password: str) -> Tuple[str, Optional[str], bool, str, Optional[str], bool, str]:
    sb_kwargs = {"uc": True, "locale": "en", "test": True}
    if IS_PROXY:
        print(f"🔗 挂载代理: {PROXY_SERVER}")
        sb_kwargs["proxy"] = PROXY_SERVER
    else:
        print("🍭 未使用代理，直连访问")

    exit_ip = get_current_ip(PROXY_SERVER if IS_PROXY else "")
    print(f"📍 当前出口 IP: {exit_ip}")

    with SB(**sb_kwargs) as sb:
        print("🚀 浏览器启动（UC Mode）")
        sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=5.0)
        time.sleep(2)

        try:
            sb.wait_for_element_visible(EMAIL_SEL, timeout=25)
            sb.wait_for_element_visible(PASS_SEL, timeout=25)
        except Exception:
            url_now = sb.get_current_url() or ""
            screenshot(sb, f"login_form_not_found_{int(time.time())}.png")
            return "FAIL", None, _has_cf_clearance(sb), url_now, None, False, exit_ip

        welcome_text = None
        logged_in = False
        has_cf = False
        current_url = ""

        # 提交表单失败一次不直接判死刑，Turnstile 本身就有一定几率没点中/
        # 没验证成功，再完整走一遍"点验证码→填表单→提交"通常就能过。
        max_attempts = 2
        for attempt_no in range(1, max_attempts + 1):
            if not _attempt_form_login(sb, email, password, attempt_no):
                current_url = sb.get_current_url() or ""
                has_cf = _has_cf_clearance(sb)
                continue

            has_cf = _has_cf_clearance(sb)
            current_url = (sb.get_current_url() or "").strip()

            for _ in range(10):
                logged_in, welcome_text = _is_logged_in(sb)
                if logged_in:
                    break
                time.sleep(1)

            if logged_in:
                break

            print(f"⚠️ 第 {attempt_no} 次登录后未检测到已登录状态，当前页：{current_url}")
            screenshot(sb, f"login_check_failed_attempt{attempt_no}_{int(time.time())}.png")

            # 如果已经跳出登录页（比如进了 dashboard 但检测条件没命中），
            # 就不要再重新提交表单了，避免重复操作已登录状态下的页面。
            if "/login" not in current_url:
                break

            # 还停在登录页，说明确实没登录成功，回到登录页重新走一遍再试
            if attempt_no < max_attempts:
                sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=5.0)
                time.sleep(2)
                try:
                    sb.wait_for_element_visible(EMAIL_SEL, timeout=25)
                    sb.wait_for_element_visible(PASS_SEL, timeout=25)
                except Exception:
                    break

        if not logged_in:
            return "FAIL", welcome_text, has_cf, current_url, None, False, exit_ip

        server_id, logout_ok = _post_login_visit_then_logout(sb)

        try:
            current_url = (sb.get_current_url() or "").strip()
        except Exception:
            pass

        return "OK", welcome_text, has_cf, current_url, server_id, logout_ok, exit_ip


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
                status, welcome_text, has_cf, url_now, server_id, logout_ok, exit_ip = login_then_flow_one_account(
                    email, password
                )

                if status == "OK":
                    ok += 1
                    if logout_ok:
                        logout_ok_count += 1
                    msg = (
                        f"✅ Lunes BetaDash 登录成功（代理+账号密码）\n"
                        f"账号：{safe_email}\n"
                        f"出口IP：{exit_ip}\n"
                        f"server_id：{server_id or '未提取到'}\n"
                        f"welcome：{welcome_text or '未读取到'}\n"
                        f"退出：{'✅ 成功' if logout_ok else '❌ 失败'}\n"
                        f"当前页：{url_now}\n"
                        f"cf_clearance：{'OK' if has_cf else 'NONE'}"
                    )
                else:
                    fail += 1
                    msg = (
                        f"❌ Lunes BetaDash 登录失败（代理+账号密码）\n"
                        f"账号：{safe_email}\n"
                        f"出口IP：{exit_ip}\n"
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
