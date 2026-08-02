import os
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st
from dateutil import tz
import jquantsapi

st.set_page_config(page_title="投資サポートアプリ - 接続確認", layout="wide")
st.title("J-Quants API 接続確認")
st.caption("最小構成：トヨタ自動車(7203)の直近の株価データを取得して表示します。")

# APIキーの取得（Streamlit CloudのSecrets優先、なければ環境変数）
api_key = st.secrets.get("JQUANTS_API_KEY", os.environ.get("JQUANTS_API_KEY", ""))

if not api_key:
    st.error(
        "APIキーが設定されていません。Streamlit Cloudの「Settings > Secrets」に\n"
        'JQUANTS_API_KEY = "取得したAPIキー" を追加してください。'
    )
    st.stop()

# J-Quantsの銘柄コードは5桁（末尾0を付与）。トヨタ自動車 7203 -> 72030
TARGET_CODE = "72030"
TARGET_NAME = "トヨタ自動車"

try:
    cli = jquantsapi.ClientV2(api_key=api_key)

    end_dt = datetime.now(tz=tz.gettz("Asia/Tokyo")) - timedelta(weeks=13)
    start_dt = end_dt - timedelta(days=14)

    with st.spinner("J-Quants APIからデータを取得中..."):
        df = cli.get_eq_bars_daily_range(start_dt=start_dt, end_dt=end_dt)

    if df is None or len(df) == 0:
        st.warning("データは取得できましたが、件数が0件でした。")
    else:
        df["Code"] = df["Code"].astype(str)
        target_df = df[df["Code"] == TARGET_CODE].sort_values("Date")

        st.success(f"接続に成功しました。全銘柄で{len(df):,}件のデータを取得しました。")

        st.subheader(f"{TARGET_NAME}（{TARGET_CODE}）の直近データ")
        if len(target_df) == 0:
            st.info("対象銘柄のデータが見つかりませんでした（休日等の可能性があります）。")
        else:
            st.dataframe(target_df, use_container_width=True)

except Exception as e:
    st.error(f"接続に失敗しました：{e}")
    st.caption("APIキーが正しいか、無料プランの登録が完了しているかをご確認ください。")
