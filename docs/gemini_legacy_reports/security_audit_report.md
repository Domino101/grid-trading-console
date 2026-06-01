# 🛡️ 專案安全性與源碼審查報告

為了確保您的資金安全與 API / 私鑰不被外洩，我們對 `ccjingeth/predict-fun-marketmaker` 的原始碼進行了嚴格的靜態分析。以下是詳細的安全性稽核結果：

## 1. 私鑰 (Private Key) 與 API Key 處理機制
> [!IMPORTANT]
> **結論：安全。私鑰僅用於本地端簽章，從未透過網路明文傳輸。**

* **認證機制 (JWT Auth)**: 
  在 `src/auth-jwt.ts` 與 `src/index.ts` 中，專案使用了標準的 Web3 簽章認證機制（類似 Sign-In with Ethereum）。
  您的 `PRIVATE_KEY` 僅在您的本地電腦上透過 `ethers.js` 庫實例化為錢包（`new Wallet(config.privateKey)`）。
  程式會向官方 API 請求一段隨機字串（Message），接著用您的私鑰在「本地端」對這段字串進行數學簽章，最後只把**「簽章結果」**和**「錢包地址」**傳送給官方伺服器換取 JWT Token。
* **API Key**: 
  `API_KEY` 被讀取後，僅作為 HTTP 標頭（`x-api-key`）傳送至官方的 API 網域（預設為 `https://api.predict.fun` 及 Polymarket 官方節點），並無傳送至其他第三方的跡象。

## 2. 外部網路連線與後門檢查
> [!TIP]
> **結論：乾淨。沒有發現未知的遙測 (Telemetry)、後門或惡意數據回傳。**

我們掃描了整個源碼中的 `http`, `https`, `fetch`, `axios` 等網路請求函式，結果如下：
* **官方連線**: 絕大多數的請求都指向 `https://api.predict.fun`、Polymarket 的 Gamma / CLOB API，以及 Opinion 等官方做市節點。
* **Webhook 通知機制**: 在 `src/utils/alert.ts` 中發現了一處會向外部發送 `axios.post` 的代碼。進一步追蹤發現，這是專案提供的**「自定義通知功能」**。使用者可在設定檔中填入自己的 Discord/Telegram Webhook URL。若您沒有設定，這段程式碼會直接忽略，不會有任何隱私外洩風險。
* **外部市場數據**: 在 `src/external-data/` 目錄下（如 `box-office-api.ts` 和 `election-api.ts`），發現了對外請求票房（貓眼、淘票票）與選舉（election.gov、出口民調）等公開資料的 API。這對於預測市場做市商來說是正常的「獲取外部現實世界數據來計算公允價格」的行為，且這些請求**皆未攜帶任何本機密鑰或環境變數**。

## 3. 依賴套件 (Dependencies)
> [!NOTE]
> **結論：使用了業界標準且主流的開源庫，無可疑依賴。**

檢視了 `package.json`：
* **Web3 核心**: `ethers` (v6), `viem`, `@predictdotfun/sdk`, `@polymarket/clob-client`, `@polymarket/order-utils`。這些皆為官方或極具公信力的區塊鏈開發套件。
* **網路與工具**: `axios`, `ws`, `express`, `dotenv`, `typescript`。皆為標準的 Node.js 基礎建設套件。
* 沒有發現任何容易藏匿惡意程式碼的生僻/未知名套件。

## 4. 程式碼混淆與動態執行
> [!NOTE]
> **結論：全開源 TypeScript，無混淆、無隱藏腳本。**

* 源碼中沒有使用任何代碼混淆工具（如 javascript-obfuscator）。
* 沒有使用 `eval()`、`new Function()` 或動態從遠端下載並執行 JavaScript 腳本的高風險行為。
* 在 `package.json` 中的 `scripts` 也僅有標準的 `tsc` 編譯與 `tsx` 執行指令，無惡意的 `postinstall` 勾子 (Hooks)。

---

### 💡 總結與安全建議

這份開源專案的代碼是**透明且安全**的，其交易邏輯與權限管理均符合正規的 Web3 交易機器人開發規範。您可以放心使用。

**為了確保 100% 的安全，提供給您的最佳實踐建議：**
1. **使用專屬做市錢包**：強烈建議**不要**使用存有大量個人資金或具有重要 NFT 的主錢包來做市。請創建一個「全新的專屬錢包」，裡面只存放預定要做市的 USDC 與少許 Gas Fee (POL/MATIC/BNB)。這樣即便發生任何極端狀況，損失也絕對可控。
2. **切勿分享 `.env` 檔案**：您的密鑰將會保存在目錄下的 `.env` 檔案中，請確保該檔案不會不小心被上傳到 GitHub 或傳送給他人。
3. **官方管道下載**：如果您要使用桌面端 GUI 介面，請務必只從該專案的 [官方 GitHub Releases](https://github.com/ccjingeth/predict-fun-marketmaker/releases) 頁面下載，避免使用來路不明的第三方載點。
