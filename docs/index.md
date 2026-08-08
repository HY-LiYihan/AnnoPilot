---
layout: home

hero:
  name: AnnoPilot
  text: Local-first agentic annotation workbench
  tagline: 用可审计的 guideline、calibration、batch annotation 和 review workflow，把概念定义转化为可复用数据集。
  actions:
    - theme: brand
      text: 查看架构
      link: /architecture
    - theme: alt
      text: 文档维护
      link: /guide/documentation

features:
  - title: Product Web UI
    details: 产品界面使用 Vue 3 + Vite + TypeScript，服务 annotation setup、review queue、batch runs 和 export workflows。
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

- [架构设计](/architecture)：当前确定的 Vue 3 + Vite frontend、FastAPI backend、SQLite runtime store 和 JSONL durable artifacts。
- [文档维护方式](/guide/documentation)：如何新增、修改、预览和发布文档。
- [GitHub Pages 部署](/guide/deployment)：文档站的 CI/CD 和 Pages 配置方式。
