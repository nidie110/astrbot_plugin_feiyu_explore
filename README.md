# 大肥鱼·自己找乐子

到点自己去网上看点东西，看完留下一条日记素材。

配套 [astrbot_plugin_persona_mind](https://github.com/nidie110/astrbot_plugin_persona_mind) 使用：那个插件负责「每天到点把当天素材写成日记」，这个插件负责「把素材喂饱和」——群里没人说话、没人发视频的日子，她自己也会出去逛一圈。

## 人格设定里要写什么（装了之后必看）

这个插件**不直接提供给模型的工具**，它把自己的动作注册进
[`astrbot_plugin_persona_mind`](../astrbot_plugin_persona_mind) 的 `feiyu` 入口。所以：

1. **必须同时装 `persona_mind`。** 只装本插件时，模型那边一个动作都看不到 —— 不是坏了，是没有入口。
2. **人格设定里只写「什么时候该动手」，不要写 `feiyu(...)`、`action=`、`target=` 或任何参数名。**
   参数格式由插件在真要动手的那一轮临时告诉她（`engine/specs.py` + `engine/selector.py`），
   写进人格只会变成「同一件事两处说法」，而且插件启停时人格不会跟着变。
3. 人格里有这么几行，本插件才会被自然调用：

- 到点自己找乐子（默认 08:30 / 14:00 / 21:00）—— **不经过 `feiyu`**，走插件自己的定时器
- 人格里写的是「到点你会去」，不是「我这就去」。他提这件事，就说时间点或者「等主人说」

### 可以直接粘贴的一段

只装本插件时，把下面这几行加进人格设定的末尾就够了：

```
- 自己找乐子（08:30 / 14:00 / 21:00）、写日记（22:40）是按点发生的，不是伸手就能做；他提这个就说时间点
```

**不要**改成 `feiyu(action="...")` 那种写法——参数格式由插件在真要动手的那一轮临时给她。

**反例**（这些不该触发）：裸的「说说」、「评论区」单独出现、她说「我去看看」但没给具体目标。

完整的原理、可逐字复制的人格段落、写入脚本，见 [persona_mind/README.md](../astrbot_plugin_persona_mind/README.md#让她自然地用这些本事人格设定里要写什么)。


## 怎么用

| 你这么说 | 她做什么 |
| --- | --- |
| 「你平时自己都看些什么」 | 报她的探索时刻表（08:30 / 14:00 / 21:00）和最近看过的东西 |
| 「去看看有什么好玩的」 | 立刻跑一次探索 |
| 「别自己乱看了」 | 关掉自动探索 |

**指令形式**：`/探索`（立刻跑一次）、`/探索状态`（下次什么时候）、`/探索开`、`/探索关`

**注意**：这是**唯一会自己动的插件**——到点它会自己上网找东西看，并把看到的东西写进当天的日记素材。不想让它自己跑就用 `/探索关`。

## 为什么需要它

`persona_mind` 的日记素材原本只有两个来源：群里有人发视频、有人跟她说话。没人找她的时候，那一天在日记里就是空白。这个插件给她安排「独处时间」：到点挑一个感兴趣的话题，去看、去读、去想，把看到的东西和当时的反应留下来。

## 三种来源

| 模式 | 做什么 | 成本 |
| --- | --- | --- |
| `video` | 搜 B 站 → 取一条视频 → 下载视频流 → 抽 5 帧 → 视觉模型看画面 → 写素材 | 最高（视觉 token + 流量） |
| `search` | 搜文章 → 取最相关一条（必要时抓正文）→ 文本模型压成 80~120 字 | 低 |
| `ask` | 不问外面，让她凭已有记忆想一件跟兴趣有关的小事 | 最低 |

`mode=auto`（默认）按 50% / 35% / 15% 轮换。`video` 失败（没装 yt-dlp、视频下不下来、视觉模型报错）会自动降级成 `search`，不会白跑一趟。

## 素材怎么进日记

产出的记录直接追加到 `data/plugin_data/astrbot_plugin_persona_mind/diary_materials/YYYY-MM-DD.jsonl`，格式与 `feiyu_video` 完全一致：

- 视频：`kind=video`，`title / summary / feel / url`
- 搜文章与自己想：`kind=event`，`text`

`persona_mind` 那边的 `build_prompt` 本来就认这三种 kind，一行代码都不用改。到 `diary_at`（默认 22:40）她照常写日记，这些素材自然就出现在「今天留下的这些事」里。

## 安装

1. 把整个目录放进 `data/plugins/astrbot_plugin_feiyu_explore/`
2. 装依赖：`pip install -r requirements.txt`（`imageio-ffmpeg` 自带 ffmpeg）
3. `video` 模式还需要 [yt-dlp](https://github.com/yt-dlp/yt-dlp)：`pip install yt-dlp`，或下载 `yt-dlp.exe` 后把路径填进 `yt_dlp`
4. 在 WebUI 插件配置里填 `search_key`（和 `feiyu_browse` 用的是同一个搜索接口）
5. 重载插件

## 指令

| 指令 | 作用 |
| --- | --- |
| `/探索` | 立刻来一次（结果同时写进素材池） |
| `/探索状态` | 看开关、时刻表、今天做了几次、素材目录 |
| `/探索开` / `/探索关` | 临时开关；状态存 `state.json`，重启后仍然有效 |

`/探索关` 只停自动探索，已经写下的素材不受影响，手动 `/探索` 也仍然可用。

## 配置

分组见 WebUI 插件配置页，全部键都能在 `_conf_schema.json` 里找到说明。常用的几个：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enable` | true | 自动探索总开关 |
| `at` | `08:30,14:00,21:00` | 每天的探索时刻；错过超过 2 小时不补，避免开机吐旧账 |
| `max_per_day` | 3 | 一天最多几次 |
| `mode` | auto | `auto` / `video` / `search` / `ask` |
| `interests` | 鲸鱼深海、天文、编程、AI、老歌、纪录片 | 一行一组，顿号分隔 |
| `mem_interests` | true | 从最近 8 篇日记的 tags / source 里捡兴趣词 |
| `repeat_days` | 14 | 同一话题多少天内不重复 |
| `video_max_seconds` | 180 | 抽帧只在这个时间跨度里均匀取 |
| `frames` | 5 | 抽几帧给视觉模型 |
| `vision_provider_id` | deepseek/deepseek-vision | 看画面用 |
| `text_provider_id` | deepseek/deepseek-flash | 摘要和「当时的反应」用 |
| `material_dir` | 空 | 留空即用 persona_mind 的默认素材目录 |
| `notify` / `notify_umo` | false / 空 | 探索完主动在那个会话说一句 |

## 工作流程

```
时刻表 tick(60s)
   └─ 到点 & 今天还没做够 → 挑兴趣词（配置 + 日记 tags）→ 选来源
        ├─ video  : 搜索 → BV 号 → yt-dlp 下 360p → ffmpeg 抽帧 → 视觉模型 → 反应
        ├─ search : 搜索 → 取一条 → 文本模型压缩 → 反应
        └─ ask    : 文本模型自言自语
   └─ 写 diary_materials/YYYY-MM-DD.jsonl ＋ 记 state（今天做了几次、这个兴趣什么时候用过）
```

## 隐私与边界

- 只读网上公开内容，不登录、不评论、不互动；B 站只拿公开视频流。
- 下载的视频放系统临时目录，抽完帧立刻删除；抽出来的帧留在 `data/plugin_data/astrbot_plugin_feiyu_explore/frames/`，可按需清理。
- 所有状态（今天做了几次、哪些话题用过）只存本地 `state.json`。
- 不主动给任何人发消息，除非你打开 `notify` 并指定 `notify_umo`。

## 许可

MIT
