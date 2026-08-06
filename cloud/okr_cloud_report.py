#!/usr/bin/env python3
"""
OKR推进进展周报 - 云端版（不依赖本地电脑，可在GitHub Actions/云函数上运行）
直接调用钉钉Open API，不需要dws CLI。

环境变量配置：
  DINGTALK_APP_KEY    - 钉钉应用AppKey
  DINGTALK_APP_SECRET - 钉钉应用AppSecret
  DINGTALK_USER_ID    - 接收人userId（默认：17397552280041830）
  REPORT_URL          - 报告URL（必填，无URL拒绝发送避免重复/无链接推送）

需要的应用权限（在钉钉开发者后台申请）：
  1. Notable.Base.Read.All  - AI表格应用读权限
  2. qyapi_get_member       - 通讯录成员读权限（用于userId转unionId）

工作流保护：
  默认要求REPORT_URL非空才发送；显式 --allow-no-url 才跳过此检查（不推荐）。
  推荐两阶段流程：
    阶段1: python okr_cloud_report.py --skip-send       # 只生成HTML
    阶段2: 部署HTML到网络托管 → 拿到URL
    阶段3: REPORT_URL=<URL> python okr_cloud_report.py  # 生成+发送（含链接）
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, date

# ===== 配置 =====
APP_KEY = os.environ.get('DINGTALK_APP_KEY', '')
APP_SECRET = os.environ.get('DINGTALK_APP_SECRET', '')
USER_ID = os.environ.get('DINGTALK_USER_ID', '17397552280041830')
REPORT_URL = os.environ.get('REPORT_URL', '')  # 报告URL（GitHub Pages或CloudStudio）
ROBOT_CODE = APP_KEY  # AppKey即robotCode
BASE_ID = 'EpGBa2Lm8azv7rn5uEONbq3rWgN7R35y'
TABLE_ID = '77lhl1x'

# O目标映射
O_MAP = {
    '深化平台型组织建设': ('O1', '平台型组织建设'),
    '实现订单到回款': ('O2', '订单到回款线上化'),
    '将已验证的里程碑': ('O3', '标准化产品复制'),
    '战略规划与行动小组': ('O4', 'SPA战略规划'),
}

# KR简称映射
KR_TITLES = {
    '3LKpTzKFpX': '三支柱人员定岗与职责边界梳理',
    'fZi3GnHztG': '核心岗位覆盖率≥90%',
    'jHZW9OdTw5': '智能体应用基础培训覆盖率100%',
    'G9rDLO7dh7': '智能体工具核心场景落地≥5个',
    'Ft23VyEwG7': '智能体AI+数智化人才≥10人',
    '4M5ay4AH6t': '区域唯一码颗粒度定义与覆盖率100%',
    'GokzKTd2RN': 'WMS/TMS/FMS/COS系统100%执行使用',
    'O43l2cDgwN': 'CPQ报价结算标准化（新客户100%）',
    'fWmksU3dRi': '大订单标准化执行率100%',
    'qPCawwav6O': 'O2C线下断点清单补全',
    'AJ6qyRqFiU': '推广泓明订单标准',
    '9F6YBb7Uhc': '国内采购场景标准化SOP（菲尼萨等）',
    'W5O3oHM2Wg': '服务类产品推广新增≥4家',
    'YrG9KuJPJE': '搬运服务场景标准化SOP',
    'a9vDqVs3hV': '成都华虹/合肥长鑫标准化解决方案',
    'uqEwQjZjmM': '每季度收集卡点≥3条',
    'ykxn6qP2KF': '数智化应用复制推广',
    'L9rbCrMLDR': '输出≥2项区域业务洞察与优化建议',
    'c7DV9KzqG2': '每季度战略复盘会闭环率100%',
    'dxRhOqFf0c': 'OKR拆解对齐通过率100%',
}

# ===== 钉钉API调用 =====

def api_request(url, method='GET', headers=None, body=None):
    """通用HTTP请求"""
    data = json.dumps(body).encode() if body else None
    hdrs = {'Content-Type': 'application/json'}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        try:
            err_json = json.loads(err_body)
            return err_json
        except:
            return {'error': err_body}

def get_access_token():
    """获取企业内部应用accessToken"""
    url = 'https://api.dingtalk.com/v1.0/oauth2/accessToken'
    result = api_request(url, 'POST', body={'appKey': APP_KEY, 'appSecret': APP_SECRET})
    token = result.get('accessToken', '')
    if not token:
        print(f'获取token失败: {json.dumps(result, ensure_ascii=False)}', file=sys.stderr)
        sys.exit(1)
    return token

def get_union_id(access_token, user_id):
    """通过userId获取unionId（需要qyapi_get_member权限）"""
    url = f'https://oapi.dingtalk.com/topapi/v2/user/get?access_token={access_token}'
    result = api_request(url, 'POST', body={'userid': user_id})
    if result.get('errcode') != 0:
        print(f'获取unionId失败（需开通qyapi_get_member权限）: {result.get("errmsg", "")}', file=sys.stderr)
        return None
    return result.get('result', {}).get('unionid', '')

def list_records(access_token, operator_id):
    """获取AI表格所有记录（需要Notable.Base.Read.All权限）"""
    url = f'https://api.dingtalk.com/v1.0/notable/bases/{BASE_ID}/sheets/{TABLE_ID}/records/list?operatorId={operator_id}'
    headers = {'x-acs-dingtalk-access-token': access_token}
    all_records = []
    next_token = None
    while True:
        body = {'maxResults': 100}
        if next_token:
            body['nextToken'] = next_token
        result = api_request(url, 'POST', headers=headers, body=body)
        if 'error' in result or 'code' in result:
            print(f'获取记录失败（需开通Notable.Base.Read.All权限）: {json.dumps(result, ensure_ascii=False)[:200]}', file=sys.stderr)
            return None
        records = result.get('records', [])
        all_records.extend(records)
        if not result.get('hasMore'):
            break
        next_token = result.get('nextToken')
    return all_records

def send_robot_message(access_token, title, markdown_text):
    """通过钉钉机器人发送Markdown消息到个人单聊"""
    url = 'https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend'
    headers = {'x-acs-dingtalk-access-token': access_token}
    msg_param = json.dumps({'title': title, 'text': markdown_text})
    body = {
        'robotCode': ROBOT_CODE,
        'userIds': [USER_ID],
        'msgKey': 'sampleMarkdown',
        'msgParam': msg_param
    }
    result = api_request(url, 'POST', headers=headers, body=body)
    return result

# ===== 数据处理 =====

def parse_records(records):
    """解析API返回的记录（API用中文字段名，返回name而非userId）"""
    krs = []
    today = date.today()

    for rec in records:
        fields = rec.get('fields', {})
        if not fields or '关键结果描述' not in fields:
            # 也检查OKR关键结果字段
            if not fields or ('关键结果描述' not in fields and 'OKR关键结果' not in fields):
                continue

        rid = rec.get('id', '')
        o_text = fields.get('集团目标', '')
        o_code, o_title = '', ''
        for key, (code, title) in O_MAP.items():
            if key in o_text:
                o_code, o_title = code, title
                break

        # KR: 优先用OKR关键结果(短标题), 没有则用关键结果描述
        kr_text = fields.get('OKR关键结果', '') or fields.get('关键结果描述', '')
        kr_short = KR_TITLES.get(rid, kr_text[:25])

        # 跟进人 - API返回{unionId, name}
        followers_raw = fields.get('跟进人', [])
        follower_names = []
        if isinstance(followers_raw, list):
            for f in followers_raw:
                if isinstance(f, dict):
                    name = f.get('name', '')
                    if name:
                        follower_names.append(name)

        # 进度
        progress_raw = fields.get('推进进度', '')
        progress = None
        if progress_raw and str(progress_raw).strip():
            try:
                progress = round(float(progress_raw) * 100)
            except:
                pass

        # 状态
        status_obj = fields.get('完成状态', None)
        status = None
        if isinstance(status_obj, dict):
            status = status_obj.get('name')

        # 截止日期 - API没有直接返回, 用目标周期(timestamp)作为近似
        deadline = None
        period = fields.get('目标周期')
        if period and isinstance(period, (int, float)):
            try:
                deadline = datetime.fromtimestamp(int(period) / 1000).date().isoformat()
            except:
                pass

        # 卡点
        blocker = fields.get('卡点', '')

        # 网点
        sites_obj = fields.get('所属网点', [])
        sites = '—'
        if isinstance(sites_obj, list) and sites_obj:
            sites = '/'.join([s.get('name', '') for s in sites_obj if isinstance(s, dict)])

        # 关键结果描述全文（进展详情，总经理主要看这个）
        kr_desc = fields.get('关键结果描述', '') or ''

        # 网点目标
        site_goal = fields.get('网点目标', '') or ''

        krs.append({
            'o': o_code, 'oTitle': o_title,
            'kr': kr_short, 'krFull': kr_text,
            'krDesc': kr_desc, 'siteGoal': site_goal,
            'followers': follower_names, 'followerCount': len(follower_names),
            'progress': progress, 'status': status,
            'deadline': deadline, 'blocker': blocker,
            'sites': sites, 'recordId': rid,
        })

    return krs

def analyze(krs, today):
    """分析数据，生成指标（GM视角：以关键结果描述=最新进展为核心）"""
    # O分组平均
    o_groups = {}
    for kr in krs:
        if kr['o'] not in o_groups:
            o_groups[kr['o']] = {'title': kr['oTitle'], 'krs': []}
        o_groups[kr['o']]['krs'].append(kr)

    o_avgs = {}
    for o, g in o_groups.items():
        tracked = [k for k in g['krs'] if k['progress'] is not None]
        o_avgs[o] = round(sum(k['progress'] for k in tracked) / len(tracked)) if tracked else 0

    overall_avg = round(sum(o_avgs.values()) / len(o_avgs)) if o_avgs else 0

    # 过期判断
    def is_overdue(kr):
        if not kr['deadline']: return False
        if kr['progress'] and kr['progress'] >= 100: return False
        try:
            return date.fromisoformat(kr['deadline']) < today
        except: return False

    def is_risk(kr):
        if kr['progress'] == 0: return True
        if kr['progress'] is not None and kr['progress'] <= 30: return True
        if kr['status'] == '未开始': return True
        if is_overdue(kr): return True
        return False

    def days_overdue(kr):
        if not kr['deadline']: return 0
        try:
            d = date.fromisoformat(kr['deadline'])
            diff = (today - d).days
            return diff if diff > 0 else 0
        except: return 0

    # 无效描述关键词（这些不算实质进展）
    INVALID_DESC = {'', '无', '暂无', '暂无进展', '没有', '-', '—', '/', 'n/a', 'na', 'null', 'none'}

    # 关键结果描述分析（GM核心指标）
    def has_update(kr):
        """是否有实质进展描述（非空且非无效关键词）"""
        desc = (kr.get('krDesc') or '').strip()
        if desc.lower() in INVALID_DESC:
            return False
        return len(desc) >= 2  # 至少2字且非无效关键词

    def update_quality(kr):
        """进展描述质量：0=无 / 1=简短(2-20字) / 2=详细(>20字)"""
        if not has_update(kr): return 0
        desc = (kr.get('krDesc') or '').strip()
        if len(desc) < 20: return 1
        return 2

    def is_stale(kr):
        """是否停滞：无实质进展描述 或 进度为0（已达成100%不算停滞）"""
        if kr['progress'] == 100:
            return False  # 已达成不算停滞
        return not has_update(kr) or kr['progress'] == 0

    risk_count = sum(1 for k in krs if is_risk(k))
    overdue_count = sum(1 for k in krs if is_overdue(k))
    untracked_count = sum(1 for k in krs if k['progress'] is None)
    nostatus_count = sum(1 for k in krs if not k['status'])
    done_count = sum(1 for k in krs if k['progress'] == 100)

    # 本周进展统计（新增）
    updated_count = sum(1 for k in krs if has_update(k))
    detailed_count = sum(1 for k in krs if update_quality(k) == 2)
    stale_count = sum(1 for k in krs if is_stale(k))

    # 跟进人责任矩阵
    resp_map = {}
    for i, kr in enumerate(krs):
        for fid in kr['followers']:
            if fid not in resp_map:
                resp_map[fid] = []
            resp_map[fid].append(i)

    resp_sorted = sorted(resp_map.items(), key=lambda x: -len(x[1]))

    return {
        'krs': krs, 'o_groups': o_groups, 'o_avgs': o_avgs,
        'overall_avg': overall_avg,
        'risk_count': risk_count, 'overdue_count': overdue_count,
        'untracked_count': untracked_count, 'nostatus_count': nostatus_count,
        'done_count': done_count,
        'updated_count': updated_count,
        'detailed_count': detailed_count,
        'stale_count': stale_count,
        'is_overdue': is_overdue, 'is_risk': is_risk,
        'days_overdue': days_overdue,
        'has_update': has_update, 'update_quality': update_quality,
        'is_stale': is_stale,
        'resp_sorted': resp_sorted,
    }

def get_iso_week(d=None):
    """返回ISO周数，如 W30"""
    d = d or date.today()
    iso = d.isocalendar()
    return f'W{iso[1]:02d}'

def generate_markdown(a, today_str, report_url=None, week_str=''):
    """生成Markdown摘要（GM结构化精简版：不照搬描述原文，只列要点，细节看网页）"""
    krs = a['krs']
    lines = []
    title_suffix = f' {week_str}' if week_str else ''
    lines.append(f'## OKR推进进展周报{title_suffix}')
    lines.append(f'{today_str}')
    lines.append('')

    # 核心数字（一行）
    lines.append(f'**整体进度 {a["overall_avg"]}%** ｜ **{a["updated_count"]}**项有进展 ｜ **{a["stale_count"]}**项停滞 ｜ **{a["overdue_count"]}**项过期')
    lines.append('')

    # 各O进展概览（按O分组，只列KR名+进度，不搬描述）
    lines.append('### 各目标进展')
    lines.append('')
    for o_code in ['O1', 'O2', 'O3', 'O4']:
        g = a['o_groups'].get(o_code, {'title': '', 'krs': []})
        if not g['krs']:
            continue
        avg = a['o_avgs'].get(o_code, 0)
        updated_in_o = [k for k in g['krs'] if a['has_update'](k)]
        lines.append(f'**{o_code} {g["title"]}**（{avg}%｜{len(updated_in_o)}/{len(g["krs"])}项有进展）')
        # 列有进展的KR（最多3条，只写名称+进度）
        for kr in updated_in_o[:3]:
            prog = f'{kr["progress"]}%' if kr['progress'] is not None else '未录入'
            overdue_mark = ' `[过期]`' if a['is_overdue'](kr) else ''
            lines.append(f'- {kr["kr"]} {prog}{overdue_mark}')
        if len(updated_in_o) > 3:
            lines.append(f'- …等{len(updated_in_o)}项')
        lines.append('')

    # 需关注（精简到2-3条，说重点）
    def _short_name(name, maxlen=16):
        """截断KR名，超过maxlen加省略号"""
        return name[:maxlen] + '…' if len(name) > maxlen else name

    risks = []
    if a['overdue_count'] > 0:
        overdue_krs = [k for k in krs if a['is_overdue'](k)]
        names = '、'.join(_short_name(k['kr']) for k in overdue_krs[:3])
        more = f'等{len(overdue_krs)}项' if len(overdue_krs) > 3 else ''
        risks.append(f'{a["overdue_count"]}项过期未完成：{names}{more}')
    stale_krs = [k for k in krs if a['is_stale'](k)]
    if stale_krs:
        names = '、'.join(_short_name(k['kr']) for k in stale_krs[:2])
        risks.append(f'{len(stale_krs)}项停滞：{names}')

    if risks:
        lines.append('### 需关注')
        lines.append('')
        for r in risks[:3]:
            lines.append(f'- {r}')
        lines.append('')

    # 完整报告链接
    if report_url:
        lines.append(f'[查看完整周报（含全部进展详情与跟进人）→]({report_url})')
    else:
        lines.append('*（本次未生成在线报告链接）*')

    return '\n'.join(lines)

# ===== HTML报告生成 =====

CSS_TEXT = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; background: #f5f6f8; color: #2c3e50; line-height: 1.6; }
.container { max-width: 1200px; margin: 0 auto; padding: 20px; }
.header { background: #fff; border-radius: 10px; padding: 28px 32px; margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
.header h1 { font-size: 22px; font-weight: 700; color: #1a1a2e; margin-bottom: 6px; }
.header .meta { font-size: 13px; color: #8898aa; }
.header .meta span { margin-right: 16px; }
.header .badge-base { display: inline-block; background: #e3f2fd; color: #1976d2; font-size: 11px; padding: 2px 10px; border-radius: 10px; font-weight: 600; }
.exec-summary { background: #fff; border-radius: 10px; padding: 24px 32px; margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); border-left: 4px solid #e74c3c; }
.exec-summary h2 { font-size: 16px; font-weight: 700; color: #1a1a2e; margin-bottom: 12px; }
.exec-summary ul { list-style: none; }
.exec-summary li { font-size: 14px; color: #2c3e50; padding: 6px 0; padding-left: 20px; position: relative; }
.exec-summary li::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: #e74c3c; position: absolute; left: 0; top: 12px; }
.exec-summary li.warn::before { background: #f39c12; }
.exec-summary li.info::before { background: #3498db; }
.exec-summary strong { color: #1a1a2e; }
.metrics { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 16px; }
.metric-card { background: #fff; border-radius: 10px; padding: 18px 16px; text-align: center; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
.metric-card .num { font-size: 28px; font-weight: 700; margin-bottom: 4px; }
.metric-card .label { font-size: 11px; color: #8898aa; text-transform: uppercase; letter-spacing: 0.5px; }
.metric-card.green .num { color: #27ae60; }
.metric-card.red .num { color: #e74c3c; }
.metric-card.gray .num { color: #95a5a6; }
.metric-card.dark .num { color: #2c3e50; }
.section { background: #fff; border-radius: 10px; padding: 24px 32px; margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
.section h2 { font-size: 16px; font-weight: 700; color: #1a1a2e; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 2px solid #f0f0f0; }
.o-chart { display: flex; flex-direction: column; gap: 14px; }
.o-bar-row { display: flex; align-items: center; gap: 12px; }
.o-bar-label { width: 280px; font-size: 13px; font-weight: 600; color: #2c3e50; flex-shrink: 0; }
.o-bar-label small { display: block; font-weight: 400; color: #8898aa; font-size: 11px; }
.o-bar-track { flex: 1; height: 32px; background: #f0f0f0; border-radius: 6px; overflow: hidden; position: relative; }
.o-bar-fill { height: 100%; border-radius: 6px; display: flex; align-items: center; justify-content: flex-end; padding-right: 10px; transition: width 0.8s ease; }
.o-bar-fill span { color: #fff; font-size: 13px; font-weight: 700; }
.o-bar-stats { width: 120px; font-size: 12px; color: #8898aa; flex-shrink: 0; text-align: right; }
.filters { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.filter-btn { padding: 6px 16px; border: 1.5px solid #e0e0e0; border-radius: 20px; background: #fff; font-size: 13px; color: #555; cursor: pointer; transition: all 0.2s; }
.filter-btn:hover { border-color: #3498db; color: #3498db; }
.filter-btn.active { background: #3498db; color: #fff; border-color: #3498db; }
.filter-btn .count { font-size: 11px; opacity: 0.8; margin-left: 4px; }
.kr-table-wrap { overflow-x: auto; }
table.kr-table { width: 100%; border-collapse: collapse; font-size: 13px; }
table.kr-table th { background: #f8f9fa; color: #8898aa; font-weight: 600; text-align: left; padding: 10px 12px; border-bottom: 2px solid #e8e8e8; cursor: pointer; white-space: nowrap; position: sticky; top: 0; }
table.kr-table td { padding: 14px 12px; border-bottom: 1px solid #eee; vertical-align: top; }
table.kr-table tr:hover { background: #f8f9fc; }
table.kr-table .o-cell { font-weight: 600; white-space: nowrap; }
table.kr-table .kr-cell { max-width: 280px; }
table.kr-table .kr-cell small { color: #8898aa; font-size: 11px; }
.prog-cell { width: 140px; }
.prog-bar { height: 8px; background: #f0f0f0; border-radius: 4px; overflow: hidden; display: inline-block; width: 80px; vertical-align: middle; margin-right: 6px; }
.prog-fill { height: 100%; border-radius: 4px; }
.prog-text { font-size: 12px; font-weight: 600; vertical-align: middle; }
.badge { display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 600; white-space: nowrap; }
.badge-red { background: #ffe0e0; color: #c0392b; }
.badge-gray { background: #eee; color: #888; }
.badge-blue { background: #e3f2fd; color: #1976d2; }
.badge-teal { background: #e0f7fa; color: #00838f; }
.resp-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.resp-table th { background: #f8f9fa; color: #8898aa; font-weight: 600; text-align: left; padding: 10px 12px; border-bottom: 2px solid #e8e8e8; }
.resp-table td { padding: 10px 12px; border-bottom: 1px solid #f0f0f0; }
.resp-table .bar-mini { height: 6px; border-radius: 3px; display: inline-block; width: 60px; vertical-align: middle; margin-right: 6px; }
.risk-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.risk-card { border-radius: 8px; padding: 16px 20px; border-left: 4px solid; }
.risk-card.high { background: #fff5f5; border-color: #e74c3c; }
.risk-card.medium { background: #fffaf3; border-color: #f39c12; }
.risk-card .risk-title { font-size: 14px; font-weight: 700; margin-bottom: 6px; }
.risk-card.high .risk-title { color: #c0392b; }
.risk-card.medium .risk-title { color: #e67e22; }
.risk-card .risk-detail { font-size: 13px; color: #555; margin-bottom: 8px; line-height: 1.8; }
.risk-card .risk-detail .kr-item { display: block; padding: 2px 0; }
.risk-card .risk-action { font-size: 12px; color: #777; padding-top: 8px; border-top: 1px dashed #ddd; }
.risk-card .risk-action strong { color: #333; }
.dq-list { list-style: none; }
.dq-list li { font-size: 13px; padding: 8px 0; padding-left: 24px; position: relative; border-bottom: 1px solid #f8f8f8; }
.dq-list li::before { content: "!"; position: absolute; left: 0; top: 8px; width: 18px; height: 18px; background: #f39c12; color: #fff; border-radius: 50%; text-align: center; line-height: 18px; font-size: 11px; font-weight: 700; }
.dq-list li.dq-red::before { background: #e74c3c; }
.dq-list .dq-items { color: #888; font-size: 12px; margin-top: 4px; line-height: 1.8; }
.dq-list .dq-items .kr-item { display: block; padding: 1px 0; }
/* 响应式：小屏幕优化 */
@media (max-width: 768px) {
  .container { padding: 10px; }
  .header { padding: 18px 20px; }
  .section { padding: 16px 18px; }
  .metrics { grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .metric-card { padding: 14px 10px; }
  .metric-card .num { font-size: 22px; }
  .risk-grid { grid-template-columns: 1fr; }
  .kr-follower-item { flex-wrap: wrap; gap: 6px; }
  .kr-follower-item .kr-fol-list { max-width: 100%; }
  .o-bar-label { width: 200px; font-size: 12px; }
  .o-bar-stats { width: 90px; font-size: 11px; }
  .kr-detail-content { grid-template-columns: 1fr; }
}
.footer { text-align: center; font-size: 12px; color: #aaa; padding: 20px 0; }
.footer a { color: #3498db; text-decoration: none; }
.kr-row.hidden { display: none; }
.blocker-text { font-size: 12px; color: #666; max-width: 200px; }
.blocker-none { color: #ccc; font-style: italic; font-size: 12px; }
/* 跟进人名字标签 */
.follower-tags { display: flex; flex-wrap: wrap; gap: 4px; max-width: 200px; }
.follower-tag { display: inline-block; font-size: 10px; padding: 2px 7px; border-radius: 8px; background: #e0f7fa; color: #006064; white-space: nowrap; cursor: pointer; transition: all 0.2s; }
.follower-tag:hover { background: #00bcd4; color: #fff; }
.follower-more { font-size: 10px; color: #888; padding: 2px 4px; }
/* 可展开行 */
.kr-row { cursor: pointer; transition: background 0.2s; }
.kr-row .kr-arrow { display: inline-block; width: 14px; transition: transform 0.2s; font-size: 10px; color: #aaa; }
.kr-row.expanded .kr-arrow { transform: rotate(90deg); }
.kr-detail-row td { background: #fafbfc; border-left: 3px solid #3498db; padding: 16px 20px; }
.kr-detail-content { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; padding: 4px 8px; }
.kr-detail-block h4 { font-size: 12px; color: #8898aa; margin-bottom: 6px; font-weight: 600; }
.kr-detail-block .desc-text { font-size: 13px; color: #2c3e50; line-height: 1.8; white-space: pre-wrap; word-break: break-word; }
.kr-detail-block .desc-text.empty { color: #ccc; font-style: italic; }
/* tooltip */
.tooltip { position: relative; }
.tooltip:hover::after { content: attr(data-tip); position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%); background: #1a1a2e; color: #fff; padding: 8px 14px; border-radius: 6px; font-size: 12px; white-space: pre-line; z-index: 1000; margin-bottom: 4px; max-width: 320px; line-height: 1.8; box-shadow: 0 4px 12px rgba(0,0,0,0.2); }
.tooltip:hover::before { content: ""; position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%); border: 5px solid transparent; border-top-color: #1a1a2e; z-index: 1000; }
/* 指标卡片hover */
.metric-card { transition: transform 0.2s, box-shadow 0.2s; cursor: help; }
.metric-card:hover { transform: translateY(-3px); box-shadow: 0 4px 12px rgba(0,0,0,0.12); }
.metric-card .tip-list { display: none; position: absolute; background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 8px 12px; font-size: 12px; color: #555; box-shadow: 0 4px 12px rgba(0,0,0,0.1); z-index: 100; max-width: 280px; max-height: 200px; overflow-y: auto; }
.metric-card { position: relative; }
.metric-card:hover .tip-list { display: block; }
/* O柱图hover */
.o-bar-row { transition: background 0.2s; padding: 4px 8px; border-radius: 6px; }
.o-bar-row:hover { background: #f0f7ff; }
.o-bar-detail { display: none; margin-top: 6px; padding: 6px 10px; background: #f8f9fa; border-radius: 4px; font-size: 12px; }
.o-bar-row:hover .o-bar-detail { display: block; }
/* KR跟进人一览 */
.kr-follower-list { display: flex; flex-direction: column; gap: 0; }
.kr-follower-item { display: flex; align-items: flex-start; gap: 12px; padding: 14px 16px; border-bottom: 1px solid #eee; transition: background 0.2s; }
.kr-follower-item:last-child { border-bottom: none; }
.kr-follower-item:hover { background: #f8f9fc; }
.kr-follower-item .kr-name { font-size: 13px; font-weight: 600; color: #2c3e50; flex: 1; min-width: 0; line-height: 1.8; }
.kr-follower-item .kr-prog { font-size: 13px; font-weight: 700; flex-shrink: 0; min-width: 50px; text-align: right; }
.kr-follower-item .kr-fol-list { font-size: 11px; color: #888; flex-shrink: 0; max-width: 320px; display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.kr-follower-item .kr-fol-list .follower-tag { font-size: 10px; }
.kr-follower-item .o-cell { display: inline-block; background: #e3f2fd; color: #1976d2; font-size: 10px; padding: 1px 6px; border-radius: 8px; font-weight: 700; margin-right: 6px; }
.kr-fol-group-header { font-size: 14px; font-weight: 700; color: #1a1a2e; padding: 12px 16px 6px; background: #f8f9fa; border-radius: 6px 6px 0 0; margin-top: 8px; border-left: 3px solid #3498db; }
.kr-fol-group-header:first-child { margin-top: 0; }
/* 本周进展亮点（GM核心板块） */
.o-badge { display: inline-block; background: #1a1a2e; color: #fff; font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 700; margin-right: 8px; }
.ph-o-header { font-size: 15px; font-weight: 700; color: #1a1a2e; padding: 14px 4px 10px; margin-top: 18px; border-bottom: 2px solid #e8e8e8; display: flex; align-items: center; gap: 4px; }
.ph-o-header:first-child { margin-top: 0; }
.ph-count { margin-left: auto; font-size: 11px; color: #8898aa; font-weight: 400; background: #f0f0f0; padding: 2px 10px; border-radius: 10px; }
.ph-card { background: #fafbfc; border: 1px solid #eceff3; border-left: 3px solid #3498db; border-radius: 6px; padding: 14px 18px; margin: 10px 0; }
.ph-head { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 6px; }
.ph-title { flex: 1; font-size: 14px; font-weight: 700; color: #1a1a2e; line-height: 1.5; }
.ph-prog { font-size: 18px; font-weight: 700; flex-shrink: 0; }
.ph-meta { font-size: 12px; color: #8898aa; margin-bottom: 8px; }
.ph-desc { font-size: 13px; color: #2c3e50; line-height: 1.8; white-space: pre-wrap; word-break: break-word; background: #fff; padding: 10px 14px; border-radius: 6px; border-left: 2px solid #3498db; }
.ph-blocker { font-size: 12px; color: #c0392b; margin-top: 8px; padding: 8px 12px; background: #fff5f5; border-radius: 4px; }
/* 停滞预警 */
.stale-item { padding: 10px 14px; border-bottom: 1px solid #f0f0f0; }
.stale-item:last-child { border-bottom: none; }
.stale-name { font-size: 13px; font-weight: 600; color: #2c3e50; margin-bottom: 4px; }
.stale-info { font-size: 12px; color: #8898aa; }
.stale-reason { color: #c0392b; }
.empty-state { text-align: center; padding: 30px; color: #95a5a6; font-size: 13px; }
/* 周数徽章 */
.week-badge { display: inline-block; background: #1a1a2e; color: #fff; font-size: 13px; padding: 3px 12px; border-radius: 6px; font-weight: 700; margin-left: 10px; vertical-align: middle; }
/* 吸顶导航栏 */
html { scroll-behavior: smooth; }
.section, .exec-summary, .metrics { scroll-margin-top: 70px; }
.topnav { position: sticky; top: 0; z-index: 100; background: #fff; border-radius: 10px; padding: 10px 16px; margin-bottom: 16px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); display: flex; gap: 4px; overflow-x: auto; scrollbar-width: none; }
.topnav::-webkit-scrollbar { display: none; }
.topnav a { flex-shrink: 0; padding: 6px 14px; font-size: 13px; color: #555; text-decoration: none; border-radius: 16px; white-space: nowrap; transition: all 0.2s; }
.topnav a:hover { background: #e3f2fd; color: #1976d2; }
.topnav a.nav-highlight { background: #1a1a2e; color: #fff; font-weight: 600; }
.topnav a.nav-highlight:hover { background: #3498db; }
@media (max-width: 768px) {
  .topnav { padding: 8px 10px; }
  .topnav a { padding: 5px 10px; font-size: 12px; }
}
"""

JS_FUNCS = """
// 只保留交互增强：筛选、排序、展开明细
function progColor(p) {
  if (p === null) return '#ccc';
  if (p >= 100) return '#1b5e20';
  if (p >= 70) return '#27ae60';
  if (p >= 30) return '#f39c12';
  return '#e74c3c';
}
function isPastDueIncomplete(kr) {
  if (!kr.deadline) return false;
  if (kr.progress >= 100) return false;
  return new Date(kr.deadline) < today;
}
function daysOverdue(d) {
  if (!d) return 0;
  const diff = Math.floor((today - new Date(d)) / (1000*60*60*24));
  return diff > 0 ? diff : 0;
}
function isRisk(kr) {
  if (kr.progress === 0) return true;
  if (kr.progress !== null && kr.progress <= 30) return true;
  if (kr.status === '未开始') return true;
  if (isPastDueIncomplete(kr)) return true;
  return false;
}
function renderKRTable(filter) {
  const tbody = document.getElementById('krBody');
  const rows = okrData.map((kr, idx) => {
    const overdue = isPastDueIncomplete(kr);
    const risk = isRisk(kr);
    const done = kr.progress === 100;
    const untracked = kr.progress === null;
    let hidden = false;
    if (filter === 'risk' && !risk) hidden = true;
    if (filter === 'overdue' && !overdue) hidden = true;
    if (filter === 'done' && !done) hidden = true;
    if (filter === 'untracked' && !untracked) hidden = true;
    const progBar = kr.progress !== null ? `<div class="prog-bar"><div class="prog-fill" style="width:${kr.progress}%;background:${progColor(kr.progress)}"></div></div><span class="prog-text">${kr.progress}%</span>` : '<span class="badge badge-gray">未录入</span>';
    const statusBadge = kr.status ? `<span class="badge ${kr.status==='未开始'?'badge-red':'badge-blue'}">${kr.status}</span>` : '<span class="badge badge-gray">未标注</span>';
    const deadlineDisplay = kr.deadline ? new Date(kr.deadline).toLocaleDateString('zh-CN') + (overdue ? ` <span class="badge badge-red">逾期${daysOverdue(kr.deadline)}天</span>` : '') : '<span style="color:#ccc">未设定</span>';
    let folTags;
    if (kr.followers && kr.followers.length > 0) {
      const visible = kr.followers.slice(0, 4).map(n => `<span class="follower-tag">${n}</span>`).join('');
      const more = kr.followers.length > 4 ? `<span class="follower-more">+${kr.followers.length - 4}人</span>` : '';
      folTags = `<div class="follower-tags">${visible}${more}</div>`;
    } else {
      folTags = '<span class="badge badge-red">无跟进人</span>';
    }
    const blockerDisplay = kr.blocker ? `<div class="blocker-text">${kr.blocker}</div>` : '<span class="blocker-none">无</span>';
    return `<tr class="kr-row${hidden?' hidden':''}" onclick="toggleDetail(${idx})">
      <td class="o-cell">${kr.o}</td>
      <td class="kr-cell"><span class="kr-arrow">▶</span> ${kr.kr}<br><small>${kr.sites}</small></td>
      <td>${folTags}</td>
      <td class="prog-cell">${progBar}</td>
      <td>${statusBadge}</td>
      <td>${deadlineDisplay}</td>
      <td>${blockerDisplay}</td>
    </tr>
    <tr class="kr-detail-row" id="detail-${idx}" style="display:${hidden?'none':'none'}">
      <td colspan="7">
        <div class="kr-detail-content">
          <div class="kr-detail-block">
            <h4>关键结果描述（进展详情）</h4>
            <div class="desc-text ${kr.krDesc ? '' : 'empty'}">${kr.krDesc || '暂无进展描述'}</div>
          </div>
          <div class="kr-detail-block">
            <h4>网点目标</h4>
            <div class="desc-text ${kr.siteGoal ? '' : 'empty'}">${kr.siteGoal || '暂无'}</div>
            <h4 style="margin-top:12px">全部跟进人（${kr.followers ? kr.followers.length : 0}人）</h4>
            <div class="follower-tags">${kr.followers ? kr.followers.map(n => `<span class="follower-tag">${n}</span>`).join('') : '无'}</div>
          </div>
        </div>
      </td>
    </tr>`;
  }).join('');
  tbody.innerHTML = rows;
}
function toggleDetail(idx) {
  const rows = document.querySelectorAll('#krBody .kr-row');
  const row = rows[idx];
  const detail = document.getElementById('detail-' + idx);
  if (!detail || !row) return;
  const isHidden = getComputedStyle(detail).display === 'none';
  if (isHidden) {
    detail.style.display = 'table-row';
    row.classList.add('expanded');
  } else {
    detail.style.display = 'none';
    row.classList.remove('expanded');
  }
}
function updateFilterCounts() {
  const setTxt = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
  setTxt('c-all', `(${okrData.length})`);
  setTxt('c-risk', `(${okrData.filter(isRisk).length})`);
  setTxt('c-overdue', `(${okrData.filter(isPastDueIncomplete).length})`);
  setTxt('c-done', `(${okrData.filter(k=>k.progress===100).length})`);
  setTxt('c-untracked', `(${okrData.filter(k=>k.progress===null).length})`);
}
const filtersEl = document.getElementById('filters');
if (filtersEl) {
  filtersEl.addEventListener('click', e => {
    const btn = e.target.closest('.filter-btn');
    if (!btn) return;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderKRTable(btn.dataset.filter);
  });
}
let sortDir = {};
function sortTable(col) {
  sortDir[col] = !sortDir[col];
  const dir = sortDir[col] ? 1 : -1;
  okrData.sort((a, b) => {
    let av, bv;
    if (col === 0) { av = a.o; bv = b.o; }
    if (col === 1) { av = a.kr; bv = b.kr; }
    if (col === 2) { av = a.followers ? a.followers.length : 0; bv = b.followers ? b.followers.length : 0; }
    if (col === 3) { av = a.progress===null?-1:a.progress; bv = b.progress===null?-1:b.progress; }
    if (col === 4) { av = a.status||'zzz'; bv = b.status||'zzz'; }
    if (col === 5) { av = a.deadline?a.deadline:'9999'; bv = b.deadline?b.deadline:'9999'; }
    if (col === 6) { av = a.blocker||'zzz'; bv = b.blocker||'zzz'; }
    if (typeof av === 'string') return av.localeCompare(bv) * dir;
    return (av - bv) * dir;
  });
  const activeBtn = document.querySelector('.filter-btn.active');
  renderKRTable(activeBtn ? activeBtn.dataset.filter : 'all');
}
"""

import html as _html_mod

def _esc(text):
    """HTML转义"""
    return _html_mod.escape(str(text), quote=False)

def _prog_color(p):
    if p is None: return '#ccc'
    if p >= 100: return '#1b5e20'
    if p >= 70: return '#27ae60'
    if p >= 30: return '#f39c12'
    return '#e74c3c'

def _gen_o_chart(krs, a):
    """服务端渲染O图"""
    o_groups = {}
    for kr in krs:
        if kr['o'] not in o_groups:
            o_groups[kr['o']] = {'title': kr['oTitle'], 'krs': []}
        o_groups[kr['o']]['krs'].append(kr)
    
    bars = []
    for o_code in ['O1', 'O2', 'O3', 'O4']:
        g = o_groups.get(o_code)
        if not g: continue
        tracked = [k for k in g['krs'] if k['progress'] is not None]
        avg = round(sum(k['progress'] for k in tracked) / len(tracked)) if tracked else 0
        untracked = len(g['krs']) - len(tracked)
        done_count = len([k for k in g['krs'] if k['progress'] == 100])
        risk_count = len([k for k in g['krs'] if a['is_risk'](k)])
        overdue_count = len([k for k in g['krs'] if a['is_overdue'](k)])
        untracked_str = f'，{untracked}项未追踪' if untracked > 0 else ''
        color = _prog_color(avg)
        # hover明细
        kr_lines = []
        for k in g['krs']:
            p = f'{k["progress"]}%' if k['progress'] is not None else '未追踪'
            star = ' ⚠️过期' if a['is_overdue'](k) else (' ⚠️' if a['is_risk'](k) else '')
            kr_lines.append(f'{k["kr"]} - {p}{star}')
        tip = _html_mod.escape('\n'.join(kr_lines), quote=True)
        overdue_str = f' / 过期{overdue_count}' if overdue_count > 0 else ''
        bars.append(f'''<div class="o-bar-row tooltip" data-tip="{tip}">
  <div class="o-bar-label">{o_code} {_esc(g["title"])}<small>{len(g["krs"])}项KR{untracked_str}</small></div>
  <div class="o-bar-track"><div class="o-bar-fill" style="width:{avg}%;background:{color}"><span>{avg}%</span></div></div>
  <div class="o-bar-stats">已达成{done_count} / 风险{risk_count}{overdue_str}</div>
</div>''')
    return ''.join(bars)

def _gen_kr_table(krs, a):
    """服务端渲染KR表格行"""
    rows = []
    for idx, kr in enumerate(krs):
        overdue = a['is_overdue'](kr)
        risk = a['is_risk'](kr)
        done = kr['progress'] == 100
        untracked = kr['progress'] is None
        
        # 进度
        if kr['progress'] is not None:
            color = _prog_color(kr['progress'])
            prog_bar = f'<div class="prog-bar"><div class="prog-fill" style="width:{kr["progress"]}%;background:{color}"></div></div><span class="prog-text">{kr["progress"]}%</span>'
        else:
            prog_bar = '<span class="badge badge-gray">未录入</span>'
        
        # 状态
        if kr['status']:
            status_cls = 'badge-red' if kr['status'] == '未开始' else 'badge-blue'
            status_badge = f'<span class="badge {status_cls}">{_esc(kr["status"])}</span>'
        else:
            status_badge = '<span class="badge badge-gray">未标注</span>'
        
        # 截止日
        if kr['deadline']:
            dl_str = kr['deadline'][:10]
            overdue_badge = f' <span class="badge badge-red">逾期{a["days_overdue"](kr)}天</span>' if overdue else ''
            deadline_display = f'{dl_str}{overdue_badge}'
        else:
            deadline_display = '<span style="color:#ccc">未设定</span>'
        
        # 跟进人标签
        if kr['followers'] and len(kr['followers']) > 0:
            visible = ''.join([f'<span class="follower-tag">{_esc(n)}</span>' for n in kr['followers'][:4]])
            more = f'<span class="follower-more">+{len(kr["followers"]) - 4}人</span>' if len(kr['followers']) > 4 else ''
            fol_tags = f'<div class="follower-tags">{visible}{more}</div>'
        else:
            fol_tags = '<span class="badge badge-red">无跟进人</span>'
        
        # 卡点
        blocker_display = f'<div class="blocker-text">{_esc(kr["blocker"][:80])}</div>' if kr['blocker'] else '<span class="blocker-none">无</span>'
        
        # 关键结果描述
        kr_desc = kr.get('krDesc', '').strip()
        if not kr_desc or kr_desc == '无':
            kr_desc_display = '暂无进展描述'
            desc_cls = 'empty'
        else:
            kr_desc_display = _esc(kr_desc)
            desc_cls = ''
        
        site_goal = _esc(kr.get('siteGoal', '')) or '暂无'
        all_followers = ''.join([f'<span class="follower-tag">{_esc(n)}</span>' for n in kr['followers']]) if kr['followers'] else '无'
        
        rows.append(f'''<tr class="kr-row" onclick="toggleDetail({idx})">
  <td class="o-cell">{kr["o"]}</td>
  <td class="kr-cell"><span class="kr-arrow">▶</span> {_esc(kr["kr"])}<br><small>{_esc(kr["sites"])}</small></td>
  <td>{fol_tags}</td>
  <td class="prog-cell">{prog_bar}</td>
  <td>{status_badge}</td>
  <td>{deadline_display}</td>
  <td>{blocker_display}</td>
</tr>
<tr class="kr-detail-row" id="detail-{idx}" style="display:none">
  <td colspan="7">
    <div class="kr-detail-content">
      <div class="kr-detail-block">
        <h4>关键结果描述（进展详情）</h4>
        <div class="desc-text {desc_cls}">{kr_desc_display}</div>
      </div>
      <div class="kr-detail-block">
        <h4>网点目标</h4>
        <div class="desc-text">{site_goal}</div>
        <h4 style="margin-top:12px">全部跟进人（{len(kr["followers"])}人）</h4>
        <div class="follower-tags">{all_followers}</div>
      </div>
    </div>
  </td>
</tr>''')
    return ''.join(rows)

def _gen_kr_followers(krs, a):
    """服务端渲染各KR跟进人一览"""
    o_groups = {}
    for kr in krs:
        if kr['o'] not in o_groups:
            o_groups[kr['o']] = {'title': kr['oTitle'], 'krs': []}
        o_groups[kr['o']]['krs'].append(kr)
    
    html_parts = []
    for o_code in ['O1', 'O2', 'O3', 'O4']:
        g = o_groups.get(o_code)
        if not g: continue
        html_parts.append(f'<div class="kr-fol-group-header">{o_code} {_esc(g["title"])}（{len(g["krs"])}项KR）</div>')
        for kr in g['krs']:
            color = _prog_color(kr['progress'])
            prog_str = f'{kr["progress"]}%' if kr['progress'] is not None else '未追踪'
            if kr['followers'] and len(kr['followers']) > 0:
                fol_names = ''.join([f'<span class="follower-tag">{_esc(n)}</span>' for n in kr['followers']])
            else:
                fol_names = '<span class="badge badge-red">无跟进人</span>'
            overdue_tag = f' <span class="badge badge-red">逾期{a["days_overdue"](kr)}天</span>' if a['is_overdue'](kr) else ''
            risk_tag = f' <span class="badge badge-red">风险</span>' if a['is_risk'](kr) and not a['is_overdue'](kr) else ''
            html_parts.append(f'''<div class="kr-follower-item">
  <div class="kr-name"><span class="o-cell">{kr["o"]}</span> {_esc(kr["kr"])}{overdue_tag}{risk_tag}</div>
  <div class="kr-prog" style="color:{color}">{prog_str}</div>
  <div class="kr-fol-list">{fol_names}</div>
</div>''')
    return ''.join(html_parts)

def _gen_risk_cards_html(risk_cards):
    """服务端渲染风险卡片"""
    cards = []
    for r in risk_cards:
        cards.append(f'''<div class="risk-card {r["level"]}">
  <div class="risk-title">{_esc(r["title"])}</div>
  <div class="risk-detail">{r["detail"]}</div>
  <div class="risk-action"><strong>建议关注：</strong>{_esc(r["action"])}</div>
</div>''')
    return ''.join(cards)

def _gen_dq_html(dq_items):
    """服务端渲染数据质量列表"""
    items = []
    for d in dq_items:
        cls = 'dq-red' if d['red'] else ''
        items.append(f'<li class="{cls}">{_esc(d["title"])}<div class="dq-items">{d["items"]}</div></li>')
    return ''.join(items)

def _gen_progress_highlights(krs, a):
    """GM核心板块：本周进展亮点（突出关键结果描述）"""
    updated = [k for k in krs if a['has_update'](k)]
    # 按O分组，再按描述详细度排序
    by_o = {}
    for k in updated:
        by_o.setdefault(k['o'], {'title': k['oTitle'], 'items': []})
        by_o[k['o']]['items'].append(k)
    
    if not updated:
        return '<div class="empty-state">本周暂无KR更新进展描述</div>'
    
    parts = []
    for o_code in ['O1', 'O2', 'O3', 'O4']:
        g = by_o.get(o_code)
        if not g:
            continue
        # 按描述详细度排序（详细的在前）
        g['items'].sort(key=lambda k: -(len(k.get('krDesc') or '')))
        parts.append(f'<div class="ph-o-header"><span class="o-badge">{o_code}</span>{_esc(g["title"])}<span class="ph-count">{len(g["items"])}项有进展</span></div>')
        for kr in g['items']:
            desc = (kr.get('krDesc') or '').strip()
            prog = kr['progress']
            prog_str = f'{prog}%' if prog is not None else '未录入'
            prog_color = _prog_color(prog)
            followers = '、'.join(kr['followers'][:3]) if kr['followers'] else '—'
            more_fol = f' 等{len(kr["followers"])}人' if len(kr['followers']) > 3 else ''
            overdue_tag = f'<span class="badge badge-red">逾期{a["days_overdue"](kr)}天</span>' if a['is_overdue'](kr) else ''
            blocker_html = f'<div class="ph-blocker"><strong>卡点：</strong>{_esc(kr["blocker"][:120])}</div>' if kr.get('blocker') else ''
            parts.append(f'''<div class="ph-card">
  <div class="ph-head">
    <div class="ph-title">{_esc(kr["kr"])}</div>
    <div class="ph-prog" style="color:{prog_color}">{prog_str}</div>
  </div>
  <div class="ph-meta">{_esc(followers)}{more_fol} · {_esc(kr["sites"])} {overdue_tag}</div>
  <div class="ph-desc">{_esc(desc)}</div>
  {blocker_html}
</div>''')
    return ''.join(parts)

def _gen_stale_list(krs, a):
    """停滞预警：无实质进展描述或进度为0"""
    stale = [k for k in krs if a['is_stale'](k)]
    if not stale:
        return '<div class="empty-state">全部KR均有进展更新</div>'
    parts = []
    for kr in stale:
        prog = kr['progress']
        prog_str = f'{prog}%' if prog is not None else '未录入'
        prog_color = _prog_color(prog)
        followers = '、'.join(kr['followers'][:3]) if kr['followers'] else '—'
        reason = []
        if not a['has_update'](kr):
            reason.append('未填写进展描述')
        if kr['progress'] == 0:
            reason.append('进度0%')
        if kr['status'] == '未开始':
            reason.append('未开始')
        reason_str = ' · '.join(reason) if reason else '—'
        overdue_tag = f'<span class="badge badge-red">逾期{a["days_overdue"](kr)}天</span>' if a['is_overdue'](kr) else ''
        parts.append(f'''<div class="stale-item">
  <div class="stale-name"><span class="o-badge">{kr["o"]}</span>{_esc(kr["kr"])} {overdue_tag}</div>
  <div class="stale-info"><span style="color:{prog_color};font-weight:700">{prog_str}</span> · {_esc(followers)} · <span class="stale-reason">{reason_str}</span></div>
</div>''')
    return ''.join(parts)

def generate_html(a, today_str, week_str=''):
    """生成GM视角HTML报告（本周进展亮点置顶，服务端渲染+JS增强交互）"""
    krs = a['krs']

    # JS数据数组
    js_krs = json.dumps([{
        'o': kr['o'], 'oTitle': kr['oTitle'],
        'kr': kr['kr'], 'krFull': kr['krFull'],
        'krDesc': kr['krDesc'],
        'siteGoal': kr['siteGoal'],
        'followers': kr['followers'],
        'followerCount': kr['followerCount'],
        'progress': kr['progress'],
        'status': kr['status'],
        'deadline': kr['deadline'],
        'blocker': str(kr['blocker'] or '').replace('\n', ' ')[:120],
        'sites': kr['sites'],
        'recordId': kr['recordId'],
    } for kr in krs], ensure_ascii=False)

    # 风险卡片
    risk_cards = []
    if a['overdue_count'] > 0:
        overdue_krs = [k for k in krs if a['is_overdue'](k)]
        detail_items = ''.join([f'<span class="kr-item">{k["kr"]}({k["progress"] if k["progress"] is not None else "未追踪"}%)</span>' for k in overdue_krs[:5]])
        risk_cards.append({
            'level': 'high',
            'title': f'{a["overdue_count"]}项KR已过截止日期但未完成',
            'detail': detail_items,
            'action': '建议了解各项过期KR的推进计划和时间安排，评估目标是否需要调整'
        })
    for kr in krs:
        if kr['progress'] == 0 and kr['status'] == '未开始':
            risk_cards.append({
                'level': 'high', 'title': f'{kr["kr"]} 0%未开始',
                'detail': f'卡点：{kr["blocker"] or "未记录"}，{kr["followerCount"]}人跟进',
                'action': '建议关注此项的推进节奏，了解当前的资源安排和客户拓展计划'
            })
            break
    if a['untracked_count'] > 0:
        untracked_krs = [k for k in krs if k['progress'] is None]
        detail_items = ''.join([f'<span class="kr-item">{k["kr"]}</span>' for k in untracked_krs[:5]])
        risk_cards.append({
            'level': 'medium', 'title': f'{a["untracked_count"]}项KR未录入进度值',
            'detail': detail_items,
            'action': '建议了解进度数据缺失原因，评估数据追踪机制是否需要完善'
        })
    for kr in krs:
        if kr['progress'] is not None and 0 < kr['progress'] <= 30:
            risk_cards.append({
                'level': 'medium',
                'title': f'{kr["kr"]} {kr["progress"]}%{"（已过期）" if a["is_overdue"](kr) else ""}',
                'detail': f'卡点：{kr["blocker"] or "未记录"}',
                'action': '建议了解当前的资源配置和推进计划'
            })

    # 数据质量项
    dq_items = []
    if a['untracked_count'] > 0:
        untracked = [k['kr'] for k in krs if k['progress'] is None]
        items_html = ''.join([f'<span class="kr-item">{name}</span>' for name in untracked])
        dq_items.append({'red': True, 'title': f'{a["untracked_count"]}项KR未录入推进进度值', 'items': items_html})
    if a['nostatus_count'] > 0:
        nostatus = [k['kr'] for k in krs if not k['status']]
        items_html = ''.join([f'<span class="kr-item">{name}</span>' for name in nostatus[:6]])
        dq_items.append({'red': False, 'title': f'{a["nostatus_count"]}项KR未标注完成状态', 'items': items_html})
    if a['overdue_count'] > 0:
        overdue = [k['kr'] for k in krs if a['is_overdue'](k)]
        items_html = ''.join([f'<span class="kr-item">{name}</span>' for name in overdue])
        dq_items.append({'red': True, 'title': f'{a["overdue_count"]}项KR已过截止日期但未完成', 'items': items_html})

    # 指标卡tooltip
    import html as _html
    tip_done = _html.escape('\n'.join([k['kr'] for k in krs if k['progress'] == 100]) or '暂无')
    tip_risk = _html.escape('\n'.join([k['kr'] for k in krs if a['is_risk'](k)]) or '无')
    tip_overdue = _html.escape('\n'.join([k['kr'] for k in krs if a['is_overdue'](k)]) or '无')
    tip_updated = _html.escape('\n'.join([k['kr'] for k in krs if a['has_update'](k)]) or '无')
    tip_stale = _html.escape('\n'.join([k['kr'] for k in krs if a['is_stale'](k)]) or '无')
    tip_untracked = _html.escape('\n'.join([k['kr'] for k in krs if k['progress'] is None]) or '无')

    # 服务端渲染各板块
    progress_highlights_html = _gen_progress_highlights(krs, a)
    stale_list_html = _gen_stale_list(krs, a)
    o_chart_html = _gen_o_chart(krs, a)
    kr_table_html = _gen_kr_table(krs, a)
    kr_follower_html = _gen_kr_followers(krs, a)
    risk_cards_html = _gen_risk_cards_html(risk_cards[:6])
    dq_html = _gen_dq_html(dq_items)

    c_all = len(krs)
    c_risk = len([k for k in krs if a['is_risk'](k)])
    c_overdue = len([k for k in krs if a['is_overdue'](k)])
    c_done = len([k for k in krs if k['progress'] == 100])
    c_untracked = len([k for k in krs if k['progress'] is None])

    week_badge = f'<span class="week-badge">{week_str}</span>' if week_str else ''
    title_week = f' {week_str}' if week_str else ''

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OKR推进进展周报{title_week} - {today_str}</title>
<style>
{CSS_TEXT}
</style>
</head>
<body>
<div class="container">

<div class="header">
  <h1>OKR推进进展周报{week_badge}</h1>
  <div class="meta">
    <span>日期：{today_str}</span>
    <span>面向：总经理 / 部门经理</span>
    <span>数据来源：协同待办事项表 · 网点OKR推进</span>
  </div>
</div>

<nav class="topnav">
  <a href="#sec-summary" class="nav-highlight">本周概况</a>
  <a href="#sec-highlights" class="nav-highlight">进展亮点</a>
  <a href="#sec-stale" class="nav-highlight">停滞预警</a>
  <a href="#sec-metrics">指标</a>
  <a href="#sec-ochart">各O进度</a>
  <a href="#sec-risk">风险关注</a>
  <a href="#sec-detail">KR明细</a>
  <a href="#sec-followers">跟进人</a>
  <a href="#sec-dq">数据质量</a>
</nav>

<div class="exec-summary" id="sec-summary">
  <h2>本周概况</h2>
  <ul>
    <li>整体平均进度 <strong>{a["overall_avg"]}%</strong>，本周 <strong>{a["updated_count"]}项KR有实质进展更新</strong>（其中{a["detailed_count"]}项描述详细）</li>
    <li class="{'warn' if a["overdue_count"] > 0 else 'info'}">{'<strong>' + str(a["overdue_count"]) + '项KR已过截止日期但未完成</strong>' if a["overdue_count"] > 0 else '无过期未完成KR'}</li>
    <li class="{'warn' if a["stale_count"] > 0 else ''}">{a["stale_count"]}项KR本周无进展更新或进度为0</li>
  </ul>
</div>

<div class="section" id="sec-highlights">
  <h2>本周进展亮点 <small style="font-weight:400;color:#8898aa;font-size:12px;margin-left:8px">跟进人更新的最新成果（按目标分组，详细度优先）</small></h2>
  <div id="progressHighlights">{progress_highlights_html}</div>
</div>

<div class="section" id="sec-stale">
  <h2>停滞预警 <small style="font-weight:400;color:#8898aa;font-size:12px;margin-left:8px">本周无进展描述或进度为0的KR</small></h2>
  <div id="staleList">{stale_list_html}</div>
</div>

<div class="metrics" id="sec-metrics">
  <div class="metric-card dark tooltip" data-tip="整体平均进度（各O均值）">
    <div class="num">{a["overall_avg"]}%</div><div class="label">平均进度</div>
  </div>
  <div class="metric-card green tooltip" data-tip="{tip_updated}">
    <div class="num">{a["updated_count"]}</div><div class="label">本周有进展</div>
  </div>
  <div class="metric-card green tooltip" data-tip="{tip_done}">
    <div class="num">{a["done_count"]}</div><div class="label">已达成</div>
  </div>
  <div class="metric-card red tooltip" data-tip="{tip_overdue}">
    <div class="num">{a["overdue_count"]}</div><div class="label">已过期</div>
  </div>
  <div class="metric-card red tooltip" data-tip="{tip_stale}">
    <div class="num">{a["stale_count"]}</div><div class="label">停滞项</div>
  </div>
  <div class="metric-card gray tooltip" data-tip="{tip_untracked}">
    <div class="num">{a["untracked_count"]}</div><div class="label">未追踪</div>
  </div>
</div>

<div class="section" id="sec-ochart">
  <h2>各目标平均进度</h2>
  <div class="o-chart" id="oChart">{o_chart_html}</div>
</div>

<div class="section" id="sec-risk">
  <h2>风险与关注事项</h2>
  <div class="risk-grid" id="riskGrid">{risk_cards_html}</div>
</div>

<div class="section" id="sec-detail">
  <h2>关键结果明细 <small style="font-weight:400;color:#8898aa;font-size:12px;margin-left:8px">备查 · 点击行展开/收起关键结果描述</small></h2>
  <div class="filters" id="filters">
    <button class="filter-btn active" data-filter="all">全部 <span class="count" id="c-all">({c_all})</span></button>
    <button class="filter-btn" data-filter="risk">风险项 <span class="count" id="c-risk">({c_risk})</span></button>
    <button class="filter-btn" data-filter="overdue">已过期 <span class="count" id="c-overdue">({c_overdue})</span></button>
    <button class="filter-btn" data-filter="done">已达成 <span class="count" id="c-done">({c_done})</span></button>
    <button class="filter-btn" data-filter="untracked">未追踪 <span class="count" id="c-untracked">({c_untracked})</span></button>
  </div>
  <div class="kr-table-wrap">
    <table class="kr-table" id="krTable">
      <thead>
        <tr>
          <th onclick="sortTable(0)">O</th>
          <th onclick="sortTable(1)">关键结果</th>
          <th onclick="sortTable(2)">跟进人</th>
          <th onclick="sortTable(3)">进度</th>
          <th onclick="sortTable(4)">状态</th>
          <th onclick="sortTable(5)">截止日</th>
          <th onclick="sortTable(6)">卡点/风险</th>
        </tr>
      </thead>
      <tbody id="krBody">{kr_table_html}</tbody>
    </table>
  </div>
</div>

<div class="section" id="sec-followers">
  <h2>各KR跟进人一览</h2>
  <div class="kr-follower-list" id="krFollowerList">{kr_follower_html}</div>
</div>

<div class="section" id="sec-dq">
  <h2>数据质量提醒</h2>
  <ul class="dq-list" id="dqList">{dq_html}</ul>
</div>

<div class="footer">
  <p>数据来源：<a href="https://docs.dingtalk.com/i/nodes/EpGBa2Lm8azv7rn5uEONbq3rWgN7R35y?iframeQuery=sheetId%3D77lhl1x" target="_blank">协同待办事项表 · 网点OKR推进</a></p>
  <p>生成时间：{today_str}{title_week} · 云端自动生成</p>
</div>

</div>

<script>
const today = new Date('{today_str}');
const okrData = {js_krs};
{JS_FUNCS}
updateFilterCounts();
</script>
</body>
</html>'''

    return html

# ===== 主流程 =====

def main():
    skip_send = '--skip-send' in sys.argv
    allow_no_url = '--allow-no-url' in sys.argv
    today = date.today()
    today_str = today.isoformat()
    week_str = get_iso_week(today)
    print(f'[{datetime.now().isoformat()}] OKR周报云端脚本启动{"（仅生成，不发送）" if skip_send else ""}')
    print(f'   本周：{week_str}')

    # URL检查：无URL且未显式允许，则拒绝发送（避免无链接推送）
    if not skip_send and not REPORT_URL and not allow_no_url:
        print(f'   ❌ 错误：REPORT_URL 未设置，拒绝发送（避免推送无链接消息）')
        print(f'   正确流程：')
        print(f'     1) python okr_cloud_report.py --skip-send   # 只生成HTML')
        print(f'     2) 部署HTML到网络托管（CloudStudio/GitHub Pages）获取URL')
        print(f'     3) REPORT_URL=<URL> python okr_cloud_report.py   # 生成+发送（含链接）')
        print(f'   如确实需要无链接推送（不推荐），加 --allow-no-url')
        sys.exit(2)

    # 1. 获取token
    print('1. 获取access token...')
    token = get_access_token()
    print(f'   token获取成功')

    # 2. 获取unionId
    print('2. 获取unionId...')
    union_id = get_union_id(token, USER_ID)
    if not union_id:
        if not skip_send:
            msg = f'## OKR周报云端脚本错误\n\n获取unionId失败，请确保应用已开通 **qyapi_get_member** 权限。\n\n申请链接：https://open-dev.dingtalk.com/appscope/apply?content={APP_KEY}%23qyapi_get_member'
            send_robot_message(token, 'OKR周报云端脚本错误', msg)
        print(f'   错误：获取unionId失败')
        return
    print(f'   unionId获取成功')

    # 3. 拉取AI表格数据
    print('3. 拉取AI表格数据...')
    records = list_records(token, union_id)
    if records is None:
        if not skip_send:
            msg = f'## OKR周报云端脚本错误\n\n拉取AI表格数据失败，请确保应用已开通 **Notable.Base.Read.All** 权限。\n\n申请链接：https://open-dev.dingtalk.com/appscope/apply?content={APP_KEY}%23Notable.Base.Read.All'
            send_robot_message(token, 'OKR周报云端脚本错误', msg)
        print(f'   错误：拉取AI表格数据失败')
        return
    print(f'   获取到{len(records)}条记录')

    # 4. 解析和分析
    print('4. 解析和分析数据...')
    krs = parse_records(records)
    a = analyze(krs, today)
    print(f'   有效KR: {len(krs)}, 平均进度: {a["overall_avg"]}%, 本周有进展: {a["updated_count"]}, 停滞: {a["stale_count"]}, 过期: {a["overdue_count"]}')

    # 5. 生成HTML报告
    print('5. 生成HTML报告...')
    html = generate_html(a, today_str, week_str)
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'okr-report.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'   HTML已保存: {html_path} ({len(html)} chars)')

    if skip_send:
        print(f'[{datetime.now().isoformat()}] 完成（--skip-send模式，未发送消息）')
        print(f'   提示：部署 {html_path} 到网络托管后，设置REPORT_URL重跑即可发送')
        return

    # 6. 生成Markdown摘要（含报告链接）
    print('6. 生成Markdown摘要...')
    md = generate_markdown(a, today_str, report_url=REPORT_URL, week_str=week_str)
    print(f'   报告URL: {REPORT_URL}')

    # 7. 发送机器人消息
    print('7. 发送钉钉机器人消息...')
    msg_title = f'OKR推进进展周报 {week_str}（{today_str}）'
    result = send_robot_message(token, msg_title, md)
    if result.get('processQueryKey'):
        print(f'   发送成功！标题：{msg_title}')
    else:
        print(f'   发送失败: {json.dumps(result, ensure_ascii=False)}')

    print(f'[{datetime.now().isoformat()}] 完成')

if __name__ == '__main__':
    main()
