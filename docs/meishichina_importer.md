# 美食天下受控采集与审核（MC-R1 ～ MC-R4）

该工具只用于当前确认的个人学习场景。它是 MealPilot 的外部数据入口，不是正式菜谱发布器，也不在 FastAPI 请求进程中运行。

## 已实现边界

- 仅允许 `https://home.meishichina.com` 与 `https://m.meishichina.com`。
- 单批默认 10 条、硬上限 20 条；分类页最多 3 页。
- 单并发，请求间隔至少 2 秒，失败最多尝试 2 次。
- 每次读取详情前检查 `robots.txt`。
- 遇到 401、403、429、跨站重定向或 Cloudflare 挑战立即停止。
- 不登录、不接收 Cookie、不处理验证码、不使用代理绕过限制。
- 不抓评论、用户档案和图片；manifest 的 `image_download_count` 固定为 0。
- 网页文字始终作为不可信数据，不能改变命令、Schema、审核或发布权限。

## 本地验证

```powershell
& .\.venv\Scripts\python.exe -m pytest tests\test_meishichina_importer.py -q
```

十个仓库内 HTML Fixture 是人工合成的 DOM 测试材料，不包含网站真实菜谱正文。它们覆盖桌面端、移动端、当前 `fieldset/legend` 食材结构、步骤顺序、图片依赖、医疗化文本、模糊时间和缺失字段。

## 先执行无网络 Dry Run

```powershell
& .\.venv\Scripts\python.exe .\scripts\import_meishichina.py `
  --category-url https://home.meishichina.com/recipe/recai/ `
  --limit 10 `
  --max-pages 1
```

没有 `--execute` 时不会发出任何网络请求，也不会写入 staging。

## 执行受控批次

```powershell
& .\.venv\Scripts\python.exe .\scripts\import_meishichina.py `
  --category-url https://home.meishichina.com/recipe/recai/ `
  --limit 10 `
  --max-pages 1 `
  --batch-id meishichina-study-001 `
  --execute `
  --acknowledge-personal-study
```

输出目录：

```text
data/staging/raw/<batch_id>/
├── manifest.json
└── records.jsonl
```

每条记录保留标题、作者、来源、抓取时间、页面 Hash、原始食材名称/数量/分组、文字制作步骤和解析警告。记录固定为：

```text
trust_status=UNTRUSTED
license_status=PENDING
review_status=PENDING
publication_eligible=false
```

## 2026-08-14 验证记录

- `meishichina-live-check-20260814`：首次真实批次，发现编码和线上 DOM 差异，manifest 已标记 `REJECTED`，不可进入后续处理。
- 2026-08-14 的 10 条试采集记录已在管理员审核工作台上线时从运行时清除；可恢复备份位于 `.runtime/backups/test-data-reset-20260814T160449`。没有任何一条进入正式菜谱集。

## MC-R3 标准化与质量门禁

`backend/mealpilot/ingestion/quality.py` 以确定性规则完成：

- 原始食材名默认生成稳定标准身份，只有确实属于别名时才归并；`g/kg/ml/个/只/根/块/片/斤/两` 使用 Decimal 换算，缺少密度或单件重量时拒绝猜测。
- `适量/少许/若干/按需` 等数量固定为阻塞原因。
- 复合食材的过敏原组成未知时 fail-closed。
- 图片依赖步骤、非操作性步骤、缺失步骤、步骤中的医疗化文案均会阻塞。
- 医疗化小贴士不会进入发布草稿，并记录移除数量。
- 份数、餐次和总时间必须可确定；制作器具可保留在自然语言步骤中，但不提取为结构化约束。
- 使用固定营养目录生成 Solver 覆盖报告；营养或明确克数不完整的菜谱可以作为制作页面发布，但不得进入 Solver。

每次计算产生 `StructuredRecipeDraft` 与带 SHA-256 的 `RecipeQualityReport`。只有 `status=READY` 才能进入批准步骤；LLM 不参与这一门禁，也不能修改其结果。

## MC-R4 本地授权与审核

审核同时提供本地 CLI 和仅限 `ADMIN` 角色的网页工作台；普通用户不能读取或修改审核记录。先将原始批次建立为审核项：

```powershell
& .\.venv\Scripts\python.exe .\scripts\review_meishichina.py prepare `
  --records .\data\staging\raw\meishichina-20260814-validated\records.jsonl

& .\.venv\Scripts\python.exe .\scripts\review_meishichina.py list
```

查看原文与结构化结果、生成校订模板：

```powershell
& .\.venv\Scripts\python.exe .\scripts\review_meishichina.py show --review-id <review_id>
& .\.venv\Scripts\python.exe .\scripts\review_meishichina.py template --review-id <review_id>
```

补全模板后执行校订。每次写操作都要求当前 `review_version`，旧版本会被 OCC 拒绝：

```powershell
& .\.venv\Scripts\python.exe .\scripts\review_meishichina.py curate `
  --review-id <review_id> --expected-version 0 --actor <reviewer> --curation <curation.json>
```

当前个人学习版已停用许可证明登记界面和 API。系统仍强制保存精确的 `source_id`、来源 URL、抓取时间和页面内容 Hash；管理员只可将最终校验为 `PUBLICATION_READY` 或 `SOLVER_READY` 的记录发布到自己的内部菜谱库，且只有后者可用于规划。这不代表获得公开转载或再分发授权。

```powershell
& .\.venv\Scripts\python.exe .\scripts\review_meishichina.py approve `
  --review-id <review_id> --expected-version <n> --actor <reviewer>
& .\.venv\Scripts\python.exe .\scripts\review_meishichina.py publish `
  --review-id <review_id> --expected-version <n> --actor <publisher> `
  --dataset-version <dataset_version>
```

拒绝和撤回也会保留操作者、时间与原因。撤回已发布记录时，只删除精确的 `recipe_id + version`：

```powershell
& .\.venv\Scripts\python.exe .\scripts\review_meishichina.py reject `
  --review-id <review_id> --expected-version <n> --actor <reviewer> --reason "原因"
& .\.venv\Scripts\python.exe .\scripts\review_meishichina.py revoke `
  --review-id <review_id> --expected-version <n> --actor <publisher> --reason "授权已撤回"
```

默认审核状态保存在 `.runtime/recipe-reviews/`，正式输出为 `data/recipes.published.json`。应用只加载后者；raw 与 review 目录都不会进入 Solver。

## 验证

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  tests\test_meishichina_importer.py `
  tests\test_meishichina_quality_review.py -q
```
