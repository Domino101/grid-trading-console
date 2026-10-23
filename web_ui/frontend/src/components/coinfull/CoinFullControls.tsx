import React from 'react';
import { AlertTriangle, RefreshCw, ShieldCheck, Sliders, Wallet, Zap } from 'lucide-react';
import type { CoinFullPrimeController } from './useCoinFullPrime';
import { formatUsd } from './format';
import { t } from '../../i18n/index.ts';

interface Props {
  controller: CoinFullPrimeController;
}

export const CoinFullControls: React.FC<Props> = ({ controller }) => {
  const {
    accounts,
    currentAccount,
    selectedAccount,
    dryRunMode,
    sizingMode,
    capitalInput,
    balancePct,
    leverage,
    manualSizing,
    scheduleAligned,
    rebalancing,
    refreshing,
    actions,
  } = controller;
  const options = accounts.some((account) => account.id === selectedAccount)
    ? accounts
    : [currentAccount, ...accounts];
  const isPaper = currentAccount.environment === 'paper';
  const blocked = !manualSizing.affordable;

  return (
    <section className="cfp-panel cfp-controls" aria-label={t("交易控制")}>
      <div className="cfp-panel-head">
        <span>{t("ACCOUNT CONFIG / 交易帳號設定")}</span>
        <span className="cfp-panel-state"><i aria-hidden="true" />{t("API 已驗證 · 唯讀金鑰")}</span>
      </div>
      <div className="cfp-control-row">
        <label className="cfp-field cfp-account-field">
          <span className="cfp-field-label"><Wallet size={14} aria-hidden="true" />{t("交易帳號")}</span>
          <select
            className="cfp-select"
            value={selectedAccount}
            onChange={(event) => actions.selectAccount(event.target.value)}
            disabled={rebalancing}
          >
            {options.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
                {account.balance_usdt !== null ? ` (${formatUsd(account.balance_usdt)})` : ''}
                {!account.is_ready && account.environment !== 'paper' ? t("（未配置金鑰）") : ''}
              </option>
            ))}
          </select>
          {!currentAccount.is_ready && !isPaper && (
            <span className="badge badge-danger">{t("未配置金鑰")}</span>
          )}
        </label>

        <div className="cfp-field">
          <span className="cfp-field-label">{t("執行模式")}</span>
          <div className="cfp-seg" role="group" aria-label={t("執行模式")}>
            <button
              type="button"
              className={dryRunMode ? 'is-active' : ''}
              aria-pressed={dryRunMode}
              onClick={() => actions.requestModeChange(true)}
              disabled={isPaper}
            >
              {t("僅試算")}{' '}</button>
            <button
              type="button"
              className={!dryRunMode ? 'is-active is-danger' : ''}
              aria-pressed={!dryRunMode}
              onClick={() => actions.requestModeChange(false)}
              disabled={isPaper}
              title={isPaper ? t("Paper 帳號只能模擬撮合") : t("切換後手動換倉會送出真實訂單")}
            >
              {t("真實發單")}{' '}</button>
          </div>
        </div>

        <div className="cfp-field cfp-balance-field">
          <span className="cfp-field-label">{t("可用保證金")}</span>
          <span className="cfp-balance">
            {currentAccount.balance_usdt === null ? t("未取得") : formatUsd(currentAccount.balance_usdt)}
          </span>
        </div>
      </div>

      <div className="cfp-control-row cfp-control-row-secondary">
        <div className="cfp-field">
          <span className="cfp-field-label"><Sliders size={14} aria-hidden="true" />{t("本金規模")}</span>
          <div className="cfp-sizing">
            <div className="cfp-seg" role="group" aria-label={t("本金模式")}>
              <button
                type="button"
                className={sizingMode === 'fixed' ? 'is-active' : ''}
                aria-pressed={sizingMode === 'fixed'}
                onClick={() => actions.setSizing('fixed')}
              >
                {t("固定")}{' '}</button>
              <button
                type="button"
                className={sizingMode === 'percent' ? 'is-active' : ''}
                aria-pressed={sizingMode === 'percent'}
                onClick={() => actions.setSizing('percent')}
              >
                {t("餘額 %")}{' '}</button>
            </div>

            {sizingMode === 'fixed' ? (
              <label className="cfp-money-input">
                <span aria-hidden="true">$</span>
                <input
                  type="number"
                  min={0}
                  step={50}
                  inputMode="decimal"
                  value={capitalInput}
                  onChange={(event) => actions.updateCapital(event.target.value)}
                  aria-label={t("固定本金（USDT）")}
                />
                <span>USDT</span>
              </label>
            ) : (
              <div className="cfp-percent-input">
                <label className="cfp-money-input">
                  <input
                    type="number"
                    min={1}
                    max={100}
                    step={1}
                    inputMode="decimal"
                    value={balancePct}
                    onChange={(event) => actions.updateBalancePct(Number(event.target.value))}
                    aria-label={t("可用餘額百分比")}
                  />
                  <span>%</span>
                </label>
                <div className="cfp-quick-pcts" role="group" aria-label={t("快速選擇餘額百分比")}>
                  {[25, 50, 80, 100].map((value) => (
                    <button
                      key={value}
                      type="button"
                      className={balancePct === value ? 'is-active' : ''}
                      aria-pressed={balancePct === value}
                      onClick={() => actions.updateBalancePct(value)}
                    >
                      {value}%
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="cfp-field">
          <span className="cfp-field-label">{t("槓桿")}</span>
          <div className="cfp-seg" role="group" aria-label={t("槓桿倍數")}>
            {[1, 1.2, 1.5, 2].map((value) => (
              <button
                key={value}
                type="button"
                className={leverage === value ? 'is-active' : ''}
                aria-pressed={leverage === value}
                onClick={() => actions.updateLeverage(value)}
              >
                {value}x
              </button>
            ))}
          </div>
        </div>

        <div className="cfp-sizing-preview" aria-live="polite">
          <span>{t("總名義")}{' '}<strong>{formatUsd(manualSizing.grossExposure)}</strong></span>
          <span>{t("單幣")}{' '}<strong>{formatUsd(manualSizing.perSymbolNotional)}</strong></span>
          <span className="cfp-muted">{t("本金")}{' '}{formatUsd(manualSizing.capital)}</span>
        </div>

        <div className="cfp-control-actions">
          <button
            type="button"
            className="btn btn-outline"
            onClick={actions.requestApplySchedule}
            disabled={rebalancing || scheduleAligned || blocked}
            title={blocked ? t("本金超過可用保證金，請先調整倉位") : scheduleAligned ? t("排程已與目前設定一致") : t("將目前帳號、模式與倉位設定套用到背景排程")}
          >
            {scheduleAligned ? <ShieldCheck size={15} aria-hidden="true" /> : <RefreshCw size={15} aria-hidden="true" />}
            <span>{scheduleAligned ? t("排程已同步") : t("套用至排程")}</span>
          </button>
          <button
            type="button"
            className={`btn ${!dryRunMode ? 'btn-danger' : 'btn-primary'}`}
            onClick={actions.requestRebalance}
            disabled={rebalancing || refreshing || blocked}
            title={blocked ? t("本金超過可用保證金，請先調整倉位") : undefined}
          >
            <Zap size={15} aria-hidden="true" />
            <span>{rebalancing ? t("換倉執行中…") : !dryRunMode ? t("立即執行（真實）") : t("立即執行模擬換倉")}</span>
          </button>
        </div>
      </div>

      {!manualSizing.affordable && (
        <p className="cfp-inline-warning" role="alert">
          <AlertTriangle size={15} aria-hidden="true" />
          <span>
            {t("本金")}{' '}{formatUsd(manualSizing.capital)} {' '}{t("超過可用保證金")}{' '}{formatUsd(currentAccount.balance_usdt)}{t("，尚差")}{' '}{formatUsd(manualSizing.shortfall)}{t("。 請改用餘額百分比或降低本金。")}{' '}</span>
        </p>
      )}
    </section>
  );
};