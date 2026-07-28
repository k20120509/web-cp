# -*- coding: utf-8 -*-
"""
网站克隆器 v2 - Website Cloner
输入目标网址，在当前目录生成完全复刻的静态 HTML 站点。

特性:
  * 进度条 + ETA 预估时间
  * 失败链接自动回退到原网站（点击跳转原站，不再是死链）
  * 代理池 + 自动重试（支持免费/付费代理，自动轮换、剔除坏代理）
  * 默认只克隆首页，其余链接点击跳转原网站（可调深度递归爬取）
  * 动态网站(如 B 站)支持：检测 SPA 自动用 Playwright 渲染
  * 速度优化：连接池、并发下载、流式大文件、快速失败
  * 完成后自动打包 zip 压缩包

用法:
    python website_cloner.py
    粘贴目标网址即可。

可选依赖:
    pip install requests beautifulsoup4
    pip install playwright         # 动态网站渲染(可选)
    playwright install chromium    # 安装浏览器内核(可选)

代理:
    在脚本同目录放 proxies.txt，每行一个代理，如:
        http://127.0.0.1:7890
        socks5://127.0.0.1:1080
        http://user:pass@host:port
    留空则不使用代理。也可设置环境变量 HTTP_PROXY/HTTPS_PROXY。
"""

import os
import sys
import re
import time
import shutil
import hashlib
import threading
from urllib.parse import urljoin, urlparse, urlunparse, unquote
from pathlib import Path, PurePosixPath
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    from bs4 import BeautifulSoup
except ImportError as e:
    print(f"缺少依赖: {e}")
    print("请运行: pip install requests beautifulsoup4")
    sys.exit(1)

# Playwright 可选
try:
    from playwright.sync_api import sync_playwright
    _HAS_PLAYWRIGHT = True
except Exception:
    _HAS_PLAYWRIGHT = False


# ===================== 配置 =====================
DEFAULT_MAX_DEPTH = 0          # 默认只克隆首页；>0 会递归爬取内部页面
DEFAULT_MAX_PAGES = 50         # 递归模式下最多爬取页面数
CONNECT_TIMEOUT = 8            # 连接超时(秒) —— 快速失败
READ_TIMEOUT = 25              # 读取超时(秒)
REQUEST_DELAY = 0.05           # 请求间隔(秒)
MAX_WORKERS = 12               # 资源下载并发数
MAX_RETRIES = 3                # 单个请求最大重试次数
LARGE_FILE_THRESHOLD = 8 * 1024 * 1024   # >8MB 走流式下载
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# 资源标签/属性
RESOURCE_SPECS = [
    ("img", "src"), ("img", "data-src"),
    ("script", "src"), ("link", "href"),
    ("source", "src"), ("source", "srcset"),
    ("video", "src"), ("video", "poster"),
    ("audio", "src"), ("iframe", "src"),
    ("embed", "src"), ("object", "data"),
    ("input", "src"),
]
CSS_URL_RE = re.compile(r"url\(\s*['\"]?([^'\"\)]+)['\"]?\s*\)", re.IGNORECASE)
CSS_IMPORT_RE = re.compile(r'@import\s+["\']([^"\']+)["\']', re.IGNORECASE)

# SPA 检测标志
SPA_HINTS = ["__NEXT_DATA__", "__NUXT__", "id=\"app\"", "id=\"root\"",
             "window.__INITIAL_STATE__", "data-reactroot", "data-server-rendered"]


# ===================== 统计 =====================
class Stats:
    def __init__(self):
        self.pages_ok = 0
        self.pages_fail = 0
        self.assets_ok = 0
        self.assets_fail = 0
        self.bytes = 0
        self.lock = threading.Lock()

    def add_bytes(self, n):
        with self.lock:
            self.bytes += n

    @property
    def total_tasks_done(self):
        return self.pages_ok + self.pages_fail + self.assets_ok + self.assets_fail


STATS = Stats()


def log(msg, level="INFO"):
    print(f"[{level}] {msg}", flush=True)


# ===================== 工具函数 =====================
def safe_name(s):
    s = unquote(s)
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s)
    return s[:120].strip(" .") or "index"


def is_same_domain(url, base_domain):
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    if not host:
        return False
    base = base_domain.lower()
    return host == base or host.endswith("." + base)


ASSET_EXTS = (
    ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".svg", ".ico", ".bmp", ".mp4", ".webm", ".mp3", ".wav", ".ogg",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".pdf", ".zip", ".rar",
    ".json", ".xml", ".txt", ".map",
)


def is_page(url):
    path = urlparse(url).path.lower()
    return not any(path.endswith(e) for e in ASSET_EXTS)


def normalize_url(url):
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", p.query, ""))


CT_EXT = {
    "text/html": ".html", "text/css": ".css",
    "application/javascript": ".js", "text/javascript": ".js",
    "application/json": ".json", "image/png": ".png", "image/jpeg": ".jpg",
    "image/gif": ".gif", "image/webp": ".webp", "image/svg+xml": ".svg",
    "image/x-icon": ".ico", "image/bmp": ".bmp", "video/mp4": ".mp4",
    "video/webm": ".webm", "audio/mpeg": ".mp3", "audio/ogg": ".ogg",
    "audio/wav": ".wav", "font/woff": ".woff", "font/woff2": ".woff2",
    "application/font-woff": ".woff", "application/vnd.ms-fontobject": ".eot",
}


def ct_to_ext(ct):
    return CT_EXT.get((ct or "").lower().split(";")[0].strip(), "")


def guess_ext(url):
    return PurePosixPath(urlparse(url).path).suffix.lower()


def looks_like_spa(html):
    """启发式检测是否为单页应用(SPA)，这类页面需要 JS 渲染。"""
    if not html:
        return False
    low = html.lower()
    # body 内可见文本极少
    try:
        soup = BeautifulSoup(html, "html.parser")
        body = soup.body
        text_len = len(body.get_text(strip=True)) if body else 0
    except Exception:
        text_len = 0
    has_hint = any(h.lower() in low for h in SPA_HINTS)
    script_count = low.count("<script")
    return text_len < 200 and (has_hint or script_count >= 5)


# ===================== 代理池 =====================
class ProxyPool:
    """代理池：自动轮换，失败自动剔除，支持无代理回退。"""

    def __init__(self, proxies_file=None):
        self.proxies = []
        self.bad = set()
        self.lock = threading.Lock()
        self.idx = 0
        self._load(proxies_file)

    def _load(self, path):
        # 1. 显式文件
        if path and Path(path).exists():
            self._read_file(path)
        # 2. 脚本同目录 proxies.txt
        if not self.proxies:
            local = Path(__file__).parent / "proxies.txt"
            if local.exists():
                self._read_file(local)
        if self.proxies:
            log(f"已加载 {len(self.proxies)} 个代理")

    def _read_file(self, path):
        try:
            for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    self.proxies.append(line)
        except Exception as e:
            log(f"读取代理文件失败: {e}", "WARN")

    def get(self):
        """返回一个可用代理 URL，无可用则返回 None(直连)。"""
        with self.lock:
            alive = [p for p in self.proxies if p not in self.bad]
            if not alive:
                return None
            p = alive[self.idx % len(alive)]
            self.idx += 1
            return p

    def mark_bad(self, proxy):
        if proxy:
            with self.lock:
                self.bad.add(proxy)

    def has_proxy(self):
        return bool(self.proxies)


# ===================== 进度条 =====================
class ProgressBar:
    """实时进度条 + 速度 + ETA。任务总数动态调整，线程安全。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.done = 0
        self.total = 0
        self.start = time.time()
        self.recent = []          # 滚动耗时窗口
        self.recent_bytes = []    # 滚动字节数窗口(算速度)
        self.last_render = 0
        self.current = ""         # 当前任务描述

    def set_total(self, n):
        with self.lock:
            self.total = max(self.total, n)

    def add_total(self, n):
        with self.lock:
            self.total += n

    def set_current(self, desc):
        with self.lock:
            self.current = desc[:40]

    def step(self, cost=None, nbytes=0):
        with self.lock:
            self.done += 1
            if cost is not None:
                self.recent.append(cost)
                if len(self.recent) > 20:
                    self.recent.pop(0)
            if nbytes:
                self.recent_bytes.append((time.time(), nbytes))
                # 只保留最近 10 秒内的字节
                cutoff = time.time() - 10
                self.recent_bytes = [t for t in self.recent_bytes if t[0] > cutoff]
            self._render()

    def _render(self):
        now = time.time()
        # 限频：至少间隔 0.1s 刷新一次，避免抖动
        if now - self.last_render < 0.1 and self.done < self.total:
            return
        self.last_render = now
        elapsed = now - self.start
        total = max(self.total, self.done)
        pct = self.done / total if total else 0
        bar_w = 30
        filled = int(bar_w * pct)
        bar = "█" * filled + "░" * (bar_w - filled)
        avg = (sum(self.recent) / len(self.recent)) if self.recent else (elapsed / self.done if self.done else 0)
        remain = max(0, (total - self.done)) * avg
        eta = self._fmt(remain)
        el = self._fmt(elapsed)
        # 速度
        if self.recent_bytes:
            bps = sum(b for _, b in self.recent_bytes) / max(1, now - self.recent_bytes[0][0])
            speed = self._size(bps)
        else:
            speed = "-"
        cur = self.current
        sys.stdout.write(
            f"\r  {bar} {pct:6.1%} | {self.done}/{total} | {speed}/s | 用时{el} | 剩余{eta} | {cur}   "
        )
        sys.stdout.flush()

    @staticmethod
    def _fmt(s):
        s = int(s)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}m{s:02d}s"
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m"

    @staticmethod
    def _size(n):
        for u in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f}{u}"
            n /= 1024
        return f"{n:.1f}TB"

    def finish(self):
        with self.lock:
            self.done = self.total = max(self.done, self.total)
            self._render()
        sys.stdout.write("\n")
        sys.stdout.flush()


PROGRESS = ProgressBar()


# ===================== 克隆器 =====================
class WebsiteCloner:
    def __init__(self, start_url, out_root, max_depth=DEFAULT_MAX_DEPTH,
                 max_pages=DEFAULT_MAX_PAGES, use_js=False, make_zip=True,
                 include_external=True):
        self.start_url = normalize_url(start_url)
        self.base_domain = urlparse(self.start_url).netloc
        self.out_root = Path(out_root)
        self.assets_dir = self.out_root / "_assets"
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.use_js = use_js and _HAS_PLAYWRIGHT
        self.make_zip = make_zip
        self.include_external = include_external   # 是否下载外域资源

        self.proxy_pool = ProxyPool()
        self.session = self._build_session()
        self.visited_pages = set()
        self.downloaded = {}        # url -> (local_rel, ok:bool)
        self.page_queue = []
        self.lock = threading.Lock()
        self._pw = None
        self._browser = None

    def _build_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        # 连接池 + 内置重试
        retry = Retry(total=MAX_RETRIES, backoff_factor=0.4,
                      status_forcelist=(500, 502, 503, 504),
                      allowed_methods=frozenset(["GET"]))
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=32,
                              max_retries=retry)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        return s

    # ---------- 路径 ----------
    def asset_path(self, url):
        p = urlparse(url)
        netloc = p.netloc or self.base_domain
        rel = unquote(p.path).lstrip("/") or "index"
        ext = guess_ext(url)
        if not ext:
            ext = ".bin"
            if p.query:
                ext = "_" + hashlib.md5(p.query.encode()).hexdigest()[:8] + ext
        local = self.assets_dir / netloc / rel
        if not local.suffix:
            local = local.with_name(local.name + ext)
        return local

    def page_path(self, url):
        p = urlparse(url)
        rel = unquote(p.path).lstrip("/") or "index"
        if rel.endswith("/"):
            rel += "index"
        local = self.out_root / rel
        if not local.suffix:
            local = local.with_name(local.name + ".html")
        elif local.suffix.lower() not in (".html", ".htm"):
            local = local.with_name(local.name + ".html")
        return local

    @staticmethod
    def rel(from_file, to_file):
        return os.path.relpath(to_file, start=from_file.parent).replace("\\", "/")

    # ---------- 请求 ----------
    def fetch(self, url, stream=False):
        """带代理轮换 + 重试的 GET。失败返回 None。"""
        last_err = None
        for attempt in range(MAX_RETRIES + 1):
            proxy = self.proxy_pool.get()
            proxies = {"http": proxy, "https": proxy} if proxy else None
            try:
                time.sleep(REQUEST_DELAY)
                r = self.session.get(
                    url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                    allow_redirects=True, stream=stream, proxies=proxies,
                )
                if r.status_code < 400:
                    return r
                # 4xx 不重试（404 等），直接返回让上层处理
                if r.status_code in (403, 429):
                    # 可能被限流，换代理重试
                    self.proxy_pool.mark_bad(proxy)
                    last_err = f"HTTP {r.status_code}"
                    continue
                return r
            except requests.RequestException as e:
                last_err = e.__class__.__name__
                self.proxy_pool.mark_bad(proxy)
                if attempt < MAX_RETRIES:
                    time.sleep(0.3 * (attempt + 1))
        log(f"请求失败: {url} ({last_err})", "WARN")
        return None

    def fetch_html(self, url):
        """获取页面 HTML。普通请求失败 / 检测到 SPA 时尝试 Playwright。"""
        r = self.fetch(url)
        html = None
        final_url = url
        if r is not None and r.status_code < 400:
            html = r.text
            final_url = normalize_url(r.url)

        need_js = False
        if html and looks_like_spa(html):
            need_js = True
        if (html is None or need_js) and self.use_js:
            log(f"使用动态渲染: {url}", "INFO")
            html2, final_url2 = self._render_js(url)
            if html2:
                return html2, final_url2
        if html is None:
            return None, url
        return html, final_url

    def _render_js(self, url):
        """用 Playwright 渲染页面（处理 B 站等 SPA）。"""
        try:
            if self._browser is None:
                self._pw = sync_playwright().start()
                self._browser = self._pw.chromium.launch(headless=True)
            ctx = self._browser.new_context(user_agent=USER_AGENT)
            pg = ctx.new_page()
            pg.goto(url, wait_until="networkidle", timeout=READ_TIMEOUT * 1000)
            pg.wait_for_timeout(1500)
            html = pg.content()
            final_url = pg.url
            pg.close()
            ctx.close()
            return html, normalize_url(final_url)
        except Exception as e:
            log(f"动态渲染失败: {url} ({e.__class__.__name__})", "WARN")
            return None, url

    def close_js(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    # ---------- 资源下载 ----------
    def download_asset(self, url):
        """下载资源，返回 (local_rel, ok)。失败时 ok=False，调用方保留原 URL。"""
        with self.lock:
            if url in self.downloaded:
                return self.downloaded[url]

        # 不下载外域资源（保留原 URL 跳转）
        if not self.include_external and not is_same_domain(url, self.base_domain):
            with self.lock:
                self.downloaded[url] = (url, False)
            STATS.assets_fail += 1
            return (url, False)

        PROGRESS.set_current(os.path.basename(urlparse(url).path)[:40] or url[:40])
        local = self.asset_path(url)
        rel = self.rel(self.out_root / "index.html", local)
        with self.lock:
            self.downloaded[url] = (rel, False)  # 占位

        t0 = time.time()
        r = self.fetch(url, stream=True)
        if r is None or r.status_code >= 400:
            STATS.assets_fail += 1
            PROGRESS.step(time.time() - t0)
            return (rel, False)

        # 校正扩展名
        ext = ct_to_ext(r.headers.get("Content-Type", ""))
        if ext and local.suffix.lower() != ext.lower():
            local = local.with_suffix(ext)
            rel = self.rel(self.out_root / "index.html", local)
            with self.lock:
                self.downloaded[url] = (rel, False)

        local.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            cl = int(r.headers.get("Content-Length", 0))
            # 统一用流式写入：内存优化，避免 r.content 一次性加载大文件
            with open(local, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        size += len(chunk)
            if size == 0:
                size = local.stat().st_size
        except Exception as e:
            log(f"资源写入失败: {url} ({e})", "WARN")
            STATS.assets_fail += 1
            PROGRESS.step(time.time() - t0)
            try:
                r.close()
            except Exception:
                pass
            return (rel, False)
        finally:
            try:
                r.close()    # 及时释放连接
            except Exception:
                pass

        STATS.add_bytes(size)
        STATS.assets_ok += 1
        with self.lock:
            self.downloaded[url] = (rel, True)
        PROGRESS.step(time.time() - t0, nbytes=size)

        # CSS 内部资源
        if local.suffix.lower() == ".css":
            self._process_css(local, url)
        return (rel, True)

    def _process_css(self, css_path, css_url):
        try:
            text = css_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return
        changed = False

        def repl_url(m):
            nonlocal changed
            raw = m.group(1).strip().strip("'\"")
            if not raw or raw.startswith(("data:", "#")):
                return m.group(0)
            absu = urljoin(css_url, raw)
            if not absu.startswith(("http://", "https://")):
                return m.group(0)
            rel, ok = self.download_asset(absu)
            if not ok:
                return m.group(0)   # 失败保留原 URL
            new_path = self.rel(css_path, self.out_root / rel)
            changed = True
            return f"url('{new_path}')"

        text = CSS_URL_RE.sub(repl_url, text)

        def repl_import(m):
            nonlocal changed
            raw = m.group(1).strip()
            if not raw or raw.startswith("data:"):
                return m.group(0)
            absu = urljoin(css_url, raw)
            if not absu.startswith(("http://", "https://")):
                return m.group(0)
            rel, ok = self.download_asset(absu)
            if not ok:
                return m.group(0)
            new_path = self.rel(css_path, self.out_root / rel)
            changed = True
            return f'@import "{new_path}"'

        text = CSS_IMPORT_RE.sub(repl_import, text)
        if changed:
            try:
                css_path.write_text(text, encoding="utf-8")
            except Exception:
                pass

    # ---------- 解析 URL ----------
    def _resolve(self, base_url, raw, base_href=None):
        if not raw:
            return None
        raw = raw.strip()
        if raw.startswith(("data:", "javascript:", "mailto:", "tel:", "#")):
            return None
        origin = base_href or base_url
        absu = urljoin(origin, raw)
        return absu if absu.startswith(("http://", "https://")) else None

    # ---------- 克隆页面 ----------
    def clone_page(self, url, depth):
        url = normalize_url(url)
        with self.lock:
            if url in self.visited_pages or STATS.pages_ok + STATS.pages_fail >= self.max_pages:
                return []
            self.visited_pages.add(url)

        log(f"[d{depth}] 克隆页面: {url}")
        t0 = time.time()
        html, final_url = self.fetch_html(url)
        if not html:
            STATS.pages_fail += 1
            PROGRESS.step(time.time() - t0)
            log(f"页面获取失败: {url}", "WARN")
            return []

        if final_url != url:
            with self.lock:
                self.visited_pages.add(final_url)

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            soup = BeautifulSoup(html, "lxml")

        base_href = None
        base_tag = soup.find("base", href=True)
        if base_tag:
            base_href = urljoin(final_url, base_tag["href"])

        # 预估：首页资源数用于进度条
        n_assets = self._count_assets(soup)
        PROGRESS.add_total(n_assets)

        # 下载并重写资源
        self._rewrite_resources(soup, final_url, base_href)

        # 重写链接（首页模式 depth=0 时，同域 <a> 保留绝对 URL 跳转原站）
        next_links = self._rewrite_links(soup, final_url, base_href, depth)

        for b in soup.find_all("base"):
            b.decompose()

        local = self.page_path(final_url if final_url != url else url)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(str(soup), encoding="utf-8")
        STATS.pages_ok += 1
        STATS.add_bytes(len(html.encode("utf-8")))
        PROGRESS.step(time.time() - t0)
        return next_links

    def _count_assets(self, soup):
        n = 0
        for tag, attr in RESOURCE_SPECS:
            for el in soup.find_all(tag):
                if el.get(attr):
                    n += 1
        for el in soup.find_all(["img", "source"]):
            if el.get("srcset"):
                n += el["srcset"].count(",") + 1
        return n

    def _rewrite_resources(self, soup, page_url, base_href):
        tasks = []
        for tag, attr in RESOURCE_SPECS:
            for el in soup.find_all(tag):
                val = el.get(attr)
                if not val:
                    continue
                absu = self._resolve(page_url, val, base_href)
                if absu:
                    tasks.append((el, attr, absu))
        # srcset
        srcset_els = []
        for el in soup.find_all(["img", "source"]):
            ss = el.get("srcset")
            if not ss:
                continue
            urls = []
            for part in ss.split(","):
                part = part.strip()
                if not part:
                    continue
                u = part.split()[0]
                absu = self._resolve(page_url, u, base_href)
                if absu:
                    tasks.append((None, None, absu))
                    urls.append(absu)
            srcset_els.append((el, urls))

        # 并发下载
        results = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(self.download_asset, au): au for (_, _, au) in tasks}
            for fut in as_completed(futs):
                au = futs[fut]
                try:
                    results[au] = fut.result()
                except Exception:
                    results[au] = (None, False)
                    STATS.assets_fail += 1

        page_file = self.page_path(page_url)
        # 重写属性：失败则保留原绝对 URL（跳转原站）
        for el, attr, au in tasks:
            rel, ok = results.get(au, (None, False))
            if ok and rel:
                new_path = self.rel(page_file, self.out_root / rel)
                if attr is not None:
                    el[attr] = new_path
            else:
                # 保留原始绝对 URL
                if attr is not None and el.get(attr):
                    el[attr] = au

        # srcset 重写
        for el, urls in srcset_els:
            ss = el.get("srcset", "")
            new_parts = []
            for part in ss.split(","):
                part = part.strip()
                if not part:
                    continue
                bits = part.split()
                u = bits[0]
                absu = self._resolve(page_url, u, base_href)
                if absu and absu in self.downloaded:
                    rel, ok = self.downloaded[absu]
                    if ok:
                        bits[0] = self.rel(page_file, self.out_root / rel)
                    else:
                        bits[0] = absu   # 回退原 URL
                new_parts.append(" ".join(bits))
            el["srcset"] = ", ".join(new_parts)

        # 内联 style / <style>
        for style in soup.find_all("style"):
            if style.string:
                new = self._rewrite_css_text(style.string, page_url, base_href)
                if new is not None:
                    style.string.replace_with(new)
        for el in soup.find_all(style=True):
            new = self._rewrite_css_text(el["style"], page_url, base_href)
            if new is not None:
                el["style"] = new

    def _rewrite_css_text(self, text, page_url, base_href):
        changed = [False]
        page_file = self.page_path(page_url)

        def repl(m):
            raw = m.group(1).strip().strip("'\"")
            if not raw or raw.startswith(("data:", "#")):
                return m.group(0)
            absu = self._resolve(page_url, raw, base_href)
            if not absu:
                return m.group(0)
            rel, ok = self.download_asset(absu)
            changed[0] = True
            if ok:
                new_path = self.rel(page_file, self.out_root / rel)
                return f"url('{new_path}')"
            return m.group(0)   # 失败保留原 URL

        new = CSS_URL_RE.sub(repl, text)
        return new if changed[0] else None

    def _rewrite_links(self, soup, page_url, base_href, depth):
        """重写 <a href>。首页模式(depth=0)下同域链接保留绝对 URL 跳转原站。"""
        next_links = []
        for a in soup.find_all("a", href=True):
            raw = a["href"].strip()
            if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                continue
            absu = self._resolve(page_url, raw, base_href)
            if not absu:
                continue

            # 外链：保留绝对 URL
            if not is_same_domain(absu, self.base_domain):
                a["href"] = absu
                a["target"] = "_blank"
                continue

            if not is_page(absu):
                # 资源型链接：下载，失败回退原 URL
                rel, ok = self.download_asset(absu)
                if ok:
                    a["href"] = self.rel(self.page_path(page_url), self.out_root / rel)
                else:
                    a["href"] = absu
                continue

            # 首页模式：同域页面链接保留绝对 URL，点击跳转原站
            if self.max_depth == 0:
                a["href"] = normalize_url(absu)
                a["target"] = "_blank"
                continue

            # 递归模式：指向本地页面
            norm = normalize_url(absu)
            local_target = self.page_path(norm)
            a["href"] = self.rel(self.page_path(page_url), local_target)
            if depth < self.max_depth and norm not in self.visited_pages:
                next_links.append((norm, depth + 1))
        return next_links

    # ---------- 主流程 ----------
    def run(self):
        log(f"开始克隆: {self.start_url}")
        log(f"输出目录: {self.out_root.resolve()}")
        mode = "仅首页(其余跳转原站)" if self.max_depth == 0 else f"递归深度{self.max_depth}"
        log(f"模式: {mode} | 并发: {MAX_WORKERS} | 动态渲染: {'开' if self.use_js else '关'}")
        log(f"代理: {'启用 ' + str(len(self.proxy_pool.proxies)) + '个' if self.proxy_pool.has_proxy() else '直连'}")
        log("-" * 60)

        self.out_root.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)

        PROGRESS.set_total(1)   # 至少首页
        self.page_queue.append((self.start_url, 0))
        while self.page_queue and STATS.pages_ok + STATS.pages_fail < self.max_pages:
            url, depth = self.page_queue.pop(0)
            if url in self.visited_pages:
                continue
            nxt = self.clone_page(url, depth)
            self.page_queue.extend(nxt)

        self.close_js()
        PROGRESS.set_total(STATS.total_tasks_done)
        PROGRESS.finish()

        # 首页复制为 index.html
        start_local = self.page_path(self.start_url)
        index_dst = self.out_root / "index.html"
        if start_local != index_dst and start_local.exists():
            try:
                index_dst.write_text(start_local.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass

        log("-" * 60)
        log("克隆完成!")
        log(f"  页面: 成功 {STATS.pages_ok} / 失败 {STATS.pages_fail}")
        log(f"  资源: 成功 {STATS.assets_ok} / 失败 {STATS.assets_fail}")
        log(f"  总大小: {STATS.bytes/1024/1024:.2f} MB")

        zip_path = None
        if self.make_zip:
            zip_path = self._make_zip()
        return index_dst, zip_path

    def _make_zip(self):
        """把整个输出目录打包成 zip。"""
        log("正在打包压缩包 ...")
        try:
            base = str(self.out_root)
            zip_base = shutil.make_archive(base, "zip", root_dir=base)
            zip_path = Path(zip_base)
            size = zip_path.stat().st_size / 1024 / 1024
            log(f"压缩包已生成: {zip_path.resolve()} ({size:.2f} MB)")
            return zip_path
        except Exception as e:
            log(f"打包失败: {e}", "WARN")
            return None


def stream_ok(r):
    """判断是否适合流式（已带 iter_content 能用即可）。"""
    return hasattr(r, "iter_content")


def _ask(prompt, default=None):
    """安全的输入，支持默认值。"""
    try:
        s = input(prompt).strip().lstrip("\ufeff").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not s and default is not None:
        return default
    return s


def _choose(prompt, options, default_idx=0):
    """交互选择。options: [(值, 说明), ...]。返回选中的值。"""
    print(f"\n{prompt}")
    for i, (_, desc) in enumerate(options, 1):
        mark = " (默认)" if i - 1 == default_idx else ""
        print(f"  {i}. {desc}{mark}")
    while True:
        s = _ask(f"请选择 [1-{len(options)}] (回车=默认): ", str(default_idx + 1))
        if s is None:
            return options[default_idx][0]
        try:
            idx = int(s) - 1
            if 0 <= idx < len(options):
                return options[idx][0]
        except ValueError:
            pass
        print("  输入无效，请重试。")


# ===================== 入口 =====================
def main():
    print("=" * 60)
    print("       网站克隆器 v3  Website Cloner")
    print("=" * 60)
    print("粘贴目标网址回车开始（输入 q 退出）")
    print(f"动态渲染(Playwright): {'可用' if _HAS_PLAYWRIGHT else '未安装(动态网站如B站需 pip install playwright 并 playwright install chromium)'}")
    print("-" * 60)

    url = _ask("目标网址> ")
    if not url or url.lower() in ("q", "quit", "exit"):
        print("已退出。")
        return
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    domain = safe_name(urlparse(url).netloc or "site")

    # 自定义下载选项
    print("\n--- 下载选项 ---")
    depth = _choose(
        "选择爬取深度：",
        [(0, "仅首页（其余链接点击跳转原站）"),
         (1, "首页 + 一层子页面"),
         (2, "首页 + 两层子页面"),
         (3, "首页 + 三层子页面（较慢）")],
        default_idx=0,
    )
    if depth > 0:
        mp = _choose(
            "选择最大页面数：",
            [(20, "20 页"), (50, "50 页"), (100, "100 页"), (200, "200 页（慢）")],
            default_idx=1,
        )
    else:
        mp = 1
    include_ext = _choose(
        "是否下载外域资源（如 CDN 的 JS/CSS/图片）：",
        [(True, "下载（完整复刻，但外域可能较慢/失败）"),
         (False, "不下载（仅同域资源，速度快；外域链接跳转原站）")],
        default_idx=0,
    )
    use_js = _choose(
        "是否启用动态渲染（B站/知乎等 SPA 站点需要）：",
        [(True, "自动启用（检测到 SPA 时用 Playwright 渲染）"),
         (False, "不启用（普通静态站点速度更快）")],
        default_idx=0,
    ) and _HAS_PLAYWRIGHT

    out_root = Path(os.getcwd()) / f"cloned_{domain}_{time.strftime('%Y%m%d_%H%M%S')}"

    print()
    log(f"目标: {url}")
    log(f"深度: {depth} | 页面上限: {mp} | 外域资源: {'下载' if include_ext else '跳过'} | 动态渲染: {'开' if use_js else '关'}")
    log(f"输出: {out_root.resolve()}")
    print("-" * 60)

    try:
        cloner = WebsiteCloner(url, out_root, max_depth=depth, max_pages=mp,
                               use_js=use_js, make_zip=True,
                               include_external=include_ext)
        entry, zip_path = cloner.run()
        print()
        print(f"  入口文件: {entry.resolve()}")
        if zip_path:
            print(f"  压缩包:   {zip_path.resolve()}")
        print(f"  目录:     {out_root.resolve()}")
    except KeyboardInterrupt:
        print("\n已中断，已下载内容保留在:", out_root)
    except Exception as e:
        log(f"发生错误: {e}", "ERROR")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
