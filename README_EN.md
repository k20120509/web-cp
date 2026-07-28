# Website Cloner V16.0 - 网站克隆器

> One-click website cloning | Smart progress bar | Proxy acceleration | Video download | Portable

---

## ⚠️ Important Notice

This program has been signed with a **Windows Self-Signed Certificate** (CN=Web CP Tools, O=k20120509).

Since it's a developer self-signed certificate, **first-time download** on some computers may trigger:
- Windows SmartScreen warnings
- False positives from 360, Windows Defender, Huorong, etc.

### Solutions (choose one)

1. **Install signing certificate (Recommended)**: Extract → Double-click `webcp_signing.cer` → "Install Certificate" → Select "Trusted Root Certification Authorities" → Done
2. **Add to whitelist**: Add `website_cloner.exe` to trust list in your antivirus
3. **Temporarily disable antivirus**: Disable 360/Defender/Huorong before running

---

## Table of Contents

- [Quick Start](#quick-start)
- [Detailed Tutorial](#detailed-tutorial)
- [Features](#features)
- [FAQ](#faq)
- [Changelog](#changelog)

---

## Quick Start

### Step 1: Download and Extract

1. Visit [GitHub Releases](https://github.com/k20120509/web-cp/releases/tag/V16.0)
2. Download `website_cloner_v16.0.exe`
3. Place it in any folder (e.g., `D:\Tools\WebCloner\`)

### Step 2: Install Certificate (Required)

1. Double-click `webcp_signing.cer`
2. Click "Install Certificate"
3. Select "Local Computer" → Next
4. Select "Place all certificates in the following store" → "Trusted Root Certification Authorities"
5. Click Next → Finish
6. Restart your computer (or refresh system cache)

### Step 3: Run the Program

1. Double-click `website_cloner_v16.0.exe`
2. Read the welcome screen
3. Type `y` to confirm you've read the documentation
4. Choose whether to initialize the proxy pool (recommend: y)
5. Select cloning mode, enter URL, and start using!

---

## Detailed Tutorial

### 3.1 Simple Mode (Recommended for Beginners)

Suitable for most websites, with configurable crawling depth (1-12 levels).

```
Initialize proxy pool for acceleration? (y/n, default n): y
Initializing proxy pool...
Proxy pool ready! Best: 1.2.3.4:8080 (latency 0.52s)

Select mode:
  1. Simple mode - select crawling depth (1-12)
  2. Full mode - automatic deep crawl (depth 999, pages 9999)
Select [1/2]: 1
Crawling depth (1-12): 3
Target URL: https://example.com

Starting clone: https://example.com
Depth: 3, Max pages: 300
Proxy: Enabled
Output: E:\cloned_example_20260728_120000
```

**Depth explanation:**
- Depth 1: Clone homepage only
- Depth 2: Homepage + links on homepage
- Depth 3: Homepage + links + links' links (recommended)
- Depth 5-12: More complete clone (takes longer)

### 3.2 Full Mode (Advanced)

For complete website cloning, automatically crawls all links.

```
Select [1/2]: 2
Target URL: https://example.com

Starting clone: https://example.com
Depth: 999, Max pages: 9999
```

**Note:** This mode crawls a large number of pages and may take a long time. Test with simple mode first.

### 3.3 After Cloning

The program will automatically:
1. Open the cloned result folder in Explorer
2. Display statistics popup (success rate, total size, etc.)
3. Ask if you want to continue cloning other websites

---

## Features

### 1. Smart Progress Bar

- Pre-scan to determine total tasks
- Progress only goes up, never back
- Real-time download speed and ETA
- Auto-complete on timeout (prevents hanging)

### 2. Proxy Acceleration (New in V16.0)

- Auto fetch, test, and select best proxy
- Supports acceleration for foreign websites
- Menu option to enable

### 3. Dynamic Page Rendering

- JavaScript rendering with Playwright
- Simulate scrolling to trigger lazy-loaded images
- Wait for network idle before saving

### 4. Resource Localization

- HTML/CSS/JS/images/fonts all downloaded locally
- Auto rewrite resource paths to local paths
- CSS resource references also processed

### 5. Bilibili Video Download

- Auto-detect Bilibili video links on page
- Background download videos to `_videos` directory
- Support multiple quality options

### 6. Resume Support (Resource Downloads)

- Retry interrupted resource downloads
- Auto-retry failed links

---

## FAQ

### Q1: Progress bar stuck at 100%?

**A**: Fixed in V16.0! Added timeout mechanism:
- Auto-complete after 10 minutes total crawl time
- Auto-mark complete after 60 seconds of no progress
- Progress bar shows "[Timeout Complete]" message

### Q2: Cloned webpage is empty or incomplete?

**A**: V16.0 improved content fetching:
- Lowered HTML length threshold (500 → 100 chars)
- Saves even very short content
- Auto fallback to dynamic rendering

### Q3: Getting rate-limited by target website?

**A**: V16.0 optimized request strategy:
- Increased request interval (0.005s → 0.05s, 10x)
- Reduced retries (10 → 3)
- Added random delays to avoid pattern detection
- Reduced concurrency (64 → 32)
- **Tip:** Initializing proxy pool effectively avoids rate limiting

### Q4: How to view cloned website locally?

**A**: 
1. Open the cloned output directory
2. Double-click `index.html`
3. All resources are localized, accessible offline

### Q5: How to uninstall/clean up?

**A**: 
1. Delete `website_cloner_v16.0.exe`
2. Delete cloned result folders
3. Delete temporary files
4. To remove certificate:
   - Open Run → `certmgr.msc`
   - Find "Trusted Root Certification Authorities"
   - Delete `Web CP Tools` certificate

---

## Changelog

### V16.0 (2026-07-28)

**Major Update:**

1. **Fixed 100% hanging issue**
   - Added timeout mechanism (10 min auto-complete)
   - Added no-progress detection (60s no activity mark complete)
   - Progress bar supports force-complete state

2. **Fixed empty page issue**
   - Lowered HTML length threshold (500→100 chars)
   - Saves even very short content
   - Dynamic rendering fallback

3. **Fixed rate limiting issue**
   - Increased request interval (0.005s→0.05s)
   - Reduced retries (10→3)
   - Added random delays
   - Reduced concurrency (64→32)
   - Optimized Session configuration

4. **New Proxy Pool Feature**
   - Lightweight proxy pool integration
   - Multi-source proxy fetching
   - Auto test and select best proxy
   - Menu option to enable

5. **Improved User Interface**
   - Added README reading confirmation
   - Added proxy pool initialization option
   - Shows proxy status

### V15.5

- Pre-scan for accurate total count
- Smart progress bar (only increases)
- Bilibili video download support
- Playwright dynamic rendering

### V15.0

- Basic website cloning
- Resource localization
- Multi-threaded downloads

---

## Support

If you encounter problems:
1. Check the "FAQ" section in this README
2. Check GitHub Issues: [https://github.com/k20120509/web-cp/issues](https://github.com/k20120509/web-cp/issues)
3. Submit a new Issue describing your problem

---

**Thank you for using Website Cloner V16.0!**