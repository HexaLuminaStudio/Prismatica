# =====================================================================
# Prismatica 后端 .env.example
# 复制为 .env 后填入实际值;生产严禁使用默认占位密钥。
# =====================================================================

# 应用
APP_NAME=prismatica-backend
ENV=dev
DEBUG=false
HOST=0.0.0.0
PORT=8000

# MySQL(对齐 docker-compose 服务名)
DB_HOST=mysql3.sqlpub.com
DB_PORT=3308
DB_NAME=six_corpus
DB_USER=hungry630
DB_PASSWORD=KaTYD6ohJxUnfRq7
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20

# Redis(限流)
REDIS_URL=redis://127.0.0.1:6379/0
RATELIMIT_STORAGE_URI=redis://127.0.0.1:6379/1

# HMAC 凭证签名(与客户端 LICENSE_SECRET 保持一致)
LICENSE_SECRET=ec41f548431eb9ab3502b00dafd5bb3c192c81c38091b38cddfdbfc1a0b9ca65

# JWT
JWT_SECRET=e39516a3268034039c7bbec81fb5c91ca8ed4c6fd706b9bea1c5cbb77c7ee183
JWT_ALG=HS256
JWT_ACCESS_TTL=3600
JWT_REFRESH_TTL=2592000

# Admin
ADMIN_TOKEN=081697a03c3bff88a36a730ef66d1dec910f0c8b3bf9c586

# 限流
RATE_LIMIT_PER_MIN=60

# 日志
LOG_LEVEL=INFO
LOG_JSON=false

# 自动建表(开发期 true,生产 false)
AUTO_INIT_SCHEMA=true

# CORS(跨域)—— 留空允许所有(仅 dev);生产必须显式列出
# 多项用英文逗号分隔,例如:
#   CORS_ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
CORS_ALLOWED_ORIGINS=http://103.236.55.211
CORS_ALLOW_CREDENTIALS=true
CORS_MAX_AGE_SEC=600