---
name: project-intake
description: 把一个想法落成 GitHub 上的项目，并持续记录进展——查知识地图确定技术、在配置的组织（或个人账号）里建私有仓库（或放进按领域划分的 sandbox 仓库）、写证据化清单 .portfolio/project.yaml、打技术 topic；做出东西后把贡献、取舍、发现和指标连同证据补进清单。当用户说"我想做个… / 想探索一下 X / 新开个项目 / 把这个想法落成仓库 / 记录一下进展 / 更新项目清单 / 这个项目可以毕业了"时使用。
---

# project-intake：想法 → 仓库 → 证据化清单

每个项目都带一份 `.portfolio/project.yaml`，profile-atlas 后续据此生成简历。清单格式见
`<profile-atlas>/src/profile_atlas/schema/project.schema.json`，完整样例见
`<profile-atlas>/examples/langgraph-agentic-workflow/project.yaml`。`<profile-atlas>` 指 profile-atlas 仓库在本机的目录。

## 调用方式

`profile-atlas <命令>`（用 `uv tool install --editable <profile-atlas>` 安装）；不在 PATH 上时用
`uv run --project <profile-atlas> profile-atlas <命令>`。所有命令都输出 JSON。

| 命令 | 用途 |
|---|---|
| `profile-atlas techs "<词>" ["<词>"…]` | 在知识地图里查技术，拿到 slug；中英文、缩写都可以 |
| `profile-atlas new <spec.json> --dry-run` | 校验 spec，打印计划（仓库名、本地路径、topic），**不创建任何东西** |
| `profile-atlas new <spec.json>` | 建仓库（或 sandbox 子目录）、写 idea 清单和 README、提交推送、打 topic |
| `profile-atlas check <项目目录>` | 校验清单和证据；`"ok": true` 才算合格 |
| `profile-atlas topics <项目目录> [--apply]` | 把清单里的技术同步成仓库 topic（只增不删） |

建仓的组织、模板仓库和本地工作区来自 `~/.config/profile-atlas/config.yaml` 的 `intake` 段（不配置时：当前登录账号、不用模板、`~/lab`）。
工作区不要放在 iCloud 之类的同步盘里。下文用 `<org>` 指建仓的组织或账号。

## 模式

| 用户说 | 模式 |
|---|---|
| "我想做个… / 想探索 X / 新开个项目" | **new** |
| "记录一下进展 / 更新清单 / 这个项目做完了" | **record** |
| "这个可以毕业了 / 拆成独立仓库" | **graduate** |

### new：立项

1. **把想法理清楚。** 需要的是：要解决的问题（为什么值得做）、想回答的 1–3 个问题、大致用到的技术。用户已经说清楚的不要再问；
   缺的最多问一轮，问题要具体。
2. **决定放哪里：**
   - **独立仓库**：有自己的问题和代码，预期会写进简历或拿给人看。
   - **sandbox 子目录**（`<org>/sandbox-<领域>`）：读论文复现、单个技术的小实验、一两天的探索。
     领域名用短的 kebab 名，优先沿用已有的：`gh repo list <org> --json name --jq '.[].name'`。
   - 拿不准时选 sandbox；长大了再毕业，比一开始就开一堆空仓库好。
3. **确定技术。** 对每个技术跑 `profile-atlas techs`，从结果里选 slug，不要自己编 slug。
   `depth` 选 `core`（项目围绕它做文章）、`used`（会实际用上并调整）或 `touched`（只是接入）。
   一个项目 3–8 个技术就够了。地图里确实没有的，写成
   `{"slug": "<英文标准名的 kebab 形式>", "depth": …, "proposed": {"name": "<英文标准名>", "name_zh": "…", "kind": "method|system|concept|model|dataset|application"}}`。
4. **写 spec**，存到 `~/.portfolio/specs/<name>.json`：

   ```json
   {
     "name": "lora-rank-ablation",
     "title": "LoRA 秩与目标模块消融",
     "summary": "≤60 字：做什么、解决什么问题",
     "problem": "≤200 字：根本问题、现有做法差在哪",
     "questions": ["想回答的问题"],
     "techs": [{"slug": "lora", "depth": "core"}],
     "sandbox": "llm-posttrain"
   }
   ```

   可选字段：`title_en`、`kind`（默认 personal）、`role`（默认 solo）、`visibility`（默认 hidden）。
   `name` 是英文 kebab，2–63 个字符，同时也是仓库名或子目录名。
5. **`--dry-run`**，修好所有错误，然后用一两行告诉用户要建什么（`组织/仓库` 或 `sandbox-x/子目录`，私有，topic 列表），
   **得到确认再执行** `profile-atlas new`。仓库建了就删不掉（token 没有删除权限），所以名字要先对齐。
6. **汇报**：仓库地址、本地路径、打上的 topic。`kmap_missing` 不为空时，把这些技术交给 kmap skill 加进知识地图
   （装了 kmap skill 时），加完后去掉清单里对应的 `proposed`。

### record：记录进展

1. 找出上次更新清单之后的改动：
   `git log -1 --format=%H -- .portfolio/project.yaml` → `git diff <该提交>..HEAD --stat`，再看关键文件和提交说明。
2. 起草新增内容，每一条都要有证据：
   - `contributions`：做了什么、为什么。
   - `decisions`：选了什么、为什么、放弃了什么。
   - `findings`：发现了什么。失败和反直觉的结果同样要写，`kind` 选 negative / failure / pitfall。
   - `metrics`：指标及其 `setting` 和 `scale`。
   - `evidence`：`quote` 必须从文件里**原样复制**，不能改写。
3. **结果文件要提交。** 指标对应的 metrics JSON、评测表要放进 `docs/metrics/` 并提交，`outputs/` 这类被忽略的目录不算证据。
4. **不替用户编动机。** 代码说明不了的取舍原因，问用户；用户说不清的就不写进 `decisions`。数字只从文件里取，不估算，不四舍五入成更好看的值。
   `scale` 如实填：只验证链路打通的是 smoke，小规模但结论成立的是 pilot。
5. 第一次补进内容时，把 `status` 从 `idea` 改成 `active`；做完了改成 `done`，并填上 `period.end`。
6. `profile-atlas check .` 直到 `"ok": true`，warnings 能修就修。然后提交（`Update portfolio manifest: …`）、推送，
   再跑 `profile-atlas topics . --apply`。

### graduate：从 sandbox 毕业

1. 新建一个 spec，`name` 与子目录同名，不带 `sandbox`，先 `--dry-run`，用户确认后执行 `profile-atlas new`。
2. 把 sandbox 子目录的内容（除 `.portfolio/` 外）拷进新仓库，再用旧清单覆盖新清单。
   需要保留历史时，在 sandbox 仓库里 `git subtree split -P <子目录> -b graduate-<name>`，然后 pull 进新仓库。
3. 在 sandbox 子目录的清单里把 `status` 改成 `archived`，sandbox 的 README 条目后面加上"→ 已毕业：<新仓库链接>"。
4. 两边都要 `check` 通过、提交、推送，并给新仓库跑 `topics --apply`。

## 硬规则

1. **默认私有、默认 `visibility: hidden`。** 只有用户明确要求时才改成 public 或 summary。
2. **公司和实习代码不进 GitHub**，私有仓库也不行。实习经历由 profile-atlas 的私有数据另行管理。
3. **不提交密钥和个人信息**：`.env`、token，以及姓名、手机号、邮箱、学校、公司都不能进仓库（包括 README、清单和提交信息）。提交前看一眼 `git status`。
4. **建仓、推送都是对外操作**：new 模式要先 dry-run，经用户确认再执行；record 模式推送前用一句话说明要推什么。
5. 提交身份用 GitHub noreply 邮箱，`new` 已经在仓库里配好了。手动 clone 的仓库先执行
   `git config user.email <id>+<login>@users.noreply.github.com`。
6. 每次修改清单都要跑 `check`，没有 `"ok": true` 不推送。
