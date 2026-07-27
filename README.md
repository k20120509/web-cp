# Website Cloner

一个功能强大的网站克隆工具，支持动态渲染、多线程并发、裂变爬取等特性。

## 快速开始

### 环境要求

- Python 3.8+
- 依赖：`requests`, `beautifulsoup4`, `lxml`
- 可选：`playwright`（动态渲染支持）

### 安装依赖

```bash
pip install requests beautifulsoup4 lxml
# 可选：安装 Playwright 支持动态渲染
pip install playwright
playwright install chromium
```

### 使用方法

```bash
python website_cloner.py
```

### 打包为可执行文件

```bash
pip install pyinstaller
pyinstaller --onefile --windowed website_cloner.py
```

## 功能特性

- 🎯 **资源路径精确重写**：确保所有资源正确引用（V13.0核心改进）
- 🤖 **动态渲染**：模拟用户滚动5次+等待网络空闲，支持淘宝、京东、B站等复杂网站
- 📈 **裂变模式**：深度999，页面上限9999，无限遍历网站
- 🔥 **64线程高并发**：请求间隔0.005s，连接超时4s，读取超时12s
- 🎨 **CSS资源延迟处理**：避免线程池死锁，确保CSS中的资源引用全部下载
- 📊 **效率统计**：爬取完成后弹窗显示效率和内容占比，支持继续或3秒后退出
- 🚫 **智能过滤**：过滤不需要下载的link rel类型（dns-prefetch/canonical等）
- 🖼️ **特殊字符处理**：处理图片URL中的@参数等特殊字符
- 🎯 **简单UI**：只有两个选项，零配置上手

## 使用说明

### 模式选择

1. **简单打包**：选择深度 1~12，适合普通网站
2. **全面打包**：全自动模式，能打包的全部打包，无法打包就跳过

### 输出结构

```
cloned_xxx/
├── index.html      # 主页面（双击即可离线打开）
├── page1.html      # 其他页面
└── _assets/        # 依赖资源目录
    ├── xxx.css
    ├── xxx.js
    ├── xxx.png
    └── ...
```

## 版本历史

| 版本 | 发布日期 | 核心特性 | 文件 |
|------|----------|----------|------|
| **V13.0** | 2026-07-27 | 资源路径精确重写、CSS死锁修复、B站测试99.7%效率 | `website_cloner.py` |
| **V11.0** | 2026-07-27 | 128线程、15次重试、弹窗统计 | `website_cloner.py`, `website_cloner.exe` |
| **V10 Pro Max** | 2026-07-27 | 简化UI、裂变爬取、动态加载增强 | `website_cloner.py`, `website_cloner.exe` |
| **V10.0** | 2026-07-27 | 内联加密修复、性能优化 | `website_cloner.py`, `website_cloner.exe` |
| **V8.5 Beta** | 2026-07-27 | 强制加密、随机密码自动解密 | `website_cloner.py` |
| **V8.0** | 2026-07-27 | 超级版、失败回刷、丢包率统计 | `website_cloner.py`, `website_cloner.exe` |
| **V7.0** | 2026-07-27 | 旗舰版、并发下载、重试机制 | `website_cloner.py`, `website_cloner.exe` |
| **V6.0** | 2026-07-27 | 正式版、基础功能完善 | `website_cloner.py`, `website_cloner.exe` |
| **V5.0** | 2026-07-27 | 完整版、资源下载 | `website_cloner.py`, `website_cloner.exe` |
| **V4.0** | 2026-07-27 | 增强版、递归爬取 | `website_cloner.py`, `website_cloner.exe` |
| **V3.0** | 2026-07-27 | 基础版、单页面克隆 | `website_cloner.py` |

## 测试结果（V13.0）

### 哔哩哔哩 (www.bilibili.com) 深度1克隆测试

- ⏱ 耗时: 312.5秒（约5分钟）
- 📄 页面: 56/56（100%成功）
- 📦 资源: 2901/2911（99.7%成功）
- 💾 总大小: 325.75 MB
- 🖼️ 图片: 1850张JPG + 712张PNG + 12个SVG
- 🎨 样式: 34个CSS + 87个JS
- 🔤 字体: 109个woff2 + 6个ttf + 5个otf等
- ✅ HTML资源引用: img src 14个全部本地化

### example.com 基线测试

- ⏱ 耗时: 0.8秒
- 📄 页面: 1/1（100%成功）
- ✅ index.html 正确生成

## 目录结构

```
website-cloner-open-source/
├── V13.0/              # V13.0 版本（最新，推荐使用）
│   └── website_cloner.py
├── V11.0/              # V11.0 版本
│   ├── website_cloner.py
│   └── website_cloner.exe
├── V10 Pro Max/        # V10 Pro Max 版本
│   ├── website_cloner.py
│   └── website_cloner.exe
├── V10.0/              # V10.0 版本
│   ├── website_cloner.py
│   └── website_cloner.exe
├── V8.5 Beta/          # V8.5 Beta 版本
│   └── website_cloner.py
├── V8.0/               # V8.0 版本
│   ├── website_cloner.py
│   └── website_cloner.exe
├── V7.0/               # V7.0 版本
│   ├── website_cloner.py
│   └── website_cloner.exe
├── V6.0/               # V6.0 版本
│   ├── website_cloner.py
│   └── website_cloner.exe
├── V5.0/               # V5.0 版本
│   ├── website_cloner.py
│   └── website_cloner.exe
├── V4.0/               # V4.0 版本
│   ├── website_cloner.py
│   └── website_cloner.exe
├── V3.0/               # V3.0 版本
│   └── website_cloner.py
├── latest/             # 最新版本快捷方式
│   └── website_cloner.py
├── LICENSE             # MIT 许可证
└── README.md           # 本说明文档
```

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！
