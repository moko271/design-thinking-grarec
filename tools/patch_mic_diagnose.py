# -*- coding: utf-8 -*-
"""
kadai.html：録音できないときに「なぜできないか」を出し分けて、記録にも残す（2026-10-27）
  背景：生徒のPCで「マイクが使えません」と出たが、理由が記録に残っておらず原因が分からなかった
  ・マイクが取れないときの理由ごとに、やることを書いたお知らせを出す
      ブロックされている／マイクがない／ほかのアプリが使っている／アプリ内の画面などで使えない／録音に対応していないブラウザ
  ・記録：mic_fail（エラーの種類、ブラウザ、安全な接続か、別ページの中に埋め込まれているか、マイクの許可の状態）
  ・マイクは取れたが録音の部品で失敗したときも、止まったままにせずお知らせを出してマイクを放す
  ・声の登録でも同じ理由を出す
何度実行しても同じ結果になる。
"""
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "static/kadai.html"
s = open(p, encoding="utf-8").read()
if "function micProblem" in s:
    print("already patched"); sys.exit(0)


def rep(old, new):
    global s
    assert s.count(old) == 1, old[:80]
    s = s.replace(old, new)


rep('''async function startRecording(){
  try {
    if (!stream) stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}});
  } catch (e) {
    banner('マイクが使えません。ブラウザのマイクの許可を確認してください。');
    return;
  }
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();''', '''// マイクが取れなかった理由を見分けて、やることを返す（記録にも残す）
function micProblem(e){
  const name = (e && e.name) || (e && e.message) || String(e);
  const inFrame = (() => { try { return window.self !== window.top; } catch (_) { return true; } })();
  let msg;
  if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia)
    msg = 'この画面ではマイクを使えません。アプリの中で開いている場合は、ChromeかEdgeでURLを直接開き直してください。';
  else if (typeof MediaRecorder === 'undefined')
    msg = 'このブラウザは録音に対応していません。ChromeかEdgeの新しい版で開いてください。';
  else if (inFrame && /NotAllowed|Security/.test(name))
    msg = '別のページの中に埋め込まれているため、マイクを使えません。GraRecのURLを直接開いてください。';
  else if (/NotAllowed|Security|PermissionDenied/.test(name))
    msg = 'マイクがブロックされています。アドレスバー左のマーク → マイク →「許可」にして、ページを再読み込みしてください。それでもだめなら、パソコンの設定（プライバシー → マイク）でブラウザがオンになっているか確認してください。';
  else if (/NotFound|Overconstrained|DevicesNotFound/.test(name))
    msg = 'マイクが見つかりません。マイク付きのイヤホンをつなぐか、別のパソコンで録音してください。';
  else if (/NotReadable|TrackStart|Abort/.test(name))
    msg = 'ほかのアプリ（Zoom・Teams など）がマイクを使っている可能性があります。閉じてから、もう一度「録音をはじめる」を押してください。';
  else
    msg = 'マイクが使えません（' + name + '）。ブラウザとパソコンのマイクの許可を確認してください。';
  const info = {name: String(name).slice(0, 60), msg: String(e && e.message || '').slice(0, 120), secure: window.isSecureContext,
                in_frame: inFrame, has_md: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
                has_mr: typeof MediaRecorder !== 'undefined', ua: navigator.userAgent.slice(0, 160)};
  const done = perm => { log('mic_fail', null, {...info, perm}); save(); };
  try { navigator.permissions.query({name: 'microphone'}).then(r => done(r.state), () => done('unknown')); } catch (_) { done('unknown'); }
  return msg;
}
async function startRecording(){
  try {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === 'undefined') throw new Error('unsupported');
    if (!stream) stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}});
  } catch (e) {
    banner(micProblem(e));
    return;
  }
  try { recorder = newRecorder(); } catch (e) {
    stream.getTracks().forEach(t => t.stop()); stream = null; recorder = null;
    banner(micProblem(e));
    return;
  }
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();''')
rep('''  if (!S.recStartedAt) S.recStartedAt = Date.now();
  recorder = newRecorder(); recorder.start();''', '''  if (!S.recStartedAt) S.recStartedAt = Date.now();
  recorder.start();''')
rep('''  catch (e) { sl.err = 'マイクが使えません。ブラウザの許可を確認してください'; renderVoice(); return; }''',
    '''  catch (e) { sl.err = micProblem(e); renderVoice(); return; }''')

open(p, "w", encoding="utf-8").write(s)
print("patched")
