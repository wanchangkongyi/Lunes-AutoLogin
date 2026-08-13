# Lunes AutoLogin

自动登录 [Lunes Host](https://betadash.lunes.host/) 保持免费实例不被 suspend。

仓库里提供两种登录方式，二选一（或都配置，互不影响）：

| 方式 | 脚本 | workflow | 原理 |
|---|---|---|---|
| Cookie 登录 | `login.py` | `main.yml` | 注入你手动登录后拿到的 Cookie，跳过表单和验证码 |
| 代理 + 账号密码登录 | `login_proxy.py` | `main_proxy.yml` | 走一个你自己的代理节点出网，再用账号密码自动填表登录 |

## 方式一：Cookie 登录（`login.py`）

使用 SeleniumBase (UC Mode) 自动化浏览器打开 Lunes BetaDash 面板，注入你手动登录后拿到的 Cookie 完成登录态，然后进入服务器页 → 返回首页，完成一次"续期"访问。

### 配置

在仓库 Settings → Secrets and variables → Actions 中添加：

**`ACCOUNTS_BATCH`** — 每行一个账号，格式：

```
cookie字符串
cookie字符串||tg_bot_token||tg_chat_id
```

- 1 段：只续期，不发 TG 通知
- 3 段：续期后通过 TG Bot 推送结果
- 注意：用 `||`（两个竖线）分隔 cookie 和 TG 字段

---

## 方式二：代理 + 账号密码登录（`login_proxy.py`）

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


## 致谢

- Cookie 登录方式参考 [yeye296/auto_login_lunes](https://github.com/yeye296/auto_login_lunes)
- 代理搭建脚本（sing-box 节点解析）参考 [chennlink/Auto-Renew-Bothosting](https://github.com/chennlink/Auto-Renew-Bothosting)
