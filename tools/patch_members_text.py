# -*- coding: utf-8 -*-
"""
kadai.html：見出し（課題番号・リーダ学番・チームメンバ名）を、PowerPointの表と同じように打てるようにする（2026-10-13）
  ・チームメンバ名は1つの文字の欄。クリックしてそのまま打つ（例：田中、三枝、松井）。区切りは「、」「,」や空白
  ・リーダ／オブザーバの色は、PowerPointと同じく「名前を選ぶ → 色を選ぶ」。文字を選ぶと小さな道具（赤＝リーダ／青＝オブザーバ／黒＝メンバ）が出る
  ・Enterで確定（日本語の変換中のEnterは無視）。貼り付けは文字だけ
  ・課題番号・学籍番号も、点線をやめて表の中の文字と同じ見た目に（マウスを乗せると枠が出る）
  ・保存・書き出しの形（members: [{name, role}]）は今までと同じ。名前ごとの役割は、その名前の1文字目の色で決める
  ・記録：members_edit（打ち終わったとき）／members_role（色を変えたとき）
何度実行しても同じ結果になる。
"""
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "static/kadai.html"
s = open(p, encoding="utf-8").read()
if "readMembers" in s:
    print("already patched"); sys.exit(0)


def rep(old, new):
    global s
    assert s.count(old) == 1, old[:80]
    s = s.replace(old, new)


rep(".s-title input{width:5.4cqw;border:0;border-bottom:1px dashed #999;font:inherit;background:transparent;color:inherit;padding:0}",
    ".s-title input{width:5.4cqw;border:0;font:inherit;background:transparent;color:inherit;padding:0;border-radius:2px;outline:1px solid transparent}\n"
    ".s-title input:hover,.s-td input:hover{outline:1px solid #9AA6B4}\n.s-title input:focus,.s-td input:focus{outline:1.5px solid var(--accent);background:rgba(255,255,255,.7)}")
rep(".s-td input{width:100%;border:0;background:transparent;font:inherit;color:#111;text-align:center}",
    ".s-td input{width:100%;border:0;background:transparent;font:inherit;color:#111;text-align:center;border-radius:2px;outline:1px solid transparent}\n.s-td input::placeholder{color:#8a8a8a}")
rep("/* usage log */", """/* チームメンバ名（PowerPointの表のセルと同じく、文字をそのまま打つ） */
.mtext{flex:1;min-width:0;align-self:stretch;display:flex;align-items:center;flex-wrap:wrap;outline:none;font-size:1.6cqw;font-weight:700;color:#111;line-height:1.45;cursor:text;white-space:pre-wrap;border-radius:2px;padding:0 .3cqw}
.mtext:hover{box-shadow:0 0 0 1px #9AA6B4}
.mtext:focus{box-shadow:0 0 0 1.5px var(--accent);background:rgba(255,255,255,.7)}
.mtext:empty::before{content:attr(data-ph);color:#8a8a8a;font-weight:400}
.mbar{position:fixed;z-index:200;display:flex;gap:4px;background:#1D2733;border-radius:8px;padding:4px;box-shadow:0 4px 14px rgba(0,0,0,.3)}
.mbar button{border:0;border-radius:5px;padding:4px 9px;font-size:12px;font-weight:700;background:#fff;color:#111}
.mbar button.r{color:#FF0000}.mbar button.b{color:#0070C0}

/* usage log */""")

start = s.index("function renderMembers(){")
end = s.index("// ================= カード =================")
new_block = r"""const ROLE_COLOR = {leader: '#FF0000', observer: '#0070C0'};
const MEMBER_SEP = /[、，,\s　]/;
const composing = e => e.isComposing || e.keyCode === 229;   // 日本語の変換中
function renderMembers(){
  const box = $('members');
  let el = $('memberText');
  if (!el){
    box.innerHTML = '';
    el = document.createElement('div'); el.id = 'memberText'; el.className = 'mtext';
    el.contentEditable = 'true'; el.spellcheck = false;
    el.dataset.ph = 'クリックして名前を入力（例：田中、三枝、松井）';
    el.setAttribute('role', 'textbox'); el.setAttribute('aria-label', 'チームメンバ名');
    box.appendChild(el); bindMemberText(el);
  }
  if (document.activeElement === el) return;                    // 打っている途中は書き換えない
  el.innerHTML = '';
  S.members.filter(m => m.name).forEach((m, i) => {
    if (i) el.appendChild(document.createTextNode('、'));
    const sp = document.createElement('span'); sp.textContent = m.name;
    if (ROLE_COLOR[m.role]) sp.style.color = ROLE_COLOR[m.role];
    el.appendChild(sp);
  });
}
function readMembers(el){
  // 文字ごとの色を見て、区切りで分けた名前ごとに役割を決める（名前の1文字目の色）
  const roleOf = col => { const c = (col || '').replace(/\s/g, ''); return c === 'rgb(255,0,0)' ? 'leader' : c === 'rgb(0,112,192)' ? 'observer' : ''; };
  const out = []; let cur = '', role = null;
  const flush = () => { const n = cur.trim(); if (n) out.push({name: n.slice(0, 20), role: role || ''}); cur = ''; role = null; };
  const walk = node => node.childNodes.forEach(n => {
    if (n.nodeType === 3){
      const col = getComputedStyle(n.parentElement).color;
      for (const ch of n.textContent){ if (MEMBER_SEP.test(ch)) flush(); else { if (role === null) role = roleOf(col); cur += ch; } }
    } else if (n.nodeName === 'BR' || n.nodeName === 'DIV') { flush(); if (n.nodeName === 'DIV') walk(n); }
    else walk(n);
  });
  walk(el); flush();
  return out.slice(0, 12);
}
let memberSaveTimer = null;
function bindMemberText(el){
  el.addEventListener('input', () => {
    S.members = readMembers(el);
    clearTimeout(memberSaveTimer); memberSaveTimer = setTimeout(save, 800);
  });
  el.addEventListener('keydown', e => { if (e.key === 'Enter' && !composing(e)){ e.preventDefault(); el.blur(); } });
  el.addEventListener('paste', e => {
    e.preventDefault();
    document.execCommand('insertText', false, (e.clipboardData || window.clipboardData).getData('text/plain').replace(/\s*\n\s*/g, '、'));
  });
  el.addEventListener('blur', () => {
    S.members = readMembers(el);
    log('members_edit', null, {n: S.members.length, roles: S.members.map(m => m.role || '-').join('')});
    renderMembers(); save();
  });
}
// 名前を選ぶと出る色の道具（PowerPointの「文字の色」と同じ）
const mbar = document.createElement('div'); mbar.className = 'mbar'; mbar.id = 'mbar'; mbar.hidden = true;
mbar.innerHTML = '<button type="button" class="r" data-c="#FF0000">リーダ（赤）</button><button type="button" class="b" data-c="#0070C0">オブザーバ（青）</button><button type="button" data-c="#111111">メンバ（黒）</button>';
document.body.appendChild(mbar);
mbar.addEventListener('mousedown', e => e.preventDefault());       // 選んだ文字の選択を外さない
mbar.addEventListener('pointerdown', e => e.preventDefault());
mbar.querySelectorAll('button').forEach(b => b.onclick = () => {
  const el = $('memberText'); if (!el) return;
  document.execCommand('styleWithCSS', false, true);
  document.execCommand('foreColor', false, b.dataset.c);
  S.members = readMembers(el);
  log('members_role', null, {roles: S.members.map(m => m.role || '-').join('')});
  save(); mbar.hidden = true;
});
document.addEventListener('selectionchange', () => {
  const el = $('memberText'); const sel = getSelection();
  if (!el || !sel.rangeCount || sel.isCollapsed || !el.contains(sel.anchorNode) || !el.contains(sel.focusNode)){ mbar.hidden = true; return; }
  const r = sel.getRangeAt(0).getBoundingClientRect();
  mbar.hidden = false;
  mbar.style.left = Math.max(8, Math.min(r.left, innerWidth - mbar.offsetWidth - 8)) + 'px';
  mbar.style.top = Math.max(8, r.top - mbar.offsetHeight - 8) + 'px';
});

"""
s = s[:start] + new_block + s[end:]
# 最初のメンバーは空から（「名前」という赤い札を出さない）
s = s.replace("members: [{name:'', role:'leader'}],", "members: [],", 1)
s = s.replace("members: [{name: '', role: 'leader'}], leader_id: '',", "members: [], leader_id: '',", 1)
s = s.replace("。カードやメンバー名はダブルクリックで書き直せる';", "。カードはダブルクリックで書き直せる。メンバー名はクリックして打ち、名前を選ぶとリーダ（赤）・オブザーバ（青）にできる';", 1)
open(p, "w", encoding="utf-8").write(s)
print("patched")
