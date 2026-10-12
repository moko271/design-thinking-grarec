# -*- coding: utf-8 -*-
"""
kadai.html：学内実験の分析に必要な記録を足す（2026-10-12）
  1. 区間ごとに録音の開始・終了時刻（seg_start / seg_end, ミリ秒）を残す
     → transcript・speech の各要素に入る。話者分離の segments[].start/end は区間の頭からの秒数なので、
        seg_start + start*1000 で絶対時刻になる
  2. 文字起こしの失敗・話者分離の失敗・無音で送らなかった区間を events に残す（seg_fail / seg_nodiarize / seg_empty）
  3. お知らせは「判定が変わった時刻」と「実際に画面に出た／消えた時刻」を分けて残す
       balance_judge  … 判定が wait/calm/off/single の間で変わった（from, to, b, shares）
       balance_notice … お知らせが実際に画面に出た（judged_at = off と判定された時刻）
       balance_hidden … 画面から消えた（reason: recovered / dismissed）
       balance_dismiss… 「閉じる」が押された（従来どおり）
     さらに 15秒ごとの判定を S.balTrace に残す（t, state, b, shares）
  4. 声を1人しか登録していないときに「いい感じ」と出てしまう問題を直す（2人以上で判定）
何度実行しても同じ結果になる（すでに入っていれば何もしない）。
"""
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "static/kadai.html"
s = open(p, encoding="utf-8").read()
if "balance_judge" in s:
    print("already patched"); sys.exit(0)


def rep(old, new, n=1):
    global s
    c = s.count(old)
    assert c == n, (c, old[:80])
    s = s.replace(old, new)


# --- 状態に balTrace を足す ---
rep("""  speech: [],           // 区間ごとの話した秒数 {t: 区間の終わり(ms), sec: {v1: 12.3, other: 4}} → 発話量均衡度の計算に使う""",
    """  speech: [],           // 区間ごとの話した秒数 {t: 届いた時刻(ms), seg_start, seg_end: 録音の開始・終了(ms), sec: {v1: 12.3, other: 4}}
  balTrace: [],         // 研究用：15秒ごとのバランス判定 {t, state, b, shares}""")
rep("""const keys = ['session_id','group_number','kadai_no','task_hint','leader_id','members','cards','events','transcript','plan','cycle','lastExtracted','pendingText','created_at','voice','speech','recStartedAt'];""",
    """const keys = ['session_id','group_number','kadai_no','task_hint','leader_id','members','cards','events','transcript','plan','cycle','lastExtracted','pendingText','created_at','voice','speech','balTrace','recStartedAt'];""")

# --- 1. 区間の開始・終了時刻 ---
rep("""  const r = new MediaRecorder(stream, mime ? {mimeType: mime} : undefined);
  const chunks = [];""",
    """  const r = new MediaRecorder(stream, mime ? {mimeType: mime} : undefined);
  r._t0 = Date.now();                                          // 研究用：この区間の録音開始時刻
  const chunks = [];""")
rep("""  r.onstop = () => {
    const blob = new Blob(chunks, {type: r.mimeType || 'audio/webm'});
    if (blob.size < 2000) return;                               // ほぼ無音・極短
    pendingTranscribes++; paintRec();
    transcribeChain = transcribeChain.then(() => transcribe(blob)).finally(() => { pendingTranscribes--; paintRec(); });""",
    """  r.onstop = () => {
    const t0 = r._t0, t1 = Date.now();
    const blob = new Blob(chunks, {type: r.mimeType || 'audio/webm'});
    if (blob.size < 2000){ log('seg_empty', null, {seg_start: t0, seg_end: t1, bytes: blob.size}); return; }   // ほぼ無音・極短
    pendingTranscribes++; paintRec();
    transcribeChain = transcribeChain.then(() => transcribe(blob, t0, t1)).finally(() => { pendingTranscribes--; paintRec(); });""")
rep("""async function transcribe(blob){
  const fd = new FormData();""",
    """async function transcribe(blob, segStart, segEnd){
  const fd = new FormData();""")
rep("""        S.speech.push({t: Date.now(), sec: data.speech_sec || {}});
        paintBalance();
      } else if (S.voice.enabled && data.warning && !warnedDiarize){ warnedDiarize = true; banner('話のバランスを一部計算できませんでした（カードはそのまま届きます）'); }
      if (text){ S.transcript.push({t: nowISO(), text, segments: data.segments || []}); S.pendingText += (S.pendingText ? '\\n' : '') + text; save(); }
      return;
    } catch (e) {
      if (attempt === 1){ console.warn('transcribe', e); banner('音声の文字起こしに失敗しました。録音は続いています。ネットワークを確認してください。'); }""",
    """        S.speech.push({t: Date.now(), seg_start: segStart, seg_end: segEnd, sec: data.speech_sec || {}});
        paintBalance();
      } else if (S.voice.enabled){
        log('seg_nodiarize', null, {seg_start: segStart, seg_end: segEnd, warning: data.warning || ''});
        if (data.warning && !warnedDiarize){ warnedDiarize = true; banner('話のバランスを一部計算できませんでした（カードはそのまま届きます）'); }
      }
      if (text){ S.transcript.push({t: nowISO(), seg_start: segStart, seg_end: segEnd, diarized: !!data.diarized, text, segments: data.segments || []}); S.pendingText += (S.pendingText ? '\\n' : '') + text; }
      save();
      return;
    } catch (e) {
      if (attempt === 1){ console.warn('transcribe', e); log('seg_fail', null, {seg_start: segStart, seg_end: segEnd, error: String(e && e.message || e).slice(0, 120)}); save(); banner('音声の文字起こしに失敗しました。録音は続いています。ネットワークを確認してください。'); }""")

# --- 3・4. バランスの判定と表示を分けて残す／1人だけ登録のとき ---
rep("""let nudgeSnoozeUntil = 0, lastBalState = '';""",
    """let nudgeSnoozeUntil = 0, lastBalState = '', nudgeShown = false, offSince = null;""")
rep("""  const msg = $('bmsg');
  const state = !r.ready ? 'wait' : r.off ? 'off' : 'calm';
  if (state === 'off' && lastBalState !== 'off') log('balance_notice', null, {b: r.b === null ? null : +r.b.toFixed(3), quiet: r.quiet});
  lastBalState = state;
  if (state === 'wait'){ msg.className = 'bmsg wait'; msg.textContent = 'もう少し話すと、バランスが見えてきます'; }
  else if (state === 'calm'){ msg.className = 'bmsg calm'; msg.textContent = 'いい感じに、みんなの声が入っています'; }
  else if (Date.now() < nudgeSnoozeUntil){ msg.className = ''; msg.textContent = ''; }
  else {""",
    """  const msg = $('bmsg');
  const state = r.reg.length < 2 ? 'single' : !r.ready ? 'wait' : r.off ? 'off' : 'calm';
  // 研究用：判定（state）と、実際に画面に出したか（nudgeShown）を分けて残す
  const shares = {};
  r.reg.forEach(v => shares[v] = r.regTotal ? +(r.sum[v] / r.regTotal).toFixed(3) : 0);
  const bval = r.b === null ? null : +r.b.toFixed(3);
  if (state !== lastBalState){
    log('balance_judge', null, {from: lastBalState || null, to: state, b: bval, quiet: r.quiet, shares});
    offSince = state === 'off' ? nowISO() : null;
  }
  lastBalState = state;
  if (recording) S.balTrace.push({t: Date.now(), state, b: bval, shares});
  const showNudge = state === 'off' && Date.now() >= nudgeSnoozeUntil;
  if (showNudge && !nudgeShown){ nudgeShown = true; log('balance_notice', null, {b: bval, quiet: r.quiet, shares, judged_at: offSince}); save(); }
  else if (!showNudge && nudgeShown){ nudgeShown = false; log('balance_hidden', null, {reason: state === 'off' ? 'dismissed' : 'recovered', to: state}); save(); }
  if (state === 'single'){ msg.className = 'bmsg wait'; msg.textContent = '2人以上の声を登録すると、話のバランスが見えてきます'; }
  else if (state === 'wait'){ msg.className = 'bmsg wait'; msg.textContent = 'もう少し話すと、バランスが見えてきます'; }
  else if (state === 'calm'){ msg.className = 'bmsg calm'; msg.textContent = 'いい感じに、みんなの声が入っています'; }
  else if (!showNudge){ msg.className = ''; msg.textContent = ''; }
  else {""")

# --- 新しいセッションで balTrace を空にし、設定値を記録する ---
rep("""    voice: {enabled: false, slots: {}}, speech: [], recStartedAt: 0});
  log('session_start');""",
    """    voice: {enabled: false, slots: {}}, speech: [], balTrace: [], recStartedAt: 0});
  lastBalState = ''; nudgeShown = false; offSince = null; nudgeSnoozeUntil = 0;
  log('session_start', null, {config: {quick: QUICK, segment_ms: SEGMENT_MS, max_cards: MAX_CARDS, plan: S.plan,
    bal_window_ms: BAL_WINDOW, bal_threshold: BAL_THRESHOLD, quiet_share: QUIET_SHARE, bal_min_speech: BAL_MIN_SPEECH,
    bal_warmup_ms: BAL_WARMUP, nudge_snooze_ms: NUDGE_SNOOZE, ua: navigator.userAgent.slice(0, 120)}});""")

open(p, "w", encoding="utf-8").write(s)
print("patched")
