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

# IBKR 官方最新标准路径 (已从旧版 Universal/servlet 迁移至 AccountManagement/FlexWebService)
PRIMARY_BASE_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
FALLBACK_BASE_URL = "https://ndcdyn.interactivebrokers.com/Universal/servlet"


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
    endpoints_to_try = [
        f"{PRIMARY_BASE_URL}/SendRequest",
        f"{FALLBACK_BASE_URL}/FlexStatementService.SendRequest"
    ]
    
    resp = None
    used_endpoint = None
    last_err = None

    for ep in endpoints_to_try:
        send_url = f"{ep}?t={token}&q={query_id}&v=3"
        print(f"▶ 正在向 IBKR 发起报表生成请求 [{ep}]...")
        for attempt in range(1, 4):
            try:
                r = requests.get(send_url, headers=HEADERS, timeout=30)
                if r.status_code == 200:
                    # 遇到 1018 限频自动等待 15 秒
                    if "1018" in r.text or "Too many requests" in r.text:
                        print("  ⏳ 触发请求频率限制 (Error 1018)，等待 15 秒后重试...")
                        time.sleep(15)
                        continue
                    # 遇到 1001 排队自动等待 10 秒
                    if "1001" in r.text:
                        print("  ⏳ 报表引擎排队中 (Error 1001)，等待 10 秒后重试...")
                        time.sleep(10)
                        continue
                    resp = r
                    used_endpoint = ep
                    break
                else:
                    last_err = f"HTTP {r.status_code}: {r.text}"
            except Exception as e:
                print(f"  连接失败: {e}...")
                last_err = str(e)
                time.sleep(3)

        if resp:
            break

    if not resp:
        raise RuntimeError(f"IBKR 接口连接失败: {last_err}")

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
    print(f"✔ 成功获取 ReferenceCode: {ref_code}，等待报表生成...")

    # 根据调用的路径自动匹配 GetStatement 基础 URL
    if "AccountManagement" in used_endpoint:
        fetch_base = f"{PRIMARY_BASE_URL}/GetStatement"
    else:
        fetch_base = f"{FALLBACK_BASE_URL}/FlexStatementService.GetStatement"

    fetch_url = f"{fetch_base}?q={ref_code}&t={token}&v=3"
    max_retries = 15
    for attempt in range(1, max_retries + 1):
        time.sleep(5)
        print(f"  [重试 {attempt}/{max_retries}] 正在获取报表数据...")
        try:
            fetch_resp = requests.get(fetch_url, headers=HEADERS, timeout=60)
            if fetch_resp.status_code == 200:
                content = fetch_resp.text
                if "<FlexQueryResponse" in content or "<FlexStatements" in content:
                    print("✔ 报表获取成功！")
                    return content
                if "Statement is not ready" in content or "1019" in content:
                    continue
                if "<Status>Fail</Status>" in content:
                    err_xml = ET.fromstring(content)
                    raise RuntimeError(f"获取报表失败: {err_xml.findtext('.//ErrorMessage')}")
        except requests.RequestException as req_err:
            print(f"  网络请求波动: {req_err}，继续重试...")

        print("  ⏳ 报表尚未就绪，稍后重试...")

    raise TimeoutError("IBKR 报表生成超时，超过最大轮询等待时间。")


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

    # 1. 未平仓持仓明细 (OpenPositions)
    positions = []
    for pos in root.findall(".//OpenPosition"):
        symbol = pos.get("symbol", "N/A")
        qty = float(pos.get("position", 0.0))
        mark_price = float(pos.get("markPrice", 0.0))
        val = float(pos.get("positionValue", 0.0))
        unrealized_pnl = float(pos.get("fifoPnlUnrealized", 0.0))

        positions.append({
            "symbol": symbol,
            "quantity": qty,
            "mark_price": mark_price,
            "value": val,
            "unrealized_pnl": unrealized_pnl
        })

    stock_val_calc = sum(p["value"] for p in positions)
    total_unrealized_pnl = sum(p["unrealized_pnl"] for p in positions)

    # 2. 现金提取
    cash = 0.0
    cash_node = (
        root.find(".//CashReportCurrency[@currency='BASE_SUMMARY']")
        or root.find(".//CashReportCurrency")
    )
    if cash_node is not None:
        cash = float(cash_node.get("endingCash", 0.0) or cash_node.get("endingSettledCash", 0.0))

    # 3. 资产净值提取
    nav = 0.0
    stock_val = 0.0
    daily_pnl = 0.0

    # 尝试读取 ChangeInNAV
    change_nav = root.find(".//ChangeInNAV")
    if change_nav is not None:
        nav = float(change_nav.get("endingNav", 0.0))
        daily_pnl = float(change_nav.get("changeInNAV", 0.0))

    # 尝试从各类 EquitySummary 标签提取
    nav_tags = [
        ".//EquitySummaryByReportDateInBase",
        ".//EquitySummaryInBase",
        ".//NetAssetValueInBase"
    ]
    for tag in nav_tags:
        for node in root.findall(tag):
            tot = float(node.get("total", 0.0))
            c_val = float(node.get("cash", 0.0))
            s_val = float(node.get("stock", 0.0))
            if tot > 0 and nav == 0.0:
                nav = tot
            if c_val > 0 and cash == 0.0:
                cash = c_val
            if s_val > 0 and stock_val == 0.0:
                stock_val = s_val

    # 4. 智能兜底计算（基于持仓与现金求和）
    if stock_val == 0.0:
        stock_val = stock_val_calc

    if nav == 0.0:
        nav = stock_val + cash

    return {
        "account_id": account_id,
        "date": report_date,
        "currency": base_currency,
        "nav": nav,
        "cash": cash,
        "stock_val": stock_val,
        "daily_pnl": daily_pnl,
        "total_unrealized_pnl": total_unrealized_pnl,
        "positions": positions
    }


def format_telegram_message(data: dict) -> str:
    """Format extracted information into a clean HTML message for Telegram."""
    cur = html.escape(data["currency"])
    acc = html.escape(data["account_id"] or "主账户")
    dt = html.escape(data["date"] or time.strftime("%Y-%m-%d"))

    nav = data["nav"]
    daily_pnl = data["daily_pnl"]
    total_unrealized = data["total_unrealized_pnl"]

    lines = [
        "📊 <b>IBKR 每日投资资产报告</b>",
        f"👤 账户: <code>{acc}</code> | 结算日: <code>{dt}</code>",
        "────────────────",
        f"💰 <b>账户总资产 (NAV):</b> {cur} ${nav:,.2f}",
        f"🏢 <b>持仓证券总值:</b> {cur} ${data['stock_val']:,.2f}",
        f"💵 <b>可用现金余额:</b> {cur} ${data['cash']:,.2f}",
    ]

    # 当日盈亏（如有变动）
    if daily_pnl != 0.0:
        pnl_icon = "🟢" if daily_pnl >= 0 else "🔴"
        pnl_sign = "+" if daily_pnl >= 0 else ""
        base_nav = nav - daily_pnl
        pct = (daily_pnl / base_nav * 100) if base_nav > 0 else 0.0
        lines.append(f"📈 <b>当日盈亏变动:</b> {pnl_icon} {cur} ${daily_pnl:+,.2f} ({pnl_sign}{pct:,.2f}%)")

    # 累计未实现浮盈
    unreal_icon = "🟢" if total_unrealized >= 0 else "🔴"
    lines.append(f"📊 <b>持仓累计浮盈:</b> {unreal_icon} {cur} ${total_unrealized:+,.2f}")

    lines.append("────────────────")
    lines.append("<b>📦 核心持仓明细:</b>")

    positions = data["positions"]
    if positions:
        positions.sort(key=lambda x: x["value"], reverse=True)
        for pos in positions:
            sym = html.escape(pos["symbol"])
            pos_icon = "🟢" if pos["unrealized_pnl"] >= 0 else "🔴"
            weight = (pos["value"] / nav * 100) if nav > 0 else 0.0
            lines.append(
                f"• <b>{sym}</b>: {pos['quantity']:,.1f} 股 | 市值 ${pos['value']:,.2f} ({weight:.1f}%)\n"
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
