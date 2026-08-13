# Lunes AutoLogin

自动登录 [Lunes Host](https://betadash.lunes.host/) 保持免费实例不被 suspend。

仓库里提供两种登录方式，二选一（或都配置，互不影响）：

| 方式 | 脚本 | workflow | 原理 |
|---|---|---|---|
| Cookie 登录 | `login.py` | `main.yml` | 注入你手动登录后拿到的 Cookie，跳过表单和验证码 |
| 代理 + 账号密码登录 | `login_proxy.py` | `main_proxy.yml` | 走一个你自己的代理节点出网，再用账号密码自动填表登录 |

两种方式解决的是同一个问题的不同角度：Cookie 登录跳过了"填表 + 验证码"这一步，图省事但 Cookie 会过期；代理登录保留原来的账号密码自动登录，但换一个出口 IP，用来绕开 GitHub Actions 默认出口 IP 被 Cloudflare/风控盯上导致登录失败的情况。如果你的账号密码登录本来就是被 IP 风控卡住的，代理这个方案更对症；如果是表单/验证码逻辑变了导致登录不了，Cookie 方案更对症。

## 文件说明

- `login.py` — Cookie 登录主脚本
- `login_proxy.py` — 代理 + 账号密码登录主脚本
- `scripts/setup_proxy.sh` — 根据 `NODE_LINK` 节点链接启动本地 sing-box 代理（参考自 [chennlink/Auto-Renew-Bothosting](https://github.com/chennlink/Auto-Renew-Bothosting)）
- `requirements.txt` — Python 依赖，两个脚本共用
- `.github/workflows/main.yml` — Cookie 登录定时任务
- `.github/workflows/main_proxy.yml` — 代理 + 账号密码登录定时任务

## 方式一：Cookie 登录（`login.py`）

使用 SeleniumBase (UC Mode) 自动化浏览器打开 Lunes BetaDash 面板，注入你手动登录后拿到的 Cookie 完成登录态，然后进入服务器页 → 返回首页，完成一次"续期"访问。

### 如何获取 Cookie

1. 用浏览器（Chrome/Edge 均可）正常登录 https://betadash.lunes.host/
2. 登录成功后按 F12 打开开发者工具，切到 **Network（网络）** 标签
3. 刷新一下页面，随便点一个发往 `betadash.lunes.host` 的请求
4. 在右侧 **Headers → Request Headers** 里找到 `Cookie:` 这一行，把冒号后面的**完整内容**复制下来，形如：

   ```
   session=eyJhbGciOiJIUzI1NiJ9...; cf_clearance=xxxxxxxx
   ```

5. 这一整串就是下面要填的 `cookie字符串`

⚠️ 注意：
- Cookie 是敏感信息，等同于你的登录凭证，不要泄露给别人
- Cookie 一般有效期几天到几周，过期后脚本会登录失败，需要重复上面步骤重新获取

### 配置

在仓库 Settings → Secrets and variables → Actions 中添加：

**`ACCOUNTS_BATCH`** — 每行一个账号，格式：

```
cookie字符串
cookie字符串||tg_bot_token||tg_chat_id
```

- 1 段：只续期，不发 TG 通知
- 3 段：续期后通过 TG Bot 推送结果
- 注意：用 `||`（两个竖线）分隔 cookie 和 TG 字段，因为 Cookie 本身可能包含逗号

**`LOGOUT_AFTER_RUN`**（可选）— 设为 `1` 表示每次跑完后退出登录（会让当前 Cookie 失效，下次需要重新获取）；不设置或设为其他值则默认保留会话，Cookie 可以继续沿用到过期为止。

---

## 方式二：代理 + 账号密码登录（`login_proxy.py`）

保留原来"自动填表登录"的逻辑，但浏览器和相关请求都会先走你自己配置的代理节点再访问 Lunes，用来规避 GitHub Actions 出口 IP 被识别限制的问题。

### 配置

在仓库 Settings → Secrets and variables → Actions 中添加：

**`ACCOUNTS_BATCH`** — 每行一个账号，格式：

```
email,password
email,password,tg_bot_token,tg_chat_id
```

- 2 列：只登录，不发 TG 通知
- 4 列：登录后通过 TG Bot 推送结果
- 注意：按逗号分割，密码中不能包含逗号

**`NODE_LINK`**（可选）— 你的代理节点分享链接，支持 `vless://`、`vmess://`、`trojan://`、`hysteria2://`（或 `hy2://`）、`tuic://`、`anytls://`、`socks5://` 等格式。不填的话会自动降级为直连（相当于没用代理）。

> `scripts/setup_proxy.sh` 会用这个节点信息在 Actions runner 本地拉起一个 sing-box 进程，监听 `127.0.0.1:1080`（socks5）；`login_proxy.py` 会自动读取上一步写入 `$GITHUB_ENV` 的 `IS_PROXY` / `PROXY_SERVER`，浏览器和取出口 IP 的请求都会走这个代理。代理连不上时会自动降级为直连，不会导致整个 workflow 失败，但登录大概率还是会跟之前一样失败——毕竟直连没变。

### 运行时会看到什么

日志里会打印当前出口 IP（`get_current_ip()`），方便你确认代理是否真的生效；TG 通知里也会带上这个 IP，方便排查"到底是代理没生效，还是账号密码本身有问题"。

### 手动触发 / 排查失败

和方式一一样，Actions 页面点 "Run workflow" 手动跑；失败截图同样会传到 `login-screenshots` Artifact。如果代理连接失败，日志里会打出 sing-box 的日志（`sing-box.log`），可以先确认节点链接本身是否有效、有没有被墙。

---

## 运行频率

当前 cron 为 `0 2 */6 * *`，也就是每月的 1、7、13、19、25、31 号 UTC 2 点跑一次。因为 cron 按日历字段计算而非按"运行间隔"计算，跨月边界时实际间隔会有 1~6 天的浮动（例如 31 号跑完，下次是下月 1 号）。如果想要更均匀的节奏，可以改成 `0 2 */5 * *` 之类更简单整除的写法，或者接受这个浮动。

## 手动触发

Actions 页面点击 "Run workflow" 即可手动执行。

## 排查失败

- 登录失败时脚本会把截图存到 `screenshots/` 目录，workflow 会作为 `login-screenshots` Artifact 上传，7 天内可下载查看当时页面状态。
- 如果日志里频繁出现 `captcha 点击异常`，检查 `requirements.txt` 是否包含 `pyautogui` 和 `pillow`（这是 `uc_gui_click_captcha()` 依赖的库，缺失会导致验证码点击静默失败）。

## 致谢

- Cookie 登录方式参考 [yeye296/auto_login_lunes](https://github.com/yeye296/auto_login_lunes)
- 代理搭建脚本（sing-box 节点解析）参考 [chennlink/Auto-Renew-Bothosting](https://github.com/chennlink/Auto-Renew-Bothosting)
