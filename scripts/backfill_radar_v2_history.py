from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

from runtime.intelligence_radar.storage import (
    connect,
    ensure_object,
    init_db,
    list_daily_dates,
    now_utc,
    radar_db_path,
    write_daily_archive,
)

# One-time, reviewed seed for the 2026-09-28 .. 2026-10-04 historical sample.
# Object names are stable nouns/strategy names. Node titles describe that day's increment.
SEED: dict[str, dict[str, Any]] = {
    "RDF_e3815ffa74c8550eee4b": {
        "object_name": "*ST康佳A",
        "node_title": "现金选择权申报窗口尚未开放，券商入口存在差异",
        "aliases": ["深康佳A", "000016"],
    },
    "RDF_976ad2dd0114f8971135": {
        "object_name": "*ST康佳A",
        "node_title": "现金选择权申报期确认，当前仍无法申报",
        "aliases": ["深康佳A", "000016"],
    },
    "RDF_ba35bbf7bcc1a525e6d5": {
        "object_name": "纳指科技ETF（159509）",
        "node_title": "场内溢价接近30%，场外申购关闭",
    },
    "RDF_58a0adc1103e5882dd83": {
        "object_name": "南航转债",
        "node_title": "盘中疑似乌龙成交，条件单未能捕获",
    },
    "RDF_064234506e78f82c8fe8": {
        "object_name": "圣晖集成",
        "node_title": "配债一手策略门槛与安全垫测算",
        "what_happened": (
            "圣晖集成8月12日转债发行同意注册，9月18日现金分红，"
            "正股/转股价为90.48%，5.5亿规模，大股东配售后实际流通约1.5亿。"
            "帖子给出沪市一手配债测算：满额182股配一手，一手策略125股配一手；"
            "按转债上市157元估算安全垫约6.1%，预计10月中下旬发债。"
        ),
        "ai_understanding": (
            "这是配债/抢权一手策略的新案例，给出了明确配售股数门槛、"
            "安全垫测算和预计发债时间窗，属于可复用的机械规则型信息。"
        ),
        "current_judgment": "新案例，配售股数门槛与安全垫明确，值得跟踪发债进度和安全垫变化。",
    },
    "RDF_0dab7a66fac93e6bdea4": {
        "object_name": "中金三兄弟换股套利",
        "node_title": "吸收合并规则理解不足导致复牌首日亏损",
        "aliases": ["中金吸收合并", "中金三兄弟"],
    },
    "RDF_9ab28e7f0aa7662a60b6": {
        "object_name": "消费贷资金投资低波资产",
        "node_title": "多银行消费贷实测将资金成本压至3%左右",
    },
    "RDF_6256bce806f8ebd13e23": {
        "object_name": "消费贷资金投资低波资产",
        "node_title": "银行卡限额与资金过手显著增加执行摩擦",
    },
    "RDF_297fe33210ba69ce4ea8": {
        "object_name": "纳指ETF溢价交易",
        "node_title": "节前ETF溢价与期货走势明显背离",
    },
    "RDF_6b383a5c2b2c1b55f21c": {
        "object_name": "岭南转债",
        "node_title": "正股退市后转债兑付与小额优先现象",
    },
    "RDF_eb73b871afc0d37ff137": {
        "object_name": "券商网格条件单",
        "node_title": "不同券商条件单触发机制存在差异",
    },
    "RDF_d455d2d0a4c37797dc34": {
        "object_name": "002667",
        "node_title": "要约达到5%门槛但要约价低于市价",
    },
    "RDF_d308e8f1cafba0184583": {
        "object_name": "江山转债",
        "node_title": "偿债能力与再次下修化债可能性",
    },
    "RDF_0bd08c00b111915da456": {
        "object_name": "绿茵转债",
        "node_title": "下修到底落地后的价格反应待验证",
    },
    "RDF_f2bf8db082bfe924c93d": {
        "object_name": "渝水转债",
        "node_title": "平台下修触发价疑似按错误比例计算",
    },
    "RDF_315e27c40baf72c8798b": {
        "object_name": "*ST康佳A",
        "node_title": "中信行权后资金可用状态出现反复",
        "aliases": ["深康佳A", "000016"],
    },
    "RDF_a627bdd1741fe91d9f56": {
        "object_name": "规则型资产配置",
        "node_title": "弱者体系将规则确定性置于价值与周期之上",
    },
    "RDF_46dc88fed01532468d34": {
        "object_name": "*ST康佳A",
        "node_title": "银河证券现金选择权行权路径确认",
        "aliases": ["深康佳A", "000016"],
    },
    "RDF_f055f173729bc2918311": {
        "object_name": "康佳B",
        "node_title": "国泰海通需通过特殊持仓代码完成行权",
        "aliases": ["200016"],
    },
    "RDF_adcce26d5799ab30d544": {
        "object_name": "货币ETF（511800）",
        "node_title": "节前出现罕见大幅拉升与预埋单机会",
    },
    "RDF_b0692e5125a40d4939f8": {
        "object_name": "港股ETF申赎",
        "node_title": "沪深市场赎回到账时间存在一天差异",
    },
    "RDF_b2f0d3f08d1134c62f5c": {
        "object_name": "侨银转债",
        "node_title": "下修计数期正股成为独立博弈窗口",
    },
    "RDF_a3505fb4fa6293146488": {
        "object_name": "跨境QDII ETF申购套利",
        "node_title": "多账户实测显示容量窗口正在收窄",
        "aliases": ["QDII跨境ETF申购套利"],
    },
    "RDF_c37223833c4fccba68f7": {
        "object_name": "中金三兄弟换股套利",
        "node_title": "现金选择权下调条款压制仓位与实际收益",
        "aliases": ["中金吸收合并", "中金三兄弟"],
    },
    "RDF_7658b1db0e6ba3656ef2": {
        "object_name": "IM跨期套利",
        "node_title": "大跨度合约收敛过晚导致首次实盘亏损",
    },
}

PRECISE_EVIDENCE: dict[str, tuple[str, str, str]] = {
    "RDF_e3815ffa74c8550eee4b": ("5550362", "caishendao", "2026-09-28 10:47"),
    "RDF_976ad2dd0114f8971135": ("5550357", "红牛Y", "2026-09-28 10:45"),
    "RDF_ba35bbf7bcc1a525e6d5": ("5551284", "虞鼠乔鱼", "2026-09-29 14:23"),
    "RDF_58a0adc1103e5882dd83": ("5551283", "viking75", "2026-09-29 14:22"),
    "RDF_064234506e78f82c8fe8": ("5552532", "稳定变富之路", "2026-09-30 21:18"),
    "RDF_0dab7a66fac93e6bdea4": ("5552500", "hannon", "2026-09-30 20:28"),
    "RDF_9ab28e7f0aa7662a60b6": ("5552439", "POOL哥", "2026-09-30 18:51"),
    "RDF_6256bce806f8ebd13e23": ("5552300", "沐柰", "2026-09-30 16:06"),
    "RDF_297fe33210ba69ce4ea8": ("5552250", "周8272339899", "2026-09-30 15:36"),
    "RDF_6b383a5c2b2c1b55f21c": ("5551900", "百战百胜心法", "2026-09-30 10:11"),
    "RDF_eb73b871afc0d37ff137": ("5552171", "寿山", "2026-09-30 15:07"),
    "RDF_d308e8f1cafba0184583": ("5552007", "枫林随手记", "2026-09-30 11:39"),
    "RDF_0bd08c00b111915da456": ("5551782", "心蓝黄", "2026-09-30 07:59"),
    "RDF_315e27c40baf72c8798b": ("5552727", "rzchen", "2026-10-01 09:49"),
    "RDF_a627bdd1741fe91d9f56": ("5552662", "zyc田忌赛马", "2026-10-01 08:14"),
    "RDF_46dc88fed01532468d34": ("5553121", "张集思78", "2026-10-02 14:36"),
    "RDF_f055f173729bc2918311": ("5553117", "CZX303304", "2026-10-02 14:32"),
    "RDF_adcce26d5799ab30d544": ("5553032", "集思小子", "2026-10-02 09:31"),
    "RDF_b0692e5125a40d4939f8": ("5553411", "bi18an", "2026-10-03 15:53"),
    "RDF_b2f0d3f08d1134c62f5c": ("5553316", "火星兔", "2026-10-03 09:28"),
    "RDF_c37223833c4fccba68f7": ("5553554", "yyj919", "2026-10-04 07:29"),
    "RDF_7658b1db0e6ba3656ef2": ("5553677", "flyzizai", "2026-10-04 13:16"),
}

SPLIT_KANGJIA_ID = "RDF_20260930_kangjia_split"


def _evidence_id(finding_id: str, locator_id: str, published_at: str) -> str:
    raw = f"{finding_id}|jisilu|{locator_id}|{published_at}"
    return "EVD_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _insert_seed_evidence(conn, finding_id: str) -> None:
    row = conn.execute(
        """SELECT source,title,url,author,observed_at,evidence_excerpt,question_id
           FROM daily_finding WHERE finding_id=?""",
        (finding_id,),
    ).fetchone()
    if row is None:
        return
    conn.execute("DELETE FROM finding_evidence WHERE finding_id=?", (finding_id,))
    precise = PRECISE_EVIDENCE.get(finding_id)
    if precise:
        locator_id, author, published_at = precise
        locator_kind = "ANSWER"
        locator_url = f"{row['url']}#answer_list_{locator_id}"
    else:
        locator_id = str(row["question_id"] or "")
        author = row["author"]
        published_at = row["observed_at"]
        locator_kind = "QUESTION"
        locator_url = row["url"]
    conn.execute(
        """INSERT INTO finding_evidence(
               evidence_id,finding_id,source,author,published_at,source_title,
               source_url,locator_kind,locator_id,locator_url,excerpt,
               is_primary,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _evidence_id(finding_id, locator_id, str(published_at or "")),
            finding_id,
            row["source"],
            author,
            published_at,
            row["title"],
            row["url"],
            locator_kind,
            locator_id,
            locator_url,
            row["evidence_excerpt"],
            1,
            now_utc(),
        ),
    )


def backfill(data_root: Path) -> dict[str, int]:
    db = radar_db_path(data_root)
    init_db(db)
    updated = 0
    missing = 0
    with connect(db) as conn:
        for finding_id, seed in SEED.items():
            row = conn.execute(
                "SELECT finding_id FROM daily_finding WHERE finding_id=?",
                (finding_id,),
            ).fetchone()
            if row is None:
                missing += 1
                continue
            object_id = ensure_object(
                conn,
                seed["object_name"],
                aliases=seed.get("aliases", []),
            )
            assignments = ["object_id=?", "node_title=?"]
            values: list[Any] = [object_id, seed["node_title"]]
            for field in ("what_happened", "ai_understanding", "current_judgment"):
                if field in seed:
                    assignments.append(f"{field}=?")
                    values.append(seed[field])
            values.append(finding_id)
            conn.execute(
                f"UPDATE daily_finding SET {', '.join(assignments)} WHERE finding_id=?",
                values,
            )
            _insert_seed_evidence(conn, finding_id)
            updated += 1

        # Split the reviewed mixed 9/30 node so one node has exactly one main object.
        original = conn.execute(
            "SELECT * FROM daily_finding WHERE finding_id='RDF_064234506e78f82c8fe8'"
        ).fetchone()
        if original is not None:
            object_id = ensure_object(
                conn,
                "*ST康佳A",
                aliases=["深康佳A", "000016"],
            )
            conn.execute(
                """INSERT INTO daily_finding(
                       finding_id,run_date,source,item_key,question_id,title,url,
                       author,observed_at,finding_type,what_happened,
                       ai_understanding,current_judgment,worth_follow_up,
                       evidence_excerpt,created_at,object_id,node_title
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(finding_id) DO UPDATE SET
                       object_id=excluded.object_id,
                       node_title=excluded.node_title,
                       what_happened=excluded.what_happened,
                       ai_understanding=excluded.ai_understanding,
                       current_judgment=excluded.current_judgment""",
                (
                    SPLIT_KANGJIA_ID,
                    original["run_date"],
                    original["source"],
                    original["item_key"],
                    original["question_id"],
                    original["title"],
                    original["url"],
                    original["author"],
                    original["observed_at"],
                    "REAL_TEST",
                    "*ST康佳A已显示按2.48元行权，帖子预计10月8日晚资金到账。",
                    "这是现金选择权执行后的到账时点实测，关系到资金周转与退出完成确认。",
                    "已有玩法的新实测，继续跟踪实际到账结果。",
                    1,
                    original["evidence_excerpt"],
                    now_utc(),
                    object_id,
                    "现金选择权按2.48元行权，等待资金到账",
                ),
            )
            PRECISE_EVIDENCE[SPLIT_KANGJIA_ID] = (
                "5552532",
                "稳定变富之路",
                "2026-09-30 21:18",
            )
            _insert_seed_evidence(conn, SPLIT_KANGJIA_ID)

        conn.execute(
            """UPDATE daily_run
               SET finding_count=(
                   SELECT COUNT(*) FROM daily_finding f
                   WHERE f.run_date=daily_run.run_date AND f.source=daily_run.source
               )
               WHERE run_date BETWEEN '2026-09-28' AND '2026-10-04'"""
        )
        conn.commit()

    archived = 0
    for day in list_daily_dates(db, 120):
        write_daily_archive(data_root, day)
        archived += 1
    return {"updated": updated, "missing": missing, "archived": archived}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill reviewed Radar V2 history metadata.")
    parser.add_argument("--data-root", default="runtime_data")
    args = parser.parse_args()
    result = backfill(Path(args.data_root))
    print(result)


if __name__ == "__main__":
    main()
