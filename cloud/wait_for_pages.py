#!/usr/bin/env python3
"""
等待 GitHub Pages 实际可访问到最新报告。

本仓库 Pages 为「从分支部署(legacy)」：push 到 main/docs 后 GitHub 异步构建，
构建完成前线上 URL 必然是旧版——纯轮询 URL 会一直看到"旧版本"而误判超时。

两阶段策略：
  阶段1：调用 pages/builds/latest API 等构建完成（status==built），构建期间不碰 URL；
  阶段2：构建完成后轮询线上 URL，确认分析时间标记已上线。

容错：
  - 未提供 GH_TOKEN → 跳过阶段1，直接阶段2（仍可用，只是不够精准）；
  - URL 轮询超时 → 降级 exit 0（不阻塞发送通知，页面可能滞后几分钟生效）；
  - 构建明确 errored → exit 1（报告真没上线，不发）。
"""
import os, sys, re, time, json, hashlib, urllib.request, urllib.error

LOCAL_HTML_DEFAULT = "cloud/okr-report.html"


def fetch(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "okr-weekly-waiter"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def extract_marker(html):
    # 报告头含：分析时间：YYYY-MM-DD HH:MM:SS（北京时间）
    m = re.search(
        r"分析时间[：:]\s*([\d]{4}-[\d]{2}-[\d]{2} [\d]{2}:[\d]{2}:[\d]{2})",
        html,
    )
    if m:
        return m.group(1)
    # 兜底：用整页 md5（仅在无分析时间标记时使用）
    return hashlib.md5(html.encode("utf-8")).hexdigest()


def wait_for_build(token, repo):
    """阶段1：等 GitHub Pages 构建完成。返回 built/errored/timeout/no_token。"""
    url = f"https://api.github.com/repos/{repo}/pages/builds/latest"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "okr-weekly-waiter",
    }
    max_attempts, interval = 60, 15  # 最多 ~15 分钟
    for i in range(1, max_attempts + 1):
        status, body = fetch(url, headers)
        if status == 200:
            try:
                d = json.loads(body)
            except Exception:
                d = {}
            st = d.get("status")
            if st == "built":
                print(f"  [构建] 第{i}次：构建完成 ✅（commit {str(d.get('commit',''))[:8]}，用时 {d.get('duration',0)}s）")
                return "built"
            elif st == "building":
                if i == 1 or i % 5 == 0:
                    print(f"  [构建] 第{i}次：构建中…（commit {str(d.get('commit',''))[:8]}，已用 {d.get('duration',0)}s）")
            else:
                err = d.get("error", {})
                print(f"  [构建] 构建异常 status={st} error={err}", file=sys.stderr)
                return "errored"
        elif status == 401 or status == 403:
            print(f"  [构建] builds API HTTP {status}（token 权限不足，降级为仅轮询 URL）")
            return "no_token"
        else:
            if i == 1:
                print(f"  [构建] 第{i}次：builds API HTTP {status}")
            elif i % 10 == 0:
                print(f"  [构建] 第{i}次：builds API HTTP {status}")
        if i < max_attempts:
            time.sleep(interval)
    print("  [构建] 构建超时（~15分钟），转为轮询 URL", file=sys.stderr)
    return "timeout"


def wait_for_url(report_url, marker):
    """阶段2：轮询线上 URL 等新版上线。"""
    max_attempts, interval = 30, 10  # 最多 ~5 分钟
    for attempt in range(1, max_attempts + 1):
        cb = int(time.time())
        url = f"{report_url}?cb={cb}" if "?" not in report_url else f"{report_url}&cb={cb}"
        status, body = fetch(url, {
            "User-Agent": "okr-weekly-waiter",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        })
        if status == 200 and marker in body:
            print(f"  [上线] 第{attempt}次：线上已是最新报告 ✅")
            return True
        elif status == 200:
            print(f"  [上线] 第{attempt}次：HTTP 200 但仍是旧版（CDN 缓存），继续…")
        else:
            print(f"  [上线] 第{attempt}次：HTTP {status}，继续…")
        if attempt < max_attempts:
            time.sleep(interval)
    return False


def main():
    report_url = os.environ.get("REPORT_URL", "")
    local_path = os.environ.get("LOCAL_HTML", LOCAL_HTML_DEFAULT)
    repo = os.environ.get("GITHUB_REPOSITORY", "cjj202307-creator/weekly-rpt-x7k2")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")

    if not report_url:
        print("REPORT_URL 未设置，跳过等待（直接发送）")
        sys.exit(0)
    if not os.path.exists(local_path):
        print(f"本地报告不存在：{local_path}，跳过等待（直接发送）")
        sys.exit(0)

    with open(local_path, "r", encoding="utf-8") as f:
        local_html = f.read()
    marker = extract_marker(local_html)

    print(f"等待 Pages 上线最新报告：{report_url}")
    print(f"判定标记（分析时间）：{marker}")

    # 阶段1：等构建完成（有 token 时）
    if token:
        print("=== 阶段1：等待 GitHub Pages 构建完成 ===")
        bstatus = wait_for_build(token, repo)
        if bstatus == "errored":
            print("❌ Pages 构建失败，报告未上线，本次不发送通知", file=sys.stderr)
            sys.exit(1)
        # built / timeout / no_token 都继续阶段2
    else:
        print("未提供 GH_TOKEN，跳过构建状态检查，直接轮询 URL（建议在 workflow 注入 GH_TOKEN 提升可靠性）")

    # 阶段2：轮询 URL 等上线
    print("=== 阶段2：轮询线上 URL 等新版上线 ===")
    ok = wait_for_url(report_url, marker)
    if ok:
        print("✅ 报告已上线，继续发送通知")
        sys.exit(0)

    # 超时降级：不阻塞发送（页面可能滞后几分钟生效）
    print("⚠️ 超时仍未检测到新版上线（可能是 Pages 构建慢或 CDN 缓存）。降级为继续发送通知——页面可能滞后几分钟生效。", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
