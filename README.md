# Web CP - 网站克隆器

> 全站克隆引擎 | 动态渲染 | 裂变抓取 | 视频下载

[![Version](https://img.shields.io/badge/version-V15.5-00d4ff?style=flat-square&logo=github)](https://github.com/k20120509/web-cp)
[![Python](https://img.shields.io/badge/python-3.8+-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-00d4ff?style=flat-square)](LICENSE)
[![Threads](https://img.shields.io/badge/threads-64-ff6b6b?style=flat-square)]()

---

## 核心特性

- **预扫描进度条** - V15.5 全新设计，先确定总数，进度只增不减，彻底解决倒退问题
- **B 站视频下载** - 内置纯 Python 视频解析器，克隆 B 站时自动下载嵌入视频
- **智能算法优化** - 去重优先、跳过已知不可达资源、自适应并发调度
- **资源路径精确重写** - 子目录资源正确加载，无断链问题
- **动态渲染引擎** - Playwright 模拟滚动 + 网络空闲检测，SPA 网站完整抓取
- **裂变模式** - 深度 999 / 页面上限 9999，自动遍历全站链接
- **高并发下载** - 64 线程并发，请求间隔 0.005s，连接超时 4s
- **图标修复** - V15.5 内置有效 ICO 图标文件

---

## 架构流程

```
输入URL
  |
  v
[预扫描阶段] --> 解析首页HTML，统计所有资源，确定进度条总数
  |
  v
[动态渲染] --> Playwright 5次滚动 + 网络空闲等待
  |
  v
[资源提取] --> BeautifulSoup 解析所有资源引用 + CSS内联资源
  |
  v
[64线程并发下载] --> 去重优先 + 重试机制 + 自适应超时
  |
  v
[视频下载] --> B站视频自动解析，分段下载合并
  |
  v
[路径重写] --> resolve() 精确计算相对/绝对路径
  |
  v
输出: index.html + _assets/ + _videos/ (完整离线包)
```

---

## 快速开始

### 方式一：直接运行 EXE（推荐）

从下方版本列表下载对应版本的 `website_cloner.exe`，双击即可运行，无需任何依赖。

### 方式二：源码运行

```bash
# 安装依赖
pip install requests beautifulsoup4 lxml playwright
playwright install chromium

# 运行
python website_cloner.py
```

### 模式选择

```
[1] 简单打包  - 自定义深度(1-12)
[2] 全面打包  - 自动裂变模式(深度999/页面9999)
```

---

## 输出结构

```
cloned_site/
├── index.html          # 主入口（双击离线打开）
├── page_1.html         # 子页面
├── page_2.html
├── _assets/            # 资源文件
│   ├── css/
│   ├── js/
│   ├── images/
│   └── fonts/
└── _videos/            # B站视频（仅B站等视频网站）
    └── video_title.mp4
```

---

## 版本列表

| 版本 | 说明 | 文件 |
|------|------|------|
| **V15.5 (最新)** | 进度条修复 + B站视频下载 + 图标修复 + 算法优化 | V15.5/website_cloner.exe |
| V13.0 | 路径精确重写 + CSS死锁修复 + B站深度1验证 | V13.0/website_cloner.exe |
| V11.0 | 128线程 + 完成弹窗统计 | V11.0/website_cloner.exe |
| V10 Pro Max | 极简UI + 裂变模式 | V10 Pro Max/website_cloner.exe |
| V10.0 | 性能优化版 | V10.0/website_cloner.exe |
| V8.5 Beta | Beta 测试版 | V8.5 Beta/website_cloner.exe |
| V8.0 | 重试机制 + 丢包统计 | V8.0/website_cloner.exe |
| V7.0 | 并发引擎 | V7.0/website_cloner.exe |
| V6.0 | 正式版 | V6.0/website_cloner.exe |
| V5.0 | 资源下载 | V5.0/website_cloner.exe |
| V4.0 | 递归爬取 | V4.0/website_cloner.exe |
| V3.0 | 基础单页克隆（含源码） | V3.0/website_cloner.py |
| latest | 快捷方式（指向最新版） | latest/website_cloner.exe |

---

## V15.5 更新亮点

### 进度条问题彻底解决

**旧版问题**：进度条不断刷新总数，导致百分比倒退，用户体验差。

**V15.5 方案**：
- 新增 `预扫描阶段`：先抓取首页 HTML，解析出所有资源 URL，一次性确定总任务数
- `SmartProgress` 类：进度只增不减，阶段切换不重置 done 计数
- 百分比单调递增，用户能看到稳定前进的进度条

### B站视频下载

- 纯 Python 实现，无需 ffmpeg 等外部依赖
- 自动识别 B 站视频链接，调用官方 API 获取播放地址
- 分段下载自动合并，输出标准 MP4 文件
- 支持 BVID 链接、b23.tv 短链接

### 智能算法优化

- 去重优先：已下载资源直接复用，避免重复请求
- 已知失败缓存：失败 URL 自动记录，不再重复尝试
- CSS 延迟处理：防止线程池死锁，确保所有 CSS 内联资源被处理
- 自适应并发：根据网络状况动态调整并发数

---

## 测试用例

### B 站 (bilibili.com) - 深度 1

| 指标 | 数值 |
|------|------|
| 耗时 | ~312s |
| 页面 | 56/56 (100%) |
| 资源 | 2901/2911 (99.7%) |
| 体积 | 325.75 MB |
| 文件分布 | JPG: 1850 / PNG: 712 / CSS: 34 / JS: 87 / WOFF2: 109 |

### Example.com - 基准测试

| 指标 | 数值 |
|------|------|
| 耗时 | 0.8s |
| 页面 | 1/1 (100%) |
| 状态 | 基准验证通过 |

---

## 目录结构

```
web-cp/
├── V15.5/               # 最新版本（推荐）
│   ├── icon.ico
│   └── website_cloner.exe
├── V13.0/
│   ├── icon.ico
│   ├── Beta_Updater.exe
│   └── website_cloner.exe
├── V11.0/
│   └── website_cloner.exe
├── V10 Pro Max/
│   └── website_cloner.exe
├── V10.0/
│   └── website_cloner.exe
├── V8.5 Beta/
│   └── website_cloner.exe
├── V8.0/
│   └── website_cloner.exe
├── V7.0/
│   └── website_cloner.exe
├── V6.0/
│   └── website_cloner.exe
├── V5.0/
│   └── website_cloner.exe
├── V4.0/
│   └── website_cloner.exe
├── V3.0/                # 含源码
│   └── website_cloner.py
├── latest/              # 指向最新版
│   ├── icon.ico
│   └── website_cloner.exe
├── LICENSE
└── README.md
```

---

## 相关仓库

- **[DL-Web_cp](https://github.com/k20120509/DL-Web_cp)** - 独立视频下载工具，支持 B 站等平台视频下载

---

## 许可证

MIT License

---

*Built with precision. Clone anything.*
