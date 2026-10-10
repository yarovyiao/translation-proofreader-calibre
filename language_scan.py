import re
from functools import lru_cache


LATIN_WORD_RE = re.compile(r"(?<![A-Za-z])[A-Za-z][A-Za-z'-]{1,}(?![A-Za-z])")
CYRILLIC_WORD_RE = re.compile(r"(?<![\w])['’\-А-Яа-яІіЇїЄєҐґЁёЫыЭэЪъ]{3,}(?![\w])")
URL_RE = re.compile(r'(?i)\b(?:https?://|www\.)\S+|\b\S+@\S+\.\S+\b')
RUSSIAN_ONLY_RE = re.compile(r'[ЁёЫыЭэЪъ]')
COMMON_RUSSIAN = frozenset('''
а ведь во вообще вот всё все всегда где да даже для до его ее её если еще ещё
же за здесь и из или как когда который которая которые ли либо мне может мы на
над не него неё но ну о об однако он она они от очень по потому после при про
раз с сейчас так такой там тебе тем то тогда только тоже тут ты уже что чтобы
эта это этот я
'''.split())
LATIN_ALLOW = frozenset({
    'i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x',
})


@lru_cache(maxsize=1)
def _ukrainian_dictionary():
    """Use Calibre's optional uk-UA Hunspell dictionary when installed."""
    try:
        from calibre.spell.dictionary import (
            get_dictionary, load_dictionary, parse_lang_code,
        )
        definition = get_dictionary(parse_lang_code('uk-UA'))
        return load_dictionary(definition).obj if definition else None
    except Exception:
        return None


def scan_language_hints(text, known_targets=(), limit=18):
    """Return high-value local hints; never edits text or calls an API."""
    clean = URL_RE.sub(' ', str(text or ''))
    known_words = {
        word.casefold()
        for value in known_targets or ()
        for word in CYRILLIC_WORD_RE.findall(str(value or ''))
    }
    found = []
    seen = set()

    def add(token, reason):
        key = (token.casefold(), reason)
        if key not in seen and len(found) < max(1, int(limit)):
            seen.add(key)
            found.append({'text': token, 'reason': reason})

    for token in LATIN_WORD_RE.findall(clean):
        folded = token.casefold()
        if folded in LATIN_ALLOW or (token.isupper() and len(token) <= 5):
            continue
        add(token, 'Latin text in the translation')

    dictionary = _ukrainian_dictionary()
    for token in CYRILLIC_WORD_RE.findall(clean):
        stripped = token.strip("'’-")
        folded = stripped.casefold()
        if not folded or folded in known_words:
            continue
        if RUSSIAN_ONLY_RE.search(stripped) or folded in COMMON_RUSSIAN:
            add(stripped, 'likely Russian in Ukrainian text')
            continue
        if (
            dictionary is not None and len(stripped) >= 5 and
            not stripped[:1].isupper() and
            not dictionary.recognized(stripped)
        ):
            add(stripped, 'not recognized by the installed Ukrainian dictionary')
    return found


def ukrainian_dictionary_available():
    return _ukrainian_dictionary() is not None
