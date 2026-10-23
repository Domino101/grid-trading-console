import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import ts from 'typescript';
import { EN_MESSAGES } from '../src/i18n/messages.en.ts';
import { getLang, setLang, t } from '../src/i18n/index.ts';
import { localizeEvaluation } from '../src/components/coinfull/format.ts';

const SRC = fileURLToPath(new URL('../src', import.meta.url));
const HAN = /[\u3400-\u9fff\uf900-\ufaff]/;

const walk = (dir: string): string[] => {
  const files: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) files.push(...walk(full));
    else if (/\.tsx?$/.test(entry.name)) files.push(full);
  }
  return files;
};

const collectKeys = (): Set<string> => {
  const keys = new Set<string>();
  for (const file of walk(SRC)) {
    if (file.includes(join('src', 'i18n'))) continue;
    const source = readFileSync(file, 'utf8');
    const sf = ts.createSourceFile(
      file,
      source,
      ts.ScriptTarget.Latest,
      true,
      file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
    );
    const visit = (node: ts.Node): void => {
      if (ts.isCallExpression(node) && node.expression.getText(sf) === 't') {
        const arg = node.arguments[0];
        if (arg && (ts.isStringLiteral(arg) || ts.isNoSubstitutionTemplateLiteral(arg))) {
          keys.add(arg.text);
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(sf);
  }
  return keys;
};

test('every t() key has an English translation', () => {
  const keys = collectKeys();
  const missing = [...keys].filter((key) => !(key in EN_MESSAGES));
  assert.deepEqual(missing, [], 'missing English translations: ' + missing.slice(0, 20).join(' | '));
});

test('English copy contains no untranslated Han characters', () => {
  const untranslated = Object.entries(EN_MESSAGES).filter(([, value]) => HAN.test(value));
  assert.deepEqual(untranslated, [], 'untranslated entries: ' + untranslated.slice(0, 10).map(([k]) => k).join(' | '));
});

test('translations are non-empty and switch with the active language', () => {
  for (const [key, value] of Object.entries(EN_MESSAGES)) {
    assert.equal(typeof value, 'string', key);
    assert.ok(value.length > 0, 'empty translation: ' + key);
  }
  assert.equal(getLang(), 'zh');
  assert.equal(t('監控中'), '監控中');
  try {
    setLang('en');
    assert.equal(getLang(), 'en');
    assert.equal(t('監控中'), EN_MESSAGES['監控中']);
    assert.equal(t('不存在__fallback__'), '不存在__fallback__');
  } finally {
    setLang('zh');
  }
  assert.equal(getLang(), 'zh');
});

const collectUnwrapped = (): string[] => {
  const found: string[] = [];
  for (const file of walk(SRC)) {
    if (file.includes(join('src', 'i18n'))) continue;
    const source = readFileSync(file, 'utf8');
    const sf = ts.createSourceFile(
      file,
      source,
      ts.ScriptTarget.Latest,
      true,
      file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
    );
    const insideTranslate = (node: ts.Node): boolean => {
      let parent: ts.Node | undefined = node.parent;
      while (parent) {
        if (ts.isCallExpression(parent) && parent.expression.getText(sf) === 't') return true;
        parent = parent.parent;
      }
      return false;
    };
    const visit = (node: ts.Node): void => {
      const at = file + ':' + (sf.getLineAndCharacterOfPosition(node.getStart()).line + 1);
      if (ts.isJsxText(node) && HAN.test(node.text) && node.text.trim()) {
        found.push(at + ' jsx:' + node.text.trim().slice(0, 40));
      } else if (
        (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) &&
        HAN.test(node.text) &&
        !insideTranslate(node) &&
        node.text !== '基準' // backend-provided evaluation category, intentionally untranslated
      ) {
        found.push(at + ' literal:' + node.text.slice(0, 40));
      }
      ts.forEachChild(node, visit);
    };
    visit(sf);
  }
  return found;
};

test('no Chinese UI text bypasses t()', () => {
  assert.deepEqual(collectUnwrapped(), []);
});

test('backend evaluation labels are localized in English mode', () => {
  try {
    setLang('en');
    assert.equal(localizeEvaluation('強烈做多 (Top 5) 🚀'), 'Strong Long (Top 5) 🚀');
    assert.equal(localizeEvaluation('震盪中性 ⚖️'), 'Ranging Neutral ⚖️');
    assert.equal(localizeEvaluation('深度貼水(反轉看多) 💎'), 'Deep Discount(Bullish Reversal) 💎');
    assert.equal(localizeEvaluation('基準指數對沖做空 (BTCUSDT 50%) 🛡️'), 'Benchmark Hedge Short (BTCUSDT 50%) 🛡️');
  } finally {
    setLang('zh');
  }
  assert.equal(localizeEvaluation('強烈做多 (Top 5) 🚀'), '強烈做多 (Top 5) 🚀');
});
