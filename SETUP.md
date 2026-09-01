# AI Telegram 资讯频道（GitHub Actions 白嫖版）

RSS 抓 AI 资讯 →（可选）Groq 中文摘要 → 推到 Telegram 频道。  
**不需要买服务器**，公开仓库 + Actions 定时跑即可。密钥放 Secrets，不会公开。

当前频道：`@rix_tech_share`（rix科技风向标-资源分享）  
机器人：`@rix_new_admin_bot`（须为频道管理员，并开启 Post Messages）

---

## 你需要准备的东西（最少 2 个）

| # | 东西 | 必填？ | 怎么拿 |
|---|------|--------|--------|
| 1 | `TELEGRAM_BOT_TOKEN` | 必填 | BotFather；若曾发到聊天先 `/revoke` 换新 |
| 2 | `TELEGRAM_CHAT_ID` | 必填 | 已定为 `@rix_tech_share` |
| 3 | `GROQ_API_KEY` | 可选 | console.groq.com，没有就只推标题+链接 |
| 4 | GitHub 账号 | 必填 | 用来建公开仓库、开 Actions |

---

## 第一步：创建 Telegram 机器人

1. 打开 Telegram，搜索 **`@BotFather`**，点 Start
2. 发送：`/newbot`
3. 按提示起名字（显示名），例如：`AI 风向资讯`
4. 再起一个 **username**（必须以 `bot` 结尾），例如：`my_ai_news_bot`
5. BotFather 会给你一串 **Token**，形如 `7123456789:AAH...`  
   → **复制保存**，这就是 `TELEGRAM_BOT_TOKEN`  
   → **不要发给任何人，不要贴到聊天/代码里**

可选：发 `/setdescription`、`/setabouttext` 写简介，方便以后被搜到。

---

## 第二步：创建公开频道（推荐）并拉机器人进频道

1. Telegram → 右上角菜单 → **New Channel**（新建频道）
2. 名称例如：`AI 资讯风向`
3. 选 **Public Channel**（公开），设一个链接用户名，例如：`my_ai_news_cn`  
   → 完整链接就是 `https://t.me/my_ai_news_cn`
4. 进频道 → 频道名 → **Administrators** → **Add Admin** → 搜你的 bot → 添加  
5. 权限至少打开：**Post Messages**（发消息）
6. `TELEGRAM_CHAT_ID` 可以直接填：`@my_ai_news_cn`（你的公开用户名）

### 如果是私有频道 / 普通群，怎么拿数字 ID？

1. 把机器人拉进频道/群并给管理员权限  
2. 在频道里随便发一条消息  
3. 浏览器打开（把 TOKEN 换成你的）：  
   `https://api.telegram.org/bot<TOKEN>/getUpdates`  
4. 在返回 JSON 里找 `"chat":{"id":-100xxxxxxxxxx`  
   → 那个负数就是 `TELEGRAM_CHAT_ID`

也可临时用第三方助手 bot（如 `@userinfobot` / `@getidsbot`）看 ID，用完可移除。

---

## 第三步：（可选）申请 Groq 免费 Key

1. 打开 https://console.groq.com/  
2. 注册登录 → **API Keys** → Create API Key  
3. 复制保存为 `GROQ_API_KEY`  
没有也没关系，脚本会只发「标题 + 来源 + 链接」。

---

## 第四步：把本项目推到 GitHub 公开仓库

在本机打开终端，进入本目录后执行（把 `你的用户名` 和仓库名改掉）：

```bash
cd ai-telegram-news
git init
git add .
git commit -m "init: AI news telegram bot on GitHub Actions"
```

然后去网页：

1. 打开 https://github.com/new  
2. Repository name：例如 `ai-telegram-news`  
3. 选 **Public**  
4. **不要**勾选 “Add a README”（本地已有文件）  
5. Create repository  

再回到终端（GitHub 网页会显示类似命令）：

```bash
git branch -M main
git remote add origin https://github.com/你的用户名/ai-telegram-news.git
git push -u origin main
```

---

## 第五步：在 GitHub 网页填 Secrets（密钥不会公开）

1. 打开你的仓库页面  
2. **Settings** → 左侧 **Secrets and variables** → **Actions**  
3. **New repository secret**，依次添加：

| Name | Value |
|------|--------|
| `TELEGRAM_BOT_TOKEN` | BotFather 给的 Token |
| `TELEGRAM_CHAT_ID` | `@你的频道用户名` 或 `-100...` |
| `GROQ_API_KEY` | （可选）Groq Key；没有就跳过不建这个 |

填完后外人看不到这些值；只有 Actions 跑任务时能用。

---

## 第六步：打开 Actions 并手动试跑

1. 仓库顶部点 **Actions**  
2. 若提示 Enable workflows，点允许  
3. 左侧选 **AI News → Telegram**  
4. 右侧 **Run workflow** → **Run workflow**  
5. 等 1～2 分钟，点进这次 run 看日志  
6. 去你的 Telegram 频道看有没有新消息  

之后默认 **每 2 小时**自动跑一次（UTC）。要改频率，编辑 `.github/workflows/crawl.yml` 里的 `cron`。

---

## 本地先试跑（可选）

```bash
cd ai-telegram-news
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入真实 Token / Chat ID
python bot.py
```

---

## 常见问题

**Actions 成功但频道没消息？**  
- 机器人是不是频道管理员、有没有「发消息」权限  
- `TELEGRAM_CHAT_ID` 是否写对（公开频道优先用 `@username`）

**日志里 Telegram 403？**  
- 机器人不在频道里，或没管理员权限

**第一次跑推了很多条？**  
- 正常；之后靠 `data/seen.json` 去重，不会重复发

**密钥会不会因为仓库公开而泄露？**  
- 只要只放在 Secrets、没写进代码/commit，就不会公开

---

## 以后怎么接广告 / 被搜到

1. 频道设公开 + 简介写清「AI 资讯 / 大模型 / LLM」等关键词  
2. 稳定日更（本项目会自动推）  
3. 简介或置顶留广告合作联系方式  
4. 有一定订阅后再挂价目 / 接广

把下面三样发给我（或你自己填 Secrets），就能继续帮你推仓库 / 排错：

1. 频道公开链接（`https://t.me/...`）——可发  
2. 是否已创建 Bot ——说「好了」即可，**不要把 Token 发在聊天里**  
3. 要不要中文摘要（要的话你自己去 Groq 申请 Key 填 Secrets）
