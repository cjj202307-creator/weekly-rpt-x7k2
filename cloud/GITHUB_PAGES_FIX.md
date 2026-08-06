# GitHub Pages 自动更新修复指南（OKR周报）

## 当前进度

- ✅ GitHub Pages 链接已可用：`https://cjj202307-creator.github.io/weekly-rpt-x7k2/`
- ✅ 工作流已在正确路径 `.github/workflows/okr-weekly.yml`
- ✅ `cloud/okr_cloud_report.py` 已推送（最新版：统一色调 + 周环比 + 环形图/条形图 + 可折叠 + HTML历史归档 + 分析时间 + 按O排序）
- ⚠️ 工作流文件需要手动更新为 **v3 版本**（修正 action 版本错误 + 改成"先部署后推送"顺序）
- ⚠️ 需要把 cron 改成周四 17:30（`30 9 * * 4`）
- ⚠️ 需要新增 Secret `DINGTALK_GROUP_WEBHOOK` 才能群推送

---

## 重要：v2 工作流为什么失败

如果你已经按上一版复制过工作流，可能会看到 workflow run 失败。原因是上一版用了不存在的 action 版本：

- ❌ `actions/checkout@v5`（不存在） → ✅ 改成 `actions/checkout@v4`
- ❌ `actions/setup-python@v6`（不存在） → ✅ 改成 `actions/setup-python@v5`

下面 v3 版本已经修正。请直接用 v3 覆盖 `.github/workflows/okr-weekly.yml`。

---

## Step 1：更新工作流文件为 v3

1. 打开 https://github.com/cjj202307-creator/weekly-rpt-x7k2/blob/main/.github/workflows/okr-weekly.yml
2. 点右上角 **铅笔图标**（Edit）
3. **全选删除**，粘贴以下内容：

```yaml
name: OKR Weekly Report

on:
  schedule:
    # 每周四 09:30 UTC = 北京时间 17:30
    - cron: '30 9 * * 4'
  workflow_dispatch:

jobs:
  okr-report:
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pages: read
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Generate OKR Report
        env:
          DINGTALK_APP_KEY: ${{ secrets.DINGTALK_APP_KEY }}
          DINGTALK_APP_SECRET: ${{ secrets.DINGTALK_APP_SECRET }}
          DINGTALK_USER_ID: '17397552280041830'
          REPORT_URL: ${{ vars.REPORT_URL }}
        run: python cloud/okr_cloud_report.py --skip-send

      - name: Deploy HTML to GitHub Pages
        run: |
          mkdir -p docs
          cp cloud/okr-report.html docs/index.html
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add docs/
          git add data/snapshots/
          git commit -m "Update OKR report HTML ($(date +%Y-%m-%d))" || echo "No changes to commit"
          git push

      - name: Wait for GitHub Pages deployment
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          echo "Waiting for GitHub Pages deployment..."
          latest_sha=$(git rev-parse HEAD)
          echo "Latest commit SHA: $latest_sha"
          export LATEST_SHA="$latest_sha"
          for i in {1..30}; do
            resp=$(curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
              -H "Accept: application/vnd.github+json" \
              -H "X-GitHub-Api-Version: 2022-11-28" \
              "https://api.github.com/repos/${{ github.repository }}/pages/deployments?per_page=5")
            status=$(echo "$resp" | python -c "
import sys, json, os
sha = os.environ.get('LATEST_SHA', '')
deployments = json.load(sys.stdin)
if not isinstance(deployments, list):
    print('unknown')
    sys.exit(0)
for d in deployments:
    if d.get('sha') == sha:
        print(d.get('status', 'unknown'))
        sys.exit(0)
print('not_found')
")
            echo "Attempt $i: Pages deployment status = $status"
            if [ "$status" = "success" ]; then
              echo "Pages deployment succeeded"
              exit 0
            elif [ "$status" = "errored" ]; then
              echo "Pages deployment failed"
              exit 1
            fi
            sleep 10
          done
          echo "Timeout waiting for Pages deployment"
          exit 1

      - name: Send OKR Report Notifications
        env:
          DINGTALK_APP_KEY: ${{ secrets.DINGTALK_APP_KEY }}
          DINGTALK_APP_SECRET: ${{ secrets.DINGTALK_APP_SECRET }}
          DINGTALK_USER_ID: '17397552280041830'
          DINGTALK_GROUP_WEBHOOK: ${{ secrets.DINGTALK_GROUP_WEBHOOK }}
          REPORT_URL: ${{ vars.REPORT_URL }}
        run: python cloud/okr_cloud_report.py --send-only
```

4. 点 **Commit changes**

> **v3 关键变化**：
> 1. 修正 action 版本为 `checkout@v4` / `setup-python@v5`
> 2. 顺序改为：生成报告 → 推送到仓库 → **等待 Pages 部署成功** → 发送钉钉消息。这样你收到链接时，页面一定已是最新版。
> 3. cron 改为周四 09:30 UTC = 北京时间 17:30
> 4. 增加 `DINGTALK_GROUP_WEBHOOK` Secret 用于群推送

---

## Step 2：确认/新增 Secrets

打开 https://github.com/cjj202307-creator/weekly-rpt-x7k2/settings/secrets/actions

确认有以下 Secret（如果没有，点 **New repository secret** 添加）：

| 名称 | 说明 |
|------|-----|
| `DINGTALK_APP_KEY` | 钉钉应用 AppKey |
| `DINGTALK_APP_SECRET` | 钉钉应用 AppSecret |
| `DINGTALK_GROUP_WEBHOOK` | 群机器人 webhook 地址（新增，用于群推送） |

> Secrets 配好后只会显示名称，不会显示值——这是正常的。

---

## Step 3：确认 Variable

打开 https://github.com/cjj202307-creator/weekly-rpt-x7k2/settings/variables/actions

确认有以下 Variable：

| 名称 | 值 |
|------|-----|
| `REPORT_URL` | `https://cjj202307-creator.github.io/weekly-rpt-x7k2/` |

---

## Step 4：手动验证

1. 打开 https://github.com/cjj202307-creator/weekly-rpt-x7k2/actions
2. 左侧选 **OKR Weekly Report**
3. 点 **Run workflow** → 确认分支 `main` → 点 **Run**
4. 等待运行完成，日志应依次显示：
   - `Generate OKR Report`：生成 HTML
   - `Deploy HTML to GitHub Pages`：推送到仓库
   - `Wait for GitHub Pages deployment`：轮询直到 `status=success`
   - `Send OKR Report Notifications`：个人 + 群消息发送成功

验证成功后，**每周四 17:30 自动执行**，无需开电脑。

---

## 安全提醒

- 仓库是 Public（GitHub 免费版 Pages 限制），靠难猜的仓库名 + URL 做弱保护
- Secrets 只存在 GitHub 服务器，不会泄露到代码或日志中
- 不要把报告 URL 发到公开渠道
