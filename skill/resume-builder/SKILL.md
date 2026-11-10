---
name: resume-builder
description: 用 profile-atlas 为当前 gh 登录的 GitHub 账号生成按岗位定制、带证据的简历——同步各仓库的 .portfolio/project.yaml、合并本机私有资料、按岗位选项目、写简历文字、机器核对每个数字、渲染 HTML/PDF 和本机版本总览页。当用户说"生成简历 / 做一份 X 岗位的简历 / 发来一段 JD 要最匹配的简历 / 按这个 JD 改简历 / 更新简历 / 出一份公开版简历"时使用。
---

# resume-builder：GitHub 项目清单 → 岗位简历

分工：**你负责写，工具负责核对。** 你根据计划按岗位组织措辞和侧重；数字是否有出处、引用的项目是否存在、smoke 级结果有没有被夸大，由 `profile-atlas verify` 判定，不通过就不能渲染。

所有命令输出 JSON。工具没装时：`uv tool install --editable <profile-atlas 仓库>`。

| 命令 | 作用 |
|---|---|
| `profile-atlas sync` | 用 gh 登录的账号，拉取本人和所属组织全部仓库里的项目清单 |
| `profile-atlas profile check` | 校验本机私有资料（`~/.config/profile-atlas/profile.yaml`），列出未核实的项目 |
| `profile-atlas roles` | 列出岗位和主题 |
| `profile-atlas plan --role <岗位>` | 选项目、排序，生成计划；输出里有计划和草稿的路径；JD 岗位还输出 `coverage` 与 `gaps` |
| `profile-atlas verify --role <岗位>` | 核对草稿 |
| `profile-atlas render --role <岗位> [--theme t]` | 核对通过后渲染完整版（带证件照，发给招聘方的就是它）；`--public` / `--both` 只在用户明确要公开版时用 |
| `profile-atlas site [--serve]` | 本机版本总览页 |

## 隐私（最高优先级）

- 姓名、电话、邮箱、学校、公司、实习细节**只能**存在 `~/.config/profile-atlas/` 和 `~/.local/share/profile-atlas/`。**不要把它们写进任何仓库、提交信息、issue、PR 或公开页面**，也不要复制到工作目录里。工具拒绝读写位于 git 仓库内的私有文件。
- 完整版只在本机，用户自己发给招聘方，**默认只生成完整版**。公开版是可选的匿名版本（贴论坛请人改、放主页、演示工具时才需要），只在用户明确要求时生成：工具会扫描输出，出现姓名、联系方式、学校、公司、GitHub 账号或 `public_redact` 里的词就中止，并删除旧的公开版。被拦下时去改草稿措辞，**不要**去改禁止列表。
- 汇报结果时只给文件路径，不要在对话里大段复述完整版内容。

## 流程

1. **准备**：`profile-atlas sync`，然后 `profile-atlas profile check`。
   - 资料不存在：`profile-atlas profile init`，和用户一起填写。从用户已有的简历里导入时，每个项目都要写 `source`；数字是估算、待核对的，`verified: false`。
   - `unverified` 不为空：告诉用户这些项目在核实前不会进入简历。**不要替用户把 `verified` 改成 true。**
2. **确定岗位**：用户给的是预设岗位（`profile-atlas roles`）就直接用。给的是一段 JD，见下面的「JD 定制」，写一个岗位文件 `~/.config/profile-atlas/roles/<id>.yaml`。
3. **计划**：`profile-atlas plan --role <id>`。看 `projects` 的得分与 `selected`、`experience` 各项目的排序、`excluded` 的原因。排序明显不对时，调岗位权重，不要硬选得分为 0 的项目。实习项目的顺序可以按岗位相关度在草稿里手动调整（例如具身岗把机器人相关项目提前）。
4. **写草稿**：读计划文件（路径在 plan 输出里），在 plan 输出给的草稿路径写 `portfolio/resume@1`（结构见工具仓库 `src/profile_atlas/schema/resume.schema.json`）：
   - `experience`：每段实习按计划顺序挑项目；`projects`：用 `selected` 的项目，最多 `limits.projects` 个；每项 `bullets` 不超过 `limits.bullets` 条。
   - 每条 bullet 都要能在该项目的 `facts` 里找到依据：贡献、取舍、发现、指标。可以压缩、合并、调整语序和侧重，**不能新增事实或数字**，也不能把别人的工作写成用户的。
   - 按岗位选角度：同一个项目，算法岗写方法和指标，工程岗写系统和取舍，失败与反直觉的发现（findings）往往最有说服力。
   - `scale: smoke` 的指标只能写成"验证链路打通"，不能和"提升 / 优于 / →"放在一起。
   - `headline`、`summary` 里不要写学校或公司全称（公开版会被拦下）。
   - JD 岗位：bullet 可以借用 JD 的术语，**前提是事实里确实是这件事**（JD 写"Agent 工具调用"、事实写"封装 MCP 入口"可以；JD 写"K8s 部署"、事实里没有就不能写）。`coverage` 里有证据的必需项要在草稿里写到。
   - 排版：每个项目可以用 `**…**` 加粗 1–2 个最关键的数字，或给 bullet 加一个很短的小标签（`**数据构建：**…`）；不要整句加粗，也不要用其他标记。以 `**` 开头的条目在 YAML 里要加引号（`- '**数据构建：**…'`）。
5. **核对**：`profile-atlas verify --role <id>`，逐条修到 `"ok": true`。`number-not-in-facts` 说明数字写错了或者没出处——删掉或改成事实里的原数，不要去改事实。
6. **渲染**：`profile-atlas render --role <id>`（只出完整版）。页面会在 11.5–14.5px 间自动选字号铺满一页，输出里的 `fill` 是填充率、`font_px` 是选中的字号。
   - `pages` > 1（最小字号也放不下）：先删得分低的项目的 bullet，再合并相近的 bullet，再缩短 tagline。不要改主题 CSS 硬塞。
   - 警告“偏空”（最大字号下 `fill` < 0.85）：加回计划里下一个得分高的项目，或从 facts 里补一条有依据的 bullet；不能为了填满编内容。
   - 完整版有证件照需要个人资料里的 `photo`（本机路径，不在 git 仓库里）；公开版永远没有照片。
7. **汇报**：岗位、选了哪些项目（以及为什么不选其他高分项目）、PDF 路径、页数、被排除的项目和原因、需要用户核实的事项。JD 岗位再给一张匹配表：JD 每条要求 → 简历里哪条项目覆盖 / 缺口；对缺口给建议（面试里怎么诚实回答，或用 project-intake 补一个小项目），不要在简历里硬凑。提醒用户可以运行 `profile-atlas open` 在本机对比各版本。

## JD 定制

用户发来一段 JD 时：

1. **起 id**：`jd-<公司或团队简称>-<岗位>`，例如 `jd-acme-agent`。JD 原文只存在本机岗位文件里（它可能含公司名，不进任何仓库）。
2. **拆要求**：把 JD 拆成 4–8 条要求，区分必需（"熟悉 / 精通 / 有…经验"）和加分（"优先 / 加分"）。每条配 2–5 个关键词，用事实里可能出现的写法，中英文和同义词都写上（如 `["vLLM", "推理部署", "serving"]`）。
3. **配权重**：`profile-atlas techs "<词>"` 查知识地图 slug；必需项对应的 topic / tech 给 0.8–1.0，加分项 0.4–0.6。查不到的技术只写进 requirements 的关键词，不要编 slug。
4. **写岗位文件**：

   ```yaml
   id: jd-acme-agent
   title: Agent 开发工程师            # 用 JD 的岗位名
   headline: 大模型应用 / Agent 开发工程师
   theme: accent                     # 算法岗 minimal，工程 / 应用岗 accent
   description: Acme 智能体团队社招 JD（2026-11）
   jd: |
     （JD 原文）
   requirements:
     - {need: 熟悉 Agent 工具调用与多步规划, keywords: [MCP, 工具调用, tool, ReAct, Agent], must: true}
     - {need: RAG 检索链路调优, keywords: [RAG, 检索, BM25, rerank, Recall], must: true}
     - {need: 有大模型后训练经验优先, keywords: [SFT, DPO, GRPO, LoRA], must: false}
   weights:
     topics: {llm-agents: 1.0, retrieval-and-knowledge-augmentation: 0.9}
     techs: {model-context-protocol: 1.0, tool-use: 1.0}
   limits: {projects: 3, bullets: 3}
   ```

5. **计划后先看匹配**：plan 输出的 `coverage` 列出每条要求有证据的项目，`gaps` 是没有任何证据的要求。必需项大量落空时先告诉用户（这个 JD 匹配度不高），再继续写。
6. 按流程 4–7 写草稿、核对、渲染、汇报。`verify` 的 `jd-must-not-shown` 警告表示有证据的必需项没写进草稿，优先补上。

## 硬规则

1. 不编数字，不改事实，不替用户核实。
2. 隐私数据只在本机的两个目录里，永远不进仓库，也不贴到对话以外的地方。
3. `verify` 不通过不渲染；公开版被拦下时改草稿，不放宽检查。
5. JD 里的要求没有证据就是缺口：在汇报里如实说明，不写进简历。
4. GitHub 上 `status: idea / archived` 的项目、资料里 `verified: false` 的项目，一律不出现在简历里。
