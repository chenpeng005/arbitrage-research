#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path('/home/admin/projects/etf-primary-shadow')
AUX = BASE / 'aux_cache'
PREMIUM = AUX / 'reference_premium.json'
OUT = AUX / 'enav_estimate.json'
LOF = Path('/home/admin/projects/lof-opportunity-runtime/current')
sys.path.insert(0, str(LOF))

from runtime.lof.us_history import fetch_tencent_us_daily
from runtime.lof.hk_history import fetch_tencent_hk_daily
from runtime.lof.fx import fetch_tencent_fx_daily, fetch_tencent_fx_quote

TZ = ZoneInfo('Asia/Shanghai')

# Only the executable / capacity-constrained cross-border pool is modeled here.
# proxy_kind: US = U.S.-quoted index/ETF; HK = HK index; JP = U.S. Japan ETF + live Japan-session overlay.
MAP = {
    '513000': ('JP', 'EWJ.AM', 'gzN225', 'B', '日经225；EWJ历史桥+日经当日叠加'),
    '513100': ('US', 'usNDX', None, 'A', '纳斯达克100直接指数'),
    '513290': ('US', 'usNBI', None, 'A', '纳斯达克生物科技直接指数'),
    '513300': ('US', 'usNDX', None, 'A', '纳斯达克100直接指数'),
    '513350': ('US', 'XOP.AM', None, 'B', 'XOP同类指数ETF代理'),
    '513390': ('US', 'usNDX', None, 'A', '纳斯达克100直接指数'),
    '513500': ('US', 'SPY.AM', None, 'B', 'SPY标普500代理'),
    '513520': ('JP', 'EWJ.AM', 'gzN225', 'B', '日经225；EWJ历史桥+日经当日叠加'),
    '513650': ('US', 'SPY.AM', None, 'B', 'SPY标普500代理'),
    '513800': ('JP', 'EWJ.AM', 'gzTPX', 'B', 'TOPIX；EWJ历史桥+TOPIX当日叠加'),
    '513850': ('US', 'OEF.AM', None, 'B', 'OEF美国大盘股代理'),
    '513870': ('US', 'usNDX', None, 'A', '纳斯达克100直接指数'),
    '520870': ('US', 'EWZ.AM', None, 'B', 'EWZ巴西股票代理'),
    '159329': ('US', 'KSA.AM', None, 'B', 'KSA沙特股票代理'),
    '159501': ('US', 'usNDX', None, 'A', '纳斯达克100直接指数'),
    '159502': ('US', 'XBI.AM', None, 'B', 'XBI生物科技代理'),
    '159509': ('US', 'XLK.AM', None, 'B', 'XLK科技代理'),
    '159513': ('US', 'usNDX', None, 'A', '纳斯达克100直接指数'),
    '159518': ('US', 'XOP.AM', None, 'B', 'XOP油气代理'),
    '159561': ('US', 'EWG.AM', None, 'B', 'EWG德国股票代理'),
    '159612': ('US', 'SPY.AM', None, 'B', 'SPY代理SPX总回报指数'),
    '159632': ('US', 'usNDX', None, 'A', '纳斯达克100直接指数'),
    '159655': ('US', 'SPY.AM', None, 'B', 'SPY代理SPX总回报指数'),
    '159659': ('US', 'usNDX', None, 'A', '纳斯达克100直接指数'),
    '159696': ('US', 'usNDX', None, 'A', '纳斯达克100直接指数'),
    '159866': ('JP', 'EWJ.AM', 'gzN225', 'B', '日经225；EWJ历史桥+日经当日叠加'),
    '159941': ('US', 'usNDX', None, 'A', '纳斯达克100直接指数'),
    '159960': ('HK', 'hkHSCEI', None, 'A', '恒生中国企业直接指数'),
}


def dec(x):
    return Decimal(str(x))


def close_at_or_before(rows, target: date):
    eligible = [r for r in rows if r.date <= target]
    return max(eligible, key=lambda r: r.date) if eligible else None


def latest_completed(rows, today: date):
    eligible = [r for r in rows if r.date < today]
    return max(eligible, key=lambda r: r.date) if eligible else None


def global_session_overlay(symbol: str, today: date):
    req = urllib.request.Request(
        'https://qt.gtimg.cn/q=' + symbol,
        headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'},
    )
    with urllib.request.urlopen(req, timeout=6) as r:
        text = r.read().decode('gb18030', errors='replace')
    if '="' not in text or 'none_match' in text:
        return None
    body = text.split('="', 1)[1].split('"', 1)[0]
    f = body.split('~')
    if len(f) < 6:
        return None
    stamp = f[2].strip()
    try:
        qdate = datetime.strptime(stamp[:10], '%Y-%m-%d').date()
        current = dec(f[3]); change = dec(f[4])
    except Exception:
        return None
    previous = current - change
    if qdate != today or previous <= 0 or current <= 0:
        return None
    return {
        'ratio': float(current / previous),
        'current': float(current),
        'previous_close': float(previous),
        'quote_time': stamp,
        'symbol': symbol,
    }


def load_previous():
    if not OUT.exists():
        return {'schema_version': 1, 'rows': []}
    try:
        x = json.loads(OUT.read_text(encoding='utf-8'))
        return x if isinstance(x, dict) else {'schema_version': 1, 'rows': []}
    except Exception:
        return {'schema_version': 1, 'rows': []}


def main():
    now = datetime.now(TZ)
    today = now.date()
    if not PREMIUM.exists():
        raise SystemExit('reference premium cache missing')
    p = json.loads(PREMIUM.read_text(encoding='utf-8'))
    anchors = {str(r.get('code')): r for r in p.get('rows') or [] if isinstance(r, dict)}
    previous = load_previous()
    old = {str(r.get('code')): r for r in previous.get('rows') or [] if isinstance(r, dict)}

    # USD/CNY relative move proxy. HKD/CNY is live during the mainland holiday and
    # USD/HKD is tightly pegged; for a rough eNAV bridge this is more useful than a stale USDCNY feed.
    fx_rows = fetch_tencent_fx_daily('whHKDCNY', count=60, timeout=7)
    fx_quote = fetch_tencent_fx_quote('whHKDCNY', timeout=7)
    if fx_quote.error or fx_quote.current is None:
        raise RuntimeError('HKDCNY proxy unavailable')

    us_cache = {}
    hk_cache = {}
    rows = []
    errors = []
    for code, cfg in MAP.items():
        kind, proxy, overlay_symbol, quality, method = cfg
        a = anchors.get(code)
        if not a:
            errors.append({'code': code, 'error': 'MISSING_FORMAL_NAV_ANCHOR'})
            if code in old: rows.append(old[code])
            continue
        try:
            nav = dec(a['formal_nav'])
            nav_date = date.fromisoformat(str(a['nav_date'])[:4]+'-'+str(a['nav_date'])[4:6]+'-'+str(a['nav_date'])[6:8])
            close = dec(a['close'])
            close_date = str(a.get('close_date') or '')
            fx_anchor_row = close_at_or_before(fx_rows, nav_date)
            if not fx_anchor_row:
                raise RuntimeError('MISSING_FX_ANCHOR')
            fx_ratio = fx_quote.current / fx_anchor_row.close

            if kind in ('US', 'JP'):
                if proxy not in us_cache:
                    us_cache[proxy] = fetch_tencent_us_daily(symbol_with_exchange=proxy, count=60, timeout=8)
                hist = us_cache[proxy]
                anchor = close_at_or_before(hist, nav_date)
                latest = latest_completed(hist, today)
                if not anchor or not latest:
                    raise RuntimeError('MISSING_PROXY_HISTORY')
                proxy_ratio = latest.close / anchor.close
                proxy_latest_date = str(latest.date).replace('-', '')
                proxy_anchor_date = str(anchor.date).replace('-', '')
            elif kind == 'HK':
                if proxy not in hk_cache:
                    hk_cache[proxy] = fetch_tencent_hk_daily(symbol=proxy, count=60, timeout=8)
                hist = hk_cache[proxy]
                anchor = close_at_or_before(hist, nav_date)
                latest = latest_completed(hist, today)
                if not anchor or not latest:
                    raise RuntimeError('MISSING_PROXY_HISTORY')
                proxy_ratio = latest.close / anchor.close
                proxy_latest_date = str(latest.date).replace('-', '')
                proxy_anchor_date = str(anchor.date).replace('-', '')
            else:
                raise RuntimeError('UNSUPPORTED_KIND')

            overlay = global_session_overlay(overlay_symbol, today) if overlay_symbol else None
            overlay_ratio = dec(overlay['ratio']) if overlay else Decimal('1')
            enav = nav * proxy_ratio * fx_ratio * overlay_ratio
            premium = (close / enav - Decimal('1')) * Decimal('100')
            rows.append({
                'code': code,
                'name': a.get('name'),
                'formal_nav': float(nav),
                'formal_nav_date': str(a.get('nav_date') or ''),
                'etf_close': float(close),
                'etf_close_date': close_date,
                'estimated_nav': round(float(enav), 6),
                'estimated_premium_pct': round(float(premium), 4),
                'quality': quality,
                'method': method,
                'proxy_symbol': proxy,
                'proxy_anchor_date': proxy_anchor_date,
                'proxy_latest_date': proxy_latest_date,
                'proxy_return_pct': round(float((proxy_ratio-1)*100), 4),
                'fx_proxy': 'HKDCNY_AS_USDCNY_RELATIVE_PROXY',
                'fx_anchor_date': str(fx_anchor_row.date).replace('-', ''),
                'fx_return_pct': round(float((fx_ratio-1)*100), 4),
                'session_overlay': overlay,
                'estimate_time': now.isoformat(),
            })
        except Exception as exc:
            errors.append({'code': code, 'error': type(exc).__name__ + ':' + str(exc)})
            if code in old:
                rows.append(old[code])

    payload = {
        'schema_version': 1,
        'generated_at': now.isoformat(),
        'method': 'formal NAV anchor advanced by completed proxy return + FX proxy; Japan adds current-session index overlay',
        'scope': 'capacity-constrained cross-border ETF pool',
        'target_count': len(MAP),
        'row_count': len(rows),
        'error_count': len(errors),
        'errors': errors,
        'rows': sorted(rows, key=lambda r: r['code']),
    }
    AUX.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'target': len(MAP), 'rows': len(rows), 'errors': len(errors), 'generated_at': payload['generated_at']}, ensure_ascii=False))

if __name__ == '__main__':
    main()
