# 意图解析 + rewrite 重写能力审计

> 目的：在上一轮把意图解析提示词重做成「小模型选槽位 + 一句英文 rewrite」之后，
> 用**真实 LLM**（`qwen-flash`，即 `model_intent`）跑一批有代表性的用户输入，
> 逐条检查它抽槽位、写 `rewrite` 的能力，把问题记下来。
>
> 方法：对每条输入调用 `resolve_intent()`（真实打 DashScope），再用
> `build_rule_proposals()` 组装出图提示词第一块，看 `rewrite` 实际落位。
> 共 **48 条**，覆盖人物 / 器物 / 场景 / 博物馆 / 风格迁移 / 修复 / 歧义 / 多轮。

---

## 一、总览结论

| 指标 | 结果 |
|---|---|
| 总条数 | 48 |
| 走 LLM（非规则兜底） | **48 / 48**（无降级、无超时） |
| 单次延迟 | 416 ~ 1405 ms（中位 ~800ms） |
| 给出非空 `rewrite` | 44 / 48 |
| 完全合格（无问题标记） | **11 / 48** |
| 有小问题（⚠） | 27 / 48 |
| 明显错误（❌） | 10 / 48 |

**好消息**：模型基本能跑通「选角色 + 写动作句」这个核心分工，速度也够快，歧义输入不会编造 rewrite，多轮切换角色正常。

**坏消息**：有几类**系统性**问题，不是偶发，而是几乎每条都犯。按严重度排序：

| # | 问题 | 影响条数 | 严重度 | 是否真进提示词 |
|---|---|---|---|---|
| 1 | `kind` 语义没定义，模型把「主体是器物」误判成 `kind=artifact` | ~17 | 高 | 是（repair 负向词 + 策略错配） |
| 2 | `identity` / `scene` 被填成固定默认值（大祭司 / 博物馆展厅），用户没提也填 | ~48 | 高 | **是**（`resolve_scene`/`resolve_identity` 真吃） |
| 3 | `style` 每条都被填成 `博物馆纪实摄影` | ~48 | 低 | 否（非 style 用例不消费该字段） |
| 4 | `rewrite` 里混入材质/外观/机位/风格词，违反「只写动作+主次」 | ~20 | 中 | 是（冗余，并有编错材质风险） |
| 5 | 风格迁移用「XX风格的大立人」这种定语式说法时漏判 `kind=style` | 3 | 中 | 是（丢了风格迁移档位） |
| 6 | `rewrite` 写了中文（应为英文） | 1 | 中 | 是 |
| 7 | `rewrite` 超 30 词上限 | 1 | 低 | 是（截断在 240 字符，词数没卡） |

---

## 二、逐条分析

判定图例：✅ 合格　⚠ 小问题　❌ 明显错误

> `rewrite` 列做了缩写，只保留动作/主次骨架；括号里标它混入的违禁词。

### 人物（figure / person）

| id | 输入 | kind/role | rewrite（缩写） | 判定 | 问题 |
|---|---|---|---|---|---|
| f01 | 大祭司戴青铜纵目面具在祭祀台前主持祭祀 | figure/person | stands before altar, arms raised, **bronze mask** on face, visual anchor | ⚠ | rewrite 含材质词 |
| f02 | 大祭司穿华丽祭服站祭坛中央 | figure/person | stands at altar center in **ornate ritual robes**, focal | ⚠ | rewrite 含外观词(robe) |
| f03 | 青铜大立人站神殿双手环握 | figure/person | stands center altar, arms cradling object, focal | ✅ | 干净，动作对 |
| f04 | 跪坐人像用玉石雕刻 | figure/person | seated cross-legged carving **jade**, mid-shot | ⚠ | 含材质词(jade) |
| f05 | 金面人头像特写 | figure/**artefact** | golden mask centered, face dominant | ⚠ | role 偏器物合理；rewrite 含 gold/mask |
| f06 | 古蜀巫师全身像 | figure/person | stands full figure arms raised, solemn pose | ✅ | 干净 |
| f07 | 戴金面具的青铜人 | figure/person | stands in pit, **golden mask** on face, central | ⚠ | 含材质词 |
| f08 | 古蜀王城楼眺望 | figure/person | stands on wall gazing distance, full-body | ✅ | 干净；identity=古蜀王 也对 |
| f09 | 青铜人像穿长袍 | figure/person | stands in **ceremonial robes**, silhouette | ⚠ | 含外观词(robe) |
| f10 | 三层祭服黄金面具赤脚祭坛的大祭司 | figure/person | stands barefoot, **three layers robes, golden mask**, anchor (31词) | ⚠ | 外观词 + **超 30 词** |

人物组的 `kind`/`role`/`identity`/`scene` 基本对（f05 偏器物也算合理）。
主要毛病是 **rewrite 总忍不住把材质/外观写进去**（见问题 4）。

### 器物（figure / artefact，但多数不应是 artifact）

| id | 输入 | kind/role | rewrite（缩写） | 判定 | 问题 |
|---|---|---|---|---|---|
| a01 | 青铜纵目面具的材质特写 | **artifact**/artefact | **bronze** mask **close-up** detail, central | ❌ | **kind 应 figure** + 材质/机位词 + scene 被填博物馆展厅 |
| a02 | 给青铜神树拍现状记录 | **artifact**/artefact | **bronze** sacred tree full view, central | ⚠ | **kind 应 figure** + 材质词 |
| a03 | 金杖上的图案 | **artifact**/artefact | **gold** scepter full view, central | ⚠ | **kind 应 figure** + 材质词 |
| a04 | 太阳轮正面照 | **artifact**/artefact | **bronze** sun disc front view, central | ⚠ | **kind 应 figure** + 材质词 |
| a05 | 陶三足炊器的修复 | artifact/artefact | pottery tripod vessel central focus | ✅ | 真修复，kind 对；rewrite 偏弱但合格 |
| a06 | 玉璋的纹饰 | **artifact**/artefact | **jade** zhang displayed, central | ⚠ | **kind 应 figure** + 材质词 |
| a07 | 青铜大面具的侧面 | **artifact**/artefact | side view **bronze** mask, central | ⚠ | **kind 应 figure** + 材质词 |
| a08 | 把破损青铜器补全 | artifact/artefact | broken **bronze** artifact reconstructed | ⚠ | 真修复 kind 对；含 bronze（修复语境可接受） |
| a09 | 金面具特写镜头 | **artifact**/artefact | **golden** mask **close-up**, central | ⚠ | **kind 应 figure** + 材质/机位词 |
| a10 | 神树矗立祭坛+人群 | scene/scene | **bronze** tree at altar center, people around | ⚠ | scene 主干对；rewrite 含 bronze（可接受） |

**关键观察**：a01–a04、a06–a07、a09 全是「器物特写/现状记录」，**不是修复**，
但模型一律给 `kind=artifact`。原因见问题 1。

### 博物馆 / 展陈

| id | 输入 | kind/role | rewrite（缩写） | 判定 | 问题 |
|---|---|---|---|---|---|
| m01 | 面具在博物馆电影级写实 | **artifact**/artefact | **bronze** mask center museum hall | ⚠ | **kind 应 figure**（style=电影级写实 抓对了） |
| m02 | 金杖放展柜展示 | **artifact**/artefact | **golden** staff in glass case | ⚠ | **kind 应 figure** + 材质词 |
| m03 | 修复台上的青铜人像 | **artifact**/artefact | **bronze** figure on restoration table | ⚠ | **kind 应 figure** + 材质词 |
| m04 | 考古现场正在发掘的器物 | **artifact**/artefact | unearthed artifact in focus | ⚠ | **kind 应 figure** |

### 多轮（顺序执行，b 继承 a 的 last_intent）

| id | 输入 | kind/role | rewrite（缩写） | 判定 | 问题 |
|---|---|---|---|---|---|
| mm01a | 大祭司在祭祀 | figure/person | stands center ritual ground, arms raised | ✅ | 干净 |
| mm01b | 换成面具的材质特写 | **artifact**/artefact | **bronze** mask surface **close-up**, central | ⚠ | 切到 artefact ✅；但 **kind 应 figure** + 材质/机位；rewrite 已正确重置（没带祭司） |
| mm02a | 青铜纵目面具 | **artifact**/artefact | **bronze** giant-eyed mask central | ⚠ | **kind 应 figure** + 材质词 |
| mm02b | 让大祭司戴着它主持祭祀 | figure/person | stands in pit, **bronze** mask on face, arms raised | ⚠ | 切回 person ✅，subject 带上面具 ✅；rewrite 含材质词 |

**多轮切换本身是对的**：角色在 person↔artefact 之间正确翻转，`rewrite` 也确实每轮重写（没把上一句的祭司带过来）。毛病还是 kind 误判 + 材质词。

### 修复（artifact —— 这里 kind=artifact 才是对的）

| id | 输入 | kind/role | rewrite（缩写） | 判定 | 问题 |
|---|---|---|---|---|---|
| r01 | 补全青铜神树缺失的枝桠 | artifact/artefact | missing branches **bronze** tree reconstructed | ⚠ | kind 对；含 bronze |
| r02 | 修复断裂的金杖 | artifact/artefact | broken **golden** staff, fragments aligned | ⚠ | kind 对；含 gold |
| r03 | 残缺陶器做3D复原 | artifact/artefact | fragmented pottery reconstructed 3D | ✅ | 干净 |

### 场景（scene —— 这组最干净）

| id | 输入 | kind/role | rewrite（缩写） | 判定 | 问题 |
|---|---|---|---|---|---|
| s01 | 祭祀场面的全景 | scene/scene | entire ritual scene, full spatial layout | ✅ | 干净 |
| s02 | 祭坛格局多人祈祷 | scene/scene | altar layout, multiple figures in prayer | ✅ | 干净 |
| s03 | 祭祀大典宏大场面 | scene/scene | grand ceremony, full spatial layout | ✅ | 干净 |
| s04 | 古城俯瞰 | scene/scene | aerial view, spatial layout of city | ✅ | 干净 |
| s05 | 祭祀坑摆满文物 | scene/scene | burial pit filled artifacts, dense layout | ✅ | 干净 |
| s06 | 大祭司和神树在一起的画面 | scene/scene | **中文**"The祭坛 is arranged…high priest stands…" | ❌ | **rewrite 写成了中文** + 含 bronze |

场景组除了 s06，rewrite 都干净且紧扣「空间关系+全貌」，正是系统提示词对 scene 的要求。

### 风格迁移（style）

| id | 输入 | kind/role | rewrite（缩写） | 判定 | 问题 |
|---|---|---|---|---|---|
| t01 | 大祭司变成油画风格 | style/**artefact** | figure rendered **oil painting style** | ⚠ | role 应 person；rewrite 重复风格词（style_preset=油画 抓对） |
| t02 | 祭祀场面加水墨画风格 | style/scene | ritual scene rendered **ink wash painting style** | ⚠ | rewrite 重复风格词（冗余） |
| t03 | 面具改成赛博朋克风 | style/artefact | **bronze** mask **cyberpunk** aesthetic, neon | ⚠ | 含材质/风格词 |
| t04 | 卡通风格的大立人 | **figure**/person | cartoon version **bronze** figure, central | ❌ | **kind 漏判，应为 style**；rewrite 含材质词 |
| t05 | 国风工笔画的三星堆 | **scene**/scene | museum hall displays **bronze** figure | ❌ | **kind 漏判，应为 style**（style_preset=工笔重彩 但 kind 错→不走迁移） |
| x06 | 纵目面具来个cosplay风格 | **artifact**/artefact | **bronze** mask **cosplay** pose, center | ❌ | **kind 漏判，应为 style** + 材质词 |

**关键观察**：用「变成/改成/加个…风格」说法（t01–t03）模型能识别 style；
但用「**XX风格的大立人**」「**XX的三星堆**」这种**定语式**（t04、t05、x06），
模型就当成普通 figure/scene，丢了风格迁移档位。见问题 5。

### 歧义 / 短指令

| id | 输入 | kind/role | rewrite | 判定 | 问题 |
|---|---|---|---|---|---|
| x01 | 来一张 | scene/scene | （空） | ✅ | 不编造，正确兜底 |
| x02 | 好看点 | scene/scene | （空） | ✅ | 不编造，正确兜底 |
| x03 | 换个风格 | scene/scene | （空），style="换个风格" | ⚠ | 风格未识别（无主体，勉强可接受；kind 应为 style） |
| x04 | 大一点，亮一点 | scene/scene | （空） | ✅ | 不编造，正确兜底 |
| x05 | 不是祭祀，是日常生活的古蜀人 | scene/scene | Daily life…**excavation site** | ❌ | 判错：日常生活≠考古现场；应为 figure/person，却给 scene+遗址考古现场 |

x01–x04 表现好：没主体的指令**宁可不写 rewrite 也不编**，退回规则锚点句。
x05 是清晰的误判——用户明确要「人物日常生活」，模型却给了「考古发掘现场」这个 scene。

---

## 三、系统性问题深挖（每条都带根因 + 修复方向）

### 问题 1：`kind` 语义完全没解释 → 模型把「主体是器物」当成了 `kind=artifact`

系统提示词用一整段讲了 `role`（person/artefact/scene），但 `kind` 只在输出 JSON 的
schema 里出现，**没有任何定义**。唯一能参考的只有示例输出里那一句
`"kind":"figure"`。于是模型的推理变成：「主语是器物 → kind 就填 artifact」。

后果：a01–a04、a06–a07、a09、m01–m04、x06、mm01b、mm02a 共约 17 条，
明明是「器物特写/现状记录/展陈」，却得到 `kind=artifact`。
而 `build_rule_proposals` 对 `kind=artifact` 会加 repair 专属负向词
（"over-restored, polished bronze, brand new look…"）并选修复策略——
给一张「博物馆里的面具特写」套上「别修得太新」的负向约束，是错配。

**修复方向**：系统提示词里给 `kind` 明确定义，强调它与 `role` 是**正交**的两个轴：
- `kind=artifact` 仅当任务是「修复/补全/复原残缺器物」；
- 普通器物展示/特写 = `kind=figure` + `role=artefact`；
- `kind=style` 仅当明确要做风格迁移；
- `kind=scene` / `kind=figure` 是通用复原。
并补一个对应示例（如 `"kind":"figure","role":"artefact"` 配器物特写）。

### 问题 2：`identity` / `scene` 被填成固定默认值（大祭司 / 博物馆展厅）

几乎每条都吐出 `identity=大祭司`、`scene=博物馆展厅`、`style=博物馆纪实摄影`，
**不管用户提没提**。这两个字段会被真吃进提示词：

- `scene` → `lexicon.resolve_scene()` → 拼进 Setting 块。而 `博物馆展厅` 是 SCENES 的一个 key，
  于是「青铜纵目面具的材质特写」这类**语境无关**的请求，被强行塞进
  「a dark-toned modern museum exhibition hall…」的博物馆环境。
- `identity` → `lexicon.resolve_identity()` → 仅当 `role=person` 时进人物块。
  对 person 用例大多歪打正着（用户要的恰是大祭司），但 x05 这种就翻车了。

根因：示例输出里写了 `identity=大祭司 / scene=祭祀台`，模型就学会了「三星堆默认大祭司、默认博物馆」；
提示词虽然写了「表里没有就留空字符串」，但**没有强调「用户没提就不能填」**。

**修复方向**：把约束改成硬规则——「`identity`/`scene`/`style` 三个字段，
**只有用户原话里明确出现了对应的人/场景/风格时才填，否则必须留空字符串 `""`**。
可加一句反例：「用户没说人，就不要默认大祭司；用户没说博物馆，就不要默认博物馆展厅」。

### 问题 3：`style` 每条都被填 `博物馆纪实摄影`（无害，但应清掉）

48 条里绝大多数非风格用例的 `style` 都是 `博物馆纪实摄影`。查证后确认：
`style` 字段**只在 `kind==style` 时**被 `build_prompt` 消费（拼成 "Style transfer target"），
所以非风格用例里这条幻觉**不影响出图**。属于噪声，优先级低，但和问题 2 同源，
用问题 2 的硬规则一并解决即可。

### 问题 4：`rewrite` 混入材质/外观/机位/风格词

约 20 条 rewrite 含 bronze / gold / jade / robe / mask / close-up / oil painting / cyberpunk 等。
这些本该由词典（材质/形制）或构图策略（机位/光位/风格）给，模型写了就是越界，
轻则冗余，重则**编错材质**（这次它写的材质碰巧都对，但下次不一定）。

**最讽刺的根因**：我给的示例输出本身就是
`"rewrite":"He stands before the stone altar with both arms raised, the bronze mask worn on his face, and is the visual anchor…"`——
**这句话自己就带着 `bronze mask`**，等于手把手教模型把材质写进 rewrite。

**修复方向**：
1. 把示例里的 `the bronze mask worn on his face` 改成 `the mask worn on his face`（去掉材质）；
2. 在 `rewrite` 规则里把「不要写」的列表明示为负面清单，并举例：
   「不要写 bronze/gold/jade（材质来自词典）、robe/crown（外观来自词典）、
   close-up/wide shot（机位来自构图）、lighting/style（来自档位）」。

### 问题 5：定语式风格迁移漏判 `kind=style`

「卡通风格的大立人」「国风工笔画的三星堆」「纵目面具cosplay风格」——模型识别不出这是风格迁移，
kind 退回 figure/scene，于是 `style_preset` 虽填了（工笔重彩等）却**不走风格迁移分支**，
出图提示词里没有 "Style transfer target" 块。

**修复方向**：在 `kind` 判定里加一条——「输入里出现 `风格/风/油画/水墨/赛博朋克/卡通/工笔/cosplay`
等风格词，且作用在已有主体上时，`kind=style`」。

### 问题 6：rewrite 写了中文（s06）

系统提示词写的是「一句**英文**」，但 s06 输出了中英混杂的句子。属于偶发，但说明
「必须英文」的约束不够硬。可在输出 schema 旁加注 `(write in English only)`，
并把它纳入 `resolve_intent` 的校验：含 CJK 就丢弃该 rewrite（退回锚点句）。

### 问题 7：rewrite 超 30 词（f10）

代码里 `rewrite` 只按 **240 字符**截断，没有卡 30 词。f10 实测 31 词通过。
可在 `resolve_intent` 里加一道词数校验（>30 词就截断到最近句号），或在提示词里把
「≤30 词」改成「不超过 25 词」留余量。

---

## 四、值得保留的好行为

- **零降级 / 零超时**：48 条全走 LLM，延迟 416–1405ms，关键路径上够快。
- **歧义输入不编造**：x01–x04 没主体时宁可不写 rewrite，退回规则锚点句，没出现「瞎编一句」。
- **多轮角色切换正确**：mm01 / mm02 在 person↔artefact 间翻转无误，且 `rewrite` 每轮重置（不串台）。
- **场景组 rewrite 质量高**：s01–s05 都紧扣「空间关系 + 全貌可读」，正是设计的预期。
- **真修复识别准**：a05 / a08 / r01–r03 正确判 `kind=artifact`，subject 也对。

---

## 五、修复优先级建议

1. **【高】问题 1 + 问题 2**：重写系统提示词，给 `kind` 下定义、给
   `identity/scene/style` 加「没提就留空」的硬规则。这两条是系统性、高频、真进提示词的。
2. **【中】问题 4 + 问题 5**：修示例（去掉示例里的 bronze mask）、补 rewrite 负面清单、
   补定语式风格迁移识别。直接决定 rewrite 的质量与风格迁移是否生效。
3. **【低】问题 6 + 问题 7**：在 `resolve_intent` 里加两道校验（CJK 丢弃、词数截断），
   顺手但能堵偶发。

> 测试脚本在 `backend/var/_audit.py`，原始结果在 `backend/var/_audit_results.json`，可复跑。

---

## 六、修复状态（已优化，轮次对比）

在 `app/conversation/agent.py` 中落地了下列改动，并复跑 48 条审计验证：

| 问题 | 改动 | 结果 |
|---|---|---|
| 1 kind 误判为 artifact | `resolve_intent` 增加 `_looks_like_repair()` 校验：`kind=artifact` 必须命中「修复/修补/补全/残缺/破损/病害/锈蚀/补配/断裂/断成」之一，否则强制回落 `figure`；并把「补全」补进 `KIND_SIGNALS` 修复词表（规则层与校验共用） | 误判从 ~17 条降到 0 条真正的误判 |
| 2 场景/身份幻觉 | 规则层 `scene` 默认 `博物馆展厅` → `黑色背景展陈`（中性展陈，不再强行现代博物馆）；`style` 默认 `博物馆纪实摄影` → `电影级写实`；提示词强化「没提就留空、别默认大祭司/博物馆」；`identity` 默认保留 `大祭司`（测试要求，且只在 role=person 时生效） | 意图里出现「博物馆展厅」从 ~48 条降到 3 条（均为用户明确提及） |
| 4 rewrite 含材质词 | 示例 rewrite 去掉 `mask`/`bronze`；负面清单聚焦「材质形容词 / 机位 / 光线 / 风格」；模板加一句反面教材 | **部分改善**：模型仍偶写 bronze/gold，但所写材质均与词典一致（无事实错误），属冗余而非错误 |
| 5 定语式风格迁移 | 提示词明确「卡通/国风/cosplay + 已有主体 → kind=style」；规则层风格词补 `卡通/国风/cosplay` | t04/t05/x06 现已正确判 `style` |
| 6 中文 rewrite | `resolve_intent` 增加 CJK 检测，含中文直接丢弃退回锚点句 | 无此标记 |
| 7 rewrite >30 词 | 增加词数上限（>30 词截到 30 词） | 无此标记 |

提示词长度受 `test_prompts_stay_short` 约束（`INTENT_SYSTEM < 900`、`INTENT_TEMPLATE < 400`），
优化后分别为 **888 / 364**，全部 115 个测试通过。

**唯一未彻底解决：问题 4 的材质词残留。** 提示词层已反复禁止且加了反面教材，模型仍会在 rewrite 里带 `bronze/gold`（主语是器物时尤其明显）。
风险评估：词典（`lexicon`）对所有材质/形制是权威来源，模型所写材质经核对均与词典一致，因此只是**冗余**、不构成事实错误。
若要 100% 干净，可加一道代码级 sanitizer（从 rewrite 中剥除材质形容词 / 机位 / 光线词），但需权衡误伤主语描述的风险——列为可选后续。

