# 后端：FastAPI + LangGraph 多智能体服务
FROM python:3.13-slim

# 容器内不装编译工具以外的东西；psycopg/asyncpg 等若需编译可在此加 build-essential
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 出图/质检产物与轨迹落盘目录（与 config.py 的 var/ 一致）
RUN mkdir -p /app/var/outputs /app/var/traces /app/var/metrics

EXPOSE 8123

# 生产建议通过环境变量注入 DASHSCOPE_API_KEY / API_KEY / DATABASE_URL，
# 不要把 .env 打进镜像（见根目录 .dockerignore）。
# 单副本；多副本部署请把 DB_AUTO_MIGRATE 设为 false，迁移放进独立 Job。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8123"]
