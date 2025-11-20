import streamlit as st
import pandas as pd
import io
import requests
from PIL import Image
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.lib.utils import ImageReader
import time
from pathlib import Path
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
import re

# ---- フォント設定（IPAex を優先、無ければCIDフォントへフォールバック）----
def _setup_font():
    here = Path(__file__).parent
    candidates = [
        here / "fonts" / "IPAexGothic.ttf",
        here / "IPAexGothic.ttf",
        Path.cwd() / "fonts" / "IPAexGothic.ttf",
        Path.cwd() / "IPAexGothic.ttf",
    ]
    for p in candidates:
        if p.exists():
            pdfmetrics.registerFont(TTFont("Japanese", str(p)))
            return "Japanese"
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    return "HeiseiKakuGo-W5"

JAPANESE_FONT = _setup_font()

st.set_page_config(page_title="🔍 学生指導用データベース", layout="wide")
st.title("🔍 QuoVadis歯科医師国家試験データベース")

# ===== 列名正規化 & 安全取得ユーティリティ =====
def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """BOM/空白/改行を除去し、よくある別名を正式名へ寄せる"""
    def _clean(s):
        s = str(s).replace("\ufeff", "")
        return re.sub(r"[\u3000 \t\r\n]+", "", s)
    df = df.copy()
    df.columns = [_clean(c) for c in df.columns]

    alias = {
        "問題文":  ["設問", "問題", "本文"],
        "選択肢1": ["選択肢Ａ","選択肢a","A","ａ"],
        "選択肢2": ["選択肢Ｂ","選択肢b","B","ｂ"],
        "選択肢3": ["選択肢Ｃ","選択肢c","C","ｃ"],
        "選択肢4": ["選択肢Ｄ","選択肢d","D","ｄ"],
        "選択肢5": ["選択肢Ｅ","選択肢e","E","ｅ"],
        "正解":    ["解答","答え","ans","answer"],
        "科目分類": ["分類","科目","カテゴリ","カテゴリー"],
        "リンクURL": ["画像URL","画像リンク","リンク","画像Link"],
    }
    colset = set(df.columns)
    for canon, cands in alias.items():
        if canon in colset:
            continue
        for c in cands:
            if c in colset:
                df.rename(columns={c: canon}, inplace=True)
                colset.add(canon)
                break
    return df

def safe_get(row: pd.Series | dict, keys, default=""):
    """Series/辞書から安全に値を取得（NaN, 空白, 別名を考慮）"""
    if isinstance(row, pd.Series):
        row = row.to_dict()
    for k in keys:
        if k in row:
            v = row.get(k)
            try:
                if pd.isna(v):
                    continue
            except Exception:
                pass
            s = str(v).strip() if v is not None else ""
            if s:
                return s
    return default

def ensure_output_columns(df: pd.DataFrame) -> pd.DataFrame:
    need = ["問題文","選択肢1","選択肢2","選択肢3","選択肢4","選択肢5","正解","科目分類","リンクURL"]
    out = df.copy()
    for c in need:
        if c not in out.columns:
            out[c] = ""
    return out

# ===== データ読み込み =====
# BOM 対策のため utf-8-sig、文字列で統一して取り込み
df = pd.read_csv("97_118DB.csv", dtype=str, encoding="utf-8-sig")
df = df.fillna("")
df = normalize_columns(df)

# ===== 検索 =====
query = st.text_input("問題文・選択肢・分類・画像リンク(URL)で検索:")
st.caption("💡 検索語を `&` でつなげるとAND検索（例: レジン & 硬さ）。URLの一部（例: http, drive.google）でも可。")

if not query:
    st.stop()

keywords = [kw.strip() for kw in query.split("&") if kw.strip()]

def row_text(r: pd.Series) -> str:
    # 🔸 ここを変更：リンク系カラムも検索対象に含める
    parts = [
        safe_get(r, ["問題文","設問","問題","本文"]),
        *[safe_get(r, [f"選択肢{i}"]) for i in range(1,6)],
        safe_get(r, ["正解","解答","答え"]),
        safe_get(r, ["科目分類","分類","科目"]),
        # 追加：URL/画像リンク
        safe_get(r, ["リンクURL","画像URL","画像リンク","リンク","画像Link"]),
    ]
    return " ".join([p for p in parts if p])

df_filtered = df[df.apply(
    lambda row: all(kw.lower() in row_text(row).lower() for kw in keywords),
    axis=1
)]
df_filtered = df_filtered.reset_index(drop=True)

st.info(f"{len(df_filtered)}件ヒットしました")

timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
file_prefix = f"{(query if query else '検索なし')}{timestamp}"

# ===== CSV ダウンロード =====
csv_buffer = io.StringIO()
ensure_output_columns(df_filtered).to_csv(csv_buffer, index=False)
st.download_button(
    label="📥 ヒット結果をCSVダウンロード",
    data=csv_buffer.getvalue(),
    file_name=f"{file_prefix}.csv",
    mime="text/csv"
)

# --------------------------------------------------------------------
# ▼▼▼ ここから追加（最小変更）：GoodNotes用CSVユーティリティ＋ボタン ▼▼▼

def _gn_clean(s: str) -> str:
    if s is None:
        return ""
    return str(s).replace("\ufeff", "").strip().replace("　", "")

def _gn_normalize_newlines(text: str, newline: str = "\n") -> str:
    """セル内の改行をLFに統一（必要なら CRLF へ再変換）"""
    if text is None:
        return ""
    t = re.sub(r"\r\n|\r", "\n", str(text))
    if newline == "\r\n":
        t = t.replace("\n", "\r\n")
    return t

def _gn_make_front_back(row: pd.Series,
                        numbering: str = "ABC",
                        add_labels: bool = True,
                        add_meta: bool = False) -> tuple[str, str]:
    q = _gn_clean(row.get("問題文", ""))

    choices = [
        _gn_clean(row.get("選択肢1", "")),
        _gn_clean(row.get("選択肢2", "")),
        _gn_clean(row.get("選択肢3", "")),
        _gn_clean(row.get("選択肢4", "")),
        _gn_clean(row.get("選択肢5", "")),
    ]
    labels = ["A","B","C","D","E"] if numbering == "ABC" else ["1","2","3","4","5"]
    choice_lines = [f"{labels[i]}. {_gn_normalize_newlines(txt)}" for i, txt in enumerate(choices) if txt]

    front = _gn_normalize_newlines(q)
    if choice_lines:
        front = front + "\n\n" + "\n".join(choice_lines)

    ans = _gn_clean(row.get("正解", ""))
    back = f"正解: {ans}" if add_labels else ans

    if add_meta:
        subject = _gn_clean(row.get("科目分類",""))
        link = _gn_clean(row.get("リンクURL",""))
        extra = "\n".join([s for s in (subject, link) if s])
        if extra:
            back = back + "\n\n" + _gn_normalize_newlines(extra)

    back = _gn_normalize_newlines(back)
    return front, back

def dataframe_to_goodnotes_bytes(df: pd.DataFrame,
                                 numbering: str = "ABC",
                                 add_labels: bool = True,
                                 add_meta: bool = False,
                                 overall_line_ending: str = "lf",
                                 quote_all: bool = False) -> bytes:
    """
    任意の DataFrame から GoodNotes 用 Front/Back CSV を UTF-8(BOM付き) bytes で返す。
    - セル内部の改行は LF に正規化（GoodNotesでの表示安定のため）
    - ファイル全体の改行は overall_line_ending で 'lf' or 'crlf'
    """
    # 必要列の担保（なければ空列を足す）
    base = ensure_output_columns(df)

    fronts, backs = [], []
    for _, row in base.iterrows():
        f, b = _gn_make_front_back(row, numbering=numbering, add_labels=add_labels, add_meta=add_meta)
        fronts.append(f); backs.append(b)

    out = pd.DataFrame({"Front": fronts, "Back": backs})

    # セル内部の改行をLFへ統一
    for c in out.columns:
        out[c] = out[c].map(lambda v: _gn_normalize_newlines(v, "\n"))

    # ファイルの行末
    file_nl = "\n" if overall_line_ending.lower() == "lf" else "\r\n"
    import csv as _csv  # 既存import汚染を避けるためローカル参照

    # pandasのStringIOではencoding引数が無視されるため、手動でBOMを書き込む
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM
    out.to_csv(
        buf,
        index=False,
        lineterminator=file_nl,
        quoting=_csv.QUOTE_ALL if quote_all else _csv.QUOTE_MINIMAL,
        doublequote=True,
        escapechar="\\",
    )
    return buf.getvalue().encode("utf-8")

# ▼ GoodNotesダウンロードボタン（既存CSVボタンの直下）
st.download_button(
    label="📥 GoodNotes用CSV（Front/Back）をダウンロード",
    data=dataframe_to_goodnotes_bytes(
        df_filtered,          # 検索結果をそのままFront/Back化
        numbering="ABC",      # "123"にしたい場合はここを変更
        add_labels=True,      # Back先頭に「正解: 」を付ける
        add_meta=False,       # Back末尾に 科目分類/リンクURL を追記するなら True
        overall_line_ending="lf",  # GoodNotesならLF推奨（Windows運用なら"crlf"も可）
    ),
    file_name=f"{file_prefix}_goodnotes.csv",
    mime="text/csv",
)
# --------------------------------------------------------------------

# ===== TXT 整形 =====
def convert_google_drive_link(url):
    if "drive.google.com" in url and "/file/d/" in url:
        try:
            file_id = url.split("/file/d/")[1].split("/")[0]
            return f"https://drive.google.com/uc?export=view&id={file_id}"
        except Exception:
            return url
    return url

def wrap_text(text: str, max_width: float, font_name: str, font_size: int):
    s = "" if text is None else str(text)
    if s == "":
        return [""]
    lines, buf = [], ""
    for ch in s:
        if stringWidth(buf + ch, font_name, font_size) <= max_width:
            buf += ch
        else:
            lines.append(buf)
            buf = ch
    if buf:
        lines.append(buf)
    return lines

def wrapped_lines(prefix: str, value: str, usable_width: float, font: str, size: int):
    return wrap_text(f"{prefix}{value}", usable_width, font, size)

def format_record_to_text(row: pd.Series) -> str:
    q = safe_get(row, ["問題文","設問","問題","本文"])
    parts = [f"問題文: {q}"]
    for i in range(1, 6):
        choice = safe_get(row, [f"選択肢{i}"])
        if choice:
            parts.append(f"選択肢{i}: {choice}")
    parts.append(f"正解: {safe_get(row, ['正解','解答','答え'])}")
    parts.append(f"分類: {safe_get(row, ['科目分類','分類','科目'])}")
    link = safe_get(row, ["リンクURL","画像URL","画像リンク","リンク","画像Link"])
    if link:
        parts.append(f"画像リンク: {convert_google_drive_link(link)}（PDFに画像表示）")
    return "\n".join(parts)

# ===== TXT ダウンロード =====
txt_buffer = io.StringIO()
for _, row in df_filtered.iterrows():
    txt_buffer.write(format_record_to_text(row))
    txt_buffer.write("\n\n" + "-"*40 + "\n\n")
st.download_button(
    label="📄 ヒット結果をTEXTダウンロード",
    data=txt_buffer.getvalue(),
    file_name=f"{file_prefix}.txt",
    mime="text/plain"
)

# ===== PDF 作成（ページ先頭は必ず問題文から／画像は必ず表示）=====
def create_pdf(records, progress=None, status=None, start_time=None):
    pdf_buffer = io.BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=A4)
    c.setFont(JAPANESE_FONT, 12)
    width, height = A4

    top_margin, bottom_margin = 40, 60
    left_margin, right_margin = 40, 40
    usable_width = width - left_margin - right_margin
    page_usable_h = (height - top_margin) - bottom_margin
    line_h = 18
    y = height - top_margin

    total = len(records)

    def fmt(sec):
        m = int(sec // 60); s = int(sec % 60)
        return f"{m:02d}:{s:02d}"

    def new_page():
        nonlocal y
        c.showPage()
        c.setFont(JAPANESE_FONT, 12)
        y = height - top_margin

    def draw_wrapped_lines(lines):
        nonlocal y
        for ln in lines:
            c.drawString(left_margin, y, ln)
            y -= line_h

    for idx, (_, row) in enumerate(records.iterrows(), start=1):
        q = safe_get(row, ["問題文","設問","問題","本文"])

        # 選択肢
        choices = []
        for i in range(1, 6):
            v = safe_get(row, [f"選択肢{i}"])
            if v:
                choices.append((i, v))

        ans = safe_get(row, ["正解","解答","答え"])
        cat = safe_get(row, ["科目分類","分類","科目"])

        # 画像の事前取得
        pil = None
        img_est_h = 0
        link_raw = safe_get(row, ["リンクURL","画像URL","画像リンク","リンク"])
        if link_raw:
            try:
                image_url = convert_google_drive_link(link_raw)
                resp = requests.get(image_url, timeout=5)
                pil = Image.open(io.BytesIO(resp.content)).convert("RGB")
                iw, ih = pil.size
                scale = min(usable_width / iw, page_usable_h / ih, 1.0)
                nw, nh = iw * scale, ih * scale
                img_est_h = nh + 20
            except Exception:
                pil = None
                img_est_h = wrapped_lines("", "[画像読み込み失敗]", usable_width, JAPANESE_FONT, 12)
                img_est_h = len(img_est_h) * line_h

        # 高さ見積り
        est_h = 0
        q_lines = wrapped_lines("問題文: ", q, usable_width, JAPANESE_FONT, 12)
        est_h += len(q_lines) * line_h
        choice_lines_list = []
        for i, v in choices:
            ls = wrapped_lines(f"選択肢{i}: ", v, usable_width, JAPANESE_FONT, 12)
            choice_lines_list.append(ls)
            est_h += len(ls) * line_h
        est_h += img_est_h if img_est_h else 0
        ans_lines = wrapped_lines("正解: ", ans, usable_width, JAPANESE_FONT, 12)
        cat_lines = wrapped_lines("分類: ", cat, usable_width, JAPANESE_FONT, 12)
        est_h += len(ans_lines) * line_h + len(cat_lines) * line_h + 20

        # ページ先頭を必ず問題文から
        if y - est_h < bottom_margin:
            new_page()

        # 描画
        draw_wrapped_lines(q_lines)
        for ls in choice_lines_list:
            draw_wrapped_lines(ls)

        if pil is not None:
            try:
                iw, ih = pil.size
                scale = min(usable_width / iw, page_usable_h / ih, 1.0)
                nw, nh = iw * scale, ih * scale
                if y - nh < bottom_margin:
                    new_page()
                remaining = y - bottom_margin
                if nh > remaining:
                    adj = remaining / nh
                    nw, nh = nw * adj, nh * adj
                img_io = io.BytesIO()
                pil.save(img_io, format="PNG")
                img_io.seek(0)
                img_reader = ImageReader(img_io)
                c.drawImage(img_reader, left_margin, y - nh, width=nw, height=nh, preserveAspectRatio=True, mask='auto')
                y -= nh + 20
            except Exception as e:
                err_lines = wrapped_lines("", f"[画像読み込み失敗: {e}]", usable_width, JAPANESE_FONT, 12)
                draw_wrapped_lines(err_lines)
        else:
            if link_raw:
                draw_wrapped_lines(wrapped_lines("", "[画像読み込み失敗]", usable_width, JAPANESE_FONT, 12))

        draw_wrapped_lines(ans_lines)
        draw_wrapped_lines(cat_lines)

        if y - 20 < bottom_margin:
            new_page()
        else:
            y -= 20

        if st.session_state.get("progress_on"):
            st.session_state["progress"].progress(min(idx / max(total, 1), 1.0))

    c.save()
    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()

# ===== PDF 生成 =====
if "pdf_bytes" not in st.session_state:
    st.session_state["pdf_bytes"] = None

if st.button("🖨️ PDFを作成（画像付き）"):
    st.session_state["progress_on"] = True
    st.session_state["progress"] = st.progress(0.0)
    start = time.time()
    with st.spinner("PDFを作成中…"):
        st.session_state["pdf_bytes"] = create_pdf(df_filtered)
    st.session_state["progress_on"] = False
    st.success("✅ PDF作成完了！")

if st.session_state["pdf_bytes"] is not None:
    st.download_button(
        label="📄 ヒット結果をPDFダウンロード",
        data=st.session_state["pdf_bytes"],
        file_name=f"{file_prefix}.pdf",
        mime="application/pdf"
    )

# ===== 画面の一覧（正解は初期非表示）=====
st.markdown("### 🔍 ヒットした問題一覧")
for i, (_, record) in enumerate(df_filtered.iterrows()):
    title = safe_get(record, ["問題文","設問","問題","本文"])
    with st.expander(f"{i+1}. {title[:50]}..."):
        st.markdown("### 📝 問題文")
        st.write(title)

        st.markdown("### ✏️ 選択肢")
        for j in range(1, 6):
            val = safe_get(record, [f"選択肢{j}"])
            if val:
                st.write(f"- {val}")

        show_ans = st.checkbox("正解を表示する", key=f"show_answer_{i}", value=False)
        if show_ans:
            st.markdown(f"**✅ 正解:** {safe_get(record, ['正解','解答','答え'])}")
        else:
            st.markdown("**✅ 正解:** |||（クリックで表示）|||")

        st.markdown(f"**📚 分類:** {safe_get(record, ['科目分類','分類','科目'])}")

        link = safe_get(record, ["リンクURL","画像URL","画像リンク","リンク"])
        if link:
            st.markdown(f"[画像リンクはこちら]({convert_google_drive_link(link)})")
        else:
            st.write("（画像リンクはありません）")

# デバッグ補助（必要時だけ展開）
#with st.expander("🔧 現在の列名（正規化後）"):
#   st.write(list(df.columns))
