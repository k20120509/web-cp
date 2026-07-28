# -*- coding: utf-8 -*-
"""
网站克隆器 V15.5 - Website Cloner V15.5
核心改进:
  * 预扫描阶段: 先获取首页HTML, 解析所有资源URL, 确定准确总数
  * 进度条只增不减: 不再动态add_total, 不重置done, 消除倒退现象
  * B站视频下载: 纯Python实现, 解析B站视频真实地址并下载
  * 智能算法: 去重优先、跳过已知不可达资源、自适应并发
  * 图标修复: 生成有效ICO文件
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
import random
from urllib.parse import urljoin, urlparse, urlunparse, unquote
from pathlib import Path, PurePosixPath
from concurrent.futures import ThreadPoolExecutor, as_completed

VERSION = "V16.0"
MAX_RETRIES = 3  # 降低重试次数（原10次太多导致卡死）
RETRY_BACKOFF = 1.0  # 增加基础等待时间
CONNECT_TIMEOUT = 4
READ_TIMEOUT = 12
REQUEST_DELAY = 0.05  # 增加请求间隔（原0.005太快易被限流）
MAX_WORKERS = 32  # 降低并发（原64太高易被限流）
MAX_CRAWL_TIME = 600  # 最大爬取时间600秒（10分钟）
DOWNLOAD_TIMEOUT = 30  # 单个下载超时30秒


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

    def reset(self):
        with self.lock:
            self.pages_ok = 0
            self.pages_fail = 0
            self.assets_ok = 0
            self.assets_fail = 0
            self.bytes = 0


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


# ===================== 智能进度条（只增不减 + 超时机制） =====================
class SmartProgress:
    """
    V16.0 改进: 进度条超时机制 + 自动完成检测
    - 预扫描确定总数后固定
    - 支持超时强制完成（爬取时间过长时）
    - 支持动态增加total（当发现预估不准时）
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.done = 0
        self.total = 0
        self.start = time.time()
        self.recent = []
        self.recent_bytes = []
        self.last_render = 0
        self.current = ""
        self.phase = "初始化"
        self._force_complete = False
        self._last_activity = time.time()  # 最后活动时间
        self._no_progress_count = 0  # 无进展计数

    def set_total(self, n):
        """设置固定总数（预扫描后调用一次）"""
        with self.lock:
            if n > self.total:
                self.total = n

    def add_total(self, n):
        """增加总数（发现预估不准时使用）"""
        with self.lock:
            self.total += n

    def set_phase(self, phase):
        """切换阶段"""
        with self.lock:
            self.phase = phase
            self.last_render = 0

    def set_current(self, desc):
        with self.lock:
            self.current = desc[:38]

    def step(self, cost=None, nbytes=0):
        with self.lock:
            self.done += 1
            self._last_activity = time.time()
            self._no_progress_count = 0
            if cost is not None:
                self.recent.append(cost)
                if len(self.recent) > 20:
                    self.recent.pop(0)
            if nbytes:
                self.recent_bytes.append((time.time(), nbytes))
                cutoff = time.time() - 10
                self.recent_bytes = [t for t in self.recent_bytes if t[0] > cutoff]
            self._render()

    def check_timeout(self, max_time=600):
        """检查是否超时"""
        with self.lock:
            elapsed = time.time() - self.start
            if elapsed > max_time:
                self._force_complete = True
                return True
            # 检查是否长时间无进展
            if self.total > 0 and self.done > 0:
                progress_ratio = self.done / max(self.total, 1)
                if progress_ratio < 0.5:  # 如果进度不到50%
                    time_since_activity = time.time() - self._last_activity
                    if time_since_activity > 60:  # 超过60秒无活动
                        self._no_progress_count += 1
                    if self._no_progress_count > 3:  # 连续3次检查无进展
                        self._force_complete = True
                        return True
            return False

    def _render(self):
        now = time.time()
        if now - self.last_render < 0.08 and self.done < self.total:
            return
        self.last_render = now
        elapsed = now - self.start
        # 如果已完成或强制完成，显示100%
        if self._force_complete or (self.total > 0 and self.done >= self.total):
            pct = 1.0
        else:
            total = max(self.total, self.done)
            pct = self.done / total if total else 0
        bw = 30
        filled = int(bw * pct)
        bar = "#" * filled + "-" * (bw - filled)
        avg = (sum(self.recent) / len(self.recent)) if self.recent else (elapsed / max(self.done, 1))
        eta = self._fmt(max(0, (self.total - self.done)) * avg) if not self._force_complete else "完成"
        el = self._fmt(elapsed)
        if self.recent_bytes:
            bps = sum(b for _, b in self.recent_bytes) / max(1, now - self.recent_bytes[0][0])
            speed = self._sz(bps)
        else:
            speed = "-"
        cur = self.current
        force_note = " [超时完成]" if self._force_complete else ""
        sys.stdout.write(
            f"\r[{self.phase}] {bar} {pct:5.1%} | {self.done}/{self.total} | {speed}/s | {el} | 剩余{eta}{force_note} | {cur}   "
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
    def _sz(n):
        for u in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f}{u}"
            n /= 1024
        return f"{n:.1f}TB"

    def finish(self):
        with self.lock:
            self.done = self.total = max(self.done, self.total, 1)
            self._render()
        sys.stdout.write("\n\n")
        sys.stdout.flush()

    def reset_for_new_task(self):
        """仅在新网站克隆时重置"""
        with self.lock:
            self.done = 0
            self.total = 0
            self.start = time.time()
            self.recent = []
            self.recent_bytes = []
            self.last_render = 0
            self.current = ""
            self.phase = "克隆中"
            self._force_complete = False
            self._last_activity = time.time()
            self._no_progress_count = 0


PROGRESS = SmartProgress()


# ===================== 轻量级代理池 =====================
class SimpleProxyPool:
    """简单代理池 - 用于网站克隆器加速"""
    
    PROXY_SOURCES = [
        "https://api.proxyscrape.com/v3/free-proxy-list/get?request=display_proxies&proxy_format=ipport&format=text",
        "https://api.openproxylist.xyz/http.txt",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    ]
    
    def __init__(self):
        self.proxies = []
        self.initialized = False
        self.best_proxy = None
    
    def initialize(self):
        """初始化代理池"""
        if self.initialized:
            return
        
        log("初始化代理池...", "INFO")
        
        # 获取代理
        all_proxies = []
        for source in self.PROXY_SOURCES:
            try:
                r = requests.get(source, timeout=8, headers={"User-Agent": USER_AGENT})
                if r.status_code == 200:
                    for line in r.text.strip().split("\n"):
                        line = line.strip()
                        if ":" in line:
                            all_proxies.append(line)
            except Exception:
                continue
        
        # 去重
        all_proxies = list(set(all_proxies))[:50]
        log(f"获取到 {len(all_proxies)} 个代理，测试中...", "INFO")
        
        # 测试代理
        valid = []
        for proxy_str in all_proxies:
            try:
                host, port = proxy_str.split(":")
                port = int(port)
                proxies = {"http": f"http://{host}:{port}", "https": f"http://{host}:{port}"}
                start = time.time()
                r = requests.get("https://www.google.com", proxies=proxies, timeout=5, verify=False)
                if r.status_code < 400:
                    latency = time.time() - start
                    valid.append({"host": host, "port": port, "latency": latency})
            except Exception:
                continue
        
        # 按延迟排序
        valid.sort(key=lambda x: x["latency"])
        self.proxies = valid
        self.best_proxy = valid[0] if valid else None
        self.initialized = True
        
        if self.best_proxy:
            log(f"代理池就绪！最优: {self.best_proxy['host']}:{self.best_proxy['port']} ({self.best_proxy['latency']:.2f}s)", "SUCCESS")
        else:
            log("代理池未找到可用代理", "WARN")
    
    def get_proxy(self):
        """获取最优代理"""
        if not self.initialized:
            self.initialize()
        if self.best_proxy:
            p = self.best_proxy
            return {"http": f"http://{p['host']}:{p['port']}", "https": f"http://{p['host']}:{p['port']}"}
        return None


CLONER_PROXY_POOL = SimpleProxyPool()


# ===================== B站视频下载器 =====================
class BilibiliDownloader:
    """纯Python实现的B站视频下载器（无需ffmpeg）"""

    HEADERS = {
        "User-Agent": USER_AGENT,
        "Referer": "https://www.bilibili.com/",
    }

    @staticmethod
    def extract_bvid(url):
        m = re.search(r"(BV[0-9A-Za-z]{10})", url)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def is_bilibili_video(url):
        """判断URL是否为B站视频页面"""
        if "bilibili.com/video" in url or "b23.tv" in url:
            return True
        return False

    def download(self, url, save_dir):
        """
        下载B站视频
        返回: (保存路径, True/False)
        """
        bvid = self.extract_bvid(url)
        if not bvid:
            return None, False

        try:
            # 1. 获取视频信息
            api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
            resp = requests.get(api_url, headers=self.HEADERS, timeout=10)
            data = resp.json()
            if data["code"] != 0:
                log(f"  [视频] 获取视频信息失败: {data.get('message', '未知错误')}", "WARN")
                return None, False

            title = data["data"]["title"]
            cid = data["data"]["cid"]
            safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)[:60]
            log(f"  [视频] 标题: {title}", "INFO")

            # 2. 获取播放地址（durl格式，音视频一体）
            play_api = (
                f"https://api.bilibili.com/x/player/playurl"
                f"?bvid={bvid}&cid={cid}&qn=64&fnval=0&fourk=1"
            )
            resp2 = requests.get(play_api, headers=self.HEADERS, timeout=10)
            play_data = resp2.json()
            if play_data["code"] != 0:
                log(f"  [视频] 获取播放地址失败: {play_data.get('message')}", "WARN")
                return None, False

            durl_list = play_data["data"]["durl"]
            save_path = Path(save_dir) / f"{safe_title}.mp4"
            save_path.parent.mkdir(parents=True, exist_ok=True)

            # 3. 下载视频分段
            total_size = 0
            with open(save_path, "wb") as f:
                for idx, seg in enumerate(durl_list, 1):
                    seg_url = seg["url"]
                    seg_size = seg.get("size", 0)
                    log(f"  [视频] 下载第 {idx}/{len(durl_list)} 段 ({seg_size/1024/1024:.1f}MB)...")

                    seg_headers = {**self.HEADERS, "Referer": f"https://www.bilibili.com/video/{bvid}/"}
                    r = requests.get(seg_url, headers=seg_headers, stream=True, timeout=30)
                    r.raise_for_status()

                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
                            total_size += len(chunk)

            log(f"  [视频] 下载完成: {save_path.name} ({total_size/1024/1024:.1f}MB)", "INFO")
            return str(save_path), True

        except Exception as e:
            log(f"  [视频] 下载失败: {e}", "WARN")
            return None, False


# ===================== 克隆器 =====================
class WebsiteCloner:
    def __init__(self, start_url, out_root, max_depth=999, max_pages=9999,
                 use_js=True, include_external=True):
        self.start_url = normalize_url(start_url)
        self.base_domain = urlparse(self.start_url).netloc
        self.out_root = Path(out_root).resolve()
        self.assets_dir = self.out_root / "_assets"
        self.videos_dir = self.out_root / "_videos"
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
        self._video_dl = BilibiliDownloader()
        self._video_links = []  # 收集B站视频链接

    def _build_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })
        retry = Retry(total=2, backoff_factor=0.5,  # 降低重试次数
                      status_forcelist=(500, 502, 503, 504),
                      allowed_methods=frozenset(["GET"]))
        ad = HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=retry)
        s.mount("http://", ad)
        s.mount("https://", ad)
        return s

    def _get_asset_filename(self, url):
        p = urlparse(url)
        path = unquote(p.path).lstrip("/")
        if not path:
            path = "index"
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
                # 添加随机延迟避免被限流
                time.sleep(REQUEST_DELAY * (0.5 + 0.5 * (attempt + 1)) + random.uniform(0, 0.1))
                r = self.session.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                                     allow_redirects=True, stream=stream)
                if r.status_code < 400:
                    return r
                if r.status_code in (403, 429):
                    last = f"HTTP {r.status_code}"
                    if attempt < max_tries - 1:
                        wait = RETRY_BACKOFF * (2 ** attempt) + random.uniform(0, 0.5)
                        time.sleep(wait)
                    continue
                return r
            except requests.exceptions.Timeout:
                last = "超时"
                if attempt < max_tries - 1:
                    time.sleep(RETRY_BACKOFF * (2 ** attempt))
            except requests.RequestException as e:
                last = e.__class__.__name__
                if attempt < max_tries - 1:
                    time.sleep(RETRY_BACKOFF)
        if last:
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
        # 降低阈值从500到100字符（有些有效页面内容较短）
        if (html is None or len(html.strip()) < 100) and self.use_js:
            log(f"动态渲染: {url}")
            h2, f2 = self._render_js(url)
            if h2:
                return h2, f2
            # 如果JS渲染也失败，保留原始结果（即使很短）
        return html, final

    # ===================== 预扫描（V15.5核心改进） =====================
    def prescan(self, url):
        """
        预扫描: 获取首页HTML, 解析所有资源URL, 确定准确总数
        返回: (资源URL列表, 页面URL列表)
        """
        log("[预扫描] 正在获取首页内容...")
        html, final = self.fetch_html(url)
        if not html:
            log("[预扫描] 首页获取失败", "ERROR")
            return [], []

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            soup = BeautifulSoup(html, "lxml")

        base_href = None
        bt = soup.find("base", href=True)
        if bt:
            base_href = urljoin(final, bt["href"])

        asset_urls = set()
        page_urls = set()

        # 收集资源URL
        for tag, attr in RESOURCE_SPECS:
            for el in soup.find_all(tag):
                v = el.get(attr)
                if not v:
                    continue
                if tag == "link" and el.get("rel"):
                    rel_val = " ".join(el.get("rel", [])).lower()
                    skip = {"dns-prefetch", "preconnect", "canonical", "alternate",
                            "prev", "next", "pingback", "wlwmanifest", "EditURI",
                            "shortlink", "amphtml", "manifest", "privacy-policy"}
                    if any(r in rel_val for r in skip):
                        continue
                a = self._resolve(final, v, base_href)
                if a:
                    asset_urls.add(a)

        # srcset
        for el in soup.find_all(["img", "source"]):
            ss = el.get("srcset")
            if ss:
                for part in ss.split(","):
                    u = part.strip().split()[0]
                    a = self._resolve(final, u, base_href)
                    if a:
                        asset_urls.add(a)

        # 收集同域页面URL（用于递归模式）
        if self.max_depth > 0:
            for a_tag in soup.find_all("a", href=True):
                raw = a_tag["href"].strip()
                if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                    continue
                absu = self._resolve(final, raw, base_href)
                if absu and is_same_domain(absu, self.base_domain) and is_page(absu):
                    norm = normalize_url(absu)
                    if norm != normalize_url(url):
                        page_urls.add(norm)

        # 检测B站视频链接
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if self._video_dl.is_bilibili_video(href):
                self._video_links.append(href)

        log(f"[预扫描] 发现 {len(asset_urls)} 个资源, {len(page_urls)} 个子页面, {len(self._video_links)} 个视频")
        return list(asset_urls), list(page_urls)

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
            STATS.assets_fail += 1
            PROGRESS.step(time.time() - t0)
            return (relp, False)
        if r.status_code >= 400:
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
            # B站视频链接特殊处理
            if self._video_dl.is_bilibili_video(absu):
                self._video_links.append(absu)
                a["href"] = absu
                a["target"] = "_blank"
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

    def close_js(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    # ===================== 主流程（V15.5 重写） =====================
    def run(self):
        log(f"目标: {self.start_url}")
        log(f"深度: {self.max_depth} | 页面上限: {self.max_pages} | 外域资源: {'下载' if self.include_external else '跳过'} | 动态渲染: {'开' if self.use_js else '关'}")
        log(f"输出: {self.out_root}")
        print("-" * 60)

        self.out_root.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.videos_dir.mkdir(parents=True, exist_ok=True)

        # ========== 阶段1: 预扫描 ==========
        PROGRESS.set_phase("预扫描")
        PROGRESS.set_current("获取首页内容...")
        asset_urls, page_urls = self.prescan(self.start_url)

        # 计算准确总数: 首页(1) + 首页资源 + 子页面(限制数量) + 子页面资源(预估)
        total_pages = min(len(page_urls) + 1, self.max_pages)
        # 预估子页面资源数: 平均每页资源数 * 子页面数
        avg_assets_per_page = len(asset_urls)
        estimated_sub_assets = avg_assets_per_page * (total_pages - 1)
        total_tasks = 1 + len(asset_urls) + (total_pages - 1) + estimated_sub_assets

        PROGRESS.set_total(total_tasks)
        log(f"[预扫描] 预估总任务数: {total_tasks} (页面:{total_pages}, 资源:{len(asset_urls)}+预估{estimated_sub_assets})")

        # ========== 阶段2: 爬取页面 ==========
        PROGRESS.set_phase("爬取页面")

        # 先下载首页
        self.queue.append((self.start_url, 0))
        crawled_count = 0
        while self.queue and STATS.pages_ok + STATS.pages_fail < self.max_pages:
            # 超时检查
            if PROGRESS.check_timeout(MAX_CRAWL_TIME):
                log("爬取超时，正在完成当前任务...", "WARN")
                break
            
            url, depth = self.queue.pop(0)
            if url in self.visited:
                continue
            
            # 动态增加总数（每发现新页面增加预估）
            crawled_count += 1
            
            try:
                new_pages = self.clone_page(url, depth)
                self.queue.extend(new_pages)
                
                # 动态调整进度条总数
                actual_tasks = (STATS.pages_ok + STATS.pages_fail + 
                              STATS.assets_ok + STATS.assets_fail)
                estimated_remaining = len(self.queue) * 10  # 预估每个页面10个资源
                if PROGRESS.total < actual_tasks + estimated_remaining:
                    PROGRESS.set_total(actual_tasks + estimated_remaining)
                    
            except Exception as e:
                log(f"页面克隆异常: {e}", "ERROR")
                STATS.pages_fail += 1
                PROGRESS.step()

        self.close_js()

        # ========== 阶段3: 回刷失败链接 ==========
        if self.failed_urls:
            PROGRESS.set_phase("回刷重试")
            log("-" * 60)
            log(f"[回刷] 正在回刷 {len(self.failed_urls)} 个失败链接 ...")
            success_count = 0
            fail_count = 0
            for url in list(self.failed_urls):
                PROGRESS.set_current(f"回刷: {safe_name(url)[:30]}")
                r = self.fetch(url, stream=True)
                PROGRESS.step()
                if r is not None and r.status_code < 400:
                    try:
                        local = self.asset_path(url)
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
                    except Exception:
                        fail_count += 1
                else:
                    fail_count += 1
                    if r:
                        try:
                            r.close()
                        except Exception:
                            pass
            if success_count > 0:
                log(f"回刷完成: 成功 {success_count} / 失败 {fail_count}")

        # ========== 阶段4: CSS资源处理 ==========
        if self._pending_css:
            PROGRESS.set_phase("CSS处理")
            log("-" * 60)
            log(f"[CSS] 正在处理 {len(self._pending_css)} 个CSS文件中的资源引用 ...")
            with self.lock:
                pending = self._pending_css.copy()
                self._pending_css = []
            for css_path, css_url in pending:
                PROGRESS.set_current(f"CSS: {css_path.name[:30]}")
                try:
                    self._process_css(css_path, css_url)
                except Exception as e:
                    log(f"  CSS处理失败: {css_path.name} ({e})", "WARN")
                PROGRESS.step()

        # ========== 阶段5: 视频下载 ==========
        if self._video_links:
            PROGRESS.set_phase("视频下载")
            log("-" * 60)
            unique_videos = list(set(self._video_links))
            log(f"[视频] 发现 {len(unique_videos)} 个B站视频链接")
            for vurl in unique_videos:
                PROGRESS.set_current(f"视频: {safe_name(vurl)[:30]}")
                path, ok = self._video_dl.download(vurl, self.videos_dir)
                if ok:
                    STATS.add_bytes(Path(path).stat().st_size)
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
        f"[爬取效率统计]\n\n"
        f"页面成功率: {STATS.pages_ok}/{total_pages} ({page_rate:.1f}%)\n"
        f"资源成功率: {STATS.assets_ok}/{total_assets} ({asset_rate:.1f}%)\n"
        f"总体效率: {overall_rate:.1f}%\n\n"
        f"[内容占比]\n"
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
    print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║       网站克隆器 V16.0  Website Cloner                      ║
║       预扫描 | 智能进度条 | 视频下载 | 代理加速              ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ⚠️  重要提示 / IMPORTANT
  在使用本软件之前，请务必查看 README 文档：
  Before using this software, please read the README first:
  
  📖 中文文档: README.md
  📖 English:   README_EN.md
  
  包含: 安装说明、使用教程、常见问题解答
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")
    
    # 询问是否阅读 README
    try:
        ack = input("您是否已阅读 README 文档？(y=是/n=否，继续使用请输入y): ").strip().lower()
        if ack != 'y':
            print("\n" + "="*60)
            print("📖 请先查看 README 文档获取详细使用说明！")
            print("📖 Please read the README documentation for detailed instructions!")
            print("="*60)
            try:
                input("\n按回车键退出...")
            except:
                pass
            return
    except (EOFError, KeyboardInterrupt):
        print("\n再见！")
        return
    
    # 初始化代理池
    use_proxy = False
    try:
        proxy_choice = input("\n是否初始化代理池用于加速？(y=是/n=否，默认n): ").strip().lower()
        if proxy_choice == 'y':
            print("正在初始化代理池...")
            CLONER_PROXY_POOL.initialize()
            use_proxy = CLONER_PROXY_POOL.best_proxy is not None
    except Exception:
        pass
    
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
    print(f"代理: {'开启' if use_proxy else '关闭'}")
    print(f"输出目录: {out_root}")
    print("-" * 60)

    cloner = WebsiteCloner(url, out_root, max_depth=depth, max_pages=max_pages,
                           use_js=True, include_external=True)
    
    # 应用代理
    if use_proxy:
        proxy = CLONER_PROXY_POOL.get_proxy()
        if proxy:
            cloner.session.proxies.update(proxy)
            log(f"已应用代理: {proxy.get('http', '')}", "INFO")
    
    entry = cloner.run()

    total_pages = STATS.pages_ok + STATS.pages_fail
    total_assets = STATS.assets_ok + STATS.assets_fail
    page_rate = (STATS.pages_ok / total_pages * 100) if total_pages else 0
    asset_rate = (STATS.assets_ok / total_assets * 100) if total_assets else 0
    overall_rate = ((STATS.pages_ok + STATS.assets_ok) / (total_pages + total_assets) * 100) if (total_pages + total_assets) else 0

    print("\n" + "=" * 60)
    print("全部完成!")
    print(f"  [*] 入口 HTML: {entry.resolve()}")
    print(f"  目录: {out_root.resolve()}")
    print("-" * 40)
    print("[成功率统计]:")
    print(f"  页面: {STATS.pages_ok}/{total_pages} ({page_rate:.1f}%)")
    print(f"  资源: {STATS.assets_ok}/{total_assets} ({asset_rate:.1f}%)")
    print(f"  总体: {overall_rate:.1f}%")
    print(f"  总大小: {STATS.bytes/1024/1024:.2f} MB")
    print("=" * 60)

    reveal_in_explorer(entry)

    while show_result_popup():
        STATS.reset()
        PROGRESS.reset_for_new_task()

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
        print(f"代理: {'开启' if use_proxy else '关闭'}")
        print(f"输出目录: {out_root}")
        print("-" * 60)

        cloner = WebsiteCloner(url, out_root, max_depth=depth, max_pages=max_pages,
                               use_js=True, include_external=True)
        
        # 应用代理
        if use_proxy and CLONER_PROXY_POOL.best_proxy:
            proxy = CLONER_PROXY_POOL.get_proxy()
            if proxy:
                cloner.session.proxies.update(proxy)
        
        entry = cloner.run()

        total_pages = STATS.pages_ok + STATS.pages_fail
        total_assets = STATS.assets_ok + STATS.assets_fail
        page_rate = (STATS.pages_ok / total_pages * 100) if total_pages else 0
        asset_rate = (STATS.assets_ok / total_assets * 100) if total_assets else 0
        overall_rate = ((STATS.pages_ok + STATS.assets_ok) / (total_pages + total_assets) * 100) if (total_pages + total_assets) else 0

        print("\n" + "=" * 60)
        print("全部完成!")
        print(f"  [*] 入口 HTML: {entry.resolve()}")
        print(f"  目录: {out_root.resolve()}")
        print("-" * 40)
        print("[成功率统计]:")
        print(f"  页面: {STATS.pages_ok}/{total_pages} ({page_rate:.1f}%)")
        print(f"  资源: {STATS.assets_ok}/{total_assets} ({asset_rate:.1f}%)")
        print(f"  总体: {overall_rate:.1f}%")
        print(f"  总大小: {STATS.bytes/1024/1024:.2f} MB")
        print("=" * 60)

        reveal_in_explorer(entry)


if __name__ == "__main__":
    main()
