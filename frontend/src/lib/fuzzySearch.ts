import { pinyin } from "pinyin-pro";

export const SEARCH_HINT = "名称 / 代码 / 拼音 / 首字母";

const han = /[\u3400-\u9fff]/;
const latin = /^[a-z][a-z0-9]*(?:[\s'’_-]+[a-z0-9]+)*$/;
const phoneticCache = new Map<string, readonly [string, string]>();

function normalized(value: string | number) {
  return String(value).normalize("NFKC").toLowerCase().trim();
}

function phoneticText(value: string) {
  return value.replace(/u:|ü/g, "v").replace(/[\s'’_-]+/g, "");
}

function phoneticKeys(value: string) {
  const cached = phoneticCache.get(value);
  if (cached) return cached;
  const syllables = pinyin(value, {
    toneType: "none",
    type: "array",
    nonZh: "consecutive",
    v: true,
  });
  const keys = [
    phoneticText(syllables.join("")),
    phoneticText(syllables.map((syllable) => syllable[0] ?? "").join("")),
  ] as const;
  if (phoneticCache.size >= 12000) phoneticCache.clear();
  phoneticCache.set(value, keys);
  return keys;
}

/** Match all terms across the displayed labels and codes, without changing row identity. */
export function makeFuzzyMatcher(query: string) {
  const phrase = normalized(query);
  if (!phrase) return (..._fields: unknown[]) => true;
  const terms = [...new Set([phrase, ...phrase.split(/\s+/)])].map((text) => ({
    text,
    phonetic: latin.test(text) ? phoneticText(text) : "",
  }));
  const [whole, ...parts] = terms;
  return (...fields: unknown[]) => {
    const labels = fields
      .flat(Infinity)
      .filter(
        (field): field is string | number =>
          typeof field === "string" || typeof field === "number",
      )
      .map(normalized);
    const matches = ({ text, phonetic }: (typeof terms)[number]) =>
      labels.some((label) => {
        if (label.includes(text)) return true;
        if (!phonetic) return false;
        return han.test(label)
          ? phoneticKeys(label).some((key) => key.includes(phonetic))
          : phoneticText(label).includes(phonetic);
      });
    return matches(whole) || (parts.length > 0 && parts.every(matches));
  };
}
