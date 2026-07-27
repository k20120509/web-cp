# -*- coding: utf-8 -*-
"""
网站克隆器 V13.0 - Website Cloner V13.0
输入目标网址，克隆为本地静态站点。

V13.0 特性:
  * 极简UI: 只有简单打包(选项1)和全面打包(选项2)
  * 资源路径精确重写: 确保所有资源正确引用
  * 动态加载增强: 模拟用户滚动5次+等待网络空闲
  * 裂变爬取: 深度999，页面上限9999，自动遍历所有链接
  * 完成弹窗: 显示爬取效率和内容占比
  * 高并发: 64线程，请求间隔0.005s，连接超时4s，读取超时12s
"""

import os
import sys
import re
import time
import shutil
import subprocess
import hashlib
import threading
import traceback
from urllib.parse import urljoin, urlparse, urlunparse, unquote
from pathlib import Path, PurePosixPath
from concurrent.futures import ThreadPoolExecutor, as_completed

VERSION = "V13.0"
MAX_RETRIES = 10
RETRY_BACKOFF = 0.3
CONNECT_TIMEOUT = 4
READ_TIMEOUT = 12
REQUEST_DELAY = 0.005
MAX_WORKERS = 64

def _msg_box(message, title="提示", style=0x40):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(message), str(title), style)
    except Exception:
        print(f"\n[{title}] {message}\n")

def _have(mod):
    try:
        __import__(mod)
        return True
    except Exception:
        return False

def ensure_deps():
    frozen = getattr(sys, "frozen", False)
    needed = []
    if not frozen:
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
            from playwright.sync_api import sync_playwright
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

LARGE_FILE_THRESHOLD = 8 * 1024 * 1024
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

class DualProgress:
    def __init__(self):
        self.lock = threading.Lock()
        self.done = 0
        self.total = 0
        self.start = time.time()
        self.recent = []
        self.recent_bytes = []
        self.last_render = 0
        self.current = ""

    def set_total(self, n):
        with self.lock:
            self.total = max(self.total, n)

    def add_total(self, n):
        with self.lock:
            self.total += n

    def set_current(self, desc):
        with self.lock:
            self.current = desc[:38]

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
        sys.stdout.write(
            f"\r总进度 {bar} {pct:5.1%} | {self.done}/{total} | {speed}/s | {el} | 剩余{eta} | {self.current}"
        )
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

class WebsiteCloner:
    def __init__(self, start_url, out_root, max_depth=999, max_pages=9999, use_js=True, include_external=True):
        self.start_url = normalize_url(start_url)
        self.base_domain = urlparse(self.start_url).netloc
        self.out_root = Path(out_root).resolve()
        self.assets_dir = self.out_root / "_assets"
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.use_js = use_js and _HAS_PLAYWRIGHT
        self.include_external = include_external

        self.session = self._build_session()
        self.visited = set()
        self.downloaded = {}
        self.queue = []
        self.lock = threading.Lock()
        self._pw = None
        self._browser = None
        self.failed_urls = set()
        self._pending_css = []

    def _build_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })
        retry = Retry(total=MAX_RETRIES, backoff_factor=0.4,
                      status_forcelist=(500, 502, 503, 504),
                      allowed_methods=frozenset(["GET"]))
        ad = HTTPAdapter(pool_connections=32, pool_maxsize=64, max_retries=retry)
        s.mount("http://", ad)
        s.mount("https://", ad)
        return s

    def _get_asset_filename(self, url):
        p = urlparse(url)
        path = unquote(p.path).lstrip("/")
        if not path:
            path = "index"
        # 去除图片处理参数，如 xxx.png@480w_300h_1c -> xxx.png
        if "@" in path:
            path = path.split("@")[0]
        filename = hashlib.md5(url.encode()).hexdigest()[:16]
        ext = PurePosixPath(path).suffix.lower()
        if not ext:
            ext = ".bin"
        return f"{filename}{ext}"

    def asset_path(self, url):
        p = urlparse(url)
        netloc = p.netloc or self.base_domain
        filename = self._get_asset_filename(url)
        return self.assets_dir / netloc / filename

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

    def rel_path(self, from_file, to_file):
        from_file = Path(from_file).resolve()
        to_file = Path(to_file).resolve()
        rel = os.path.relpath(to_file, start=from_file.parent)
        return rel.replace("\\", "/")

    def fetch(self, url, stream=False):
        last = None
        max_tries = MAX_RETRIES
        for attempt in range(max_tries):
            try:
                time.sleep(REQUEST_DELAY)
                r = self.session.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                                     allow_redirects=True, stream=stream)
                if r.status_code < 400:
                    return r
                if r.status_code in (403, 429):
                    last = f"HTTP {r.status_code}"
                    if attempt in (0, 2, 4, 8):
                        log(f"  被限流({last}) 第{attempt+1}/{max_tries}次: {safe_name(url)[:40]}", "WARN")
                    time.sleep(RETRY_BACKOFF * (2 ** attempt))
                    continue
                return r
            except requests.exceptions.Timeout:
                last = "超时"
                remain = max_tries - attempt - 1
                if attempt in (0, 2, 4, 8):
                    log(f"  下载超时 第{attempt+1}/{max_tries}次，剩余{remain}次: {safe_name(url)[:40]}", "WARN")
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
            except requests.RequestException as e:
                last = e.__class__.__name__
                remain = max_tries - attempt - 1
                if attempt in (0, 2, 4, 8):
                    log(f"  请求失败({last}) 第{attempt+1}/{max_tries}次，剩余{remain}次: {safe_name(url)[:40]}", "WARN")
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
        if last:
            if url not in getattr(self, 'failed_urls', set()):
                self.failed_urls.add(url)
            log(f"  下载失败({last}) {max_tries}次重试用尽: {safe_name(url)[:40]}", "ERROR")
        return None

    def _render_js(self, url):
        try:
            if self._browser is None:
                self._pw = sync_playwright().start()
                self._browser = self._pw.chromium.launch(headless=True)
            ctx = self._browser.new_context(user_agent=USER_AGENT, viewport={"width": 1920, "height": 1080})
            pg = ctx.new_page()
            pg.goto(url, wait_until="domcontentloaded", timeout=READ_TIMEOUT * 1000)

            for _ in range(5):
                pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                pg.wait_for_timeout(800)

            pg.wait_for_load_state("networkidle", timeout=READ_TIMEOUT * 1000)
            pg.wait_for_timeout(1000)

            html = pg.content()
            final = pg.url
            pg.close()
            ctx.close()
            return html, normalize_url(final)
        except Exception as e:
            log(f"动态渲染失败: {url} ({e.__class__.__name__})", "WARN")
            return None, url

    def fetch_html(self, url):
        r = self.fetch(url)
        html, final = None, url
        if r and r.status_code < 400:
            html = r.text
            final = normalize_url(r.url)
        if (html is None or len(html.strip()) < 500) and self.use_js:
            log(f"动态渲染: {url}")
            h2, f2 = self._render_js(url)
            if h2:
                return h2, f2
        return html, final

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
        relp = self.rel_path(self.out_root / "index.html", local)

        with self.lock:
            self.downloaded[url] = (relp, False)

        t0 = time.time()
        r = self.fetch(url, stream=True)
        if r is None:
            log(f"  资源下载失败: {url}", "ERROR")
            STATS.assets_fail += 1
            PROGRESS.step(time.time() - t0)
            return (relp, False)
        if r.status_code >= 400:
            log(f"  资源下载失败(HTTP {r.status_code}): {url}", "ERROR")
            STATS.assets_fail += 1
            PROGRESS.step(time.time() - t0)
            try:
                r.close()
            except Exception:
                pass
            return (relp, False)

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
            try:
                r.close()
            except Exception:
                pass
            return (relp, False)
        finally:
            try:
                r.close()
            except Exception:
                pass

        STATS.add_bytes(size)
        STATS.assets_ok += 1
        with self.lock:
            self.downloaded[url] = (relp, True)
        PROGRESS.step(time.time() - t0, nbytes=size)

        # CSS处理延迟到线程池外执行，避免死锁
        if local.suffix.lower() == ".css":
            with self.lock:
                self._pending_css.append((local, url))
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
            return f"url('{self.rel_path(css_path, self.out_root / rp)}')"

        text = CSS_URL_RE.sub(ru, text)
        text = CSS_IMPORT_RE.sub(ru, text)

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

        log(f"[d{depth}] 克隆页面: {url}")
        PROGRESS.set_current(f"页面: {safe_name(url)[:30]}")
        t0 = time.time()
        html, final = self.fetch_html(url)
        if not html:
            log(f"  页面获取失败: {url}", "ERROR")
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

        is_home = (normalize_url(url) == self.start_url
                   or normalize_url(final) == self.start_url)
        if is_home:
            page_file = self.out_root / "index.html"
        else:
            page_file = self.page_path(final if final != url else url)

        n_assets = self._count_assets(soup)
        PROGRESS.add_total(n_assets)

        self._rewrite_resources(soup, final, base_href, page_file)
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

    # 不需要下载的 link rel 类型（保留原URL）
    SKIP_LINK_RELS = {"dns-prefetch", "preconnect", "canonical", "alternate",
                      "prev", "next", "pingback", "wlwmanifest", "EditURI",
                      "shortlink", "amphtml", "manifest", "privacy-policy"}

    def _rewrite_resources(self, soup, page_url, base_href, page_file):
        tasks = []
        for tag, attr in RESOURCE_SPECS:
            for el in soup.find_all(tag):
                v = el.get(attr)
                if not v:
                    continue
                # 跳过不需要下载的 link rel 类型
                if tag == "link" and el.get("rel"):
                    rel_val = " ".join(el.get("rel", [])).lower()
                    if any(r in rel_val for r in self.SKIP_LINK_RELS):
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
                el[attr] = self.rel_path(page_file, self.out_root / rp)
            else:
                if el.get(attr):
                    el[attr] = a

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
                    bits[0] = self.rel_path(page_file, self.out_root / rp) if ok else a
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
            return f"url('{self.rel_path(page_file, self.out_root / rp)}')" if ok else m.group(0)

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
                a["href"] = self.rel_path(page_file, self.out_root / rp) if ok else absu
                continue
            if self.max_depth == 0:
                a["href"] = normalize_url(absu)
                a["target"] = "_blank"
                continue
            norm = normalize_url(absu)
            a["href"] = self.rel_path(page_file, self.page_path(norm))
            if depth < self.max_depth and norm not in self.visited:
                nxt.append((norm, depth + 1))
        return nxt

    def _rewrite_js_links(self, soup, page_url, base_href, page_file):
        if self.max_depth == 0:
            return
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
                new_path = self.rel_path(page_file, local_target)
                changed = True
                return f"{prefix}{quote}{new_path}{quote}"

            new_text = js_re.sub(repl, text)
            if changed:
                script.string.replace_with(new_text)

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
                new_path = self.rel_path(page_file, local_target)
                changed = True
                return f"{prefix}{quote}{new_path}{quote}"

            new_text = js_re.sub(repl2, text)
            if changed:
                el["onclick"] = new_text

    def close_js(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    def run(self):
        log(f"目标: {self.start_url}")
        log(f"深度: {self.max_depth} | 页面上限: {self.max_pages} | 外域资源: {'下载' if self.include_external else '跳过'} | 动态渲染: {'开' if self.use_js else '关'}")
        log(f"输出: {self.out_root}")
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

        if self.failed_urls:
            log("-" * 60)
            log(f"🔄 正在回刷 {len(self.failed_urls)} 个失败链接 ...")
            success_count = 0
            fail_count = 0
            PROGRESS.set_total(len(self.failed_urls))
            PROGRESS.start = time.time()
            PROGRESS.done = 0
            for url in list(self.failed_urls):
                PROGRESS.set_current(f"回刷: {safe_name(url)[:30]}")
                r = self.fetch(url, stream=True)
                PROGRESS.step()
                if r is not None and r.status_code < 400:
                    try:
                        relp = self.asset_path(url)
                        local = self.out_root / relp
                        local.parent.mkdir(parents=True, exist_ok=True)
                        with open(local, "wb") as f:
                            for chunk in r.iter_content(8192):
                                if chunk:
                                    f.write(chunk)
                        STATS.assets_ok += 1
                        STATS.assets_fail -= 1
                        STATS.add_bytes(local.stat().st_size)
                        success_count += 1
                        self.failed_urls.discard(url)
                        log(f"  ✅ 回刷成功: {safe_name(url)[:40]}", "INFO")
                    except Exception:
                        fail_count += 1
                else:
                    fail_count += 1
                    if r:
                        try:
                            r.close()
                        except Exception:
                            pass
            PROGRESS.finish()
            if success_count > 0:
                log(f"回刷完成: 成功 {success_count} / 失败 {fail_count}")

        # 处理延迟的CSS文件资源引用（避免线程池死锁）
        if self._pending_css:
            log("-" * 60)
            log(f"🎨 正在处理 {len(self._pending_css)} 个CSS文件中的资源引用 ...")
            with self.lock:
                pending = self._pending_css.copy()
                self._pending_css = []
            PROGRESS.set_total(len(pending))
            PROGRESS.start = time.time()
            PROGRESS.done = 0
            for css_path, css_url in pending:
                PROGRESS.set_current(f"CSS: {css_path.name[:30]}")
                try:
                    self._process_css(css_path, css_url)
                except Exception as e:
                    log(f"  CSS处理失败: {css_path.name} ({e})", "WARN")
                PROGRESS.step()
            PROGRESS.finish()

        index_dst = self.out_root / "index.html"

        log("-" * 60)
        log("克隆完成!")
        log(f"  页面: 成功 {STATS.pages_ok} / 失败 {STATS.pages_fail}")
        log(f"  资源: 成功 {STATS.assets_ok} / 失败 {STATS.assets_fail}")
        log(f"  总大小: {STATS.bytes/1024/1024:.2f} MB")

        return index_dst

def reveal_in_explorer(file_path):
    p = Path(file_path).resolve()
    if not p.exists():
        log(f"  入口文件不存在: {p}", "WARN")
        return False
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", str(p)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent)])
        log(f"  已在资源管理器中定位: {p.name}")
        return True
    except Exception as e:
        log(f"  打开资源管理器失败: {e}", "WARN")
        return False

def show_result_popup():
    total_pages = STATS.pages_ok + STATS.pages_fail
    total_assets = STATS.assets_ok + STATS.assets_fail
    total_items = total_pages + total_assets
    success_items = STATS.pages_ok + STATS.assets_ok

    page_rate = (STATS.pages_ok / total_pages * 100) if total_pages else 0
    asset_rate = (STATS.assets_ok / total_assets * 100) if total_assets else 0
    overall_rate = (success_items / total_items * 100) if total_items else 0

    packet_loss = ((total_items - success_items) / total_items * 100) if total_items else 0
    content_ratio = (success_items / total_items * 100) if total_items else 0

    msg = (
        f"📊 爬取效率统计\n\n"
        f"页面成功率: {STATS.pages_ok}/{total_pages} ({page_rate:.1f}%)\n"
        f"资源成功率: {STATS.assets_ok}/{total_assets} ({asset_rate:.1f}%)\n"
        f"总体效率: {overall_rate:.1f}%\n\n"
        f"📦 内容占比\n"
        f"已获取: {content_ratio:.1f}%\n"
        f"丢包率: {packet_loss:.1f}%\n"
        f"总大小: {STATS.bytes/1024/1024:.2f} MB\n\n"
        f"选择[是]继续爬取，[否]关闭(3秒后自动退出)"
    )

    import ctypes
    result = ctypes.windll.user32.MessageBoxW(0, msg, f"网站克隆器 {VERSION} - 爬取完成", 0x04 | 0x40)
    if result == 6:
        return True
    else:
        log("3秒后自动退出...")
        time.sleep(3)
        return False

def main():
    print("=" * 60)
    print(f"       网站克隆器 {VERSION}  Website Cloner")
    print("=" * 60)

    while True:
        print("\n请选择模式:")
        print("  1. 简单打包 - 可选择爬取深度(1-12)")
        print("  2. 全面打包 - 全自动裂变爬取(深度999,页面9999)")
        choice = input("\n选择 [1/2]: ").strip()

        if choice == "1":
            while True:
                depth_input = input("请输入爬取深度 (1-12): ").strip()
                if depth_input.isdigit():
                    depth = int(depth_input)
                    if 1 <= depth <= 12:
                        max_pages = depth * 100
                        break
                print("请输入1-12之间的数字")
            break
        elif choice == "2":
            depth = 999
            max_pages = 9999
            break
        else:
            print("请输入1或2")

    url = input("\n目标网址: ").strip()
    if not url:
        print("网址不能为空")
        return
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    domain = safe_name(urlparse(url).netloc or "site")
    ts = time.strftime("%Y%m%d_%H%M%S")
    folder = f"cloned_{domain}_{ts}"
    out_root = Path(os.getcwd()) / folder

    print(f"\n开始克隆: {url}")
    print(f"深度: {depth}, 页面上限: {max_pages}")
    print(f"输出目录: {out_root}")
    print("-" * 60)

    cloner = WebsiteCloner(url, out_root, max_depth=depth, max_pages=max_pages,
                           use_js=True, include_external=True)
    entry = cloner.run()

    total_pages = STATS.pages_ok + STATS.pages_fail
    total_assets = STATS.assets_ok + STATS.assets_fail
    page_rate = (STATS.pages_ok / total_pages * 100) if total_pages else 0
    asset_rate = (STATS.assets_ok / total_assets * 100) if total_assets else 0
    overall_rate = ((STATS.pages_ok + STATS.assets_ok) / (total_pages + total_assets) * 100) if (total_pages + total_assets) else 0

    print("\n" + "=" * 60)
    print("全部完成!")
    print(f"  ★ 入口 HTML: {entry.resolve()}")
    print(f"  目录: {out_root.resolve()}")
    print("-" * 40)
    print("📊 成功率统计:")
    print(f"  页面: {STATS.pages_ok}/{total_pages} ({page_rate:.1f}%)")
    print(f"  资源: {STATS.assets_ok}/{total_assets} ({asset_rate:.1f}%)")
    print(f"  总体: {overall_rate:.1f}%")
    print(f"  总大小: {STATS.bytes/1024/1024:.2f} MB")
    print("=" * 60)

    reveal_in_explorer(entry)

    while show_result_popup():
        STATS.pages_ok = 0
        STATS.pages_fail = 0
        STATS.assets_ok = 0
        STATS.assets_fail = 0
        STATS.bytes = 0

        url = input("\n新目标网址: ").strip()
        if not url:
            print("网址不能为空")
            continue
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        domain = safe_name(urlparse(url).netloc or "site")
        ts = time.strftime("%Y%m%d_%H%M%S")
        folder = f"cloned_{domain}_{ts}"
        out_root = Path(os.getcwd()) / folder

        print(f"\n开始克隆: {url}")
        print(f"深度: {depth}, 页面上限: {max_pages}")
        print(f"输出目录: {out_root}")
        print("-" * 60)

        cloner = WebsiteCloner(url, out_root, max_depth=depth, max_pages=max_pages,
                               use_js=True, include_external=True)
        entry = cloner.run()

        total_pages = STATS.pages_ok + STATS.pages_fail
        total_assets = STATS.assets_ok + STATS.assets_fail
        page_rate = (STATS.pages_ok / total_pages * 100) if total_pages else 0
        asset_rate = (STATS.assets_ok / total_assets * 100) if total_assets else 0
        overall_rate = ((STATS.pages_ok + STATS.assets_ok) / (total_pages + total_assets) * 100) if (total_pages + total_assets) else 0

        print("\n" + "=" * 60)
        print("全部完成!")
        print(f"  ★ 入口 HTML: {entry.resolve()}")
        print(f"  目录: {out_root.resolve()}")
        print("-" * 40)
        print("📊 成功率统计:")
        print(f"  页面: {STATS.pages_ok}/{total_pages} ({page_rate:.1f}%)")
        print(f"  资源: {STATS.assets_ok}/{total_assets} ({asset_rate:.1f}%)")
        print(f"  总体: {overall_rate:.1f}%")
        print(f"  总大小: {STATS.bytes/1024/1024:.2f} MB")
        print("=" * 60)

        reveal_in_explorer(entry)

if __name__ == "__main__":
    main()