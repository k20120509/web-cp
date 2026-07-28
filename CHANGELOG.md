# Website Cloner - Changelog / 更新日志

---

## V16.0 (Current / 当前版本 - 2026-07-28)

### Major Fixes / 重大修复

1. **Fixed 100% hanging issue / 修复卡死100%问题**
   - Added timeout mechanism (10 min auto-complete) / 添加超时机制（10分钟自动完成）
   - Added no-progress detection (60s no activity marks complete) / 添加无进展检测（60秒无活动标记完成）
   - Progress bar supports force-complete state / 进度条支持强制完成状态

2. **Fixed empty page issue / 修复空页面问题**
   - Lowered HTML length threshold (500 -> 100 chars) / 降低HTML长度阈值（500->100字符）
   - Saves even very short content / 即使很短的内容也保存
   - Dynamic rendering fallback / 动态渲染回退机制

3. **Fixed rate limiting issue / 修复限流问题**
   - Increased request interval (0.005s -> 0.05s) / 增加请求间隔
   - Reduced retries (10 -> 3) / 降低重试次数
   - Added random delays / 添加随机延迟
   - Reduced concurrency (64 -> 32) / 降低并发数

### New Features / 新功能

4. **Proxy Pool Integration / 代理池集成**
   - Lightweight proxy pool / 轻量级代理池
   - Multi-source proxy fetching / 多源代理获取
   - Auto test and select best proxy / 自动测试选择最优代理
   - Menu option to enable/disable / 菜单可启用/禁用

5. **Improved UI / 改进界面**
   - README reading confirmation / README阅读确认提示
   - Proxy pool initialization option / 代理池初始化选项
   - Shows proxy status / 显示代理状态

---

## V15.5 (2026-07-28)

### Features / 功能
- Full rewrite with Playwright dynamic rendering / 使用 Playwright 动态渲染全面重写
- Bilingual README (Chinese + English) / 双语 README（中文+英文）
- Proxy pool support / 代理池支持
- Resource localization (CSS/JS/Images) / 资源本地化（CSS/JS/图片）
- Bilibili video download / 哔哩哔哩视频下载
- Resume support for resource downloads / 资源下载断点续传
- Digital signature support / 数字签名支持

### Variants / 变体
- V15.5-Beta: Beta test version / Beta 测试版本
- V15.5-Test: Internal test version / 内部测试版本

---

## V13.0

### Features / 功能
- Improved download performance / 改进下载性能
- Better error handling / 更好的错误处理
- Enhanced stability / 增强稳定性

---

## V11.0

### Features / 功能
- Added proxy support / 添加代理支持
- Improved page parsing / 改进页面解析

---

## V10 Pro Max

### Features / 功能
- Enhanced version with extended features / 增强版本，扩展功能
- Optimized resource handling / 优化资源处理

---

## V10.0

### Features / 功能
- Major update with dynamic page rendering / 重大更新，添加动态页面渲染
- Improved UI / 改进用户界面

---

## V8.0

### Features / 功能
- Improved stability / 改进稳定性
- Better progress feedback / 更好的进度反馈

---

## V7.0

### Features / 功能
- Added Bilibili support / 添加哔哩哔哩支持
- Improved download speed / 提升下载速度

---

## V6.0

### Features / 功能
- Added progress bar / 添加进度条
- Improved UI / 改进用户界面

---

## V5.0

### Features / 功能
- Added JavaScript support / 添加 JavaScript 支持
- Better resource detection / 更好的资源检测

---

## V4.0

### Features / 功能
- Added resource download (CSS/JS/Images) / 添加资源下载（CSS/JS/图片）
- Basic website cloning / 基础网站克隆

---

## V3.0

### Features / 功能
- Initial release / 初始版本
- Basic HTML page download / 基础 HTML 页面下载
