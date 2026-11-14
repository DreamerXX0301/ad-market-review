"""Bounded identity matching; phonetic keys never change instrument identity."""
from functools import lru_cache
import re
import unicodedata

from pypinyin import Style, lazy_pinyin


def normalized(value):
    return unicodedata.normalize('NFKC', str(value)).strip().casefold()


def _latin(value):
    return re.sub(r"[\s'’\-]", '', normalized(value).replace('ü', 'v').replace('u:', 'v'))


@lru_cache(maxsize=20000)
def phonetic_keys(name):
    # Cache only immutable name transforms, not catalog rows, policies or results.
    # Renames and deleted identities are taken from the current catalog each call.
    if len(name) > 200:
        return '', ''
    return tuple(_latin(''.join(lazy_pinyin(name, style=style)))
                 for style in (Style.NORMAL, Style.FIRST_LETTER))


def phonetic_index(names):
    """Expose keys for the labels already present in a bounded dated view."""
    labels = {name.strip() for name in names if isinstance(name, str) and re.search(r'[\u3400-\u9fff]', name)}
    return {name: list(phonetic_keys(name)) for name in sorted(labels)}


def one_edit(left, right):
    """At most one substitution/insertion/deletion/adjacent transposition."""
    if left == right:
        return True
    if abs(len(left)-len(right)) > 1:
        return False
    if len(left) == len(right):
        different = [i for i, (a, b) in enumerate(zip(left, right)) if a != b]
        return len(different) == 1 or (len(different) == 2 and different[1] == different[0]+1
            and left[different[0]] == right[different[1]] and left[different[1]] == right[different[0]])
    if len(left) > len(right):
        left, right = right, left
    at = next((i for i, (a, b) in enumerate(zip(left, right)) if a != b), len(left))
    return left[at:] == right[at+1:]


def identity_match(query, name, *codes, allow_short_leading_typo=True):
    term = normalized(query)
    values = [normalized(value) for value in (name, *codes) if value is not None]
    for rank, matched, kind, label in (
        (0, term in values, 'exact', '准确匹配'),
        (1, any(value.startswith(term) for value in values), 'prefix', '名称/代码前缀'),
        (2, any(term in value for value in values), 'contains', '名称/代码包含'),
    ):
        if matched:
            return rank, {'match_type': kind, 'match_label': label, 'is_suggestion': False}
    # Numeric identities are never typo-corrected into a different instrument.
    if not term or len(term) > 80 or re.search(r'\d', term):
        return None
    latin = _latin(term)
    is_latin = re.fullmatch(r'[a-z]+', latin) is not None
    is_chinese = re.fullmatch(r'[\u3400-\u9fff]{3,40}', term) is not None
    if not is_latin and not is_chinese:
        return None
    full, initials = phonetic_keys(str(name))
    if is_latin and len(latin) >= 2:
        if latin == full or full.startswith(latin):
            return (3 if latin == full else 4), {'match_type': 'pinyin', 'match_label': '拼音匹配', 'is_suggestion': False}
        if latin == initials or initials.startswith(latin):
            return (3 if latin == initials else 4), {'match_type': 'initials', 'match_label': '首字母匹配', 'is_suggestion': False}
    if is_chinese and phonetic_keys(term)[0] == full:
        return 5, {'match_type': 'homophone', 'match_label': '可能想找 · 同音', 'is_suggestion': True}
    name_text = normalized(name)
    # A three-character display name sharing only its suffix can identify a
    # different stock or classification. Genuine homophones were handled above.
    ambiguous_suffix = (not allow_short_leading_typo and len(term) == len(name_text) == 3
                        and term[0] != name_text[0] and term[1:] == name_text[1:])
    if ((is_chinese and not ambiguous_suffix and one_edit(term, name_text))
            or (is_latin and len(latin) >= 5 and one_edit(latin, full))):
        return 6, {'match_type': 'typo', 'match_label': '可能想找 · 近似名称', 'is_suggestion': True}
    return None


def list_identity_match(query, name, codes, labels=()):
    """Find the best identity or classification match without reordering a list."""
    if not normalized(query):
        return None
    match = identity_match(query, name or '', *codes, allow_short_leading_typo=False)
    if match and match[0] == 0:
        return match[1]
    for label in labels:
        if isinstance(label, dict):
            candidate = identity_match(query, label.get('name') or '', label.get('topic_id'), label.get('topic_code'),
                                       allow_short_leading_typo=False)
        elif isinstance(label, str):
            candidate = identity_match(query, label, allow_short_leading_typo=False)
        else:
            continue
        if candidate and (match is None or candidate[0] < match[0]):
            match = candidate
            if match[0] == 0:
                break
    return match[1] if match else None
