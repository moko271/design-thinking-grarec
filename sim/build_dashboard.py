#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_dashboard.py — out/ の結果を1枚のHTMLにまとめる（API不要）
  python sim/build_dashboard.py              → sim/out/dashboard.html（ブラウザで開く）
  python sim/build_dashboard.py --fragment   → <html>等を付けない版（Artifact公開用）
結果ファイルがない部分は「まだ結果がありません」と表示する（例の数字は入れない）。
"""
import html, json, os, sys
from datetime import datetime
from sim_common import HERE

OUT = os.path.join(HERE, "out")
E = lambda s: html.escape(str(s))


def load(name):
    p = os.path.join(OUT, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def pol_label(r):
    m = "エントロピー" if r["metric"] == "entropy" else "ジニ係数"
    op = "<" if r["metric"] == "entropy" else "≥"
    q = "なし" if r["quiet"] is None else f"{int(r['quiet'] * 100)}%"
    return f"{m}{op}{r['thr']}・{r['window']}分・話していない人 {q}・閉じた後 {r['snooze']}分"


def fmt(v, pct=False):
    if v is None: return "–"
    return f"{v * 100:.0f}%" if pct else f"{v}"


def strip_svg(s):
    W, H, L, R = 640, 46, 4, 4
    span = max(s["minutes"], 1)
    x = lambda m: L + (W - L - R) * m / span
    parts = [f'<rect x="{x(a):.1f}" y="6" width="{max(1, x(b) - x(a)):.1f}" height="34" rx="3" class="truth"/>' for a, b in s["truth"]]
    parts += [f'<line x1="{x(t):.1f}" x2="{x(t):.1f}" y1="8" y2="22" class="m-cur"><title>今の設定：{t:.1f}分</title></line>' for t in s["current"]]
    parts += [f'<line x1="{x(t):.1f}" x2="{x(t):.1f}" y1="24" y2="38" class="m-best"><title>候補：{t:.1f}分</title></line>' for t in s["best"]]
    tm = list(range(0, int(span) + 1, 10))
    anc = lambda m: "start" if m == 0 else ("end" if m == tm[-1] and x(m) > W - R - 8 else "middle")
    ticks = "".join(f'<text x="{x(m):.1f}" y="{H + 10}" class="tick" text-anchor="{anc(m)}">{m}分</text>' for m in tm)
    return (f'<svg viewBox="0 0 {W} {H + 14}" role="img" aria-label="{E(s["timeline"])}：灰色が本当に偏っていた時間帯、'
            f'上段の線が今の設定のお知らせ（{len(s["current"])}回）、下段が候補（{len(s["best"])}回）">'
            f'<rect x="{L}" y="6" width="{W - L - R}" height="34" rx="3" class="track"/>{"".join(parts)}{ticks}</svg>')


def section_balance(b):
    if not b:
        return '<p class="empty">まだ結果がありません。<code>python sim/sim_balance.py</code> を実行してください。</p>'
    tab = b["summary"]; cur = next(r for r in tab if r["current"]); best = next((r for r in tab if r.get("candidate")), None) or next(r for r in tab if not r["current"]); a = b["assumptions"]
    kpi = lambda label, a, c, pct=False, lower=False: (
        f'<div class="kpi"><span class="k">{label}</span><span class="v"><b>{fmt(a, pct)}</b><small>今の設定</small></span>'
        f'<span class="v alt"><b>{fmt(c, pct)}</b><small>候補</small></span></div>')
    rows = []
    for i, r in enumerate(tab[:12]):
        rows.append(f'<tr class="{"cur" if r["current"] else ("cand" if r.get("candidate") else "")}"><td class="n">{i + 1}</td><td>{E(pol_label(r))}{" <span class=chip>今の設定</span>" if r["current"] else ""}{" <span class=chip2>候補</span>" if r.get("candidate") else ""}{" <span class=out>線の外</span>" if r.get("quiet_enough") is False else ""}</td>'
                    f'<td class="n">{fmt(r["hit_rate"], True)}</td><td class="n">{r["median_delay_min"]}<small class="mx"> / {r.get("max_delay_min", "–")}</small></td>'
                    f'<td class="n">{r["false_per_hour"]}</td><td class="n">{r["per_hour"]}</td></tr>')
    rank_cur = tab.index(cur) + 1
    if rank_cur > 12:
        r = cur
        rows.append(f'<tr class="cur"><td class="n">{rank_cur}</td><td>{E(pol_label(r))} <span class=chip>今の設定</span>{" <span class=out>線の外</span>" if r.get("quiet_enough") is False else ""}</td>'
                    f'<td class="n">{fmt(r["hit_rate"], True)}</td><td class="n">{r["median_delay_min"]}<small class="mx"> / {r.get("max_delay_min", "–")}</small></td>'
                    f'<td class="n">{r["false_per_hour"]}</td><td class="n">{r["per_hour"]}</td></tr>')
    seen, strips = set(), []
    for s in b["strips"]:
        base = s["timeline"].split("#")[0]
        if base in seen: continue
        seen.add(base)
        strips.append(f'<div class="strip"><div class="sname">{E(base)}</div>{strip_svg(s)}</div>')
    a = b["assumptions"]
    return f'''
<div class="kpis">
  {kpi("偏りの時間帯で出た割合", cur["hit_rate"], best["hit_rate"], True)}
  {kpi("出るまでの時間（中央値・分）", cur["median_delay_min"], best["median_delay_min"])}
  {kpi("偏っていないのに出た（回/時）", cur["false_per_hour"], best["false_per_hour"])}
  {kpi("お知らせの回数（回/時）", cur["per_hour"], best["per_hour"])}
</div>
<p class="note">候補：<b>{E(pol_label(best))}</b>（{tab.index(best) + 1}位）。1位とほぼ同じ成績（拾えた割合・誤報が同じ、回数が1位の1.1倍以内）の中で、今の設定から変える所がいちばん少ないものを選んだ。並べ方：まず「偏っていないのに出る {a.get("max_false_per_hour", "–")}回/時以下・お知らせ {a.get("max_per_hour", "–")}回/時以下・出るまで 中央値{a.get("max_delay_min", "–")}分以内・最長{a.get("max_worst_delay_min", "–")}分以内」の線（仮）を満たすもの → その中で拾えた割合が高い → 回数が少ない → 早い。線を満たさない設定には「線の外」と付けた。</p>
<h3>時間の流れで見る（各パターンの1本目）</h3>
<div class="legend"><span><i class="lg truth"></i>本当に偏っていた時間帯</span><span><i class="lg cur"></i>今の設定のお知らせ（上段）</span><span><i class="lg best"></i>候補のお知らせ（下段）</span></div>
<div class="strips">{"".join(strips)}</div>
<h3>216通りの上位</h3>
<div class="tw"><table><thead><tr><th>#</th><th>設定</th><th>拾えた割合</th><th>出るまで（中央値 / 最長・分）</th><th>誤報（回/時）</th><th>回数（回/時）</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<p class="note">前提：{E(a["truth"])}。区間{a["segment_sec"]}秒・処理の遅れ{a["delay_sec"]}秒・お知らせは{a["dismiss_after_sec"]}秒で閉じると仮定。</p>'''


def section_tray(t):
    if not t:
        return ('<p class="empty">まだ結果がありません。Macで <code>python sim/sim_cards.py</code>（APIを使う）→ '
                '<code>python sim/sim_tray.py</code> の順に実行すると、ここに入ります。</p>')
    hdr = ["設定", "置き方", "10分あたり", "トレイ最大", "置かれず入替", "ちがい", "問い", "まとまり", "ひとこと", "引用が原文どおり", "キーワードの字が原文に"]
    keys = ["per_10min", "max_tray", "swap_rate", "share_difference", "share_question", "share_agreement", "share_lone", "quote_grounded", "keyword_char_grounded"]
    pct = {"swap_rate", "share_difference", "share_question", "share_agreement", "share_lone", "quote_grounded", "keyword_char_grounded"}
    pl = {"none": "誰も置かない", "place1": "毎回1枚", "place2": "毎回2枚"}
    VL = {"plan3-10_max6": "3分→10分・最大6枚（今）", "plan3-10_max4": "3分→10分・最大4枚", "plan3-10_max8": "3分→10分・最大8枚",
          "plan3-10_max6_noprio": "3分→10分・最大6枚・問い/ちがい優先なし", "plan3_max6": "3分ごと・最大6枚",
          "plan5_max6": "5分ごと・最大6枚", "plan10_max6": "10分ごと・最大6枚"}
    order = list(VL)
    rows_ = sorted(t["summary"], key=lambda r: (order.index(r["variant"]) if r["variant"] in order else 99, list(pl).index(r["placement"]) if r["placement"] in pl else 9))
    f1 = lambda k, v: fmt(v, True) if k in pct else ("–" if v is None else f"{v:.1f}")
    body = "".join(f'<tr class="{"cur" if r["variant"] == "plan3-10_max6" else ""}"><td>{E(VL.get(r["variant"], r["variant"])) if r["placement"] == "none" else ""}</td><td class="nw">{pl.get(r["placement"], r["placement"])}</td>'
                   + "".join(f'<td class="n">{f1(k, r[k])}</td>' for k in keys) + "</tr>" for r in rows_)
    return f'<div class="tw"><table><thead><tr>{"".join(f"<th>{h}</th>" for h in hdr)}</tr></thead><tbody>{body}</tbody></table></div>'


def section_gold(g):
    if not g:
        return '<p class="empty">まだ正解がありません。<code>sim/gold/README.md</code> を見て2〜3班ぶん付けたあと、<code>python sim/eval_gold.py</code> を実行してください。</p>'
    cols = [("topic_any", "#2 話題を拾えた"), ("topic_delay_min", "#2 話題→カード（分）"),
            ("difference_any", "#3 意見の分かれ目（内容）"), ("difference_kind", "#3 同（種類も「ちがい」）"),
            ("question_any", "#4 残った問い"), ("agreement_any", "#4 決まったこと")]
    body = "".join(f'<tr class="{"cur" if r["variant"] == "plan3-10_max6" else ""}"><td>{E(r["variant"])}</td>'
                   + "".join(f'<td class="n">{fmt(r.get(k), not k.endswith("_min"))}</td>' for k, _ in cols) + "</tr>" for r in g["summary"])
    return (f'<div class="tw"><table><thead><tr><th>設定</th>{"".join(f"<th>{h}</th>" for _, h in cols)}</tr></thead><tbody>{body}</tbody></table></div>'
            f'<p class="note">「拾えた」＝その場面の終わりから{g.get("lag_min", 15)}分以内に、文字の重なり（バイグラムJaccard）≥{g.get("similarity_threshold", 0.2)}のカードが届いた。粗い判定なので gold_results.json の examples で目で確かめること。</p>')


CSS = """
:root{--bg:#F3F5F8;--panel:#FFFFFF;--ink:#1D2733;--ink-2:#5A6778;--line:#D5DBE3;--navy:#1C3A5E;--accent:#2a78d6;--accent-2:#eb6834;--band:#C9D1DB;--soft:#EAF1FB;color-scheme:light}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121820;--panel:#1B232E;--ink:#E6EBF1;--ink-2:#9AA7B8;--line:#334052;--navy:#0F2238;--accent:#3987e5;--accent-2:#d95926;--band:#3A4656;--soft:#1E2F4A;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#121820;--panel:#1B232E;--ink:#E6EBF1;--ink-2:#9AA7B8;--line:#334052;--navy:#0F2238;--accent:#3987e5;--accent-2:#d95926;--band:#3A4656;--soft:#1E2F4A;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"Zen Kaku Gothic New","Hiragino Sans","Yu Gothic",sans-serif;font-size:14px;line-height:1.6}
.wrap{max-width:1040px;margin:0 auto;padding-block:24px 40px;padding-inline:16px;display:flex;flex-direction:column;gap:20px}
header h1{margin:0;font-size:22px;text-wrap:balance}
header p{margin:4px 0 0;color:var(--ink-2)}
section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px 20px}
section h2{margin:0 0 4px;font-size:17px}
section h3{margin:18px 0 8px;font-size:14px;color:var(--ink-2);font-weight:600}
.sub{margin:0 0 12px;color:var(--ink-2);font-size:13px}
.def{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:8px}
.def div{border:1px solid var(--line);border-radius:8px;padding:10px 12px;display:flex;gap:10px;align-items:flex-start}
.def b.no{font-variant-numeric:tabular-nums;color:var(--ink-2)}
.def .st{margin-left:auto;font-size:11px;border-radius:999px;padding:1px 8px;white-space:nowrap;border:1px solid var(--line);color:var(--ink-2)}
.def .st.ok{background:var(--soft);color:var(--accent);border-color:transparent}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}
.kpi{border:1px solid var(--line);border-radius:10px;padding:10px 12px;display:grid;grid-template-columns:1fr 1fr;gap:4px}
.kpi .k{grid-column:1/-1;font-size:12px;color:var(--ink-2)}
.kpi .v b{font-size:22px;font-variant-numeric:tabular-nums}
.kpi .v small{display:block;font-size:11px;color:var(--ink-2)}
.kpi .v.alt b{color:var(--ink-2)}
.note{font-size:12px;color:var(--ink-2);margin:10px 0 0}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12px;color:var(--ink-2);margin-bottom:6px}
.legend span{display:flex;align-items:center;gap:6px}
.lg{display:inline-block;width:14px;height:10px;border-radius:2px}
.lg.truth{background:var(--band)} .lg.cur{background:var(--accent);width:3px} .lg.best{background:var(--accent-2);width:3px}
.strips{display:flex;flex-direction:column;gap:6px}
.strip{display:grid;grid-template-columns:200px 1fr;gap:10px;align-items:center}
.sname{font-size:12px}
.strip svg{width:100%;height:auto;display:block}
.track{fill:none;stroke:var(--line)} .truth{fill:var(--band)}
.m-cur{stroke:var(--accent);stroke-width:2} .m-best{stroke:var(--accent-2);stroke-width:2}
.tick{font-size:9px;fill:var(--ink-2)}
@media (max-width:640px){.strip{grid-template-columns:1fr}}
.tw{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{font-weight:600;color:var(--ink-2);white-space:nowrap}
td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.cur td{background:var(--soft)}
.chip{font-size:10.5px;background:var(--accent);color:#fff;border-radius:999px;padding:0 7px;margin-left:4px;white-space:nowrap}
.out{font-size:10.5px;border:1px solid var(--line);color:var(--ink-2);border-radius:999px;padding:0 7px;margin-left:4px;white-space:nowrap}
.mx{color:var(--ink-2)}
td.nw{white-space:nowrap}
.chip2{font-size:10.5px;background:var(--accent-2);color:#fff;border-radius:999px;padding:0 7px;margin-left:4px;white-space:nowrap}
tr.cand td{background:color-mix(in srgb,var(--accent-2) 10%,transparent)}
.empty{margin:0;padding:14px;border:1px dashed var(--line);border-radius:8px;color:var(--ink-2)}
code{font-size:12px;background:var(--bg);padding:1px 5px;border-radius:4px}
ul.lim{margin:0;padding-left:20px;color:var(--ink-2);font-size:13px}
"""


def main():
    frag = "--fragment" in sys.argv
    b, t, g = load("balance_results.json"), load("tray_results.json"), load("gold_results.json")
    st = lambda done, todo: f'<span class="st ok">{done}</span>' if done else f'<span class="st">{todo}</span>'
    defs = [("1", "みんなの声が場に出ている", "発話量・参加促進", st(b, "未実行")),
            ("2", "今なにを話しているか分かる", "議論構造・整理", st(g, "正解待ち")),
            ("3", "違う見方が拾われる", "他者の意見", st(g, "正解待ち")),
            ("4", "次に進むきっかけがある", "進行", st(g, "正解待ち")),
            ("5", "話し合いの邪魔をしない", "安心・負担", st(t and b, "カード未実行")),
            ("6", "AIが勝手に足さない", "補完であって代替でない", st(t, "カード未実行"))]
    defs = [(n, a, c, s.replace(">None<", ">測定済み<").replace(">True<", ">測定済み<")) for n, a, c, s in defs]
    defs = [(n, a, c, s if "ok" not in s else '<span class="st ok">測定済み</span>') for n, a, c, s in defs]
    body = f"""
<div class="wrap">
<header><h1>GraRec インターフェース比較</h1>
<p>「いいグループワーク」の6つの状態を物差しに、設定を総当たりで比べたオフライン・シミュレーションの結果（{datetime.now():%Y-%m-%d %H:%M} 作成）。画面にこう出るはず、までを見ていて、参加者が実際に気づいたかは分からない。</p></header>
<section><h2>物差し：いいグループワークの6つの状態</h2>
<div class="def">{"".join(f'<div><b class="no">#{n}</b><span>{E(a)}<br><small style="color:var(--ink-2)">{E(c)}</small></span>{s}</div>' for n, a, c, s in defs)}</div></section>
<section><h2>#1・#5 話のバランスとお知らせ</h2><p class="sub">合成シナリオ6種×3回と実録音で、お知らせの設定216通りを比べた。</p>{section_balance(b)}</section>
<section><h2>#3〜#6 カードとトレイ</h2><p class="sub">7/15の文字起こし（7班）を、今の /kadai と同じ手順でカードにした結果。</p>{section_tray(t)}</section>
<section><h2>#2〜#4 正解との照合</h2><p class="sub">mokoが付けた正解（話題・意見が分かれた場面・残った問い・決まったこと）を、カードでどれだけ拾えたか。</p>{section_gold(g)}</section>
<section><h2>読むときの注意</h2><ul class="lim">
<li>7/15は量子力学・デザイン思考の授業で、録音に15〜20%の抜けがある</li>
<li>「本当の偏り」は分析用に決めた物差しで、それに近い判定の設定ほど有利になる</li>
<li>合成シナリオは乱数で作った話し方。実録音の話者は pyannote の推定で、正解ではない</li>
<li>人がカードを置くかどうか、お知らせを何秒で閉じるかは仮定</li>
<li>これは研究の結果ではなく、学内実験の前に設定を絞り込むための材料</li></ul></section>
</div>"""
    page = f"<title>GraRec インターフェース比較</title>\n<style>{CSS}</style>\n{body}"
    if not frag:
        page = f'<!DOCTYPE html>\n<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">{page.split(chr(10),2)[0]}<style>{CSS}</style></head><body>{body}</body></html>'
    name = "dashboard_fragment.html" if frag else "dashboard.html"
    open(os.path.join(OUT, name), "w", encoding="utf-8").write(page)
    print("保存:", os.path.join(OUT, name))


if __name__ == "__main__":
    main()
