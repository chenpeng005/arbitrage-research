#!/usr/bin/env python3
import base64, gzip, json, os, pathlib, shutil, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = pathlib.Path('/home/admin/projects/etf-primary-shadow')
STATE = BASE / 'runtime_data'
WEB = BASE / 'web'
DATA = WEB / 'data'
PUBLIC = pathlib.Path('/var/www/opportunity-portal/etf-shadow')
PUBLIC_DATA = PUBLIC / 'data'
HISTORY_DATA = DATA / 'history'
PUBLIC_HISTORY = PUBLIC_DATA / 'history'
PUBLIC_SNAPSHOTS = PUBLIC_HISTORY / 'snapshots'
AUX_CACHE = BASE / 'aux_cache'
PREMIUM_CACHE = AUX_CACHE / 'reference_premium.json'
ENAV_CACHE = AUX_CACHE / 'enav_estimate.json'
API = 'https://api.github.com/repos/chenpeng005/arbitrage-research'


def get_json(url, timeout=60):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'ETF-Primary-Shadow-Web',
        'Accept': 'application/vnd.github+json',
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def fetch_relay_file(filename):
    meta = get_json(API + '/contents/etf_primary_relay/' + filename + '?ref=etf-primary-data-relay', 20)
    enc = meta.get('encoding')
    if enc == 'base64' and meta.get('content'):
        return base64.b64decode((meta.get('content') or '').replace('\n', ''))
    sha = meta.get('sha')
    if not sha:
        raise RuntimeError(filename + ' metadata has no sha')
    blob = get_json(API + '/git/blobs/' + sha, 30)
    if blob.get('encoding') != 'base64':
        raise RuntimeError(filename + ' blob is not base64')
    return base64.b64decode((blob.get('content') or '').replace('\n', ''))


def fetch_monitor_view():
    return fetch_relay_file('monitor_view.json')


def normalize_relay_comparison(payload):
    rows = []
    for item in payload.get('rows') or []:
        prev = item.get('previous') or {}
        cur = item.get('current') or {}
        rows.append({
            'exchange': item.get('exchange'),
            'code': item.get('code'),
            'previous_total_baskets': prev.get('total_baskets'),
            'current_total_baskets': cur.get('total_baskets'),
            'previous_account_baskets': prev.get('account_baskets'),
            'current_account_baskets': cur.get('account_baskets'),
            'previous_capacity_kind': prev.get('market_capacity_kind'),
            'current_capacity_kind': cur.get('market_capacity_kind'),
            'previous_market_capacity_status': prev.get('market_capacity_status'),
            'current_market_capacity_status': cur.get('market_capacity_status'),
            'previous_account_capacity_status': prev.get('account_capacity_status'),
            'current_account_capacity_status': cur.get('account_capacity_status'),
            'capacity_comparable': item.get('capacity_comparable', False),
            'basket_delta': item.get('basket_delta'),
            'basket_ratio': item.get('basket_ratio'),
            'account_basket_delta': item.get('account_basket_delta'),
            'creation_changed': item.get('creation_changed', False),
            'capacity_rule_changed': item.get('capacity_rule_changed', False),
            'account_rule_changed': item.get('account_rule_changed', False),
            'capacity_status_comparable': item.get('capacity_status_comparable', False),
            'account_capacity_status_comparable': item.get('account_capacity_status_comparable', False),
            'capacity_status_changed': item.get('capacity_status_changed', False),
            'account_capacity_status_changed': item.get('account_capacity_status_changed', False),
            'changed': item.get('changed', False),
        })
    return {
        'schema_version': payload.get('schema_version', 1),
        'previous_trade_date': payload.get('previous_trade_date'),
        'current_trade_date': payload.get('current_trade_date'),
        'row_count': len(rows),
        'changed_count': sum(1 for r in rows if r['changed']),
        'rows': rows,
    }


def fetch_comparison_view():
    return normalize_relay_comparison(json.loads(fetch_relay_file('comparison.json')))


def is_focus_row(row):
    return bool(row.get('qdii_flag')) or row.get('region_scope') in ('CROSS_BORDER', 'MIXED')


def reference_premium_targets(monitor, comparison):
    changed = {
        (str(r.get('exchange') or ''), str(r.get('code') or ''))
        for r in (comparison.get('rows') or [])
        if r.get('changed')
    }
    targets = []
    for row in monitor.get('rows') or []:
        if not isinstance(row, dict) or not is_focus_row(row):
            continue
        # Every cross-border/QDII row gets a low-frequency formal-NAV fallback.
        # eNAV is layered on top only where the benchmark/proxy model is trusted.
        targets.append(row)
    targets.sort(key=lambda r: (
        0 if (str(r.get('exchange') or ''), str(r.get('code') or '')) in changed else 1,
        -(int(r.get('total_baskets') or 0)),
        str(r.get('code') or ''),
    ))
    return targets


def fetch_json_with_retry(req, timeout=8, attempts=3):
    last = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.35 * (attempt + 1))
    raise last


def fetch_latest_closes(rows):
    """Low-frequency previous-close proxy for the auxiliary premium layer.

    Tencent is used here because the Eastmoney batch quote endpoint is
    intermittently closing server-side connections from this host. Before the
    call auction (our 08:35/08:55 refresh window), field 3 is still the latest
    completed market close. This is deliberately non-canonical and never gates
    the official PCF path.
    """
    if not rows:
        return {}
    symbols = []
    for row in rows:
        code = str(row.get('code') or '')
        prefix = 'sh' if row.get('exchange') == 'SSE' else 'sz'
        symbols.append(prefix + code)
    url = 'https://qt.gtimg.cn/q=' + ','.join(symbols)
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://gu.qq.com/',
            })
            with urllib.request.urlopen(req, timeout=6) as response:
                text = response.read().decode('gbk', errors='ignore')
            result = {}
            for line in text.split(';'):
                if '="' not in line:
                    continue
                body = line.split('="', 1)[1].rsplit('"', 1)[0]
                parts = body.split('~')
                if len(parts) <= 30:
                    continue
                code = str(parts[2] or '')
                try:
                    close = float(parts[3])
                except (TypeError, ValueError):
                    continue
                if close <= 0:
                    continue
                stamp = str(parts[30] or '')
                close_date = stamp[:8] if len(stamp) >= 8 and stamp[:8].isdigit() else None
                result[code] = {'close': close, 'close_date': close_date}
            if result:
                return result
            raise RuntimeError('Tencent quote payload has no usable rows')
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(0.35 * (attempt + 1))
    raise last


def fetch_latest_formal_nav(code):
    url = 'https://api.fund.eastmoney.com/f10/lsjz?' + urllib.parse.urlencode({
        'fundCode': code, 'pageIndex': 1, 'pageSize': 3,
    })
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0',
        'Referer': f'https://fundf10.eastmoney.com/jjjz_{code}.html',
    })
    payload = fetch_json_with_retry(req, timeout=8, attempts=3)
    for item in ((payload.get('Data') or {}).get('LSJZList') or []):
        try:
            nav = float(item.get('DWJZ'))
        except (TypeError, ValueError):
            continue
        if nav > 0:
            return {'nav': nav, 'nav_date': str(item.get('FSRQ') or '').replace('-', '') or None}
    return None


def load_premium_cache():
    if not PREMIUM_CACHE.exists():
        return {'schema_version': 1, 'rows': []}
    try:
        payload = json.loads(PREMIUM_CACHE.read_text(encoding='utf-8'))
        return payload if isinstance(payload, dict) else {'schema_version': 1, 'rows': []}
    except Exception:
        return {'schema_version': 1, 'rows': []}


def build_reference_premium(monitor, comparison):
    targets = reference_premium_targets(monitor, comparison)
    target_codes = [str(r.get('code') or '') for r in targets]
    china_day = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d')
    previous = load_premium_cache()
    previous_rows = {str(r.get('code') or ''): r for r in previous.get('rows') or [] if isinstance(r, dict)}
    target_signature = ','.join(sorted(target_codes))
    if (
        os.environ.get('ETF_FORCE_REFERENCE_PREMIUM') != '1'
        and previous.get('cache_day') == china_day
        and previous.get('target_signature') == target_signature
        and int(previous.get('row_count') or 0) > 0
    ):
        return previous, 'cache_current'
    try:
        closes = fetch_latest_closes(targets)
        navs = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(fetch_latest_formal_nav, code): code for code in target_codes}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    item = future.result()
                except Exception:
                    item = None
                if item:
                    navs[code] = item
        rows = []
        for row in targets:
            code = str(row.get('code') or '')
            close = closes.get(code)
            nav = navs.get(code)
            if not close or not nav:
                if code in previous_rows:
                    rows.append(previous_rows[code])
                continue
            premium = (close['close'] / nav['nav'] - 1.0) * 100.0
            rows.append({
                'exchange': row.get('exchange'),
                'code': code,
                'name': row.get('name'),
                'close': close['close'],
                'close_date': close.get('close_date'),
                'formal_nav': nav['nav'],
                'nav_date': nav.get('nav_date'),
                'premium_pct': round(premium, 4),
            })
        payload = {
            'schema_version': 1,
            'cache_day': china_day,
            'method': 'latest_close / latest_formal_nav - 1',
            'source': 'Tencent close + Eastmoney formal NAV, auxiliary non-canonical',
            'target_signature': target_signature,
            'target_count': len(targets),
            'row_count': len(rows),
            'fetched_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
            'rows': rows,
        }
        AUX_CACHE.mkdir(parents=True, exist_ok=True)
        PREMIUM_CACHE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return payload, 'fresh'
    except Exception as exc:
        previous['error'] = type(exc).__name__
        return previous, 'cache_fallback:' + type(exc).__name__


def latest_events():
    empty = {'trade_date': None, 'previous_trade_date': None, 'event_count': 0, 'events': []}
    d = STATE / 'events'
    files = sorted(d.glob('*.json')) if d.exists() else []
    if not files:
        return empty
    return json.loads(files[-1].read_text(encoding='utf-8'))


def read_snapshot(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return json.load(f)


def build_comparison():
    files = sorted((STATE / 'snapshots').glob('*.json.gz'))
    if not files:
        return {'schema_version': 1, 'previous_trade_date': None, 'current_trade_date': None, 'row_count': 0, 'changed_count': 0, 'rows': []}
    current = read_snapshot(files[-1])
    previous = read_snapshot(files[-2]) if len(files) >= 2 else None
    previous_map = {
        (str(r.get('exchange') or ''), str(r.get('code') or '')): r
        for r in ((previous or {}).get('rows') or []) if isinstance(r, dict)
    }
    rows = []
    for cur in current.get('rows') or []:
        if not isinstance(cur, dict):
            continue
        key = (str(cur.get('exchange') or ''), str(cur.get('code') or ''))
        prev = previous_map.get(key)
        prev_kind = prev.get('market_capacity_kind') if prev else None
        cur_kind = cur.get('market_capacity_kind')
        prev_status = prev.get('market_capacity_status') if prev else None
        cur_status = cur.get('market_capacity_status') or 'UNKNOWN'
        comparable = prev is not None and prev_status == cur_status == 'LIMITED' and prev_kind == cur_kind
        prev_total = prev.get('total_baskets') if prev else None
        cur_total = cur.get('total_baskets')
        delta = None
        ratio = None
        if comparable and isinstance(prev_total, int) and isinstance(cur_total, int):
            delta = cur_total - prev_total
            if prev_total > 0:
                ratio = cur_total / prev_total
        prev_account_kind = prev.get('account_capacity_kind') if prev else None
        cur_account_kind = cur.get('account_capacity_kind')
        prev_account = prev.get('account_baskets') if prev else None
        cur_account = cur.get('account_baskets')
        account_delta = None
        if prev is not None and prev_account_kind == cur_account_kind and isinstance(prev_account, int) and isinstance(cur_account, int):
            account_delta = cur_account - prev_account
        creation_changed = prev is not None and prev.get('creation_allowed') != cur.get('creation_allowed')
        capacity_rule_changed = prev is not None and prev_kind != cur_kind
        account_rule_changed = prev is not None and prev_account_kind != cur_account_kind
        prev_account_status = prev.get('account_capacity_status') if prev else None
        cur_account_status = cur.get('account_capacity_status') or 'UNKNOWN'
        capacity_status_comparable = prev is not None and prev_status not in (None, 'UNKNOWN') and cur_status != 'UNKNOWN'
        account_capacity_status_comparable = prev is not None and prev_account_status not in (None, 'UNKNOWN') and cur_account_status != 'UNKNOWN'
        capacity_status_changed = capacity_status_comparable and prev_status != cur_status
        account_capacity_status_changed = account_capacity_status_comparable and prev_account_status != cur_account_status
        changed = bool(creation_changed or capacity_rule_changed or account_rule_changed or capacity_status_changed or account_capacity_status_changed or delta not in (None, 0) or account_delta not in (None, 0))
        rows.append({
            'exchange': key[0], 'code': key[1],
            'previous_total_baskets': prev_total,
            'current_total_baskets': cur_total,
            'previous_account_baskets': prev_account,
            'current_account_baskets': cur_account,
            'previous_capacity_kind': prev_kind,
            'current_capacity_kind': cur_kind,
            'previous_market_capacity_status': prev_status or ('UNKNOWN' if prev is not None else None),
            'current_market_capacity_status': cur_status,
            'previous_account_capacity_status': prev_account_status or ('UNKNOWN' if prev is not None else None),
            'current_account_capacity_status': cur_account_status,
            'capacity_comparable': comparable,
            'basket_delta': delta,
            'basket_ratio': ratio,
            'account_basket_delta': account_delta,
            'creation_changed': creation_changed,
            'capacity_rule_changed': capacity_rule_changed,
            'account_rule_changed': account_rule_changed,
            'capacity_status_comparable': capacity_status_comparable,
            'account_capacity_status_comparable': account_capacity_status_comparable,
            'capacity_status_changed': capacity_status_changed,
            'account_capacity_status_changed': account_capacity_status_changed,
            'changed': changed,
        })
    return {
        'schema_version': 1,
        'previous_trade_date': (previous or {}).get('trade_date'),
        'current_trade_date': current.get('trade_date'),
        'row_count': len(rows),
        'changed_count': sum(1 for r in rows if r['changed']),
        'rows': rows,
    }


def publish_history_index():
    HISTORY_DATA.mkdir(parents=True, exist_ok=True)
    PUBLIC_HISTORY.mkdir(parents=True, exist_ok=True)
    PUBLIC_SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    snapshots = []
    for src in sorted((STATE / 'snapshots').glob('*.json.gz')):
        dst = PUBLIC_SNAPSHOTS / src.name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            if dst.exists():
                dst.unlink()
            try:
                os.link(src, dst)
            except OSError:
                shutil.copyfile(src, dst)
        snapshots.append({'trade_date': src.name[:8], 'file': src.name, 'bytes': src.stat().st_size})
    payload = {'schema_version': 1, 'snapshots': snapshots}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    (HISTORY_DATA / 'index.json').write_bytes(raw)
    (PUBLIC_HISTORY / 'index.json').write_bytes(raw)
    return len(snapshots)


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    PUBLIC_DATA.mkdir(parents=True, exist_ok=True)
    raw = fetch_monitor_view()
    monitor = json.loads(raw)
    (DATA / 'monitor_view.json').write_bytes(raw)

    status = {}
    p = STATE / 'last_run.json'
    if p.exists():
        status = json.loads(p.read_text(encoding='utf-8'))
    events = latest_events()
    try:
        comparison = fetch_comparison_view()
        comparison_source = 'relay'
    except Exception as exc:
        comparison = build_comparison()
        comparison_source = 'local_fallback:' + type(exc).__name__
    comparison_raw = (json.dumps(comparison, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    (DATA / 'comparison.json').write_bytes(comparison_raw)

    if os.environ.get('ETF_REFRESH_REFERENCE_PREMIUM') == '1':
        reference_premium, premium_source = build_reference_premium(monitor, comparison)
    else:
        reference_premium = load_premium_cache()
        premium_source = 'cache_only' if int(reference_premium.get('row_count') or 0) > 0 else 'cache_empty'
    premium_raw = (json.dumps(reference_premium, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    (DATA / 'reference_premium.json').write_bytes(premium_raw)

    if ENAV_CACHE.exists():
        try:
            enav = json.loads(ENAV_CACHE.read_text(encoding='utf-8'))
        except Exception:
            enav = {'schema_version': 1, 'rows': []}
    else:
        enav = {'schema_version': 1, 'rows': []}
    enav_raw = (json.dumps(enav, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    (DATA / 'enav_estimate.json').write_bytes(enav_raw)

    summary = monitor.get('summary') or {}
    meta = {
        'shadow_mode': True,
        'trade_date': monitor.get('target_trade_date') or monitor.get('trade_date') or status.get('trade_date'),
        'runtime_status': status,
        'monitor_summary': summary,
        'event_trade_date': events.get('trade_date'),
        'event_count': events.get('event_count', 0),
        'events': events.get('events') or [],
        'comparison_previous_trade_date': comparison.get('previous_trade_date'),
        'comparison_current_trade_date': comparison.get('current_trade_date'),
        'comparison_changed_count': comparison.get('changed_count', 0),
        'comparison_source': comparison_source,
        'reference_premium_source': premium_source,
        'reference_premium_count': reference_premium.get('row_count', 0),
        'enav_count': enav.get('row_count', 0),
        'enav_error_count': enav.get('error_count', 0),
        'enav_generated_at': enav.get('generated_at'),
    }
    status_raw = (json.dumps(meta, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    (DATA / 'status.json').write_bytes(status_raw)

    (PUBLIC_DATA / 'monitor_view.json').write_bytes(raw)
    (PUBLIC_DATA / 'status.json').write_bytes(status_raw)
    (PUBLIC_DATA / 'comparison.json').write_bytes(comparison_raw)
    (PUBLIC_DATA / 'reference_premium.json').write_bytes(premium_raw)
    (PUBLIC_DATA / 'enav_estimate.json').write_bytes(enav_raw)
    history_days = publish_history_index()
    shutil.copyfile(WEB / 'index.html', PUBLIC / 'index.html')
    shutil.copyfile(WEB / 'detail.html', PUBLIC / 'detail.html')
    print(json.dumps({
        'rows': len(monitor.get('rows') or []),
        'events': meta['event_count'],
        'trade_date': meta['trade_date'],
        'comparison': f"{comparison.get('previous_trade_date')}->{comparison.get('current_trade_date')}",
        'comparison_changed': comparison.get('changed_count', 0),
        'comparison_source': comparison_source,
        'reference_premium': reference_premium.get('row_count', 0),
        'premium_source': premium_source,
        'enav': enav.get('row_count', 0),
        'enav_errors': enav.get('error_count', 0),
        'history_days': history_days,
        'public': '/opportunity-portal/etf-shadow/'
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
