from runtime.intelligence_radar.filtering import enrich_findings, validate_filter_output


def _input():
    return {
        "run_date": "2026-10-04",
        "source": "jisilu",
        "batch_id": "b1",
        "candidates": [
            {
                "question_id": "1",
                "title": "到账时间实测",
                "url": "https://www.jisilu.cn/question/1",
                "question_author": "甲",
                "activity_at": "2026-10-04 20:00",
                "context_segments": [
                    {
                        "segment_id": "question",
                        "is_daily": False,
                        "author": "甲",
                        "text": "讨论ETF申购",
                    }
                ],
                "daily_segments": [
                    {
                        "segment_id": "answer_10",
                        "is_daily": True,
                        "author": "乙",
                        "published_at": "2026-10-04 20:00",
                        "text": "今天实测到账慢了20分钟",
                    }
                ],
            }
        ],
    }


def test_filter_validator_requires_daily_support() -> None:
    output = {
        "run_date": "2026-10-04",
        "source": "jisilu",
        "batch_id": "b1",
        "findings": [
            {
                "question_id": "1",
                "finding_type": "EXECUTION_ISSUE",
                "what_happened": "申购份额到账比预期慢",
                "ai_understanding": "到账时间会形成未对冲的价格暴露",
                "current_judgment": "执行风险，值得继续跟踪",
                "worth_follow_up": True,
                "supporting_segment_ids": ["answer_10"],
            }
        ],
    }
    assert validate_filter_output(_input(), output) == []
    enriched = enrich_findings(_input(), output)
    assert enriched[0]["title"] == "到账时间实测"
    assert "20分钟" in enriched[0]["evidence_excerpt"]


def test_filter_validator_rejects_context_only_support() -> None:
    output = {
        "run_date": "2026-10-04",
        "source": "jisilu",
        "batch_id": "b1",
        "findings": [
            {
                "question_id": "1",
                "finding_type": "NEW_MECHANISM",
                "what_happened": "只是复述旧帖内容",
                "ai_understanding": "没有当天新增证据",
                "current_judgment": "不应作为今日发现",
                "worth_follow_up": False,
                "supporting_segment_ids": ["question"],
            }
        ],
    }
    errors = validate_filter_output(_input(), output)
    assert any("daily segment" in item for item in errors)
