#!/usr/bin/env python3
"""
IBKR Daily Telegram Report Generator & Pusher
Fetches portfolio and statement data via IBKR Flex Web Service (v3)
and sends a daily briefing to Telegram.
"""

import html
import os
import sys
import time
import xml.etree.ElementTree as ET
import requests

IBKR_TOKEN = os.getenv("IBKR_TOKEN")
IBKR_QUERY_ID = os.getenv("IBKR_QUERY_ID")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

HEADERS = {
    "User-Agent": "Python/3.11 (IBKR-Telegram-Notifier/1.0)"
}


def validate_environment():
    missing = []
    if not IBKR_TOKEN:
        missing.append("IBKR_TOKEN")
    if not IBKR_QUERY_ID:
        missing.append("IBKR_QUERY_ID")
    if not TG_BOT_TOKEN:
        missing.append("TG_BOT_TOKEN")
    if not TG_CHAT_ID:
        missing.append("TG_CHAT_ID")
    if missing:
        raise ValueError(f"缺少必要环境变量配置: {', '.join(missing)}")


def fetch_ibkr_statement(token: str, query_id: str) -> str:
    """Fetch Flex XML report from IBKR using the 2-step Flex Web Service."""
    send_url = (
        f"https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
        f"?t={token}&q={query_id}&v=3"
    )
    print("▶ 正在向 IBKR 发起报表生成请求...")
    resp = requests.get(send_url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"SendRequest HTTP 错误: {resp.status_code} - {resp.text}")

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        raise RuntimeError(f"解析 IBKR 响应失败: {e}\n响应内容: {resp.text}")

    status = root.findtext(".//Status")
    if status != "Success":
        err_code = root.findtext(".//ErrorCode") or "Unknown"
        err_msg = root.findtext(".//ErrorMessage") or resp.text
        raise RuntimeError(f"IBKR 接口报错 [{err_code}]: {err_msg}")

    ref_code = root.findtext(".//ReferenceCode")
    fetch_base_url = (
        root.findtext(".//Url")
        or "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"
    )
    print(f"✔ 成功获取 ReferenceCode: {ref_code}，等待报表生成...")

    fetch_url = f"{fetch_base_url}?q={ref_code}&t={token}&v=3"
    max_retries = 12
    for attempt in range(1, max_retries + 1):
        time.sleep(5)
        print(f"  [重试 {attempt}/{max_retries}] 正在获取报表数据...")
        fetch_resp = requests.get(fetch_url, headers=HEADERS, timeout=60)
        if fetch_resp.status_code == 200:
            content = fetch_resp.text
            if "<FlexQueryResponse" in content or "<FlexStatements" in content:
                print("✔ 报表获取成功！")
                return content
            if "Statement is not ready" in content:
                continue
            if "<Status>Fail</Status>" in content:
                err_xml = ET.fromstring(content)
                raise RuntimeError(f"获取报表失败: {err_xml.findtext('.//ErrorMessage')}")
        print("  ⏳ 报表尚未就绪，稍后重试...")

    raise TimeoutError("IBKR 报表生成超时，超过最大轮询等待时间 (60秒)。")


def parse_statement(xml_content: str) -> dict:
    """Parse key portfolio metrics from the Flex statement XML."""
    root = ET.fromstring(xml_content)

    stmt = root.find(".//FlexStatement")
    report_date = stmt.get("toDate", "") if stmt is not None else ""
    account_id = stmt.get("accountId", "") if stmt is not None else ""

    account_info = root.find(".//AccountInformation")
    base_currency = "USD"
    if account_info is not None:
        base_currency = account_info.get("currency", base_currency)
        if not account_id:
            account_id = account_info.get("accountId", "")

    nav = 0.0
    prior_nav = 0.0
    cash = 0.0
    stock_val = 0.0
    daily_pnl = 0.0

    # 1. 尝试从 ChangeInNAV 获取总览
    change_nav = root.find(".//ChangeInNAV")
    if change_nav is not None:
        nav = float(change_nav.get("endingNav", 0.0))
        prior_nav = float(change_nav.get("priorNav", 0.0))
        daily_pnl = float(change_nav.get("changeInNAV", 0.0))

    # 2. 从 EquitySummaryInBase 提取资产分布
    equity_nodes = root.findall(".//EquitySummaryInBase")
    for eq in equity_nodes:
        tot = float(eq.get("total", 0.0))
        c_val = float(eq.get("cash", 0.0))
        s_val = float(eq.get("stock", 0.0))
        if tot > 0:
            nav = tot
            cash = c_val
            stock_val = s_val

    # 3. 现金补充解析
    if cash == 0.0:
        cash_node = root.find(".//CashReportCurrency[@currency='BASE_SUMMARY']") or root.find(".//CashReportCurrency")
        if cash_node is not None:
            cash = float(cash_node.get("endingCash", 0.0) or cash_node.get("endingSettledCash", 0.0))

    # 4. 未平仓持仓明细 (OpenPositions)
    positions = []
    for pos in root.findall(".//OpenPosition"):
        symbol = pos.get("symbol", "N/A")
        qty = float(pos.get("position", 0.0))
        mark_price = float(pos.get("markPrice", 0.0))
        val = float(pos.get("positionValue", 0.0))
        unrealized_pnl = float(pos.get("fifoPnlUnrealized", 0.0))
        percent_nav = float(pos.get("percentOfNAV", 0.0))

        positions.append({
            "symbol": symbol,
            "quantity": qty,
            "mark_price": mark_price,
            "value": val,
            "unrealized_pnl": unrealized_pnl,
            "percent_nav": percent_nav
        })

    if daily_pnl == 0.0 and prior_nav > 0 and nav > 0:
        daily_pnl = nav - prior_nav

    return {
        "account_id": account_id,
        "date": report_date,
        "currency": base_currency,
        "nav": nav,
        "cash": cash,
        "stock_val": stock_val,
        "daily_pnl": daily_pnl,
        "positions": positions
    }


def format_telegram_message(data: dict) -> str:
    """Format extracted information into a clean HTML message for Telegram."""
    cur = html.escape(data["currency"])
    acc = html.escape(data["account_id"] or "主账户")
    dt = html.escape(data["date"] or time.strftime("%Y-%m-%d"))

    daily_pnl = data["daily_pnl"]
    pnl_sign = "+" if daily_pnl >= 0 else ""
    pnl_icon = "🟢" if daily_pnl >= 0 else "🔴"

    base_for_pct = data["nav"] - daily_pnl
    pct_val = (daily_pnl / base_for_pct * 100) if base_for_pct > 0 else 0.0

    lines = [
        "📊 <b>IBKR 每日投资资产报告</b>",
        f"👤 账户: <code>{acc}</code> | 结算日: <code>{dt}</code>",
        "────────────────",
        f"💰 <b>账户总资产 (NAV):</b> {cur} ${data['nav']:,.2f}",
        f"📈 <b>当日盈亏变动:</b> {pnl_icon} {cur} ${daily_pnl:+,.2f} ({pnl_sign}{pct_val:,.2f}%)",
        f"💵 <b>可用现金余额:</b> {cur} ${data['cash']:,.2f}",
        f"🏢 <b>持仓证券总值:</b> {cur} ${data['stock_val']:,.2f}",
        "────────────────",
        "<b>📦 核心持仓明细:</b>"
    ]

    positions = data["positions"]
    if positions:
        positions.sort(key=lambda x: x["value"], reverse=True)
        for pos in positions:
            sym = html.escape(pos["symbol"])
            pos_icon = "🟢" if pos["unrealized_pnl"] >= 0 else "🔴"
            pct_str = f" ({pos['percent_nav']:.1f}%)" if pos['percent_nav'] > 0 else ""
            lines.append(
                f"• <b>{sym}</b>: {pos['quantity']:,.1f} 股 | 市值 ${pos['value']:,.2f}{pct_str}\n"
                f"   └ 现价 ${pos['mark_price']:,.2f} | 浮盈 {pos_icon} ${pos['unrealized_pnl']:+,.2f}"
            )
    else:
        lines.append("• <i>暂无持仓或未勾选 Open Positions 字段</i>")

    lines.append("────────────────")
    lines.append(f"⏱ <i>推送时间: {time.strftime('%Y-%m-%d %H:%M:%S')}</i>")
    return "\n".join(lines)


def send_telegram(bot_token: str, chat_id: str, message: str):
    """Send text message to Telegram Bot."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    resp = requests.post(url, json=payload, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"Telegram 推送失败: {resp.status_code} - {resp.text}")
    print("✔ Telegram 消息推送成功！")


def main():
    try:
        validate_environment()
        xml_data = fetch_ibkr_statement(IBKR_TOKEN, IBKR_QUERY_ID)
        parsed_data = parse_statement(xml_data)
        message = format_telegram_message(parsed_data)
        send_telegram(TG_BOT_TOKEN, TG_CHAT_ID, message)
    except Exception as e:
        print(f"❌ 执行出错: {e}", file=sys.stderr)
        if TG_BOT_TOKEN and TG_CHAT_ID:
            err_msg = (
                f"⚠️ <b>IBKR 报表推送失败警报</b>\n\n"
                f"错误信息: <code>{html.escape(str(e))}</code>\n\n"
                f"请检查 IBKR Token 是否到期或 Query ID 是否正确。"
            )
            try:
                send_telegram(TG_BOT_TOKEN, TG_CHAT_ID, err_msg)
            except Exception as tg_err:
                print(f"发送报警失败: {tg_err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
