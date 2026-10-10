import hashlib
import json
import os
import re
import copy
import time
import shutil
from collections import Counter
from difflib import SequenceMatcher

from calibre.utils.config import config_dir


WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]{2,}")
BRACKET_RE = re.compile(r"\[([^\[\]\n]{2,100})\]")
CAPITAL_PHRASE_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z'’-]{2,})(?:\s+(?:of|the|and|[A-Z][A-Za-z'’-]{2,})){0,3}\b"
)
MAPPING_RE = re.compile(r"^\s*([^\n=→>-]{2,100}?)\s*(?:->|=>|→|=)\s*(.{1,160}?)\s*$")

STOPWORDS = frozenset("""
a an and are as at be been being but by can could did do does for from had has
have he her hers him his how i if in into is it its may might more most much my
no not of on one or our ours she should so some than that the their theirs them
then there these they this those through to too up us very was we were what when
where which who why will with would you your yours
""".split())

COMMON_WORDS = STOPWORDS | frozenset("""
about after again against all almost also always another any around away back
because before behind below between both came come comes coming day days down
each end even ever every far few first found get gets getting give given go goes
going good got great hand hands here high himself herself itself just keep know
knew know later left less let like little long look looked made make many might
much must near never new next now old once only other over own people place put
right said same saw say says see seemed seen several side since small still take
taken than thing things think thought three time times took two under until upon
use used using want wanted way well went while without work world year years
actually although apparently already besides certainly clearly eventually
finally fortunately furthermore generally however indeed instead immediately
likely meanwhile moreover nevertheless nonetheless obviously often otherwise
particularly perhaps probably rather really simply similarly sometimes suddenly
therefore though unfortunately usually yet apart middle thank thanks i'm i’m
i'll i’ll it's it’s oceanofpdf
""".split())

TERM_HINTS = frozenset({
    'ability', 'armor', 'array', 'bloodline', 'class', 'clan', 'cosmic',
    'cultivation', 'dao', 'energy', 'faction', 'field', 'fractal', 'grade',
    'intent', 'item', 'kingdom', 'location', 'mystic', 'path', 'pill',
    'profession', 'quest', 'race', 'rank', 'realm', 'sect', 'skill', 'spatial',
    'system', 'teleportation', 'title', 'trait', 'treasure', 'void', 'weapon',
})

SYSTEM_VALUE_LABELS = frozenset({
    'additional reward', 'amount', 'bonus', 'chance', 'cost', 'damage',
    'duration', 'efficiency', 'gain', 'increase', 'level', 'limited title',
    'limited title slots', 'place', 'progress', 'rank', 'rate', 'reward',
    'titles', 'value',
})


def _clean_candidate(value):
    value = re.sub(r"\s+", " ", str(value or '')).strip(" \t\r\n.,;:!?\"'`(){}[]")
    value = re.sub(r"(?:['’]s)$", '', value, flags=re.I)
    value = re.sub(r'^(?:the|a|an)\s+', '', value, flags=re.I)
    return value[:160]


def _term_signature(value):
    value = _clean_candidate(value).casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _term_key(value):
    """Stable identity for the same spelling with harmless typography changes."""
    value = str(value or '').translate(str.maketrans({
        '\u2018': "'", '\u2019': "'", '\u02bc': "'", '\u0060': "'",
        '\u2010': '-', '\u2011': '-', '\u2012': '-', '\u2013': '-', '\u2014': '-',
    }))
    return re.sub(r'\s+', ' ', value).strip().casefold()


def _source_occurs(text, source):
    """Match a source term without treating ``He`` as part of ``The``."""
    source = str(source or '').strip()
    if not source:
        return False
    return bool(re.search(
        r'(?<![A-Za-z0-9])%s(?![A-Za-z0-9])' % re.escape(source),
        str(text or ''), flags=re.I,
    ))


def _glossary_source_occurs(text, source, proper_name=False):
    """Conservative matching for instructions that can change book text.

    A one-word lower-case entry is too ambiguous to be a safe global glossary
    instruction. Capitalized terms and names are case-sensitive, preventing a
    character such as ``Black`` from matching the ordinary adjective ``black``.
    """
    source = str(source or '').strip()
    words = WORD_RE.findall(source)
    if not source or (len(words) == 1 and source == source.lower()):
        return False
    flags = 0 if proper_name or any(char.isupper() for char in source) else re.I
    escaped = re.escape(source).replace(r'\ ', r'\s+')
    return bool(re.search(
        r'(?<![A-Za-z0-9])%s(?![A-Za-z0-9])' % escaped,
        str(text or ''), flags=flags,
    ))


def _looks_untranslated(source, target, forbid_latin_target=False):
    """Return True for unchanged/bracketed/nearly unchanged Latin targets."""
    source_signature = _term_signature(source)
    target_signature = _term_signature(target)
    if not source_signature or not target_signature:
        return False
    if source_signature == target_signature:
        return True
    if forbid_latin_target:
        target_text = str(target or '')
        if (
            re.search(r'[A-Za-z]{2,}', target_text) or
            not re.search(r'[\u0400-\u052f]', target_text)
        ):
            return True
    if (
        re.search(r'[\u0400-\u052f]', str(target or '')) and
        re.search(r'[A-Za-z]{2,}', str(target or ''))
    ):
        # Mixed forms such as "лорде Atwood" are neither a translation nor a
        # target-script transliteration and should never become canonical.
        return True
    if not re.fullmatch(r'[a-z0-9 ]+', source_signature):
        return False
    if not re.fullmatch(r'[a-z0-9 ]+', target_signature):
        return False
    return SequenceMatcher(
        None, source_signature, target_signature
    ).ratio() >= 0.90


def _candidate_allowed(value):
    if _looks_like_scanner_noise(value):
        return False
    if re.search(r"['’](?:d|ll|ve|re|m|t)\b", str(value or ''), flags=re.I):
        return False
    words = WORD_RE.findall(value)
    if not words or any(word.isdigit() for word in words):
        return False
    if len(words) == 1:
        word = words[0]
        return (
            len(word) >= 3 and word.casefold() not in COMMON_WORDS and
            word.casefold() not in TERM_HINTS and
            (len(word) >= 4 or word[:1].isupper())
        )
    content = [word for word in words if word.casefold() not in STOPWORDS]
    return bool(content) and any(
        word.casefold() not in COMMON_WORDS and len(word) >= 4
        for word in content
    )


def _looks_like_scanner_noise(value):
    """Reject UI values and sentence fragments that are not reusable terms."""
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    if not text:
        return True
    words = WORD_RE.findall(text)
    lowered = text.casefold()
    if re.search(r'\d', text) or '%' in text:
        return True
    if len(text) > 100 or len(words) > 8:
        return True
    if ':' in text:
        prefix = _clean_candidate(text.split(':', 1)[0]).casefold()
        if prefix in SYSTEM_VALUE_LABELS or len(words) > 5:
            return True
    return False


def _forced_bracket_term(value):
    """Allow only concise, term-shaped bracket content to bypass frequency."""
    value = _clean_candidate(value)
    if not value or _looks_like_scanner_noise(value):
        return False
    words = WORD_RE.findall(value)
    if not (2 <= len(words) <= 5):
        return False
    meaningful = [word for word in words if word.casefold() not in STOPWORDS]
    return bool(meaningful) and (
        any(word.casefold() in TERM_HINTS for word in meaningful) or
        all(word[:1].isupper() for word in meaningful)
    )


def scan_terms(texts, minimum_occurrences=5):
    """Return conservative terminology candidates without making API calls."""
    minimum = max(1, int(minimum_occurrences))
    display = {}
    counts = Counter()
    proper_signals = Counter()
    forced = set()

    def add(value, force=False, proper_signal=False):
        value = _clean_candidate(value)
        if not value or not _candidate_allowed(value):
            return
        key = value.casefold()
        display.setdefault(key, value)
        counts[key] += 1
        if proper_signal:
            proper_signals[key] += 1
        if force:
            forced.add(key)

    for raw_text in texts:
        text = str(raw_text or '')
        occupied = []
        for match in BRACKET_RE.finditer(text):
            bracket_value = match.group(1)
            # A full System notification is often "Title: requirement/value".
            # Keep a concise title-like prefix, never the numeric/imperative tail.
            if ':' in bracket_value:
                prefix = bracket_value.split(':', 1)[0]
                if _forced_bracket_term(prefix):
                    add(prefix, force=True)
            add(bracket_value, force=_forced_bracket_term(bracket_value))
            occupied.append(match.span())
        for match in CAPITAL_PHRASE_RE.finditer(text):
            start, end = match.span()
            if any(start < prior_end and end > prior_start for prior_start, prior_end in occupied):
                continue
            value = match.group(0)
            words = WORD_RE.findall(value)
            before = text[:start].rstrip()
            sentence_start = not before or before[-1] in '.!?\n"“”'
            add(
                value,
                proper_signal=(len(words) == 1 and not sentence_start),
            )
        # Lower-case multiword candidates are admitted only when every word is
        # uncommon. This catches recurring setting terms while rejecting the
        # vast majority of ordinary prose and connective phrases.
        for segment in re.split(r'[.!?;:\n]+', text):
            tokens = WORD_RE.findall(segment)
            for size in (2, 3, 4):
                for index in range(0, len(tokens) - size + 1):
                    phrase_tokens = tokens[index:index + size]
                    if any(token[:1].isupper() for token in phrase_tokens):
                        continue
                    if any(
                        len(token) < 4 or token.casefold() in COMMON_WORDS
                        for token in phrase_tokens
                    ):
                        continue
                    if not any(token.casefold() in TERM_HINTS for token in phrase_tokens):
                        continue
                    add(' '.join(phrase_tokens))

    results = []
    for key, count in counts.items():
        if (
            len(WORD_RE.findall(display[key])) == 1 and
            key not in forced and not proper_signals[key]
        ):
            # A recurring capitalized word seen only at sentence starts is far
            # more likely ordinary prose ("Thank", "Apart", "Middle") than a
            # name. Real recurring names normally appear mid-sentence at least
            # once; bracketed/System terms remain forced.
            continue
        if count >= minimum or key in forced:
            results.append({'source': display[key], 'count': count})
    results.sort(key=lambda item: (-item['count'], item['source'].casefold()))
    return results


class BookTerminology:
    VERSION = 6

    def __init__(
        self, original_path, translation_path='', forbid_latin_targets=False
    ):
        self.original_path = os.path.abspath(original_path)
        self.translation_path = os.path.abspath(translation_path) if translation_path else ''
        self.forbid_latin_targets = bool(forbid_latin_targets)
        identity = os.path.normcase(self.original_path)
        try:
            identity += '|%d' % os.path.getsize(self.original_path)
        except OSError:
            pass
        self.project_key = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]
        self.project_dir = os.path.join(
            config_dir, 'plugins', 'translation-proofreader-projects', self.project_key
        )
        self.path = os.path.join(self.project_dir, 'book_terms.json')
        self.data = self._defaults()
        self.load()
        # A reopened book may contain stale per-book copies produced by an old
        # plugin version. Refresh every already-attached profile immediately so
        # its established rows regain authoritative values and non-new flags
        # before any scan or ordinary save can occur.
        attached_profile = self.active_profile
        if attached_profile:
            try:
                attached_payload = MemoryProfileLibrary().load(
                    attached_profile
                )
            except ValueError:
                pass
            else:
                self.apply_profile(
                    attached_profile, attached_payload,
                    sync=self.sync_profile,
                )

    def _defaults(self):
        return {
            'version': self.VERSION,
            'project_key': self.project_key,
            'original_path': self.original_path,
            'translation_path': self.translation_path,
            'minimum_occurrences': 5,
            'confirmation_threshold': 3,
            'manual_prompt': '',
            'auto_prompt': '',
            'active_profile': '',
            'sync_profile': False,
            'deleted_terms': [],
            'terms': {},
        }

    def load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as stream:
                loaded = json.load(stream)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError, TypeError):
            pass
        self.data['version'] = self.VERSION
        self.data['original_path'] = self.original_path
        self.data['translation_path'] = self.translation_path
        self.data.setdefault('terms', {})
        self.data.setdefault('deleted_terms', [])
        self.data.setdefault('active_profile', '')
        self.data.setdefault('sync_profile', False)
        for term in self.data['terms'].values():
            if isinstance(term, dict):
                term.setdefault('gender', '')
                term.setdefault('ai_suggestion', '')
                term.setdefault('ai_gender', '')
                term.setdefault('ai_category', '')
                term.setdefault('ai_confidence', 0.0)
                term.setdefault('ai_notes', '')
                term.setdefault('ai_revision', 0.0)
                term.setdefault('proper_name', False)
                term.setdefault('blocked_alternatives', [])
                term.setdefault('new_in_book', False)
                term.setdefault('profile_term', False)
                term.setdefault('user_revision', 0.0)
                if 'confirmed_by_user' not in term:
                    explicitly_locked_name = bool(
                        term.get('proper_name', False) and
                        term.get('canonical')
                    )
                    term['confirmed_by_user'] = explicitly_locked_name
                    if (
                        str(term.get('status', '')).upper() == 'CONFIRMED' and
                        not explicitly_locked_name
                    ):
                        # Before 0.11 CONFIRMED meant an AI vote threshold,
                        # not explicit human approval. Require one new click.
                        term['status'] = 'AUTO'
                if (
                    term.get('proper_name', False) and term.get('canonical') and
                    str(term.get('status', '')).upper() in {'AUTO', 'CONFIRMED'}
                ):
                    term['status'] = 'CONFIRMED'
                    term['confirmed_by_user'] = True
                if (
                    term.get('ai_suggestion') and not term.get('canonical') and
                    str(term.get('status', '')).upper() not in {
                        'USER', 'CONFIRMED'
                    }
                ):
                    term['status'] = 'AUTO'
        # Never rewrite persisted terminology merely because a later plugin
        # version considers an old mapping suspicious. Invalid mappings remain
        # visible for manual repair but are filtered out when requests are built.

    def save(self):
        os.makedirs(self.project_dir, exist_ok=True)
        temporary = self.path + '.tmp'
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(self.data, stream, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(temporary, self.path)
        profile_name = self.active_profile
        if profile_name and self.sync_profile:
            MemoryProfileLibrary().merge_payload(
                profile_name, self.profile_payload()
            )

    def sync_active_profile_prompts(self):
        """Persist explicitly edited book prompts to the attached profile."""
        profile_name = self.active_profile
        if profile_name and self.sync_profile:
            MemoryProfileLibrary().merge_payload(
                profile_name, self.profile_payload(), update_prompts=True
            )

    @property
    def terms(self):
        return self.data['terms']

    def _stored_term_key(self, source):
        wanted = _term_key(source)
        if wanted in self.terms:
            return wanted
        return next((
            key for key, term in self.terms.items()
            if isinstance(term, dict) and
            _term_key(term.get('source') or key) == wanted
        ), '')

    @property
    def minimum_occurrences(self):
        return max(1, int(self.data.get('minimum_occurrences', 5)))

    @minimum_occurrences.setter
    def minimum_occurrences(self, value):
        self.data['minimum_occurrences'] = max(1, int(value))

    @property
    def confirmation_threshold(self):
        return max(1, int(self.data.get('confirmation_threshold', 3)))

    @confirmation_threshold.setter
    def confirmation_threshold(self, value):
        self.data['confirmation_threshold'] = max(1, int(value))

    @property
    def manual_prompt(self):
        return str(self.data.get('manual_prompt', ''))

    @manual_prompt.setter
    def manual_prompt(self, value):
        self.data['manual_prompt'] = str(value or '')

    @property
    def auto_prompt(self):
        return str(self.data.get('auto_prompt', ''))

    @auto_prompt.setter
    def auto_prompt(self, value):
        self.data['auto_prompt'] = str(value or '')

    @property
    def active_profile(self):
        return str(self.data.get('active_profile', '')).strip()

    @active_profile.setter
    def active_profile(self, value):
        self.data['active_profile'] = str(value or '').strip()

    @property
    def sync_profile(self):
        return bool(self.data.get('sync_profile', False))

    @sync_profile.setter
    def sync_profile(self, value):
        self.data['sync_profile'] = bool(value)

    def profile_payload(self):
        terms = copy.deepcopy(self.terms)
        for term in terms.values():
            if isinstance(term, dict):
                # Kept in private legacy memory for rollback compatibility,
                # but never propagate obsolete AI votes to a shared profile.
                term['alternatives'] = {}
                term['blocked_alternatives'] = []
                # These flags describe the current book's review state and
                # must never leak into a shared series profile.
                term.pop('new_in_book', None)
                term.pop('profile_term', None)
        return {
            'manual_prompt': self.manual_prompt,
            'auto_prompt': self.auto_prompt,
            'minimum_occurrences': self.minimum_occurrences,
            'confirmation_threshold': self.confirmation_threshold,
            'deleted_terms': list(self.data.get('deleted_terms', [])),
            'terms': terms,
        }

    def apply_profile(self, name, payload, sync=True):
        if not isinstance(payload, dict):
            raise ValueError('The selected memory profile is invalid.')
        self.manual_prompt = payload.get('manual_prompt', '')
        self.auto_prompt = payload.get('auto_prompt', '')
        self.minimum_occurrences = payload.get(
            'minimum_occurrences', self.minimum_occurrences
        )
        self.confirmation_threshold = payload.get(
            'confirmation_threshold', self.confirmation_threshold
        )
        imported_terms = payload.get('terms', {})
        if not isinstance(imported_terms, dict):
            imported_terms = {}
        deleted_values = []
        deleted_keys = set()
        for value in list(payload.get('deleted_terms', []) or []) + list(
            self.data.get('deleted_terms', []) or []
        ):
            identity = _term_key(value)
            if identity and identity not in deleted_keys:
                deleted_keys.add(identity)
                deleted_values.append(str(value))
        self.data['deleted_terms'] = deleted_values
        current_terms = {}
        for key, term in self.terms.items():
            if not isinstance(term, dict):
                continue
            normalized_key = _term_key(term.get('source') or key)
            if normalized_key in deleted_keys:
                continue
            current = current_terms.get(normalized_key)
            if current is None or (
                not current.get('canonical') and term.get('canonical')
            ):
                current_terms[normalized_key] = copy.deepcopy(term)
        rebuilt = {}
        for key, imported in imported_terms.items():
            if not isinstance(imported, dict):
                continue
            normalized_key = _term_key(imported.get('source') or key)
            if not normalized_key or normalized_key in deleted_keys:
                continue
            current = current_terms.pop(normalized_key, {})
            current_count = int(current.get('count', 0) or 0)
            # The shared profile is authoritative for every term it already
            # contains. A stale/reset private entry from a later volume must
            # never clear or replace the established canonical mapping.
            merged = copy.deepcopy(imported)
            merged['source'] = str(
                merged.get('source') or key
            )
            merged['count'] = current_count
            merged.setdefault('canonical', '')
            merged.setdefault('gender', '')
            merged.setdefault('ai_suggestion', '')
            merged.setdefault('ai_gender', '')
            merged.setdefault('ai_category', '')
            merged.setdefault('ai_confidence', 0.0)
            merged.setdefault('ai_notes', '')
            merged.setdefault('ai_revision', 0.0)
            merged.setdefault('proper_name', False)
            merged.setdefault('blocked_alternatives', [])
            merged['new_in_book'] = False
            merged['profile_term'] = True
            merged.setdefault('user_revision', 0.0)
            merged.setdefault('confirmed_by_user', False)
            if (
                str(merged.get('status', '')).upper() == 'CONFIRMED' and
                not merged.get('confirmed_by_user')
            ):
                merged['status'] = 'AUTO'
            merged.setdefault('status', 'UNRESOLVED')
            if (
                merged.get('ai_suggestion') and not merged.get('canonical') and
                str(merged.get('status', '')).upper() not in {
                    'USER', 'CONFIRMED'
                }
            ):
                merged['status'] = 'AUTO'
            merged.setdefault('alternatives', {})
            rebuilt[normalized_key] = merged
        # Terms absent from the profile are the only additions supplied by the
        # current book. Keep them, but never compare them against or mutate an
        # existing series entry.
        for key, current in current_terms.items():
            current['new_in_book'] = True
            current['profile_term'] = False
            rebuilt[key] = current
        self.data['terms'] = rebuilt
        self.active_profile = name
        self.sync_profile = sync
        self.apply_manual_prompt_mappings()
        self.save()

    def detach_profile(self):
        self.active_profile = ''
        self.sync_profile = False
        self.save()

    def clear_book_memory(self):
        # Detach first so clearing this book cannot erase the saved profile.
        self.data['active_profile'] = ''
        self.data['sync_profile'] = False
        self.data['manual_prompt'] = ''
        self.data['auto_prompt'] = ''
        self.data['deleted_terms'] = []
        self.data['terms'] = {}
        self.save()

    def scan(self, texts):
        candidates = scan_terms(texts, self.minimum_occurrences)
        # Remove old scanner-created UI values as soon as the user rescans.
        # Human USER/CONFIRMED decisions are never pruned automatically.
        for key in list(self.terms):
            term = self.terms.get(key, {})
            source = str(term.get('source') or key)
            status = str(term.get('status', '')).upper()
            human_locked = status == 'USER' or (
                status == 'CONFIRMED' and
                bool(term.get('confirmed_by_user', False))
            )
            if not human_locked and _looks_like_scanner_noise(source):
                self.terms.pop(key, None)
                deleted = self.data.setdefault('deleted_terms', [])
                if not any(
                    _term_key(value) == _term_key(source) for value in deleted
                ):
                    deleted.append(source)
        before_keys = {
            _term_key(term.get('source') or key)
            for key, term in self.terms.items() if isinstance(term, dict)
        }
        profile_before = set()
        if self.active_profile and self.sync_profile:
            try:
                profile_payload = MemoryProfileLibrary().load(
                    self.active_profile
                )
                profile_before = {
                    _term_key(term.get('source') or key)
                    for key, term in profile_payload.get('terms', {}).items()
                    if isinstance(term, dict)
                }
            except ValueError:
                profile_before = set()
        deleted = {_term_key(value) for value in self.data.get('deleted_terms', [])}
        seen = set()
        for candidate in candidates:
            source = candidate['source']
            key = _term_key(source)
            seen.add(key)
            if key in deleted:
                continue
            existing_key = next((
                stored_key for stored_key, stored in self.terms.items()
                if isinstance(stored, dict) and
                _term_key(stored.get('source') or stored_key) == key
            ), '')
            if not existing_key:
                self.terms[key] = {
                    'source': source,
                    'count': 0,
                    'canonical': '',
                    'ai_suggestion': '',
                    'ai_gender': '',
                    'ai_category': '',
                    'ai_confidence': 0.0,
                    'ai_notes': '',
                    'ai_revision': 0.0,
                    'status': 'UNRESOLVED',
                    'alternatives': {},
                    'new_in_book': True,
                    'profile_term': False,
                    'user_revision': 0.0,
                }
                existing_key = key
            term = self.terms[existing_key]
            term['source'] = term.get('source') or source
            term['count'] = int(candidate['count'])
            term.setdefault('canonical', '')
            term.setdefault('gender', '')
            term.setdefault('ai_suggestion', '')
            term.setdefault('ai_gender', '')
            term.setdefault('ai_category', '')
            term.setdefault('ai_confidence', 0.0)
            term.setdefault('ai_notes', '')
            term.setdefault('ai_revision', 0.0)
            term.setdefault('proper_name', False)
            term.setdefault('blocked_alternatives', [])
            term.setdefault('confirmed_by_user', False)
            term.setdefault('new_in_book', False)
            if term.get('profile_term', False):
                term['new_in_book'] = False
            term.setdefault('profile_term', False)
            term.setdefault('user_revision', 0.0)
            term.setdefault('status', 'UNRESOLVED')
            term.setdefault('alternatives', {})
        for key in list(self.terms):
            identity = _term_key(self.terms[key].get('source') or key)
            if identity not in seen and self.terms[key].get('status') != 'USER':
                term = self.terms[key]
                term['count'] = 0
                # A rescan is additive. It may update this book's occurrence
                # count, but it never silently removes an older candidate.
                # Deletion remains an explicit user action.
        self.save()
        after_keys = {
            _term_key(term.get('source') or key)
            for key, term in self.terms.items() if isinstance(term, dict)
        }
        self.last_scan_candidate_count = len(candidates)
        self.last_scan_added_count = len(after_keys - before_keys)
        added_keys = after_keys - before_keys
        self.last_scan_added_sources = [
            str(term.get('source', ''))
            for key, term in self.terms.items()
            if _term_key(term.get('source') or key) in added_keys
        ]
        self.last_scan_profile_added_count = 0
        if self.active_profile and self.sync_profile:
            try:
                profile_after_payload = MemoryProfileLibrary().load(
                    self.active_profile
                )
                profile_after = {
                    _term_key(term.get('source') or key)
                    for key, term in profile_after_payload.get(
                        'terms', {}
                    ).items() if isinstance(term, dict)
                }
                self.last_scan_profile_added_count = len(
                    profile_after - profile_before
                )
            except ValueError:
                pass
        return len(candidates)

    def apply_manual_prompt_mappings(self):
        for line in self.manual_prompt.splitlines():
            match = MAPPING_RE.match(line)
            if not match:
                continue
            self.set_user(match.group(1), match.group(2))

    def narrative_instructions(self):
        """Book notes without glossary mapping lines already sent separately."""
        return '\n'.join(
            line for line in self.manual_prompt.splitlines()
            if not MAPPING_RE.match(line)
        ).strip()

    def set_user(self, source, target):
        source = _clean_candidate(source)
        target = _clean_candidate(target)
        if not source or not target:
            return False
        if _looks_untranslated(
            source, target, self.forbid_latin_targets
        ):
            return False
        key = self._stored_term_key(source) or _term_key(source)
        term = self.terms.setdefault(key, {
            'source': source, 'count': 0, 'alternatives': {},
        })
        term['source'] = term.get('source') or source
        term['canonical'] = target
        term.setdefault('gender', '')
        term.setdefault('proper_name', False)
        term.setdefault('blocked_alternatives', [])
        term['confirmed_by_user'] = True
        term['user_revision'] = time.time()
        term['status'] = 'USER'
        term.setdefault('alternatives', {})
        self.data['deleted_terms'] = [
            value for value in self.data.get('deleted_terms', [])
            if _term_key(value) != _term_key(source)
        ]
        if self.active_profile and self.sync_profile:
            MemoryProfileLibrary().restore_terms(
                self.active_profile, [source]
            )
        return True

    def set_ai_suggestion(
        self, source, target, gender='', category='', confidence=0.0,
        notes='',
    ):
        """Store a lower-priority AI proposal without replacing human text."""
        source = _clean_candidate(source)
        target = _clean_candidate(target)
        if not source or not target or _looks_untranslated(
            source, target, self.forbid_latin_targets
        ):
            return False
        key = self._stored_term_key(source) or _term_key(source)
        term = self.terms.setdefault(key, {
            'source': source, 'count': 0, 'canonical': '',
            'status': 'UNRESOLVED', 'alternatives': {},
        })
        term['source'] = term.get('source') or source
        term['ai_suggestion'] = target
        term['ai_gender'] = re.sub(
            r'\s+', ' ', str(gender or '')
        ).strip()[:120]
        term['ai_category'] = re.sub(
            r'\s+', ' ', str(category or '')
        ).strip()[:80]
        try:
            term['ai_confidence'] = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            term['ai_confidence'] = 0.0
        term['ai_notes'] = re.sub(
            r'\s+', ' ', str(notes or '')
        ).strip()[:400]
        term['ai_revision'] = time.time()
        term.setdefault('canonical', '')
        term.setdefault('gender', '')
        term.setdefault('proper_name', False)
        term.setdefault('confirmed_by_user', False)
        term.setdefault('user_revision', 0.0)
        term.setdefault('alternatives', {})
        if not str(term.get('canonical', '')).strip() and str(
            term.get('status', '')
        ).upper() not in {'USER', 'CONFIRMED'}:
            term['status'] = 'AUTO'
        return True

    def set_gender(self, source, value):
        """Store a user-authored character gender or grammar instruction."""
        source = _clean_candidate(source)
        value = re.sub(r'\s+', ' ', str(value or '')).strip()[:240]
        if not source:
            return
        key = self._stored_term_key(source) or _term_key(source)
        term = self.terms.setdefault(key, {
            'source': source, 'count': 0, 'canonical': '',
            'status': 'UNRESOLVED', 'alternatives': {},
        })
        term['source'] = term.get('source') or source
        term['gender'] = value
        term.setdefault('canonical', '')
        term.setdefault('status', 'UNRESOLVED')
        term.setdefault('alternatives', {})
        term.setdefault('proper_name', False)
        term.setdefault('blocked_alternatives', [])
        term.setdefault('confirmed_by_user', False)
        term['user_revision'] = time.time()

    def set_proper_name(self, source, value):
        source = _clean_candidate(source)
        key = self._stored_term_key(source) if source else ''
        term = self.terms.get(key) if key else None
        if not term:
            return
        term['proper_name'] = bool(value)
        term.setdefault('blocked_alternatives', [])
        term['user_revision'] = time.time()

    def set_alternatives(self, source, targets):
        """Replace the editable alternative list while preserving evidence."""
        source = _clean_candidate(source)
        if not source:
            return []
        key = self._stored_term_key(source)
        term = self.terms.get(key)
        if not term:
            return []
        existing = term.get('alternatives', {}) or {}
        blocked = {
            str(value).casefold(): str(value)
            for value in term.get('blocked_alternatives', []) or []
        }
        updated = {}
        rejected = []
        seen = set()
        for raw_target in targets or []:
            target = _clean_candidate(raw_target)
            folded = target.casefold()
            if not target or folded in seen:
                continue
            seen.add(folded)
            if _looks_untranslated(
                source, target, self.forbid_latin_targets
            ):
                rejected.append(target)
                continue
            evidence = copy.deepcopy(existing.get(target, {
                'chunks': [], 'manual': True,
            }))
            evidence.setdefault('chunks', [])
            evidence['manual'] = True
            updated[target] = evidence
            blocked.pop(folded, None)
        updated_folded = {value.casefold() for value in updated}
        for removed in set(existing) - set(updated):
            if removed.casefold() not in updated_folded:
                blocked[removed.casefold()] = removed
        term['alternatives'] = updated
        term['blocked_alternatives'] = sorted(
            blocked.values(), key=str.casefold
        )
        return rejected

    def confirm(self, source):
        key = self._stored_term_key(source)
        term = self.terms.get(key) if key else None
        if term and not term.get('canonical') and term.get('ai_suggestion'):
            term['canonical'] = term.get('ai_suggestion', '')
            if not term.get('gender') and term.get('ai_gender'):
                term['gender'] = term.get('ai_gender', '')
        if term and term.get('canonical'):
            term['status'] = 'CONFIRMED'
            term['confirmed_by_user'] = True
            term['user_revision'] = time.time()
            self.save()

    def reset(self, source):
        key = self._stored_term_key(source)
        term = self.terms.get(key) if key else None
        if term:
            term['canonical'] = ''
            term['gender'] = ''
            term['proper_name'] = False
            term['confirmed_by_user'] = False
            term['status'] = (
                'AUTO' if term.get('ai_suggestion') else 'UNRESOLVED'
            )
            term['user_revision'] = time.time()
            term['alternatives'] = {}
            term['blocked_alternatives'] = []
            self.save()

    def delete(self, source):
        key = self._stored_term_key(source)
        term = self.terms.pop(key, None)
        if term:
            deleted = self.data.setdefault('deleted_terms', [])
            if not any(
                _term_key(value) == _term_key(term.get('source'))
                for value in deleted
            ):
                deleted.append(term.get('source'))
            self.save()

    def clear_auto_memory(self):
        revision = time.time()
        for term in self.terms.values():
            status = str(term.get('status', '')).upper()
            if status == 'USER' or (
                status == 'CONFIRMED' and
                bool(term.get('confirmed_by_user', False))
            ):
                continue
            term['canonical'] = ''
            term['ai_suggestion'] = ''
            term['ai_gender'] = ''
            term['ai_category'] = ''
            term['ai_confidence'] = 0.0
            term['ai_notes'] = ''
            term['ai_revision'] = revision
            term['status'] = 'UNRESOLVED'
            term['user_revision'] = revision
            term['alternatives'] = {
                target: evidence
                for target, evidence in (term.get('alternatives', {}) or {}).items()
                if isinstance(evidence, dict) and evidence.get('manual')
            }
        self.save()

    def record_decisions(self, decisions, chunk_id):
        # 0.11 deliberately disables AI voting. Only explicit USER edits or a
        # user-confirmed legacy mapping may enter the active glossary.
        return False

    def relevant_for_text(self, text):
        relevant = []
        for term in self.terms.values():
            source = str(term.get('source', ''))
            status = str(term.get('status', '')).upper()
            human_canonical = str(term.get('canonical', '')).strip()
            ai_canonical = str(term.get('ai_suggestion', '')).strip()
            canonical = human_canonical or ai_canonical
            gender = str(
                term.get('gender', '') or term.get('ai_gender', '')
            ).strip()
            proper_name = bool(term.get('proper_name', False))
            occurs_here = _glossary_source_occurs(
                text, source, proper_name=proper_name
            )
            if (
                source and canonical and (
                    bool(human_canonical) and (
                    status in {'USER', 'AUTO'} or (
                        status == 'CONFIRMED' and
                        bool(term.get('confirmed_by_user', False))
                    )
                    ) or (not human_canonical and bool(ai_canonical))
                ) and not _looks_untranslated(
                    source, canonical, self.forbid_latin_targets
                ) and
                occurs_here
            ):
                relevant.append({
                    'source': source,
                    'canonical': canonical,
                    'gender': gender,
                    'proper_name': proper_name,
                    'status': status if human_canonical else 'AUTO',
                    'confirmed_by_user': bool(
                        term.get('confirmed_by_user', False)
                    ),
                    'context_only': False,
                })
        relevant.sort(key=lambda value: (-len(value['source']), value['source'].casefold()))
        return relevant

    def unresolved_for_text(self, text, maximum_words=5):
        """Return inactive scanned terms that occur in the supplied source text."""
        candidates = []
        for term in self.terms.values():
            source = str(term.get('source', '')).strip()
            status = str(term.get('status', '')).upper()
            if (
                _source_occurs(text, source) and
                status == 'UNRESOLVED' and
                len(WORD_RE.findall(source)) <= max(1, int(maximum_words))
            ):
                candidates.append(term)
        candidates.sort(key=lambda value: (
            -len(str(value.get('source', ''))),
            str(value.get('source', '')).casefold(),
        ))
        return candidates

    def rows(self):
        rows = list(self.terms.values())
        rows.sort(key=lambda term: (
            not bool(term.get('new_in_book', False)),
            -int(term.get('count', 0)),
            term.get('source', '').casefold(),
        ))
        return rows

    def mark_reviewed(self, sources):
        changed = False
        for source in sources or ():
            key = self._stored_term_key(source)
            term = self.terms.get(key) if key else None
            if term and term.get('new_in_book', False):
                term['new_in_book'] = False
                changed = True
        if changed:
            self.save()
        return changed

    def memory_summary(self):
        lines = []
        for term in self.rows():
            human_canonical = str(term.get('canonical', '')).strip()
            ai_canonical = str(term.get('ai_suggestion', '')).strip()
            canonical = human_canonical or ai_canonical
            gender = str(
                term.get('gender', '') or term.get('ai_gender', '')
            ).strip()
            proper_name = bool(term.get('proper_name', False))
            status = str(term.get('status', '')).upper()
            active = bool(ai_canonical and not human_canonical) or status in {'USER', 'AUTO'} or (
                status == 'CONFIRMED' and
                bool(term.get('confirmed_by_user', False))
            )
            if canonical and active:
                target = canonical or 'unresolved'
                suffix = '; gender/grammar: %s' % gender if gender else ''
                if proper_name:
                    suffix += '; proper name spelling locked'
                lines.append('%s -> %s%s' % (
                    term.get('source', ''), target, suffix,
                ))
        return '\n'.join(lines)


class MemoryProfileLibrary:
    """Persistent, user-managed prompt and terminology profiles."""

    VERSION = 6

    def __init__(self):
        self.root = os.path.join(
            config_dir, 'plugins', 'translation-proofreader-memory-profiles'
        )

    @staticmethod
    def _normalized_name(name):
        name = re.sub(r'\s+', ' ', str(name or '')).strip()
        if not name:
            raise ValueError('Enter a profile name.')
        return name[:160]

    def _path(self, name):
        normalized = self._normalized_name(name)
        digest = hashlib.sha256(
            normalized.casefold().encode('utf-8')
        ).hexdigest()[:24]
        return os.path.join(self.root, digest + '.json')

    def names(self):
        result = []
        try:
            filenames = os.listdir(self.root)
        except OSError:
            return result
        for filename in filenames:
            if not filename.lower().endswith('.json'):
                continue
            try:
                with open(os.path.join(self.root, filename), 'r', encoding='utf-8') as stream:
                    data = json.load(stream)
                name = self._normalized_name(data.get('name', ''))
            except (OSError, ValueError, TypeError):
                continue
            result.append(name)
        return sorted(set(result), key=str.casefold)

    def load(self, name):
        path = self._path(name)
        try:
            with open(path, 'r', encoding='utf-8') as stream:
                data = json.load(stream)
        except (OSError, ValueError, TypeError) as err:
            raise ValueError('Could not load memory profile "%s": %s' % (name, err))
        if not isinstance(data, dict) or not isinstance(data.get('memory'), dict):
            raise ValueError('Memory profile "%s" has an invalid structure.' % name)
        memory = copy.deepcopy(data['memory'])
        if int(data.get('version', 0) or 0) < self.VERSION:
            backup = path + '.v%d-backup' % int(data.get('version', 0) or 0)
            if not os.path.exists(backup):
                try:
                    shutil.copy2(path, backup)
                except OSError:
                    pass
            memory = self._recover_from_linked_projects(name, memory)
            self.save_payload(name, memory)
        return memory

    def save_payload(self, name, payload):
        name = self._normalized_name(name)
        if not isinstance(payload, dict):
            raise ValueError('The memory profile payload is invalid.')
        os.makedirs(self.root, exist_ok=True)
        path = self._path(name)
        temporary = path + '.tmp'
        data = {
            'version': self.VERSION,
            'name': name,
            'memory': copy.deepcopy(payload),
        }
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(temporary, path)
        return path

    @staticmethod
    def _clean_profile_term(term):
        cleaned = copy.deepcopy(term if isinstance(term, dict) else {})
        cleaned.pop('new_in_book', None)
        cleaned.pop('profile_term', None)
        cleaned['alternatives'] = {}
        cleaned['blocked_alternatives'] = []
        cleaned.setdefault('user_revision', 0.0)
        if (
            cleaned.get('canonical') and cleaned.get('proper_name', False) and
            str(cleaned.get('status', '')).upper() in {'AUTO', 'CONFIRMED'}
        ):
            cleaned['status'] = 'CONFIRMED'
            cleaned['confirmed_by_user'] = True
        return cleaned

    @classmethod
    def _merge_payload_values(cls, existing, incoming, update_prompts=False):
        """Add missing terms and accept only newer explicit human revisions."""
        merged = copy.deepcopy(existing if isinstance(existing, dict) else {})
        incoming = incoming if isinstance(incoming, dict) else {}
        for field in ('manual_prompt', 'auto_prompt'):
            incoming_value = str(incoming.get(field, '') or '')
            if update_prompts:
                merged[field] = incoming_value
            elif not str(merged.get(field, '') or '') and incoming_value:
                merged[field] = incoming_value
        for field in ('minimum_occurrences', 'confirmation_threshold'):
            if field not in merged:
                merged[field] = incoming.get(field)

        deleted_values = []
        deleted_keys = set()
        for value in list(merged.get('deleted_terms', []) or []) + list(
            incoming.get('deleted_terms', []) or []
        ):
            identity = _term_key(value)
            if identity and identity not in deleted_keys:
                deleted_keys.add(identity)
                deleted_values.append(str(value))
        merged['deleted_terms'] = deleted_values

        existing_terms = merged.get('terms', {})
        if not isinstance(existing_terms, dict):
            existing_terms = {}
        normalized = {}
        for key, term in existing_terms.items():
            if not isinstance(term, dict):
                continue
            normalized_key = _term_key(term.get('source') or key)
            if normalized_key:
                # Existing profile records are immutable in automatic merge.
                # Do not even normalize their status, notes, or alternatives.
                cleaned = copy.deepcopy(term)
                if normalized_key not in normalized:
                    normalized[normalized_key] = cleaned
                elif (
                    not normalized[normalized_key].get('canonical') and
                    cleaned.get('canonical')
                ):
                    # Conservative cleanup of a pre-existing duplicate: retain
                    # the populated record, never a blank duplicate.
                    normalized[normalized_key] = cleaned
        incoming_terms = incoming.get('terms', {})
        if isinstance(incoming_terms, dict):
            for key, term in incoming_terms.items():
                if not isinstance(term, dict):
                    continue
                normalized_key = _term_key(term.get('source') or key)
                if not normalized_key:
                    continue
                if normalized_key not in normalized:
                    normalized[normalized_key] = cls._clean_profile_term(term)
                else:
                    # Automatic scans/counts never replace a series entry. A
                    # human edit made after the profile copy was loaded carries
                    # a newer user_revision timestamp and is the sole permitted
                    # update path from the book window.
                    current_revision = float(
                        normalized[normalized_key].get('user_revision', 0.0) or 0.0
                    )
                    incoming_revision = float(
                        term.get('user_revision', 0.0) or 0.0
                    )
                    if incoming_revision > current_revision:
                        normalized[normalized_key] = cls._clean_profile_term(term)
                    else:
                        # AI proposals are a separate, lower-priority layer.
                        # A newer proposal may refresh only AI-owned fields;
                        # it can never replace a USER/CONFIRMED canonical.
                        current_ai_revision = float(
                            normalized[normalized_key].get(
                                'ai_revision', 0.0
                            ) or 0.0
                        )
                        incoming_ai_revision = float(
                            term.get('ai_revision', 0.0) or 0.0
                        )
                        if incoming_ai_revision > current_ai_revision:
                            for field in (
                                'ai_suggestion', 'ai_gender', 'ai_category',
                                'ai_confidence', 'ai_notes', 'ai_revision',
                            ):
                                normalized[normalized_key][field] = copy.deepcopy(
                                    term.get(field)
                                )
                            current = normalized[normalized_key]
                            if (
                                current.get('ai_suggestion') and
                                not current.get('canonical') and
                                str(current.get('status', '')).upper() not in {
                                    'USER', 'CONFIRMED'
                                }
                            ):
                                current['status'] = 'AUTO'
        merged['terms'] = {
            key: term for key, term in normalized.items()
            if key not in deleted_keys
        }
        return merged

    def _recover_from_linked_projects(self, name, memory):
        """Rebuild a pre-v5 profile from every book that used it."""
        project_root = os.path.join(
            config_dir, 'plugins', 'translation-proofreader-projects'
        )
        candidates = []
        try:
            directories = os.listdir(project_root)
        except OSError:
            directories = []
        for directory in directories:
            directory_path = os.path.join(project_root, directory)
            for filename in (
                'book_terms.json.pre-v5-backup', 'book_terms.json'
            ):
                path = os.path.join(directory_path, filename)
                try:
                    with open(path, 'r', encoding='utf-8') as stream:
                        project = json.load(stream)
                    if (
                        not isinstance(project, dict) or
                        str(project.get('active_profile', '')).casefold() !=
                        str(name).casefold()
                    ):
                        continue
                    candidates.append((os.path.getmtime(path), project))
                except (OSError, ValueError, TypeError):
                    continue
        recovered = copy.deepcopy(memory)
        # The profile itself is authoritative. Linked books may recover only
        # keys that the old profile did not contain; none may replace it.
        for _modified, project in sorted(
            candidates, key=lambda item: item[0]
        ):
            recovered = self._merge_payload_values(recovered, project)
        return recovered

    def merge_payload(self, name, payload, update_prompts=False):
        """Accumulate book memory without replacing older series entries."""
        name = self._normalized_name(name)
        if not isinstance(payload, dict):
            raise ValueError('The memory profile payload is invalid.')
        try:
            existing = self.load(name)
        except ValueError:
            return self.save_payload(name, payload)

        merged = self._merge_payload_values(
            existing, payload, update_prompts=update_prompts
        )
        return self.save_payload(name, merged)

    def merge_book(self, name, terminology, update_prompts=False):
        return self.merge_payload(
            name, terminology.profile_payload(),
            update_prompts=update_prompts,
        )

    def delete_terms(self, name, sources):
        """Delete keys and keep profile tombstones against stale book copies."""
        payload = self.load(name)
        terms = payload.get('terms', {})
        wanted = {_term_key(source) for source in sources or ()}
        if not wanted:
            return 0
        if not isinstance(terms, dict):
            terms = {}
        kept = {}
        removed = 0
        for key, term in terms.items():
            identity = _term_key(
                term.get('source') or key
            ) if isinstance(term, dict) else _term_key(key)
            if identity in wanted:
                removed += 1
            else:
                kept[key] = term
        deleted = list(payload.get('deleted_terms', []) or [])
        deleted_keys = {_term_key(value) for value in deleted}
        tombstones_added = 0
        for source in sources or ():
            identity = _term_key(source)
            if identity and identity not in deleted_keys:
                deleted.append(str(source))
                deleted_keys.add(identity)
                tombstones_added += 1
        if removed or tombstones_added:
            payload['terms'] = kept
            payload['deleted_terms'] = deleted
            self.save_payload(name, payload)
        return removed

    def restore_terms(self, name, sources):
        """Clear tombstones when the user explicitly recreates a mapping."""
        try:
            payload = self.load(name)
        except ValueError:
            return 0
        wanted = {_term_key(source) for source in sources or ()}
        old = list(payload.get('deleted_terms', []) or [])
        kept = [value for value in old if _term_key(value) not in wanted]
        if len(kept) == len(old):
            return 0
        payload['deleted_terms'] = kept
        self.save_payload(name, payload)
        return len(old) - len(kept)

    def save_book(self, name, terminology):
        return self.save_payload(name, terminology.profile_payload())

    def delete(self, name):
        path = self._path(name)
        try:
            os.remove(path)
        except FileNotFoundError:
            return False
        return True


class ProfileTerminology(BookTerminology):
    """Editable BookTerminology-compatible view of a shared profile."""

    def __init__(self, library, name, forbid_latin_targets=True):
        self.library = library
        self.profile_name = library._normalized_name(name)
        self.original_path = ''
        self.translation_path = ''
        self.forbid_latin_targets = bool(forbid_latin_targets)
        self.project_key = 'shared-profile'
        self.project_dir = ''
        self.path = ''
        self.data = self._defaults()
        payload = library.load(self.profile_name)
        for field in (
            'manual_prompt', 'auto_prompt', 'minimum_occurrences',
            'confirmation_threshold', 'deleted_terms', 'terms',
        ):
            if field in payload:
                self.data[field] = copy.deepcopy(payload[field])
        self.data['active_profile'] = self.profile_name
        self.data['sync_profile'] = False
        self.data.setdefault('terms', {})
        for term in self.data['terms'].values():
            if not isinstance(term, dict):
                continue
            term.setdefault('canonical', '')
            term.setdefault('ai_suggestion', '')
            term.setdefault('ai_gender', '')
            term.setdefault('ai_category', '')
            term.setdefault('ai_confidence', 0.0)
            term.setdefault('ai_notes', '')
            term.setdefault('ai_revision', 0.0)
            term.setdefault('status', 'UNRESOLVED')
            term.setdefault('alternatives', {})
            term.setdefault('gender', '')
            term.setdefault('proper_name', False)
            term.setdefault('blocked_alternatives', [])
            term.setdefault('new_in_book', False)
            term.setdefault('profile_term', True)
            term.setdefault('user_revision', 0.0)
            term.setdefault('confirmed_by_user', False)
            if (
                term.get('ai_suggestion') and not term.get('canonical') and
                str(term.get('status', '')).upper() not in {
                    'USER', 'CONFIRMED'
                }
            ):
                term['status'] = 'AUTO'
        # A shared profile is edited only by the user. Loading it must never
        # silently clear a legacy canonical value.

    def save(self):
        self.library.save_payload(
            self.profile_name, self.profile_payload()
        )
