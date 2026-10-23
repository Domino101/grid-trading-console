import { useSyncExternalStore } from 'react';
import { getLang, setLang, subscribeLang, translate, type Lang } from './index.ts';

interface I18nState {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: typeof translate;
}

export const useI18n = (): I18nState => {
  const lang = useSyncExternalStore(subscribeLang, getLang, getLang);
  // A full reload re-evaluates module-level label maps and memoised copy, so
  // every surface (including strings already held in component state) flips
  // to the selected language in one step.
  const changeLang = (next: Lang): void => {
    setLang(next);
    if (typeof window === 'undefined') return;
    // A stale ?lang= override would win over the stored choice on reload.
    const url = new URL(window.location.href);
    if (url.searchParams.has('lang')) {
      url.searchParams.delete('lang');
      window.history.replaceState(null, '', url);
    }
    window.location.reload();
  };
  return { lang, setLang: changeLang, t: translate };
};
