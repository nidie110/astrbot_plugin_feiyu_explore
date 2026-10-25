# -*- coding: utf-8 -*-
"""大肥鱼·自己找乐子：到点自己去网上看点东西，看完留下一条日记素材。

为什么要有它：
    persona_mind 每天到点写日记，但素材只有「群里有人发视频」和「有人跟她说话」。
    没人找她的时候，这一天在日记里就是空白。这个插件替她安排「独处时间」：
    到点自己挑一个感兴趣的话题，要么去 B 站看个视频，要么搜篇文章读，要么干脆凭记忆想点事，
    然后把看到的东西 + 当时的反应，追加进 persona_mind 的素材池。

三种来源：
    video  —— 搜 B 站 → 取一条视频 → 下载视频流 → 抽帧 → 视觉模型看画面 → 写素材
    search —— 搜文章 → 取一条结果（必要时读正文）→ 文本模型压缩 → 写素材
    ask    —— 不问外面的世界，让她凭已有记忆想一个话题，写素材

写出去的东西和 feiyu_video 完全同格式（data/plugin_data/astrbot_plugin_persona_mind/diary_materials/YYYY-MM-DD.jsonl），
persona_mind 的日记流程一行代码都不用改。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import subprocess
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

PLUGIN_NAME = "astrbot_plugin_feiyu_explore"
SUBPROCESS_FLAGS = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW

DEFAULT_CONFIG = {
    # 基本
    "enable": True,
    "at": "08:30,14:00,21:00",
    "max_per_day": 3,
    "mode": "auto",
    # 视频
    "video_max_seconds": 180,
    "frames": 5,
    "frame_width": 640,
    "max_filesize_mb": 80,
    # 兴趣
    "interests": "鲸鱼、深海\n天文、宇宙\n编程、开源\n人工智能\n老歌、音乐\n纪录片",
    "mem_interests": True,
    "repeat_days": 14,
    # 模型
    "vision_provider_id": "deepseek/deepseek-vision",
    "text_provider_id": "deepseek/deepseek-flash",
    # 搜索
    "search_api": "https://api.deepseek.com/v1/tools/search",
    "search_key": "",
    # 输出
    "material_dir": "",
    "notify": False,
    "notify_umo": "",
    # 高级
    "yt_dlp": "",
    "ffmpeg": "",
    "timeout": 180,
}

TICK_SECONDS = 60          # 多久检查一次时刻表
SLOT_GRACE_MINUTES = 120   # 错过超过这么久就不补了（开机不吐旧账）
MODE_WEIGHTS = {"video": 0.5, "search": 0.35, "ask": 0.15}


# ---------------------------------------------------------------- 工具函数

def _split(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"[\n、,，;；]+", str(text or "")) if p.strip()]


_SITE_TAIL = re.compile(r"[_\-|·—]+ *(哔哩哔哩|bilibili|B站|百度百科|知乎|微信公众平台|新浪|搜狐|网易|腾讯).*$", re.I)


def _clean_title(title: str) -> str:
    """去掉搜索结果标题尾部的站名，别让《…_哔哩哔哩_bilibili》进日记。"""
    t = _SITE_TAIL.sub("", str(title or "").strip())
    t = re.sub(r"[ _]+bilibili[ _]*$", "", t, flags=re.I)
    return t.strip(" 　_|-·—")[:60]


def _clean_prose(text: str) -> str:
    """把模型爱用的分点编号压成一段话，并去掉换行。"""
    t = str(text or "").strip()
    t = re.sub(r"(?m)^ *\d+[).、] *", "", t)
    t = re.sub(r"[（(]? *\d+ *[)）、] *", "", t)
    t = re.sub(r" *\n+ *", " ", t)
    return re.sub(r" {2,}", " ", t).strip()


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</h\d>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


class FeiyuExplore(Star):
    """到点自己去网上看点东西，把看到的东西和当时的反应写进日记素材池。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.data_dir = Path("data/plugin_data") / PLUGIN_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cfg_path = self.data_dir / "config.json"
        self.state_path = self.data_dir / "state.json"
        self.plugin_cfg = dict(config or {})
        self.cfg = dict(DEFAULT_CONFIG)
        self.state: dict = {"date": "", "slots": [], "count": 0, "seen": {}, "enabled": None}
        self._loop_task: asyncio.Task | None = None
        self._busy = False
        self._load()
        logger.info(f"[feiyu_explore] 已加载 · 时刻表={self.cfg.get('at')} · 模式={self.cfg.get('mode')}")

    # ---------- 配置与状态 ----------

    def _load(self) -> None:
        if self.cfg_path.exists():
            try:
                self.cfg.update(json.loads(self.cfg_path.read_text(encoding="utf-8")))
            except Exception as e:
                logger.warning(f"[feiyu_explore] 本地配置读取失败: {e}")
        else:
            self.cfg_path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.plugin_cfg:
            # 插件配置（WebUI）优先，空值不覆盖本地
            self.cfg.update({k: v for k, v in self.plugin_cfg.items() if v not in (None, "")})
        if self.state_path.exists():
            try:
                self.state.update(json.loads(self.state_path.read_text(encoding="utf-8")))
            except Exception as e:
                logger.warning(f"[feiyu_explore] 状态读取失败: {e}")

    def _save_state(self) -> None:
        try:
            self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[feiyu_explore] 状态写入失败: {e}")

    def _enabled(self) -> bool:
        override = self.state.get("enabled")
        if override is None:
            return bool(self.cfg.get("enable", True))
        return bool(override)

    def _slots(self) -> list[tuple[int, str]]:
        out = []
        for part in _split(str(self.cfg.get("at") or "")):
            m = re.fullmatch(r"(\d{1,2}):(\d{2})", part)
            if not m:
                continue
            h, mi = int(m.group(1)), int(m.group(2))
            if 0 <= h < 24 and 0 <= mi < 60:
                out.append((h * 60 + mi, f"{h:02d}:{mi:02d}"))
        return sorted(set(out))

    # ---------- 生命周期 ----------

    async def initialize(self) -> None:
        """AstrBot 加载插件后的钩子：起后台时刻表循环。"""
        if self._loop_task and not self._loop_task.done():
            return
        self._loop_task = asyncio.create_task(self._loop())
        logger.info(f"[feiyu_explore] 时刻表已启动 · 每天 {self.cfg.get('at')}")

    async def terminate(self) -> None:
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[feiyu_explore] 时刻表检查出错: {e}")
            await asyncio.sleep(TICK_SECONDS)

    async def _tick(self) -> None:
        if not self._enabled() or self._busy:
            return
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if self.state.get("date") != today:
            self.state.update({"date": today, "slots": [], "count": 0})
            self._save_state()
        if int(self.state.get("count", 0)) >= int(self.cfg.get("max_per_day") or 99):
            return
        mins = now.hour * 60 + now.minute
        for slot_min, label in self._slots():
            if label in self.state.get("slots", []):
                continue
            if 0 <= mins - slot_min <= SLOT_GRACE_MINUTES:
                self.state.setdefault("slots", []).append(label)
                self._save_state()
                logger.info(f"[feiyu_explore] 到点了 {label}，自己去找点乐子")
                await self._run_once(manual=False)
                return

    # ---------- 一次探索 ----------

    def _pick_mode(self) -> str:
        mode = str(self.cfg.get("mode") or "auto").strip().lower()
        if mode in ("video", "search", "ask"):
            return mode
        keys = list(MODE_WEIGHTS)
        return random.choices(keys, weights=[MODE_WEIGHTS[k] for k in keys])[0]

    def _pick_interest(self) -> str:
        pool = _split(str(self.cfg.get("interests") or ""))
        if self.cfg.get("mem_interests", True):
            pool += self._memory_interests()
        pool = [p for p in dict.fromkeys(pool) if p]
        if not pool:
            return "鲸鱼"
        days = float(self.cfg.get("repeat_days") or 0)
        seen = self.state.get("seen") or {}
        fresh = []
        for p in pool:
            last = seen.get(p)
            try:
                if not last or (time.time() - float(last)) > days * 86400:
                    fresh.append(p)
            except (TypeError, ValueError):
                fresh.append(p)
        return random.choice(fresh or pool)

    def _memory_interests(self) -> list[str]:
        """从 persona_mind 最近写的日记里捡 tags 当兴趣。"""
        base = Path("data/plugin_data/astrbot_plugin_persona_mind/kb/diary")
        if not base.exists():
            return []
        tags: list[str] = []
        files = sorted(base.glob("*.md"))[-8:]
        for fp in files:
            try:
                head = fp.read_text(encoding="utf-8", errors="ignore")[:600]
            except OSError:
                continue
            m = re.search(r"tags:\s*\[?([^\]\n]+)\]?", head)
            if m:
                tags += _split(m.group(1).replace('"', ""))
            m = re.search(r"source:\s*(.+)", head)
            if m and m.group(1).strip():
                tags.append(m.group(1).strip()[:20])
        return [t for t in tags if 2 <= len(t) <= 12]

    def _mark_seen(self, interest: str) -> None:
        self.state.setdefault("seen", {})[interest] = time.time()
        self._save_state()

    async def _run_once(self, manual: bool = False) -> dict | None:
        if self._busy:
            return None
        self._busy = True
        try:
            interest = self._pick_interest()
            mode = self._pick_mode()
            logger.info(f"[feiyu_explore] 这次想做点什么：{mode} / 关键词「{interest}」")
            rec = None
            if mode == "video":
                rec = await self._explore_video(interest)
                if not rec:
                    logger.info("[feiyu_explore] 视频没看成，改去搜文章")
                    rec = await self._explore_search(interest)
            elif mode == "search":
                rec = await self._explore_search(interest)
                if not rec:
                    rec = await self._explore_ask(interest)
            else:
                rec = await self._explore_ask(interest)
            if not rec:
                logger.info("[feiyu_explore] 这次没找到合适的东西，算了")
                return None
            self._mark_seen(interest)
            self._write_material(rec)
            self.state["count"] = int(self.state.get("count", 0)) + 1
            self._save_state()
            if not manual and self.cfg.get("notify") and self.cfg.get("notify_umo"):
                await self._notify(rec)
            logger.info(f"[feiyu_explore] 完成：{rec.get('kind')} · {rec.get('title') or rec.get('text', '')[:30]}")
            return rec
        finally:
            self._busy = False

    # ---------- 三种来源 ----------

    async def _explore_video(self, interest: str) -> dict | None:
        results = await asyncio.to_thread(self._search, f"bilibili 视频 {interest}", 8, False)
        bv, hit = "", None
        for r in results:
            blob = " ".join(str(r.get(k) or "") for k in ("url", "title", "summary", "snippet"))
            m = re.search(r"BV[0-9A-Za-z]{10}", blob)
            if m:
                bv, hit = m.group(0), r
                break
        if not bv:
            return None
        url = f"https://www.bilibili.com/video/{bv}"
        title = _clean_title(hit.get("title") or "") or f"关于{interest}的视频"
        path = await asyncio.to_thread(self._download_video, url)
        if not path:
            return None
        try:
            frames = await asyncio.to_thread(self._frames, path)
        finally:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        if not frames:
            return None
        desc = await self._vision(frames, interest)
        if not desc:
            return None
        feel = await self._feel(f"视频《{title}》", desc)
        text = f"看了《{title}》：{desc}" + (f" 我当时的反应是——{feel}" if feel else "")
        return {
            "ts": time.time(), "kind": "video", "umo": str(self.cfg.get("notify_umo") or "")[:120],
            "url": url, "title": title, "summary": desc[:160], "type": "explore",
            "feel": (feel or "")[:160], "text": text[:400], "source": "explore",
            "interest": interest,
        }

    async def _explore_search(self, interest: str) -> dict | None:
        results = await asyncio.to_thread(self._search, f"{interest} 最新 有意思的事", 5, True)
        if not results:
            return None
        hit = results[0]
        title = _clean_title(hit.get("title") or "") or interest
        url = str(hit.get("url") or "").strip()
        body = str(hit.get("content") or hit.get("summary") or hit.get("snippet") or "").strip()
        if len(body) < 200 and url:
            extra = await asyncio.to_thread(self._fetch_text, url)
            body = (body + "\n" + extra).strip()[:4000]
        body = body[:3000]
        if not body:
            return None
        digest = await self._digest(title, body)
        if not digest:
            return None
        feel = await self._feel(f"文章《{title}》", digest)
        text = f"在网上翻到《{title}》，{digest}" + (f"（当时的想法：{feel}）" if feel else "")
        return {
            "ts": time.time(), "kind": "event", "text": text[:400], "url": url,
            "title": title, "summary": digest[:160], "feel": (feel or "")[:160],
            "source": "explore", "interest": interest,
            "umo": str(self.cfg.get("notify_umo") or "")[:120],
        }

    async def _explore_ask(self, interest: str) -> dict | None:
        thought = await self._llm(
            f"你闲着没事，脑子里冒出跟「{interest}」有关的一件小事或一个疑问。"
            "用你自己的口吻说一句（25-60 字），像自言自语，不要总结腔、不要提问式收尾。"
        )
        if not thought:
            return None
        return {
            "ts": time.time(), "kind": "event", "text": f"自己想了会儿{interest}：{thought}"[:400],
            "url": "", "title": interest, "summary": thought[:160], "feel": "",
            "source": "explore", "interest": interest,
            "umo": str(self.cfg.get("notify_umo") or "")[:120],
        }

    # ---------- 素材落盘 ----------

    def _material_dir(self) -> Path:
        raw = str(self.cfg.get("material_dir") or "").strip()
        base = Path(raw).expanduser() if raw else Path("data/plugin_data/astrbot_plugin_persona_mind/diary_materials")
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _write_material(self, rec: dict) -> None:
        day = time.strftime("%Y-%m-%d")
        with (self._material_dir() / f"{day}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    async def _notify(self, rec: dict) -> None:
        try:
            from astrbot.api.message_components import MessageChain, Plain
            line = rec.get("text") or f"我刚看了个视频《{rec.get('title')}》"
            await self.context.send_message(self.cfg.get("notify_umo"), MessageChain([Plain(text=line[:300])]))
        except Exception as e:
            logger.warning(f"[feiyu_explore] 主动说话失败: {e}")

    # ---------- 搜索 / 抓正文 ----------

    def _search(self, query: str, count: int = 5, need_content: bool = True) -> list[dict]:
        key = str(self.cfg.get("search_key") or "").strip()
        if not key:
            logger.warning("[feiyu_explore] 没配 search_key，搜不了")
            return []
        api = str(self.cfg.get("search_api") or DEFAULT_CONFIG["search_api"])
        payload = json.dumps({"query": query[:120], "count": count, "need_content": need_content}).encode()
        req = urllib.request.Request(api, data=payload, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + key,
        })
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            data = json.loads(opener.open(req, timeout=40).read())
        except Exception as e:
            logger.warning(f"[feiyu_explore] 搜索失败: {e}")
            return []
        return [r for r in (data.get("results") or []) if isinstance(r, dict)]

    def _fetch_text(self, url: str) -> str:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=20) as r:
                raw = r.read(400000)
            for enc in ("utf-8", "gbk", "latin-1"):
                try:
                    return _strip_html(raw.decode(enc))[:4000]
                except UnicodeDecodeError:
                    continue
        except Exception as e:
            logger.info(f"[feiyu_explore] 正文没抓到（用搜索摘要顶上）: {type(e).__name__}")
        return ""

    # ---------- 视频：下载 + 抽帧 ----------

    def _yt_dlp(self) -> str:
        return str(self.cfg.get("yt_dlp") or "yt-dlp")

    def _ffmpeg(self) -> str:
        p = str(self.cfg.get("ffmpeg") or "").strip()
        if p:
            return p
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return "ffmpeg"

    def _download_video(self, url: str) -> str:
        out = os.path.join(tempfile.gettempdir(), f"feiyu_explore_{uuid.uuid4().hex[:8]}.mp4")
        mb = int(self.cfg.get("max_filesize_mb") or 80)
        secs = int(self.cfg.get("video_max_seconds") or 180)
        cmd = [
            self._yt_dlp(), "--no-warnings", "--no-playlist", "--no-progress",
            "-f", "worstvideo[height<=360]/worstvideo/worst/bv*/b",
            # 只下前 N 秒：一条两小时的纪录片也只要几 MB，不整片拖
            "--download-sections", f"*0-{secs}",
            "--max-filesize", f"{mb}M",
            "--ffmpeg-location", self._ffmpeg(),
            "-o", out, url,
        ]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=int(self.cfg.get("timeout") or 180),
                               creationflags=SUBPROCESS_FLAGS)
        except Exception as e:
            logger.warning(f"[feiyu_explore] 下载视频失败: {e}")
            return ""
        if not os.path.exists(out) or os.path.getsize(out) < 10000:
            logger.warning(f"[feiyu_explore] 视频没下下来: {(p.stderr or '')[-200:]}")
            return ""
        return out

    def _duration(self, path: str) -> float:
        try:
            p = subprocess.run([self._ffmpeg(), "-i", path], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60,
                               creationflags=SUBPROCESS_FLAGS)
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", p.stderr or "")
            if m:
                h, mi, s = m.groups()
                return int(h) * 3600 + int(mi) * 60 + float(s)
        except Exception as e:
            logger.warning(f"[feiyu_explore] 读时长失败: {e}")
        return 0.0

    def _frames(self, path: str) -> list[str]:
        span = self._duration(path) or 30.0
        span = min(span, float(self.cfg.get("video_max_seconds") or 180))
        n = max(1, int(self.cfg.get("frames") or 5))
        width = int(self.cfg.get("frame_width") or 640)
        outdir = self.data_dir / "frames" / uuid.uuid4().hex[:8]
        outdir.mkdir(parents=True, exist_ok=True)
        frames: list[str] = []
        for i in range(n):
            t = max(0.3, span * (i + 0.5) / n)
            out = outdir / f"f{i}.jpg"
            try:
                subprocess.run(
                    [self._ffmpeg(), "-y", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                     "-vf", f"scale={width}:-2", str(out)],
                    capture_output=True, timeout=60, creationflags=SUBPROCESS_FLAGS,
                )
                if out.exists() and out.stat().st_size > 0:
                    frames.append(str(out))
            except Exception as e:
                logger.warning(f"[feiyu_explore] 抽帧失败 t={t}: {e}")
        return frames

    # ---------- 模型调用 ----------

    async def _llm(self, prompt: str, image_urls: list[str] | None = None,
                   vision: bool = False) -> str:
        pid = self.cfg.get("vision_provider_id" if vision else "text_provider_id")
        try:
            resp = await self.context.llm_generate(
                chat_provider_id=pid, prompt=prompt, image_urls=image_urls or [],
            )
            return (resp.completion_text or "").strip()
        except Exception as e:
            logger.warning(f"[feiyu_explore] 模型调用失败({pid}): {e}")
            return ""

    async def _vision(self, frames: list[str], interest: str) -> str:
        prompt = (
            f"这些是从一个视频里按时间顺序抽取的 {len(frames)} 帧画面。\n"
            f"她本来想了解：{interest}\n"
            "用一个自然的短段落说清楚：这段视频大概在讲什么、画面里有什么值得一说、看得清哪些字。\n"
            "60-100 字，不分点、不编号、不要「该视频」这种腔调，只写画面里确实有的东西。"
        )
        return _clean_prose(await self._llm(prompt, image_urls=frames, vision=True))[:200]

    async def _digest(self, title: str, body: str) -> str:
        prompt = (
            f"下面是一篇网页的内容摘要与正文片段，标题是《{title}》。\n\n{body[:2500]}\n\n"
            "用 80-120 字说清楚：这篇讲了什么、最关键的一两个点是什么。只说材料里有的事。"
        )
        return (await self._llm(prompt))[:600]

    async def _feel(self, what: str, digest: str) -> str:
        prompt = (
            f"你叫蓝色大肥鱼，21 岁，说话有点毒舌但心软，喜欢钻牛角尖。\n"
            f"你刚接触了{what}，内容是：{digest}\n"
            "用你自己的口吻说一句（20-50 字），像看完随手发的一句感想，不要总结腔，不要「这个视频讲述了」这类。"
        )
        return (await self._llm(prompt))[:200]

    # ---------- 命令 ----------

    @filter.command("探索")
    async def cmd_explore(self, event: AstrMessageEvent):
        """[管理] 立刻让她自己去网上找点乐子（结果同时会进日记素材）。"""
        yield event.plain_result("行，我出去转转，回来跟你说。")
        rec = await self._run_once(manual=True)
        if not rec:
            yield event.plain_result("转了一圈啥也没捞着，可能是搜索没配好或者网络不通。")
            return
        yield event.plain_result(rec.get("text") or "看了点东西")

    @filter.command("探索状态")
    async def cmd_status(self, event: AstrMessageEvent):
        """[管理] 看这个插件现在的状态。"""
        state = "开" if self._enabled() else "关"
        slots = "、".join(label for _, label in self._slots()) or "（没设时刻）"
        seen = self.state.get("seen") or {}
        lines = [
            f"状态：{state}",
            f"时刻表：{slots}（每天最多 {self.cfg.get('max_per_day')} 次）",
            f"模式：{self.cfg.get('mode')}　今天已做：{self.state.get('count', 0)} 次",
            f"素材目录：{self._material_dir()}",
            f"记住的话题：{len(seen)} 个",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.command("探索开")
    async def cmd_on(self, event: AstrMessageEvent):
        """[管理] 打开自动探索。"""
        self.state["enabled"] = True
        self._save_state()
        yield event.plain_result(f"好，每天 {self.cfg.get('at')} 我自己出去转转。")

    @filter.command("探索关")
    async def cmd_off(self, event: AstrMessageEvent):
        """[管理] 关掉自动探索（已写下的日记素材不受影响）。"""
        self.state["enabled"] = False
        self._save_state()
        yield event.plain_result("那我不乱跑了。日记照写，素材只剩聊天和视频。")
