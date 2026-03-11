# Slurm Telegram Notification

监控 Slurm 集群作业状态，当作业提交或完成时通过 Telegram Bot 发送通知。

## 功能

- 轮询 Slurm REST API，自动检测新提交的作业和状态变更
- 作业完成时发送通知，包含运行时长、退出码等信息
- 自动上传 stdout/stderr 日志文件（大文件自动截断）
- 支持按用户过滤、代理设置、失败重试
- 启动时自动快照现有作业，不会重复通知

## 依赖

- Python >= 3.10
- Slurm REST API（slurmrestd）
- Telegram Bot Token

## 安装

```bash
pip install -r requirements.txt
```

## 配置

编辑 `notify.py` 中的 Telegram 配置：

| 变量 | 说明 |
|------|------|
| `TELEGRAM_TOKEN` | Telegram Bot Token |
| `CHAT_ID` | 目标聊天/群组 ID |
| `MESSAGE_THREAD_ID` | 话题 ID（不需要则设为 `"0"`） |
| `PROXIES` | HTTPS 代理，不需要设为 `{}` |

编辑 `slurm_monitor.py` 中的 Slurm 配置：

| 变量 | 说明 |
|------|------|
| `SLURM_API_URL` | Slurm REST API 地址（默认端口 6820） |
| `SLURM_API_VERSION` | API 版本，需与 slurmrestd 匹配 |
| `SLURM_JWT_TOKEN` | JWT 令牌，通过 `scontrol token username=<user> lifespan=86400` 生成 |
| `POLL_INTERVAL` | 轮询间隔（秒），默认 30 |
| `WATCH_USERS` | 监控的用户集合，为空则监控所有用户 |

## 运行

```bash
python slurm_monitor.py
```

使用 `Ctrl-C` 或发送 `SIGTERM` 优雅停止。

## 文件说明

- `slurm_monitor.py` — 主守护进程，轮询 Slurm API 并跟踪作业状态
- `notify.py` — Telegram 通知模块，负责消息格式化与发送
