import os
import time
from datetime import datetime, timedelta
 
import pandas as pd
import streamlit as st
from dateutil import tz
import jquantsapi
 
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
 
        eps = bps = div = None
        if latest is not None:
            if pd.notna(latest.get("EPS")):
                eps = float(latest.get("EPS"))
            if pd.notna(latest.get("BPS")):
                bps = float(latest.get("BPS"))
            for col in ("FDivAnn", "DivAnn"):
                v = latest.get(col)
                if pd.notna(v) and float(v) > 0:
                    div = float(v)
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
st.subheader("ステップ2：詳しく見る銘柄を選ぶ（最大15銘柄）")
top_candidates = filtered.head(200)
option_labels = top_candidates.apply(lambda r: f"{r['コード4桁']} - {r['銘柄']}", axis=1).tolist()
selected = st.multiselect(
    "PER・PBR・配当利回りを取得する銘柄を選んでください", option_labels, max_selections=15
)
 
if st.button("選択した銘柄のPER・PBR・配当利回りを取得"):
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
            with st.spinner(f"{len(selected_codes)}銘柄の財務データを取得中です..."):
                detail_df = fetch_financials(api_key, rows_key)
        except Exception as e:
            st.error(f"財務データの取得に失敗しました：{e}")
            st.stop()
 
        detail_df["スコア"] = (
            (
                pct_rank(detail_df["PER"], False) * 0.4
                + pct_rank(detail_df["PBR"], False) * 0.3
                + pct_rank(detail_df["配当利回り(%)"], True) * 0.3
            )
            * 100
        ).round(0)
        detail_df = detail_df.sort_values("スコア", ascending=False)
 
        st.dataframe(detail_df, use_container_width=True, hide_index=True)
