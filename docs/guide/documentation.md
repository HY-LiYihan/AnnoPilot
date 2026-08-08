# 文档维护方式

AnnoPilot 使用 **VitePress** 维护独立 documentation site。产品 Web UI 和文档站在同一个 repo 内共存，但职责分开。

## 目录分工

```text
.
  src/                    # Product Web UI: Vue 3 + Vite + TypeScript
  docs/                   # Documentation Site: VitePress
    index.md
    architecture.md
    guide/
      documentation.md
      deployment.md
    .vitepress/
      config.ts
```

## 常用命令

```bash
npm run docs:dev
npm run docs:build
npm run docs:preview
```

- `docs:dev`：本地启动 VitePress dev server，用于实时编辑文档。
- `docs:build`：构建 GitHub Pages 使用的 static files。
- `docs:preview`：预览 production build 结果。

Product Web UI 仍然使用原有命令：

```bash
npm run dev
npm run build
npm run preview
```

## 写作约定

- 文档主要使用中文，关键技术术语保留英文，例如 `FastAPI`、`SQLite`、`JSONL`、`SSE`、`Docker`、`runtime state`。
- README 只保留项目简介和入口链接。
- 架构决策、模块边界、部署方式和演进记录放在 `docs/`。
- 新增大主题时优先放在 `docs/guide/` 或后续新建的专题目录中。
- 每次改架构文档时，确认 Product Web UI 和 Documentation Site 的边界没有混在一起。

## 推荐新增页面

后续可以逐步增加：

- `docs/guide/getting-started.md`：本地启动和开发流程。
- `docs/guide/runtime-storage.md`：SQLite 与 JSONL 的职责分工。
- `docs/guide/api.md`：FastAPI endpoint 和 OpenAPI 维护方式。
- `docs/guide/release-notes.md`：项目演进记录。
