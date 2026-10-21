# 公開前安全審查報告（Public Release Audit）— 2026-10-21

> **讀者**：repo 擁有者（Domino101）與**後續接手的 AI / 工程師**。
> 本文件記錄 `grid-trading-console` 由 private 轉 public 前的完整檢查結果、每一項「被剔除 / 被改寫」內容的**原因**，以及哪些步驟仍待人工完成。
> 日後若有人疑惑「為什麼 `config/exchanges` 只剩 `.example.yaml`」「為什麼 `.gitignore` 封鎖 `*.zip`」「為什麼要重寫歷史」，答案都在這裡。

---

## 0. 狀態板（先看這裡）

| # | 項目 | 狀態 | 由誰完成 |
|---|------|------|----------|
| 1 | 工作樹 (HEAD) 清理：惡意 zip、token、IP、被 ignore 卻追蹤的檔案 | ✅ 已完成（本分支 `arena/8533ffd4-grid-trading-console`） | AI |
| 2 | 結構性防護：`config/exchanges` example 化、`.gitignore` 強化、CI 守門 `scripts/public_repo_guard.sh` | ✅ 已完成 | AI |
| 3 | 歷史重寫腳本 `scripts/purge_history.sh`（已在本機鏡像上實測通過） | ✅ 已完成 | AI |
| 4 | 將本分支合併進 `main` | ⏳ 待執行 | 擁有者 |
| 5 | 執行 `scripts/purge_history.sh` 並 force-push（AI 權限僅能推送本分支，無法代勞） | ⏳ 待執行 | 擁有者 |
| 6 | 正式機 checkout 重新對齊（備份 → `reset --hard` → 還原被 ignore 的本機檔） | ⏳ 待執行 | 擁有者 |
| 7 | 輪換 `GRID_API_TOKEN`（若正式機 `.env` 曾沿用範本值） | ⏳ **立即執行** | 擁有者 |
| 8 | 確認沒有任何 Windows 機器執行過兩個 zip 內的 `Launch.bat` / `Launcher.cmd` | ⏳ 待確認 | 擁有者 |
| 9 | 向 GitHub Support 申請清除舊 PR ref / 快取中的敏感物件（或刪庫同名重建） | ⏳ 建議 | 擁有者 |
| 10 | 切換 visibility → public | ⏳ 第 4–9 項完成後 | 擁有者或 AI |

**決策紀錄（擁有者 2026-10-21 授權 AI 以保守預設處理）**：
- 實盤策略 YAML（`config/grid/*.yaml`、`config/dca/*.yaml`、`config/grid/archive/*`）→ **保留公開**（見 F-07）。
- `config/exchanges/*_config.yaml` → **改名為 `*_config.example.yaml`，真實檔名加入 `.gitignore`**（見 F-05）。
- LICENSE → **暫不加入**（預設保留所有權利；見 F-06）。

---

## 1. 範圍與方法

| 面向 | 內容 |
|------|------|
| 版本 | `main @ bcf2236`（2026-10-20），追蹤檔案 900 個，工作樹 144 MB |
| 歷史 | `git fetch --unshallow` 後完整 106 個 commit（2026-05-01 `f3546cb` 起），含遠端分支 `arena/81b539a4-…` 與標籤 `v0.3.0` |
| 靜態掃描（HEAD 與**全部歷史**） | 64-hex 高熵字串、`0x`+64hex 私鑰、PEM、平台金鑰格式（OpenAI/GitHub/AWS/Slack/Google/Telegram bot token）、`api_key/secret/passphrase/password` 指派、以太坊地址、Email、公/私網 IP、家目錄路徑、SSH/主機名稱、Telegram chat id、`account_index`/錢包欄位 |
| 檔案層 | `git ls-files -i -c`（已追蹤卻被 ignore）、>1 MB 大檔、所有壓縮檔/執行檔（含魔術數字檢查）、所有曾存在於歷史的敏感路徑 |
| 設定與部署 | `.env.example`、`config/**`、`ecosystem.config.js`、systemd unit、`scripts/**`、`.agents/**`、`.trae/**`、`docs/**` |
| 程式碼安全面 | Web UI 認證（`web_ui/backend/auth.py`：無預設 token、`secrets.compare_digest`、未設定時產生 0600 檔案）— 無硬編碼後門 |
| 授權 | 本 repo 無 LICENSE；上游 `cryptocj520/crypto-trading-open`（1.7k★）亦無 LICENSE |
| GitHub 設定 | private、無 release、2 個已合併 PR（#1、#2）、1 個過期 session 分支、App 權限不足以讀 secrets/deploy keys/webhooks |

---

## 2. 發現總表

| ID | 嚴重度 | 內容 | 位置 | 處置 | 狀態 |
|----|--------|------|------|------|------|
| F-01 | 🔴 Critical | **兩個惡意 zip（LuaJIT loader）** | `core/services/volume_maker/crypto_open_trading_v2.8.zip`、`core/domain/value_objects/crypto-trading-open_3.0.zip` | HEAD 刪除 + `.gitignore` 封鎖 + CI 守門 + **需歷史重寫** | HEAD ✅ / 歷史 ⏳ |
| F-02 | 🔴 High | 真實樣貌的 `GRID_API_TOKEN`（64-hex）寫在範本 | `.env.example:18`（自 `948c757` 2026-08-14 起） | 改為佔位符 + **必須輪換** + 歷史抹除 | HEAD ✅ / 輪換 ⏳ |
| F-03 | 🟠 Medium | 維運者真實對外 IP（HiNet 動態 IP，同一 HiNet /16 網段內的兩個位址） | `docs/gemini_legacy_reports/diagnosis_binance_ip_ban_and_apikey_20260503.md`、`work_log_20260503.md`（自 `7828e35`）；歷史中的 `reset_and_test.py` | 改為 `<REDACTED_PUBLIC_IP>` + 歷史抹除 | HEAD ✅ / 歷史 ⏳ |
| F-04 | 🟠 Medium | 已列 `.gitignore` 卻被追蹤：`config/grid/archived/`（16 檔）、`graphify-out/2026-09-0[1-8]/`（40 檔，約 110 MB）、`graphify-out/cache/`（2 檔） | 同左 | `git rm --cached`（保留正式機本地檔）+ 歷史移除 | HEAD ✅ / 歷史 ⏳ |
| F-05 | 🟠 Medium（結構性） | `config/exchanges/<x>_config.yaml` 被追蹤；正式機上同名檔存真實金鑰，日後 `git add -A` 即外洩 | `config/exchanges/` | 改名 `*_config.example.yaml`、ignore 真實檔名、新增 README | ✅ |
| F-06 | 🟡 Low（法務） | 無 LICENSE；上游無授權條款 | repo 根目錄 | 暫不加；README 註明 | ✅（決策） |
| F-07 | 🟡 Low（隱私） | 實盤策略參數公開（價格區間、125x 槓桿、下單量、bot_id） | `config/grid/*.yaml`、`config/dca/*.yaml`、`config/grid/archive/*` | 保留（決策）；提供反悔步驟 | ✅（決策） |
| F-08 | ⚪ Info | `/root/grid-trading-console` 等路徑、私網 IP `10.172.0.2`、餘額截圖數字、備份 `.md`、過期 session 分支 | 多處 | 不處理，列入觀察 | — |
| F-09 | ⚪ Info | GitHub 安全設定（secret scanning / push protection / Dependabot / branch protection） | GitHub | 公開後啟用 | ⏳ |

---

## 3. 各項詳述

### F-01 兩個惡意 zip（🔴 Critical）

**是什麼**

| 檔案 | 大小 | 內容 | SHA-256 |
|------|------|------|---------|
| `core/services/volume_maker/crypto_open_trading_v2.8.zip` | 517 KB | `Launch.bat`（`start luad.exe rsp.json`）、`luad.exe`（Windows PE）、`rsp.json`（310 KB，**實為混淆 Lua**） | `d1a9069d1c7956be2e267b393a27ddb02f0cdc1bc0b796087a0a97513ac581e7` |
| `core/domain/value_objects/crypto-trading-open_3.0.zip` | 1.4 MB | `Launcher.cmd`（`start luajit.exe cert.txt`）、`luajit.exe` + `lua51.dll`（Windows PE）、`cert.txt`（355 KB，**實為混淆 Lua**） | `596ef12897f12134dd552d6267fe582a5462db05e69a7fc3c52c330e49542167` |

內層檔案 SHA-256：`rsp.json`/`cert.txt` 皆以 `return(function(...)local d=function(W)...` 開頭，字串被切成數千個 `\ddd` 碎片再由索引表重組（`cert.txt` 4,595 片、`rsp.json` 3,156 片），並內嵌 base64 blob。`luad.exe` / `luajit.exe` / `lua51.dll` 皆為 `MZ` 標頭之 Windows 可執行檔。

**為何確定是惡意而非誤判**

1. 副檔名偽裝（`.json`/`.txt` 裝 Lua 程式）+ 重度混淆 + 以合法直譯器（LuaJIT）載入 ── 這是 2024–2025 年 GitHub「假下載頁」散佈 Lua loader 的標準手法，後續階段通常是資訊竊取（瀏覽器密碼/Cookie、Discord/Telegram session、加密錢包）、剪貼簿位址置換或 RAT。
2. 來源可直接對上：`docs/archive/README_fake_download_page.md` 保存了本專案最初複製來源的 README，其下載連結指向 `github.com/HamaX83/crypto-trading-open/raw/refs/heads/main/core/services/volume_maker/crypto_open_trading_v2.8.zip` ── **與本 repo 內的路徑、檔名完全相同**。即本專案是從被植入木馬的 fork（HamaX83）而非原作者（cryptocj520）複製而來。
3. 專案任何 Python/Go/TS 程式碼都沒有引用這兩個檔案；它們對系統功能毫無作用。
4. 兩檔皆自第一個 commit `f3546cb`（2026-05-01）存在，之後從未被修改。

**風險**

- 對擁有者：檔案在 Linux 伺服器上不會自行執行；**但若任何人曾在 Windows 上解壓執行**，該機器須視為失陷 → 輪換所有交易所 API Key、錢包私鑰、GitHub/交易所密碼與 2FA，並檢查提幣紀錄。
- 對公開：repo 將成為惡意程式托管點（GitHub 掃描可能標記/停用；clone 者防毒報警；信譽與法律風險），且 `git show f3546cb:<path>` 可直接取得完整檔案。

**處置**

- HEAD：`git rm` 兩檔（本分支）。
- 防再犯：`.gitignore` 封鎖 `*.zip *.7z *.rar *.tar *.gz *.tgz *.exe *.dll *.msi *.scr *.bat *.cmd`；`scripts/public_repo_guard.sh` G3/G3b 以副檔名 + 魔術數字（`MZ`/`PK`/`ELF`）雙重攔截並接入 `scripts/ci.sh`。
- 歷史：`scripts/purge_history.sh` 以 `git filter-repo --invert-paths` 移除；**須由擁有者執行 force-push**（第 5 節）。

### F-02 `.env.example` 內的 `GRID_API_TOKEN`（🔴 High）

- 值：`7250cdee…f76f`（64-hex，格式與 `web_ui/backend/auth.py` 以 `secrets.token_hex(32)` 產生的正式 token 完全一致，不像人工編造的範例）。
- 自 `948c757`（2026-08-14）進入歷史。若正式機 `.env` 是 `cp .env.example .env` 後只改金鑰而沿用此值，則公開後**任何人都能呼叫 Web UI / REST API（啟停網格、改配置、下單）**。
- 處置：HEAD 改為 `replace_with_a_random_64_hex_token` 並附產生指令；歷史以 `--replace-text` 抹為 `<REDACTED_GRID_API_TOKEN>`；守門 G5 阻擋範本再出現 32+ hex 的值。
- **無論是否重寫歷史，都應立即在正式機更換 `GRID_API_TOKEN` 並重啟 `grid-web-ui`。**

### F-03 真實對外 IP（🟠 Medium）

- 中華電信 HiNet 動態 IP（本文不載明具體值）出現在 2026-05-03 兩份診斷/工作日誌中，用於說明 Binance IP 白名單失效事件；另一個同網段的更早 IP 出現在歷史中已刪除的 `reset_and_test.py`（HEAD 不存在，僅歷史）。
- 風險：定位維運者所在 ISP / 地區，配合其他資訊可用於針對性攻擊；雖為動態 IP 仍屬個資。
- 處置：HEAD 改為 `<REDACTED_PUBLIC_IP>`；歷史以 `--replace-text` 的網段 regex（`36\.224\.\d+\.\d+`）一次抹除兩個位址；守門 G6 以同一 regex 阻擋回流。腳本與本報告皆不載明真實值。

### F-04 已列 `.gitignore` 卻被追蹤的檔案（🟠 Medium）

| 路徑 | 數量 | 為何在 ignore 清單 | 為何仍被追蹤 |
|------|------|------------------|--------------|
| `config/grid/archived/*.yaml` | 16 | Web UI 刪除網格時把 YAML **搬到**此處當轉儲（`web_ui/backend/routes/grids.py:1113`）；`lifecycle.py:148` 明示「never archived dumps」 | 在 `948c757` 被追蹤，`62e3ba4` 才加 ignore 規則，ignore 對已追蹤檔案無效 |
| `graphify-out/2026-09-0[1-8]/**` | 40（約 110 MB） | graphify 每日快照，可由 `graphify update .` 重建 | 同上（`b0a6af7` 起） |
| `graphify-out/cache/**` | 2 | 查詢快取（含本機 mtime 與 `/root/...` 索引） | 同上 |

處置：`git rm -r --cached`（**只移出索引，不刪工作樹**，正式機的本地檔案不受影響）。守門 G1 以 `git ls-files -i -c --exclude-standard` 永久攔截此類情況。
保留追蹤的 `graphify-out/graph.json`（15 MB）、`graph.html`、`GRAPH_REPORT.md`、`manifest.json`、`.graphify_labels.json*` 是 `AGENTS.md` 依賴的知識圖譜主體，屬刻意追蹤。

### F-05 `config/exchanges/*_config.yaml` 的結構性風險（🟠 Medium）

- 現狀：HEAD 與全部歷史中的 7 個檔案**只含佔位符**（`1111…`、`0x...`、空字串；曾出現的 `account_index: 65` 位於檔內 `examples.testnet` 範例段落）。
- 問題：程式以固定檔名讀取（`run_grid_trading.py:598`、`lighter.py:172`、`hyperliquid_base.py:81`、`o1exchange_base.py:75`、`edgex.py:102`），因此**正式機上的同名檔就是真實金鑰檔**，而它們同時是被 git 追蹤的檔案 ── 任何一次 `git add -A` / `git commit -a` 就會把真實金鑰推上公開 repo。`scripts/publish_private_repo.sh` 當初就是因此整個排除 `config/exchanges/`。
- 處置：
  - `git mv <x>_config.yaml <x>_config.example.yaml`（backpack / binance / edgex / hyperliquid / lighter / o1exchange；原本就叫 `hyperliquid_example.yaml` 者不動）。
  - `.gitignore`：`config/exchanges/*_config.yaml` + `!config/exchanges/*.example.yaml`。
  - 新增 `config/exchanges/README.md` 說明複製方式與載入優先順序。
  - 相容性：所有載入點在檔案不存在時都會略過並改用環境變數（已逐一核對），fresh clone 不會當機；正式機只需保留既有的真實檔（已被 ignore）。
  - 守門 G2 攔截 `config/exchanges/<x>_config.yaml`、`.env`、`.grid_api_token`、`.secrets/`、`*.pem/.key/.p12` 等被追蹤。

### F-06 授權（🟡 Low，法務）

- 本 repo 無 LICENSE → 依著作權預設**保留所有權利**：他人可瀏覽與 fork（GitHub ToS），但不得合法使用、修改、散布。
- 上游 `cryptocj520/crypto-trading-open` 同樣無 LICENSE。本專案的交易所適配器、`run_*.py`、`grid_volatility_scanner` 等源自上游；Web UI、DCA、CoinFull Prime、回測、資料層為本專案新增。**嚴格而言，源自上游的部分不能由本專案單方面套用 MIT/Apache 等授權**；若日後要加 LICENSE，建議（a）只對自行撰寫的部分聲明，或（b）先取得上游作者同意，並在 README 標註來源。
- 擁有者決策：暫不加 LICENSE。README 已加註「授權與來源」段落。

### F-07 實盤策略 YAML 公開（🟡 Low，隱私）

- 內容：交易對、價格區間、槓桿（最高 125x）、每格下單量、DCA 階梯參數、`metadata.bot_id`。不含金鑰；`bot_id` 只對應本機 SQLite。
- 擁有者決策：**保留公開**，作為真實可用的設定範例。
- 若日後反悔：`git rm --cached config/grid/*.yaml config/dca/dca-*.yaml config/grid/archive/*.yaml`，於 `.gitignore` 加入對應規則並保留 `defaults.yaml`、`example-*.yaml`、`README.md`；**注意正式機**：若本地檔未修改，之後 `git pull` 會把它們從工作樹刪除 → 先備份 `config/` 再 pull，pull 後還原。

### F-08 其他低風險資訊（⚪ Info，未處理）

- `/root/grid-trading-console`、`/root/crypto-trading-open`、`/root/nofx*`：出現在 docs、`scripts/remediate_uni_dca.py`、`scripts/reset_uni_dca_state.py`、`graphify-out/.graphify_python`。僅透露部署路徑與以 root 執行過的事實。
- `.agents/skills/traffic-audit/scripts/traffic_stats.py:37` 的私網 fallback `10.172.0.2`。
- `docs/gemini_legacy_reports/trading_dashboard_improvement_plan.md:51` 的餘額截圖數字 `672.24454 USDT`。
- 備份/殘留檔：`core/adapters/exchanges/adapters/backpack_backup.py.md`、`edgex_backup.md`、`hyperliquid_base copy.md`、`core/services/grid/implementations/order_health_checker.py.backup_*`（歷史）。
- 遠端分支 `arena/81b539a4-grid-trading-console`（已合併的舊 session，建議刪除）。
- commit 作者含 `Your Name <…>`、`Trae AI`、`Antigravity AI`、`Arena Agent`：無個資疑慮。
- `.agents/skills/*` 內含第三方技能（graphify、ponytail、skill-creator=Apache-2.0 已附 LICENSE.txt、ui-ux-pro-max、unslop 等）：若公開後收到授權要求再補 NOTICE。

### F-09 GitHub 端建議（⚪ Info）

公開後於 Settings → Code security 啟用：**Secret scanning + Push protection**、**Dependabot alerts/security updates**、**Private vulnerability reporting**；Branches → 保護 `main`（禁止 force-push、需 PR）—— 注意第 5 節的 force-push 要在啟用保護**之前**完成。

---

## 4. 本分支已執行的變更

```
D  core/services/volume_maker/crypto_open_trading_v2.8.zip        # F-01（同時刪工作樹）
D  core/domain/value_objects/crypto-trading-open_3.0.zip           # F-01（同時刪工作樹）
D  config/grid/archived/*.yaml (16)                                # F-04（--cached）
D  graphify-out/2026-09-0[1-8]/** (40), graphify-out/cache/** (2)  # F-04（--cached）
R  config/exchanges/{x}_config.yaml -> {x}_config.example.yaml (6) # F-05
A  config/exchanges/README.md                                      # F-05
M  .env.example                                                    # F-02 token → 佔位符
M  docs/gemini_legacy_reports/{diagnosis_binance_ip_ban_and_apikey_20260503,work_log_20260503}.md  # F-03
M  .gitignore                                                      # F-01/F-05 防再犯
A  scripts/public_repo_guard.sh                                    # 守門 G1–G6
M  scripts/ci.sh                                                   # 接入守門
A  scripts/purge_history.sh                                        # 歷史重寫（擁有者執行）
A  docs/PUBLIC_RELEASE_AUDIT_20261021.md                           # 本報告
M  AGENTS.md, README.md, CHANGELOG.md                              # 守則 / 說明 / 變更紀錄
```

---

## 5. 尚待擁有者執行的步驟（依序）

```bash
# (1) 合併清理分支到 main —— 建議本機 fast-forward，避免再多一個會釘住舊歷史的 PR ref
git fetch origin
git checkout main && git merge --ff-only origin/arena/8533ffd4-grid-trading-console
git push origin main

# (2) 立即輪換 Web UI token（不要等歷史重寫）
python3 -c "import secrets; print(secrets.token_hex(32))"   # 寫入正式機 .env 的 GRID_API_TOKEN，然後 pm2 restart grid-web-ui

# (3) 歷史重寫（在你自己的機器，gh 以 Domino101 登入）
pip install git-filter-repo
./scripts/purge_history.sh --dry-run   # 先看驗證輸出
./scripts/purge_history.sh             # 重寫 + force-push 所有分支與標籤

# (4) 正式機重新對齊（腳本結尾會再印一次）
cd /root/grid-trading-console && cp -a . ../grid-trading-console.bak-$(date +%F)
git fetch origin && git checkout main && git reset --hard origin/main
cp ../grid-trading-console.bak-*/config/exchanges/*_config.yaml config/exchanges/ 2>/dev/null || true
cp -a ../grid-trading-console.bak-*/config/grid/archived config/grid/ 2>/dev/null || true

# (5) GitHub Support：請求移除 refs/pull/1、refs/pull/2 與快取中的舊物件
#     （舊 commit：f3546cb… 起全部；惡意 zip 自首個 commit 即存在）。或刪庫後同名重建。

# (6) 切 public
gh repo edit Domino101/grid-trading-console --visibility public --accept-visibility-change-consequences

# (7) 公開後：啟用 secret scanning / push protection / Dependabot；刪除過期分支
git push origin --delete arena/81b539a4-grid-trading-console
```

**為什麼第 (4) 步要備份再 `reset --hard`**：重寫後的歷史與正式機現有 commit 不相容，不能 merge/pull；`reset --hard` 會讓工作樹與新 `main` 完全一致 ── 也就是會**刪除**「舊 HEAD 有追蹤、新 HEAD 不再追蹤且本地未修改」的檔案（`config/grid/archived/*`、未改過的 `config/exchanges/*_config.yaml`），並**覆寫**「本地有修改」的追蹤檔（例如 Web UI 改寫過的 `config/grid/*.yaml`，若與 repo 版本不同）。備份後再還原即可；還原回來的真實金鑰檔已被 ignore，`git status` 應保持乾淨。

---

## 6. 歷史重寫的實測結果（AI 在本機鏡像上驗證，2026-10-21）

`scripts/purge_history.sh` 已於沙箱內對本 repo 的完整歷史鏡像（`main` = 清理 commit、舊 session 分支、標籤 `v0.3.0`，共 107 commits）以「真正推送到本機假遠端」模式執行，結果：

| 檢查 | 結果 |
|------|------|
| 兩個惡意 zip 於所有 commit | 0 筆（原 2 筆） |
| `graphify-out/2026-*/`、`graphify-out/cache/`、`config/grid/archived/` 於所有 commit | 0 筆 |
| `GRID_API_TOKEN=<64hex>` 於所有 blob | 0 筆（原 73 筆，含歷史 `.env.example` 各版本） |
| 維運者 IP 網段於所有 blob | 0 筆（原 414 + 58 筆，含已刪除的 `reset_and_test.py`） |
| commit 數 | 107 → 107（無 commit 被清空） |
| bare repo 大小 | 13 MB → 7.5 MB |
| 標籤 `v0.3.0` | 仍為 annotated tag，指向重寫後的對應 commit |
| 重寫後 `main` 的 tree | 與清理 commit 的 tree **逐位元相同**（腳本與守門使用通用 regex，不含敏感值，重寫不會改動它們；可重複執行） |

另以 `.venv` 安裝全部依賴後執行 12 個相關測試模組（import contract、lifecycle/safety、HTTP contract、DCA config loader/yaml generator、dashboard snapshot、worker isolation、accounting path policy、database guard、preflight、exchange audit、secret vault）：94 個測試中 88 個通過；6 個失敗全部位於 `tests/test_web_ui_http_contract.py`，需要連線 Binance 做交易對驗證，**在未修改的 `origin/main` 上失敗集合完全相同**（沙箱無交易所網路），與本次變更無關。

---

## 7. 驗證指令（任何人可重跑）

```bash
scripts/public_repo_guard.sh                       # G1–G6 全綠
git ls-files -i -c --exclude-standard              # 應為空
git ls-files | grep -iE '\.(zip|exe|dll)$'         # 應為空
git grep -n '7250cdee' $(git rev-list --all)       # 重寫後應為空（重寫前只會命中 .env.example）
git log --all --oneline -- core/services/volume_maker/crypto_open_trading_v2.8.zip   # 重寫後應為空
```

---

## 8. 給後續接手 AI 的守則

1. **不要**把 `config/exchanges/*.example.yaml` 改回 `*_config.yaml` 提交；**不要**在任何範本放真實值（含 `account_index`、錢包地址、chat id）。
2. **不要**把壓縮檔、執行檔、資料庫、`data/`、`logs/`、graphify 每日快照加入版本控制；`.gitignore` 與 `scripts/public_repo_guard.sh` 的封鎖是刻意的，不要為了「方便」放寬。
3. 若 `scripts/ci.sh` 因 `public_repo_guard` 失敗，**先處理違規，不是改守門規則**；若確屬誤判（例如文件中引用的 SHA-256），在守門腳本的白名單 regex 內補註明原因。
4. `.env.example` 中任何 `*_KEY/*_SECRET/*_TOKEN` 必須是明顯佔位符（`your_…`、`replace_…`、空值）。
5. 看到 `/root/...`、個人 IP、帳戶餘額等出現在新文件時，改用 `<PROJECT_ROOT>`、`<REDACTED_*>`。
6. 本專案源於無授權條款的上游 fork，且該 fork 曾被植入木馬；評估「從上游同步更新」時，**先掃描上游內容**再合併。
7. 若需再次重寫歷史，沿用 `scripts/purge_history.sh` 的模式：全新 bare clone → filter-repo → 驗證 → force-push → 通知所有 clone `reset --hard`。

---

## 附錄 A：關鍵 commit

| SHA | 日期 | 事件 |
|-----|------|------|
| `f3546cb` | 2026-05-01 | 首個 commit；兩個惡意 zip 與 `config/exchanges/*_config.yaml` 進入 repo |
| `7828e35` | 2026-05-03 | 真實 IP 進入 docs |
| `948c757` | 2026-08-14 | `.env.example` 寫入真實樣貌 token；`config/grid/archived/` 開始被追蹤 |
| `62e3ba4` | 2026-08-26 | `.gitignore` 加入 `config/grid/archived/`（但未取消追蹤） |
| `b0a6af7` | 2026-09-01 | `graphify-out/2026-09-01/` 等每日快照開始被追蹤 |
| `bcf2236` | 2026-10-20 | 審查基準（`main` HEAD） |

## 附錄 B：掃描時排除的雜訊來源

`web_ui/frontend/package-lock.json`（integrity hash）、`trading-core-go/go.sum`、`*.pb.go`、`graphify-out/**`（檔案 SHA-256 索引）、`tests/test_o1exchange.py:63` 的 `0x1234…7890` 測試地址、`.agents/skills/traffic-audit` 內的 Cloudflare 公開 CIDR 清單、`docs/gemini_legacy_reports/telegram_deployment_guide.md` 的範例 token/chat id。
