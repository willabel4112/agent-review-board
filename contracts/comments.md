# 接缝契约 v2 — server ↔ session (v0 2026-08-05 冻结; v1 2026-08-14 回合化; v2 2026-08-23 飞书化)

改本契约 = 先改这里, 再改两边实现 (SE floor §4)。
**v1/v2 全部增量向后兼容**: 旧版写下的行照旧解析, 缺的新字段一律走 legacy 分支。
v2 新增见文末「v2 增量」节。

## 文件 (doc 同目录 sidecar, git 追踪)

| 文件 | 唯一写者 | 内容 |
|---|---|---|
| `<doc>.comments.jsonl` | review-board server (人的 UI 动作) | comment / submit / resolve / reopen / **edit / drop / approve / rev(by=server)** |
| `<doc>.replies.jsonl` | agent session (boardctl.py) | reply / answer / **rev(by=agent)** |

队列语义 (继承 board-comment-queue idea): **append-only** (永不改已有行) ·
**single-writer-per-file** · **ID + 幂等消费** (at-least-once)。读方合并两文件按
`ts` 折叠。坏行跳过不炸 (容错解析是契约)。

## 事件行 schema

```jsonl
{"type":"comment","id":"c-0001","ts":1754400000.0,"author":"Reviewer","kind":"block",
 "anchor":{"slug":"iter1-接线","hash":"ab12ef34","quote":"…原文前120字…","rev":"9f3a…"},"text":"…"}
{"type":"submit","batch":1,"ts":…,"ids":["c-0001","c-0002"]}
{"type":"resolve","id":"c-0001","ts":…}
{"type":"reopen","id":"c-0001","ts":…}
{"type":"edit","re":"c-0001","ts":…,"text":"改过的正文"}
{"type":"drop","re":"c-0001","ts":…}
{"type":"approve","batch":2,"rev":"7c1d…","ts":…,"note":"可选一行"}
{"type":"comment","id":"c-0011","kind":"reject","re_batch":2,"anchor":{"rev":"7c1d…"},"text":"【打回第 2 回合】…"}
{"type":"rev","sha":"7c1d5e0a91b2","ts":…,"commit":"<git hash|null>","by":"server|agent",
 "note":"submit batch 2"}
{"type":"reply","id":"r-0001","re":"c-0001","ts":…,"author":"claude","session":"<sid>","text":"…"}
{"type":"answer","re":"c-0001","ts":…,"mode":"reply|edit","commit":"<hash|null>",
 "summary":"一行外行结论 (必填)","session":"<sid>","rev":"7c1d…"}
```

`comment.kind` ∈ `block` (默认, 缺省即此) | `doc` (整篇, 无块锚) | `reject` (打回, 见下)。

## 状态机 (折叠推导, 不落盘)

`draft` --submit--> `open` --answer--> `answered` --resolve--> `resolved`
--reopen--> `open`。agent 只消费 `open` (= 已提交未回答); `draft` 对 agent 不可见。
已 `resolved`/`draft` 的评论 reply 拒写 (409 语义)。

`edit` / `drop` **只在目标仍是 `draft` 时生效**, 否则忽略 (server 侧同时回 409)。
—— 已提交的发言不可撤回不可改, 这是"submit = 他的发言边界"的直接推论。

## 版本 (rev) — v1 核心

- `rev.sha` = `sha1(文档字节)` 前 12 位, **文档内容的唯一身份**; `rev.commit` 只是溯源指针
  (可能为 null: agent 改了还没 commit)。
- **rev 只在回合边界产生**, 不追每次保存: 人侧 = 本批第一条评论 / submit / approve;
  agent 侧 = 每次 answer。当前 sha 与上一条 rev 相同则不追加 (幂等)。
- **快照是缓存, 不是第二真源**: 产生 rev 的一方把该版本字节写进
  `~/review-board/.snapshots/<docid>/<sha>.md` (内容寻址、不可变、gitignored、tmp+rename)。
  按自身内容哈希命名 ⇒ 不可能与真源分歧; 丢了只损失"看旧版", 不损失任何决定。
  真源永远是那份 md 与它的 git 历史。

## 回合 (round) — 折叠推导, 不落盘

一个回合 = `submit(batch N)` 开始 → 该批评论的 answer 全部到齐 → `approve(batch N)` 结束。
推导出 `{batch, rev_before, ids, answered_ids, edited_ids, rev_after,
status: open|answered|approved|rejected}`。

**打回不是新事件类型**: 打回 = 一条 `kind:"reject"` 的 doc 级评论 + 立即 submit 成新批次。
⇒ `pending` / `wait` / `answer` 整条唤醒链零改动复用; 语义也对 —— 打回就是"这版不行, 因为…"。
`reject` 评论带 `re_batch` ⇒ 被打回的那个回合状态转 `rejected`, 不再挂在"等你批"里。

## 锚点 (trap #1: 评论永不因文档重写而丢失)

- anchor = `slug` (所在节标题 slug) + `hash` (块文本 normalize 后 sha1 前 8) +
  `quote` (normalize 后前 120 字) + **`rev`** (写这条评论时的文档版本)。**永不用行号**。
- 解析 (v1 四档, 结果带 `anchor_status`):
  1. `ok` — 当前文档里 hash 精确匹配, 或 quote 包含匹配 (= v0 两档)。
  2. `modified` — 失配, 但 `anchor.rev` 的快照里能定位到原块, 且块级 diff 把它**配对**到当前
     文档的某个改写块 ⇒ 锚点前向映射到新块, 卡上标「已按你的意见改写」。
  3. `deleted` — 快照里有原块, 当前文档里配不上任何块 ⇒ 进未锚定托盘 (带旧原文)。
  4. `unknown` — 没有 `rev` (v0 legacy 评论) 或快照丢失且失配 ⇒ 托盘, 行为同 v0。
- **仍然不许硬改/硬猜**: 前向映射只用块级 diff 的配对结果 (同 slug + 相似度阈值),
  配不上就诚实落 `deleted`/`unknown`, 不做模糊近似。
- ⚠️ v1 存在的理由: v0 里"意见被采纳"与"文档漂移"输出同一个信号 (失配) —— 三条改变了方案的
  评论全被标成孤儿。**分类不是装饰, 是把喜讯从事故里摘出来。**

## 块级 diff (为什么不是行 diff)

比较单位 = `split_blocks` 的块, 不是行: 改一段方案就是整段替换, 行 diff 出来是一墙红绿。
`diff_blocks(old, new)` 输出 `same|mod|add|del` 序列 —— 先在块 hash 序列上取匹配段,
段间空隙按 slug + 文本相似度配对为 `mod`, 余下落 `add`/`del`。

## 归因 (causes) — 只到回合级

某块的改动归因 = **该回合里 `mode=edit` 的评论 id 集合**, 不是逐块归因。
理由: 一次 answer-edit 往往一个 commit 覆盖多条评论, 更细的归因是**假精度**。要更细, 由 agent 显式声明, 不由系统猜。

## 其他不变量

- 渲染层永不改源 md 字节 (server 对 doc 文件只读; 快照是只读拷贝, 不回写)。
- `answer.summary` 必填且须外行能懂 (PRD §3 硬性验收, boardctl 强制)。
- answered-by-edit 必须带源 repo 的 commit hash (溯源链)。
- **`draft` 对 agent 不可见的边界**: agent 可以知道"某文档有 N 条未提交"(用于提醒),
  **不许读其正文** (`boardctl drafts` 只出计数)。

## v2 增量 (2026-08-23 飞书化: 即发单条 / thread 追问 / 划词 / 外链答案)

### 新/改事件行 (写者分工不变: 前三种 = server→comments.jsonl; doc_ref = boardctl→replies.jsonl)

```jsonl
{"type":"comment","id":"c-0012",…,"sent":true,
 "anchor":{…v1 四字段…, "sel":{"text":"划词原文 (normalize 后, ≤200)","start":123,"end":150}}}
{"type":"followup","id":"f-0001","re":"c-0003","ts":…,"author":"Reviewer",
 "text":"追问正文","sent":true,"rev":"9f3a…"}
{"type":"deliver","ts":…,"fids":["f-0001","f-0002"]}
{"type":"edit","re":"f-0001",…}   {"type":"drop","re":"f-0001",…}
{"type":"reply"/"answer",…,"doc_ref":"<相对 doc 目录的 .md 路径>"}
```

- **`comment.sent:true`** = 即发单条: 生而 `open`、`batch=null`、**永不入回合**。缺省 = v1 draft 流。
- **`followup`** = 人类 thread 追问: `re` 指根 c-id; `sent:true` 即发, 缺省 draft (对 agent 不可见,
  可 edit/drop, `re` 允许 f-id — draft-only 规则原样适用)。
- **`deliver`** = 批量释放 draft followup (submit 时随批同发)。**不开回合、无 batch** —
  submit 事件完全冻结不动, 批号↔回合保持 1:1。
- **`answer` 仍以根 c-id 收口**; followup 后再答 = 同一 thread 的新一轮 answer
  (summary/answer 头 last-writer-wins, 完整对话在 `turns`)。

### 状态机扩展

`followup(sent:true)` 或 `deliver` 命中 ⇒ thread `answered|resolved → open` (追问 = 隐式 reopen;
open 保持 open) ⇒ `pending`/`wait` 整条唤醒链零改动复用。不变量: **最后一个送达的人类发言晚于
最后一个 answer ⇔ thread open** — 事件按 ts 重放天然满足。

### fold 输出扩展

thread 新增有序 **`turns`**: comment/followup/reply/answer 各成一轮
`{kind, id?, ts, author?, text?, draft?, mode?, commit?, rev?, summary?, doc_ref?}`;
`replies`/`summary`/`answer` 头字段保留 (兼容 + 卡片一行结论)。
**draft turn 正文对 agent 不可见**: `pending()` 输出剥除 draft turns (trap #6 延伸, 单点封堵)。

### 划词锚点 (anchor.sel)

- `sel = {text, start, end}`: `text` = 选中文 normalize 后 (≤200 字符), `start/end` =
  在 `normalize(块 md)` 内的偏移 (对 `anchor.rev` 那一版)。
- 解析: **先走 v1 四档块级解析 (sel 永不影响选块)**; 块命中后在当前块 normalize 文本里重找
  `sel.text` (多处命中取距 `start` 最近), 找到 ⇒ `sel_status:"ok"` + `sel_pos:{start,end}`,
  找不到 ⇒ `sel_status:"gone"`, **优雅降级为整块锚** — 不猜、不硬映射 (trap #1 同族)。
- 无 sel 的锚照旧; sel 只是块内细化, 前向映射仍由块级 rev 机制承担。

### 外链答案 (doc_ref)

- 长答案落**独立 md**: 约定 `<doc>.answers/<cid>-<slug>.md`, 与 sidecar 同族同目录、随源 repo
  commit。(为什么不共享 answers/ 目录: 同目录多文档各有自己的 c-0001, 已实证撞名。)
- `reply`/`answer` 可带 `doc_ref` = 相对 doc 目录路径。`--summary` 硬性验收不豁免。
- server 折叠时解析为 `doc_ref_url` 两态: 路径命中**已注册文档** ⇒ `/d/<那个docid>`
  (可评论卫星, 深链); 否则 ⇒ `/x/<docid>/<rel>` 只读渲染页 (无 sidecar、无评论、不落快照)。

### registry 元数据 (板侧, 不进文档正文 — 回合模型惩罚纯导航性编辑, 实测)

- entry 可带 `parent: <母docid>` (板列表母卡下挂卫星) 与 `born: {doc: <母docid>, cid: "c-0002"}`
  (卫星页头自动渲染反链「born from c-0002」; 母文档评论卡渲染正向「→ 卫星」)。
- 母子关系/出身只活在 registry — 文档正文零导航性编辑, 回合归因不受污染。

## v2.1 增量 (2026-08-24 explore-design 三件套; 出处 = explore-design 讨论稿板上拍板)

### registry 字段 (板侧元数据续, 零开关)

- `question: "<原话>"` — **question-first 出生记录**: 孕育本文档的原始问题
  (`register --question`)。viewer 头部与首页渲染「❓ 本文回答的是: …」。
- `archived: true` — **冷却封存** (「active 不该越来越多」的第二去处; 第一去处=毕业归仓 `move`)。
  首页折进归档区; **`pending()`/`draft_counts()` 跳过 archived doc** ⇒ wait 不再被它吵醒。
  archive/unarchive 可逆, 评论史不动。

### 新事件: graduate (毕业指针; 写者 = agent → replies.jsonl)

```jsonl
{"type":"graduate","re":"c-0005","ts":…,"dest":"review-board/docs/KERNEL.md §9",
 "note":"一行裁决","session":"<sid>"}
```

- 语义: 该 thread 的裁决**已由 agent 写进 dest** (HANDOFF/open-decisions/memory —
  路由是 agent 的活, 不问用户归属); 板上只记指针, **板永不写源文件**。
- graduate 是**标记不是状态**: 状态机不动, resolve 仍归他。fold 输出
  `th["graduated"]={dest,note,ts}` + turn `{kind:"graduate"}`; 卡片渲染「⤤ 已毕业 → dest」。
- gate (boardctl 侧): thread 须 answered/resolved — 裁决得先存在才有得毕业。

## v2.3 增量 (2026-09-01 board-v3 板上拍板: 看完了/覆盖率 · 认领锁 · 联合首页 · 笔记 · 基线泛化 · 批准合一)

### 新事件 (写者 = server → comments.jsonl)

```jsonl
{"type":"seen","ts":…,"rev":"<sha>","hashes":["ab12ef34",…]}
{"type":"comment","id":"c-0031","kind":"note","anchor":{…}|null,"text":"…"}
```

- **`seen`** = 「看完了」: 记下此刻文档 (rev) 全部块的 hash — 语义「这些块他看过了」。
  **approve 隐含 seen** (approve.rev 快照的全部块 hash 视为看过; fold 时推导, 不重复落盘)。
- **看过集合 (fold 推导, 不落盘)**: `seen_hashes = ∪ seen.hashes ∪ {approve.rev 快照的块 hash}
  ∪ {每条 block 评论的 anchor.hash}`; 块「看过」⇔ 当前块 hash ∈ seen_hashes。块改写 = 新 hash =
  自动回到未看 (零簿记)。`coverage = {n_seen, n_total, unseen:[{i, slug, hash}]}` 由 `/api/doc`
  与 `boardctl pending` 输出。**沉默的默认语义 = 没看** (mode a); 「看完了」一键 = mode b; 不设开关。
- **`kind:"note"`** = 私有笔记 (F4): 永远 draft、永不 submit (`/api/submit` 跳过)、`draft_counts` 不计、
  `pending` 永远不见; 可 edit/drop (draft-only 规则原样)。随 sidecar 入 git — 他自己的记录 (session 书签等)。

### 装饰基线泛化 (B9 ③)

baseline = **最后一次人类发言时的 rev**: 发言 = sent comment (anchor.rev) / sent followup (rev) /
submit / deliver / approve (approve_rev) / seen (rev)。有回合且未批准时与 v1 等价 (submit 的
rev_before, 除非他之后又即发了评论 — 那基线前移到那条, 语义仍是「自我上次发言以来」); 无回合的
纯即发文档从此也有基线。`causes` 仍只取当前回合的 edited_ids。`/api/diff/<doc>` 不带 `a=` ⇒ `a=baseline`。
批准后装饰清场不变, 但「改动」页保留 (a=该回合 rev_before → b=approve_rev), 淡显入口。

### 认领 (F2; 机器本地 `claims.local.json`, gitignored, 不进 sidecar — 活状态不是决定)

```json
{"<docid>": {"sid":"…","name":"board-v3","host":"workstation","cwd":"~/work","pid":1171,"since":ts,"beat":ts}}
```

- `boardctl wait --doc X` = 认领 X (多 doc 各一条); 每 interval 刷 `beat`; 退出释放 (finally)。
- 活着 ⇔ `beat` 距今 < 90 s **或** 同机 pid 存活。被别的活 session 认领 → wait 拒绝 (exit 3) 并打印
  值守者; `--takeover` 强抢。`boardctl claims` 列表, `boardctl release [doc]` 手放。
- `/api/docs` / `/api/doc` 带 `claim` (含 `alive`, `beat_ago`); 页头 chip「值守: name · sid8 · host · cwd ·
  心跳 Ns 前」; 无 → 「无人值守」。
- `reply` / `answer` 事件的 `session` (已有) 在 fold 输出到 turn (`turn.session`); public package uses short native session IDs for labels; it does not read private harness registries.

### Public local-only scope

The peer API, peer homepage and SSH tunnel helper are removed in 0.2.0.
An existing `peers.local.json` is ignored. Registry and claims remain local.

### registry

- `boardctl unregister <doc>`: 摘掉条目 (sidecar / 快照不动, 可重新 register 找回); `register` 拒绝不存在的
  路径 (已有)。首页 exists=false 灰显「本机无此文件」。

### 「批准」与「看完了」合一 (c-0009 拍板)

同一按钮, 名字随上下文: 有回合处于 answered → 「批准这版」= approve (隐含 seen); 否则 → 「看完了」= seen。

## v2.4 增量 (2026-09-04 batch 6 拍板: B10 / F12 / F14; F13 冻结, F15 收窄后暂缓)

- registry 新字段 `emoji: "🧭"` (板侧元数据): favicon 身份。`register --emoji` / `boardctl emoji <doc> <e>`;
  skill 铁律: 起板必配, 推荐前看已用的避撞。
- `GET /favicon.svg[?doc=<id>]`: 服务端合成 SVG — 画框底图 (首页=画框+三条评论线);
  一级文档 = 画框 + emoji; 卫星 = 母 emoji 0.5× 半透明垫底 + 自己的 emoji。
- `GET /api/search?q=&doc=<可选>`: 精确子串 (归一 = 全角→半角/小写/空白折叠, ≥2 字);
  扫描面 = 正文逐块 / question / 两 sidecar 的 comment·followup·reply 文本与 answer summary /
  `<doc>.answers/*.md`; **含 archived** (挖坟), 结果 `{doc,title,archived,kind,excerpt,url(深链)}`, 上限 100。
- 首页两级树: 机器节与卫星组可折叠 (localStorage), 卫星默认折进母行。

## v2.5 增量 (2026-09-07 batch 7: 卡顿 memo / 悬浮搜索 / 页面改元数据 / 卫星标准)

- **热路径 memo (B12)**: read_events (mtime_ns,size 键) / snapshot_blocks (内容寻址) / 锚解析与块 diff
  (docid, rev, 当前块序列 键) / seen_hashes (sidecar stat 键)。**键即有效性, 无 TTL**; read_events 返回值消费方只读。
  实测 todos-decisions (62 thread, 锚全失配): 10.1 s → 冷 0.4 s → 热 2 ms。
- registry 新字段 `color: "#RRGGBB"` (画框色); `POST /api/meta {doc, emoji?, color?}` — server 与 boardctl 同为
  registry 写者 (同机原子写)。favicon 画框 stroke 用 color。
- **文档三类 (c-0026, 09-07 拍板)**: ① 他要能评论/追问的 → `register --parent --born-from --emoji`
  (可编辑可评卫星); ② 纯参考/原始材料 → 外链答案页 `/x/` (只读, 不占首页, 搜索可达);
  ③ 草稿 → **也上板, 但注明**: registry `draft: true` (`register --draft` / `boardctl draft <doc> on|off`),
  首页与文档页出「✏️ 草稿」chip, 其余行为与正式文档相同。

## v2.6 阅读视图 (2026-10-20, board-v3 batch 8)

本次仅改浏览器投影，无新增事件或 registry 字段，server / CLI 的送达边界不变。

- 章节折叠、目录树展开态存在浏览器 localStorage，按 docid 与外链路径隔离。
  折叠不写 `seen` / `resolve` / `approve`，不代表看过、批准、搁置决定或完成。
  评论与块深链会自动展开所需祖先章节；章节折叠时仍显示隐藏评论和未提交数量。
- `followup.draft` 仍不改变 thread 的 answered/resolved 状态、不向 agent 送达；
  人侧默认列表保留带追问草稿的 resolved thread，并可用「未提交」「追问」筛选。
- 卡片完整显示已存的 `anchor.sel.text` 或 `anchor.quote`。匹配到当前渲染文本时高亮对应引文，
  编号与评论/侧栏一致；无法匹配则保留历史引文并降级块定位，不猜选区。
  原 `sel.text ≤ 200` 契约不变，完整显示指完整的已存引文。
- 块深链继续支持 `#b-<hash>`；同页后续同 hash 块使用 `#b-<hash>~<occurrence>`（从 1 开始）
  消除重复标题的 DOM id 冲突。旧链接仍定位第一处；这是浏览器导航标识，不是新锚点身份。
- 顶栏分标题/操作两行，自适应换行；「看完了」和「批准这版」仍共用一个按钮，
  状态机及落盘事件与 v2.3 相同。

## v2.7 探索式阅读 v1 (2026-10-21)

目标：从原文的一问进入独立分支，保持来路和返回位置；已有 subdoc 可直接成为分支正文。
回答仍经当前 agent 的 pending/wait → reply/answer-edit，不增加模型 API、后台 worker 或自动在线承诺。

### 兼容事件与送达

- `comment` 可带 `intent: "explore"`；kind/anchor/text/sent 语义不变。草稿探索仍不可被 agent 消费。
  私有 note 不接受 explore intent。
- 新增人侧事件 `{type:"explore", re:"c-…", ts:…}`：把一个**已送达、非笔记** thread 挂进探索树。
  不改变 open/answered/resolved，不生成新问题、不开回合、不隐式重开。重复请求幂等。
- fold 输出 `intent`；pending 透传，CLI 文本输出标「探索分支」。agent 可先回复，必要时沿现有
  `register --parent <doc> --born-from <cid>` 建立 subdoc；源 Markdown 仍仅由 agent 修改。

### HTTP

- `POST /api/comment` 增加可选 intent（只允许 explore）。
- `POST /api/explore {doc,id}`：推广既有 thread；成功 `{ok:true,id}`，draft/note/未知 id 返回 409。
- `POST /api/followup/send {doc,id:<f-id>}`：只送达这条已保存的追问，复用 deliver 事件；
  不附带其他草稿，已送达时幂等成功，未知/已撤销 id 返回 409。
- `GET /api/explore/<doc>`：只读返回当前文档所属 parent 家族的 `{root,current,docs,branches}`。
  docs 含 id,parent,title,emoji,question,born,exists,archived；branches 含 doc,id,title,quote,status,summary。
  branches 只含已送达的显式探索 thread 或已有 subdoc 的出生 thread；不含私有笔记、草稿正文、未提交追问。
  parent 缺失时以现存祖先为根；有环时终止遍历，不递归失控、不影响其他家族。

### 浏览器

- 文档旁增加「探索」树，与本文目录分别呈现；无须给文档预先选择模式。
- 划词浮钮「深入」、composer「作为探索发出」、已送达评论「探索」都是显式动作。
  先发送问题才能进入共享探索树；普通评论不会自动变成新文档。
- 同源 `/d/` subdoc 和 `/x/` 外链答案在侧面阅读区打开，提供返回/路径/单独打开。
  后退保留各层 DOM、滚动和未发送输入；隐藏嵌入页暂停轮询，浏览器刷新仍回到持久分支关系。
  草稿输入不会因进入分支自动提交。`/x/` 继续只读（不重开 F13）。
- `?thread=<cid>` 为单个 thread 的专注阅读投影；仍使用原 thread 的追问和答复协议。
  `embedded=1` 只影响布局。iframe 消息只接受同源、由本页创建的 frame，目标只允许 /d/ 或 /x/。
- 探索树、侧面阅读状态和访问记录不代表 seen/approve/resolve，也不会自动把解释当作正式结论。

## Public package identity and state

`RB_DATA_DIR` isolates registry, claims and snapshots. Sidecars remain next to
their documents. Explicit `RB_AGENT_SESSION` / `RB_AGENT_HARNESS` / `RB_AGENT_NAME`
take precedence over native session environment variables. Native Codex and Claude
Code sessions remain distinct; model identity is not inferred. Reply and answer
events may carry an additive `harness` field. No private host inventory or harness
session registry is read. See docs/USAGE.md for portable setup.
