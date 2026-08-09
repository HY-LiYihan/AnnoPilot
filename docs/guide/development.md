# 本地开发

本文记录当前 repo 的实际开发入口。Product Web UI、Backend API 和 Documentation Site 是三条不同命令，不要混在一个 dev server 里理解。

## 安装依赖

Frontend 和 docs 使用 Node toolchain：

```bash
npm install
```

Backend 使用 Python dependencies：

```bash
python3 -m pip install -r backend/requirements-dev.txt
```

## 启动 Backend API

```bash
npm run api
```

该命令会启动：

```text
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

本地 runtime data 默认写入：

```text
.runtime/annopilot.sqlite
.runtime/projects/
```

## 启动 Product Web UI

```bash
npm run dev
```

Vite dev server 默认运行在：

```text
http://localhost:5173
```

当前 `vite.config.ts` 将 `/api` proxy 到：

```text
http://127.0.0.1:8000
```

所以本地开发时通常需要两个 terminal：一个跑 `npm run api`，另一个跑 `npm run dev`。

## 启动 Documentation Site

```bash
npm run docs:dev
```

Docs 使用 VitePress，内容位于：

```text
docs/
```

## Build 命令

Product Web UI build：

```bash
npm run build
```

Documentation Site build：

```bash
npm run docs:build
```

Documentation Site production preview：

```bash
npm run docs:preview
```

## Backend Tests

```bash
python3 -m pytest backend/tests
```

测试重点覆盖 API workflow 和 text processing，不依赖真实 `.runtime/` 数据。

当前测试还覆盖 JSONL event log、Prodigy-compatible export、manifest、audit / rebuild preview、LLM health secret redaction 和 LLM HTTP error redaction。

## LLM 配置

LLM review 使用 OpenAI-compatible `/chat/completions` provider。复制 `.env.example` 为 `.env` 后填写 key：

```bash
cp .env.example .env
```

当前 `.env.example`：

```text
LLM_BASE_URL=https://api.aixhan.com/v1
LLM_API_KEY=
LLM_MODEL=gpt5.5
```

`GET /api/health` 只返回 `llm_configured`、`llm_model` 和 `llm_base_host`，不会返回 secret。

## Docker Build

当前 Dockerfile 是 production app image，不是 docs image。

```bash
docker build -t annopilot .
```

运行时需要挂载 `/data`，用于 SQLite runtime database 和 JSONL artifacts：

```bash
docker run --rm -p 8080:8080 -v "$PWD/data:/data" annopilot
```

也可以直接使用根目录 compose，它默认限制容器内存为 1G，并挂载 `./data:/data`：

```bash
docker compose up --build
```

Compose 会把 `.env` 中的 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 传入容器；`.env` 和 `data/` 均不应提交。

访问：

```text
http://localhost:8080
```

## 工作区约定

- `.runtime/`：本地 API runtime data，已被 `.gitignore` 忽略。
- `data/`：Docker compose runtime data，已被 `.gitignore` 忽略。
- `dist/`：Product Web UI build output，已被 `.gitignore` 忽略。
- `docs/.vitepress/dist/`：Documentation Site build output，不应提交。
- `tmp/`：一次性数据集、OpenNER samples 和本地转换中间文件，已被 `.gitignore` 忽略。
- `node_modules/`、`.pytest_cache/`、Python cache files 都不应提交。
