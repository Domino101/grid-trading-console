import { EN_MESSAGES } from './messages.en.ts';

export type Lang = 'zh' | 'en';

const STORAGE_KEY = 'gtc.lang';
const listeners = new Set<() => void>();

const isLang = (value: unknown): value is Lang => value === 'zh' || value === 'en';

const detectLang = (): Lang => {
  if (typeof window === 'undefined') return 'zh';
  try {
    const requested = new URLSearchParams(window.location.search).get('lang');
    if (isLang(requested)) return requested;
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (isLang(stored)) return stored;
  } catch {
    // localStorage can be unavailable in private browsing / embedded webviews.
  }
  return 'zh';
};

let currentLang: Lang = detectLang();

const applyDocumentLang = (lang: Lang): void => {
  if (typeof document === 'undefined') return;
  document.documentElement.lang = lang === 'en' ? 'en' : 'zh-Hant';
};

applyDocumentLang(currentLang);

export const getLang = (): Lang => currentLang;

export const setLang = (lang: Lang): void => {
  if (lang === currentLang) return;
  currentLang = lang;
  try {
    if (typeof window !== 'undefined') window.localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    // Keep the in-memory preference when persistence is blocked.
  }
  applyDocumentLang(lang);
  listeners.forEach((listener) => listener());
};

export const subscribeLang = (listener: () => void): (() => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};

export const translate = (key: string, params?: Record<string, string | number>): string => {
  const template = currentLang === 'en' ? EN_MESSAGES[key] ?? key : key;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    params[name] === undefined ? match : String(params[name]),
  );
};

export const t = translate;
