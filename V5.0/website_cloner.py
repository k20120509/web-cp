# -*- coding: utf-8 -*-
"""
网站克隆器 v4 - Website Cloner
输入目标网址，克隆为本地静态站点。

特性:
  * 启动自检依赖(缺失自动安装)
  * 模式选择: 极简(0) / 1-10级递归 / 超级(全站) / 自定义
  * 双进度条: 总进度 + 当前任务, 多任务并行
  * 智能刷新下载: 多次请求合并资源, 保证主页完整
  * 失败链接回退原站; 代理池+重试; 流式内存优化
  * 自定义文件夹名/压缩包名
  * 完成后自动打包 zip

用法: python website_cloner.py
"""

import os
import sys
import re
import time
import shutil
import subprocess
import hashlib
import threading
from urllib.parse import urljoin, urlparse, urlunparse, unquote
from pathlib import Path, PurePosixPath
from concurrent.futures import ThreadPoolExecutor, as_completed

# ===================== 依赖自检 =====================
def _have(mod):
    try:
        __import__(mod)
        return True
    except Exception:
        return False

def ensure_deps():
    """启动时检测依赖，缺失则自动 pip 安装。已装则跳过。
    打包成 exe 后(sys.frozen)，基础依赖已内置，只检查 playwright。"""
    frozen = getattr(sys, "frozen", False)
    needed = []
    if not frozen:   # 非打包模式才检查基础依赖
        if not _have("requests"):
            needed.append("requests")
        if not _have("bs4"):
            needed.append("beautifulsoup4")
    pw_ok = _have("playwright")
    if needed:
        print(f"[依赖] 检测到缺失: {', '.join(needed)}，正在自动安装 ...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", *needed, "-q"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            print("[依赖] 安装完成。")
        except Exception as e:
            print(f"[依赖] 自动安装失败: {e}")
            print(f"请手动运行: pip install {' '.join(needed)}")
            sys.exit(1)
    else:
        print("[依赖] 基础依赖已就绪。")
    if not pw_ok:
        print("[依赖] Playwright 未安装(动态网站如B站需要)。")
        print("       如需启用: pip install playwright && playwright install chromium")
    else:
        try:
            from playwright.sync_api import sync_playwright  # noqa
            print("[依赖] Playwright 已就绪。")
        except Exception:
            print("[依赖] Playwright 已安装，但浏览器内核缺失，正在下载 chromium ...")
            try:
                py = sys.executable if not frozen else "python"
                subprocess.check_call([py, "-m", "playwright", "install", "chromium"])
                print("[依赖] chromium 下载完成。")
            except Exception as e:
                print(f"[依赖] chromium 下载失败: {e}")
    print("-" * 60)

ensure_deps()

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright
    _HAS_PLAYWRIGHT = True
except Exception:
    _HAS_PLAYWRIGHT = False


# ===================== 配置 =====================
CONNECT_TIMEOUT = 8
READ_TIMEOUT = 25
REQUEST_DELAY = 0.05
MAX_WORKERS = 12
MAX_RETRIES = 3
LARGE_FILE_THRESHOLD = 8 * 1024 * 1024
SMART_REFRESH = 3          # 智能刷新次数(主页多请求合并资源)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

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
SPA_HINTS = ["__NEXT_DATA__", "__NUXT__", "id=\"app\"", "id=\"root\"",
             "window.__INITIAL_STATE__", "data-reactroot"]

# 模式预设: (深度, 最大页面数, 说明)
MODES = [
    (0, 1,    "极简模式: 仅克隆首页(资源全下，其余链接跳转原站)"),
    (1, 20,   "模式 1: 首页 + 一层子页面(约20页)"),
    (2, 50,   "模式 2: 首页 + 两层子页面(约50页)"),
    (3, 100,  "模式 3: 首页 + 三层子页面(约100页)"),
    (4, 150,  "模式 4: 四层(约150页)"),
    (5, 200,  "模式 5: 五层(约200页)"),
    (6, 250,  "模式 6: 六层(约250页)"),
    (7, 300,  "模式 7: 七层(约300页)"),
    (8, 350,  "模式 8: 八层(约350页)"),
    (9, 400,  "模式 9: 九层(约400页)"),
    (10, 450, "模式 10: 十层(约450页)"),
    (500, 1000, "超级模式: 全站完整克隆(深度500,最多1000页,较慢)"),
]


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
    def total_done(self):
        return self.pages_ok + self.pages_fail + self.assets_ok + self.assets_fail


STATS = Stats()


def log(msg, level="INFO"):
    print(f"[{level}] {msg}", flush=True)


# ===================== 工具 =====================
def safe_name(s):
    s = unquote(s)
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s)
    return s[:80].strip(" .") or "index"


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
    if not html:
        return False
    low = html.lower()
    try:
        soup = BeautifulSoup(html, "html.parser")
        body = soup.body
        text_len = len(body.get_text(strip=True)) if body else 0
    except Exception:
        text_len = 0
    has_hint = any(h.lower() in low for h in SPA_HINTS)
    return text_len < 200 and (has_hint or low.count("<script") >= 5)


# ===================== 代理池 =====================
class ProxyPool:
    def __init__(self):
        self.proxies = []
        self.bad = set()
        self.lock = threading.Lock()
        self.idx = 0
        local = Path(__file__).parent / "proxies.txt" if "__file__" in dir() else Path("proxies.txt")
        # 打包成 exe 后 __file__ 不可靠，用 cwd
        for p in [Path(os.getcwd()) / "proxies.txt", local]:
            if p.exists():
                try:
                    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                        line = line.strip()
                        if line and not line.startswith("#"):
                            self.proxies.append(line)
                except Exception:
                    pass
                break
        if self.proxies:
            log(f"已加载 {len(self.proxies)} 个代理")

    def get(self):
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


# ===================== 双进度条 =====================
class DualProgress:
    """双进度条: 第1行总进度, 第2行当前任务。线程安全。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.done = 0
        self.total = 0
        self.start = time.time()
        self.recent = []
        self.recent_bytes = []
        self.last_render = 0
        self.current = ""
        self.cur_done = 0
        self.cur_total = 0

    def set_total(self, n):
        with self.lock:
            self.total = max(self.total, n)

    def add_total(self, n):
        with self.lock:
            self.total += n

    def set_current(self, desc, cur_done=0, cur_total=0):
        with self.lock:
            self.current = desc[:38]
            self.cur_done = cur_done
            self.cur_total = cur_total

    def step(self, cost=None, nbytes=0):
        with self.lock:
            self.done += 1
            if cost is not None:
                self.recent.append(cost)
                if len(self.recent) > 20:
                    self.recent.pop(0)
            if nbytes:
                self.recent_bytes.append((time.time(), nbytes))
                cutoff = time.time() - 10
                self.recent_bytes = [t for t in self.recent_bytes if t[0] > cutoff]
            self._render()

    def _render(self):
        now = time.time()
        if now - self.last_render < 0.08 and self.done < self.total:
            return
        self.last_render = now
        elapsed = now - self.start
        total = max(self.total, self.done)
        pct = self.done / total if total else 0
        bw = 26
        bar = "█" * int(bw * pct) + "░" * (bw - int(bw * pct))
        avg = (sum(self.recent) / len(self.recent)) if self.recent else (elapsed / self.done if self.done else 0)
        eta = self._fmt(max(0, (total - self.done)) * avg)
        el = self._fmt(elapsed)
        if self.recent_bytes:
            bps = sum(b for _, b in self.recent_bytes) / max(1, now - self.recent_bytes[0][0])
            speed = self._sz(bps)
        else:
            speed = "-"
        # 第1行: 总进度
        sys.stdout.write(
            f"\r总进度 {bar} {pct:5.1%} | {self.done}/{total} | {speed}/s | {el} | 剩余{eta}"
        )
        # 第2行: 当前任务
        if self.cur_total:
            cp = self.cur_done / self.cur_total if self.cur_total else 0
            cbar = "█" * int(10 * cp) + "░" * (10 - int(10 * cp))
            sys.stdout.write(f"\n  当前 {cbar} {cp:3.0%} {self.current}      \033[A")
        sys.stdout.flush()

    @staticmethod
    def _fmt(s):
        s = int(s)
        if s < 60: return f"{s}s"
        m, s = divmod(s, 60)
        if m < 60: return f"{m}m{s:02d}s"
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m"

    @staticmethod
    def _sz(n):
        for u in ("B", "KB", "MB", "GB"):
            if n < 1024: return f"{n:.0f}{u}"
            n /= 1024
        return f"{n:.1f}TB"

    def finish(self):
        with self.lock:
            self.done = self.total = max(self.done, self.total)
            self._render()
        sys.stdout.write("\n\n")
        sys.stdout.flush()


PROGRESS = DualProgress()


# ===================== 克隆器 =====================
class WebsiteCloner:
    def __init__(self, start_url, out_root, max_depth=0, max_pages=1,
                 use_js=False, make_zip=True, include_external=True,
                 zip_name=None):
        self.start_url = normalize_url(start_url)
        self.base_domain = urlparse(self.start_url).netloc
        self.out_root = Path(out_root)
        self.assets_dir = self.out_root / "_assets"
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.use_js = use_js and _HAS_PLAYWRIGHT
        self.make_zip = make_zip
        self.include_external = include_external
        self.zip_name = zip_name

        self.proxy_pool = ProxyPool()
        self.session = self._build_session()
        self.visited = set()
        self.downloaded = {}
        self.queue = []
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
        retry = Retry(total=MAX_RETRIES, backoff_factor=0.4,
                      status_forcelist=(500, 502, 503, 504),
                      allowed_methods=frozenset(["GET"]))
        ad = HTTPAdapter(pool_connections=20, pool_maxsize=32, max_retries=retry)
        s.mount("http://", ad)
        s.mount("https://", ad)
        return s

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

    def fetch(self, url, stream=False):
        last = None
        for attempt in range(MAX_RETRIES + 1):
            proxy = self.proxy_pool.get()
            proxies = {"http": proxy, "https": proxy} if proxy else None
            try:
                time.sleep(REQUEST_DELAY)
                r = self.session.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                                     allow_redirects=True, stream=stream, proxies=proxies)
                if r.status_code < 400:
                    return r
                if r.status_code in (403, 429):
                    self.proxy_pool.mark_bad(proxy)
                    last = f"HTTP {r.status_code}"
                    continue
                return r
            except requests.RequestException as e:
                last = e.__class__.__name__
                self.proxy_pool.mark_bad(proxy)
                if attempt < MAX_RETRIES:
                    time.sleep(0.3 * (attempt + 1))
        return None

    def _render_js(self, url):
        try:
            if self._browser is None:
                self._pw = sync_playwright().start()
                self._browser = self._pw.chromium.launch(headless=True)
            ctx = self._browser.new_context(user_agent=USER_AGENT)
            pg = ctx.new_page()
            pg.goto(url, wait_until="networkidle", timeout=READ_TIMEOUT * 1000)
            pg.wait_for_timeout(1500)
            html = pg.content()
            final = pg.url
            pg.close()
            ctx.close()
            return html, normalize_url(final)
        except Exception as e:
            log(f"动态渲染失败: {url} ({e.__class__.__name__})", "WARN")
            return None, url

    def fetch_html(self, url):
        """获取页面HTML。SPA时用Playwright。"""
        r = self.fetch(url)
        html, final = None, url
        if r and r.status_code < 400:
            html = r.text
            final = normalize_url(r.url)
        if (html is None or (html and looks_like_spa(html))) and self.use_js:
            log(f"动态渲染: {url}")
            h2, f2 = self._render_js(url)
            if h2:
                return h2, f2
        return html, final

    def smart_fetch_html(self, url):
        """智能刷新: 多次请求合并，保证主页资源完整。返回 (html, final_url, all_resource_urls)。"""
        all_res_urls = set()
        best_html, best_final = None, url
        times = SMART_REFRESH if self.max_depth == 0 else 1
        for i in range(times):
            html, final = self.fetch_html(url)
            if not html:
                continue
            if best_html is None:
                best_html, best_final = html, final
            # 解析本次发现的资源URL
            try:
                soup = BeautifulSoup(html, "html.parser")
                for tag, attr in RESOURCE_SPECS:
                    for el in soup.find_all(tag):
                        v = el.get(attr)
                        if v:
                            a = self._resolve(final, v, None)
                            if a:
                                all_res_urls.add(a)
                for el in soup.find_all(["img", "source"]):
                    for part in (el.get("srcset") or "").split(","):
                        u = part.strip().split()[0] if part.strip() else ""
                        a = self._resolve(final, u, None) if u else None
                        if a:
                            all_res_urls.add(a)
            except Exception:
                pass
            if i < times - 1:
                time.sleep(0.2)
        return best_html, best_final, all_res_urls

    def download_asset(self, url):
        with self.lock:
            if url in self.downloaded:
                return self.downloaded[url]
        if not self.include_external and not is_same_domain(url, self.base_domain):
            with self.lock:
                self.downloaded[url] = (url, False)
            STATS.assets_fail += 1
            PROGRESS.step()
            return (url, False)

        PROGRESS.set_current(os.path.basename(urlparse(url).path)[:38] or url[:38])
        local = self.asset_path(url)
        relp = self.rel(self.out_root / "index.html", local)
        with self.lock:
            self.downloaded[url] = (relp, False)

        t0 = time.time()
        r = self.fetch(url, stream=True)
        if r is None or r.status_code >= 400:
            STATS.assets_fail += 1
            PROGRESS.step(time.time() - t0)
            return (relp, False)

        ext = ct_to_ext(r.headers.get("Content-Type", ""))
        if ext and local.suffix.lower() != ext.lower():
            local = local.with_suffix(ext)
            relp = self.rel(self.out_root / "index.html", local)
            with self.lock:
                self.downloaded[url] = (relp, False)

        local.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
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
            try: r.close()
            except Exception: pass
            return (relp, False)
        finally:
            try: r.close()
            except Exception: pass

        STATS.add_bytes(size)
        STATS.assets_ok += 1
        with self.lock:
            self.downloaded[url] = (relp, True)
        PROGRESS.step(time.time() - t0, nbytes=size)

        if local.suffix.lower() == ".css":
            self._process_css(local, url)
        return (relp, True)

    def _process_css(self, css_path, css_url):
        try:
            text = css_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return
        changed = False

        def ru(m):
            nonlocal changed
            raw = m.group(1).strip().strip("'\"")
            if not raw or raw.startswith(("data:", "#")):
                return m.group(0)
            a = urljoin(css_url, raw)
            if not a.startswith(("http://", "https://")):
                return m.group(0)
            rp, ok = self.download_asset(a)
            if not ok:
                return m.group(0)
            changed = True
            return f"url('{self.rel(css_path, self.out_root / rp)}')"

        text = CSS_URL_RE.sub(ru, text)

        def ri(m):
            nonlocal changed
            raw = m.group(1).strip()
            if not raw or raw.startswith("data:"):
                return m.group(0)
            a = urljoin(css_url, raw)
            if not a.startswith(("http://", "https://")):
                return m.group(0)
            rp, ok = self.download_asset(a)
            if not ok:
                return m.group(0)
            changed = True
            return f'@import "{self.rel(css_path, self.out_root / rp)}"'

        text = CSS_IMPORT_RE.sub(ri, text)
        if changed:
            try:
                css_path.write_text(text, encoding="utf-8")
            except Exception:
                pass

    def _resolve(self, base_url, raw, base_href=None):
        if not raw:
            return None
        raw = raw.strip()
        if raw.startswith(("data:", "javascript:", "mailto:", "tel:", "#")):
            return None
        origin = base_href or base_url
        a = urljoin(origin, raw)
        return a if a.startswith(("http://", "https://")) else None

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

    def clone_page(self, url, depth):
        url = normalize_url(url)
        with self.lock:
            if url in self.visited or STATS.pages_ok + STATS.pages_fail >= self.max_pages:
                return []
            self.visited.add(url)

        log(f"[d{depth}] 克隆: {url}")
        PROGRESS.set_current(f"页面: {safe_name(url)[:30]}", 0, 1)
        t0 = time.time()
        html, final, extra_urls = self.smart_fetch_html(url)
        if not html:
            STATS.pages_fail += 1
            PROGRESS.step(time.time() - t0)
            return []

        if final != url:
            with self.lock:
                self.visited.add(final)

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            soup = BeautifulSoup(html, "lxml")

        base_href = None
        bt = soup.find("base", href=True)
        if bt:
            base_href = urljoin(final, bt["href"])

        # 首页强制保存为 out_root/index.html —— 统一离线入口
        is_home = (normalize_url(url) == self.start_url
                   or normalize_url(final) == self.start_url)
        if is_home:
            page_file = self.out_root / "index.html"
        else:
            page_file = self.page_path(final if final != url else url)

        n_assets = self._count_assets(soup) + len(extra_urls)
        PROGRESS.add_total(n_assets)

        self._rewrite_resources(soup, final, base_href, page_file, extra_urls)
        nxt = self._rewrite_links(soup, final, base_href, page_file, depth)
        self._rewrite_js_links(soup, final, base_href, page_file)
        for b in soup.find_all("base"):
            b.decompose()

        page_file.parent.mkdir(parents=True, exist_ok=True)
        page_file.write_text(str(soup), encoding="utf-8")
        STATS.pages_ok += 1
        STATS.add_bytes(len(html.encode("utf-8")))
        PROGRESS.step(time.time() - t0)
        return nxt

    def _rewrite_resources(self, soup, page_url, base_href, page_file, extra_urls=None):
        tasks = []
        for tag, attr in RESOURCE_SPECS:
            for el in soup.find_all(tag):
                v = el.get(attr)
                if not v:
                    continue
                a = self._resolve(page_url, v, base_href)
                if a:
                    tasks.append((el, attr, a))
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
                a = self._resolve(page_url, u, base_href)
                if a:
                    tasks.append((None, None, a))
                    urls.append(a)
            srcset_els.append((el, urls))

        # 智能刷新发现的额外资源也下载
        for a in (extra_urls or []):
            tasks.append((None, None, a))

        results = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(self.download_asset, a): a for (_, _, a) in tasks}
            for fut in as_completed(futs):
                a = futs[fut]
                try:
                    results[a] = fut.result()
                except Exception:
                    results[a] = (None, False)
                    STATS.assets_fail += 1

        for el, attr, a in tasks:
            if attr is None:
                continue
            rp, ok = results.get(a, (None, False))
            if ok and rp:
                el[attr] = self.rel(page_file, self.out_root / rp)
            else:
                if el.get(attr):
                    el[attr] = a   # 回退原 URL

        for el, urls in srcset_els:
            ss = el.get("srcset", "")
            parts = []
            for part in ss.split(","):
                part = part.strip()
                if not part:
                    continue
                bits = part.split()
                u = bits[0]
                a = self._resolve(page_url, u, base_href)
                if a and a in self.downloaded:
                    rp, ok = self.downloaded[a]
                    bits[0] = self.rel(page_file, self.out_root / rp) if ok else a
                parts.append(" ".join(bits))
            el["srcset"] = ", ".join(parts)

        for st in soup.find_all("style"):
            if st.string:
                new = self._rewrite_css_text(st.string, page_url, base_href, page_file)
                if new is not None:
                    st.string.replace_with(new)
        for el in soup.find_all(style=True):
            new = self._rewrite_css_text(el["style"], page_url, base_href, page_file)
            if new is not None:
                el["style"] = new

    def _rewrite_css_text(self, text, page_url, base_href, page_file):
        changed = [False]

        def repl(m):
            raw = m.group(1).strip().strip("'\"")
            if not raw or raw.startswith(("data:", "#")):
                return m.group(0)
            a = self._resolve(page_url, raw, base_href)
            if not a:
                return m.group(0)
            rp, ok = self.download_asset(a)
            changed[0] = True
            return f"url('{self.rel(page_file, self.out_root / rp)}')" if ok else m.group(0)

        new = CSS_URL_RE.sub(repl, text)
        return new if changed[0] else None

    def _rewrite_links(self, soup, page_url, base_href, page_file, depth):
        nxt = []
        for a in soup.find_all("a", href=True):
            raw = a["href"].strip()
            if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                continue
            absu = self._resolve(page_url, raw, base_href)
            if not absu:
                continue
            if not is_same_domain(absu, self.base_domain):
                a["href"] = absu
                a["target"] = "_blank"
                continue
            if not is_page(absu):
                rp, ok = self.download_asset(absu)
                a["href"] = self.rel(page_file, self.out_root / rp) if ok else absu
                continue
            if self.max_depth == 0:
                a["href"] = normalize_url(absu)
                a["target"] = "_blank"
                continue
            norm = normalize_url(absu)
            a["href"] = self.rel(page_file, self.page_path(norm))
            if depth < self.max_depth and norm not in self.visited:
                nxt.append((norm, depth + 1))
        return nxt

    def _rewrite_js_links(self, soup, page_url, base_href, page_file):
        """本地化 JS 中的页面跳转: location.href='xxx' / window.location='xxx' 等。
        完全模式下把同域页面跳转改为本地 .html 路径，保证离线可点击。"""
        if self.max_depth == 0:
            return  # 极简模式不动 JS，保留原站跳转
        # 匹配 location.href='...' / location.href="..." / location='...'
        js_re = re.compile(
            r"(location(?:\.href)?\s*=\s*)(['\"])([^'\"]+)(\2)", re.IGNORECASE)
        for script in soup.find_all("script"):
            if not script.string:
                continue
            text = script.string
            changed = False

            def repl(m):
                nonlocal changed
                prefix, quote, raw, _ = m.group(1), m.group(2), m.group(3), m.group(4)
                if raw.startswith(("http://", "https://", "#", "javascript:", "mailto:", "tel:", "data:")):
                    return m.group(0)
                absu = self._resolve(page_url, raw, base_href)
                if not absu or not is_same_domain(absu, self.base_domain):
                    return m.group(0)
                if not is_page(absu):
                    return m.group(0)
                norm = normalize_url(absu)
                local_target = self.page_path(norm)
                new_path = self.rel(page_file, local_target)
                changed = True
                return f"{prefix}{quote}{new_path}{quote}"

            new_text = js_re.sub(repl, text)
            if changed:
                script.string.replace_with(new_text)

        # 行内 onclick="location.href='xxx'" 等
        for el in soup.find_all(onclick=True):
            text = el["onclick"]
            changed = False

            def repl2(m):
                nonlocal changed
                prefix, quote, raw, _ = m.group(1), m.group(2), m.group(3), m.group(4)
                if raw.startswith(("http://", "https://", "#", "javascript:", "mailto:", "tel:", "data:")):
                    return m.group(0)
                absu = self._resolve(page_url, raw, base_href)
                if not absu or not is_same_domain(absu, self.base_domain):
                    return m.group(0)
                if not is_page(absu):
                    return m.group(0)
                norm = normalize_url(absu)
                local_target = self.page_path(norm)
                new_path = self.rel(page_file, local_target)
                changed = True
                return f"{prefix}{quote}{new_path}{quote}"

            new_text = js_re.sub(repl2, text)
            if changed:
                el["onclick"] = new_text

    def close_js(self):
        try:
            if self._browser: self._browser.close()
            if self._pw: self._pw.stop()
        except Exception:
            pass

    def run(self):
        log(f"目标: {self.start_url}")
        log(f"深度: {self.max_depth} | 页面上限: {self.max_pages} | 外域资源: {'下载' if self.include_external else '跳过'} | 动态渲染: {'开' if self.use_js else '关'}")
        log(f"输出: {self.out_root.resolve()}")
        print("-" * 60)

        self.out_root.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)

        PROGRESS.set_total(1)
        self.queue.append((self.start_url, 0))
        while self.queue and STATS.pages_ok + STATS.pages_fail < self.max_pages:
            url, depth = self.queue.pop(0)
            if url in self.visited:
                continue
            self.queue.extend(self.clone_page(url, depth))

        self.close_js()
        PROGRESS.set_total(STATS.total_done)
        PROGRESS.finish()

        # 首页已在 clone_page 中直接保存为 out_root/index.html
        index_dst = self.out_root / "index.html"

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
        log("正在打包压缩包 ...")
        try:
            base = str(self.out_root)
            if self.zip_name:
                # 自定义压缩包名
                dst = Path(base).parent / self.zip_name
                shutil.make_archive(str(dst.with_suffix("")), "zip", root_dir=base)
                zp = dst.with_suffix(".zip")
            else:
                zp = Path(shutil.make_archive(base, "zip", root_dir=base))
            size = zp.stat().st_size / 1024 / 1024
            log(f"压缩包: {zp.resolve()} ({size:.2f} MB)")
            return zp
        except Exception as e:
            log(f"打包失败: {e}", "WARN")
            return None


# ===================== 交互工具 =====================
def _ask(prompt, default=None):
    try:
        s = input(prompt).strip().lstrip("\ufeff").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not s and default is not None:
        return default
    return s


def _choose(prompt, options, default_idx=0):
    print(f"\n{prompt}")
    for i, (_, desc) in enumerate(options, 1):
        mark = " (默认)" if i - 1 == default_idx else ""
        print(f"  {i:2}. {desc}{mark}")
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
    print("       网站克隆器 v4  Website Cloner")
    print("=" * 60)
    print(f"动态渲染(Playwright): {'可用' if _HAS_PLAYWRIGHT else '未安装'}")
    print("-" * 60)

    # 1. 选择模式
    mode_opts = [(i, f"模式 {i}: " + desc) for i, (_, _, desc) in enumerate(MODES)]
    mode_opts.append((len(MODES), "自定义模式"))
    mi = _choose("选择下载模式：", mode_opts, default_idx=0)

    if mi < len(MODES):
        depth, max_pages, _ = MODES[mi]
    else:
        # 自定义
        d = _ask("输入递归深度(0=仅首页, 数字): ", "0")
        depth = int(d) if d and d.isdigit() else 0
        mp = _ask("输入最大页面数(1-1000): ", "50")
        max_pages = int(mp) if mp and mp.isdigit() else 50

    include_ext = _choose(
        "是否下载外域资源(如CDN的JS/CSS/图片)：",
        [(True, "下载(完整复刻,外域可能慢/失败)"),
         (False, "不下载(仅同域,速度快;外域跳转原站)")],
        default_idx=0,
    )
    use_js = _choose(
        "是否启用动态渲染(B站/知乎等SPA需要)：",
        [(True, "启用(检测SPA时用Playwright)"),
         (False, "不启用(静态站点更快)")],
        default_idx=0,
    ) and _HAS_PLAYWRIGHT

    # 2. 输入网址
    url = _ask("\n目标网址> ")
    if not url or url.lower() in ("q", "quit", "exit"):
        print("已退出。")
        return
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    domain = safe_name(urlparse(url).netloc or "site")

    # 3. 询问文件夹名/压缩包名
    ts = time.strftime("%Y%m%d_%H%M%S")
    default_folder = f"cloned_{domain}_{ts}"
    default_zip = f"cloned_{domain}_{ts}.zip"
    folder = _ask(f"\n文件夹名(回车=默认 {default_folder}): ", default_folder)
    zip_name = _ask(f"压缩包名(回车=默认 {default_zip}): ", default_zip)

    out_root = Path(os.getcwd()) / folder

    print()
    try:
        cloner = WebsiteCloner(url, out_root, max_depth=depth, max_pages=max_pages,
                               use_js=use_js, make_zip=True,
                               include_external=include_ext, zip_name=zip_name)
        entry, zp = cloner.run()
        print()
        print(f"  入口文件: {entry.resolve()}")
        if zp:
            print(f"  压缩包:   {zp.resolve()}")
        print(f"  目录:     {out_root.resolve()}")
    except KeyboardInterrupt:
        print("\n已中断，已下载内容保留在:", out_root)
    except Exception as e:
        log(f"发生错误: {e}", "ERROR")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
