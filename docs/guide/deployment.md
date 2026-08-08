# GitHub Pages 部署

Documentation Site 使用 GitHub Actions 构建，并发布到 GitHub Pages。

## 部署模型

```text
push to main
    |
GitHub Actions
    |
npm ci
    |
npm run docs:build
    |
upload docs/.vitepress/dist
    |
deploy to GitHub Pages
```

## GitHub 设置

在 GitHub repo 中进入：

```text
Settings -> Pages -> Build and deployment -> Source -> GitHub Actions
```

之后每次 push 到 `main`，workflow 会自动构建并发布文档站。

## Base Path

因为 AnnoPilot 是 user/org 下的 project page，VitePress 配置中使用：

```ts
base: '/AnnoPilot/'
```

发布后的默认 URL 形态是：

```text
https://hy-liyihan.github.io/AnnoPilot/
```

如果未来迁移到 custom domain，需要同步调整 `docs/.vitepress/config.ts` 中的 `base`。

## 验证方式

本地提交前建议运行：

```bash
npm run docs:build
```

如果需要预览 production build：

```bash
npm run docs:preview
```

Product Web UI 的 build 与 Documentation Site 的 build 是两条独立命令，避免首页开发和文档发布互相阻塞。
