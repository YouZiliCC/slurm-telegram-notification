import os

# Bind Address
bind = "127.0.0.1:8080"

# Worker Configuration
worker_class = "threading" 
workers = 2
threads = 1
timeout = 120
worker_tmp_dir = "/dev/shm" if os.path.exists("/dev/shm") else None

loglevel = "info"
capture_output = True

# Logging Configuration
# - > stdout
accesslog = "-"
# - > stderr
errorlog = "-"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Daemonization
daemon = True

# PID file path
pidfile = None

# Hot reload
reload = True

# Conflict with reload
preload_app = False

# Avoid OOM
max_requests = 1000
max_requests_jitter = 50

# Timeout reboot
graceful_timeout = 30



# 启动时回调
def on_starting(server):
    server.log.info("=" * 60)
    server.log.info("  Gunicorn Server Starting...")
    server.log.info("=" * 60)
    server.log.info(f"  Bind Address: {bind}")
    server.log.info(f"  Worker Class: {worker_class}")
    server.log.info(f"  Worker Count: {workers}")
    server.log.info(f"  Log Level: {loglevel}")
    server.log.info("=" * 60)


def on_reload(server):
    server.log.info("Hot Relaoding...")


def worker_int(worker):
    worker.log.info(f"Worker {worker.pid} SIGINT received, shutting down gracefully...")


def worker_abort(worker):
    worker.log.error(f"Worker {worker.pid} wrongly exited")