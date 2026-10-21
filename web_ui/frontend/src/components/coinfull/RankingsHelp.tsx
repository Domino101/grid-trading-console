import React from 'react';

export const RankingsHelp: React.FC = () => (
  <details className="cfp-panel cfp-help">
    <summary>策略指標與決策原則</summary>
    <div className="cfp-help-grid">
      <p><strong>多時距動能（60%）</strong>：1 日（50%）、7 日（30%）、30 日（20%）日線漲跌幅。Z &gt; 1.5 為動能極強，Z &lt; -1.5 為動能極弱。</p>
      <p><strong>平滑資金費率反向（40%）</strong>：取最近 3 次 8 小時結算費率的平滑平均並反向計分；深度負費率代表貼水超賣。</p>
      <p><strong>基準指數對沖</strong>：空方以 BTC（或 BTC + ETH）佔 50% 名義資金對沖系統性 Beta，多方維持 Top 5 動能強者。</p>
      <p><strong>Regime Gate</strong>：監測 BTC 50 日均線、宇宙 50 日廣度（&gt;30%）與全市場費率百分位（&gt;45%），具 T1 停單清倉與 T2 崩跌延後。</p>
      <p><strong>0% 因子</strong>：主動買賣流與大戶多空比已依實證統計停用，預設隱藏，可由工具列開啟檢視。</p>
    </div>
  </details>
);
