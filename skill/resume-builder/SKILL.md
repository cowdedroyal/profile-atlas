---
name: resume-builder
description: 用 profile-atlas 为当前 gh 登录的 GitHub 账号生成按岗位定制、带证据的简历——同步各仓库的 .portfolio/project.yaml、合并本机私有资料、按岗位选项目、写简历文字、机器核对每个数字、渲染 HTML/PDF 和本机版本总览页。当用户说"生成简历 / 做一份 X 岗位的简历 / 按这个 JD 改简历 / 更新简历 / 出一份公开版简历"时使用。
---

# resume-builder：GitHub 项目清单 → 岗位简历

分工：**你负责写，工具负责核对。** 你根据计划按岗位组织措辞和侧重；数字是否有出处、引用的项目是否存在、smoke 级结果有没有被夸大，由 `profile-atlas verify` 判定，不通过就不能渲染。

所有命令输出 JSON。工具没装时：`uv tool install --editable <profile-atlas 仓库>`。

| 命令 | 作用 |
|---|---|
| `profile-atlas sync` | 用 gh 登录的账号，拉取本人和所属组织全部仓库里的项目清单 |
| `profile-atlas profile check` | 校验本机私有资料（`~/.config/profile-atlas/profile.yaml`），列出未核实的项目 |
| `profile-atlas roles` | 列出岗位和主题 |
| `profile-atlas plan --role <岗位>` | 选项目、排序，生成计划；输出里有计划和草稿的路径 |
| `profile-atlas verify --role <岗位>` | 核对草稿 |
| `profile-atlas render --role <岗位> [--both] [--theme t]` | 核对通过后渲染完整版（和公开版） |
| `profile-atlas site [--serve]` | 本机版本总览页 |

## 隐私（最高优先级）

- 姓名、电话、邮箱、学校、公司、实习细节**只能**存在 `~/.config/profile-atlas/` 和 `~/.local/share/profile-atlas/`。**不要把它们写进任何仓库、提交信息、issue、PR 或公开页面**，也不要复制到工作目录里。工具拒绝读写位于 git 仓库内的私有文件。
- 完整版只在本机，用户自己发给招聘方。公开版是匿名的：工具会扫描输出，出现姓名、联系方式、学校、公司、GitHub 账号或 `public_redact` 里的词就中止，并删除旧的公开版。被拦下时去改草稿措辞，**不要**去改禁止列表。
- 汇报结果时只给文件路径，不要在对话里大段复述完整版内容。

## 流程

1. **准备**：`profile-atlas sync`，然后 `profile-atlas profile check`。
   - 资料不存在：`profile-atlas profile init`，和用户一起填写。从用户已有的简历里导入时，每个项目都要写 `source`；数字是估算、待核对的，`verified: false`。
   - `unverified` 不为空：告诉用户这些项目在核实前不会进入简历。**不要替用户把 `verified` 改成 true。**
2. **确定岗位**：用户给的是预设岗位（`profile-atlas roles`）就直接用。给的是一段 JD，就写一个岗位文件 `~/.config/profile-atlas/roles/<id>.yaml`，格式参考内置岗位：
   `weights.topics` / `weights.techs` 用知识地图的 slug（`profile-atlas techs "<词>"` 查），权重 0–1，按 JD 的核心要求给分；`limits: {projects: 3, bullets: 3}`；`theme` 选 `minimal` 或 `accent`。
3. **计划**：`profile-atlas plan --role <id>`。看 `projects` 的得分与 `selected`、`experience` 各项目的排序、`excluded` 的原因。排序明显不对时，调岗位权重，不要硬选得分为 0 的项目。
4. **写草稿**：读计划文件（路径在 plan 输出里），在 plan 输出给的草稿路径写 `portfolio/resume@1`（结构见工具仓库 `src/profile_atlas/schema/resume.schema.json`）：
   - `experience`：每段实习按计划顺序挑项目；`projects`：用 `selected` 的项目，最多 `limits.projects` 个；每项 `bullets` 不超过 `limits.bullets` 条。
   - 每条 bullet 都要能在该项目的 `facts` 里找到依据：贡献、取舍、发现、指标。可以压缩、合并、调整语序和侧重，**不能新增事实或数字**，也不能把别人的工作写成用户的。
   - 按岗位选角度：同一个项目，算法岗写方法和指标，工程岗写系统和取舍，失败与反直觉的发现（findings）往往最有说服力。
   - `scale: smoke` 的指标只能写成"验证链路打通"，不能和"提升 / 优于 / →"放在一起。
   - `headline`、`summary` 里不要写学校或公司全称（公开版会被拦下）。
   - 排版：每个项目可以用 `**…**` 加粗 1–2 个最关键的数字，或给 bullet 加一个很短的小标签（`**数据构建：**…`）；不要整句加粗，也不要用其他标记。以 `**` 开头的条目在 YAML 里要加引号（`- '**数据构建：**…'`）。
5. **核对**：`profile-atlas verify --role <id>`，逐条修到 `"ok": true`。`number-not-in-facts` 说明数字写错了或者没出处——删掉或改成事实里的原数，不要去改事实。
6. **渲染**：`profile-atlas render --role <id> --both`。页面会在 11.5–14.5px 间自动选字号铺满一页，输出里的 `fill` 是填充率、`font_px` 是选中的字号。
   - `pages` > 1（最小字号也放不下）：先删得分低的项目的 bullet，再合并相近的 bullet，再缩短 tagline。不要改主题 CSS 硬塞。
   - 警告“偏空”（最大字号下 `fill` < 0.85）：加回计划里下一个得分高的项目，或从 facts 里补一条有依据的 bullet；不能为了填满编内容。公开版去掉了 hidden 项目，偏空可以接受。
   - 完整版有证件照需要个人资料里的 `photo`（本机路径，不在 git 仓库里）；公开版永远没有照片。
7. **汇报**：岗位、选了哪些项目（以及为什么不选其他高分项目）、HTML/PDF 路径、页数、被排除的项目和原因、需要用户核实的事项。提醒用户可以运行 `profile-atlas site --serve` 在本机对比各版本。

## 硬规则

1. 不编数字，不改事实，不替用户核实。
2. 隐私数据只在本机的两个目录里，永远不进仓库，也不贴到对话以外的地方。
3. `verify` 不通过不渲染；公开版被拦下时改草稿，不放宽检查。
4. GitHub 上 `status: idea / archived` 的项目、资料里 `verified: false` 的项目，一律不出现在简历里。
