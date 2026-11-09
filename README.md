# IBKR 每日投资报表 Telegram 自动推送系统

通过 IBKR 官方 **Flex Web Service (v3)** 接口与 **GitHub Actions**，实现完全免维护、免服务器费用的每日投资报表 Telegram 机器人自动推送。

---

## ⚡ 极速部署指南（3 分钟完成）

### 第一步：在 GitHub 创建私有仓库
1. 打开 [GitHub.com](https://github.com/) 并登录。
2. 点击右上角 **+** ➔ **New repository**。
3. 仓库名称填 `ibkr-telegram-bot`，勾选 **Private（私有）**（必须保持私有以保护你的投资数据与脚本安全）。
4. 将本压缩包中的全部文件解压后直接拖拽上传或 Git 推送到该仓库主分支。

### 第二步：配置 4 个 GitHub 密钥 (Secrets)
在你的 GitHub 仓库页面中：
1. 点击上方 **Settings** ➔ 左侧菜单 **Secrets and variables** ➔ **Actions**。
2. 点击绿色的 **New repository secret**，依次添加以下 4 个密钥：

| Secret 名称 | 对应说明与获取方式 |
| :--- | :--- |
| `IBKR_TOKEN` | IBKR 网页后台：**业绩与报表** ➔ **Flex 查询** ➔ **Flex Web Service 配置** 生成的当前 Token（建议过期时间选 1 年） |
| `IBKR_QUERY_ID` | IBKR 网页后台创建的 **活动 Flex 查询** 模版的数字 ID（需包含净资产、持仓与现金板块） |
| `TG_BOT_TOKEN` | Telegram 向 `@BotFather` 发送 `/newbot` 获取的 Bot API Token |
| `TG_CHAT_ID` | 你的 Telegram 用户 ID（向 `@userinfobot` 发送任意消息即可获取数字 ID） |

### 第三步：一键测试与自动运行
1. 进入仓库的 **Actions** 标签页。
2. 在左侧选择 **IBKR Daily Telegram Report** 工作流。
3. 点击右侧 **Run workflow** 绿色按钮进行手动测试。
4. 稍等约 10~20 秒，你的 Telegram 即可收到第一条排版整洁的 IBKR 每日投资总结！
5. **后续全自动静默运行**：系统将在每个美股交易日结束后（北京时间周二至周六晨间 06:30）自动推送。

---

## 📁 文件结构

```text
├── .github/
│   └── workflows/
│       └── daily_report.yml  # GitHub Actions 定时调度工作流
├── main.py                   # 核心拉取、解析与推送逻辑
├── requirements.txt          # Python 依赖项 (requests)
├── .env.example              # 本地测试环境变量模版
├── .gitignore                # Git 忽略配置
└── README.md                 # 快速部署说明
```
