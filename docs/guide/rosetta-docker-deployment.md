# Rosetta Docker 服务器部署方案

本文说明如何把 AnnoPilot/Rosetta 部署到 Ali 服务器上的 `rosetta` Docker stack，并满足三个目标：

- GitHub `main` 分支更新后，服务器自动拉取并切换到新版本。
- SQLite、JSONL event log 和项目文件保存在宿主机数据目录中，容器重建不会丢数据。
- 前端和后端可以先保持同仓库开发，但部署时拆成独立服务，便于单独更新 web 或 API。

本文不覆盖域名解析、HTTPS、OpenResty/Nginx 反向代理和公网访问策略；这里只关注 Docker、CI/CD 和 runtime data。

## 当前项目事实

当前代码库已经具备 production image 的基础：

- Frontend：Vue 3 + Vite + TypeScript，源码在 `src/`。
- Backend：FastAPI，源码在 `backend/`。
- 当前 `Dockerfile` 是 two-stage 单容器镜像：先构建 frontend，再把 `dist/` 复制给 FastAPI 静态托管。
- `deploy/` 已提供前后端拆分镜像、服务器 compose template、部署脚本和 webhook receiver。
- Runtime data 由环境变量控制：

```text
DATA_ROOT=/data/projects
DATABASE_PATH=/data/runtime/annopilot.sqlite
STATIC_DIR=/app/static
```

当前服务器上已有一个 `rosetta-app` 容器，公开端口是 `8501`，并把宿主机目录挂载到容器内。新的方案建议把部署整理成一个 compose project，名称使用 `rosetta`，服务名使用 `rosetta-web` 和 `rosetta-api`。

## 推荐目标架构

```text
GitHub main
    |
    | GitHub Actions
    | build and push images
    v
GHCR images
    |
    | signed webhook POST /deploy/rosetta
    v
Ali server /opt/rosetta
    |
    | docker compose up -d
    v
rosetta-web  ->  rosetta-api  ->  /opt/rosetta/data
Nginx static     FastAPI           SQLite + JSONL
```

核心原则：**代码进 image，数据进宿主机 bind mount**。每次更新只替换 image 和 container，不删除 `/opt/rosetta/data`。

## 服务器目录

建议在服务器上固定使用：

```text
/opt/rosetta/
  compose.yml
  .env
  webhook.env
  bin/
    deploy.sh
    webhook.py
  data/
    runtime/
      annopilot.sqlite
      annopilot.sqlite-wal
      annopilot.sqlite-shm
    projects/
      <project_id>/
        events.jsonl
```

约定：

- `/opt/rosetta/compose.yml` 只描述运行方式，不保存应用源码。
- `/opt/rosetta/data` 是唯一持久化数据根目录。
- 部署脚本禁止执行 `docker compose down -v`，禁止删除 `/opt/rosetta/data`。
- 镜像、停止容器和 build cache 可以清理；data 目录不能自动清理。

## Docker Compose 推荐配置

前后端拆分后的 compose template 已放在 `deploy/compose.rosetta.yml`：

```yaml
name: rosetta

services:
  rosetta-api:
    image: ghcr.io/hy-liyihan/annopilot-api:main
    container_name: rosetta-api
    restart: unless-stopped
    environment:
      APP_ENV: production
      DATA_ROOT: /data/projects
      DATABASE_PATH: /data/runtime/annopilot.sqlite
      STATIC_DIR: /app/static
      PYTHONUNBUFFERED: "1"
    volumes:
      - /opt/rosetta/data:/data
    expose:
      - "8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"]
      interval: 30s
      timeout: 5s
      retries: 5

  rosetta-web:
    image: ghcr.io/hy-liyihan/annopilot-web:main
    container_name: rosetta-web
    restart: unless-stopped
    depends_on:
      rosetta-api:
        condition: service_healthy
    ports:
      - "${ROSETTA_WEB_PORT:-8501}:80"
```

说明：

- `8501:80` 可以沿用当前服务器上 `rosetta-app` 的端口习惯；迁移时需要先停止旧容器，避免端口冲突。
- 迁移灰度阶段可以在 `/opt/rosetta/.env` 设置 `ROSETTA_WEB_PORT=18501`，避免影响当前 `8501` 上的旧服务。
- `rosetta-web` 用 Nginx serve Vite build，并把 `/api/*` 转发到 `rosetta-api:8000`。
- `rosetta-api` 不需要暴露公网端口，只在 Docker network 内提供 API。
- 如果暂时不拆分服务，也可以继续使用当前单容器 `Dockerfile`，但长期不如拆分模式方便独立更新。

## 前后端镜像拆分

当前仓库保留根目录单容器 `Dockerfile`，同时已经提供前后端拆分部署文件：

```text
deploy/
  Dockerfile.api
  Dockerfile.web
  nginx.conf
```

Backend image：
实际文件：`deploy/Dockerfile.api`。

```dockerfile
FROM python:3.12-slim
WORKDIR /app
ENV DATA_ROOT=/data/projects \
    DATABASE_PATH=/data/runtime/annopilot.sqlite \
    STATIC_DIR=/app/static \
    PYTHONUNBUFFERED=1
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend ./backend
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

Frontend image：
实际文件：`deploy/Dockerfile.web`。

```dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY index.html vite.config.ts tsconfig.json ./
COPY src ./src
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
```

Nginx config：
实际文件：`deploy/nginx.conf`。

```nginx
server {
  listen 80;
  server_name _;

  root /usr/share/nginx/html;
  index index.html;

  location /api/ {
    proxy_pass http://rosetta-api:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  location / {
    try_files $uri $uri/ /index.html;
  }
}
```

这样 frontend 仍然可以使用相对路径 `/api/...`，不需要把 backend URL 写进浏览器 bundle。

## CI/CD 自动更新闭环

当前推荐闭环是：

1. `CI` workflow 先跑 frontend build、docs build 和 backend tests。
2. `Deploy Rosetta` workflow 只在 `CI` 对 `main` push 成功后触发。
3. GitHub Actions 构建 `annopilot-api` 和 `annopilot-web` 镜像，并推送到 GHCR。
4. GitHub Actions 用共享 secret 对 payload 做 HMAC-SHA256 签名。
5. GitHub Actions POST 到服务器 webhook：`/deploy/rosetta`。
6. 服务器 webhook 验签、校验 repo/ref/timestamp，然后执行 `/opt/rosetta/bin/deploy.sh`。
7. 部署脚本加锁，执行 `docker compose pull && docker compose up -d --remove-orphans`，最后检查 `/api/health`。

实际 workflow 文件：`.github/workflows/deploy-rosetta.yml`。

核心触发方式：

```yaml
name: Deploy Rosetta

on:
  workflow_run:
    workflows: [CI]
    types: [completed]
    branches: [main]
  workflow_dispatch:
```

关键保护点：

- `workflow_run` 要求上游 workflow 名为 `CI`。
- job 条件要求 `conclusion == success` 且上游事件是 `push`，避免 PR 通过后直接发生产部署。
- webhook payload 使用 `X-Rosetta-Signature: sha256=<hmac>`。
- payload 包含 `issued_at`，服务器默认只接受 900 秒窗口内的请求。
- 服务器端使用 lock，避免两次部署同时执行。

GitHub repo secrets 需要配置：

```text
ROSETTA_DEPLOY_URL=http://8.217.119.172/deploy/rosetta
ROSETTA_WEBHOOK_SECRET=<same secret as /opt/rosetta/webhook.env>
```

当前 Ali 服务器的 `9010` 端口没有从公网放行，因此实际使用已有 OpenResty 的 80 端口做一个窄路由，把 `/deploy/rosetta` 转发到 `127.0.0.1:9010/deploy/rosetta`。接口暴露到公网时必须保留 HMAC secret；不建议无鉴权直接开放。

当前健康检查 URL：

```text
http://8.217.119.172/rosetta-webhook-healthz
```

## 服务器 Webhook 服务

服务器侧文件已经放在：

```text
deploy/server/deploy.sh
deploy/server/webhook.py
deploy/server/webhook.env.example
deploy/server/rosetta-webhook.service
```

安装到服务器时建议复制为：

```bash
sudo mkdir -p /opt/rosetta/bin /opt/rosetta/data/runtime /opt/rosetta/data/projects /opt/rosetta/backups
sudo cp deploy/compose.rosetta.yml /opt/rosetta/compose.yml
sudo cp deploy/server/deploy.sh /opt/rosetta/bin/deploy.sh
sudo cp deploy/server/webhook.py /opt/rosetta/bin/webhook.py
sudo cp deploy/server/webhook.env.example /opt/rosetta/webhook.env
sudo chmod +x /opt/rosetta/bin/deploy.sh /opt/rosetta/bin/webhook.py
```

编辑 `/opt/rosetta/webhook.env`：

```text
ROSETTA_WEBHOOK_SECRET=<long random secret>
ROSETTA_WEBHOOK_HOST=127.0.0.1
ROSETTA_WEBHOOK_PORT=9010
ROSETTA_ALLOWED_REPOSITORY=HY-LiYihan/AnnoPilot
ROSETTA_ALLOWED_REF=main
ROSETTA_DEPLOY_COMMAND=/opt/rosetta/bin/deploy.sh
ROSETTA_DEPLOY_MODE=image
ROSETTA_COMPOSE_FILE=/opt/rosetta/compose.yml
ROSETTA_HEALTH_URL=http://127.0.0.1:18501/api/health
```

灰度阶段建议同时创建 `/opt/rosetta/.env`：

```text
ROSETTA_IMAGE_TAG=main
ROSETTA_WEB_PORT=18501
```

等新服务确认可用后，再把 `ROSETTA_WEB_PORT` 改回 `8501` 并停止旧 `rosetta-app`。

安装 systemd service：

```bash
sudo cp deploy/server/rosetta-webhook.service /etc/systemd/system/rosetta-webhook.service
sudo systemctl daemon-reload
sudo systemctl enable --now rosetta-webhook.service
sudo systemctl status rosetta-webhook.service
```

本机健康检查：

```bash
curl -f http://127.0.0.1:9010/healthz
```

通过 OpenResty 外部检查：

```bash
curl -f http://8.217.119.172/rosetta-webhook-healthz
```

手动模拟一次签名部署请求：

```bash
secret='<same as ROSETTA_WEBHOOK_SECRET>'
payload="{\"repository\":\"HY-LiYihan/AnnoPilot\",\"ref\":\"main\",\"sha\":\"manual\",\"mode\":\"image\",\"issued_at\":$(date -u +%s)}"
signature="$(printf '%s' "$payload" | openssl dgst -sha256 -hmac "$secret" -hex | sed 's/^.* //')"
curl -fsS -X POST http://127.0.0.1:9010/deploy/rosetta \
  -H "Content-Type: application/json" \
  -H "X-Rosetta-Signature: sha256=$signature" \
  --data "$payload"
```

## GitHub Actions 镜像发布

`Deploy Rosetta` workflow 会构建两个镜像：

```text
ghcr.io/hy-liyihan/annopilot-api:main
ghcr.io/hy-liyihan/annopilot-api:<commit-sha>
ghcr.io/hy-liyihan/annopilot-web:main
ghcr.io/hy-liyihan/annopilot-web:<commit-sha>
```

如果 GHCR package 设为 private，服务器还需要提前执行一次：

```bash
docker login ghcr.io
```

并使用具备 `read:packages` 权限的 token。若 package 设为 public，则服务器通常不需要额外登录即可 pull。

保留 `:main` tag 是为了让服务器 compose 文件稳定；同时发布 `:<commit-sha>` tag 是为了审计和必要时回滚。

## 源码重建备选模式

默认使用 image 模式：服务器只拉 GHCR 镜像，不在服务器上 build。这个模式更稳定，也更省服务器 CPU 和磁盘。

如果确实希望服务器根据最新代码重新生成 Docker，可以把 webhook payload 中的 `mode` 改成 `source`，并在 `/opt/rosetta/webhook.env` 中配置：

```text
ROSETTA_DEPLOY_MODE=source
ROSETTA_REPO_DIR=/opt/rosetta/src/AnnoPilot
ROSETTA_SOURCE_BRANCH=main
```

source 模式会执行：

```bash
git fetch origin main
git checkout main
git pull --ff-only origin main
docker compose -f /opt/rosetta/compose.yml build
docker compose -f /opt/rosetta/compose.yml up -d --remove-orphans
```

只有当服务器已经维护干净的 git checkout，并且 compose 文件使用 `build:` 而不是 `image:` 时，才建议启用 source 模式。生产默认仍推荐 image 模式。

## 独立更新策略

拆成两个镜像后，可以渐进优化 workflow：

- `src/**`、`package.json`、`vite.config.ts` 变化时，只构建 `annopilot-web`。
- `backend/**` 变化时，只构建 `annopilot-api`。
- 公共配置、Dockerfile 或锁文件变化时，两个镜像都构建。
- `docs/**` 变化只触发文档站 workflow，不需要重启生产 app。

即使先采用“每次 main 更新都构建两个镜像”的简单模式，持久化数据仍然安全；优化独立构建主要是为了减少部署时间。

## 数据持久化和备份

必须持久化：

```text
/data/runtime/annopilot.sqlite
/data/runtime/annopilot.sqlite-wal
/data/runtime/annopilot.sqlite-shm
/data/projects/**
```

宿主机对应目录：

```text
/opt/rosetta/data/runtime/
/opt/rosetta/data/projects/
```

推荐备份策略：

```bash
cd /opt/rosetta
tar -czf backups/rosetta-data-$(date +%Y%m%d-%H%M%S).tgz data
```

如果要做热备 SQLite，更稳的方式是在容器内使用 SQLite backup API 或短暂停止 `rosetta-api` 后打包：

```bash
docker compose stop rosetta-api
tar -czf backups/rosetta-data-$(date +%Y%m%d-%H%M%S).tgz data
docker compose up -d rosetta-api
```

部署流程中不要运行：

```bash
docker compose down -v
rm -rf /opt/rosetta/data
docker volume prune -f
```

## 迁移步骤

建议迁移顺序：

1. 使用仓库内的 `deploy/Dockerfile.api`、`deploy/Dockerfile.web`、`deploy/nginx.conf` 和 `deploy/compose.rosetta.yml`。
2. 配置 GitHub secrets：`ROSETTA_DEPLOY_URL` 和 `ROSETTA_WEBHOOK_SECRET`。
3. 在服务器创建 `/opt/rosetta/data` 和 `/opt/rosetta/backups`。
4. 备份当前 `/opt/rosetta/runtime` 和旧 `rosetta-app` 使用的数据目录。
5. 写入 `/opt/rosetta/compose.yml`、`/opt/rosetta/bin/deploy.sh`、`/opt/rosetta/bin/webhook.py` 和 `/opt/rosetta/webhook.env`。
6. 先用非冲突端口试运行，例如 `18501:80`。
7. 启动 `rosetta-webhook.service`，并验证 `/healthz` 与签名 POST。
8. 验证 `/api/health`、前端页面、TXT import、annotation、export JSONL。
9. 停止旧 `rosetta-app`，把新 `rosetta-web` 端口切到 `8501:80`。
10. push 到 `main`，确认 `CI` 成功后 `Deploy Rosetta` 自动触发服务器 webhook。

当前旧容器使用的 `ROSETTA_RUNTIME_DIR=/opt/rosetta/runtime` 不等同于新 AnnoPilot 的 `/data/runtime` 和 `/data/projects` 结构。迁移旧数据前需要先确认旧 runtime 目录中的文件格式，不建议直接覆盖新 `/opt/rosetta/data`。

## 验证清单

每次迁移或更新后检查：

```bash
docker compose ps
docker compose logs --tail=100 rosetta-api
docker compose logs --tail=100 rosetta-web
curl -f http://127.0.0.1:8501/api/health
ls -lah /opt/rosetta/data/runtime
find /opt/rosetta/data/projects -maxdepth 3 -type f | head
```

功能层面检查：

- 页面能打开。
- 上传 TXT 后会生成 document。
- 标注、删除标注、完成 sentence 后刷新页面仍然存在。
- `/opt/rosetta/data/projects/<project_id>/events.jsonl` 持续追加。
- 重新 `docker compose up -d --force-recreate` 后数据不丢。

## Watchtower 备选方案

如果不希望 GitHub Actions SSH 到服务器，也可以在服务器运行 Watchtower：

```yaml
services:
  watchtower:
    image: containrrr/watchtower
    container_name: rosetta-watchtower
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    command: --interval 300 rosetta-web rosetta-api
```

Watchtower 会定时检查 image tag 是否更新，然后自动重建容器。它的优点是服务器主动拉取，缺点是发布不是 GitHub workflow 完成后立即发生，并且需要谨慎管理 image tag。对于这个项目，优先推荐 GitHub Actions signed webhook deploy；Watchtower 更适合作为简化版自动更新。

## 最小可落地版本

如果想先尽快跑起来，不立刻拆前后端，可以保留当前单容器 `Dockerfile`，只新增服务器 compose：

```yaml
name: rosetta

services:
  rosetta-app:
    image: ghcr.io/hy-liyihan/annopilot:main
    container_name: rosetta-app
    restart: unless-stopped
    ports:
      - "8501:8080"
    environment:
      DATA_ROOT: /data/projects
      DATABASE_PATH: /data/runtime/annopilot.sqlite
      STATIC_DIR: /app/static
      PYTHONUNBUFFERED: "1"
    volumes:
      - /opt/rosetta/data:/data
```

这个版本已经能保证自动更新和数据持久化。等产品 UI/API 迭代更频繁时，再拆成 `rosetta-web` 和 `rosetta-api`。
