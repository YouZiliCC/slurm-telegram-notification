# Slurm Telegram Notification

Slurm 作业生命周期事件通过 Telegram Bot 推送通知。Slurm 钩子脚本在作业启动/完成时通知本地 Flask daemon，无需轮询。

## 架构

```
┌─────────────────┐ curl POST  ┌───────────────────┐  HTTPS  ┌──────────┐
│ PrologSlurmctld │──────────▶ │   Flask daemon    │───────▶ │ Telegram │
│ EpilogSlurmctld │            │   (app.py + db)   │         │ Bot API  │
└─────────────────┘            └───────────────────┘         └──────────┘
```

- **on_start.sh** (PrologSlurmctld)：作业开始 → 发送「已启动」通知
- **on_finish.sh** (EpilogSlurmctld)：作业结束 → 发送「已完成」通知 + 日志文件
- **daemon**：接收 HTTP 请求，通过 Telegram Bot API 发送消息
- **消息管理**：所有通知记录永久保存在 SQLite 数据库中；Telegram 群组内仅保留最新 `MAX_MESSAGES` 条消息，超出时自动删除旧消息

## 依赖

- Python >= 3.13
- Telegram Bot Token
- 可选：`jq`（钩子脚本用于解析 `scontrol --json` 输出）

## 安装

```bash
# uv（推荐）
uv sync

# 或 pip
pip install .
```

## 配置

```bash
cp .env.example .env
# 编辑 .env 填写 Telegram Bot Token 和 Chat ID
```

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TELEGRAM_TOKEN` | Telegram Bot Token | *(必填)* |
| `CHAT_ID` | 目标聊天/群组 ID | *(必填)* |
| `MESSAGE_THREAD_ID` | 话题 ID（无话题设 `0`） | `0` |
| `PROXIES` | HTTPS 代理地址，留空禁用 | *(空)* |
| `AUTH_TOKEN` | Bearer 认证令牌，为空不验证 | *(空)* |
| `WATCH_USERS` | 监控用户列表，逗号分隔，为空接受所有 | *(空)* |
| `MAX_MESSAGES` | Telegram 群组中保留的最大消息数 | `5` |
| `MAX_LOG_BYTES` | 日志文件截断大小 | `1048576` |
| `RETRY_COUNT` | 发送失败重试次数 | `3` |
| `RETRY_DELAY` | 重试间隔（秒） | `5` |

### Slurm 钩子

```bash
cp scripts/on_start.sh scripts/on_finish.sh /etc/slurm/scripts/
chmod +x /etc/slurm/scripts/on_start.sh /etc/slurm/scripts/on_finish.sh
```

```ini
# slurm.conf
PrologSlurmctld=/etc/slurm/scripts/on_start.sh
EpilogSlurmctld=/etc/slurm/scripts/on_finish.sh
```

```bash
scontrol reconfigure
```

钩子默认请求 `http://127.0.0.1:8080`，通过 `SLURM_TG_NOTIFY_URL` 环境变量覆盖。

## 运行

```bash
# 生产
gunicorn -c gunicorn_conf.py app:app

# 开发
flask --app app run --host 127.0.0.1 --port 8080
```

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/notify/start` | POST | 作业启动通知 |
| `/notify/finish` | POST | 作业完成通知 |
| `/messages` | GET | 查询最近通知记录 |
| `/health` | GET | 健康检查 |

请求体为 JSON，至少包含 `{"job_id": "12345"}`，也可直接传入 `scontrol show job --json` 的输出。

## 文件

| 文件 | 说明 |
|------|------|
| `app.py` | Flask 应用，路由 + 请求处理 |
| `notify.py` | Telegram 消息格式化与发送 |
| `db.py` | SQLite 消息记录与 Telegram 消息管理 |
| `gunicorn_conf.py` | Gunicorn 配置 |
| `.env.example` | 环境变量模板 |
| `scripts/on_start.sh` | PrologSlurmctld 钩子 |
| `scripts/on_finish.sh` | EpilogSlurmctld 钩子 |
