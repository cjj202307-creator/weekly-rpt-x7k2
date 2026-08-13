#!/usr/bin/env python3
"""
全国网点OKR推进周报 - 云端版（不依赖本地电脑，可在GitHub Actions/云函数上运行）
直接调用钉钉Open API，不需要dws CLI。

环境变量配置：
  DINGTALK_APP_KEY    - 钉钉应用AppKey
  DINGTALK_APP_SECRET - 钉钉应用AppSecret
  DINGTALK_USER_ID    - 接收人userId（默认：17397552280041830）
  REPORT_URL          - 报告URL（必填，无URL拒绝发送避免重复/无链接推送）
  DINGTALK_GROUP_WEBHOOK - 钉钉群机器人webhook（可选；配置后推送到群，与个人推送并存）
  DINGTALK_GROUP_WEBHOOK_SITE_DIGITAL - 第二个群webhook（网点数字化，可选）
  DINGTALK_GROUP_WEBHOOK_MANAGER - 第三个群webhook（全国网点部门经理群，可选）
  REPORT_PASSWORD      - 报告访问密码（可选；设置后HTML内容加密，打开需输入密码）

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
import glob
import time
import difflib
import urllib.request
import urllib.error
from datetime import datetime, date, timedelta, timezone

# ===== 配置 =====
APP_KEY = os.environ.get('DINGTALK_APP_KEY', '')
APP_SECRET = os.environ.get('DINGTALK_APP_SECRET', '')
USER_ID = os.environ.get('DINGTALK_USER_ID', '17397552280041830')
REPORT_URL = os.environ.get('REPORT_URL', '')  # 报告URL（GitHub Pages或CloudStudio）
GROUP_WEBHOOK = os.environ.get('DINGTALK_GROUP_WEBHOOK', '')  # 钉钉群机器人webhook（推群，可选）
GROUP_WEBHOOK_SITE_DIGITAL = os.environ.get('DINGTALK_GROUP_WEBHOOK_SITE_DIGITAL', '')  # 第二个群：网点数字化
GROUP_WEBHOOK_MANAGER = os.environ.get('DINGTALK_GROUP_WEBHOOK_MANAGER', '')  # 第三个群：全国网点部门经理群
LLM_API_KEY = os.environ.get('DASHSCOPE_API_KEY', '')  # DeepSeek API Key（AI洞察用，可选；不配置则跳过。环境变量名沿用DASHSCOPE_API_KEY，workflow无需改）
LLM_MODEL = os.environ.get('LLM_MODEL') or 'deepseek-chat'    # 模型名，默认 deepseek-chat（空字符串也回退到默认值）
REPORT_PASSWORD = os.environ.get('REPORT_PASSWORD', '')  # 报告访问密码（可选；设置后HTML内容加密，打开需输入密码）
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

def api_request(url, method='GET', headers=None, body=None, timeout=30, retries=2):
    """通用HTTP请求（含重试和超时容错）"""
    data = json.dumps(body).encode() if body else None
    hdrs = {'Content-Type': 'application/json'}
    if headers:
        hdrs.update(headers)
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            try:
                err_json = json.loads(err_body)
                return err_json
            except:
                return {'error': err_body}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if attempt < retries:
                wait = 2 * (attempt + 1)
                print(f'   请求失败（第{attempt+1}次），{wait}秒后重试: {e}', file=sys.stderr)
                time.sleep(wait)
            else:
                print(f'   请求失败（已重试{retries}次）: {e}', file=sys.stderr)
                return {'error': str(e)}
    return {'error': 'unknown'}

def get_access_token():
    """获取企业内部应用accessToken。失败返回None（不sys.exit，让调用方决定是否致命）。"""
    url = 'https://api.dingtalk.com/v1.0/oauth2/accessToken'
    result = api_request(url, 'POST', body={'appKey': APP_KEY, 'appSecret': APP_SECRET})
    token = result.get('accessToken', '')
    if not token:
        print(f'获取token失败: {json.dumps(result, ensure_ascii=False)}', file=sys.stderr)
        return None
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


def send_group_message(webhook, title, markdown_text):
    """通过钉钉群自定义机器人webhook发送Markdown消息到群（无安全验证方式）

    与个人单聊推送并存：配置了任一 DINGTALK_GROUP_WEBHOOK* 群webhook 时，
    对应群会额外收到推送（main 中遍历所有已配置的群）。
    """
    if not webhook:
        print('   未配置该群 webhook，跳过群推送')
        return None
    body = {
        'msgtype': 'markdown',
        'markdown': {'title': title, 'text': markdown_text}
    }
    try:
        result = api_request(webhook, 'POST', headers={'Content-Type': 'application/json'}, body=body)
    except Exception as e:
        print(f'   群推送请求异常: {e}')
        return None
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

def _o_sort_key(kr):
    """按O编号数字排序的key（兼容 'O1'/'O2'/'O10' 等格式）"""
    o = str(kr.get('o', '')).strip()
    m = __import__('re').search(r'\d+', o)
    return (int(m.group()) if m else 0, o)

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

# ===== 周环比快照机制 =====

SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'snapshots')

def _snapshot_path(today_str=None):
    """生成本次快照文件路径"""
    today_str = today_str or date.today().isoformat()
    return os.path.join(SNAPSHOT_DIR, f'okr-snapshot-{today_str}.json')

def _ensure_snapshot_dir():
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)

def save_snapshot(a, today_str, week_str):
    """保存当前数据快照，用于下周环比"""
    _ensure_snapshot_dir()
    snapshot = {
        'week': week_str,
        'date': today_str,
        'generated_at': datetime.now().isoformat(),
        'summary': {
            'overall_avg': a['overall_avg'],
            'updated_count': a['updated_count'],
            'detailed_count': a['detailed_count'],
            'done_count': a['done_count'],
            'stale_count': a['stale_count'],
            'overdue_count': a['overdue_count'],
            'risk_count': a['risk_count'],
            'untracked_count': a['untracked_count'],
        },
        'o_avgs': a['o_avgs'],
        'krs': [{
            'recordId': k['recordId'],
            'o': k['o'],
            'kr': k['kr'],
            'progress': k['progress'],
            'status': k['status'],
            'krDesc': k['krDesc'],
            'deadline': k['deadline'],
            'blocker': k['blocker'],
            'followers': k['followers'],
            'sites': k['sites'],
        } for k in a['krs']]
    }
    path = _snapshot_path(today_str)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return path

def load_previous_snapshot(today_str=None):
    """加载上一周（不同ISO周）的历史快照。同周多次运行不对比。"""
    _ensure_snapshot_dir()
    today = date.fromisoformat(today_str) if today_str else date.today()
    today_iso = today.isocalendar()  # (year, week, weekday)
    files = []
    if os.path.isdir(SNAPSHOT_DIR):
        for name in os.listdir(SNAPSHOT_DIR):
            if name.startswith('okr-snapshot-') and name.endswith('.json'):
                try:
                    d = date.fromisoformat(name.replace('okr-snapshot-', '').replace('.json', ''))
                    # 排除当天，且只取不同ISO周的快照
                    if d < today and d.isocalendar()[1] != today_iso[1]:
                        files.append((d, name))
                except: pass
    if not files:
        return None
    files.sort(reverse=True)
    latest_path = os.path.join(SNAPSHOT_DIR, files[0][1])
    try:
        with open(latest_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'读取历史快照失败: {e}', file=sys.stderr)
        return None

def compare_with_previous(a, prev_snapshot):
    """计算本周与上周的环比变化"""
    if not prev_snapshot:
        return None
    prev_krs = {k['recordId']: k for k in prev_snapshot.get('krs', [])}
    curr_krs = {k['recordId']: k for k in a['krs']}

    # 按recordId匹配后的KR
    common_ids = set(curr_krs.keys()) & set(prev_krs.keys())

    # 描述文本标准化（进度提升、描述变化都会用到）
    def _norm_desc(d):
        return (d or '').strip()

    # 进度提升的KR
    progress_gained = []
    for rid in common_ids:
        cur = curr_krs[rid]
        pre = prev_krs[rid]
        cur_p = cur.get('progress') if cur.get('progress') is not None else None
        pre_p = pre.get('progress') if pre.get('progress') is not None else None
        if cur_p is not None and pre_p is not None and cur_p > pre_p:
            progress_gained.append({
                'recordId': rid, 'o': cur['o'], 'kr': cur['kr'],
                'before': pre_p, 'after': cur_p, 'delta': cur_p - pre_p,
                'before_desc': _norm_desc(pre.get('krDesc')),
                'after_desc': _norm_desc(cur.get('krDesc'))
            })
    progress_gained.sort(key=lambda x: -x['delta'])

    # 描述有变化的KR（新增或修改）——捕获所有文本变化，含before/after原文
    desc_updated = []
    for rid in common_ids:
        cur = curr_krs[rid]
        pre = prev_krs[rid]
        cur_d = _norm_desc(cur.get('krDesc'))
        pre_d = _norm_desc(pre.get('krDesc'))
        if cur_d and cur_d != pre_d:
            desc_updated.append({
                'recordId': rid, 'o': cur['o'], 'kr': cur['kr'],
                'progress': cur.get('progress'),
                'is_new': not pre_d or len(pre_d) < 2,
                'before_desc': pre_d,
                'after_desc': cur_d
            })
    desc_updated.sort(key=lambda x: (x['o'], x['kr']))

    # 新增的KR
    new_krs = [curr_krs[rid] for rid in (set(curr_krs.keys()) - set(prev_krs.keys()))]
    new_krs.sort(key=lambda x: (x['o'], x['kr']))

    # 消失的KR
    removed_krs = [prev_krs[rid] for rid in (set(prev_krs.keys()) - set(curr_krs.keys()))]
    removed_krs.sort(key=lambda x: (x['o'], x['kr']))

    # 真正"本周有进展" = 进度提升 或 新增描述
    truly_updated = []
    seen = set()
    for k in progress_gained:
        truly_updated.append({'type': 'progress', **k})
        seen.add(k['recordId'])
    for k in desc_updated:
        if k['recordId'] not in seen:
            truly_updated.append({'type': 'desc', **k})
            seen.add(k['recordId'])
    for k in new_krs:
        if k['recordId'] not in seen:
            truly_updated.append({'type': 'new', 'recordId': k['recordId'], 'o': k['o'], 'kr': k['kr'], 'progress': k.get('progress')})
            seen.add(k['recordId'])
    truly_updated.sort(key=lambda x: (x['o'], x['kr']))

    # 指标变化
    prev_summary = prev_snapshot.get('summary', {})
    return {
        'prev_week': prev_snapshot.get('week', ''),
        'prev_date': prev_snapshot.get('date', ''),
        'overall_avg_delta': a['overall_avg'] - prev_summary.get('overall_avg', 0),
        'done_count_delta': a['done_count'] - prev_summary.get('done_count', 0),
        'stale_count_delta': a['stale_count'] - prev_summary.get('stale_count', 0),
        'overdue_count_delta': a['overdue_count'] - prev_summary.get('overdue_count', 0),
        'updated_count_delta': len(truly_updated) - prev_summary.get('updated_count', 0),
        'progress_gained': progress_gained,
        'desc_updated': desc_updated,
        'new_krs': new_krs,
        'removed_krs': removed_krs,
        'truly_updated': truly_updated,
        'prev_o_avgs': prev_snapshot.get('o_avgs', {}),
    }

def get_iso_week(d=None):
    """返回ISO周数，如 W30"""
    d = d or date.today()
    iso = d.isocalendar()
    return f'W{iso[1]:02d}'

def build_notify_md(today_str, report_url, week_str='', password=''):
    """极简钉钉通知：仅标题 + 一句引导 + 链接，完整内容见网页报告。"""
    title_suffix = f' {week_str}' if week_str else ''
    lines = []
    lines.append(f'# 全国网点OKR推进周报{title_suffix}')
    lines.append('')
    lines.append('本周报告已生成，完整内容（智能洞察 指标 · 进度 · 重点关注 · 历史周报）请点击下方查看 👇')
    lines.append('')
    if report_url:
        lines.append(f'[📊 查看完整报告]({report_url})')
    else:
        lines.append('> 报告链接暂未配置（REPORT_URL 为空）')
    if password:
        lines.append('')
        lines.append(f'🔒 访问密码：{password}')
    return '\n'.join(lines)


def generate_markdown(a, today_str, report_url=None, week_str='', comparison=None):
    """生成Markdown摘要（GM结构化精简版：不照搬描述原文，只列要点，细节看网页）"""
    krs = a['krs']
    lines = []
    title_suffix = f' {week_str}' if week_str else ''
    lines.append(f'## 全国网点OKR推进周报{title_suffix}')
    lines.append(f'{today_str}')
    lines.append('')

    # 核心数字（一行）
    updated_count_md = len(comparison['truly_updated']) if comparison else a['updated_count']
    lines.append(f'**整体进度 {a["overall_avg"]}%** ｜ **{updated_count_md}**项本周新进展 ｜ **{a["stale_count"]}**项停滞 ｜ **{a["overdue_count"]}**项超目标日期')

    # 周环比摘要
    if comparison:
        prev_week = comparison['prev_week']
        delta_avg = comparison['overall_avg_delta']
        delta_done = comparison['done_count_delta']
        delta_stale = comparison['stale_count_delta']
        delta_overdue = comparison['overdue_count_delta']
        lines.append('')
        lines.append(f'**较上周 {prev_week}：** 平均进度{delta_avg:+d}% ｜ 已达成{delta_done:+d} ｜ 停滞{delta_stale:+d} ｜ 超目标日期{delta_overdue:+d}')
        if comparison['progress_gained']:
            top = sorted(comparison['progress_gained'], key=lambda x: -x['delta'])[:2]
            names = '、'.join([f'{k["kr"]} {k["before"]}%→{k["after"]}%' for k in top])
            lines.append(f'- 进度提升TOP：{names}')
    else:
        lines.append('')
        lines.append('*（本周为首周基准，下周起显示环比变化）*')
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
            overdue_mark = ' `[超目标日期]`' if a['is_overdue'](kr) else ''
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
        risks.append(f'{a["overdue_count"]}项超过目标日期未完成：{names}{more}')
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
.container { max-width: 1200px; margin: 0 auto; padding: 24px; }
.header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); border-radius: 18px; padding: 34px 38px; margin-bottom: 22px; box-shadow: 0 10px 40px rgba(26,26,46,0.2); color: #fff; }
.header-logo { margin-bottom: 18px; display: inline-block; padding: 10px 18px; background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.20); border-radius: 10px; box-shadow: 0 4px 16px rgba(0,0,0,0.18); backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px); }
    .header-logo img { max-height: 54px; max-width: 100%; height: auto; width: auto; display: block; }
.header-brand { display: flex; align-items: center; gap: 18px; margin-bottom: 16px; }
.header-icon { width: 52px; height: 52px; border-radius: 14px; background: linear-gradient(135deg, #3498db 0%, #1a5f9e 100%); display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 800; color: #fff; letter-spacing: 1px; box-shadow: 0 4px 12px rgba(52,152,219,0.35); flex-shrink: 0; }
.header-titles { flex: 1; min-width: 0; }
.header h1 { font-size: 26px; font-weight: 800; color: #fff; margin-bottom: 6px; letter-spacing: -0.3px; }
.header-subtitle { font-size: 14px; color: rgba(255,255,255,0.72); font-weight: 400; }
.header .meta { font-size: 13px; color: rgba(255,255,255,0.65); display: flex; align-items: center; flex-wrap: wrap; gap: 8px 18px; }
.header .meta span { margin-right: 0; }
.header .meta-tag { display: inline-block; background: rgba(255,255,255,0.12); color: rgba(255,255,255,0.9); font-size: 11px; padding: 3px 10px; border-radius: 12px; font-weight: 600; border: 1px solid rgba(255,255,255,0.15); }
.header .badge-base { display: inline-block; background: rgba(52,152,219,0.2); color: #7dd3fc; font-size: 11px; padding: 2px 10px; border-radius: 10px; font-weight: 600; }
.exec-summary { background: #fff; border-radius: 14px; padding: 26px 34px; margin-bottom: 18px; box-shadow: 0 4px 16px rgba(0,0,0,0.06); border-left: 4px solid #e57373; }
.exec-summary h2 { font-size: 17px; font-weight: 700; color: #1a1a2e; margin-bottom: 14px; }
.exec-summary ul { list-style: none; }
.exec-summary li { font-size: 14px; color: #2c3e50; padding: 6px 0; padding-left: 20px; position: relative; }
.exec-summary li::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: #e57373; position: absolute; left: 0; top: 12px; }
.exec-summary li.warn::before { background: #f5b041; }
.exec-summary li.info::before { background: #3498db; }
.exec-summary strong { color: #1a1a2e; }
.metrics { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 16px; }
.metric-card { background: #fff; border-radius: 14px; padding: 20px 16px; text-align: center; box-shadow: 0 4px 16px rgba(0,0,0,0.06); border: 1px solid rgba(0,0,0,0.03); }
.metric-card .num { font-size: 30px; font-weight: 800; margin-bottom: 6px; }
.metric-card .label { font-size: 11px; color: #8898aa; text-transform: uppercase; letter-spacing: 0.6px; font-weight: 600; }
.metric-card.green .num { color: #1a5f9e; }
.metric-card.red .num { color: #e57373; }
.metric-card.gray .num { color: #aab7b8; }
.metric-card.dark .num { color: #1a1a2e; }
.section { background: #fff; border-radius: 14px; padding: 26px 34px; margin-bottom: 18px; box-shadow: 0 4px 16px rgba(0,0,0,0.06); border: 1px solid rgba(0,0,0,0.03); }
.ai-insight { border-left: 4px solid #1a5f9e; background: linear-gradient(135deg, #f5f9ff 0%, #eef4fb 100%); }
.ai-summary { font-size: 15px; line-height: 1.8; color: #1a1a2e; margin: 10px 0 16px; padding: 14px 18px; background: #fff; border-radius: 10px; box-shadow: 0 2px 8px rgba(26,95,158,0.08); }
.ai-block { margin-bottom: 12px; }
.ai-block-title { font-size: 14px; font-weight: 700; color: #1a5f9e; margin-bottom: 6px; }
.ai-block ul { margin: 0; padding-left: 20px; }
.ai-block li { font-size: 13px; line-height: 1.7; color: #334; margin-bottom: 4px; }
.ai-note { font-size: 11px; color: #8898aa; margin-top: 10px; padding-top: 8px; border-top: 1px dashed rgba(0,0,0,0.08); }
.section h2 { font-size: 17px; font-weight: 700; color: #1a1a2e; margin-bottom: 18px; padding-bottom: 10px; border-bottom: 2px solid #f0f0f0; }
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
.filter-btn.toggle-all-btn { border-color: #3498db; color: #3498db; margin-left: auto; font-weight: 600; }
.filter-btn.toggle-all-btn:hover { background: #3498db; color: #fff; }
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
.badge-red { background: #fdecea; color: #d32f2f; }
.badge-orange { background: #fff3e0; color: #e65100; }
.badge-gray { background: #f0f2f5; color: #7f8c8d; }
.badge-blue { background: #e3f2fd; color: #1a5f9e; }
.badge-teal { background: #e3f2fd; color: #1a5f9e; }
.resp-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.resp-table th { background: #f8f9fa; color: #8898aa; font-weight: 600; text-align: left; padding: 10px 12px; border-bottom: 2px solid #e8e8e8; }
.resp-table td { padding: 10px 12px; border-bottom: 1px solid #f0f0f0; }
.resp-table .bar-mini { height: 6px; border-radius: 3px; display: inline-block; width: 60px; vertical-align: middle; margin-right: 6px; }
.risk-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.risk-card { border-radius: 10px; padding: 16px 20px; border-left: 4px solid; }
.risk-card.high { background: #fdecea; border-color: #e57373; }
.risk-card.medium { background: #fff8e1; border-color: #f5b041; }
.risk-card .risk-title { font-size: 14px; font-weight: 700; margin-bottom: 6px; }
.risk-card.high .risk-title { color: #d32f2f; }
.risk-card.medium .risk-title { color: #f5b041; }
.risk-card .risk-detail { font-size: 13px; color: #555; margin-bottom: 8px; line-height: 1.8; }
.risk-card .risk-detail .kr-item { display: block; padding: 2px 0; }
.risk-card .risk-action { font-size: 12px; color: #777; padding-top: 8px; border-top: 1px dashed #ddd; }
.risk-card .risk-action strong { color: #333; }
.dq-list { list-style: none; }
.dq-list li { font-size: 13px; padding: 8px 0; padding-left: 24px; position: relative; border-bottom: 1px solid #f8f8f8; }
.dq-list li::before { content: "!"; position: absolute; left: 0; top: 8px; width: 18px; height: 18px; background: #f5b041; color: #fff; border-radius: 50%; text-align: center; line-height: 18px; font-size: 11px; font-weight: 700; }
.dq-list li.dq-red::before { background: #e57373; }
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
.follower-tag { display: inline-block; font-size: 10px; padding: 2px 7px; border-radius: 8px; background: #e3f2fd; color: #1a5f9e; white-space: nowrap; cursor: pointer; transition: all 0.2s; }
.follower-tag:hover { background: #3498db; color: #fff; }
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
.ph-blocker { font-size: 12px; color: #d32f2f; margin-top: 8px; padding: 8px 12px; background: #fdecea; border-radius: 4px; }
/* 停滞预警 */
.stale-item { padding: 10px 14px; border-bottom: 1px solid #f0f0f0; }
.stale-item:last-child { border-bottom: none; }
.stale-name { font-size: 13px; font-weight: 600; color: #2c3e50; margin-bottom: 4px; }
.stale-info { font-size: 12px; color: #8898aa; }
.stale-reason { color: #d32f2f; }
.empty-state { text-align: center; padding: 30px; color: #95a5a6; font-size: 13px; }
/* 周数徽章 */
.week-badge { display: inline-block; background: linear-gradient(135deg, #3498db 0%, #1a5f9e 100%); color: #fff; font-size: 13px; padding: 4px 14px; border-radius: 8px; font-weight: 800; margin-left: 12px; vertical-align: middle; box-shadow: 0 2px 8px rgba(0,0,0,0.25); letter-spacing: 0.5px; }
/* 吸顶导航栏 */
html { scroll-behavior: smooth; }
.section, .exec-summary, .metrics { scroll-margin-top: 76px; }
.topnav { position: sticky; top: 0; z-index: 100; background: rgba(255,255,255,0.96); backdrop-filter: blur(10px); border-radius: 12px; padding: 10px 16px; margin-bottom: 18px; box-shadow: 0 4px 16px rgba(0,0,0,0.08); border: 1px solid rgba(0,0,0,0.04); display: flex; gap: 4px; overflow-x: auto; scrollbar-width: none; }
.topnav::-webkit-scrollbar { display: none; }
.topnav a { flex-shrink: 0; padding: 7px 16px; font-size: 13px; color: #4a5568; text-decoration: none; border-radius: 20px; white-space: nowrap; transition: all 0.2s; font-weight: 500; }
.topnav a:hover { background: #e3f2fd; color: #1a5f9e; }
.topnav a.nav-highlight { background: linear-gradient(135deg, #1a1a2e 0%, #0f3460 100%); color: #fff; font-weight: 600; box-shadow: 0 2px 6px rgba(26,26,46,0.2); }
.topnav a.nav-highlight:hover { background: linear-gradient(135deg, #3498db 0%, #1a5f9e 100%); }
/* 历史周报板块 */
.history-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; margin-top: 6px; }
.history-card { display: block; text-decoration: none; background: linear-gradient(135deg, #f8f9fc 0%, #eef2f8 100%); border: 1px solid #e3e8f0; border-left: 4px solid #3498db; border-radius: 10px; padding: 16px 18px; color: #1a1a2e; transition: all 0.2s; }
.history-card:hover { transform: translateY(-2px); box-shadow: 0 6px 18px rgba(26,26,46,0.12); border-left-color: #1a5f9e; background: linear-gradient(135deg, #eef4fb 0%, #e3f2fd 100%); }
.history-week { font-size: 20px; font-weight: 800; color: #0f3460; }
.history-date { font-size: 12px; color: #8898aa; margin-top: 6px; }
.history-all { display: inline-block; margin-top: 16px; padding: 10px 22px; background: linear-gradient(135deg, #1a1a2e 0%, #0f3460 100%); color: #fff; text-decoration: none; border-radius: 8px; font-size: 13px; font-weight: 600; box-shadow: 0 2px 8px rgba(26,26,46,0.2); transition: all 0.2s; }
.history-all:hover { background: linear-gradient(135deg, #3498db 0%, #1a5f9e 100%); }
@media (max-width: 768px) {
  .topnav { padding: 8px 10px; }
  .topnav a { padding: 5px 10px; font-size: 12px; }
}
/* 指标卡可点击 */
.metric-card.clickable { cursor: pointer; position: relative; }
.metric-card.clickable::after { content: "点击查看明细"; display: block; font-size: 10px; color: #b0bec5; margin-top: 4px; letter-spacing: 0; text-transform: none; }
.metric-card.clickable.active { outline: 2px solid #3498db; box-shadow: 0 4px 14px rgba(52,152,219,0.25); }
.metric-card.clickable.active::after { content: "点击收起"; color: #3498db; }
/* 预览提示 */
.preview-hint { margin-top: 10px; padding: 6px 12px; background: #f0f7ff; border-radius: 6px; font-size: 12px; color: #1a5f9e; }
.snapshot-note { margin-top: 8px; padding: 6px 12px; background: #fafafa; border-left: 3px solid #bdc3c7; border-radius: 4px; font-size: 11px; color: #7f8c8d; line-height: 1.6; }
/* 关注项标签（折叠态可见） */
.mdp-blocker-tag { display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 8px; background: #f0f4f8; color: #4a6572; font-weight: 600; margin-left: 6px; vertical-align: 1px; }
/* 关注项详情（展开态可见） */
.mdp-blocker-detail { display: none; margin-top: 6px; padding: 8px 12px; background: #f5f7fa; border-radius: 6px; font-size: 12px; color: #4a6572; line-height: 1.6; border-left: 3px solid #90a4ae; }
.mdp-kr.expanded .mdp-blocker-detail { display: block; }
/* 指标详情面板 */
.metric-detail-panel { display: none; background: #fff; border-radius: 10px; padding: 14px 20px; margin: -4px 0 16px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); border-top: 3px solid #3498db; }
.metric-detail-panel.open { display: block; }
.mdp-header { font-size: 14px; font-weight: 700; color: #1a1a2e; margin-bottom: 10px; }
.mdp-kr { border-bottom: 1px solid #f0f2f5; padding: 10px 0; }
.mdp-kr:last-child { border-bottom: none; }
.mdp-kr-row { display: flex; align-items: center; gap: 10px; cursor: pointer; flex-wrap: wrap; }
.mdp-kr-row:hover .mdp-kr-name { color: #3498db; }
.mdp-arrow { font-size: 10px; color: #8898aa; transition: transform 0.2s; display: inline-block; }
.mdp-kr.expanded .mdp-arrow { transform: rotate(90deg); }
.mdp-kr-name { flex: 1; min-width: 180px; font-size: 13px; font-weight: 600; color: #2c3e50; }
.mdp-kr-prog { font-size: 13px; font-weight: 700; min-width: 52px; text-align: right; }
.mdp-kr-meta { font-size: 12px; color: #8898aa; }
.mdp-desc { display: none; margin-top: 8px; padding: 10px 12px; background: #f8f9fa; border-radius: 6px; font-size: 12px; color: #555; line-height: 1.8; white-space: pre-wrap; border-left: 3px solid #3498db; }
.mdp-kr.expanded .mdp-desc { display: block; }
.mdp-desc.empty { color: #b0bec5; border-left-color: #ccc; }
.mdp-desc.diff { white-space: pre-wrap; word-break: break-word; }
.mdp-desc.diff .diff-line { margin: 2px 0; }
.mdp-desc.diff .diff-same { color: #555; background: transparent; }
.mdp-desc.diff .diff-added { background: #e8f8e8; color: #1a7d1a; font-weight: 600; }
.mdp-desc.diff .diff-inline-add { background: #e8f8e8; color: #1a7d1a; font-weight: 600; border-radius: 3px; padding: 0 2px; }
.mdp-desc.diff .diff-inline-del { background: #ffeaea; color: #c0392b; text-decoration: line-through; text-decoration-color: #e74c3c; border-radius: 3px; padding: 0 2px; }
/* 本周新增进展面板：左右对照样式 */
.mdp-kr.compare-row { background: #fafbfc; border-radius: 10px; padding: 12px 14px; margin: 8px 0; border: 1px solid rgba(0,0,0,0.04); }
.mdp-kr.compare-row .mdp-kr-row { cursor: default; }
.mdp-kr.compare-row .desc-diff.compact { margin-top: 10px; background: #fff; border-radius: 8px; padding: 12px; border: 1px solid rgba(0,0,0,0.04); }
.mdp-kr.compare-row .desc-diff.compact .desc-text { white-space: normal; }
.mdp-kr.compare-row .desc-diff.compact .diff-line { margin: 2px 0; padding: 2px 6px; border-radius: 4px; }
.mdp-kr.compare-row .desc-diff.compact .diff-same { color: #888; }
.mdp-kr.compare-row .desc-diff.compact .diff-removed { background: #ffeaea; color: #c0392b; text-decoration: line-through; text-decoration-color: #e74c3c; }
.mdp-kr.compare-row .desc-diff.compact .diff-added { background: #e8f8e8; color: #1a7d1a; font-weight: 600; }
.mdp-kr.compare-row .desc-diff.compact .desc-empty { color: #ccc; font-style: italic; padding: 2px 6px; }
/* 进度分布图 */
.dist-chart { background: #fff; border-radius: 10px; padding: 18px 20px; margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
.dist-title { font-size: 14px; font-weight: 700; color: #1a1a2e; margin-bottom: 12px; }
.dist-bar { display: flex; height: 34px; border-radius: 8px; overflow: hidden; }
.dist-seg { display: flex; align-items: center; justify-content: center; color: #fff; font-size: 12px; font-weight: 700; min-width: 0; transition: flex 0.3s; cursor: default; }
.dist-legend { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 10px; font-size: 12px; color: #666; }
.dist-legend .dot { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 5px; vertical-align: -1px; }
/* 周环比 */
.comparison-banner { display: flex; align-items: center; gap: 12px; padding: 14px 18px; background: linear-gradient(135deg, #f0f7ff 0%, #e3f2fd 100%); border-radius: 12px; margin-bottom: 14px; border: 1px solid rgba(52,152,219,0.12); }
.comparison-banner.first-week { background: linear-gradient(135deg, #f8f9fa 0%, #eef1f5 100%); border-color: rgba(0,0,0,0.06); }
.comparison-banner .comp-icon { font-size: 22px; }
.comparison-banner .comp-title { font-size: 14px; font-weight: 700; color: #1a1a2e; }
.comparison-banner .comp-sub { font-size: 12px; color: #8898aa; }
.comparison-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
.comp-card { background: #fff; border-radius: 12px; padding: 16px 14px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.04); border: 1px solid rgba(0,0,0,0.03); }
.comp-card .comp-label { font-size: 11px; color: #8898aa; margin-bottom: 6px; font-weight: 600; }
.comp-card .comp-cur { font-size: 22px; font-weight: 800; color: #1a1a2e; }
.comp-card .comp-prev { font-size: 11px; color: #aab7b8; margin-top: 2px; }
.comp-card .comp-delta { margin-top: 6px; }
.delta-badge { display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 700; }
.delta-up { background: #e3f2fd; color: #1a5f9e; }
.delta-down { background: #fdecea; color: #d32f2f; }
.delta-neutral { background: #f0f2f5; color: #7f8c8d; }
/* 本周变化一览 */
.change-group { margin-bottom: 22px; }
.change-group:last-child { margin-bottom: 0; }
.change-group-title { font-size: 14px; font-weight: 700; color: #1a1a2e; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }
.change-count { font-size: 11px; color: #8898aa; font-weight: 600; background: #f0f2f5; padding: 2px 10px; border-radius: 10px; }
.change-empty { font-size: 13px; color: #aab7b8; padding: 12px 0; }
.change-item { background: #fafbfc; border-radius: 10px; padding: 14px 18px; margin-bottom: 8px; border: 1px solid rgba(0,0,0,0.04); }
.change-item:last-child { margin-bottom: 0; }
.change-kr { font-size: 13px; font-weight: 600; color: #2c3e50; margin-bottom: 6px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.change-kr .o-tag { font-size: 10px; padding: 1px 6px; border-radius: 6px; background: #e8eaf6; color: #3949ab; font-weight: 700; }
.change-tag { font-size: 10px; padding: 1px 7px; border-radius: 8px; font-weight: 700; }
.change-tag.tag-new { background: #e8f5e9; color: #1b5e20; }
.change-tag.tag-update { background: #e3f2fd; color: #1a5f9e; }
.change-delta-row { display: flex; align-items: center; gap: 10px; font-size: 13px; }
.change-delta-row .before-val { color: #aab7b8; font-weight: 600; }
.change-delta-row .after-val { color: #1a5f9e; font-weight: 800; }
.change-delta-row .arrow { color: #bbb; font-size: 14px; }
.desc-diff { display: flex; align-items: flex-start; gap: 14px; margin-top: 4px; }
.desc-diff .desc-col { flex: 1; min-width: 0; }
.desc-diff .desc-label { font-size: 10px; color: #aab7b8; font-weight: 700; margin-bottom: 3px; text-transform: uppercase; letter-spacing: 0.5px; }
.desc-diff .desc-text { font-size: 13px; line-height: 1.7; color: #555; word-break: break-word; white-space: pre-wrap; }
.desc-diff .desc-text.before { color: #999; }
.desc-diff .desc-text.after { color: #1a1a2e; font-weight: 500; }
.desc-diff .desc-text.compact { white-space: normal; }
.desc-diff .desc-arrow { font-size: 18px; color: #3498db; flex-shrink: 0; padding-top: 14px; }
.desc-diff .desc-empty { font-size: 13px; color: #ccc; font-style: italic; }
/* 逐行diff高亮 */
.diff-line { display: block; padding: 2px 6px; margin: 0 -6px; border-radius: 4px; }
.diff-line.diff-same { color: #b0b0b0; }
.diff-line.diff-removed { background: #ffeaea; color: #c0392b; text-decoration: line-through; text-decoration-color: #e74c3c; }
.diff-line.diff-added { background: #e8f8e8; color: #1a7d1a; font-weight: 600; }
/* 本周有进展面板的变化图例 */
.diff-legend { display: flex; gap: 18px; margin: 4px 0 12px; font-size: 11px; color: #8898aa; flex-wrap: wrap; }
.diff-legend .lg-add, .diff-legend .lg-del, .diff-legend .lg-same { display: inline-flex; align-items: center; gap: 6px; }
.diff-legend .lg-add::before { content: ''; width: 13px; height: 13px; background: #e8f8e8; border: 1px solid #1a7d1a; border-radius: 3px; }
.diff-legend .lg-del::before { content: ''; width: 13px; height: 13px; background: #ffeaea; border: 1px solid #c0392b; border-radius: 3px; }
.diff-legend .lg-same::before { content: ''; width: 13px; height: 13px; background: #f5f5f5; border: 1px solid #888; border-radius: 3px; }
@media (max-width: 768px) {
  .desc-diff { flex-direction: column; gap: 6px; }
  .desc-diff .desc-arrow { display: none; }
}
/* 图表网格 */
.chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-bottom: 18px; }
.chart-box { background: #fafbfc; border-radius: 12px; padding: 18px; border: 1px solid rgba(0,0,0,0.04); }
.chart-box.full-width { grid-column: 1 / -1; }
.chart-title { font-size: 13px; font-weight: 700; color: #1a1a2e; margin-bottom: 14px; }
/* 环形图 */
.donut-wrap { display: flex; align-items: center; gap: 24px; flex-wrap: wrap; }
.donut-chart { width: 140px; height: 140px; flex-shrink: 0; }
.donut-svg { width: 100%; height: 100%; transform: rotate(-90deg); }
.donut-legend { display: flex; flex-direction: column; gap: 8px; font-size: 12px; color: #555; }
.donut-legend-item { display: flex; align-items: center; gap: 6px; }
.donut-legend-item .dot { width: 10px; height: 10px; border-radius: 3px; }
.donut-seg { cursor: pointer; transition: opacity 0.2s; }
.donut-seg:hover { opacity: 0.82; }
/* 各O进度条形图 */
.o-bar-chart { display: flex; flex-direction: column; gap: 14px; }
.o-bar-compare-row { display: flex; align-items: center; gap: 12px; }
.o-bar-compare-label { width: 260px; font-size: 13px; font-weight: 600; color: #2c3e50; flex-shrink: 0; }
.o-bar-compare-track { flex: 1; height: 28px; background: #f0f0f0; border-radius: 6px; overflow: hidden; position: relative; }
.o-bar-compare-track .o-prev-bar { position: absolute; top: 0; left: 0; height: 100%; background: #d0d8e0; border-radius: 6px; z-index: 1; }
.o-bar-compare-fill { position: relative; z-index: 2; height: 100%; background: linear-gradient(90deg, #3498db 0%, #1a5f9e 100%); border-radius: 6px; display: flex; align-items: center; justify-content: flex-end; padding-right: 10px; min-width: 40px; }
.o-bar-compare-fill span { color: #fff; font-size: 12px; font-weight: 700; white-space: nowrap; }
.o-bar-delta { font-size: 11px; font-weight: 700; margin-left: 4px; }
/* 指标面板变化标记 */
.mdp-delta-up { display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 8px; background: #e3f2fd; color: #1a5f9e; font-weight: 700; margin-left: 4px; }
.mdp-delta-new { display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 8px; background: #e8f5e9; color: #1b5e20; font-weight: 700; margin-left: 4px; }
@media (max-width: 768px) {
  .mdp-kr-name { min-width: 120px; }
  .dist-bar { height: 28px; }
  .dist-seg { font-size: 11px; }
  .comparison-grid { grid-template-columns: repeat(3, 1fr); }
  .chart-grid { grid-template-columns: 1fr; }
  .o-bar-compare-label { width: 180px; }
}
"""

JS_FUNCS = """
// 只保留交互增强：筛选、排序、展开明细
function progColor(p) {
  if (p === null) return '#aab7b8';
  if (p >= 100) return '#0f3460';
  if (p >= 70) return '#1a5f9e';
  if (p >= 30) return '#5dade2';
  return '#e57373';
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
    const deadlineDisplay = kr.deadline ? new Date(kr.deadline).toLocaleDateString('zh-CN') + (overdue ? ` <span class="badge badge-red">超目标${daysOverdue(kr.deadline)}天</span>` : '') : '<span style="color:#ccc">未设定</span>';
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
function toggleAllDetails() {
  const btn = document.getElementById('toggleAllBtn');
  const visibleRows = document.querySelectorAll('#krBody .kr-row:not(.hidden)');
  const allExpanded = Array.from(visibleRows).every(r => r.classList.contains('expanded'));
  visibleRows.forEach((row, i) => {
    const idx = Array.from(document.querySelectorAll('#krBody .kr-row')).indexOf(row);
    const detail = document.getElementById('detail-' + idx);
    if (!detail) return;
    if (allExpanded) {
      detail.style.display = 'none';
      row.classList.remove('expanded');
    } else {
      detail.style.display = 'table-row';
      row.classList.add('expanded');
    }
  });
  if (btn) btn.textContent = allExpanded ? '展开全部描述' : '收起全部描述';
}
function toggleMetricDetail(key) {
  const panel = document.getElementById('mdp-' + key);
  if (!panel) return;
  const isOpen = panel.classList.contains('open');
  // 关闭所有面板和卡片激活态
  document.querySelectorAll('.metric-detail-panel').forEach(p => p.classList.remove('open'));
  document.querySelectorAll('.metric-card.clickable').forEach(c => c.classList.remove('active'));
  if (!isOpen) {
    panel.classList.add('open');
    const card = document.querySelector('.metric-card[data-metric="' + key + '"]');
    if (card) card.classList.add('active');
  }
}
function toggleMKR(el) {
  const kr = el.closest('.mdp-kr');
  if (kr) kr.classList.toggle('expanded');
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
    if (btn.classList.contains('toggle-all-btn')) return;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderKRTable(btn.dataset.filter);
    const taBtn = document.getElementById('toggleAllBtn');
    if (taBtn) taBtn.textContent = '展开全部描述';
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
  const taBtn2 = document.getElementById('toggleAllBtn');
  if (taBtn2) taBtn2.textContent = '展开全部描述';
}
"""

import html as _html_mod

def _esc(text):
    """HTML转义"""
    return _html_mod.escape(str(text), quote=False)

def _prog_color(p):
    if p is None: return '#aab7b8'
    if p >= 100: return '#0f3460'
    if p >= 70: return '#1a5f9e'
    if p >= 30: return '#5dade2'
    return '#e57373'

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
            star = ' ⚠️超目标日期' if a['is_overdue'](k) else (' ⚠️' if a['is_risk'](k) else '')
            kr_lines.append(f'{k["kr"]} - {p}{star}')
        tip = _html_mod.escape('\n'.join(kr_lines), quote=True)
        overdue_str = f' / 超目标{overdue_count}' if overdue_count > 0 else ''
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
            overdue_badge = f' <span class="badge badge-red">超目标{a["days_overdue"](kr)}天</span>' if overdue else ''
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
            overdue_tag = f'<span class="badge badge-red">超目标{a["days_overdue"](kr)}天</span>' if a['is_overdue'](kr) else ''
            blocker_html = f'<div class="ph-blocker"><strong>关注项：</strong>{_esc(kr["blocker"][:120])}</div>' if kr.get('blocker') else ''
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
        overdue_tag = f'<span class="badge badge-red">超目标{a["days_overdue"](kr)}天</span>' if a['is_overdue'](kr) else ''
        parts.append(f'''<div class="stale-item">
  <div class="stale-name"><span class="o-badge">{kr["o"]}</span>{_esc(kr["kr"])} {overdue_tag}</div>
  <div class="stale-info"><span style="color:{prog_color};font-weight:700">{prog_str}</span> · {_esc(followers)} · <span class="stale-reason">{reason_str}</span></div>
</div>''')
    return ''.join(parts)

def _mdp_kr_row(kr, a, extra_meta='', before_desc=None, after_desc=None, type_tag=''):
    """指标详情面板中的单条KR（点击展开关键结果描述）"""
    prog = kr['progress']
    prog_str = f'{prog}%' if prog is not None else '未录入'
    prog_color = _prog_color(prog)
    followers = '、'.join(kr['followers'][:3]) if kr['followers'] else '—'
    more_fol = f'等{len(kr["followers"])}人' if len(kr['followers']) > 3 else ''
    if a['is_overdue'](kr):
        meta_extra = f'超过目标日期{a["days_overdue"](kr)}天'
    elif kr['deadline']:
        meta_extra = f'目标{kr["deadline"][:10]}'
    else:
        meta_extra = '未设目标日期'
    # 网点信息
    sites_str = f' · {_esc(kr["sites"])}' if kr['sites'] and kr['sites'] != '—' else ''
    # 关注项指示器
    blocker_tag = ' <span class="mdp-blocker-tag">需关注</span>' if kr.get('blocker') else ''
    if extra_meta:
        meta_extra = f'{meta_extra} · {extra_meta}'
    desc = (kr.get('krDesc') or '').strip()
    if desc and desc not in ('无', '暂无', '-', '—', 'n/a', 'N/A'):
        # 有周环比描述对比时，精确内联高亮本周变化（新增=绿底，删除=红删除线）
        if before_desc is not None and after_desc is not None and before_desc != after_desc:
            desc_html = _inline_desc_diff(before_desc, after_desc)
            desc_cls = 'diff'
        else:
            desc_html = _esc(desc)
            desc_cls = ''
    else:
        desc_html = '暂无进展描述'
        desc_cls = 'empty'
    # 关注项详情（展开时可见）
    blocker_detail = f'<div class="mdp-blocker-detail"><strong>关注项：</strong>{_esc(kr["blocker"][:150])}</div>' if kr.get('blocker') else ''
    return f'''<div class="mdp-kr">
  <div class="mdp-kr-row" onclick="toggleMKR(this)">
    <span class="mdp-arrow">▶</span>
    <span class="o-badge">{kr["o"]}</span>
    <span class="mdp-kr-name">{_esc(kr["kr"])}{blocker_tag}</span>
    <span class="mdp-kr-prog" style="color:{prog_color}">{prog_str}</span>
    <span class="mdp-kr-meta">{_esc(followers)}{more_fol}{sites_str} · {_esc(meta_extra)}{type_tag}</span>
  </div>
  <div class="mdp-desc {desc_cls}">{desc_html}</div>
  {blocker_detail}
</div>'''

def _mdp_updated_row(k, a, comp_item=None):
    """指标面板中'本周有进展'的KR行（支持周环比标记）"""
    type_tag = ''
    before_desc = None
    after_desc = None
    if comp_item:
        t = comp_item.get('type', '')
        if t == 'progress':
            type_tag = ' <span class="mdp-delta-up">↗ 进度提升</span>'
            # 进度提升KR若同时有描述变化，也高亮出来
            before_desc = comp_item.get('before_desc', '')
            after_desc = comp_item.get('after_desc', '')
            if before_desc == after_desc:
                before_desc = None
                after_desc = None
        elif t == 'desc':
            type_tag = ' <span class="mdp-delta-new">✎ 描述更新</span>'
            before_desc = comp_item.get('before_desc', '')
            after_desc = comp_item.get('after_desc', '')
        elif t == 'new':
            type_tag = ' <span class="mdp-delta-new">NEW 新增KR</span>'
            # 新增KR：整段描述都是新的，用空before高亮全部
            before_desc = ''
            after_desc = (k.get('krDesc') or '').strip()
    return _mdp_kr_row(k, a, before_desc=before_desc, after_desc=after_desc, type_tag=type_tag)

def _mdp_updated_compare_row(k, a, comp_item=None):
    """'本周新增进展'面板中的KR行：左右对照展示上周vs本周描述变化（截图效果）。"""
    prog = k['progress']
    prog_str = f'{prog}%' if prog is not None else '未录入'
    prog_color = _prog_color(prog)

    type_tag = ''
    before_desc = ''
    after_desc = (k.get('krDesc') or '').strip()
    if comp_item:
        t = comp_item.get('type', '')
        if t == 'progress':
            type_tag = ' <span class="mdp-delta-up">↗ 进度提升</span>'
            before_desc = comp_item.get('before_desc', '')
            after_desc = comp_item.get('after_desc', '')
        elif t == 'desc':
            type_tag = ' <span class="mdp-delta-new">✎ 描述更新</span>'
            before_desc = comp_item.get('before_desc', '')
            after_desc = comp_item.get('after_desc', '')
        elif t == 'new':
            type_tag = ' <span class="mdp-delta-new">NEW 新增KR</span>'
            before_desc = ''
            after_desc = (k.get('krDesc') or '').strip()

    before_html, after_html = _side_by_side_desc_diff(before_desc, after_desc)

    return f'''<div class="mdp-kr compare-row">
  <div class="mdp-kr-row" style="cursor:default">
    <span class="o-badge">{k["o"]}</span>
    <span class="mdp-kr-name">{_esc(k["kr"])}{type_tag}</span>
    <span class="mdp-kr-prog" style="color:{prog_color}">{prog_str}</span>
  </div>
  <div class="desc-diff compact">
    <div class="desc-col">
      <div class="desc-label">上周</div>
      <div class="desc-text before">{before_html}</div>
    </div>
    <div class="desc-arrow">→</div>
    <div class="desc-col">
      <div class="desc-label">本周</div>
      <div class="desc-text after">{after_html}</div>
    </div>
  </div>
</div>'''

def _gen_metric_panels(krs, a, comparison=None):
    """服务端渲染6个指标详情面板（默认隐藏，点击指标卡展开）"""
    panels = {}
    # 1. 平均进度 → 各O分解
    o_rows = []
    for o_code in ['O1', 'O2', 'O3', 'O4']:
        g = a['o_groups'].get(o_code)
        if not g: continue
        avg = a['o_avgs'].get(o_code, 0)
        color = _prog_color(avg)
        prev_avg = comparison['prev_o_avgs'].get(o_code) if comparison else None
        delta_str = f' <span style="color:#1a5f9e;font-size:12px">({prev_avg}%→{avg}%)</span>' if prev_avg is not None else ''
        klist = '、'.join([k['kr'][:16] for k in g['krs'][:3]])
        more = f'等{len(g["krs"])}项' if len(g['krs']) > 3 else ''
        o_rows.append(f'''<div class="mdp-kr">
  <div class="mdp-kr-row" style="cursor:default">
    <span class="o-badge">{o_code}</span>
    <span class="mdp-kr-name">{_esc(g["title"])}</span>
    <span class="mdp-kr-prog" style="color:{color}">{avg}%{delta_str}</span>
    <span class="mdp-kr-meta">{len(g['krs'])}项KR：{_esc(klist)}{more}</span>
  </div>
</div>''')
    panels['avg'] = f'<div class="mdp-header">各目标进度分解（整体平均为各O均值）</div>' + ''.join(o_rows)

    # 2. 本周有进展：有历史快照时显示"真正本周新增进展"，否则显示所有有描述的KR
    if comparison:
        updated_list = comparison['truly_updated']
        updated_krs = []
        for item in updated_list:
            for k in krs:
                if k['recordId'] == item['recordId']:
                    updated_krs.append((k, item))
                    break
        updated_title = f'本周新增进展的KR（较上周{comparison["prev_week"]}）'
    else:
        updated_krs = [(k, None) for k in krs if a['has_update'](k)]
        updated_title = '本周有实质进展的KR（首周基准：所有有描述更新的KR）'
    # 所有KR列表统一按 O1→O2→O3→O4 排序
    updated_krs = sorted(updated_krs, key=lambda x: _o_sort_key(x[0]))

    # 3-6. 其他KR列表类指标
    kr_groups = {
        'updated': (updated_title, updated_krs),
        'done': ('已达成的KR', sorted([(k, None) for k in krs if k['progress'] == 100], key=lambda x: _o_sort_key(x[0]))),
        'overdue': ('超过目标日期未完成的KR', sorted([(k, None) for k in krs if a['is_overdue'](k)], key=lambda x: _o_sort_key(x[0]))),
        'stale': ('停滞KR（无进展描述或进度为0）', sorted([(k, None) for k in krs if a['is_stale'](k)], key=lambda x: _o_sort_key(x[0]))),
        'untracked': ('未录入进度的KR', sorted([(k, None) for k in krs if k['progress'] is None], key=lambda x: _o_sort_key(x[0]))),
    }
    for key, (title, klist) in kr_groups.items():
        if not klist:
            panels[key] = f'<div class="mdp-header">{title}</div><div class="empty-state">无</div>'
        else:
            if key == 'updated':
                rows = ''.join([_mdp_updated_compare_row(k, a, item) for k, item in klist])
                legend = '<div class="diff-legend"><span class="lg-add">绿底 = 本周新增/修改</span><span class="lg-del">红删除线 = 本周删去</span><span class="lg-same">灰色 = 未变化</span></div>'
                panels[key] = f'<div class="mdp-header">{title}（{len(klist)}项）</div>' + legend + rows
            else:
                rows = ''.join([_mdp_kr_row(k, a) for k, item in klist])
                panels[key] = f'<div class="mdp-header">{title}（{len(klist)}项）</div>' + rows
    return panels

def _gen_dist_chart(krs):
    """KR进度分布图（分段堆叠条形图）"""
    buckets = [
        ('0%', '#e57373', [k for k in krs if k['progress'] == 0]),
        ('1-30%', '#5dade2', [k for k in krs if k['progress'] is not None and 0 < k['progress'] <= 30]),
        ('31-70%', '#3498db', [k for k in krs if k['progress'] is not None and 30 < k['progress'] <= 70]),
        ('71-99%', '#1a5f9e', [k for k in krs if k['progress'] is not None and 70 < k['progress'] < 100]),
        ('100%', '#0f3460', [k for k in krs if k['progress'] == 100]),
        ('未追踪', '#aab7b8', [k for k in krs if k['progress'] is None]),
    ]
    total = len(krs) or 1
    segs = []
    legend = []
    for label, color, items in buckets:
        n = len(items)
        if n == 0: continue
        pct = n / total * 100
        # 原生title（最可靠，钉钉内置浏览器也支持）+ CSS tooltip增强
        title_lines = [f'{label}：{n}项（{pct:.1f}%）'] + [k['kr'] for k in items[:10]]
        if len(items) > 10:
            title_lines.append(f'...等共{len(items)}项')
        title_text = '\n'.join(title_lines)
        title_attr = _esc(title_text).replace('"', '&quot;')
        # data-tip用于CSS tooltip（桌面浏览器），属性值中保留真实换行符
        data_tip = '\n'.join(title_lines)
        segs.append(f'<div class="dist-seg tooltip" title="{title_attr}" data-tip="{data_tip}" style="flex:{n};background:{color}">{n}</div>')
        legend.append(f'<span><span class="dot" style="background:{color}"></span>{label}（{n}项）</span>')
    return f'''<div class="dist-title">KR进度分布（共{len(krs)}项 · 悬停分段查看明细）</div>
<div class="dist-bar">{''.join(segs)}</div>
<div class="dist-legend">{''.join(legend)}</div>'''

def _delta_badge(delta, suffix='', reverse=False):
    """生成变化徽章（reverse=True时负数为好事，如逾期数下降）"""
    if delta is None:
        return '<span class="delta-badge delta-neutral">—</span>'
    if delta == 0:
        return '<span class="delta-badge delta-neutral">→ 持平</span>'
    good = (delta > 0) if not reverse else (delta < 0)
    cls = 'delta-up' if good else 'delta-down'
    sign = '+' if delta > 0 else ''
    return f'<span class="delta-badge {cls}">{sign}{delta}{suffix}</span>'

def _gen_comparison_html(a, comparison):
    """周环比指标卡（首周显示无对比提示）"""
    if not comparison:
        return '''<div class="comparison-banner first-week">
  <div class="comp-icon">📊</div>
  <div class="comp-text">
    <div class="comp-title">首周基准数据</div>
    <div class="comp-sub">下周自动生成后将显示与本周的环比变化</div>
  </div>
</div>'''
    prev_week = comparison['prev_week']
    cards = [
        ('平均进度', f'{a["overall_avg"]}%', f'{comparison.get("prev_o_avgs", {}).get("overall", a["overall_avg"] - comparison["overall_avg_delta"])}%', _delta_badge(comparison['overall_avg_delta'], '%')),
        ('本周新进展', len(comparison['truly_updated']), '上周', _delta_badge(comparison['updated_count_delta'], '项')),
        ('已达成', a['done_count'], comparison.get('prev_summary', {}).get('done_count', a['done_count']), _delta_badge(comparison['done_count_delta'], '项')),
        ('停滞项', a['stale_count'], comparison.get('prev_summary', {}).get('stale_count', a['stale_count']), _delta_badge(comparison['stale_count_delta'], '项', reverse=True)),
        ('超目标日期', a['overdue_count'], comparison.get('prev_summary', {}).get('overdue_count', a['overdue_count']), _delta_badge(comparison['overdue_count_delta'], '项', reverse=True)),
    ]
    # 修正prev值显示
    prev_summary = comparison.get('prev_summary', {})
    cards = [
        ('平均进度', f'{a["overall_avg"]}%', f'{a["overall_avg"] - comparison["overall_avg_delta"]}%', _delta_badge(comparison['overall_avg_delta'], '%')),
        ('本周新进展', len(comparison['truly_updated']), prev_summary.get('updated_count', 0), _delta_badge(len(comparison['truly_updated']) - prev_summary.get('updated_count', 0), '项')),
        ('已达成', a['done_count'], prev_summary.get('done_count', 0), _delta_badge(comparison['done_count_delta'], '项')),
        ('停滞项', a['stale_count'], prev_summary.get('stale_count', 0), _delta_badge(comparison['stale_count_delta'], '项', reverse=True)),
        ('超目标日期', a['overdue_count'], prev_summary.get('overdue_count', 0), _delta_badge(comparison['overdue_count_delta'], '项', reverse=True)),
    ]
    html = f'<div class="comparison-banner"><div class="comp-text"><div class="comp-title">较上周 {prev_week} 环比变化</div></div></div><div class="comparison-grid">'
    for label, cur, prev, delta in cards:
        html += f'''<div class="comp-card">
  <div class="comp-label">{label}</div>
  <div class="comp-cur">{cur}</div>
  <div class="comp-prev">上周 {prev}</div>
  <div class="comp-delta">{delta}</div>
</div>'''
    html += '</div>'
    return html

def _inline_desc_diff(before_text, after_text):
    """精确字符级内联diff：在本周文本基础上，标出到底哪些字变了。
    - 新增/修改后的内容：绿底高亮
    - 被删除的内容：红字删除线
    用于'本周有进展'面板，使领导一眼看到变化点，且不重复展示未变文字。
    """
    def _e(s):
        import html as _h
        return _h.escape(s, quote=False)

    bt = before_text or ''
    at = after_text or ''
    if not bt:
        # 首次填写/整段新增：全部绿底
        if at:
            return f'<span class="diff-inline-add">{_e(at)}</span>'
        return '<span class="desc-empty">（无描述）</span>'
    if not at:
        # 已清空：整段红删除线
        return f'<span class="diff-inline-del">{_e(bt)}</span>'

    sm = difflib.SequenceMatcher(a=bt, b=at, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            out.append(_e(bt[i1:i2]))
        elif tag == 'delete':
            seg = bt[i1:i2]
            out.append(f'<span class="diff-inline-del">{_e(seg)}</span>' if seg.strip() else _e(seg))
        elif tag == 'insert':
            seg = at[j1:j2]
            out.append(f'<span class="diff-inline-add">{_e(seg)}</span>' if seg.strip() else _e(seg))
        elif tag == 'replace':
            del_seg = bt[i1:i2]
            add_seg = at[j1:j2]
            out.append(f'<span class="diff-inline-del">{_e(del_seg)}</span>' if del_seg.strip() else _e(del_seg))
            out.append(f'<span class="diff-inline-add">{_e(add_seg)}</span>' if add_seg.strip() else _e(add_seg))
    return ''.join(out)

def _side_by_side_desc_diff(before_text, after_text):
    """行级左右对照diff：返回 (before_html, after_html)。
    - 删除行：红底删除线（before栏）
    - 新增行：绿底高亮（after栏）
    - 未变行：正常灰色显示
    用于'本周新增进展'面板，实现截图式的上周↔本周左右对照效果。
    """
    def _e(s):
        import html as _h
        return _h.escape(s, quote=False)

    before_lines = before_text.split('\n') if before_text else []
    after_lines = after_text.split('\n') if after_text else []

    sm = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    before_parts = []
    after_parts = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for line in before_lines[i1:i2]:
                before_parts.append(f'<div class="diff-line diff-same">{_e(line)}</div>')
            for line in after_lines[j1:j2]:
                after_parts.append(f'<div class="diff-line diff-same">{_e(line)}</div>')
        elif tag == 'replace':
            for line in before_lines[i1:i2]:
                before_parts.append(f'<div class="diff-line diff-removed">{_e(line)}</div>')
            for line in after_lines[j1:j2]:
                after_parts.append(f'<div class="diff-line diff-added">{_e(line)}</div>')
        elif tag == 'delete':
            for line in before_lines[i1:i2]:
                before_parts.append(f'<div class="diff-line diff-removed">{_e(line)}</div>')
        elif tag == 'insert':
            for line in after_lines[j1:j2]:
                after_parts.append(f'<div class="diff-line diff-added">{_e(line)}</div>')

    before_html = ''.join(before_parts) if before_parts else '<div class="desc-empty">（上周无描述）</div>'
    after_html = ''.join(after_parts) if after_parts else '<div class="desc-empty">（已清空）</div>'
    return before_html, after_html

def _gen_changes_overview(a, comparison):
    """本周变化一览：逐条展示KR的进度变化和关键结果描述变化（before→after）"""
    if not comparison:
        return '''<div class="comparison-banner first-week">
  <div class="comp-icon">📊</div>
  <div class="comp-text">
    <div class="comp-title">首周基准数据</div>
    <div class="comp-sub">下周自动生成后将显示与本周的变化对比</div>
  </div>
</div>'''

    prev_week = comparison['prev_week']
    parts = []

    # ── 进度提升 ──
    pg = comparison.get('progress_gained', [])
    parts.append(f'<div class="change-group">')
    parts.append(f'<div class="change-group-title">📈 进度提升 <span class="change-count">{len(pg)}项</span></div>')
    if pg:
        for k in pg:
            parts.append(f'''<div class="change-item">
  <div class="change-kr"><span class="o-tag">{_esc(k["o"])}</span>{_esc(k["kr"])}</div>
  <div class="change-delta-row">
    <span class="before-val">{k["before"]}%</span>
    <span class="arrow">→</span>
    <span class="after-val">{k["after"]}%</span>
    {_delta_badge(k["delta"], "%")}
  </div>
</div>''')
    else:
        parts.append('<div class="change-empty">本周无进度提升项</div>')
    parts.append('</div>')

    # ── 新增KR ──
    nk = comparison.get('new_krs', [])
    if nk:
        parts.append(f'<div class="change-group">')
        parts.append(f'<div class="change-group-title">🆕 新增KR <span class="change-count">{len(nk)}项</span></div>')
        for k in nk:
            p_str = f'{k.get("progress", 0)}%' if k.get('progress') is not None else '未追踪'
            parts.append(f'''<div class="change-item">
  <div class="change-kr"><span class="o-tag">{_esc(k["o"])}</span>{_esc(k["kr"])}</div>
  <div class="change-delta-row"><span class="before-val">新增</span><span class="arrow">→</span><span class="after-val">{p_str}</span></div>
</div>''')
        parts.append('</div>')

    # ── 移除KR ──
    rk = comparison.get('removed_krs', [])
    if rk:
        parts.append(f'<div class="change-group">')
        parts.append(f'<div class="change-group-title">❌ 移除KR <span class="change-count">{len(rk)}项</span></div>')
        for k in rk:
            p_str = f'{k.get("progress", 0)}%' if k.get('progress') is not None else '未追踪'
            parts.append(f'''<div class="change-item">
  <div class="change-kr"><span class="o-tag">{_esc(k["o"])}</span>{_esc(k["kr"])}</div>
  <div class="change-delta-row"><span class="before-val">{p_str}</span><span class="arrow">→</span><span class="after-val">已移除</span></div>
</div>''')
        parts.append('</div>')

    return '\n'.join(parts)

def _gen_status_donut(krs, a):
    """状态分布环形图（SVG），悬停显示该分类下的KR明细"""
    # 非重叠分类（互斥）
    categories = [
        ('已达成', [k for k in krs if k['progress'] == 100], '#0f3460'),
        ('推进中', [k for k in krs if k['progress'] is not None and 0 < k['progress'] < 100], '#3498db'),
        ('未开始', [k for k in krs if k['progress'] == 0], '#5dade2'),
        ('未追踪', [k for k in krs if k['progress'] is None], '#aab7b8'),
    ]
    total = len(krs) or 1

    radius = 60
    cx, cy = 70, 70
    stroke = 18
    circ = 2 * 3.1416 * radius
    offset = 0
    segments = []
    legend = []
    for label, items, color in categories:
        n = len(items)
        if n == 0: continue
        pct = n / total
        dash = pct * circ
        # 悬停明细：分类名+数量+占比+最多10条KR名
        title_lines = [f'{label}：{n}项（{pct*100:.0f}%）']
        for k in items[:10]:
            p = f'{k["progress"]}%' if k['progress'] is not None else '未追踪'
            title_lines.append(f'• {k["kr"]} - {p}')
        if len(items) > 10:
            title_lines.append(f'...等共{len(items)}项')
        title_text = _esc('\n'.join(title_lines)).replace('"', '&quot;')
        title_svg = f'<title>{title_text}</title>'
        segments.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="{color}" stroke-width="{stroke}" stroke-dasharray="{dash:.2f} {circ - dash:.2f}" stroke-dashoffset="{-offset:.2f}" class="donut-seg">{title_svg}</circle>')
        offset += dash
        legend.append(f'<span class="donut-legend-item"><span class="dot" style="background:{color}"></span>{label} {n}项（{pct*100:.0f}%）</span>')
    if not segments:
        segments.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="#eef" stroke-width="{stroke}" />')
    inner_text = f'<g transform="rotate(90, {cx}, {cy})"><text x="{cx}" y="{cy-4}" text-anchor="middle" font-size="14" font-weight="700" fill="#1a1a2e">{total}</text><text x="{cx}" y="{cy+12}" text-anchor="middle" font-size="10" fill="#8898aa">项KR</text></g>'
    svg = f'<svg class="donut-svg" viewBox="0 0 140 140">{ "".join(segments) }{inner_text}</svg>'
    return f'<div class="donut-wrap"><div class="donut-chart">{svg}</div><div class="donut-legend">{"".join(legend)}</div></div>'

def _gen_o_bar_chart(a, comparison):
    """各O进度横向条形图（带上周对比）"""
    bars = []
    max_val = 100
    for o_code in ['O1', 'O2', 'O3', 'O4']:
        title = a['o_groups'].get(o_code, {}).get('title', '')
        avg = a['o_avgs'].get(o_code, 0)
        prev_avg = comparison['prev_o_avgs'].get(o_code) if comparison else None
        prev_bar = f'<div class="o-prev-bar" style="width:{prev_avg}%"></div>' if prev_avg is not None else ''
        delta = f' <span class="o-bar-delta">{avg - prev_avg:+.0f}%</span>' if prev_avg is not None else ''
        bars.append(f'''<div class="o-bar-compare-row">
  <div class="o-bar-compare-label"><span class="o-badge">{o_code}</span>{_esc(title)}</div>
  <div class="o-bar-compare-track">
    {prev_bar}
    <div class="o-bar-compare-fill" style="width:{avg}%"><span>{avg}%{delta}</span></div>
  </div>
</div>''')
    return ''.join(bars)

# ===== 智能洞察（DeepSeek，OpenAI 兼容接口）=====

def build_data_summary_text(a, comparison=None):
    """构造给大模型的精简数据摘要（结构化、不泄露密钥）。失败也不影响主流程。"""
    krs = a['krs']
    o_map_rev = {v[0]: v[1] for v in O_MAP.values()}
    o_prog = {}
    for k in krs:
        o_prog.setdefault(k['o'], []).append(k.get('progress') or 0)
    lines = []
    today_cn = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y年%m月%d日')
    lines.append(f'全国各网点基于集团四大OKR的推进执行数据（分析时点：{today_cn}。注意：这是各网点推进执行层面的数据，不是集团目标本身的完成情况）：')
    lines.append(f'- OKR推进平均进度：{a["overall_avg"]}%')
    lines.append(f'- KR总数：{len(krs)}项；本周有进展：{a["updated_count"]}项；已达成：{a["done_count"]}项；停滞：{a["stale_count"]}项；超过目标日期未完成：{a["overdue_count"]}项；未追踪：{a["untracked_count"]}项')
    for o in sorted(o_prog.keys(), key=lambda x: int(''.join(ch for ch in x if ch.isdigit()) or 0)):
        ps = o_prog[o]
        avg = round(sum(ps) / len(ps)) if ps else 0
        lines.append(f'- {o} {o_map_rev.get(o, "")}：平均进度 {avg}%')
    concerns = []
    for k in krs:
        if a['is_risk'](k) or a['is_overdue'](k):
            fol_list = k.get('followers') or []
            fol_names = []
            for f in fol_list:
                if isinstance(f, dict):
                    fol_names.append(f.get('name') or '')
                elif isinstance(f, str):
                    fol_names.append(f)
            fol = '、'.join(n for n in fol_names if n) or '未指定'
            blk = (k.get('blocker') or '').strip()
            concerns.append(f'  · {k["o"]} {k["kr"]}（进度{k.get("progress") or 0}%，跟进人{fol}）{("卡点："+blk) if blk else ""}')
    if concerns:
        lines.append('- 需重点关注KR（风险或超目标日期）：')
        lines.extend(concerns[:10])
    if comparison:
        lines.append(f'- 周环比（较{comparison["prev_week"]}）：平均进度变化{comparison["overall_avg_delta"]:+}%，新增进展{len(comparison["truly_updated"])}项')
    return '\n'.join(lines)


def call_llm_insight(api_key, model, data_text):
    """调用 DeepSeek（OpenAI兼容）生成管理层洞察。返回 dict 或 None（失败兜底）。"""
    if not api_key:
        return None
    url = 'https://api.deepseek.com/chat/completions'
    today_cn = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y年%m月%d日')
    system = (
        '你是OKR推进数据分析助手。'
        '重要背景：这份数据反映的是"全国各网点基于集团OKR的推进执行情况"，即网点层面的落地进度，'
        '绝不是集团目标本身的完成情况。'
        f'当前日期是{today_cn}。'
        '严格要求：'
        '① 用简体中文，客观、简洁、可直接给管理层看；'
        '② 只基于数据中存在的内容分析，不虚构任何数据中不存在的信息；'
        '③ 表述口径必须是"OKR推进进度"，严禁说"集团整体平均进度""集团完成度"这类话——'
        '进度数字代表各网点推进执行的平均水平，正确说法是"OKR推进平均进度约56%"；'
        '④ 不臆想组织架构或职位名称——数据中只有"跟进人"姓名，不要出现"总经理办公室""副总裁""运营总监"等任何虚构职位；'
        '⑤ 语言中立客观，不使用归因性或指责性措辞——禁止使用"领导缺失""不重视""失职""不作为""推诿"等负面定性词，只客观描述进度状态和卡点事实；'
        '⑥ 不指派任务、不安排人员、不设截止日期、不提改进建议——只做客观进展描述，不教管理层做事；'
        '⑦ 严格只输出JSON，格式：'
        '{"summary":"一段80-120字客观概括OKR推进态势与各目标进展差异",'
        '"risks":["客观描述的关注点1（基于进度/卡点事实，不带指责）","关注点2"]}'
    )
    user = f'以下是本周OKR推进数据摘要（当前日期{today_cn}）：\n{data_text}\n\n请输出上述JSON。'
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        'response_format': {'type': 'json_object'},
        'temperature': 0.3,
        'max_tokens': 700,
    }
    headers = {'Authorization': f'Bearer {api_key}'}
    try:
        resp = api_request(url, 'POST', headers=headers, body=body, timeout=60, retries=1)
        if 'error' in resp or 'choices' not in resp:
            print(f'   AI洞察接口返回异常: {json.dumps(resp, ensure_ascii=False)[:200]}')
            return None
        content = resp['choices'][0]['message']['content']
        insight = json.loads(content)
        if not isinstance(insight, dict) or 'summary' not in insight:
            return None
        insight.setdefault('risks', [])
        insight.setdefault('actions', [])
        return insight
    except Exception as e:
        print(f'   AI洞察生成失败（跳过，不影响报告）: {e}')
        return None


def _gen_ai_insight_html(insight):
    """渲染AI洞察板块；无 insight 时返回空字符串（不显示板块）。"""
    if not insight:
        return ''
    summary = (insight.get('summary') or '').strip()
    risks = insight.get('risks') or []
    parts = ['<div class="section ai-insight" id="sec-ai">', '  <h2>智能洞察</h2>']
    if summary:
        parts.append(f'  <div class="ai-summary">{_esc(summary)}</div>')
    if risks:
        parts.append('  <div class="ai-block"><div class="ai-block-title">⚠️ 重点关注</div><ul>')
        for r in risks[:5]:
            parts.append(f'    <li>{_esc(r)}</li>')
        parts.append('  </ul></div>')
    parts.append('  <div class="ai-note">本板块由 DeepSeek 基于表格数据自动生成，仅供参考，请以最新实际进展为准。</div>')
    parts.append('</div>')
    return '\n'.join(parts)


def generate_html(a, today_str, week_str='', comparison=None, ai_insight=None):
    """生成GM视角HTML报告（本周进展亮点置顶，服务端渲染+JS增强交互）"""
    krs = sorted(a['krs'], key=_o_sort_key)
    # 分析时间（北京时间 UTC+8），用于说明本报告对应的表格快照时刻
    analysis_time = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')
    # 有历史快照时，"本周有进展"显示真正新增进展数；首周显示所有有描述的KR数
    updated_count_display = len(comparison['truly_updated']) if comparison else a['updated_count']

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
            'title': f'{a["overdue_count"]}项KR已超过目标日期但未完成',
            'detail': detail_items,
            'action': '建议了解各项超过目标日期KR的推进计划和时间安排，评估目标是否需要调整'
        })
    for kr in krs:
        if kr['progress'] == 0 and kr['status'] == '未开始':
            risk_cards.append({
                'level': 'high', 'title': f'{kr["kr"]} 0%未开始',
                'detail': f'当前情况：{kr["blocker"] or "未记录"}，{kr["followerCount"]}人跟进',
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
                'title': f'{kr["kr"]} {kr["progress"]}%{"（超过目标日期）" if a["is_overdue"](kr) else ""}',
                'detail': f'当前情况：{kr["blocker"] or "未记录"}',
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
        dq_items.append({'red': True, 'title': f'{a["overdue_count"]}项KR已超过目标日期但未完成', 'items': items_html})

    # 指标卡tooltip
    import html as _html
    tip_done = _html.escape('\n'.join([k['kr'] for k in krs if k['progress'] == 100]) or '暂无')
    tip_risk = _html.escape('\n'.join([k['kr'] for k in krs if a['is_risk'](k)]) or '无')
    tip_overdue = _html.escape('\n'.join([k['kr'] for k in krs if a['is_overdue'](k)]) or '无')
    tip_updated = _html.escape('\n'.join([k['kr'] for k in krs if a['has_update'](k)]) or '无')
    tip_stale = _html.escape('\n'.join([k['kr'] for k in krs if a['is_stale'](k)]) or '无')
    tip_untracked = _html.escape('\n'.join([k['kr'] for k in krs if k['progress'] is None]) or '无')

    # 服务端渲染各板块
    dist_chart_html = _gen_dist_chart(krs)
    ai_html = _gen_ai_insight_html(ai_insight)
    status_donut_html = _gen_status_donut(krs, a)
    o_bar_chart_html = _gen_o_bar_chart(a, comparison)
    metric_panels = _gen_metric_panels(krs, a, comparison=comparison)
    kr_table_html = _gen_kr_table(krs, a)
    risk_cards_html = _gen_risk_cards_html(risk_cards[:6])
    dq_html = _gen_dq_html(dq_items)
    history_html = _gen_history_section()

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
<title>全国网点OKR推进周报{title_week} - {today_str}</title>
<style>
{CSS_TEXT}
</style>
</head>
<body>
<div class="container">

<div class="header">
  <div class="header-logo">
    <img src="assets/hmg-logo.png" alt="HMG 30周年">
  </div>
  <div class="header-brand">
    <div class="header-icon">OKR</div>
    <div class="header-titles">
      <h1>全国网点OKR推进周报{week_badge}</h1>
      <div class="header-subtitle">集团四大战略目标执行进度 · 网点协同推进情况</div>
    </div>
  </div>
  <div class="meta">
    <span>日期：{today_str}</span>
    <span>分析时间：{analysis_time}（北京时间）</span>
    <span>数据来源：协同待办事项表 · 网点OKR推进</span>
    <span class="meta-tag">云端自动生成</span>
  </div>
</div>

<nav class="topnav">
  <a href="#sec-summary" class="nav-highlight">本周概况</a>
  <a href="#sec-ai" class="nav-highlight">智能洞察</a>
  <a href="#sec-metrics" class="nav-highlight">指标</a>
  <a href="#sec-ochart" class="nav-highlight">进度可视化</a>
  <a href="#sec-risk" class="nav-highlight">重点关注</a>
  <a href="#sec-detail" class="nav-highlight">KR明细</a>
  <a href="#sec-dq" class="nav-highlight">数据质量</a>
  <a href="#sec-history" class="nav-highlight">历史</a>
</nav>

<div class="exec-summary" id="sec-summary">
  <h2>本周概况</h2>
  <ul>
    <li class="info">整体平均进度 <strong>{a["overall_avg"]}%</strong>，共 <strong>{len(krs)}项KR</strong>，其中 <strong>{updated_count_display}项</strong>本周有进展更新，<strong>{a["done_count"]}项</strong>已达成{'' if not comparison else '（较上周' + comparison['prev_week'] + '新增' + str(len(comparison['truly_updated'])) + '项）'}</li>
    <li class="{'warn' if a["overdue_count"] > 0 else ''}">{'<strong>' + str(a["overdue_count"]) + '项KR已超过目标日期但未完成</strong>，需了解推进计划' if a["overdue_count"] > 0 else '无超过目标日期未完成KR'}</li>
    <li class="{'warn' if a["stale_count"] > 0 else ''}">{'<strong>' + str(a["stale_count"]) + '项KR本周停滞</strong>（无进展描述或进度为0）' if a["stale_count"] > 0 else '全部KR本周均有进展'}</li>
  </ul>
  <div class="preview-hint">点击下方指标卡可展开对应KR明细 · 点击KR行可查看进展描述全文</div>
  <div class="snapshot-note">本报告数据对应分析时间（{analysis_time}）的表格快照，如后续表格有更新，数字以最新一期周报为准。</div>
</div>

{ai_html}

<div class="metrics" id="sec-metrics">
  <div class="metric-card dark clickable" data-metric="avg" onclick="toggleMetricDetail('avg')">
    <div class="num">{a["overall_avg"]}%</div><div class="label">平均进度</div>
  </div>
  <div class="metric-card green clickable" data-metric="updated" onclick="toggleMetricDetail('updated')">
    <div class="num">{updated_count_display}</div><div class="label">本周有进展</div>
  </div>
  <div class="metric-card green clickable" data-metric="done" onclick="toggleMetricDetail('done')">
    <div class="num">{a["done_count"]}</div><div class="label">已达成</div>
  </div>
  <div class="metric-card red clickable" data-metric="overdue" onclick="toggleMetricDetail('overdue')">
    <div class="num">{a["overdue_count"]}</div><div class="label">超过目标日期</div>
  </div>
  <div class="metric-card red clickable" data-metric="stale" onclick="toggleMetricDetail('stale')">
    <div class="num">{a["stale_count"]}</div><div class="label">停滞项</div>
  </div>
  <div class="metric-card gray clickable" data-metric="untracked" onclick="toggleMetricDetail('untracked')">
    <div class="num">{a["untracked_count"]}</div><div class="label">未追踪</div>
  </div>
</div>

<div class="metric-detail-panel" id="mdp-avg">{metric_panels["avg"]}</div>
<div class="metric-detail-panel" id="mdp-updated">{metric_panels["updated"]}</div>
<div class="metric-detail-panel" id="mdp-done">{metric_panels["done"]}</div>
<div class="metric-detail-panel" id="mdp-overdue">{metric_panels["overdue"]}</div>
<div class="metric-detail-panel" id="mdp-stale">{metric_panels["stale"]}</div>
<div class="metric-detail-panel" id="mdp-untracked">{metric_panels["untracked"]}</div>

<div class="section" id="sec-ochart">
  <h2>进度可视化</h2>
  <div class="chart-grid">
    <div class="chart-box">
      <div class="chart-title">KR进度分布</div>
      <div class="dist-chart">{dist_chart_html}</div>
    </div>
    <div class="chart-box">
      <div class="chart-title">状态分布</div>
      {status_donut_html}
    </div>
  </div>
  <div class="chart-box full-width">
    <div class="chart-title">各目标进度{'' if not comparison else '（本周 vs 上周' + comparison['prev_week'] + '，浅条为上周）'}</div>
    <div class="o-bar-chart">{o_bar_chart_html}</div>
  </div>
</div>

<div class="section" id="sec-risk">
  <h2>重点关注事项</h2>
  <div class="risk-grid" id="riskGrid">{risk_cards_html}</div>
</div>

<div class="section" id="sec-detail">
  <h2>关键结果明细 <small style="font-weight:400;color:#8898aa;font-size:12px;margin-left:8px">备查 · 点击行展开/收起关键结果描述</small></h2>
  <div class="filters" id="filters">
    <button class="filter-btn active" data-filter="all">全部 <span class="count" id="c-all">({c_all})</span></button>
    <button class="filter-btn" data-filter="risk">需关注项 <span class="count" id="c-risk">({c_risk})</span></button>
    <button class="filter-btn" data-filter="overdue">超过目标日期 <span class="count" id="c-overdue">({c_overdue})</span></button>
    <button class="filter-btn" data-filter="done">已达成 <span class="count" id="c-done">({c_done})</span></button>
    <button class="filter-btn" data-filter="untracked">未追踪 <span class="count" id="c-untracked">({c_untracked})</span></button>
    <button class="filter-btn toggle-all-btn" id="toggleAllBtn" onclick="toggleAllDetails()">展开全部描述</button>
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
          <th onclick="sortTable(6)">关注项/风险</th>
        </tr>
      </thead>
      <tbody id="krBody">{kr_table_html}</tbody>
    </table>
  </div>
</div>

<div class="section" id="sec-dq">
  <h2>数据质量提醒</h2>
  <ul class="dq-list" id="dqList">{dq_html}</ul>
</div>

<div class="section" id="sec-history">
  <h2>历史周报</h2>
  <p style="font-size:13px;color:#8898aa;margin:-6px 0 14px">往期报告存档，点击查看完整内容</p>
  {history_html}
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

def _gen_history_section():
    """扫描 docs/archive/ 目录，生成历史周报链接板块（内嵌于报告末尾）。按ISO周去重，每周只显示最新一期。"""
    archive_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'docs', 'archive')
    files = []
    if os.path.isdir(archive_dir):
        for name in os.listdir(archive_dir):
            if name.startswith('okr-report-') and name.endswith('.html') and name != 'index.html':
                d = name.replace('okr-report-', '').replace('.html', '')
                try:
                    dt = datetime.strptime(d, '%Y-%m-%d')
                    week = get_iso_week(dt.date())
                except Exception:
                    week = ''
                files.append((d, name, week))
    files.sort(key=lambda x: x[0], reverse=True)  # 最新在前

    if not files:
        return '<div class="empty-state">暂无历史周报（本周为首次生成，下周起自动累积）</div>'

    # 按ISO周去重：每周只保留最新一期
    seen_weeks = set()
    unique_files = []
    for d, name, week in files:
        if week and week in seen_weeks:
            continue
        if week:
            seen_weeks.add(week)
        unique_files.append((d, name, week))

    cards = []
    for d, name, week in unique_files[:12]:  # 最多显示最近12周
        week_badge = f'<div class="history-week">{week}</div>' if week else ''
        cards.append(f'<a class="history-card" href="archive/{name}">{week_badge}<div class="history-date">{d}</div></a>')
    cards_html = ''.join(cards)

    more = ''
    if len(unique_files) > 12:
        more = f'<div style="margin-top:12px;font-size:12px;color:#8898aa">仅显示最近 12 周，共 {len(unique_files)} 周</div>'
    all_link = '<a class="history-all" href="archive/index.html">查看全部历史周报 →</a>'

    return f'<div class="history-grid">{cards_html}</div>{more}{all_link}'


def _gen_archive_index(archive_dir):
    """生成归档索引页，按ISO周去重列出历史报告（每周只显示最新一期）"""
    files = []
    for name in os.listdir(archive_dir):
        if name.startswith('okr-report-') and name.endswith('.html') and name != 'index.html':
            d = name.replace('okr-report-', '').replace('.html', '')
            try:
                dt = datetime.strptime(d, '%Y-%m-%d')
                week = get_iso_week(dt.date())
            except Exception:
                week = ''
            files.append((d, name, week))
    files.sort(key=lambda x: x[0], reverse=True)  # 最新在前

    # 按ISO周去重：每周只保留最新一期
    seen_weeks = set()
    unique_files = []
    for d, name, week in files:
        if week and week in seen_weeks:
            continue
        if week:
            seen_weeks.add(week)
        unique_files.append((d, name, week))

    items = []
    for d, name, week in unique_files:
        week_badge = f'<span class="week-badge">{week}</span>' if week else ''
        items.append(f'    <li>{week_badge}<a href="./{name}">{d}</a></li>')

    index_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OKR周报历史归档</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; background: #f5f6fa; color: #1a1a2e; max-width: 720px; margin: 40px auto; padding: 0 20px; }}
  h1 {{ font-size: 22px; color: #0f3460; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ padding: 14px 18px; background: #fff; margin-bottom: 10px; border-radius: 8px; border-left: 4px solid #3498db; display: flex; align-items: center; gap: 12px; }}
  .week-badge {{ display: inline-block; background: #1a1a2e; color: #fff; font-size: 14px; font-weight: 700; padding: 4px 10px; border-radius: 6px; min-width: 44px; text-align: center; }}
  a {{ color: #1a5f9e; text-decoration: none; font-weight: 600; font-size: 15px; }}
  a:hover {{ text-decoration: underline; }}
  .back {{ margin-top: 24px; }}
  .back a {{ color: #3498db; }}
</style>
</head>
<body>
  <h1>全国网点OKR推进周报 · 历史归档</h1>
  <p>共 {len(unique_files)} 周报告</p>
  <ul>
{chr(10).join(items)}
  </ul>
  <div class="back"><a href="../index.html">← 返回最新周报</a></div>
</body>
</html>"""
    with open(os.path.join(archive_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(index_html)


def protect_html(html_content, password):
    """用密码加密HTML内容，生成需要密码才能查看的页面。

    加密：PBKDF2(10000轮)派生32字节密钥 → SHA-256计数器流密码 → XOR
    解密：浏览器 Web Crypto API（原生，无需第三方JS库）
    验证：密文前加 <!--OKR--> 标记，解密后检查标记判断密码是否正确
    """
    import hashlib
    import base64
    import secrets

    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 10000, dklen=32)

    # 加验证标记
    marked_content = '<!--OKR-->' + html_content
    content_bytes = marked_content.encode('utf-8')

    # SHA-256计数器模式流密码
    encrypted = bytearray(len(content_bytes))
    counter = 0
    pos = 0
    while pos < len(content_bytes):
        block = hashlib.sha256(key + counter.to_bytes(8, 'big')).digest()
        end = min(pos + 32, len(content_bytes))
        for j in range(end - pos):
            encrypted[pos + j] = content_bytes[pos + j] ^ block[j]
        pos += 32
        counter += 1

    enc_b64 = base64.b64encode(bytes(encrypted)).decode()
    salt_b64 = base64.b64encode(salt).decode()

    return '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OKR\u5468\u62a5\u7cfb\u7edf</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',sans-serif;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);display:flex;justify-content:center;align-items:center;min-height:100vh}
.lock-card{background:#fff;border-radius:16px;padding:48px 40px;box-shadow:0 20px 60px rgba(0,0,0,.15);width:380px;text-align:center}
.lock-icon{font-size:56px;margin-bottom:20px;line-height:1}
.lock-title{font-size:20px;font-weight:600;color:#1a1a1a;margin-bottom:6px}
.lock-subtitle{font-size:14px;color:#999;margin-bottom:28px}
.lock-input{width:100%;padding:14px 18px;border:2px solid #e8e8e8;border-radius:10px;font-size:16px;outline:none;transition:border-color .2s}
.lock-input:focus{border-color:#667eea}
.lock-btn{width:100%;margin-top:16px;padding:14px;background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;border:none;border-radius:10px;font-size:16px;font-weight:500;cursor:pointer;transition:opacity .2s}
.lock-btn:hover{opacity:.9}
.lock-btn:disabled{opacity:.5;cursor:not-allowed}
.lock-error{color:#ff4d4f;font-size:14px;margin-top:14px;display:none}
.lock-hint{margin-top:20px;font-size:12px;color:#bbb}
</style>
</head>
<body>
<div class="lock-card">
  <div class="lock-icon">\U0001f512</div>
  <div class="lock-title">\u5168\u56fd\u7f51\u70b9OKR\u63a8\u8fdb\u5468\u62a5</div>
  <div class="lock-subtitle">\u8bf7\u8f93\u5165\u8bbf\u95ee\u5bc6\u7801</div>
  <input type="password" class="lock-input" id="pwd" placeholder="\u8bbf\u95ee\u5bc6\u7801" autofocus>
  <button class="lock-btn" id="btn" onclick="decrypt()">\u67e5\u770b\u5468\u62a5</button>
  <div class="lock-error" id="err">\u5bc6\u7801\u9519\u8bef\uff0c\u8bf7\u91cd\u8bd5</div>
  <div class="lock-hint">\u5185\u90e8\u8d44\u6599 \u00b7 \u4ec5\u9650\u6388\u6743\u4eba\u5458\u67e5\u770b</div>
</div>
<script>
const SALT="__SALT__";
const DATA="__DATA__";
async function decrypt(){
  var btn=document.getElementById('btn'),pwd=document.getElementById('pwd'),
      err=document.getElementById('err');
  err.style.display='none';btn.disabled=true;btn.textContent='\u89e3\u5bc6\u4e2d...';
  try{
    var password=pwd.value;
    var saltBytes=Uint8Array.from(atob(SALT),function(c){return c.charCodeAt(0)});
    var enc=Uint8Array.from(atob(DATA),function(c){return c.charCodeAt(0)});
    var km=await crypto.subtle.importKey('raw',new TextEncoder().encode(password),'PBKDF2',false,['deriveBits']);
    var key=new Uint8Array(await crypto.subtle.deriveBits({name:'PBKDF2',salt:saltBytes,iterations:10000,hash:'SHA-256'},km,256));
    var n=Math.ceil(enc.length/32),ps=[];
    for(var i=0;i<n;i++){
      var cb=new Uint8Array(8);new DataView(cb.buffer).setBigInt64(0,BigInt(i));
      var d=new Uint8Array(key.length+cb.length);d.set(key,0);d.set(cb,key.length);
      ps.push(crypto.subtle.digest('SHA-256',d));
    }
    var hs=await Promise.all(ps);
    var dec=new Uint8Array(enc.length);
    for(var i=0;i<n;i++){
      var b=new Uint8Array(hs[i]);
      for(var j=0;j<32&&i*32+j<enc.length;j++)dec[i*32+j]=enc[i*32+j]^b[j];
    }
    var html=new TextDecoder().decode(dec);
    if(html.indexOf('<!--OKR-->')!==0)throw new Error('bad password');
    var actual=html.substring(10);
    document.open();document.write(actual);document.close();
  }catch(e){
    err.style.display='block';btn.disabled=false;btn.textContent='\u67e5\u770b\u5468\u62a5';
  }
}
document.getElementById('pwd').addEventListener('keypress',function(e){if(e.key==='Enter')decrypt();});
</script>
</body>
</html>'''.replace('__SALT__', salt_b64).replace('__DATA__', enc_b64)


def main():
    skip_send = '--skip-send' in sys.argv
    send_only = '--send-only' in sys.argv
    allow_no_url = '--allow-no-url' in sys.argv
    today = date.today()
    today_str = today.isoformat()
    week_str = get_iso_week(today)
    mode_label = ''
    if skip_send:
        mode_label = '（仅生成，不发送）'
    elif send_only:
        mode_label = '（仅发送，不生成）'
    print(f'[{datetime.now().isoformat()}] OKR周报云端脚本启动{mode_label}')
    print(f'   本周：{week_str}')

    # ===== 阶段A：拉取数据并生成报告（--send-only 时跳过） =====
    if not send_only:
        # 1. 获取token
        print('1. 获取access token...')
        token = get_access_token()
        if not token:
            print(f'   错误：获取token失败（钉钉API不可用或凭证错误），脚本终止')
            return
        print(f'   token获取成功')

        # 2. 获取unionId
        print('2. 获取unionId...')
        union_id = get_union_id(token, USER_ID)
        if not union_id:
            msg = f'## OKR周报云端脚本错误\n\n获取unionId失败，请确保应用已开通 **qyapi_get_member** 权限。\n\n申请链接：https://open-dev.dingtalk.com/appscope/apply?content={APP_KEY}%23qyapi_get_member'
            send_robot_message(token, 'OKR周报云端脚本错误', msg)
            print(f'   错误：获取unionId失败')
            return
        print(f'   unionId获取成功')

        # 3. 拉取AI表格数据
        print('3. 拉取AI表格数据...')
        records = list_records(token, union_id)
        if records is None:
            msg = f'## OKR周报云端脚本错误\n\n拉取AI表格数据失败，请确保应用已开通 **Notable.Base.Read.All** 权限。\n\n申请链接：https://open-dev.dingtalk.com/appscope/apply?content={APP_KEY}%23Notable.Base.Read.All'
            send_robot_message(token, 'OKR周报云端脚本错误', msg)
            print(f'   错误：拉取AI表格数据失败')
            return
        print(f'   获取到{len(records)}条记录')

        # 4. 解析和分析
        print('4. 解析和分析数据...')
        krs = parse_records(records)
        a = analyze(krs, today)
        print(f'   有效KR: {len(krs)}, 平均进度: {a["overall_avg"]}%, 本周有进展: {a["updated_count"]}, 停滞: {a["stale_count"]}, 超目标日期: {a["overdue_count"]}')

        # 4.5 读取上周快照并计算环比
        print('4.5. 计算周环比...')
        prev_snapshot = load_previous_snapshot(today_str)
        comparison = compare_with_previous(a, prev_snapshot)
        if comparison:
            print(f'   对比上周 {comparison["prev_week"]}({comparison["prev_date"]}): 平均进度Δ{comparison["overall_avg_delta"]:+d}%，{len(comparison["truly_updated"])}项新进展，{len(comparison["progress_gained"])}项进度提升')
        else:
            print('   无历史快照，本周为首周基准')

        # 5. 生成HTML报告
        # 5.0 生成 AI 洞察（可选：配置 DASHSCOPE_API_KEY 后生效）
        ai_insight = None
        if LLM_API_KEY:
            print('5.0 生成AI洞察...')
            data_text = build_data_summary_text(a, comparison)
            ai_insight = call_llm_insight(LLM_API_KEY, LLM_MODEL, data_text)
            if ai_insight:
                print('   AI洞察生成成功')
            else:
                print('   AI洞察未生成（接口异常，使用规则报告，不影响主流程）')
        else:
            print('5.0 跳过AI洞察（未配置 DASHSCOPE_API_KEY）')

        print('5. 生成HTML报告...')
        html = generate_html(a, today_str, week_str, comparison=comparison, ai_insight=ai_insight)
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'okr-report.html')
        # 可选：密码保护（设置 REPORT_PASSWORD 环境变量后启用）
        if REPORT_PASSWORD:
            print('   应用密码保护（REPORT_PASSWORD已设置）...')
            final_html = protect_html(html, REPORT_PASSWORD)
        else:
            final_html = html
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(final_html)
        print(f'   HTML已保存: {html_path} ({len(final_html)} chars)')

        # 5.1 保存带日期的归档副本 + 生成归档索引（用于历史回看）
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            repo_root = os.path.dirname(script_dir)  # cloud/ -> 仓库根目录
            archive_dir = os.path.join(repo_root, 'docs', 'archive')
            os.makedirs(archive_dir, exist_ok=True)
            archive_path = os.path.join(archive_dir, f'okr-report-{today_str}.html')
            # 归档文件位于 docs/archive/ 子目录，资源相对路径需指向上一级 docs/
            archive_html = html.replace('src="assets/hmg-logo.png"', 'src="../assets/hmg-logo.png"')
            if REPORT_PASSWORD:
                archive_html = protect_html(archive_html, REPORT_PASSWORD)
            with open(archive_path, 'w', encoding='utf-8') as f:
                f.write(archive_html)
            print(f'   归档HTML已保存: {archive_path}')
            _gen_archive_index(archive_dir)
            print(f'   归档索引已更新')
        except Exception as e:
            print(f'   警告：归档保存失败（不影响主流程）: {e}', file=sys.stderr)

        # 5.5 保存本次快照（用于下周环比）
        print('5.5. 保存数据快照...')
        snapshot_path = save_snapshot(a, today_str, week_str)
        print(f'   快照已保存: {snapshot_path}')

        if skip_send:
            print(f'[{datetime.now().isoformat()}] 完成（--skip-send模式，已生成报告和快照，未发送消息）')
            return

    # ===== 阶段B：发送钉钉通知（--skip-send 时跳过） =====
    if not skip_send:
        # URL检查：无URL且未显式允许，则拒绝发送（避免无链接推送）
        if not REPORT_URL and not allow_no_url:
            print(f'   错误：REPORT_URL 未设置，拒绝发送（避免推送无链接消息）')
            print(f'   正确流程：')
            print(f'     1) python okr_cloud_report.py --skip-send   # 只生成HTML')
            print(f'     2) 部署HTML到网络托管（CloudStudio/GitHub Pages）获取URL')
            print(f'     3) REPORT_URL=<URL> python cloud/okr_cloud_report.py --send-only   # 仅发送消息')
            print(f'   如确实需要无链接推送（不推荐），加 --allow-no-url')
            sys.exit(2)

        # 6. 生成钉钉通知（极简版：标题 + 链接，详情见网页报告）
        print('6. 生成钉钉通知（极简）...')
        md = build_notify_md(today_str, REPORT_URL, week_str, password=REPORT_PASSWORD)
        print(f'   报告URL: {REPORT_URL}')

        # 7. 获取token并发送钉钉消息（个人单聊 + 群）
        print('7. 获取access token...')
        token = get_access_token()
        if not token:
            print(f'   错误：获取token失败，无法发送钉钉消息')
            return
        print('   token获取成功')

        print('8. 发送钉钉消息...')
        msg_title = f'全国网点OKR推进周报 {week_str}（{today_str}）'

        # 8.1 个人单聊推送
        print('   8.1 发送给个人（单聊）...')
        result = send_robot_message(token, msg_title, md)
        if result.get('processQueryKey'):
            print(f'   个人发送成功！标题：{msg_title}')
        else:
            print(f'   个人发送失败: {json.dumps(result, ensure_ascii=False)}')

        # 8.2 群推送（配置了 webhook 的群都推送）
        print('   8.2 推送到群...')
        group_webhooks = [
            ('DINGTALK_GROUP_WEBHOOK', GROUP_WEBHOOK),
            ('DINGTALK_GROUP_WEBHOOK_SITE_DIGITAL', GROUP_WEBHOOK_SITE_DIGITAL),
            ('DINGTALK_GROUP_WEBHOOK_MANAGER', GROUP_WEBHOOK_MANAGER),
        ]
        for gname, gwh in group_webhooks:
            if not gwh:
                print(f'   群推送跳过（未配置 {gname}）')
                continue
            gresult = send_group_message(gwh, msg_title, md)
            if gresult is None:
                print(f'   群推送请求异常（{gname}）')
            elif gresult.get('errcode') == 0:
                print(f'   群发送成功！（{gname}）标题：{msg_title}')
            else:
                print(f'   群发送失败（{gname}）: {json.dumps(gresult, ensure_ascii=False)}')

    print(f'[{datetime.now().isoformat()}] 完成')

if __name__ == '__main__':
    main()
