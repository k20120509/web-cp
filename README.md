# Web CP - 网站克隆器

> 全站克隆引擎 | 动态渲染 | 裂变抓取 | 视频下载 | 进度条零倒退

[![Version](https://img.shields.io/badge/version-V15.5-00d4ff?style=flat-square&logo=github)](https://github.com/k20120509/web-cp)
[![Python](https://img.shields.io/badge/python-3.8+-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-00d4ff?style=flat-square)](LICENSE)
[![Threads](https://img.shields.io/badge/threads-64-ff6b6b?style=flat-square)]()
[![Release](https://img.shields.io/badge/release-V15.5-green?style=flat-square&logo=github)

---

## ⚠️ 关于杀毒软件误报

本程序已使用 **Windows 自签名证书**（CN=Web CP Tools, O=k20120509）签名。由于是开发者自签证书，**首次下载** 在部分电脑可能触发：
- Windows SmartScreen 拦截
- 360、电脑管家、火绒等误报为"未知发布者"或病毒

### 解决方案（任选其一）

1. **安装签名证书（推荐）**：解压 → 双击压缩包内的 `webcp_signing.cer` → "安装证书" → 选"受信任的根证书颁发机构" → 完成
2. **添加白名单**：在杀毒软件中将 `website_cloner.exe` 加入信任列表
3. **临时关闭杀毒软件**：运行前关闭 360/电脑管家/火绒等

> 解决一次后，后续双击即可正常运行。

---

## 快速下载

👉 **[点击下载最新版 V15.5](https://github.com/k20120509/web-cp/releases/tag/V15.5)** - 双击即运行，零依赖

> 🧪 **测试版本**：Debug / Test / Release 三个测试版已发布到 [Releases 页面](https://github.com/k20120509/web-cp/releases)

---

## 核心特性

### 1. 预扫描进度条（V15.5 全新设计）

**旧版痛点：**
- 进度条不断刷新总数，百分比倒退，用户体验差

**V15.5 方案：**
- 新增 **预扫描阶段**：先抓取首页 HTML，解析所有资源 URL，一次性确定总任务数
- **SmartProgress 类**：进度只增不减，阶段切换不重置 done 计数
- **百分比单调递增**：用户看到稳定前进的进度条，不再焦虑

### 2. B站视频下载

- 纯 Python 实现，无需 ffmpeg 等外部依赖
- 自动识别 B 站视频链接，调用官方 API 获取播放地址
- 分段下载自动合并，输出标准 MP4 文件
- 支持 BVID 链接、b23.tv 短链接

### 3. 智能算法优化

- **去重优先**：已下载资源直接复用，避免重复请求
- **已知失败缓存**：失败 URL 自动记录，不再重复尝试
- **CSS 延迟处理**：防止线程池死锁，确保所有 CSS 内联资源被处理
- **自适应并发**：64 线程并发，请求间隔 0.005s

### 4. 资源路径精确重写

- 使用 `resolve()` 精确计算相对/绝对路径
- 子目录资源正确加载，无断链问题
- 深度克隆后直接双击 index.html 即可离线浏览

### 5. 动态渲染引擎

- Playwright 模拟滚动 + 网络空闲检测
- 5 次滚动模拟用户浏览行为
- SPA 单页应用完整抓取
- 动态加载内容不丢失

### 6. 裂变抓取模式

- 深度 999 / 页面上限 9999
- 自动遍历全站链接
- 同域名自动递归
- 外域资源可选下载

---

## 架构流程

```
输入URL
  |
  v
[预扫描阶段]
  |  - 抓取首页HTML
  |  - 解析所有资源引用
  |  - 统计子页面数量
  |  - 确定进度条总数
  v
[动态渲染] (可选)
  |  - Playwright 浏览器渲染
  |  - 5次滚动模拟
  |  - 等待网络空闲
  v
[页面处理队列]
  |  - BFS广度优先遍历
  |  - 深度/页面数限制
  |  - 同域名过滤
  v
[资源提取]
  |  - BeautifulSoup 解析HTML
  |  - 提取 img/script/link/video/audio 等
  |  - CSS 内联 url() 资源
  |  - srcset 响应式图片
  v
[64线程并发下载]
  |  - 去重优先机制
  |  - 10次自动重试
  |  - 指数退避策略
  |  - 连接池复用
  v
[视频下载] (B站等视频网站
  |  - 自动识别视频链接
  |  - API解析真实地址
  |  - 分段下载合并
  v
[路径重写引擎]
  |  - resolve() 精确路径计算
  |  - 相对路径转换
  |  - 失败资源回退原站
  v
输出: index.html + _assets/ + _videos/
```

---

## 快速开始

### 方式一：EXE 版本（推荐 Windows 用户）

从 **[下载 V15.5](https://github.com/k20120509/web-cp/releases/tag/V15.5) 中的 `website_cloner.exe`，双击运行，无需任何依赖。

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
╔══════════════════════════════════════╗
║     网站克隆器 V15.5                 ║
╠══════════════════════════════════════╣
║  [1] 简单打包 - 自定义深度(1-12)     ║
║  [2] 全面打包 - 自动裂变模式         ║
║  [0] 退出                            ║
╚══════════════════════════════════════╝
```

- **简单打包**：适合单页或少量页面，深度 1~12 可选
- **全面打包**：裂变模式，深度 999，页面上限 9999

---

## 输出结构

```
cloned_site/
├── index.html              # 主入口（双击离线打开）
├── about.html               # 子页面
├── category/
│   └── page1.html
│   └── page2.html
├── _assets/               # 资源文件
│   ├── www.example.com/
│   │   ├── abc123.css
│   │   ├── def456.js
│   │   └── ghi789.png
│   │   └── ...
│   └── cdn.other.com/     # 外域资源（如果开启）
│       └── ...
└── _videos/               # 视频文件（仅视频网站）
    └── video_title.mp4
```

---

## 技术参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 并发线程 | 64 | 最大同时下载数 |
| 请求间隔 | 0.005s | 每次请求前的延迟 |
| 连接超时 | 4s | 建立连接超时时间 |
| 读取超时 | 12s | 数据传输超时时间 |
| 最大重试 | 10次 | 失败自动重试次数 |
| 重试退避 | 指数 0.3s | 重试间隔递增策略 |
| 裂变深度 | 999 | 全面打包模式深度 |
| 页面上限 | 9999 | 全面打包模式最大页面数 |
| 滚动次数 | 5次 | 动态渲染滚动模拟次数 |
| 滚动间隔 | 800ms | 每次滚动等待时间 |

---

## 克隆案例展示

### 案例 1：Example.com（基准测试）

| 指标 | 数值 |
|------|------|
| 目标网站 | https://www.example.com |
| 深度 | 1 |
| 耗时 | 1.2 秒 |
| 页面 | 1/1 (100%) |
| 资源 | 0/0 |
| 文件数 | 1 |
| 大小 | ~1 KB |
| 成功率 | 100% |

**说明：** 极简静态页面，用于验证程序基础功能正常。

---

### 案例 2：W3Schools（教学网站）

| 指标 | 数值 |
|------|------|
| 目标网站 | https://www.w3schools.com |
| 深度 | 1 |
| 页面 | 多页 |
| 资源类型 | HTML/CSS/JS/图片/字体 |
| 特点 | 大量教学资源站 |

---

### 案例 3：哔哩哔哩 B站（动态网站 + 视频）

| 指标 | 数值 |
|------|------|
| 目标网站 | https://www.bilibili.com |
| 深度 | 1 |
| 耗时 | ~312 秒 |
| 页面 | 56/56 (100%) |
| 资源 | 2901/2911 (99.7%) |
| 大小 | 325.75 MB |
| 文件分布 | JPG: 1850 / PNG: 712 / CSS: 34 / JS: 87 / WOFF2: 109 |
| 视频下载 | 支持（V15.5新增） |

**特点：**
- 动态渲染完整抓取 SPA 页面
- 自动识别并下载嵌入视频
- 图片资源完整本地化
- 样式脚本完整保留

---

### 案例 4：知乎（动态内容）

| 指标 | 数值 |
|------|------|
| 目标网站 | https://www.zhihu.com |
| 深度 | 1 |
| 耗时 | ~60 秒 |
| 页面 | 10/10 (100%) |
| 资源 | ~99 个 |
| 大小 | ~38 MB |
| 动态渲染 | 支持 |

**特点：**
- 动态渲染获取完整内容
- 用户信息、回答内容完整抓取
- 图片资源本地化

---

## 版本历史

| 版本 | 日期 | 核心特性 |
|------|------|----------|
| **V15.5** | 2026-07-28 | 预扫描进度条 + B站视频下载 + 图标修复 + 智能算法优化 |
| V13.0 | 2026-07-27 | 路径精确重写 + CSS死锁修复 + B站深度1验证 |
| V11.0 | 2026-07-27 | 128线程 + 完成弹窗统计 |
| V10 Pro Max | 2026-07-27 | 极简UI + 裂变模式 |
| V10.0 | 2026-07-27 | 性能优化版 |
| V8.5 Beta | 2026-07-27 | Beta 测试版 |
| V8.0 | 2026-07-27 | 重试机制 + 丢包统计 |
| V7.0 | 2026-07-27 | 并发引擎 |
| V6.0 | 2026-07-27 | 正式版 |
| V5.0 | 2026-07-27 | 资源下载 |
| V4.0 | 2026-07-27 | 递归爬取 |
| V3.0 | 2026-07-27 | 基础单页克隆（含源码） |

---

## 完整版本列表

| 版本 | 说明 | 文件 |
|------|------|------|
| **V15.5 (最新)** | 进度条修复 + B站视频下载 + 图标修复 + 算法优化 | V15.5/website_cloner.exe |
| V13.0 | 路径精确重写 + CSS死锁修复 + B站验证 | V13.0/website_cloner.exe |
| V11.0 | 128线程 + 完成弹窗统计 | V11.0/website_cloner.exe |
| V10 Pro Max | 极简UI + 裂变模式 | V10 Pro Max/website_cloner.exe |
| V10.0 | 性能优化版 | V10.0/website_cloner.exe |
| V8.5 Beta | Beta 测试版 | V8.5 Beta/website_cloner.exe |
| V8.0 | 重试机制 + 丢包统计 | V8.0/website_cloner.exe |
| V7.0 | 并发引擎 | V7.0/website_cloner.exe |
| V6.0 | 正式版 | V6.0/website_cloner.exe |
| V5.0 | 资源下载 | V5.0/website_cloner.exe |
| V4.0 | 递归爬取 | V4.0/website_cloner.exe |
| V3.0 | 基础版（含源码） | V3.0/website_cloner.py |
| latest | 快捷方式（指向最新版） | latest/website_cloner.exe |

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

## 常见问题

### Q: 克隆后双击 index.html 打不开？

A: 这是因为浏览器的 file:// 协议限制。建议启动一个本地服务器：

```bash
# Python 自带服务器
python -m http.server 8000
# 然后访问 http://localhost:8000
```

### Q: 动态网站克隆后内容不全？

A: 确保安装了 Playwright：

```bash
pip install playwright
playwright install chromium
```

程序会自动检测并使用动态渲染。

### Q: 进度条为什么一开始不动？

A: V15.5 新增了预扫描阶段，会先解析首页确定总任务数。这个阶段进度条显示的是预扫描进度，扫描完成后正式下载阶段进度条会快速前进。这是正常现象，确保总数准确，不会倒退。

### Q: B站视频下载失败怎么办？

A:
1. 检查网络连接是否正常
2. 确认视频链接是否有效（在浏览器中能正常播放）
3. 部分高清晰度视频可能需要登录，V15.5 默认使用 720P

### Q: 下载速度慢？

A:
- 程序已使用 64 线程并发下载
- 速度取决于目标网站的带宽限制
- 可以尝试在网络环境较好的时候使用

### Q: 能克隆需要登录的网站吗？

A: 当前版本不支持登录态克隆。需要登录才能访问的内容无法克隆。

---

## 相关仓库

- **[DL-Web_cp](https://github.com/k20120509/DL-Web_cp)** - 独立视频下载工具，支持 B 站等平台视频下载

---

## 免责声明

1. 本工具仅供学习交流使用
2. 请勿用于商业用途
3. 克隆的网站内容版权归原网站所有
4. 使用本工具产生的任何问题由使用者自行承担

---

## 许可证

MIT License

---

*Built with precision. Clone anything.*
