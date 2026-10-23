# 🎬 MediaButler · 影音管家

**一个容器搞定整条影音自动化链路。** 融合 CineChill / CloudMediaSync / Media Automator 三个项目的核心能力，专为 NAS（飞牛 fnOS 等低内存设备）设计：单容器、SQLite 存储、Python 异步架构，常驻内存远低于三容器方案。

```
订阅（豆瓣/TMDB/Emby 缺集）→ 资源搜寻（TG 监控 + 盘搜 + 影巢）→ 智能匹配（仅缺集/洗版）
→ 推送入库（115 转存/离线）→ 网盘整理（重命名/分类）→ STRM 生成 → Emby 302 播放
```

## 功能总览

| 模块 | 能力 | 复刻来源 |
|---|---|---|
| 📺 订阅管理 | TMDB 搜索手动订阅、豆瓣「想看」/榜单/自定义片单自动订阅、Emby 库缺集自动订阅 | Media Automator |
| 📡 TG 监控 | Telethon 7×24 监听频道、消息净化（去广告）、正则+Gemini 混合解析、历史消息本地缓存秒搜、TG 主动搜索、「搜 xxx」指令 | Media Automator |
| 🔍 盘搜入库 | pansou 聚合盘搜（默认千山实例，可配第二源）+ 影巢（HDHive）资源查询/解锁，自动推送到 115 | Media Automator |
| 🧠 智能决策 | 仅匹配缺集、关键词/正则过滤、洗版期只收高画质、完结自动归档 | Media Automator |
| 🗂️ 网盘整理 | 监听 115 待整理目录 → TMDB 匹配 → 规范重命名 → 电影/剧集分类移动 | CineChill |
| ☁️ STRM 同步 | 115 全量同步生成 STRM + nfo/字幕/图片落地，增量同步靠 115 生活事件驱动 | CMS |
| ▶️ Emby 反代 | `/stream` 网关 302 到 115 CDN 直链；`/emby/*` 反代带图片磁盘缓存 | CMS / CineChill |
| ✅ 每日签到 | 115 签到、影巢签到（支持赌狗模式），结果推送通知 | — |
| 🤖 AI 解析 | 正则 + AI 混合解析（默认走 wb-hub OpenAI 兼容反代，Gemini 兜底） | Media Automator |
| 🔔 通知 | Telegram Bot / 企业微信机器人 / 自定义 Webhook | CineChill |
| 🪝 Emby Webhook | 入库事件实时刷新订阅状态 | Media Automator |
| ⏰ 任务中心 | Cron 可视化调度，每个任务可启停/手动触发/查看结果 | 三家合一 |

## 快速部署（飞牛 / 任何 Docker 主机）

```bash
# 飞牛约定：全部容器数据放 /vol1/1000/Docker/
sudo mkdir -p /vol1/1000/Docker/mediabutler && cd /vol1/1000/Docker/mediabutler
sudo git clone https://github.com/Russ534/mediabutler.git .
# 如需改 IP，编辑 docker-compose.yml 里的 MB_STRM_HOST（默认 192.168.31.201:8866）
sudo docker compose up -d --build
sudo docker compose logs -f mediabutler   # 首次启动日志里打印初始密码
```

飞牛用户也可以直接在「Docker → Compose」里粘贴 `docker-compose.yml` 内容创建。

**访问** `http://飞牛IP:8866`，账号 `admin`，密码看启动日志，登录后立即改密。

## 配置顺序（最短上手路径）

1. **设置 → Emby**：填地址 + API Key，点「测试 Emby」
2. **设置 → 115 网盘**：浏览器登录 115 后 F12 复制 Cookie 填入，点「测试 115」
3. **设置 → TMDB**：填 API Key（订阅/整理都依赖）
4. **设置 → AI 解析**：默认已指向 wb-hub（`http://192.168.31.201:8788/v1`），API Key 从 hub 面板「设置」页复制填入，点「测试 AI」验证
5. **设置 → Telegram**：填 api_id/api_hash（[my.telegram.org](https://my.telegram.org) 申请），回到「TG 监控」页登录（手机号 + 验证码）
6. **STRM 同步**：添加同步路径（115 目录 CID 可在页面下方目录浏览器查到）→ 点「全量同步」→ Emby 里把 `media` 目录加为媒体库扫描
7. **TG 监控**：添加监控规则，选频道、推送目标选「115 网盘」、勾选「仅匹配缺集」
8. **订阅管理**：搜索添加订阅，或等豆瓣/Emby 定时任务自动补充

## Emby 侧配置

- **播放**：STRM 内容为 `http://飞牛IP:8866/stream/...`，Emby 播放时 302 到 115 CDN，不占用本机带宽转发。
- **Webhook**：Emby 通知里加 Webhook 指向 `http://飞牛IP:8866/api/webhook/emby`，入库即刷新订阅状态。
- **图片加速**（可选）：把 Emby 前端图片请求反代到 `http://飞牛IP:8866/emby/...`，自带磁盘缓存。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `MB_PORT` | 8866 | Web/网关端口 |
| `MB_CONFIG_DIR` | /config | 配置与数据库目录 |
| `MB_MEDIA_ROOT` | /media | 容器内媒体根目录 |
| `MB_STRM_HOST` | — | **必填**，Emby 可访问的本程序地址，如 `http://192.168.31.201:8866` |
| `TZ` | Asia/Shanghai | 时区 |

## 架构

```
app/
├── main.py            FastAPI 入口 + 生命周期
├── scheduler.py       APScheduler 任务调度（DB 驱动，可热重载）
├── core/              tmdb / douban / emby / pan115(p115client) / hdhive(re0.me) / pansearch(pansou) / parser / notify / strm
├── services/          subscribe(订阅状态机) / tg_monitor(Telethon) / matcher(决策) / sync115(全量+增量) / organizer(整理) / tasks
├── gateway/proxy.py   STRM 302 网关 + Emby 图片缓存反代
├── api/               REST API + 会话认证
└── web/static/        原生 JS 单页管理后台（无前端构建，零依赖）
```

## 致谢与声明

功能设计参考了 [CineChill](https://bbong9.github.io/cinechill-wiki/)、[CloudMediaSync](https://wiki.cmscc.cc/)、[Media Automator](https://media-automator.gitbook.io/media_automator-docs)。本项目为独立实现，仅供个人学习与家庭影音库管理使用，请部署在局域网内，勿用于商业用途。
