"""fnmusic-ext 管理 WebUI（fnmusic-sources 容器内第四个 supervisor 进程）。

定位：同容器内经 127.0.0.1 访问三源服务，subprocess 调 supervisorctl 切换音源
进程，直接读写挂载在 /repo 的仓库目录下的 .env（proxy 靠热重载生效）。
不挂 docker.sock、不做容器级操作。管理接口只接受飞牛网关注入的管理员身份。

前端为原生单页（static/，无构建、无 CDN 资产）。
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # /repo：复用 proxy/env_merge
from proxy.env_merge import (  # noqa: E402
    parse_env_file,
    preserve_user_comments,
    render_env,
    write_env_atomic,
)

KG_CHARTS: list[dict[str, Any]] = [
    {"id": "kg_8888", "name": "TOP500", "source": "kg", "rankid": 8888, "cover": "http://imge.kugou.com/mcommon/400/20241219/20241219164209670219.png"},
    {"id": "kg_52144", "name": "国潮音乐榜", "source": "kg", "rankid": 85897, "cover": "http://imge.kugou.com/mcommon/400/20241120/20241120202644296668.jpg"},
    {"id": "kg_52767", "name": "视频号热歌酷狗榜", "source": "kg", "rankid": 100530, "cover": "http://imge.kugou.com/mcommon/400/20260701/20260701213145443732.jpg"},
    {"id": "kg_31313", "name": "民谣榜", "source": "kg", "rankid": 51341, "cover": "http://imge.kugou.com/mcommon/400/20241211/20241211184719475668.jpg"},
    {"id": "kg_33161", "name": "纯音乐榜", "source": "kg", "rankid": 59900, "cover": "http://imge.kugou.com/mcommon/400/20241211/20241211185324343605.jpg"},
    {"id": "kg_33162", "name": "电音榜", "source": "kg", "rankid": 33160, "cover": "http://imge.kugou.com/mcommon/400/20241211/20241211184628349205.jpg"},
    {"id": "kg_23784", "name": "网络热歌榜", "source": "kg", "rankid": 82831, "cover": "http://imge.kugou.com/mcommon/400/20241219/20241219195626644029.png"},
    {"id": "kg_6666", "name": "飙升榜", "source": "kg", "rankid": 6666, "cover": "http://imge.kugou.com/mcommon/400/20241219/20241219193628550054.png"},
    {"id": "kg_52055", "name": "短视频热歌榜", "source": "kg", "rankid": 52144, "cover": "http://imge.kugou.com/mcommon/400/20260604/20260604150723458910.png"},
    {"id": "kg_46908", "name": "摇滚榜", "source": "kg", "rankid": 59896, "cover": "http://imge.kugou.com/mcommon/400/20241211/20241211184944114279.jpg"},
    {"id": "kg_24971", "name": "DJ热歌榜", "source": "kg", "rankid": 24971, "cover": "http://imge.kugou.com/mcommon/400/20241219/20241219194419154116.png"},
    {"id": "kg_54884", "name": "国乐榜", "source": "kg", "rankid": 80025, "cover": "http://imge.kugou.com/mcommon/400/20241211/20241211184535924138.png"},
    {"id": "kg_52054", "name": "百万收藏榜", "source": "kg", "rankid": 85432, "cover": "http://imge.kugou.com/mcommon/400/20241219/20241219171042182683.png"},
    {"id": "kg_59717", "name": "短视频收藏人气榜", "source": "kg", "rankid": 52767, "cover": "http://imge.kugou.com/mcommon/400/20260702/20260702101133223934.png"},
    {"id": "kg_24306", "name": "新歌榜", "source": "kg", "rankid": 74534, "cover": "http://imge.kugou.com/mcommon/400/20241219/20241219200509300768.png"},
    {"id": "kg_52895", "name": "名品堂", "source": "kg", "rankid": 84235, "cover": "http://imge.kugou.com/mcommon/400/20241219/20241219195054422404.png"},
    {"id": "kg_31308", "name": "内地榜", "source": "kg", "rankid": 31308, "cover": "http://imge.kugou.com/mcommon/400/20241211/20241211192146592398.png"},
    {"id": "kg_33163", "name": "粤语金曲榜", "source": "kg", "rankid": 33165, "cover": "http://imge.kugou.com/mcommon/400/20241219/20241219200119140556.png"},
    {"id": "kg_31310", "name": "欧美榜", "source": "kg", "rankid": 31310, "cover": "http://imge.kugou.com/mcommon/400/20241211/20241211192454699571.jpg"},
    {"id": "kg_30972", "name": "伤感榜", "source": "kg", "rankid": 51340, "cover": "http://imge.kugou.com/mcommon/400/20241219/20241219195213930623.png"},
]

WY_CHARTS: list[dict[str, Any]] = [
    {"id": "wy_19723756", "name": "飙升榜", "source": "wy", "toplist_id": 19723756, "cover": "https://p2.music.126.net/rIi7Qzy2i2Y_1QD7cd0MYA==/109951170048506929.jpg"},
    {"id": "wy_3779629", "name": "新歌榜", "source": "wy", "toplist_id": 3779629, "cover": "https://p1.music.126.net/5guhqPBTcIrrhLBotgaT6w==/109951170048511751.jpg"},
    {"id": "wy_2884035", "name": "原创榜", "source": "wy", "toplist_id": 2884035, "cover": "https://p1.music.126.net/BaP9nrocNTL3gGThysv4eQ==/109951170091896587.jpg"},
    {"id": "wy_3778678", "name": "热歌榜", "source": "wy", "toplist_id": 3778678, "cover": "https://p1.music.126.net/0SUEG8yDACfx0Bw2MYFv4Q==/109951170048519512.jpg"},
    {"id": "wy_71385702", "name": "网易云古典榜", "source": "wy", "toplist_id": 71385702, "cover": "https://p1.music.126.net/na1kEeCS1iZEkzOrs9r_9g==/109951167976973667.jpg"},
    {"id": "wy_1978921795", "name": "网易云电音榜", "source": "wy", "toplist_id": 1978921795, "cover": "https://p1.music.126.net/hXGObvXfsGtFjFvRhOYAkA==/109951170091888741.jpg"},
    {"id": "wy_991319590", "name": "网易云中文说唱榜", "source": "wy", "toplist_id": 991319590, "cover": "https://p1.music.126.net/GgHbgDfGXHpE2YTchU7IvA==/109951171510498108.jpg"},
    {"id": "wy_5338990334", "name": "实时分享榜", "source": "wy", "toplist_id": 5338990334, "cover": "https://p1.music.126.net/Mi4QPklg1mtbWAfq74tEqQ==/109951165498334721.jpg"},
    {"id": "wy_21845217", "name": "网易云全球说唱榜", "source": "wy", "toplist_id": 21845217, "cover": "https://p1.music.126.net/5wDP78s43ydVTKt62C8OjQ==/109951165613100063.jpg"},
    {"id": "wy_60198", "name": "潮流风向榜", "source": "wy", "toplist_id": 60198, "cover": "https://p1.music.126.net/rwRsVIJHQ68gglhA6TNEYA==/109951165611413732.jpg"},
    {"id": "wy_5059632704", "name": "音乐合伙人推荐榜", "source": "wy", "toplist_id": 5059632704, "cover": "https://p2.music.126.net/fFQA72TD6CR-xuaINAGzEw==/109951164631802043.jpg"},
    {"id": "wy_5059642708", "name": "音乐合伙人热歌榜", "source": "wy", "toplist_id": 5059642708, "cover": "https://p2.music.126.net/kTJC5OBhg8I477X_ZmXyDQ==/109951168539740982.jpg"},
    {"id": "wy_5059644681", "name": "音乐合伙人留名榜", "source": "wy", "toplist_id": 5059644681, "cover": "https://p1.music.126.net/YFBFNI2F-4BveUpv6FKFuw==/109951167430864069.jpg"},
    {"id": "wy_5312894314", "name": "音乐合伙人高分新歌榜", "source": "wy", "toplist_id": 5312894314, "cover": "https://p2.music.126.net/QmKJczFCnfzVguxnFvxP2g==/109951166294017311.jpg"},
    {"id": "wy_5312895267", "name": "音乐合伙人高分榜", "source": "wy", "toplist_id": 5312895267, "cover": "https://p2.music.126.net/sfcWaSciUmnfxLYoI19uZQ==/4453022092493067.jpg"},
    {"id": "wy_5453912201", "name": "黑胶VIP爱听榜", "source": "wy", "toplist_id": 5453912201, "cover": "https://p2.music.126.net/qo6-o9n5AhMjNyejev38-A==/109951169743111905.jpg"},
    {"id": "wy_71384707", "name": "网易云ACG榜", "source": "wy", "toplist_id": 71384707, "cover": "https://p1.music.126.net/urByD_AmfBDBrs7fA9-O8A==/109951167976973225.jpg"},
    {"id": "wy_745956260", "name": "网易云韩语榜", "source": "wy", "toplist_id": 745956260, "cover": "https://p2.music.126.net/5oN9YaFznwNGXkmi8i2Ytw==/109951167430864741.jpg"},
]

try:
    from proxy.charts import ALL_CHARTS as _AC, KG_CHARTS as _KC, WY_CHARTS as _WC
    if _AC:
        ALL_CHARTS, KG_CHARTS, WY_CHARTS = _AC, _KC, _WC
    else:
        ALL_CHARTS = KG_CHARTS + WY_CHARTS
except Exception:
    ALL_CHARTS = KG_CHARTS + WY_CHARTS

logger = logging.getLogger("webui_service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

SERVICE_VERSION = "2.0.0"

CONF = {
    "repo_dir": os.environ.get("WEBUI_REPO_DIR", "/repo"),
    "musicdl_url": os.environ.get("WEBUI_MUSICDL_URL", "http://127.0.0.1:8001"),
    "musicbox_url": os.environ.get("WEBUI_MUSICBOX_URL", "http://127.0.0.1:8002"),
    "lx_url": os.environ.get("WEBUI_LX_URL", "http://127.0.0.1:8003"),
    "supervisorctl": os.environ.get("WEBUI_SUPERVISORCTL", "supervisorctl"),
    "env_header": "generated by install.sh — do not commit",
}

ENV_PATH = Path(CONF["repo_dir"]) / ".env"
VERSION_PATH = Path(CONF["repo_dir"]) / "VERSION"
repo_static = Path(CONF["repo_dir"]) / "webui-service" / "static"
STATIC_DIR = repo_static if repo_static.is_dir() else (Path(__file__).resolve().parent / "static")

# 音源进程 ↔ 启用开关（三选一互斥）
PROVIDERS = {
    "musicdl": "FNMUSIC_MUSICDL_ENABLED",
    "musicbox": "FNMUSIC_NETEASE_ENABLED",
    "lxmusic": "FNMUSIC_LX_ENABLED",
}

# WebUI 可管理的配置键与元数据（kind/default/reload 驱动前端渲染与校验）。
# reload: hot=proxy 热重载即生效；process=需要切换/重启对应音源进程。
SCHEMA: dict[str, dict] = {
    "FNMUSIC_MUSICDL_ENABLED": {"kind": "bool", "default": "false", "group": "provider", "reload": "process", "label": "musicdl 聚合音源"},
    "FNMUSIC_NETEASE_ENABLED": {"kind": "bool", "default": "false", "group": "provider", "reload": "process", "label": "网易云音乐盒子"},
    "FNMUSIC_LX_ENABLED": {"kind": "bool", "default": "false", "group": "provider", "reload": "process", "label": "洛雪自定义源"},
    "FNMUSIC_ONLINE_SOURCES": {"kind": "csv", "default": "", "group": "musicdl", "reload": "process", "label": "musicdl 启用平台"},
    "MUSICDL_SOURCES": {"kind": "csv", "default": "", "group": "musicdl", "reload": "process", "label": "musicdl 服务白名单（联动）"},
    "LX_SOURCE_URL": {"kind": "str", "default": "", "group": "lx", "reload": "process", "label": "洛雪主音源（首选）"},
    "LX_SOURCE_URL_2": {"kind": "str", "default": "", "group": "lx", "reload": "process", "label": "洛雪备用源 1（主源失效时自动切换）"},
    "LX_SOURCE_URL_3": {"kind": "str", "default": "", "group": "lx", "reload": "process", "label": "洛雪备用源 2（备用兜底源）"},
    "LX_SOURCES": {"kind": "csv", "default": "kg,wy,mg,kw", "group": "lx", "reload": "hot", "label": "lx 平台（按源声明推导）"},
    "FNMUSIC_QUALITY_MODE": {"kind": "enum", "values": ["high", "balanced", "smooth"], "default": "high", "group": "quality", "reload": "hot", "label": "音质偏好"},
    "FNMUSIC_RECOMMEND_HOT": {"kind": "bool", "default": "true", "group": "recommend", "reload": "hot", "label": "热门榜单推荐"},
    "FNMUSIC_RECOMMEND_DAILY": {"kind": "bool", "default": "true", "group": "recommend", "reload": "hot", "label": "每日推荐"},
    "FNMUSIC_RECOMMEND_CHARTS": {"kind": "bool", "default": "true", "group": "recommend", "reload": "hot", "label": "排行榜歌单总开关"},
    "FNMUSIC_ENABLED_CHARTS": {"kind": "str", "default": "", "group": "recommend", "reload": "hot", "label": "自定义启用的榜单ID列表"},
    "FNMUSIC_TEE_SAVE_ENABLED": {"kind": "bool", "default": "true", "group": "tee", "reload": "hot", "label": "边听边存"},
    "FNMUSIC_TEE_SAVE_DIR": {"kind": "str", "default": "", "group": "tee", "reload": "hot", "label": "保存路径（留空自动探测）"},
    "FNMUSIC_TEE_CACHE_MAX": {"kind": "int", "default": "2", "min": 1, "max": 100, "group": "tee", "reload": "hot", "label": "关闭时滚动缓存数"},
    "FNMUSIC_FAV_AUTO_BIND": {"kind": "bool", "default": "false", "group": "tee", "reload": "hot", "label": "收藏自动绑定本地"},
    "FNMUSIC_AUTO_COVER": {"kind": "bool", "default": "true", "group": "tee", "reload": "hot", "label": "自动下载封面"},
    "FNMUSIC_LYRIC_AUTO_DL": {"kind": "bool", "default": "true", "group": "tee", "reload": "hot", "label": "自动下载歌词"},
    "FNMUSIC_OFFICIAL_BIND_TIMEOUT_S": {"kind": "int", "default": "120", "min": 10, "max": 3600, "group": "tee", "reload": "hot", "label": "官方绑定等待（秒）"},
    "FNMUSIC_TEE_HANDOFF_MAX": {"kind": "int", "default": "3", "min": 0, "max": 20, "group": "tee", "reload": "hot", "label": "切歌续传并行数"},
    "FNMUSIC_LIBRARY_SCAN_PATH": {"kind": "str", "default": "", "group": "tee", "reload": "hot", "label": "曲库重扫接口（选填）"},
    "FNMUSIC_LLM_BASE_URL": {"kind": "str", "default": "", "group": "llm", "reload": "hot", "label": "OpenAI 兼容 Base URL"},
    "FNMUSIC_LLM_API_KEY": {"kind": "secret", "default": "", "group": "llm", "reload": "hot", "label": "API Key"},
    "FNMUSIC_LLM_MODEL": {"kind": "str", "default": "gpt-4o-mini", "group": "llm", "reload": "hot", "label": "模型"},
    "FNMUSIC_SEARCH_TIMEOUT": {"kind": "int", "default": "15", "min": 1, "max": 60, "group": "search", "reload": "hot", "label": "搜索超时时间"},
    "FNMUSIC_SEARCH_PROBE": {"kind": "bool", "default": "false", "group": "search", "reload": "hot", "label": "逐曲探活(beta)"},
    "FNMUSIC_NETEASE_MY_PLAYLISTS": {"kind": "bool", "default": "false", "group": "source", "reload": "hot", "label": "网易账号歌单"},
}

_PROVIDER_KEYS = set(PROVIDERS.values())


# ------------------------------------------------------------------ .env 读写 --

def read_env() -> dict[str, str]:
    kv, _ = parse_env_file(ENV_PATH)
    return dict(kv)


def current_provider(values: dict[str, str]) -> str:
    for name, key in PROVIDERS.items():
        if values.get(key, "false").lower() in ("true", "1", "yes"):
            return name
    return ""


def write_env(updates: dict[str, str]) -> list[str]:
    """把 updates 合并进 .env（显式覆盖），原子写 + 备份 + 保留用户自定义键与注释。

    返回实际发生变化的键列表。
    """
    existing_kv, others = parse_env_file(ENV_PATH)
    existing_map = dict(existing_kv)
    changed: list[str] = []
    for key, value in updates.items():
        if existing_map.get(key) != value:
            changed.append(key)
    if not changed:
        return []
    if ENV_PATH.exists():
        # with_suffix 会把 ".env" 当后缀替换，产出 ".env.env.webui.bak" 畸形名；拼名字才是 /repo/.env.webui.bak
        backup = ENV_PATH.with_name(ENV_PATH.name + ".webui.bak")
        try:
            backup.write_bytes(ENV_PATH.read_bytes())
            os.chmod(backup, 0o600)  # write_bytes 按 umask 落盘（0644），密钥备份必须收紧
        except OSError as exc:  # 备份失败不阻断写入，但要有迹可循
            logger.warning("webui env backup failed: %s", exc)
    merged = [(k, updates.get(k, v)) if k in updates else (k, v) for k, v in existing_kv]
    seen = {k for k, _ in merged}
    merged.extend((k, v) for k, v in updates.items() if k not in seen)
    write_env_atomic(
        ENV_PATH,
        render_env(
            merged,
            CONF["env_header"],
            trailing=preserve_user_comments(others, header=CONF["env_header"]),
        ),
    )
    return changed


# ------------------------------------------------------------------ supervisor --

def supervisorctl(*args: str, timeout: float = 20.0) -> tuple[int, str]:
    cmd = [CONF["supervisorctl"], *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "supervisorctl not found"
    except subprocess.TimeoutExpired:
        return 124, f"supervisorctl {' '.join(args)} timed out"
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


def supervisor_status() -> dict[str, dict]:
    # supervisorctl status 的退出码语义是“是否全部 RUNNING”（存在 STOPPED 即 3、
    # 存在 FATAL 即 4），按需加载架构下未选中的音源常驻 STOPPED 是正常态，
    # 不能用退出码判成败：只认输出里能解析出的状态行；连接失败等异常输出
    # 解析不出任何状态行，自然返回空表（前端显示未知）。
    _, out = supervisorctl("status")
    result: dict[str, dict] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ("RUNNING", "STOPPED", "STARTING", "FATAL", "BACKOFF", "EXITED"):
            result[parts[0]] = {"state": parts[1], "detail": " ".join(parts[2:])}
    return result


def switch_provider_process(old: str, new: str) -> list[dict]:
    """切源进程：先停旧再起新；失败逐项记录，不抛出。"""
    actions: list[dict] = []
    if old and old != new:
        code, out = supervisorctl("stop", old)
        actions.append({"kind": "process", "program": old, "op": "stop",
                        "ok": code == 0, "error": "" if code == 0 else out})
    if new:
        code, out = supervisorctl("start", new)
        actions.append({"kind": "process", "program": new, "op": "start",
                        "ok": code == 0, "error": "" if code == 0 else out})
    return actions


# ------------------------------------------------------------------ 音源预览（临时拉起） --
# WebUI 点选未启用的音源卡片时临时拉起对应进程（仅 supervisorctl，不写 .env），
# 让 lx 测试 / musicdl 平台列表 / 网易扫码立即可用：
# - 保存后转正（正式启用）或被立即清退（api_config 保存路径 reconcile）；
# - 只点选未保存的，PREVIEW_TTL 秒无访问后自动停止（选平台/测试源/扫码会续期）。
PREVIEW_TTL = 300.0
_preview_until: dict[str, float] = {}

PROVIDER_PROGRAM = {"musicdl": "musicdl", "musicbox": "musicbox", "lxmusic": "lxmusic"}
PROVIDER_HEALTH = {"musicdl": CONF["musicdl_url"], "musicbox": CONF["musicbox_url"], "lxmusic": CONF["lx_url"]}


def preview_seconds_left(provider: str) -> float:
    deadline = _preview_until.get(provider)
    return max(0.0, deadline - time.monotonic()) if deadline else 0.0


def preview_renew(provider: str) -> None:
    if provider in _preview_until:
        _preview_until[provider] = time.monotonic() + PREVIEW_TTL


def preview_reap() -> list[str]:
    """清退到期预览：已随保存启用的转正（移出预览表，进程常驻），其余停止。

    stop 失败（supervisorctl 抖动/超时）不 pop 表项：deadline 仍过期，
    下一轮 reaper 会重试——否则预览进程漏停后常驻，无人再管。
    """
    stopped: list[str] = []
    enabled = current_provider(read_env())
    for provider, deadline in list(_preview_until.items()):
        if provider == enabled:
            _preview_until.pop(provider, None)
        elif deadline <= time.monotonic():
            code, out = supervisorctl("stop", PROVIDER_PROGRAM[provider])
            if code == 0:
                _preview_until.pop(provider, None)
                logger.info("预览到期，停止音源进程 %s", provider)
                stopped.append(provider)
            else:
                logger.warning("预览到期但停止失败（下轮重试）%s: %s", provider, out)
    return stopped


def preview_reconcile_after_save() -> list[dict]:
    """保存成功后的收尾：预览转正的进程保留，其余预览进程立即停止。"""
    enabled = current_provider(read_env())
    actions: list[dict] = []
    for provider in list(_preview_until):
        if provider == enabled:
            _preview_until.pop(provider, None)
            continue
        _preview_until.pop(provider, None)
        code, out = supervisorctl("stop", PROVIDER_PROGRAM[provider])
        actions.append({"kind": "process", "program": PROVIDER_PROGRAM[provider], "op": "stop",
                        "ok": code == 0, "error": "" if code == 0 else out, "preview": True})
    return actions


# ------------------------------------------------------------------ 校验 --

def _normalize_value(key: str, raw) -> str:
    spec = SCHEMA[key]
    kind = spec["kind"]
    if kind == "bool":
        if isinstance(raw, bool):
            return "true" if raw else "false"
        text = str(raw).strip().lower()
        if text in ("true", "1", "yes", "on"):
            return "true"
        if text in ("false", "0", "no", "off", ""):
            return "false"
        raise ValueError(f"{key}: 期望 true/false，收到 {raw!r}")
    if kind == "int":
        try:
            num = int(str(raw).strip())
        except ValueError as exc:
            raise ValueError(f"{key}: 期望整数，收到 {raw!r}") from exc
        num = max(int(spec.get("min", num)), min(int(spec.get("max", num)), num))
        return str(num)
    if kind == "enum":
        text = str(raw).strip()
        if text not in spec["values"]:
            raise ValueError(f"{key}: 可选值 {'/'.join(spec['values'])}，收到 {raw!r}")
        return text
    if kind == "csv":
        items: list[str] = []
        if isinstance(raw, list):
            raw_list = [str(x) for x in raw]
        else:
            raw_list = [x for x in str(raw).replace("，", ",").split(",")]
        for item in raw_list:
            item = item.strip()
            if item and item not in items:
                items.append(item)
        return ",".join(items)
    return str(raw).strip()


def validate_updates(values: dict) -> dict[str, str]:
    updates: dict[str, str] = {}
    for key, raw in (values or {}).items():
        if key not in SCHEMA:
            raise ValueError(f"不支持的配置键: {key}")
        updates[key] = _normalize_value(key, raw)
    # 三选一互斥：以"应用后的最终状态"判断
    final = dict(read_env())
    final.update(updates)
    enabled = [name for name, key in PROVIDERS.items()
               if final.get(key, SCHEMA[key]["default"]).lower() in ("true", "1", "yes")]
    if len(enabled) > 1:
        raise ValueError(f"音源三选一：{'、'.join(enabled)} 同时启用，请只保留一个")
    if updates.keys() & _PROVIDER_KEYS and not enabled:
        raise ValueError("至少需要启用一个音源")
    return updates


# ------------------------------------------------------------------ HTTP 客户端 --

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    async def _preview_reaper():
        while True:
            await asyncio.sleep(15.0)
            try:
                preview_reap()
            except Exception:  # noqa: BLE001
                logger.exception("预览清退循环异常")
    reaper = asyncio.create_task(_preview_reaper())
    try:
        yield
    finally:
        reaper.cancel()
        client = getattr(_app.state, "http", None)
        if client is not None:
            await client.aclose()
            _app.state.http = None


app = FastAPI(title="fnmusic-webui", version=SERVICE_VERSION, lifespan=_lifespan)


def get_http(request: Request) -> httpx.AsyncClient:
    client = getattr(request.app.state, "http", None)
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0))
        request.app.state.http = client
    return client


def _resp_json(resp: httpx.Response) -> dict:
    """防御性解析同容器服务的响应体：上游裸 500 等非 JSON 文本时返回带 error 的
    字典而不是抛 JSONDecodeError（否则自身也会 500，用户只能看到 "HTTP 500"）。"""
    try:
        return resp.json() if resp.content else {}
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": f"上游返回非 JSON 响应（HTTP {resp.status_code}）"}


async def _fetch_json(request: Request, url: str, *, timeout: float = 4.0) -> "tuple[bool, dict]":
    client = get_http(request)
    try:
        resp = await client.get(url, timeout=timeout)
        data = _resp_json(resp)
        return resp.status_code < 400 and data.get("ok", True), data
    except Exception as exc:  # noqa: BLE001
        return False, {"error": str(exc)}


def _read_version() -> str:
    try:
        return VERSION_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return SERVICE_VERSION


# ------------------------------------------------------------------ API --

@app.get("/healthz")
async def healthz():
    return {"ok": True}


def _header_map(scope) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in scope.get("headers") or []:
        out[key.decode("latin-1").lower()] = value.decode("latin-1")
    return out


def _is_admin(headers: dict[str, str]) -> bool:
    return headers.get("x-trim-isadmin", "").lower() == "true"


@app.get("/api/status")
async def api_status(request: Request):
    values = read_env()
    provider = current_provider(values)
    processes = supervisor_status()
    services: dict[str, dict] = {}
    for name, base in (("musicdl", CONF["musicdl_url"]), ("musicbox", CONF["musicbox_url"]),
                       ("lxmusic", CONF["lx_url"])):
        if name in PROVIDERS and name != provider:
            services[name] = {"reachable": False, "note": "未启用（按需未启动）"}
            continue
        ok, data = await _fetch_json(request, f"{base}/healthz")
        services[name] = {"reachable": ok, "detail": data if ok else data.get("error", "")}
    lx_source = None
    if provider == "lxmusic":
        ok, data = await _fetch_json(request, f"{CONF['lx_url']}/api/v1/source")
        lx_source = data.get("data") if ok else {"error": data.get("error", "unreachable")}
    return {
        "ok": True,
        "version": _read_version(),
        "deploy_mode": values.get("FNMUSIC_DEPLOY_MODE", "docker"),
        "current_provider": provider or "none",
        "processes": {name: processes.get(name, {"state": "UNKNOWN", "detail": ""})
                      for name in ("musicdl", "musicbox", "lxmusic", "webui")},
        "previews": {p: round(preview_seconds_left(p)) for p in list(_preview_until)},
        "services": services,
        "lx_source": lx_source,
    }


@app.get("/api/config")
async def api_config():
    values = read_env()
    return {
        "ok": True,
        "values": {key: values.get(key, spec["default"]) for key, spec in SCHEMA.items()},
        "schema": SCHEMA,
        "env_path": str(ENV_PATH),
    }


class ConfigBody(BaseModel):
    values: dict


class PreviewBody(BaseModel):
    provider: str


@app.post("/api/preview")
async def api_preview(body: PreviewBody, request: Request):
    """点选未启用的音源卡片：临时拉起进程供预览（不写 .env，倒计时自动停止）。"""
    provider = body.provider
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="provider 必须是 musicdl/musicbox/lxmusic 之一")
    if current_provider(read_env()) == provider:
        return {"ok": True, "preview": False, "note": "该音源已启用，进程常驻"}
    if preview_seconds_left(provider) > 0:
        preview_renew(provider)
        return {"ok": True, "preview": True, "seconds_left": preview_seconds_left(provider)}
    code, out = supervisorctl("start", PROVIDER_PROGRAM[provider])
    if code != 0:
        return JSONResponse(content={"ok": False, "error": out or "supervisorctl start 失败"}, status_code=500)
    client = get_http(request)
    for _ in range(60):  # 等待服务真正可用（healthz），最长约 30s
        try:
            r = await client.get(f"{PROVIDER_HEALTH[provider]}/healthz", timeout=2.0)
            if r.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    _preview_until[provider] = time.monotonic() + PREVIEW_TTL
    return {"ok": True, "preview": True, "seconds_left": PREVIEW_TTL}


@app.put("/api/config")
async def api_config_put(body: ConfigBody, request: Request):
    before = read_env()
    try:
        updates = validate_updates(body.values)
    except ValueError as exc:
        return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=400)

    # lx 换源前置校验：新配置的 URL 必须先通过 lxmusic verify 才写入
    lx_slots_config = [
        ("LX_SOURCE_URL", 0, "主音源"),
        ("LX_SOURCE_URL_2", 1, "备用源 1"),
        ("LX_SOURCE_URL_3", 2, "备用源 2"),
    ]
    after_preview = dict(before)
    after_preview.update(updates)
    is_lx = current_provider(after_preview) == "lxmusic"
    all_platforms: list[str] = []

    for key, slot, label in lx_slots_config:
        val_changed = updates.get(key) not in (None, before.get(key, ""))
        new_val = (updates.get(key) if key in updates else before.get(key, "")).strip()
        if is_lx and val_changed and new_val:
            client = get_http(request)
            try:
                resp = await client.post(f"{CONF['lx_url']}/api/v1/source/verify",
                                         json={"url": new_val}, timeout=130.0)
                report = _resp_json(resp)
            except Exception as exc:  # noqa: BLE001
                report = {"ok": False, "data": {"message": str(exc)}}
            if not report.get("ok"):
                message = (report.get("data") or {}).get("message") or report.get("error") or "校验失败"
                return JSONResponse(
                    content={"ok": False, "error": f"洛雪{label}校验未通过：{message}"}, status_code=400
                )
            for p in (report.get("data") or {}).get("platforms") or []:
                if p not in all_platforms:
                    all_platforms.append(p)
    if all_platforms:
        updates.setdefault("LX_SOURCES", ",".join(all_platforms))

    old_provider = current_provider(before)
    changed = write_env(updates)
    if not changed:
        return {"ok": True, "changed": [], "actions": [], "note": "配置无变化"}

    after = read_env()
    new_provider = current_provider(after)
    actions: list[dict] = []

    # 音源切换（先停旧再起新）
    if old_provider != new_provider:
        actions.extend(switch_provider_process(old_provider, new_provider))
    # lx 换源激活：热切换 SOURCE_MANAGER 各槽位
    if new_provider == "lxmusic":
        client = get_http(request)
        for key, slot, label in lx_slots_config:
            val_changed = updates.get(key) not in (None, before.get(key, ""))
            if val_changed or (old_provider != "lxmusic" and after.get(key)):
                slot_url = (after.get(key) or "").strip()
                if slot_url:
                    try:
                        resp = await client.post(f"{CONF['lx_url']}/api/v1/source",
                                                 json={"url": slot_url, "slot": slot}, timeout=130.0)
                        payload = _resp_json(resp)
                        ok = resp.status_code == 200 and payload.get("ok", False)
                        err = "" if ok else (payload.get("error") or f"HTTP {resp.status_code}")
                    except Exception as exc:  # noqa: BLE001
                        ok, err = False, str(exc)
                    actions.append({"kind": "lx_activate" if slot == 0 else f"lx_activate_slot_{slot}", "slot": slot, "ok": ok, "error": err or ""})
                elif val_changed and before.get(key):
                    try:
                        resp = await client.delete(f"{CONF['lx_url']}/api/v1/source?slot={slot}", timeout=30.0)
                        ok = resp.status_code == 200
                        err = ""
                    except Exception as exc:  # noqa: BLE001
                        ok, err = False, str(exc)
                    actions.append({"kind": f"lx_clear_slot_{slot}", "slot": slot, "ok": ok, "error": err or ""})
    elif new_provider == "musicdl" and (
        "FNMUSIC_ONLINE_SOURCES" in changed or "MUSICDL_SOURCES" in changed
    ):
        code, out = supervisorctl("restart", "musicdl")
        actions.append({"kind": "process", "program": "musicdl", "op": "restart",
                        "ok": code == 0, "error": "" if code == 0 else out})

    # 预览收尾：保存启用的转正常驻，其余预览进程立即停止
    actions.extend(preview_reconcile_after_save())

    restart_keys = [k for k in changed if SCHEMA.get(k, {}).get("reload") == "restart"]
    return {"ok": True, "changed": changed, "actions": actions, "restart_keys": restart_keys}


@app.post("/api/lx/verify")
async def api_lx_verify(body: ConfigBody, request: Request):
    preview_renew("lxmusic")  # 预览期间测试源视为活跃，续期倒计时
    url = str((body.values or {}).get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://", "file://")):
        raise HTTPException(status_code=400, detail="url 必须以 http:// 、https:// 或 file:// 开头")
    client = get_http(request)
    try:
        resp = await client.post(f"{CONF['lx_url']}/api/v1/source/verify",
                                 json={"url": url}, timeout=130.0)
        return JSONResponse(content=_resp_json(resp) or {"ok": False},
                            status_code=resp.status_code)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"lxmusic 服务不可达: {exc}") from exc


def _lx_script_limits(filename: str, script: str) -> None:
    if not (filename or "").strip().lower().endswith(".js"):
        raise HTTPException(status_code=400, detail="只支持 .js 后缀的洛雪源脚本文件")
    if len(script.encode("utf-8")) > 9_000_000:
        raise HTTPException(status_code=400, detail="脚本超过 9MB 大小上限")


@app.post("/api/lx/upload")
async def api_lx_upload(request: Request):
    """上传 .js：管理页/NAS 选择发 JSON {filename, script}，也接受 multipart 文件。

    校验后转给 lxmusic 落盘，返回 file:// URL。
    """
    preview_renew("lxmusic")
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="请求体必须是 JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
        filename = str(payload.get("filename") or "")
        script = payload.get("script")
        if not isinstance(script, str) or not script:
            raise HTTPException(status_code=400, detail="缺少 script")
    elif "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "filename"):
            raise HTTPException(status_code=400, detail="缺少 file 字段")
        filename = upload.filename or ""
        raw = await upload.read()
        if len(raw) > 9_000_000:
            raise HTTPException(status_code=400, detail="脚本超过 9MB 大小上限")
        try:
            script = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="脚本不是有效的 UTF-8 文本") from exc
    else:
        raise HTTPException(
            status_code=400, detail="请用 application/json 或 multipart/form-data 上传文件")
    _lx_script_limits(filename, script)
    client = get_http(request)
    try:
        resp = await client.post(f"{CONF['lx_url']}/api/v1/source/upload",
                                 json={"filename": filename, "script": script},
                                 timeout=30.0)
        return JSONResponse(content=_resp_json(resp) or {"ok": False},
                            status_code=resp.status_code)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"lxmusic 服务不可达: {exc}") from exc


@app.get("/api/platforms")
async def api_platforms(request: Request):
    preview_renew("musicdl")  # 预览期间查看平台列表视为活跃，续期倒计时
    ok, data = await _fetch_json(request, f"{CONF['musicdl_url']}/sources")
    if not ok:
        return JSONResponse(
            content={"ok": False, "error": data.get("error", "musicdl 服务不可达"),
                     "hint": "musicdl 进程未启用时无法获取平台列表"},
            status_code=503,
        )
    return {"ok": True, "enabled": data.get("enabled") or [], "registered": data.get("registered") or []}


@app.get("/api/charts")
async def api_charts():
    env = read_env()
    charts_enabled = env.get("FNMUSIC_RECOMMEND_CHARTS", "true").lower() in ("true", "1", "yes")
    enabled_charts_str = env.get("FNMUSIC_ENABLED_CHARTS", "").strip()
    enabled_set = {x.strip() for x in enabled_charts_str.split(",") if x.strip()} if enabled_charts_str else None

    kg_list = []
    for c in KG_CHARTS:
        item = dict(c)
        item["enabled"] = (enabled_set is None) or (c["id"] in enabled_set)
        kg_list.append(item)

    wy_list = []
    for c in WY_CHARTS:
        item = dict(c)
        item["enabled"] = (enabled_set is None) or (c["id"] in enabled_set)
        wy_list.append(item)

    return {
        "ok": True,
        "charts_enabled": charts_enabled,
        "kg": kg_list,
        "wy": wy_list,
        "total": len(ALL_CHARTS),
    }


# ------------------------------------------------- 网易扫码（反代 musicbox） --

@app.api_route("/api/netease/auth/{path:path}", methods=["GET", "POST"])
async def netease_auth_proxy(path: str, request: Request):
    """透传 musicbox 的 auth 接口（登录/轮询/状态），路径段白名单内。"""
    if path not in ("login", "login/check", "status"):
        raise HTTPException(status_code=404, detail="unknown auth endpoint")
    preview_renew("musicbox")  # 预览期间扫码/查状态视为活跃，续期倒计时
    target = f"{CONF['musicbox_url']}/api/v1/auth/{path}"
    client = get_http(request)
    try:
        resp = await client.request(
            request.method, target,
            params=dict(request.query_params),
            json=(await request.json()) if request.method == "POST" and await request.body() else None,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"musicbox 服务不可达: {exc}") from exc
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json"))


@app.get("/api/netease/qr")
async def netease_qr(unikey: str = Query(...)):
    """把登录链接渲染为 SVG 二维码（无 PIL 依赖，前端直接 <img> 展示）。"""
    if not unikey.strip():
        raise HTTPException(status_code=400, detail="unikey cannot be empty")
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError as exc:
        raise HTTPException(status_code=501, detail="qrcode not installed") from exc
    url = f"https://music.163.com/login?codekey={unikey.strip()}"
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=12)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


# ------------------------------------------------------------------ 静态前端 --

@app.get("/")
async def index():
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate, max-age=0"},
    )


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 飞牛桌面 iframe 不能嵌 http://主机:8774（桌面是 HTTPS，混合内容会被浏览器丢掉，窗口一片空白）。
# 入口改走网关同源路径 /app/fnmusic-ext，这里把该前缀剥掉，直连 :8774 的 /api 不受影响。
DESKTOP_PREFIX = "/app/fnmusic-ext"


class DesktopPrefixMiddleware:
    def __init__(self, app, prefix: str = DESKTOP_PREFIX):
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path") or ""
            if path == self.prefix or path.startswith(self.prefix + "/"):
                scope = dict(scope)
                stripped = path[len(self.prefix):] or "/"
                scope["path"] = stripped
                if isinstance(scope.get("raw_path"), (bytes, bytearray)):
                    scope["raw_path"] = stripped.encode("ascii")
        await self.app(scope, receive, send)


class AuthMiddleware:
    """管理接口要求飞牛网关注入的管理员身份。桌面前缀由外层中间件先剥掉。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if not path.startswith("/api/") or _is_admin(_header_map(scope)):
            await self.app(scope, receive, send)
            return
        body = '{"ok":false,"error":"需要管理员"}'.encode()
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [
                [b"content-type", b"application/json; charset=utf-8"],
                [b"content-length", str(len(body)).encode()],
            ],
        })
        await send({"type": "http.response.body", "body": body})


class NoCacheMiddleware:
    """禁止浏览器和桌面端缓存静态资源与接口，保证升级后界面即时生效。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                raw_headers = list(message.get("headers", []))
                # 剔除已有的缓存控制头并追加强力防缓存头
                headers = [
                    h for h in raw_headers
                    if h[0].lower() not in (b"cache-control", b"pragma", b"expires")
                ]
                headers.append((b"cache-control", b"no-cache, no-store, must-revalidate, max-age=0"))
                headers.append((b"pragma", b"no-cache"))
                headers.append((b"expires", b"0"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(AuthMiddleware)
app.add_middleware(DesktopPrefixMiddleware)
app.add_middleware(NoCacheMiddleware)
