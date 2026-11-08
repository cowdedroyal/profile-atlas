# profile-atlas

把你 GitHub 上的项目变成**带证据、按岗位定制**的简历。

AI 时代，自己学着做出来的实验项目也是能力；但直接写进简历，读的人无从核实，也看不出深浅。
profile-atlas 让每个项目带一份证据化清单，再由 coding agent 按岗位组织文字、由工具逐个核对数字，
最后生成单页 HTML / PDF 简历。

```
你的 GitHub（gh 登录）              本机私有资料（永不入库）
  每个项目 .portfolio/project.yaml     ~/.config/profile-atlas/profile.yaml
          │  sync                        姓名、联系方式、教育、实习
          ▼                                        │
  plan：按岗位给项目打分、排序  ◀──────────────────┘
          │
  agent 写简历文字（skill: resume-builder）
          │
  verify：每个数字都要在事实里找得到；smoke 级结果不能写成提升
          │
  render：完整版（本机）+ 匿名公开版，HTML + 单页 PDF
```

## 隐私

工具开源，数据不开源。

- 个人资料只放在 `~/.config/profile-atlas/`，生成的计划、草稿和简历只放在 `~/.local/share/profile-atlas/`（目录权限 700）。
  **这些路径如果位于任何 git 仓库内，工具会拒绝读写**，避免误提交。
- 完整版只在本机，由你自己发给招聘方。
- 公开版是匿名的：只显示 `public_name`；没有电话、邮箱、论文，默认也不放 GitHub 链接；公司和学校只用你给的别称（没给别称的学校不显示）；
  `visibility: hidden` 的项目不出现。生成后会对输出做不区分大小写的扫描，出现姓名、联系方式、公司、学校、GitHub 账号或你列出的词就中止，并删除旧的公开版。

## 快速开始

需要：Python ≥ 3.11、[uv](https://docs.astral.sh/uv/)、已登录的 [gh](https://cli.github.com/)，导出 PDF 需要本机有 Chrome / Chromium。

```bash
git clone https://github.com/cowdedroyal/profile-atlas && cd profile-atlas
uv tool install --editable .                       # profile-atlas 进入 PATH
gh auth status                                     # 工具读的就是这个账号

profile-atlas sync                                 # 拉取你和所属组织全部仓库里的项目清单
profile-atlas profile init                         # 生成 ~/.config/profile-atlas/profile.yaml，填好
profile-atlas profile check
profile-atlas roles                                # 内置岗位：llm-algorithm / agent-dev / search-rec / embodied
profile-atlas plan --role llm-algorithm            # 选项目、排序，输出计划和草稿路径
# 让 agent 按 skill/resume-builder 写草稿，或自己按 schema 写
profile-atlas verify --role llm-algorithm
profile-atlas render --role llm-algorithm --both   # 完整版 + 公开版
profile-atlas site --serve                         # 本机总览各版本：http://127.0.0.1:<port>/
```

## 项目清单

每个项目仓库放一份 `.portfolio/project.yaml`（[schema](src/profile_atlas/schema/project.schema.json)）：用到的技术
（引用 [AI 知识地图](https://cowdedroyal.github.io) 的 slug）、做了什么、取舍、发现、指标，以及每一项的证据在仓库里哪个文件。
真实样例见 [examples/langgraph-agentic-workflow](examples/langgraph-agentic-workflow/project.yaml)。

```bash
profile-atlas init path/to/repo          # 生成清单骨架
profile-atlas check path/to/repo         # 校验：证据文件已提交、引用原文存在、数字能在证据里找到
profile-atlas techs "LoRA" "混合检索"     # 在知识地图里查技术 slug
```

`status` 决定项目能不能进简历：只有 `active` 和 `done` 进入；`idea`（还没开始）和 `archived`（保留但不展示）一律排除。
`visibility` 只决定公开版里的展示：`public` 可以带链接，`summary` 只出文字，`hidden` 不出现。

## Agent skills

| skill | 用途 |
|---|---|
| [project-intake](skill/project-intake/SKILL.md) | 把想法落成私有仓库（或 sandbox 子目录），写清单、打 topic；做出东西后补证据 |
| [resume-builder](skill/resume-builder/SKILL.md) | 同步 → 计划 → 写草稿 → 核对 → 渲染，一页纸 |

```bash
ln -s "$PWD/skill/project-intake" ~/.claude/skills/project-intake
ln -s "$PWD/skill/resume-builder" ~/.claude/skills/resume-builder
```

## 配置

`~/.config/profile-atlas/config.yaml`，全部可选：

```yaml
orgs: [my-lab]                      # 要扫描的组织；默认你所属的全部组织
kmap: https://cowdedroyal.github.io/data/graph.json   # 知识地图；none 表示不用
intake: {org: my-lab, template: my-lab/project-template, workspace: ~/lab}
chrome: /path/to/chrome             # PDF 导出；默认自动查找
```

你自己的岗位定义（比如根据一段 JD 写的）放在 `~/.config/profile-atlas/roles/<id>.yaml`，格式同 [内置岗位](src/profile_atlas/roles/)。

## 开发

```bash
uv sync && uv run pytest
```

开发目录在 iCloud 同步的位置（如 macOS 的 Desktop）时，iCloud 会给 `.venv` 里的文件打 hidden 标记，Python 3.12 会跳过带这个标记的
`.pth`，editable 安装就失效了。把环境放在不同步的目录里：`mkdir .venv.nosync && ln -s .venv.nosync .venv && uv sync`。

设计说明见 [docs/design.md](docs/design.md)。
