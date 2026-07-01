# Lunes AutoLogin

自动登录 [Lunes Host](https://betadash.lunes.host/) 保持免费实例不被 suspend。

## 原理

使用 SeleniumBase (UC Mode) 自动化浏览器登录 Lunes BetaDash 面板，绕过 Cloudflare Turnstile 验证，完成登录 → 进入服务器页 → 返回首页 → 退出。

## 文件说明

- `login.py` — 主脚本，放在仓库根目录
- `requirements.txt` — Python 依赖，放在仓库根目录
- `.github/workflows/main.yml` — 定时任务

## 配置

在仓库 Settings → Secrets and variables → Actions 中添加：

**`ACCOUNTS_BATCH`** — 每行一个账号，格式：

```
email,password
email,password,tg_bot_token,tg_chat_id
```

- 2 列：只登录，不发 TG 通知
- 4 列：登录后通过 TG Bot 推送结果
- 注意：按逗号分割，密码中不能包含逗号

## 运行频率

当前 cron 为 `0 2 */6 * *`，也就是每月的 1、7、13、19、25、31 号 UTC 2 点跑一次。因为 cron 按日历字段计算而非按"运行间隔"计算，跨月边界时实际间隔会有 1~6 天的浮动（例如 31 号跑完，下次是下月 1 号）。如果想要更均匀的节奏，可以改成 `0 2 */5 * *` 之类更简单整除的写法，或者接受这个浮动。

## 手动触发

Actions 页面点击 "Run workflow" 即可手动执行。

## 排查失败

- 登录失败时脚本会把截图存到 `screenshots/` 目录，workflow 会作为 `login-screenshots` Artifact 上传，7 天内可下载查看当时页面状态。
- 如果日志里频繁出现 `captcha 点击异常`，检查 `requirements.txt` 是否包含 `pyautogui` 和 `pillow`（这是 `uc_gui_click_captcha()` 依赖的库，缺失会导致验证码点击静默失败）。

## 致谢

参考 [yeye296/auto_login_lunes](https://github.com/yeye296/auto_login_lunes)
