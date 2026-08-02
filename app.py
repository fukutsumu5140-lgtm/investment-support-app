import os
from datetime import datetime, timedelta

import streamlit as st
from dateutil import tz
import jquantsapi

st.set_page_config(page_title="投資サポートアプリ - 接続確認", layout="wide")
st.title("J-Quants API 接続確認")
st.caption("最小構成：トヨタ自動車(7203)の株価データを取得して表示します。")

# APIキーの取得（Streamlit CloudのSecrets優先、なければ環境変数）
api_key = st.secrets.get("JQUANTS_API_KEY", os.environ.get("JQUANTS_API_KEY", ""))

if not api_key:
    st.error(
        "APIキーが設定されていません。Streamlit Cloudの「Settings > Secrets」に\n"
        'JQUANTS_API_KEY = "取得したAPIキー" を追加してください。'
    )
    st.stop()

TARGET_CODE = "7203"
TARGET_NAME = "トヨタ自動車"

try:
    cli = jquantsapi.ClientV2(api_key=api_key)

    # 無料プランはデータが12週間遅延するため、直近13週間前を基準に取得する
    target_date = datetime.now(tz=tz.gettz("Asia/Tokyo")) - timedelta(weeks=13)
    target_date_str = target_date.strftime("%Y-%m-%d")

    with st.spinner("J-Quants APIからデータを取得中..."):
        df = cli.get_eq_bars_daily(code=TARGET_CODE, date_yyyymmdd=target_date_str)

    if df is None or len(df) == 0:
        st.warning(f"{target_date_str} のデータが見つかりませんでした（休日等の可能性があります）。")
    else:
        st.success("接続に成功しました。")
        st.subheader(f"{TARGET_NAME}（{TARGET_CODE}） {target_date_str} のデータ")
        st.dataframe(df, use_container_width=True)

except Exception as e:
    st.error(f"接続に失敗しました：{e}")
    st.caption("しばらく時間を置いてから再度お試しください（レートリミットの可能性があります）。")
