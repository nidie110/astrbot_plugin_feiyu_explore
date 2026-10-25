# Changelog

## 1.0.1 (2026-10-25)

- 下载/抽帧/搜索改用 _feiyu_kit，去掉重复实现。
- 新增 `_feiyu_kit.py`（vendor 方式共用，`sync_kit.py` 同步），功能与配置键不变。

# 更新日志

## 1.0.0 — 2026-10-25

首个版本。

- 新增定时自主探索：每天在 `at` 指定的时刻自己挑一个兴趣话题去网上看点东西
- 三种来源：`video`（搜 B 站 → 下载视频流 → 抽帧 → 视觉模型看画面）、`search`（搜文章 → 压缩摘要）、`ask`（凭记忆自己想）
- `auto` 模式按 50% / 35% / 15% 的比例轮换三种来源；视频失败自动降级为搜文章
- 兴趣词双来源：配置里的固定词 + 从 persona_mind 最近日记的 tags / source 里捡
- 同一话题 `repeat_days` 天内不重复（状态存 `state.json`）
- 产出直接写进 persona_mind 的日记素材池（`diary_materials/YYYY-MM-DD.jsonl`），格式与 feiyu_video 一致，对方零改动
- 指令：`/探索`、`/探索状态`、`/探索开`、`/探索关`
- 可选主动说话（`notify` + `notify_umo`），默认关闭

## 1.0.2 (2026-10-25)

- 下载/抽帧/搜索走 kit 新版：错误页不再当视频（<10KB 判失败）、日志保留真因、requirements 补 Pillow。
- 插件侧 import 前清 `sys.modules['_feiyu_kit']` 缓存，改用新 kit 不必整进程重启也能生效（稳妥起见仍建议重启）。
