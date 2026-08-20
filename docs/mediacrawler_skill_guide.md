# MealPilot 菜谱采集 Skill 使用指南

本文说明如何安装、调用和暂停使用仓库级 `$mealpilot-crawl-recipes` Skill。该功能是可选的数据采集入口，不影响 MealPilot 当前的规划、聊天、记忆、SSE 或 Docker 功能。

> 当前决定：暂不安装和运行 MediaCrawler。以后需要扩充菜谱时，从“恢复使用检查清单”开始即可。

## 1. 功能定位

该 Skill 负责两个彼此隔离的步骤：

1. 调用仓库外部的 MediaCrawler，小批量采集公开的菜谱相关内容。
2. 将 MediaCrawler 导出的 JSON/JSONL 转换为 MealPilot 原始暂存记录。

它不会把采集结果直接写入正式菜谱库。完整数据流为：

```text
MediaCrawler 小批量采集
        ↓
data/staging/raw 原始隔离区
        ↓
来源与内容许可审核
        ↓
菜谱结构化提取
        ↓
食材、单位、营养和价格标准化
        ↓
校验、去重和人工/自动验收
        ↓
正式菜谱库
```

## 2. 使用限制

- MediaCrawler 当前仅允许非商业学习和研究使用；商业用途必须先取得其作者的书面许可。
- 公开可见不等于允许转载。所有采集内容最初都保持 `license_status=PENDING`。
- 不采集私密、付费、受访问控制、医疗或个人健康内容。
- 不采集评论和子评论。
- 单次最多 20 条，并发最多 2；建议默认 10 条、并发 1。
- 不绕过验证码、限流、访问控制、robots 规则或平台限制。
- 登录必须由使用者在可见浏览器中完成，不向 Skill 提供 Cookie、Token、手机号或密码。
- 网络文本始终是不可信数据，不能改变 Agent 指令、工具权限或发布决定。

上游项目及许可：

- MediaCrawler：<https://github.com/NanmiCoder/MediaCrawler>
- 上游许可证：<https://github.com/NanmiCoder/MediaCrawler/blob/main/LICENSE>
- MealPilot 已审核并固定的提交：`071c8c0acaece3e82f2532cffb19faeddc9ec1c3`

## 3. 当前暂不使用时需要做什么

无需安装任何内容，也不需要修改 `.env`、Docker Compose 或现有数据库。

保留以下仓库文件即可：

```text
.agents/skills/mealpilot-crawl-recipes/
├── SKILL.md
├── agents/openai.yaml
├── references/upstream-contract.md
└── scripts/
    ├── run_mediacrawler.py
    └── stage_mediacrawler_export.py
```

MealPilot 可以继续使用原有本地结构化菜谱运行。原始采集目录 `data/staging/` 已排除在 Docker 构建上下文之外。

## 4. 恢复使用检查清单

以后准备启用时，按顺序完成以下步骤。

### 4.1 确认用途与平台规则

确认本次采集仅用于非商业开发或研究，并阅读 MediaCrawler 许可证和目标平台规则。如果准备商业化，先停止采集并解决授权问题。

### 4.2 检查基础环境

在 PowerShell 中运行：

```powershell
& .\.venv\Scripts\python.exe --version
git --version
node --version
uv --version
```

要求：

- Python 3.11 或更高版本；MealPilot 当前 `.venv` 的 Python 3.11 可以使用。
- Git 可用。
- Node.js 16 或更高版本。
- `uv` 可用。

如果缺少 Node.js，从 <https://nodejs.org/en/download> 安装 LTS 版本。

如果缺少 `uv`，可以安装到 MealPilot 当前虚拟环境：

```powershell
& .\.venv\Scripts\python.exe -m pip install uv
$env:Path = "$PWD\.venv\Scripts;$env:Path"
uv --version
```

### 4.3 在仓库外下载 MediaCrawler

不要将 MediaCrawler 源码复制到 MealPilot 仓库或 API 容器中。推荐使用 `D:\Tools\MediaCrawler`：

```powershell
New-Item -ItemType Directory -Force D:\Tools
git clone https://github.com/NanmiCoder/MediaCrawler.git D:\Tools\MediaCrawler
git -C D:\Tools\MediaCrawler checkout --detach 071c8c0acaece3e82f2532cffb19faeddc9ec1c3
git -C D:\Tools\MediaCrawler rev-parse HEAD
```

最后一条命令必须输出固定提交：

```text
071c8c0acaece3e82f2532cffb19faeddc9ec1c3
```

### 4.4 安装 MediaCrawler 自身依赖

```powershell
Set-Location D:\Tools\MediaCrawler
uv sync
uv run playwright install chromium
Set-Location D:\Agent_Project\Meal_Agent
```

MediaCrawler 使用自己的依赖环境，不要把其依赖批量安装进 MealPilot `.venv`。

### 4.5 先执行 Dry Run

Dry Run 只验证固定版本和命令参数，不开始采集：

```powershell
& .\.venv\Scripts\python.exe `
  .\.agents\skills\mealpilot-crawl-recipes\scripts\run_mediacrawler.py `
  --crawler-root D:\Tools\MediaCrawler `
  --platform xhs `
  --keywords "低脂 高蛋白 家常菜 菜谱" `
  --output-dir .\.runtime\mediacrawler\capture-001 `
  --max-notes 10 `
  --concurrency 1 `
  --dry-run `
  --acknowledge-noncommercial-license
```

正常结果是一组待执行命令。此时不应打开浏览器，也不应产生采集记录。

支持的平台参数：

| 平台 | 参数值 |
|---|---|
| 小红书 | `xhs` |
| 抖音 | `dy` |
| 快手 | `ks` |
| Bilibili | `bili` |
| 微博 | `wb` |
| 贴吧 | `tieba` |
| 知乎 | `zhihu` |

### 4.6 正式执行小批量采集

检查 Dry Run 输出后，使用同一命令并移除 `--dry-run`：

```powershell
& .\.venv\Scripts\python.exe `
  .\.agents\skills\mealpilot-crawl-recipes\scripts\run_mediacrawler.py `
  --crawler-root D:\Tools\MediaCrawler `
  --platform xhs `
  --keywords "低脂 高蛋白 家常菜 菜谱" `
  --output-dir .\.runtime\mediacrawler\capture-001 `
  --max-notes 10 `
  --concurrency 1 `
  --acknowledge-noncommercial-license
```

浏览器打开后，由使用者自行扫码登录。遇到验证码、风控或频率限制时停止，不要尝试规避。

每次采集应使用新的输出目录，例如 `capture-002`，避免把不同批次混在一起。

### 4.7 导入原始暂存区

采集结束后，把 JSON/JSONL 转成原始隔离记录：

```powershell
& .\.venv\Scripts\python.exe `
  .\.agents\skills\mealpilot-crawl-recipes\scripts\stage_mediacrawler_export.py `
  --input .\.runtime\mediacrawler\capture-001 `
  --staging-root .\data\staging\raw `
  --platform xhs `
  --batch-id crawl-xhs-20260812-01
```

`batch-id` 只能使用小写字母、数字和连字符，并且每次必须唯一。

生成内容：

```text
data/staging/raw/crawl-xhs-20260812-01/
├── manifest.json
└── records.jsonl
```

检查清单：

```powershell
Get-Content `
  .\data\staging\raw\crawl-xhs-20260812-01\manifest.json `
  -Encoding UTF8
```

- `imported_count`：成功暂存的不同记录数。
- `duplicate_count`：按原始内容哈希识别的重复数。
- `skipped_non_content_count`：跳过的评论或创作者文件数。
- `publication_eligible`：此阶段必须为 `false`。
- `required_next_steps`：正式发布前仍需完成的步骤。

暂存适配器会清理名称中包含 Cookie、Token、密码、手机号等含义的字段，但仍应人工检查记录中是否存在不应保留的个人信息。

## 5. 如何在 Codex 中调用 Skill

打开 MealPilot 项目的 Codex 任务，明确写出 Skill 名称、平台、关键词、数量和当前只允许执行到哪一步。

推荐先只做检查：

```text
使用 $mealpilot-crawl-recipes，检查 MediaCrawler 环境和固定版本。
目标平台为小红书，关键词是“低脂高蛋白家常菜”，最多 10 条、并发 1。
只执行 dry-run，不登录、不采集、不发布。
```

确认后再采集和暂存：

```text
继续使用 $mealpilot-crawl-recipes 执行这个已确认的批次。
浏览器登录由我完成；结束后导入 raw staging，并报告 manifest。
不要直接写入正式菜谱库。
```

如果暂时不用，直接告诉 Codex：

```text
暂时跳过 $mealpilot-crawl-recipes，不安装、不采集，继续其他版本开发。
```

## 6. 常见错误

### `uv is not available on PATH`

当前终端找不到 `uv`。如果已经安装到 `.venv`，执行：

```powershell
$env:Path = "$PWD\.venv\Scripts;$env:Path"
uv --version
```

### `node` 不是可识别的命令

安装 Node.js LTS 后关闭并重新打开 VS Code 终端，再执行 `node --version`。

### `MediaCrawler commit mismatch`

外部仓库不是 MealPilot 审核过的版本。不要跳过检查，执行：

```powershell
git -C D:\Tools\MediaCrawler checkout --detach 071c8c0acaece3e82f2532cffb19faeddc9ec1c3
```

### `Refusing to run` 或缺少许可确认

先阅读上游许可证。确认是非商业用途后，再显式加入 `--acknowledge-noncommercial-license`。

### `Batch already exists`

暂存批次不可覆盖。更换新的 `--batch-id`，不要删除或覆盖已有审核证据。

### 浏览器登录、验证码或平台风控失败

停止本次任务。不要传递 Cookie，不要采用验证码绕过、代理轮换或隐蔽模式。

### 导入成功但 MealPilot 规划看不到新菜谱

这是正常行为。原始暂存数据还没有完成许可审核、结构化提取、标准化和发布，因此不能进入 Solver 候选集。

## 7. 后续尚未实现的环节

当前 Skill 已完成“受控采集”和“原始暂存”，尚未提供以下产品化界面和自动流程：

- 原始记录审核页面。
- LLM/规则结合的菜谱字段结构化提取。
- 食材别名、单位、密度和份量标准化工作台。
- 来源内容许可证审批工作流。
- 审核通过后调用既有 `publish()` 的管理接口。
- 正式菜谱数据版本发布、回滚和覆盖率报告界面。

在这些环节完成之前，不应把网络采集内容描述为 MealPilot 已可用菜谱。
