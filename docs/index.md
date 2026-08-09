---
layout: home

hero:
  name: AnnoPilot
  text: Local-first agentic annotation workbench
  tagline: 用 Vue 3 + FastAPI + SQLite + JSONL，构建可审计、可导出、适配 mobile 的轻量 annotation workflow。
  actions:
    - theme: brand
      text: 查看项目现状
      link: /guide/current-state
    - theme: alt
      text: 文档维护
      link: /guide/documentation

features:
  - title: Product Web UI
    details: 产品界面使用 Vue 3 + Vite + TypeScript，当前服务 TXT reader、token annotation、suggestion review、run provenance 和 export workflows。
  - title: Documentation Site
    details: 文档站使用 VitePress，独立构建并发布到 GitHub Pages，用于持续讲解项目架构和演进记录。
  - title: Local-first Runtime
    details: Runtime 使用 FastAPI + SQLite，持久化事实源使用 JSONL artifacts，默认保持 single-container Docker deployment。
---

## 两个维护表面

AnnoPilot repo 同时维护两个面向用户的入口：

- **Product Web UI**：根目录的 Vue 3 + Vite app，面向实际 annotation workflow。
- **Documentation Site**：`docs/` 下的 VitePress site，面向架构说明、维护手册和项目演进记录。

文档站的目标不是替代 README，而是作为项目长期知识库。README 保持简洁入口，详细设计、部署和演进记录放在这里。

## 快速入口

- [当前状态](/guide/current-state)：目前已经落地的 Product Web UI、FastAPI API、SQLite storage、JSONL event log 和 Docker deployment。
- [架构设计](/architecture)：当前确定的 Vue 3 + Vite frontend、FastAPI backend、SQLite runtime store 和 JSONL durable artifacts。
- [API Surface](/guide/api)：当前 FastAPI endpoints、request/response 边界和 frontend client 分工。
- [本地开发](/guide/development)：如何启动 Web UI、API、docs site、测试和 Docker build。
- [Runtime Storage](/guide/runtime-storage)：当前 SQLite schema、JSONL events 和 export JSONL 的职责分工。
- [Rosetta Docker 服务器部署](/guide/rosetta-docker-deployment)：Ali 服务器上的 Docker、自动更新、前后端拆分和数据持久化方案。
- [文档维护方式](/guide/documentation)：如何新增、修改、预览和发布文档。
- [GitHub Pages 部署](/guide/deployment)：文档站的 CI/CD 和 Pages 配置方式。
