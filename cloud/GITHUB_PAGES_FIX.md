# GitHub Pages 自动更新修复指南（OKR周报）

## 当前进度

- ✅ GitHub Pages 链接已可用：`https://cjj202307-creator.github.io/weekly-rpt-x7k2/`
- ✅ 工作流已在正确路径 `.github/workflows/okr-weekly.yml`（状态：active）
- ✅ `cloud/okr_cloud_report.py` 已推送（最新v3：统一色调 + 周环比 + 环形图/条形图 + 可折叠）
- ✅ `docs/index.html` 已推送（W32基线报告，Pages已可访问）
- ⚠️ 工作流文件需手动更新（加入snapshot持久化步骤，否则周环比对比不生效）
- ❓ Secrets 和 Variable 需确认是否已配置

---

## 你需要做的 3 步（全部在 GitHub 网页操作）

### Step 1：更新工作流文件（加入snapshot持久化）

> 这步让周环比对比功能生效。不做也能跑，只是每周都显示"首周基准数据"。

1. 打开 https://github.com/cjj202307-creator/weekly-rpt-x7k2/blob/main/.github/workflows/okr-weekly.yml
2. 点右上角 **铅笔图标**（Edit）
3. **全选删除**，粘贴以下完整内容：

```yaml
name: OKR Weekly Report

on:
  schedule:
    # 每周五 02:00 UTC = 北京时间 10:00
    - cron: '0 2 * * 5'
  workflow_dispatch: # 允许手动触发

jobs:
  okr-report:
    runs-on: ubuntu-latest
    permissions:
      contents: write  # 需要写权限推送HTML和snapshot到仓库
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Run OKR Report
        env:
          DINGTALK_APP_KEY: ${{ secrets.DINGTALK_APP_KEY }}
          DINGTALK_APP_SECRET: ${{ secrets.DINGTALK_APP_SECRET }}
          DINGTALK_USER_ID: '17397552280041830'
          REPORT_URL: ${{ vars.REPORT_URL }}
        run: python cloud/okr_cloud_report.py

      - name: Deploy HTML to GitHub Pages and persist snapshot
        run: |
          mkdir -p docs
          cp cloud/okr-report.html docs/index.html
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add docs/index.html
          git add data/snapshots/
          git commit -m "Update OKR report HTML and snapshot ($(date +%Y-%m-%d))" || echo "No changes to commit"
          git push
```

4. 点 **Commit changes**

> **变化点**：最后的 Deploy 步骤增加了 `git add data/snapshots/`，让每周数据快照持久化到仓库，下周自动对比。

---

### Step 2：确认 Secrets 已配置（钉钉凭证）

打开 https://github.com/cjj202307-creator/weekly-rpt-x7k2/settings/secrets/actions

确认有以下两个 Secret（如果没有，点 **New repository secret** 添加）：

| 名称 | 值 |
|------|-----|
| `DINGTALK_APP_KEY` | `ding1dxisfpwv9gcbtyb` |
| `DINGTALK_APP_SECRET` | `7zM0cBvTBjFbxnl-eqhGUqg-mj--UeV16v-bRfog4KphLKvSBz3o2AOjPcC7rhlT` |

> Secrets 配好后只会显示名称，不会显示值——这是正常的。

---

### Step 3：确认 Variable 已配置（报告链接）

打开 https://github.com/cjj202307-creator/weekly-rpt-x7k2/settings/variables/actions

确认有以下 Variable（如果没有，点 **New repository variable** 添加）：

| 名称 | 值 |
|------|-----|
| `REPORT_URL` | `https://cjj202307-creator.github.io/weekly-rpt-x7k2/` |

> 不配这个，工作流会拒绝发钉钉消息（脚本内置保护机制）。

---

### Step 4：手动验证一次

1. 打开 https://github.com/cjj202307-creator/weekly-rpt-x7k2/actions
2. 左侧选 **OKR Weekly Report**
3. 点 **Run workflow** → 确认分支 `main` → 点 **Run**
4. 等 1-2 分钟，点进运行记录看日志：
   - 应出现 `发送成功！标题：OKR推进进展周报 Wxx（...）`
   - 钉钉应收到一条带 GitHub Pages 链接的周报

验证成功后，**每周五 10:00 自动执行**，无需开电脑。

---

### Step 5：确认后告诉我

云端 Actions 跑通后，告诉我"云端已验证"。我会把本地 OKR 周报自动化（automation-1785895565220）保持 PAUSED 状态，避免重复消息。

---

## 安全提醒

- 仓库是 Public（GitHub 免费版 Pages 限制），靠难猜的仓库名 + URL 做弱保护
- Secrets 只存在 GitHub 服务器，不会泄露到代码或日志中
- 不要把报告 URL 发到公开渠道
