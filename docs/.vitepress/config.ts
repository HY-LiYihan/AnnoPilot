import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'AnnoPilot',
  description: 'Local-first agentic annotation workbench architecture notes.',
  lang: 'zh-CN',
  base: '/AnnoPilot/',
  cleanUrls: true,
  lastUpdated: true,
  themeConfig: {
    nav: [
      { text: '首页', link: '/' },
      { text: '架构', link: '/architecture' },
      { text: '项目现状', link: '/guide/current-state' },
      { text: '维护指南', link: '/guide/documentation' },
      { text: 'GitHub', link: 'https://github.com/HY-LiYihan/AnnoPilot' },
    ],
    sidebar: [
      {
        text: '项目文档',
        items: [
          { text: '项目概览', link: '/' },
          { text: '当前状态', link: '/guide/current-state' },
          { text: '架构设计', link: '/architecture' },
          { text: 'API Surface', link: '/guide/api' },
          { text: 'Runtime Storage', link: '/guide/runtime-storage' },
        ],
      },
      {
        text: '维护',
        items: [
          { text: '本地开发', link: '/guide/development' },
          { text: 'Rosetta Docker 服务器部署', link: '/guide/rosetta-docker-deployment' },
          { text: '文档维护方式', link: '/guide/documentation' },
          { text: 'GitHub Pages 部署', link: '/guide/deployment' },
        ],
      },
    ],
    socialLinks: [
      { icon: 'github', link: 'https://github.com/HY-LiYihan/AnnoPilot' },
    ],
    search: {
      provider: 'local',
    },
    footer: {
      message: 'Released under the MIT License.',
      copyright: 'Copyright © 2026 AnnoPilot',
    },
  },
})
