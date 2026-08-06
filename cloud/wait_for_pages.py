#!/usr/bin/env python3
"""
等待 GitHub Pages 实际可访问到最新报告。

本仓库 Pages 为「从分支部署」：push 到 main 后 GitHub 自动构建，
pages/deployments API 不会返回记录（所以不能用它判断部署是否完成）。

改为直接轮询线上 URL：把刚生成的本地 HTML 作为基准，
当线上页面内容与本次生成的报告一致（含相同的分析时间标记）时，视为已生效。
"""
import os, sys, re, time, hashlib, urllib.request, urllib.error

LOCAL_HTML_DEFAULT = "cloud/okr-report.html"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "okr-weekly-waiter"})
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


def main():
    report_url = os.environ.get("REPORT_URL", "")
    local_path = os.environ.get("LOCAL_HTML", LOCAL_HTML_DEFAULT)

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

    max_attempts = 40
    interval = 15
    for attempt in range(1, max_attempts + 1):
        # 加 cache-buster，避免 CDN 返回旧缓存
        cb = int(time.time())
        url = f"{report_url}?cb={cb}" if "?" not in report_url else f"{report_url}&cb={cb}"
        status, body = fetch(url)
        if status == 200 and marker in body:
            print(f"Attempt {attempt}: 成功（线上页面已是最新报告）")
            sys.exit(0)
        elif status == 200:
            print(f"Attempt {attempt}: HTTP 200，但页面仍是旧版本（CDN 缓存中），继续等待…")
        else:
            print(f"Attempt {attempt}: HTTP {status}，继续等待…")
        if attempt < max_attempts:
            time.sleep(interval)

    print("超时：Pages 仍未上线最新报告（请检查 Pages 构建状态）", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
