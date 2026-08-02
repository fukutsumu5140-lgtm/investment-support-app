import base64
import json
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from dateutil import tz
import jquantsapi

# お気に入りの保存先（このリポジトリの favorites.json に保存する）
GITHUB_OWNER = "fukutsumu5140-lgtm"
GITHUB_REPO = "investment-support-app"
FAVORITES_PATH = "favorites.json"

st.set_page_config(page_title="投資サポートアプリ - 銘柄スクリーニング", layout="wide")
st.title("銘柄スクリーニング")
st.caption(
    "ステップ1：全銘柄を業種・株価・出来高で絞り込み → "
    "ステップ2：選んだ銘柄だけPER・PBR・配当利回りを取得します。"
)

# APIキーの取得（Streamlit CloudのSecrets優先、なければ環境変数）
api_key = st.secrets.get("JQUANTS_API_KEY", os.environ.get("JQUANTS_API_KEY", ""))

if not api_key:
    st.error(
        "APIキーが設定されていません。Streamlit Cloudの「Settings > Secrets」に\n"
        'JQUANTS_API_KEY = "取得したAPIキー" を追加してください。'
    )
    st.stop()

# 無料プランのレートリミット（1分間に5リクエストまで）を守るための間隔（秒）
REQUEST_INTERVAL_SEC = 13


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def fetch_market_snapshot(_api_key: str):
    """全銘柄の株価・業種・会社名のスナップショットを取得する（軽量：数回のAPIリクエストのみ）。"""
    cli = jquantsapi.ClientV2(api_key=_api_key)

    # 無料プランは株価データが12週間遅延するため、13週間前から遡って取引日を探す
    base_date = datetime.now(tz=tz.gettz("Asia/Tokyo")) - timedelta(weeks=13)
    price_df = pd.DataFrame()
    used_date_str = ""
    for i in range(7):
        d_str = (base_date - timedelta(days=i)).strftime("%Y-%m-%d")
        result = cli.get_eq_bars_daily(date_yyyymmdd=d_str)
        time.sleep(REQUEST_INTERVAL_SEC)
        if result is not None and len(result) > 0:
            price_df = result
            used_date_str = d_str
            break

    if price_df.empty:
        return pd.DataFrame(), ""

    master_df = cli.get_eq_master(date=used_date_str)
    time.sleep(REQUEST_INTERVAL_SEC)

    if master_df.empty:
        return pd.DataFrame(), used_date_str

    price_df = price_df.copy()
    master_df = master_df.copy()
    price_df["Code5"] = price_df["Code"].astype(str)
    master_df["Code5"] = master_df["Code"].astype(str)

    merged = pd.merge(
        master_df[["Code5", "CoName", "S33Nm"]],
        price_df[["Code5", "C", "Vo"]],
        on="Code5",
        how="inner",
    )
    merged = merged.rename(
        columns={"CoName": "銘柄", "S33Nm": "業種", "C": "株価", "Vo": "出来高"}
    )
    merged["株価"] = pd.to_numeric(merged["株価"], errors="coerce")
    merged["出来高"] = pd.to_numeric(merged["出来高"], errors="coerce")
    merged = merged.dropna(subset=["株価"])
    merged["コード4桁"] = merged["Code5"].str[:4]
    return merged, used_date_str


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def fetch_financials(_api_key: str, rows_key: tuple):
    """選択された銘柄だけ、財務情報（EPS・BPS・配当）を取得してPER・PBR・配当利回りを計算する。"""
    cli = jquantsapi.ClientV2(api_key=_api_key)
    rows = []
    for code, name, sector, price in rows_key:
        fin_df = cli.get_fin_summary(code=code)
        time.sleep(REQUEST_INTERVAL_SEC)

        latest = fin_df.iloc[-1] if fin_df is not None and len(fin_df) > 0 else None

        def safe_float(v):
            v = pd.to_numeric(v, errors="coerce")
            return float(v) if pd.notna(v) else None

        eps = bps = div = None
        if latest is not None:
            eps = safe_float(latest.get("EPS"))
            bps = safe_float(latest.get("BPS"))
            for col in ("FDivAnn", "DivAnn"):
                v = safe_float(latest.get(col))
                if v is not None and v > 0:
                    div = v
                    break

        per = round(price / eps, 1) if price and eps and eps > 0 else None
        pbr = round(price / bps, 2) if price and bps and bps > 0 else None
        div_yield = round(div / price * 100, 2) if price and div else None

        rows.append(
            {
                "コード": code,
                "銘柄": name,
                "業種": sector,
                "株価": price,
                "PER": per,
                "PBR": pbr,
                "配当利回り(%)": div_yield,
            }
        )

    return pd.DataFrame(rows)


def pct_rank(series: pd.Series, ascending: bool) -> pd.Series:
    return series.rank(pct=True, ascending=ascending, na_option="keep").fillna(0.5)


def _github_token() -> str:
    return st.secrets.get("GITHUB_TOKEN", os.environ.get("GITHUB_TOKEN", ""))


def _github_headers() -> dict:
    return {
        "Authorization": f"token {_github_token()}",
        "Accept": "application/vnd.github+json",
    }


def load_favorites_from_github():
    """favorites.json をGitHubリポジトリから読み込む。無ければ空リストを返す。"""
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{FAVORITES_PATH}"
    resp = requests.get(url, headers=_github_headers())
    if resp.status_code == 404:
        return [], None
    resp.raise_for_status()
    data = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return json.loads(content), data["sha"]


def save_favorites_to_github(favorites_list: list, sha):
    """favorites.json をGitHubリポジトリに書き込む（新規作成 or 更新）。"""
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{FAVORITES_PATH}"
    content_str = json.dumps(favorites_list, ensure_ascii=False, indent=2)
    payload = {
        "message": "お気に入りを更新",
        "content": base64.b64encode(content_str.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        payload["sha"] = sha
    resp = requests.put(url, headers=_github_headers(), json=payload)
    resp.raise_for_status()
    return resp.json()["content"]["sha"]


def extract_health_metrics(hist_df: pd.DataFrame) -> dict:
    """業績推移データから、純利益成長率・自己資本比率・営業CFを取り出す（総合スコア用）。"""
    if hist_df is None or hist_df.empty:
        return {"純利益成長率(%)": None, "自己資本比率(%)": None, "営業CF": None}

    latest = hist_df.iloc[-1]
    prev = hist_df.iloc[-2] if len(hist_df) > 1 else None

    growth = None
    if (
        prev is not None
        and pd.notna(latest.get("純利益"))
        and pd.notna(prev.get("純利益"))
        and prev.get("純利益") != 0
    ):
        growth = (latest["純利益"] - prev["純利益"]) / abs(prev["純利益"]) * 100

    eqar = latest.get("自己資本比率(%)")
    cfo = latest.get("営業CF")

    return {
        "純利益成長率(%)": round(growth, 1) if growth is not None else None,
        "自己資本比率(%)": round(float(eqar), 1) if pd.notna(eqar) else None,
        "営業CF": round(float(cfo), 0) if pd.notna(cfo) else None,
    }


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def fetch_financial_history(_api_key: str, code: str) -> pd.DataFrame:
    """指定した銘柄の、開示されている全期間分の業績・財務データを取得する（1銘柄=1リクエスト）。"""
    cli = jquantsapi.ClientV2(api_key=_api_key)
    df = cli.get_fin_summary(code=code)
    time.sleep(REQUEST_INTERVAL_SEC)

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    df["会計期間"] = df["CurPerSt"].astype(str).str[:10] + " 〜 " + df["CurPerEn"].astype(str).str[:10]

    def to_num(col: str) -> pd.Series:
        return pd.to_numeric(df[col], errors="coerce") if col in df.columns else pd.Series([None] * len(df))

    out = pd.DataFrame(
        {
            "会計期間": df["会計期間"],
            "DiscDate": df["DiscDate"],
            "売上高": to_num("Sales"),
            "営業利益": to_num("OP"),
            "経常利益": to_num("OdP"),
            "純利益": to_num("NP"),
            "EPS": to_num("EPS"),
            "総資産": to_num("TA"),
            "純資産": to_num("Eq"),
            "自己資本比率(%)": to_num("EqAR") * 100,
            "営業CF": to_num("CFO"),
            "投資CF": to_num("CFI"),
            "財務CF": to_num("CFF"),
            "現金同等物": to_num("CashEq"),
        }
    )
    out["推定負債（総資産−純資産）"] = out["総資産"] - out["純資産"]
    out = out.sort_values("DiscDate").reset_index(drop=True)
    return out


# --- データ取得（全銘柄スナップショット） ---
try:
    with st.spinner("全銘柄の株価・業種データを取得中です（初回は数分かかることがあります）..."):
        market_df, used_date_str = fetch_market_snapshot(api_key)
except Exception as e:
    st.error(f"データ取得に失敗しました：{e}")
    st.stop()

if market_df.empty:
    st.error("データを取得できませんでした。しばらく時間を置いて再度お試しください。")
    st.stop()

st.caption(f"株価データ基準日：{used_date_str}（全{len(market_df):,}銘柄）")

# --- ステップ1：全銘柄の絞り込み ---
st.divider()
st.subheader("ステップ1：絞り込み")
col1, col2, col3 = st.columns(3)
with col1:
    sectors = ["すべて"] + sorted(market_df["業種"].dropna().unique().tolist())
    sector = st.selectbox("業種", sectors)
with col2:
    price_min = st.number_input("株価 下限(円)", min_value=0, value=0, step=100, help="0のときは指定なし")
with col3:
    volume_min = st.number_input("出来高 下限(株)", min_value=0, value=0, step=10000, help="0のときは指定なし")

filtered = market_df.copy()
if sector != "すべて":
    filtered = filtered[filtered["業種"] == sector]
if price_min > 0:
    filtered = filtered[filtered["株価"] >= price_min]
if volume_min > 0:
    filtered = filtered[filtered["出来高"] >= volume_min]

filtered = filtered.sort_values("出来高", ascending=False)

st.write(f"{len(filtered):,}銘柄が該当しました（出来高が多い順に上位200件を表示）。")
st.dataframe(
    filtered[["コード4桁", "銘柄", "業種", "株価", "出来高"]].head(200),
    use_container_width=True,
    hide_index=True,
)

# --- ステップ2：選んだ銘柄だけ財務データを取得 ---
st.divider()
st.subheader("ステップ2：詳しく見る銘柄を選ぶ（最大15銘柄）")
st.caption(
    "総合スコアは、割安度（PER・PBR）、配当利回り、純利益成長率、自己資本比率、営業CFを"
    "組み合わせた機械的な順位付けです。多く選ぶほど取得に時間がかかります（1銘柄あたり数十秒）。"
)
top_candidates = filtered.head(200)
option_labels = top_candidates.apply(lambda r: f"{r['コード4桁']} - {r['銘柄']}", axis=1).tolist()
selected = st.multiselect(
    "総合スコアを見る銘柄を選んでください", option_labels, max_selections=15
)

if st.button("選択した銘柄の総合スコアを取得"):
    if not selected:
        st.warning("銘柄を1つ以上選んでください。")
    else:
        selected_codes = [s.split(" - ")[0] for s in selected]
        rows_key = tuple(
            (
                code,
                top_candidates.loc[top_candidates["コード4桁"] == code, "銘柄"].iloc[0],
                top_candidates.loc[top_candidates["コード4桁"] == code, "業種"].iloc[0],
                float(top_candidates.loc[top_candidates["コード4桁"] == code, "株価"].iloc[0]),
            )
            for code in selected_codes
        )

        try:
            with st.spinner(f"{len(selected_codes)}銘柄のバリュエーション・財務健全性を取得中です..."):
                detail_df = fetch_financials(api_key, rows_key)

                health_rows = []
                for code in selected_codes:
                    hist = fetch_financial_history(api_key, code)
                    metrics = extract_health_metrics(hist)
                    metrics["コード"] = code
                    health_rows.append(metrics)
                health_df = pd.DataFrame(health_rows)
        except Exception as e:
            st.error(f"財務データの取得に失敗しました：{e}")
            st.stop()

        detail_df = detail_df.merge(health_df, on="コード", how="left")

        detail_df["総合スコア"] = (
            (
                pct_rank(detail_df["PER"], False) * 0.25
                + pct_rank(detail_df["PBR"], False) * 0.15
                + pct_rank(detail_df["配当利回り(%)"], True) * 0.15
                + pct_rank(detail_df["純利益成長率(%)"], True) * 0.20
                + pct_rank(detail_df["自己資本比率(%)"], True) * 0.15
                + pct_rank(detail_df["営業CF"], True) * 0.10
            )
            * 100
        ).round(0)
        detail_df = detail_df.sort_values("総合スコア", ascending=False)

        st.caption("内訳：PER25%、PBR15%、配当利回り15%、純利益成長率20%、自己資本比率15%、営業CF10%")
        st.dataframe(detail_df, use_container_width=True, hide_index=True)

# --- お気に入り（GitHubリポジトリに保存、アプリを更新しても消えない） ---
st.divider()
st.subheader("★ お気に入り")

if not _github_token():
    st.info(
        "お気に入り機能を使うには、Streamlit Cloudの「Settings > Secrets」に "
        "GITHUB_TOKEN を追加してください。"
    )
else:
    if "favorites" not in st.session_state:
        try:
            favs, sha = load_favorites_from_github()
        except Exception as e:
            st.warning(f"お気に入りの読み込みに失敗しました：{e}")
            favs, sha = [], None
        st.session_state["favorites"] = favs
        st.session_state["favorites_sha"] = sha

    if st.session_state["favorites"]:
        fav_df = pd.DataFrame(st.session_state["favorites"])
        st.dataframe(fav_df, use_container_width=True, hide_index=True)

        fav_labels = [f"{f['code']} - {f['name']}" for f in st.session_state["favorites"]]
        to_remove = st.multiselect("削除する銘柄を選んでください", fav_labels, key="fav_remove_select")
        if st.button("選択した銘柄をお気に入りから削除"):
            remove_codes = [s.split(" - ")[0] for s in to_remove]
            new_favs = [f for f in st.session_state["favorites"] if f["code"] not in remove_codes]
            try:
                new_sha = save_favorites_to_github(new_favs, st.session_state["favorites_sha"])
                st.session_state["favorites"] = new_favs
                st.session_state["favorites_sha"] = new_sha
                st.success("削除しました。")
                st.rerun()
            except Exception as e:
                st.error(f"削除に失敗しました：{e}")
    else:
        st.caption("まだお気に入りはありません。")

    st.markdown("##### お気に入りに追加")
    add_labels = st.multiselect("追加する銘柄を選んでください（ステップ1の絞り込み結果から選べます）", option_labels, key="fav_add_select")
    if st.button("お気に入りに追加する"):
        if not add_labels:
            st.warning("銘柄を選んでください。")
        else:
            existing_codes = {f["code"] for f in st.session_state["favorites"]}
            new_entries = []
            for label in add_labels:
                code = label.split(" - ")[0]
                if code in existing_codes:
                    continue
                row = top_candidates.loc[top_candidates["コード4桁"] == code].iloc[0]
                new_entries.append(
                    {
                        "code": code,
                        "name": row["銘柄"],
                        "sector": row["業種"],
                        "added_at": datetime.now(tz=tz.gettz("Asia/Tokyo")).strftime("%Y-%m-%d"),
                    }
                )
            if not new_entries:
                st.info("選んだ銘柄はすでにお気に入りに登録されています。")
            else:
                updated = st.session_state["favorites"] + new_entries
                try:
                    new_sha = save_favorites_to_github(updated, st.session_state["favorites_sha"])
                    st.session_state["favorites"] = updated
                    st.session_state["favorites_sha"] = new_sha
                    st.success(f"{len(new_entries)}銘柄を追加しました。")
                    st.rerun()
                except Exception as e:
                    st.error(f"追加に失敗しました：{e}")

# --- ステップ3：個別銘柄の業績推移・財務健全性 ---
st.divider()
st.subheader("ステップ3：個別銘柄の業績推移・財務健全性を見る")
st.caption(
    "開示されている全期間分の業績・負債・キャッシュフローを表示します"
    "（無料プランのため、負債は「総資産−純資産」で概算しています）。"
    "表の色は前期からの増減を示します（増加＝赤、減少＝青）。"
)

code_input = st.text_input("銘柄コードを入力してください（例：7203）", value="")

# 金額が大きい項目は億円単位に変換して表示する
OKU_COLS = [
    "売上高", "営業利益", "経常利益", "純利益",
    "総資産", "純資産", "推定負債（総資産−純資産）",
    "営業CF", "投資CF", "財務CF", "現金同等物",
]


def to_oku(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in OKU_COLS:
        if col in out.columns:
            out[col] = out[col] / 1e8
    return out


def style_by_change(df: pd.DataFrame, cols: list):
    """前期からの増減で文字色を変える（増加＝赤、減少＝青）。"""

    def apply_style(data: pd.DataFrame) -> pd.DataFrame:
        styles = pd.DataFrame("", index=data.index, columns=data.columns)
        for col in cols:
            if col not in data.columns:
                continue
            diff = data[col].diff()
            styles[col] = diff.apply(
                lambda v: "color:#d6336c; font-weight:600;"
                if pd.notna(v) and v > 0
                else ("color:#1c7ed6; font-weight:600;" if pd.notna(v) and v < 0 else "")
            )
        return styles

    fmt = {c: "{:,.1f}" for c in cols if c in df.columns}
    return df.style.hide(axis="index").apply(apply_style, axis=None).format(fmt, na_rep="―")


if st.button("業績・財務データを取得", key="fetch_history_button"):
    if not code_input.strip():
        st.warning("銘柄コードを入力してください。")
    else:
        try:
            with st.spinner("業績・財務データの推移を取得中です..."):
                history_df = fetch_financial_history(api_key, code_input.strip())
        except Exception as e:
            st.error(f"データ取得に失敗しました：{e}")
            history_df = pd.DataFrame()

        if history_df.empty:
            st.warning("データが見つかりませんでした。銘柄コードをご確認ください。")
        else:
            oku_df = to_oku(history_df)

            # --- 最新期のサマリーカード ---
            latest = oku_df.iloc[-1]
            prev = oku_df.iloc[-2] if len(oku_df) > 1 else None

            def delta(col: str):
                if prev is None or pd.isna(latest[col]) or pd.isna(prev[col]):
                    return None
                return latest[col] - prev[col]

            st.markdown("##### 最新期のサマリー")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric(
                "売上高（億円）", f"{latest['売上高']:,.1f}",
                delta=(f"{delta('売上高'):,.1f}" if delta("売上高") is not None else None),
                delta_color="inverse",
            )
            c2.metric(
                "営業利益（億円）", f"{latest['営業利益']:,.1f}",
                delta=(f"{delta('営業利益'):,.1f}" if delta("営業利益") is not None else None),
                delta_color="inverse",
            )
            c3.metric(
                "純利益（億円）", f"{latest['純利益']:,.1f}",
                delta=(f"{delta('純利益'):,.1f}" if delta("純利益") is not None else None),
                delta_color="inverse",
            )
            c4.metric(
                "自己資本比率（%）", f"{latest['自己資本比率(%)']:,.1f}",
                delta=(f"{delta('自己資本比率(%)'):,.1f}" if delta("自己資本比率(%)") is not None else None),
                delta_color="inverse",
            )

            # --- 業績推移 ---
            st.markdown("##### 業績推移（億円、EPSは円）")
            perf_cols = ["会計期間", "売上高", "営業利益", "経常利益", "純利益", "EPS"]
            st.dataframe(style_by_change(oku_df[perf_cols], perf_cols[1:]), use_container_width=True)

            fig1 = px.line(
                oku_df, x="会計期間", y=["売上高", "営業利益", "純利益"],
                markers=True, labels={"value": "億円", "variable": "指標"},
            )
            st.plotly_chart(fig1, use_container_width=True)

            # --- 財務健全性 ---
            st.markdown("##### 財務健全性（負債関連、億円）")
            debt_cols = ["会計期間", "総資産", "純資産", "自己資本比率(%)", "推定負債（総資産−純資産）"]
            st.dataframe(style_by_change(oku_df[debt_cols], debt_cols[1:]), use_container_width=True)

            fig2 = px.line(
                oku_df, x="会計期間", y=["総資産", "純資産", "推定負債（総資産−純資産）"],
                markers=True, labels={"value": "億円", "variable": "指標"},
            )
            st.plotly_chart(fig2, use_container_width=True)

            # --- キャッシュフロー ---
            st.markdown("##### キャッシュフロー（億円）")
            cf_cols = ["会計期間", "営業CF", "投資CF", "財務CF", "現金同等物"]
            st.dataframe(style_by_change(oku_df[cf_cols], cf_cols[1:]), use_container_width=True)

            cf_long = oku_df.melt(
                id_vars="会計期間", value_vars=["営業CF", "投資CF", "財務CF"],
                var_name="種類", value_name="金額",
            )
            cf_long["符号"] = cf_long["金額"].apply(lambda v: "プラス" if v >= 0 else "マイナス")
            fig3 = px.bar(
                cf_long, x="会計期間", y="金額", color="符号", barmode="group", facet_col="種類",
                color_discrete_map={"プラス": "#d6336c", "マイナス": "#1c7ed6"},
                labels={"金額": "億円"},
            )
            st.plotly_chart(fig3, use_container_width=True)

# --- ステップ4：複数銘柄の比較 ---
st.divider()
st.subheader("ステップ4：複数銘柄を比較する")
st.caption("2〜5銘柄を選んで、PER・PBR・配当利回りや業績・財務健全性をまとめて比較できます。")

compare_labels = st.multiselect(
    "比較する銘柄を選んでください", option_labels, max_selections=5, key="compare_select"
)

if st.button("比較する", key="compare_button"):
    if len(compare_labels) < 2:
        st.warning("2銘柄以上を選んでください。")
    else:
        compare_codes = [s.split(" - ")[0] for s in compare_labels]
        compare_rows_key = tuple(
            (
                code,
                top_candidates.loc[top_candidates["コード4桁"] == code, "銘柄"].iloc[0],
                top_candidates.loc[top_candidates["コード4桁"] == code, "業種"].iloc[0],
                float(top_candidates.loc[top_candidates["コード4桁"] == code, "株価"].iloc[0]),
            )
            for code in compare_codes
        )

        try:
            with st.spinner(f"{len(compare_codes)}銘柄の指標を取得中です（既に見た銘柄は高速です）..."):
                per_pbr_df = fetch_financials(api_key, compare_rows_key)
                history_frames = {code: fetch_financial_history(api_key, code) for code in compare_codes}
        except Exception as e:
            st.error(f"データ取得に失敗しました：{e}")
            st.stop()

        comp_rows = []
        for code in compare_codes:
            base = per_pbr_df[per_pbr_df["コード"] == code].iloc[0]
            hist = history_frames.get(code, pd.DataFrame())
            latest_h = hist.iloc[-1] if not hist.empty else None

            def h_val(col, in_oku=False):
                if latest_h is None or pd.isna(latest_h.get(col)):
                    return None
                v = float(latest_h.get(col))
                return v / 1e8 if in_oku else v

            comp_rows.append(
                {
                    "銘柄": base["銘柄"],
                    "コード": code,
                    "業種": base["業種"],
                    "株価（円）": base["株価"],
                    "PER（倍）": base["PER"],
                    "PBR（倍）": base["PBR"],
                    "配当利回り（%）": base["配当利回り(%)"],
                    "売上高（億円）": h_val("売上高", in_oku=True),
                    "純利益（億円）": h_val("純利益", in_oku=True),
                    "自己資本比率（%）": h_val("自己資本比率(%)"),
                    "営業CF（億円）": h_val("営業CF", in_oku=True),
                }
            )

        comp_df = pd.DataFrame(comp_rows)

        st.markdown("##### 比較表（銘柄を横に並べています）")
        st.dataframe(comp_df.set_index("銘柄").T, use_container_width=True)

        st.markdown("##### 主要指標の比較グラフ")
        chart_metrics = ["PER（倍）", "PBR（倍）", "配当利回り（%）", "自己資本比率（%）"]
        cols = st.columns(2)
        for i, metric in enumerate(chart_metrics):
            fig = px.bar(comp_df, x="銘柄", y=metric, color="銘柄", title=metric)
            fig.update_layout(showlegend=False, height=300)
            cols[i % 2].plotly_chart(fig, use_container_width=True)
