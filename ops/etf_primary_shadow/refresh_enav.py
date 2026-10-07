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
MONITOR = BASE / 'web' / 'data' / 'monitor_view.json'
OUT = AUX / 'enav_estimate.json'
LOF = Path('/home/admin/projects/lof-opportunity-runtime/current')
sys.path.insert(0, str(LOF))

from runtime.lof.us_history import fetch_tencent_us_daily
from runtime.lof.hk_history import fetch_tencent_hk_daily
from runtime.lof.fx import fetch_tencent_fx_daily, fetch_tencent_fx_quote

TZ = ZoneInfo('Asia/Shanghai')


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
        current = dec(f[3])
        change = dec(f[4])
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


def resolve_model(meta):
    """Return (kind, proxy, overlay, quality, method) or None.

    A = direct benchmark index.
    B = close, transparent market/index ETF proxy or a same-theme broad index.
    Anything requiring a weak sector guess, mixed A/H weights, commodities, or
    active judgment deliberately falls back to formal NAV instead of emitting a
    false-precision eNAV.
    """
    if not isinstance(meta, dict):
        return None
    if meta.get('asset_class') != 'EQUITY':
        return None
    if meta.get('strategy_style') not in (None, '', 'INDEX'):
        return None
    if meta.get('region_scope') == 'MIXED':
        return None

    idx = str(meta.get('tracking_index') or '').strip()
    name = str(meta.get('name') or '')

    if idx in {'NDX', '纳斯达克100指数'}:
        return ('US', 'usNDX', None, 'A', '纳斯达克100直接指数')
    if idx == '纳斯达克生物科技指数':
        return ('US', 'usNBI', None, 'A', '纳斯达克生物科技直接指数')
    if idx in {'HSTECH', '恒生科技指数'}:
        return ('HK', 'hkHSTECH', None, 'A', '恒生科技直接指数')
    if idx in {'HSI', '恒生指数'}:
        return ('HK', 'hkHSI', None, 'A', '恒生指数直接指数')
    if idx in {'HSCEI', '恒生中国企业指数'}:
        return ('HK', 'hkHSCEI', None, 'A', '恒生中国企业直接指数')
    if idx == 'H11153':
        return ('HK', 'hkH11153', None, 'A', 'H11153直接指数')

    if idx in {'N225', '日经225指数'}:
        return ('JP', 'EWJ.AM', 'gzN225', 'B', '日经225；EWJ历史桥+日经当日叠加')
    if idx == '东证指数':
        return ('JP', 'EWJ.AM', 'gzTPX', 'B', 'TOPIX；EWJ历史桥+TOPIX当日叠加')

    if idx in {'SPXNTR', '标准普尔500指数', '标普500指数'}:
        return ('US', 'SPY.AM', None, 'B', 'SPY标普500代理')
    if idx == 'SPSIBI':
        return ('US', 'XBI.AM', None, 'B', 'XBI生物科技代理')
    if idx in {'SPSIOP', '标普石油天然气勘探及生产精选行业指数'}:
        return ('US', 'XOP.AM', None, 'B', 'XOP油气代理')
    if idx == 'NDXTMC':
        return ('US', 'XLK.AM', None, 'B', 'XLK科技代理')
    if idx in {'DAX', '德国DAX指数'}:
        return ('US', 'EWG.AM', None, 'B', 'EWG德国股票代理')
    if idx == 'CAC40指数':
        return ('US', 'EWQ.AM', None, 'B', 'EWQ法国股票代理')
    if '巴西IBOVESPA' in idx:
        return ('US', 'EWZ.AM', None, 'B', 'EWZ巴西股票代理')
    if idx in {'FSSAULM', '富时沙特阿拉伯指数'}:
        return ('US', 'KSA.AM', None, 'B', 'KSA沙特股票代理')
    if idx in {'MSCI美国50指数', '750108'}:
        return ('US', 'OEF.AM', None, 'B', 'OEF美国大盘股代理')
    if '道琼斯工业平均' in idx:
        return ('US', 'DIA.AM', None, 'B', 'DIA道琼斯代理')
    if idx == 'SP5CSSUP':
        return ('US', 'XLY.AM', None, 'B', 'XLY美国可选消费代理')

    tech_terms = ('科技', '互联网', '信息技术', '新经济')
    hk_context = any(t in idx or t in name for t in ('港股', '香港', '恒生')) or idx.startswith('HS')
    sector_exclude = ('生物', '医疗', '医药', '创新药', '消费', '汽车', '红利', '金融')
    if (
        meta.get('region_scope') == 'CROSS_BORDER'
        and hk_context
        and any(t in idx or t in name for t in tech_terms)
        and not any(t in idx or t in name for t in sector_exclude)
    ):
        return ('HK', 'hkHSTECH', None, 'B', '恒生科技指数同主题代理')

    return None


def load_previous():
    if not OUT.exists():
        return {'schema_version': 2, 'rows': []}
    try:
        x = json.loads(OUT.read_text(encoding='utf-8'))
        return x if isinstance(x, dict) else {'schema_version': 2, 'rows': []}
    except Exception:
        return {'schema_version': 2, 'rows': []}


def main():
    now = datetime.now(TZ)
    today = now.date()
    if not PREMIUM.exists():
        raise SystemExit('reference premium cache missing')
    if not MONITOR.exists():
        raise SystemExit('monitor view cache missing')

    premium = json.loads(PREMIUM.read_text(encoding='utf-8'))
    anchors = {str(r.get('code')): r for r in premium.get('rows') or [] if isinstance(r, dict)}
    monitor = json.loads(MONITOR.read_text(encoding='utf-8'))
    metadata = {str(r.get('code')): r for r in monitor.get('rows') or [] if isinstance(r, dict)}

    models = {}
    fallback_reasons = {}
    for code, a in anchors.items():
        meta = metadata.get(code) or {}
        model = resolve_model(meta)
        if model is None:
            if meta.get('region_scope') == 'MIXED':
                fallback_reasons[code] = 'MIXED_MARKET_NO_SINGLE_PROXY'
            elif meta.get('asset_class') != 'EQUITY' or meta.get('strategy_style') not in (None, '', 'INDEX'):
                fallback_reasons[code] = 'NON_INDEX_OR_NON_EQUITY'
            else:
                fallback_reasons[code] = 'NO_TRUSTED_PROXY_YET'
        else:
            models[code] = model

    previous = load_previous()
    old = {str(r.get('code')): r for r in previous.get('rows') or [] if isinstance(r, dict)}

    fx_rows = fetch_tencent_fx_daily('whHKDCNY', count=60, timeout=7)
    fx_quote = fetch_tencent_fx_quote('whHKDCNY', timeout=7)
    if fx_quote.error or fx_quote.current is None:
        raise RuntimeError('HKDCNY proxy unavailable')

    us_cache = {}
    hk_cache = {}
    rows = []
    errors = []
    for code, model in models.items():
        kind, proxy, overlay_symbol, quality, method = model
        a = anchors.get(code)
        try:
            nav = dec(a['formal_nav'])
            nav_text = str(a['nav_date'])
            nav_date = date.fromisoformat(nav_text[:4] + '-' + nav_text[4:6] + '-' + nav_text[6:8])
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
            elif kind == 'HK':
                if proxy not in hk_cache:
                    hk_cache[proxy] = fetch_tencent_hk_daily(symbol=proxy, count=60, timeout=8)
                hist = hk_cache[proxy]
            else:
                raise RuntimeError('UNSUPPORTED_KIND')

            anchor = close_at_or_before(hist, nav_date)
            latest = latest_completed(hist, today)
            if not anchor or not latest:
                raise RuntimeError('MISSING_PROXY_HISTORY')
            proxy_ratio = latest.close / anchor.close
            proxy_latest_date = str(latest.date).replace('-', '')
            proxy_anchor_date = str(anchor.date).replace('-', '')

            overlay = global_session_overlay(overlay_symbol, today) if overlay_symbol else None
            overlay_ratio = dec(overlay['ratio']) if overlay else Decimal('1')
            enav = nav * proxy_ratio * fx_ratio * overlay_ratio
            premium_pct = (close / enav - Decimal('1')) * Decimal('100')
            rows.append({
                'code': code,
                'name': a.get('name'),
                'tracking_index': (metadata.get(code) or {}).get('tracking_index'),
                'formal_nav': float(nav),
                'formal_nav_date': nav_text,
                'etf_close': float(close),
                'etf_close_date': close_date,
                'estimated_nav': round(float(enav), 6),
                'estimated_premium_pct': round(float(premium_pct), 4),
                'quality': quality,
                'method': method,
                'proxy_symbol': proxy,
                'proxy_anchor_date': proxy_anchor_date,
                'proxy_latest_date': proxy_latest_date,
                'proxy_return_pct': round(float((proxy_ratio - 1) * 100), 4),
                'fx_proxy': 'HKDCNY_AS_USDCNY_RELATIVE_PROXY',
                'fx_anchor_date': str(fx_anchor_row.date).replace('-', ''),
                'fx_return_pct': round(float((fx_ratio - 1) * 100), 4),
                'session_overlay': overlay,
                'estimate_time': now.isoformat(),
            })
        except Exception as exc:
            errors.append({'code': code, 'error': type(exc).__name__ + ':' + str(exc)})
            if code in old:
                rows.append(old[code])

    quality_counts = {}
    for row in rows:
        quality_counts[row['quality']] = quality_counts.get(row['quality'], 0) + 1
    fallback_counts = {}
    for reason in fallback_reasons.values():
        fallback_counts[reason] = fallback_counts.get(reason, 0) + 1

    payload = {
        'schema_version': 2,
        'generated_at': now.isoformat(),
        'method': 'formal NAV anchor advanced by completed direct-index/proxy return + FX proxy; Japan adds current-session index overlay',
        'scope': 'cross-border/QDII ETFs with trusted A/B benchmark mapping; others fall back to formal NAV',
        'anchor_count': len(anchors),
        'target_count': len(models),
        'row_count': len(rows),
        'quality_counts': quality_counts,
        'formal_fallback_count': len(fallback_reasons),
        'fallback_counts': fallback_counts,
        'error_count': len(errors),
        'errors': errors,
        'rows': sorted(rows, key=lambda r: r['code']),
    }
    AUX.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({
        'anchors': len(anchors), 'targets': len(models), 'rows': len(rows),
        'quality': quality_counts, 'formal_fallback': len(fallback_reasons),
        'errors': len(errors), 'generated_at': payload['generated_at'],
    }, ensure_ascii=False))

if __name__ == '__main__':
    main()
