import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { api, type MarketSymbolItem } from '../services/api';
import { Search, ChevronDown, Check, Loader2, X } from 'lucide-react';
import { t } from '../i18n/index.ts';

interface SymbolSelectProps {
  value: string;
  onChange: (value: string) => void;
  exchange: string;
  placeholder?: string;
  disabled?: boolean;
  id?: string;
  ariaDescribedBy?: string;
}

export const SymbolSelect: React.FC<SymbolSelectProps> = ({
  value,
  onChange,
  exchange,
  placeholder = t("搜尋或輸入交易對 (如: BTC/USDT 或 牛来USDT)"),
  disabled = false,
  id,
  ariaDescribedBy,
}) => {
  const [symbols, setSymbols] = useState<MarketSymbolItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [inputValue, setInputValue] = useState(value || '');
  const [prevValue, setPrevValue] = useState(value);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);

  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  // 當外部 value 變更時，依據 React 官方規範在 render 階段同步 state
  if (value !== prevValue) {
    setPrevValue(value);
    setInputValue(value || '');
  }

  // 自適應正規化檢查：若輸入正好對應某個交易所 ID，自適應替換為 canonical 符號
  const resolveCanonical = useCallback((raw: string): string => {
    const trimmed = raw.trim();
    if (!trimmed) return '';
    const q = trimmed.toLowerCase();
    const exact = symbols.find(
      (s) => s.id.toLowerCase() === q || s.symbol.toLowerCase() === q
    );
    if (exact) {
      return exact.symbol;
    }
    return trimmed;
  }, [symbols]);

  const handleClose = useCallback(() => {
    setIsOpen(false);
    setHighlightedIndex(-1);
    const canonical = resolveCanonical(inputValue);
    if (canonical !== inputValue) {
      setInputValue(canonical);
      onChange(canonical);
    } else if (inputValue !== value) {
      onChange(inputValue);
    }
  }, [inputValue, onChange, resolveCanonical, value]);

  // 預載該交易所交易對
  useEffect(() => {
    let active = true;
    const controller = new AbortController();

    const fetchSymbols = async () => {
      setLoading(true);
      try {
        const res = await api.getMarketSymbols(exchange, controller.signal);
        if (active && res && Array.isArray(res.symbols)) {
          setSymbols(res.symbols);
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name !== 'AbortError') {
          console.warn((t("[SymbolSelect] 載入交易對列表失敗 (") + (exchange) + "):"), err);
        }
      } finally {
        if (active) setLoading(false);
      }
    };

    void fetchSymbols();

    return () => {
      active = false;
      controller.abort();
    };
  }, [exchange]);

  // 點選外部關閉下拉
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        handleClose();
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [handleClose]);

  // 智慧過濾：比對 symbol, id, base
  const filteredSymbols = useMemo(() => {
    if (!inputValue.trim()) {
      return symbols.slice(0, 40);
    }
    const q = inputValue.trim().toLowerCase();
    const matches = symbols.filter((item) => {
      const sym = item.symbol.toLowerCase();
      const idStr = item.id.toLowerCase();
      const base = item.base.toLowerCase();
      return sym.includes(q) || idStr.includes(q) || base.includes(q);
    });
    return matches.slice(0, 50);
  }, [symbols, inputValue]);

  const handleSelect = (item: MarketSymbolItem) => {
    setInputValue(item.symbol);
    onChange(item.symbol);
    setIsOpen(false);
    setHighlightedIndex(-1);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (disabled) return;

    if (!isOpen && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      setIsOpen(true);
      return;
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlightedIndex((prev) =>
        prev < filteredSymbols.length - 1 ? prev + 1 : 0
      );
      scrollHighlightedIntoView(highlightedIndex + 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlightedIndex((prev) =>
        prev > 0 ? prev - 1 : filteredSymbols.length - 1
      );
      scrollHighlightedIntoView(highlightedIndex - 1);
    } else if (e.key === 'Enter') {
      if (isOpen && highlightedIndex >= 0 && highlightedIndex < filteredSymbols.length) {
        e.preventDefault();
        handleSelect(filteredSymbols[highlightedIndex]);
      } else {
        handleClose();
      }
    } else if (e.key === 'Escape') {
      setIsOpen(false);
      setHighlightedIndex(-1);
    }
  };

  const scrollHighlightedIntoView = (index: number) => {
    if (listRef.current && index >= 0) {
      const items = listRef.current.children;
      if (items[index]) {
        (items[index] as HTMLElement).scrollIntoView({
          block: 'nearest',
          behavior: 'smooth',
        });
      }
    }
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setInputValue(val);
    onChange(val);
    setIsOpen(true);
    setHighlightedIndex(0);
  };

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation();
    setInputValue('');
    onChange('');
    setIsOpen(true);
    inputRef.current?.focus();
  };

  return (
    <div
      ref={containerRef}
      className="symbol-select-container"
      style={{ position: 'relative', width: '100%' }}
    >
      <div
        className={`symbol-input-wrapper ${isOpen ? 'focused' : ''} ${disabled ? 'disabled' : ''}`}
        style={{
          display: 'flex',
          alignItems: 'center',
          position: 'relative',
        }}
      >
        <span
          style={{
            position: 'absolute',
            left: '0.85rem',
            pointerEvents: 'none',
            color: 'var(--text-muted, #888)',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          {loading ? (
            <Loader2 size={16} className="spin" style={{ color: 'var(--accent-cyan, #00e5ff)' }} />
          ) : (
            <Search size={16} />
          )}
        </span>

        <input
          ref={inputRef}
          id={id}
          aria-describedby={ariaDescribedBy}
          type="text"
          value={inputValue}
          onChange={handleInputChange}
          onFocus={() => !disabled && setIsOpen(true)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={placeholder}
          style={{
            width: '100%',
            paddingLeft: '2.5rem',
            paddingRight: inputValue ? '3.8rem' : '2.4rem',
          }}
          autoComplete="off"
          spellCheck={false}
        />

        <div
          style={{
            position: 'absolute',
            right: '0.75rem',
            display: 'flex',
            alignItems: 'center',
            gap: '0.35rem',
          }}
        >
          {inputValue && !disabled && (
            <button
              type="button"
              onClick={handleClear}
              className="btn-icon-subtle"
              title={t("清除")}
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--text-muted, #888)',
                cursor: 'pointer',
                padding: '0.2rem',
                display: 'flex',
                borderRadius: '4px',
              }}
            >
              <X size={14} />
            </button>
          )}
          <button
            type="button"
            onClick={() => !disabled && setIsOpen((prev) => !prev)}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--text-muted, #888)',
              cursor: disabled ? 'not-allowed' : 'pointer',
              padding: '0.2rem',
              display: 'flex',
            }}
          >
            <ChevronDown
              size={16}
              style={{
                transition: 'transform 0.2s',
                transform: isOpen ? 'rotate(180deg)' : 'rotate(0)',
              }}
            />
          </button>
        </div>
      </div>

      {isOpen && !disabled && (
        <div
          className="glass-panel symbol-dropdown-menu"
          style={{
            position: 'absolute',
            top: 'calc(100% + 4px)',
            left: 0,
            right: 0,
            zIndex: 1050,
            maxHeight: '260px',
            overflowY: 'auto',
            borderRadius: '8px',
            boxShadow: '0 8px 32px rgba(0, 0, 0, 0.45)',
            border: '1px solid var(--border-color, rgba(255, 255, 255, 0.12))',
            background: 'var(--dropdown-bg, rgba(16, 20, 30, 0.96))',
            backdropFilter: 'blur(16px)',
          }}
        >
          {filteredSymbols.length === 0 ? (
            <div
              style={{
                padding: '1rem',
                textAlign: 'center',
                color: 'var(--text-muted, #888)',
                fontSize: '0.85rem',
              }}
            >
              {loading ? (
                <span>{t("正在載入交易對列表...")}</span>
              ) : (
                <span>{t("找不到符合「")}{inputValue}{t("」的上市交易對")}</span>
              )}
            </div>
          ) : (
            <ul
              ref={listRef}
              style={{
                listStyle: 'none',
                margin: 0,
                padding: '0.35rem 0',
              }}
            >
              {filteredSymbols.map((item, idx) => {
                const isSelected = item.symbol === value;
                const isHighlighted = idx === highlightedIndex;
                return (
                  <li
                    key={`${item.symbol}-${item.id}-${idx}`}
                    onMouseDown={(e) => {
                      e.preventDefault(); // 防止失焦
                      handleSelect(item);
                    }}
                    onMouseEnter={() => setHighlightedIndex(idx)}
                    style={{
                      padding: '0.6rem 0.9rem',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      cursor: 'pointer',
                      background: isHighlighted
                        ? 'var(--accent-hover-bg, rgba(0, 229, 255, 0.12))'
                        : isSelected
                        ? 'rgba(0, 229, 255, 0.06)'
                        : 'transparent',
                      transition: 'background 0.15s ease',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <span
                        style={{
                          fontWeight: isSelected ? 600 : 500,
                          color: isSelected
                            ? 'var(--accent-cyan, #00e5ff)'
                            : 'var(--text-primary, #fff)',
                          fontSize: '0.88rem',
                        }}
                      >
                        {item.symbol}
                      </span>
                      {item.id && item.id !== item.symbol && (
                        <span
                          style={{
                            fontSize: '0.72rem',
                            padding: '0.1rem 0.35rem',
                            borderRadius: '3px',
                            background: 'rgba(255, 255, 255, 0.08)',
                            color: 'var(--text-muted, #aaa)',
                          }}
                        >
                          {item.id}
                        </span>
                      )}
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                      <span
                        style={{
                          fontSize: '0.68rem',
                          textTransform: 'uppercase',
                          padding: '0.1rem 0.3rem',
                          borderRadius: '3px',
                          background:
                            item.market_type === 'swap' || item.market_type === 'future'
                              ? 'rgba(0, 229, 255, 0.15)'
                              : 'rgba(255, 255, 255, 0.08)',
                          color:
                            item.market_type === 'swap' || item.market_type === 'future'
                              ? 'var(--accent-cyan, #00e5ff)'
                              : 'var(--text-muted, #aaa)',
                        }}
                      >
                        {item.market_type}
                      </span>
                      {isSelected && (
                        <Check size={14} color="var(--accent-cyan, #00e5ff)" />
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
};
