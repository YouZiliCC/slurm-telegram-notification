# Slurm Telegram Notification

Slurm 作业生命周期事件通过 Telegram Bot 推送通知。采用事件驱动架构，Slurm 钩子脚本在作业提交/完成时主动通知本地 daemon，无需轮询。

## 架构

```
┌─────────────────┐ curl POST  ┌───────────────────┐  HTTPS  ┌──────────┐
│ PrologSlurmctld │──────────▶│ slurm_monitor.py  │───────▶│ Telegram │
│ EpilogSlurmctld │            │   (HTTP daemon)   │         │ Bot API  │
└─────────────────┘            └───────────────────┘         └──────────┘
```

- **PrologSlurmctld** (`on_submit.sh`)：作业开始调度时触发，发送「已提交」通知
- **EpilogSlurmctld** (`on_finish.sh`)：作业结束时触发，发送「已完成」通知 + 日志文件
- **daemon** (`slurm_monitor.py`)：监听 HTTP 请求，接收作业信息后通过 `notify.py` 发送 Telegram 消息

## 依赖

- Python >= 3.10
- `requests` (pip)
- Telegram Bot Token
- 可选：`jq`（钩子脚本使用，用于解析 `scontrol --json` 输出）

## 安装

```bash
pip install -r requirements.txt
```

## 配置

### 1. Telegram（`notify.py`）

| 变量 | 说明 |
|------|------|
| `TELEGRAM_TOKEN` | Telegram Bot Token |
| `CHAT_ID` | 目标聊天/群组 ID |
| `MESSAGE_THREAD_ID` | 话题 ID（不需要则设为 `"0"`） |
| `PROXIES` | HTTPS 代理，不需要设为 `{}` |

### 2. Daemon（`slurm_monitor.py`）

| 变量 | 说明 |
|------|------|
| `LISTEN_HOST` | 监听地址，默认 `127.0.0.1` |
| `LISTEN_PORT` | 监听端口，默认 `8080` |
| `AUTH_TOKEN` | Bearer 认证令牌，为空则不验证 |
| `WATCH_USERS` | 监控的用户集合，为空则接受所有用户 |

### 3. Slurm 钩子脚本

将脚本复制到 slurmctld 节点，并在 `slurm.conf` 中配置：

```bash
cp scripts/on_submit.sh /etc/slurm/scripts/
cp scripts/on_finish.sh /etc/slurm/scripts/
chmod +x /etc/slurm/scripts/on_submit.sh /etc/slurm/scripts/on_finish.sh
```

```ini
# slurm.conf
PrologSlurmctld=/etc/slurm/scripts/on_submit.sh
EpilogSlurmctld=/etc/slurm/scripts/on_finish.sh
```

```bash
scontrol reconfigure
```

脚本默认请求 `http://127.0.0.1:8080`，可通过环境变量 `SLURM_TG_NOTIFY_URL` 覆盖。

## 运行

```bash
python slurm_monitor.py [--host 127.0.0.1] [--port 8080]
```

`Ctrl-C` 或 `SIGTERM` 优雅停止。

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/notify/submit` | POST | 作业提交通知 |
| `/notify/finish` | POST | 作业完成通知 |

请求体为 JSON，至少包含 `{"job_id": "12345"}`，也可直接传入 `scontrol show job --json` 的输出。

## 文件说明

| 文件 | 说明 |
|------|------|
| `slurm_monitor.py` | HTTP daemon，接收钩子请求并转发 Telegram 通知 |
| `notify.py` | Telegram 通知模块，负责消息格式化与发送 |
| `scripts/on_submit.sh` | PrologSlurmctld 钩子脚本 |
| `scripts/on_finish.sh` | EpilogSlurmctld 钩子脚本 |
