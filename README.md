# Slurm Telegram Notification

Slurm 作业生命周期事件通过 Telegram Bot 推送通知。采用事件驱动架构，Slurm 钩子脚本在作业启动/完成时主动通知本地 Flask daemon，无需轮询。

## 架构

```
┌─────────────────┐ curl POST  ┌───────────────────┐  HTTPS  ┌──────────┐
│ PrologSlurmctld │──────────▶ │   Flask daemon    │───────▶ │ Telegram │
│ EpilogSlurmctld │            │     (app.py)      │         │ Bot API  │
└─────────────────┘            └───────────────────┘         └──────────┘
```

- **PrologSlurmctld** (`on_start.sh`)：作业开始运行时触发，发送「已启动」通知
- **EpilogSlurmctld** (`on_finish.sh`)：作业结束时触发，发送「已完成」通知 + 日志文件
- **daemon** (`app.py`)：Flask 应用，监听 HTTP 请求，接收作业信息后通过 `notify.py` 发送 Telegram 消息

## 依赖

- Python >= 3.13
- `flask` — Web 框架
- `python-dotenv` — 从 `.env` 文件加载配置
- `requests` — HTTP 客户端
- `gunicorn` — 生产环境 WSGI 服务器
- Telegram Bot Token
- 可选：`jq`（钩子脚本使用，用于解析 `scontrol --json` 输出）

## 安装

```bash
# 使用 uv（推荐）
uv sync

# 或使用 pip
pip install .
```

## 配置

所有配置通过 `.env` 文件（或系统环境变量）管理。复制示例文件后修改：

```bash
cp .env.example .env
```

### 环境变量说明

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TELEGRAM_TOKEN` | Telegram Bot Token | *(必填)* |
| `CHAT_ID` | 目标聊天/群组 ID | *(必填)* |
| `MESSAGE_THREAD_ID` | 话题 ID（不需要则设为 `0`） | `0` |
| `PROXIES` | HTTPS 代理地址，留空则不使用代理 | *(空)* |
| `AUTH_TOKEN` | Bearer 认证令牌，为空则不验证 | *(空)* |
| `WATCH_USERS` | 监控的用户列表，逗号分隔，为空则接受所有用户 | *(空)* |
| `MAX_LOG_BYTES` | 日志文件截断大小 | `1048576` (1MB) |
| `RETRY_COUNT` | Telegram 发送失败重试次数 | `3` |
| `RETRY_DELAY` | 重试间隔（秒） | `5` |

### Slurm 钩子脚本

将脚本复制到 slurmctld 节点，并在 `slurm.conf` 中配置：

```bash
cp scripts/on_start.sh /etc/slurm/scripts/
cp scripts/on_finish.sh /etc/slurm/scripts/
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

脚本默认请求 `http://127.0.0.1:8080`，可通过环境变量 `SLURM_TG_NOTIFY_URL` 覆盖。

## 运行

### 生产环境（推荐）

```bash
gunicorn -c gunicorn_conf.py app:app
```

### 开发环境

```bash
flask --app app run --host 127.0.0.1 --port 8080
```

`Ctrl-C` 或 `SIGTERM` 优雅停止。

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/notify/start` | POST | 作业启动通知 |
| `/notify/finish` | POST | 作业完成通知 |
| `/health` | GET | 健康检查 |

请求体为 JSON，至少包含 `{"job_id": "12345"}`，也可直接传入 `scontrol show job --json` 的输出。

## 文件说明

| 文件 | 说明 |
|------|------|
| `app.py` | Flask 应用，HTTP 路由 + 请求处理 |
| `notify.py` | Telegram 通知模块，负责消息格式化与发送 |
| `gunicorn_conf.py` | Gunicorn 生产环境配置 |
| `.env.example` | 环境变量配置模板 |
| `scripts/on_start.sh` | PrologSlurmctld 钩子脚本 |
| `scripts/on_finish.sh` | EpilogSlurmctld 钩子脚本 |
