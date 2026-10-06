import json
import tempfile
import unittest
from pathlib import Path

from runtime.intelligence_radar.storage import get_daily_view, save_daily_result
from runtime.intelligence_radar.xueqiu_daily import load_shadow_candidates


class IntelligenceRadarXueqiuFormalTest(unittest.TestCase):
    def test_shadow_archive_maps_into_formal_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / 'intelligence_radar/xueqiu_shadow/2026/10/2026-10-06.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                'schema_version': 1,
                'source': 'xueqiu',
                'logical_date': '2026-10-06',
                'item_count': 2,
                'items': [
                    {
                        'item_type': 'POST', 'item_id': '100', 'status_id': '100',
                        'parent_id': None, 'author_id': '1', 'author_name': '甲',
                        'published_at': '2026-10-06T10:00:00+08:00',
                        'title': '测试帖子', 'locator_url': 'https://www.xueqiu.com/1/100',
                        'excerpt': '明确的规则型新信息', 'parent_excerpt': None,
                        'discovery_paths': ['XUEQIU_HOT_EXPLORATION'],
                    },
                    {
                        'item_type': 'REPLY', 'item_id': '501', 'status_id': '101',
                        'parent_id': '999', 'author_id': '2', 'author_name': '乙',
                        'published_at': '2026-10-06T11:00:00+08:00',
                        'title': None, 'locator_url': 'https://www.xueqiu.com/2/101',
                        'excerpt': '补充一个真实执行结果', 'parent_excerpt': '父帖背景',
                        'discovery_paths': ['XUEQIU_AUTHOR_SHADOW'],
                    },
                ],
            }, ensure_ascii=False), encoding='utf-8')
            candidates, archive = load_shadow_candidates(root, '2026-10-06')
            self.assertEqual(archive['item_count'], 2)
            self.assertEqual(len(candidates), 2)
            self.assertEqual({c['question_id'] for c in candidates}, {'100', '999'})
            reply = next(c for c in candidates if c['question_id'] == '999')
            self.assertEqual(reply['daily_segments'][0]['locator_id'], '501')
            self.assertEqual(reply['daily_segments'][0]['kind'], 'XQ_REPLY')
            self.assertEqual(reply['context_segments'][0]['text'], '父帖背景')

    def test_two_sources_coexist_in_same_daily_view(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'radar.sqlite3'
            common = dict(
                run_date='2026-10-06', scan_status='OK',
                scanned_at='2026-10-06T21:20:00+08:00',
                completed_at='2026-10-06T21:21:00+08:00',
                candidate_count=1, run_mode='BACKFILL',
            )
            finding = lambda name, url: [{
                'item_key': name, 'question_id': name, 'object_name': name,
                'node_title': '一个规则节点', 'title': name, 'url': url,
                'author': '作者', 'observed_at': '2026-10-06T20:00:00+08:00',
                'finding_type': 'NEW_CASE', 'what_happened': '新增具体事实',
                'ai_understanding': '可能影响一种规则型机会',
                'current_judgment': '值得继续验证', 'worth_follow_up': True,
                'evidence_excerpt': '证据',
            }]
            save_daily_result(db, source='jisilu', findings=finding('集思录对象','https://www.jisilu.cn'), **common)
            save_daily_result(db, source='xueqiu', findings=finding('雪球对象','https://www.xueqiu.com'), **common)
            view = get_daily_view(db, '2026-10-06')
            self.assertEqual({r['source'] for r in view['runs']}, {'jisilu','xueqiu'})
            self.assertEqual({f['source'] for f in view['findings']}, {'jisilu','xueqiu'})
            self.assertEqual(view['finding_count'], 2)

    def test_prompts_explicitly_exclude_technical_indicator_timing(self):
        root = Path('runtime/intelligence_radar')
        broad = (root / 'prompt_daily_filter.md').read_text(encoding='utf-8')
        final = (root / 'prompt_final_gate.md').read_text(encoding='utf-8')
        for text in (broad, final):
            self.assertIn('技术指标', text)
            self.assertIn('均线', text)
            self.assertIn('面值退市', text)


if __name__ == '__main__':
    unittest.main()
