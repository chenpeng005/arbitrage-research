from runtime.intelligence_radar.jisilu import parse_feed, parse_question_daily

FEED = """
<div class="aw-item">
<span class="aw-question-replay-count aw-border-radius-5 active">
<em>27</em> 回复</span>
<div class="aw-questoin-content">
<h4><a target="_blank" href="https://www.jisilu.cn/question/525689">鹿公停止更新。</a></h4>
<span class="aw-text-color-999">
<span class="aw-question-tags"><a href="https://www.jisilu.cn/category/6">其他</a></span> •
<a href="https://www.jisilu.cn/people/a" class="aw-user-name">甲</a>
回复 • 2026-10-04 20:44 • 2,202 次浏览
</span>
</div>
</div>
"""

DETAIL = """
<div class="aw-mod aw-item aw-question-detail-title">
<div class="aw-mod-head"><h1>测试主题</h1></div>
<div class="aw-mod-body">
<div class="aw-question-detail-txt markitup-box">原帖<br />第二行</div>
<div class="aw-question-detail-meta">发表时间 2026-10-03 08:30</div>
</div></div>
<div class="aw-question-detail-box">
<h2>2 个回复</h2>
<div class="aw-item" id="answer_list_10">
<p class="publisher"><a class="aw-user-name">乙</a></p>
<div class="markitup-box" >今天实测到账慢了20分钟</div>
<div class="aw-dynamic-topic-meta"><span class="pull-left aw-text-color-999">2026-10-04 20:21 来自重庆</span></div>
</div>
<div class="aw-item" id="answer_list_9">
<p class="publisher"><a class="aw-user-name">丙</a></p>
<div class="markitup-box" >昨天的回复</div>
<div class="aw-dynamic-topic-meta"><span class="pull-left aw-text-color-999">2026-10-03 20:21 来自重庆</span></div>
</div>
</div>
<div class="aw-side-bar-mod">
<div class="aw-side-bar-mod-head"><h3>发起人</h3></div>
<div class="aw-side-bar-mod-body"><a class="aw-user-name" href="/people/a">楼主</a></div>
</div>
"""


def test_parse_feed_latest_activity() -> None:
    rows = parse_feed(FEED, feed_mode="activity")
    assert len(rows) == 1
    row = rows[0]
    assert row.question_id == "525689"
    assert row.activity_at == "2026-10-04 20:44"
    assert row.reply_count == 27
    assert row.view_count == 2202
    assert row.actor == "甲"


def test_parse_question_keeps_only_target_day_activity() -> None:
    packet = parse_question_daily(
        DETAIL,
        target_date="2026-10-04",
        fallback_title="fallback",
        url="https://www.jisilu.cn/question/1",
        question_id="1",
        activity_at="2026-10-04 20:21",
        category="基金",
    )
    assert packet["title"] == "测试主题"
    assert packet["question_author"] == "楼主"
    assert len(packet["daily_segments"]) == 1
    assert packet["daily_segments"][0]["segment_id"] == "answer_10"
    assert "20分钟" in packet["daily_segments"][0]["text"]
    assert packet["context_segments"][0]["is_daily"] is False
