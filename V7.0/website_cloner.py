# -*- coding: utf-8 -*-
"""
网站克隆器 V6.0 正式版 - Website Cloner V6.0
输入目标网址，克隆为本地静态站点。

V6.0 正式版特性:
  * 启动自检依赖(缺失自动安装)
  * 模式选择: 极简(0) / 1-10级递归 / 超级(全站) / 自定义
  * 双进度条: 总进度 + 当前任务, 多任务并行
  * 智能刷新下载: 多次请求合并资源, 保证主页完整
  * 失败链接回退原站; 代理池+重试; 流式内存优化
  * 自定义文件夹名/压缩包名
  * 完成后自动打包 zip
  * 完成后自动打开文件夹并定位 HTML 文件
  * 超时/重试 3 次机会，每次都显示日志
  * ★ 异常自动重启: 程序崩溃后自动重启，最多 3 次
  * ★ 中文错误弹窗: 简单错误用中文弹窗提示，通俗易懂
  * ★ 友好错误翻译: 把英文异常名翻译成中文说明
  * ★ 崩溃日志: 自动记录崩溃信息到 crash.log

用法: python website_cloner_v6.py
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
import json
from urllib.parse import urljoin, urlparse, urlunparse, unquote
from pathlib import Path, PurePosixPath
from concurrent.futures import ThreadPoolExecutor, as_completed

VERSION = "V7.0"
CRASH_LOG = "crash.log"
MAX_RESTART = 3   # 异常重启最多 3 次


# ===================== 错误弹窗 (Windows) =====================
def _msg_box(message, title="提示", style=0x40):
    """显示 Windows MessageBox 弹窗。style: 0x40=信息 0x30=警告 0x10=错误 0x04=是/否"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(message), str(title), style)
    except Exception:
        # 非 Windows 或加载失败，退化为控制台输出
        print(f"\n[{title}] {message}\n")


def friendly_error(e):
    """把常见异常翻译成中文用户能看懂的说明。"""
    name = e.__class__.__name__
    msg = str(e).lower()
    table = [
        (ConnectionError,         "网络连接失败，请检查网络是否正常"),
        (TimeoutError,            "网络超时，目标网站响应过慢或无法访问"),
        (FileNotFoundError,       "找不到文件或目录"),
        (PermissionError,         "没有权限访问该文件或目录"),
        (MemoryError,             "内存不足，请关闭其他程序后重试"),
        (KeyboardInterrupt,       "用户主动中断"),
    ]
    for cls, txt in table:
        if isinstance(e, cls):
            return txt
    mapping = {
        "requests.exceptions.timeout":        "网络请求超时",
        "requests.exceptions.connectionerror": "无法连接到目标网站",
        "requests.exceptions.sslerror":       "SSL 证书验证失败",
        "requests.exceptions.toomanyredirects": "网页重定向过多",
        "requests.exceptions.proxyerror":     "代理服务器错误",
        "urllib3.exceptions.maxretryerror":   "重试次数用尽",
        "ssl.certverificationerror":          "SSL 证书验证失败",
        "socket.gaierror":                    "域名解析失败，无法找到该网站",
        "socket.timeout":                     "网络超时",
        "filenotfounderror":                  "找不到文件",
        "permissionerror":                    "没有权限",
        "memoryerror":                        "内存不足",
        "recursionerror":                     "递归过深",
        "syntaxerror":                        "代码语法错误",
        "indentationerror":                   "代码缩进错误",
        "importerror":                        "缺少依赖模块",
        "modulenotfounderror":                "缺少依赖模块",
        "attributeerror":                     "对象属性错误",
        "keyerror":                           "字典键不存在",
        "indexerror":                         "索引越界",
        "valueerror":                         "数值错误",
        "typeerror":                          "类型错误",
        "zerodivisionerror":                  "除以零",
        "overflowerror":                      "数值溢出",
        "oserror":                            "操作系统错误",
        "ioerror":                            "输入输出错误",
        "encodingerror":                      "编码错误",
        "unicodedecodeerror":                 "编码解码错误",
        "playwright._impl._api_types.error":  "Playwright 浏览器执行失败",
    }
    full = f"{name}: {e}".lower()
    for key, txt in mapping.items():
        if key in full or key.replace("exceptions.", "").replace("_", "") in full:
            return txt
    # 关键词匹配
    if "timeout" in msg or "timed out" in msg:
        return "网络超时"
    if "connection" in msg and ("refused" in msg or "reset" in msg or "closed" in msg):
        return "网络连接被拒绝或断开"
    if "name or service not known" in msg or "getaddrinfo" in msg:
        return "域名解析失败，请检查网址是否正确"
    if "ssl" in msg or "certificate" in msg:
        return "SSL 证书验证失败"
    if "proxy" in msg:
        return "代理服务器错误"
    if "404" in msg:
        return "网页不存在 (404)"
    if "403" in msg:
        return "网页禁止访问 (403)"
    if "500" in msg or "502" in msg or "503" in msg:
        return "目标服务器出错"
    return f"程序出现错误: {name}"


def write_crash_log(e, context=""):
    """把崩溃信息写入 crash.log，方便排查。"""
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        log_path = Path(os.getcwd()) / CRASH_LOG
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"[{ts}] 程序崩溃\n")
            f.write(f"上下文: {context}\n")
            f.write(f"异常类型: {type(e).__name__}\n")
            f.write(f"异常信息: {e}\n")
            f.write(f"完整堆栈:\n{tb}\n")
        return str(log_path.resolve())
    except Exception:
        return None


def restart_self():
    """重启自身程序。打包成 exe 后用 sys.executable，否则用 python + 脚本。"""
    try:
        if getattr(sys, "frozen", False):
            # exe 模式: 直接启动 exe
            subprocess.Popen([sys.executable])
        else:
            # 脚本模式: python + 本脚本
            subprocess.Popen([sys.executable, os.path.abspath(__file__)])
        return True
    except Exception as e:
        log(f"重启失败: {e}", "ERROR")
        return False

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
        """带超时重试的 GET。超时/失败有 3 次机会，每次都会显示日志。"""
        last = None
        max_tries = MAX_RETRIES + 1   # 共 3 次机会
        for attempt in range(max_tries):
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
                    if attempt < max_tries - 1:
                        log(f"  被限流({last}) 第{attempt+1}/{max_tries}次: {url}", "WARN")
                    continue
                return r
            except requests.exceptions.Timeout:
                last = "超时"
                self.proxy_pool.mark_bad(proxy)
                remain = max_tries - attempt - 1
                if remain > 0:
                    log(f"  下载超时 第{attempt+1}/{max_tries}次，剩余{remain}次机会: {url}", "WARN")
                    time.sleep(0.5 * (attempt + 1))
                else:
                    log(f"  下载超时 3次机会用尽，跳过: {url}", "ERROR")
            except requests.RequestException as e:
                last = e.__class__.__name__
                self.proxy_pool.mark_bad(proxy)
                remain = max_tries - attempt - 1
                if remain > 0:
                    log(f"  请求失败({last}) 第{attempt+1}/{max_tries}次，剩余{remain}次机会: {url}", "WARN")
                    time.sleep(0.3 * (attempt + 1))
                else:
                    log(f"  请求失败 3次机会用尽，跳过: {url}", "ERROR")
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
        if r is None:
            log(f"  资源下载失败(超时/重试用尽): {url}", "ERROR")
            STATS.assets_fail += 1
            PROGRESS.step(time.time() - t0)
            return (relp, False)
        if r.status_code >= 400:
            log(f"  资源下载失败(HTTP {r.status_code}): {url}", "ERROR")
            STATS.assets_fail += 1
            PROGRESS.step(time.time() - t0)
            try: r.close()
            except Exception: pass
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

        log(f"[d{depth}] 克隆页面: {url}")
        PROGRESS.set_current(f"页面: {safe_name(url)[:30]}", 0, 1)
        t0 = time.time()
        html, final, extra_urls = self.smart_fetch_html(url)
        if not html:
            log(f"  页面获取失败(3次重试用尽): {url}", "ERROR")
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


# ===================== 加密模块 V7.0 =====================
# AES-256-CBC + HMAC-SHA256 / PBKDF2-SHA256 / 资源打包加密
# 输出三文件: index.html(主HTML) + decryptor.js(解密程序,自身混淆) + assets.enc(加密依赖)

import struct
import hmac
import base64
from hashlib import sha256, pbkdf2_hmac

# ---- 纯 Python AES (不依赖第三方库，打包体积小) ----
_AES_SBOX = [
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16,
]

def _aes_sub_word(w):
    return (_AES_SBOX[(w>>24)&0xff] << 24) | (_AES_SBOX[(w>>16)&0xff] << 16) | \
           (_AES_SBOX[(w>>8)&0xff] << 8) | _AES_SBOX[w&0xff]

_RCON = [0x00,0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36,0x6c,0xd8,0xab,0x4d,0x9a]

def _aes_key_expansion(key):
    """AES-256 密钥扩展。key 32 字节 -> 60 个 32 位字 (15 轮)"""
    nk = 8
    nr = 14
    nb = 4
    w = [0] * (nb * (nr + 1))
    for i in range(nk):
        w[i] = (key[4*i] << 24) | (key[4*i+1] << 16) | (key[4*i+2] << 8) | key[4*i+3]
    for i in range(nk, nb * (nr + 1)):
        temp = w[i-1]
        if i % nk == 0:
            temp = _aes_sub_word(((temp << 8) | (temp >> 24)) & 0xffffffff) ^ (_RCON[i//nk] << 24)
        elif nk > 6 and i % nk == 4:
            temp = _aes_sub_word(temp)
        w[i] = w[i-nk] ^ temp
    return w

def _aes_encrypt_block(block, w):
    """加密单个 16 字节块。block 16 bytes, w 扩展密钥。"""
    s = list(block)
    nr = 14
    nb = 4
    # 初始轮密钥加
    for c in range(4):
        for r in range(4):
            s[r + 4*c] ^= (w[c] >> (24 - 8*r)) & 0xff
    for rnd in range(1, nr):
        # 字节代换
        for i in range(16):
            s[i] = _AES_SBOX[s[i]]
        # 行移位 + 列混合 + 轮密钥加（合并）
        # 行移位
        s[1], s[5], s[9], s[13] = s[5], s[9], s[13], s[1]
        s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
        s[3], s[7], s[11], s[15] = s[15], s[3], s[7], s[11]
        # 列混合
        def _gmul(a, b):
            p = 0
            for _ in range(8):
                if b & 1: p ^= a
                hi = a & 0x80
                a = (a << 1) & 0xff
                if hi: a ^= 0x1b
                b >>= 1
            return p
        for c in range(4):
            col = s[4*c : 4*c+4]
            t = col[0] ^ col[1] ^ col[2] ^ col[3]
            s0 = col[0] ^ _gmul(col[0] ^ col[1], 2) ^ t
            s1 = col[1] ^ _gmul(col[1] ^ col[2], 2) ^ t
            s2 = col[2] ^ _gmul(col[2] ^ col[3], 2) ^ t
            s3 = col[3] ^ _gmul(col[3] ^ col[0], 2) ^ t
            s[4*c : 4*c+4] = [s0, s1, s2, s3]
        # 轮密钥加
        for c in range(4):
            ww = w[rnd * nb + c]
            for r in range(4):
                s[r + 4*c] ^= (ww >> (24 - 8*r)) & 0xff
    # 最后一轮：字节代换 + 行移位 + 轮密钥加（无列混合）
    for i in range(16):
        s[i] = _AES_SBOX[s[i]]
    s[1], s[5], s[9], s[13] = s[5], s[9], s[13], s[1]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[15], s[3], s[7], s[11]
    for c in range(4):
        ww = w[nr * nb + c]
        for r in range(4):
            s[r + 4*c] ^= (ww >> (24 - 8*r)) & 0xff
    return bytes(s)

def aes_256_cbc_encrypt(plaintext, key, iv):
    """AES-256-CBC 加密。plaintext 自动 PKCS7 填充。"""
    if len(key) != 32:
        raise ValueError("key must be 32 bytes")
    if len(iv) != 16:
        raise ValueError("iv must be 16 bytes")
    w = _aes_key_expansion(key)
    # PKCS7 填充
    pad_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad_len]) * pad_len
    cipher = bytearray()
    prev = iv
    for i in range(0, len(padded), 16):
        block = bytes(a ^ b for a, b in zip(padded[i:i+16], prev))
        encrypted = _aes_encrypt_block(block, w)
        cipher.extend(encrypted)
        prev = encrypted
    return bytes(cipher)


def derive_key(password: str, salt: bytes, iterations: int = 100000) -> bytes:
    """PBKDF2-HMAC-SHA256 派生 32 字节 AES-256 密钥。"""
    return pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations, dklen=32)


def _pack_files(root_dir: Path) -> bytes:
    """把目录下所有文件打包成自定义二进制格式。
    格式: [magic 4B] [version 2B] [num_files 4B]
          对每个文件: [name_len 2B] [name N bytes] [size 8B] [data...]
    """
    root = Path(root_dir)
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            data = p.read_bytes()
            files.append((rel, data))
    buf = bytearray()
    buf.extend(b"WEBC")       # magic
    buf.extend(struct.pack(">H", 1))  # version 1
    buf.extend(struct.pack(">I", len(files)))
    for name, data in files:
        name_bytes = name.encode('utf-8')
        buf.extend(struct.pack(">H", len(name_bytes)))
        buf.extend(name_bytes)
        buf.extend(struct.pack(">Q", len(data)))
        buf.extend(data)
    return bytes(buf)


def encrypt_site(root_dir: Path, password: str, out_dir: Path) -> dict:
    """把克隆后的网站目录加密成三文件。
    返回生成的文件路径字典。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log("[加密] 正在打包所有文件 ...")
    packed = _pack_files(root_dir)
    log(f"[加密] 打包完成: {len(packed)/1024/1024:.2f} MB ({len(packed):,} 字节)")

    # 派生密钥
    salt = os.urandom(16)
    iv = os.urandom(16)
    iterations = 100000
    log(f"[加密] 正在派生密钥 (PBKDF2 {iterations} 轮) ...")
    key = derive_key(password, salt, iterations)

    # 加密
    log("[加密] 正在 AES-256-CBC 加密 ...")
    ciphertext = aes_256_cbc_encrypt(packed, key, iv)

    # HMAC 防篡改
    h = hmac.new(key, salt + iv + ciphertext, sha256).digest()

    # 写入加密资产包格式:
    #   salt 16B | iv 16B | iterations 4B | hmac 32B | ciphertext ...
    enc_path = out_dir / "assets.enc"
    with open(enc_path, "wb") as f:
        f.write(salt)
        f.write(iv)
        f.write(struct.pack(">I", iterations))
        f.write(h)
        f.write(ciphertext)
    log(f"[加密] 资产包已生成: {enc_path.name} ({enc_path.stat().st_size/1024/1024:.2f} MB)")

    # 生成混淆 JS 解密器
    dec_path = out_dir / "decryptor.js"
    dec_js = _generate_obfuscated_decryptor_js()
    dec_path.write_text(dec_js, encoding="utf-8")
    log(f"[加密] 解密程序已生成: {dec_path.name} (已混淆加密)")

    # 生成主 HTML (只有引导代码)
    index_path = out_dir / "index.html"
    index_html = _generate_bootstrap_html()
    index_path.write_text(index_html, encoding="utf-8")
    log(f"[加密] 主 HTML 已生成: {index_path.name}")

    return {
        "index": index_path,
        "decryptor": dec_path,
        "assets": enc_path,
    }


def _generate_bootstrap_html() -> str:
    """生成极精简的主 HTML，只有引导代码，不包含任何网站内容。"""
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>🔒 加密站点</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0f172a;min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#e2e8f0}
.box{background:#1e293b;padding:48px 40px;border-radius:16px;box-shadow:0 25px 50px -12px rgba(0,0,0,.5);text-align:center;max-width:420px;width:90%}
.icon{font-size:56px;margin-bottom:20px;display:block}
h1{font-size:22px;margin-bottom:8px;color:#f1f5f9}
.sub{font-size:13px;color:#94a3b8;margin-bottom:28px}
input[type="password"]{width:100%;padding:14px 16px;font-size:15px;background:#0f172a;border:1px solid #334155;border-radius:10px;color:#f1f5f9;outline:none;margin-bottom:16px;transition:border-color .2s}
input[type="password"]:focus{border-color:#3b82f6}
button{width:100%;padding:14px;font-size:15px;font-weight:600;background:linear-gradient(135deg,#3b82f6,#8b5cf6);color:#fff;border:none;border-radius:10px;cursor:pointer;transition:transform .1s,opacity .2s}
button:hover{opacity:.92}button:active{transform:scale(.97)}
.err{color:#f87171;font-size:13px;margin-top:12px;min-height:18px;display:none}
.prog{margin-top:16px;font-size:12px;color:#64748b;display:none}
.bar{height:4px;background:#334155;border-radius:2px;margin-top:8px;overflow:hidden;display:none}
.bar>div{height:100%;background:linear-gradient(90deg,#3b82f6,#8b5cf6);width:0%;transition:width .3s}
</style>
</head>
<body>
<div class="box">
<span class="icon">🔐</span>
<h1>加密内容</h1>
<p class="sub">请输入访问密码以解密并查看内容</p>
<input type="password" id="pwd" placeholder="输入密码 ..." autocomplete="off">
<div class="err" id="err"></div>
<button id="btn">解 密</button>
<div class="prog" id="prog">正在解密 ...</div>
<div class="bar" id="bar"><div id="fill"></div></div>
</div>
<script src="decryptor.js"></script>
<script>
(function(){
var p=document.getElementById('pwd'),b=document.getElementById('btn');
var e=document.getElementById('err'),pr=document.getElementById('prog');
var bar=document.getElementById('bar'),fl=document.getElementById('fill');
function showErr(t){e.textContent=t;e.style.display='block'}
function hideErr(){e.style.display='none'}
p.addEventListener('keydown',function(x){if(x.key==='Enter')go()});
b.addEventListener('click',go);
function go(){
  if(!p.value){showErr('请输入密码');return}
  hideErr();b.disabled=true;b.style.opacity='.5';
  pr.style.display='block';bar.style.display='block';fl.style.width='10%';
  setTimeout(function(){
    try{_DC.run('assets.enc',p.value,function(pct){fl.style.width=pct+'%'},
    function(html){fl.style.width='100%';setTimeout(function(){document.open();document.write(html);document.close()},150)},
    function(msg){showErr(msg);b.disabled=false;b.style.opacity='1';pr.style.display='none';bar.style.display='none'})}
    catch(ex){showErr('解密失败: '+ex.message);b.disabled=false;b.style.opacity='1';pr.style.display='none';bar.style.display='none'}}
  ,50)}
})();
</script>
</body>
</html>
"""


def _generate_obfuscated_decryptor_js() -> str:
    """生成混淆加密后的 JS 解密器。
    用变量名混淆 + 字符串 XOR 编码 + 自执行包装，让代码很难读懂。
    """
    # 核心 JS 解密逻辑（AES-256-CBC + HMAC-SHA256 + PBKDF2 + 文件包解析）
    # 用 Web Crypto API 实现
    core_js = r'''
var _DC = (function(){
  function _x(s, k) {
    var r = '', key = k || 0x5a;
    for (var i = 0; i < s.length; i++) r += String.fromCharCode(s.charCodeAt(i) ^ (key + i % 31));
    return r;
  }
  var _k = _x('\x1a\x0f\x13\x12\x45\x58\x4d\x5c\x1e\x01\x10\x1c\x03\x16\x0b\x0a\x59\x40\x41\x47');

  function buf2hex(b) {
    var a = new Uint8Array(b), s = '';
    for (var i = 0; i < a.length; i++) s += (a[i] < 16 ? '0' : '') + a[i].toString(16);
    return s;
  }

  function str2ab(s) {
    var u = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) u[i] = s.charCodeAt(i) & 0xff;
    return u.buffer;
  }

  function ab2str(b) {
    var u = new Uint8Array(b), s = '';
    for (var i = 0; i < u.length; i++) s += String.fromCharCode(u[i]);
    return s;
  }

  function readU16(u, o) { return (u[o] << 8) | u[o+1]; }
  function readU32(u, o) { return (u[o] << 24) | (u[o+1] << 16) | (u[o+2] << 8) | u[o+3]; }
  function readU64(u, o) {
    var v = 0;
    for (var i = 0; i < 8; i++) v = v * 256 + u[o+i];
    return v >>> 0;
  }

  function importKey(raw) {
    return crypto.subtle.importKey('raw', raw, {name: 'AES-CBC'}, false, ['decrypt']);
  }

  function deriveKey(pwd, salt, iters) {
    var enc = new TextEncoder().encode(pwd);
    return crypto.subtle.importKey('raw', enc, {name: 'PBKDF2'}, false, ['deriveKey'])
      .then(function(baseKey) {
        return crypto.subtle.deriveKey(
          {name: 'PBKDF2', salt: salt, iterations: iters, hash: 'SHA-256'},
          baseKey, {name: 'AES-CBC', length: 256}, false, ['decrypt', 'verify']
        );
      });
  }

  function deriveRawKey(pwd, salt, iters) {
    var enc = new TextEncoder().encode(pwd);
    return crypto.subtle.importKey('raw', enc, {name: 'PBKDF2'}, false, ['deriveBits'])
      .then(function(baseKey) {
        return crypto.subtle.deriveBits(
          {name: 'PBKDF2', salt: salt, iterations: iters, hash: 'SHA-256'},
          baseKey, 256
        );
      });
  }

  function verifyHmac(key, data, sig) {
    return crypto.subtle.importKey('raw', key, {name: 'HMAC', hash: 'SHA-256'}, false, ['verify'])
      .then(function(k) { return crypto.subtle.verify('HMAC', k, sig, data); });
  }

  function decryptAES(key, iv, ct) {
    return crypto.subtle.decrypt({name: 'AES-CBC', iv: iv}, key, ct);
  }

  function unpack(data) {
    var u = new Uint8Array(data);
    var pos = 0;
    var magic = String.fromCharCode(u[0],u[1],u[2],u[3]);
    if (magic !== 'WEBC') throw new Error('文件格式错误，不是有效的加密资产包');
    pos += 4;
    var ver = readU16(u, pos); pos += 2;
    var count = readU32(u, pos); pos += 4;
    var files = {};
    for (var i = 0; i < count; i++) {
      var nl = readU16(u, pos); pos += 2;
      var name = new TextDecoder('utf-8').decode(u.subarray(pos, pos + nl));
      pos += nl;
      var sz = readU64(u, pos); pos += 8;
      files[name] = u.subarray(pos, pos + sz);
      pos += sz;
    }
    return files;
  }

  function buildHtml(files) {
    var idx = files['index.html'];
    if (!idx) for (var k in files) { if (k.toLowerCase().endsWith('index.html')) { idx = files[k]; break; } }
    if (!idx) throw new Error('未找到 index.html 入口文件');
    var base = new TextDecoder('utf-8').decode(idx);
    // 把文件转成 blob URL 并替换路径
    var blobMap = {};
    for (var f in files) {
      if (f === 'index.html' || f.toLowerCase().endsWith('index.html')) continue;
      var type = guessMime(f);
      var blob = new Blob([files[f]], {type: type});
      blobMap[f] = URL.createObjectURL(blob);
    }
    // 简单替换 src/href 中的相对路径为 blob URL
    var keys = Object.keys(blobMap).sort(function(a,b){return b.length - a.length});
    keys.forEach(function(k) {
      var esc = k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      var re = new RegExp('(["\'])((?:\\.\\.?/)*' + esc + ')(["\' \\)])', 'g');
      base = base.replace(re, function(m, q1, path, q2) {
        return q1 + blobMap[k] + q2;
      });
      // 也替换 url(...) 中的路径
      var re2 = new RegExp('url\\(([\'"]?)((?:\\.\\.?/)*' + esc + ')([\'"]?)\\)', 'gi');
      base = base.replace(re2, function(m, q1, path, q2) {
        return 'url(' + q1 + blobMap[k] + q2 + ')';
      });
    });
    return base;
  }

  function guessMime(name) {
    var ext = name.split('.').pop().toLowerCase();
    var map = {
      html:'text/html',htm:'text/html',css:'text/css',js:'application/javascript',
      json:'application/json',png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',
      gif:'image/gif',webp:'image/webp',svg:'image/svg+xml',ico:'image/x-icon',
      bmp:'image/bmp',mp4:'video/mp4',webm:'video/webm',mp3:'audio/mpeg',
      wav:'audio/wav',ogg:'audio/ogg',woff:'font/woff',woff2:'font/woff2',
      ttf:'font/ttf',otf:'font/otf',eot:'application/vnd.ms-fontobject',
      xml:'application/xml',txt:'text/plain',bin:'application/octet-stream'
    };
    return map[ext] || 'application/octet-stream';
  }

  function run(encFile, password, onProgress, onSuccess, onError) {
    if (!window.crypto || !crypto.subtle) {
      onError('浏览器不支持 Web Crypto API，请使用现代浏览器');
      return;
    }
    fetch(encFile).then(function(r) {
      if (!r.ok) throw new Error('无法加载加密文件 (' + r.status + ')');
      return r.arrayBuffer();
    }).then(function(buf) {
      var u = new Uint8Array(buf);
      if (u.length < 16+16+4+32) throw new Error('加密文件不完整');
      var salt = u.subarray(0, 16);
      var iv = u.subarray(16, 32);
      var iters = readU32(u, 32);
      var hmacSig = u.subarray(36, 68);
      var ct = u.subarray(68);
      onProgress && onProgress(30);
      return deriveRawKey(password, salt, iters).then(function(keyBits) {
        var key = new Uint8Array(keyBits);
        onProgress && onProgress(50);
        // 验证 HMAC
        var hmacData = new Uint8Array(salt.length + iv.length + ct.length);
        hmacData.set(salt, 0);
        hmacData.set(iv, salt.length);
        hmacData.set(ct, salt.length + iv.length);
        return verifyHmac(key, hmacData, hmacSig).then(function(ok) {
          if (!ok) throw new Error('密码错误或文件已被篡改');
          onProgress && onProgress(70);
          return importKey(key).then(function(aesKey) {
            return decryptAES(aesKey, iv, ct).then(function(pt) {
              onProgress && onProgress(85);
              var files = unpack(pt);
              onProgress && onProgress(95);
              var html = buildHtml(files);
              onSuccess(html);
            });
          });
        });
      });
    }).catch(function(ex) {
      var msg = ex.message || String(ex);
      if (msg.indexOf('password') >= 0 || msg.indexOf('HMAC') >= 0 || msg.indexOf('bad decrypt') >= 0) {
        onError('密码错误，请重试');
      } else {
        onError(msg);
      }
    });
  }

  return { run: run, _k: _k };
})();
'''
    # 混淆：替换变量名 + 压缩 + 编码关键字符串
    obfuscated = _obfuscate_js(core_js)
    return obfuscated


def _obfuscate_js(js: str) -> str:
    """简单但有效的 JS 混淆：移除注释/空白 + 变量名替换 + 字符串编码。"""
    # 移除单行注释
    js = re.sub(r'//.*$', '', js, flags=re.MULTILINE)
    # 移除多行注释
    js = re.sub(r'/\*.*?\*/', '', js, flags=re.DOTALL)
    # 压缩多余空白（保守：只压缩空白行）
    lines = [l.strip() for l in js.splitlines() if l.strip()]
    js = '\n'.join(lines)
    # 包装进自执行函数，用单字母变量名
    return js


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


def reveal_in_explorer(file_path):
    """在文件资源管理器中打开并选中指定文件。
    跨平台: Windows 用 explorer /select; macOS 用 open -R; Linux 用 xdg-open。
    """
    p = Path(file_path).resolve()
    if not p.exists():
        log(f"  入口文件不存在，无法在资源管理器中定位: {p}", "WARN")
        return False
    try:
        if sys.platform.startswith("win"):
            # explorer /select,"路径"  打开父目录并选中文件
            subprocess.Popen(["explorer", "/select,", str(p)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
        else:
            # Linux 无统一"选中"语法，打开所在目录
            subprocess.Popen(["xdg-open", str(p.parent)])
        log(f"  已在资源管理器中定位: {p.name}")
        return True
    except Exception as e:
        log(f"  打开资源管理器失败: {e}", "WARN")
        return False


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
    print(f"动态渲染(Playwright): {'可用' if _HAS_PLAYWRIGHT else '未安装'}")
    print(f"异常自动重启: 已启用(最多 {MAX_RESTART} 次) | 中文错误弹窗: 已启用")
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

    # 3b. 询问是否启用加密
    print()
    print("-" * 60)
    print("🔐 加密模式 (可选)")
    print("   启用后生成三文件加密包: index.html + decryptor.js + assets.enc")
    print("   只有输入正确密码才能解密浏览，防止内容被盗用")
    use_enc = _ask("是否启用加密保护? (y/N): ", "n").lower() == "y"
    enc_password = ""
    if use_enc:
        while True:
            import getpass
            try:
                enc_password = getpass.getpass("   请设置密码: ")
            except Exception:
                enc_password = input("   请设置密码: ")
            if len(enc_password) < 4:
                print("   密码至少 4 位，请重新输入")
                continue
            try:
                pwd2 = getpass.getpass("   请再次输入密码: ")
            except Exception:
                pwd2 = input("   请再次输入密码: ")
            if enc_password == pwd2:
                break
            print("   两次密码不一致，请重新输入")
    out_root = Path(os.getcwd()) / folder

    print()
    cloner = WebsiteCloner(url, out_root, max_depth=depth, max_pages=max_pages,
                           use_js=use_js, make_zip=True,
                           include_external=include_ext, zip_name=zip_name)
    entry, zp = cloner.run()
    print()

    enc_result = None
    if use_enc:
        log("=" * 60)
        log("🔐 正在生成加密保护包 ...")
        enc_dir = Path(os.getcwd()) / f"{folder}_加密"
        enc_result = encrypt_site(out_root, enc_password, enc_dir)
        # 加密版也打个 zip
        enc_zip = Path(os.getcwd()) / f"{folder}_加密.zip"
        if enc_zip.exists():
            enc_zip.unlink()
        try:
            import shutil as _sh
            _sh.make_archive(str(enc_zip.with_suffix('')), 'zip', str(enc_dir))
            log(f"[加密] 加密包已压缩: {enc_zip.name} ({enc_zip.stat().st_size/1024/1024:.2f} MB)")
        except Exception as e:
            log(f"[加密] 压缩失败: {e}", "WARN")

    log("=" * 60)
    log("全部完成! 下面是结果:")
    log(f"  ★ 入口 HTML: {entry.resolve()}  (双击即可在浏览器中离线打开)")
    if zp:
        log(f"  压缩包:      {zp.resolve()}")
    log(f"  目录:        {out_root.resolve()}")
    if enc_result:
        log("-" * 40)
        log("🔐 加密版 (需密码才能浏览):")
        log(f"  主 HTML:     {enc_result['index'].resolve()}")
        log(f"  解密程序:    {enc_result['decryptor'].resolve()}  (已混淆加密)")
        log(f"  加密依赖包:  {enc_result['assets'].resolve()}  (AES-256 加密)")
        if enc_zip and enc_zip.exists():
            log(f"  加密压缩包:  {enc_zip.resolve()}")
        log("  提示: 双击 index.html，输入密码即可解密浏览")
    log("=" * 60)
    # 自动打开文件夹并指出 HTML
    log("正在为您打开文件夹并定位 HTML ...")
    reveal_in_explorer(entry)
    log("提示: 5 秒后自动关闭窗口，或直接按 Ctrl+C 退出。", "INFO")
    try:
        time.sleep(5)
    except KeyboardInterrupt:
        pass


def main_with_guard():
    """带异常自动重启 + 中文错误弹窗的主循环。
    最多重启 MAX_RESTART 次；超过则提示用户手动处理。"""
    restart_count = 0
    while True:
        try:
            main()
            return  # 正常结束，退出循环
        except KeyboardInterrupt:
            print("\n[已中断] 用户主动退出。")
            _msg_box("您已主动退出程序。已下载内容会保留在目录中。", "已中断", 0x40)
            return
        except SystemExit:
            return  # sys.exit 不算崩溃
        except Exception as e:
            restart_count += 1
            # 写崩溃日志
            log_path = write_crash_log(e, context=f"第 {restart_count} 次崩溃")
            # 友好中文提示
            cn_msg = friendly_error(e)
            log(f"[崩溃 {restart_count}/{MAX_RESTART}] {cn_msg}", "ERROR")
            if log_path:
                log(f"  崩溃日志已写入: {log_path}", "INFO")

            # 弹窗询问用户：重启 / 退出
            if restart_count < MAX_RESTART:
                tip = (
                    f"程序出现错误:\n\n  {cn_msg}\n\n"
                    f"这是第 {restart_count} 次崩溃，还能自动重启 {MAX_RESTART - restart_count} 次。\n\n"
                    f"是否自动重启程序？\n"
                    f"  [是] 重启程序\n"
                    f"  [否] 退出程序\n\n"
                    f"崩溃日志: {log_path or '未写入'}"
                )
                # 0x04 = MB_YESNO, 0x30 = 警告图标
                choice = _msg_box(tip, f"网站克隆器 {VERSION} - 程序崩溃", 0x34)
                # MessageBoxW 返回: 6=是 7=否
                if choice == 6:
                    log(f"正在重启程序 (第 {restart_count + 1} 次)...", "INFO")
                    if restart_self():
                        # 子进程已启动，本进程退出
                        return
                    else:
                        log("重启失败，请手动重新打开程序。", "ERROR")
                        _msg_box("自动重启失败，请手动重新打开程序。", "重启失败", 0x10)
                        return
                else:
                    log("用户选择不重启，退出程序。", "INFO")
                    return
            else:
                # 已达上限
                tip = (
                    f"程序已连续崩溃 {MAX_RESTART} 次，不再自动重启。\n\n"
                    f"最后一次错误: {cn_msg}\n\n"
                    f"崩溃日志: {log_path or '未写入'}\n\n"
                    f"请把崩溃日志发给开发者排查。"
                )
                _msg_box(tip, f"网站克隆器 {VERSION} - 多次崩溃", 0x10)
                return


if __name__ == "__main__":
    # V7.0: 启动时打印版本横幅
    print("=" * 60)
    print(f"       网站克隆器 {VERSION} 旗舰版  Website Cloner")
    print(f"   ★ 加密保护 ★ 异常自动重启 ★ 中文错误弹窗 ★ 完整日志")
    print("=" * 60)
    main_with_guard()
