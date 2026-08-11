# 文档维护方式

AnnoPilot 使用 **VitePress** 维护独立 documentation site。产品 Web UI 和文档站在同一个 repo 内共存，但职责分开。

## 目录分工

```text
.
  src/                    # Product Web UI: Vue 3 + Vite + TypeScript
  docs/                   # Documentation Site: VitePress
    index.md
    architecture.md
    decisions/
      index.md
      0001-local-first-sqlite-jsonl.md
      0002-storage-facade-refactor.md
      0003-defer-heavy-runtime-dependencies.md
    guide/
      api.md
      current-state.md
      documentation.md
      deployment.md
      development.md
      runtime-storage.md
      annopilot-docker-deployment.md
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
- 长期架构取舍使用 `docs/decisions/` 的 ADR 记录，保留 Context / Decision / Consequences。
- 新增大主题时优先放在 `docs/guide/` 或后续新建的专题目录中。
- 每次改架构文档时，确认 Product Web UI 和 Documentation Site 的边界没有混在一起。
- 文档描述当前实现时使用“当前已实现”；描述未来能力时使用“后续”“建议”“目标”，避免把 roadmap 写成 shipped feature。

## 推荐新增页面

后续可以逐步增加：

- `docs/guide/getting-started.md`：面向新用户的最短启动路径。
- `docs/guide/release-notes.md`：项目演进记录。
- `docs/guide/operations.md`：Docker 运行、备份、恢复、LLM provider 和常见故障处理。
