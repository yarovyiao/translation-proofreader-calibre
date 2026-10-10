import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

from .language_scan import scan_language_hints


RETRYABLE_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
CONTENT_BLOCK_MARKERS = (
    'prohibited content', 'content_filter', 'content filter', 'safety',
    'blocked_reason', 'blocked reason', 'prompt was blocked',
)
SERVICE_NOTE_RE = re.compile(
    r'\s*\(\s*(?:note|comment|'
    r'\u043f\u0440\u0438\u043c\u0456\u0442\u043a\u0430)\s*:\s*'
    r'(?=[^)]*(?:p\d{3,}|input|source|'
    r'\u0432\u0445\u0456\u0434|\u043e\u0440\u0438\u0433\u0456\u043d))'
    r'(?=[^)]*(?:mismatch|does\s+not\s+match|'
    r'\u043d\u0435\s+\u0437\u0431\u0456\u0433|'
    r'\u043d\u0435\s+\u0432\u0456\u0434\u043f\u043e\u0432\u0456\u0434\u0430))'
    r'[^)]*\)\s*\.?',
    flags=re.I,
)
SQUARE_MODEL_ASIDE_RE = re.compile(
    r'\s*\[(?=[^\]\n]{0,500}\b(?:wait|let\s+me|i\s+(?:need|should)|'
    r'correction|fix(?:ed)?\s+to|keep\s+(?:the\s+)?term|should\s+be)\b)'
    r'[^\]\n]{1,500}\]\s*',
    flags=re.I,
)
ANGLE_MODEL_ASIDE_RE = re.compile(
    r'\s*<(?=[^>\n]{0,500}\b(?:where|wait|let\s+me|i\s+(?:need|should)|'
    r'translat(?:e|ion))\b)[^>\n]{1,500}>'
    r'(?:\s*[—-]\s*(?:(?:перекладаємо|перекласти|слід|треба|тобто)\b)'
    r'[^\n.!?]{0,240}[.!?]?)?\s*',
    flags=re.I,
)
LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")
LATIN_RUN_RE = re.compile(
    r"[A-Za-z][A-Za-z'-]*(?:\s+[A-Za-z][A-Za-z'-]*)+"
)
CYRILLIC_TARGET_MARKERS = (
    'ukrain', 'укра', 'russian', 'рос', 'belarus', 'білорус', 'белорус',
    'bulgar', 'болгар', 'macedon', 'македон', 'serbian', 'серб',
    'kazakh', 'казах', 'kyrgyz', 'киргиз',
)
REVIEW_MODES = {'terminology', 'cleanup', 'full', 'polish'}
STRUCTURAL_WRAPPERS = '[](){}«»“”"'
ROUND_GROUP_RE = re.compile(r'\([^()\n]{1,500}\)')
SQUARE_GROUP_RE = re.compile(r'\[[^\[\]\n]{1,500}\]')
SENTENCE_END_RE = re.compile(
    r'[.!?\u2026]+(?=[\s\"\'\u00bb\u201d\u2019)\]]|$)'
)


def _latin_signature(value):
    return ' '.join(LATIN_TOKEN_RE.findall(str(value or '').casefold()))


def _looks_untranslated_term(source, target):
    source_signature = _latin_signature(source)
    target_signature = _latin_signature(target)
    if not source_signature or not target_signature:
        return False
    if source_signature == target_signature:
        return True
    target_text = str(target or '')
    if re.search(r'[\u0400-\u052f]', target_text):
        return False
    return SequenceMatcher(
        None, source_signature, target_signature
    ).ratio() >= 0.90


class APIError(Exception):
    pass


class ContentBlockedError(APIError):
    pass


class ResponseFormatError(APIError):
    """The provider answered, but its proofreading payload was malformed."""


def _json_values(text):
    text = (text or '').strip()
    text = re.sub(r'(?im)^\s*```(?:json)?\s*$', '', text).strip()
    if not text:
        raise ResponseFormatError(
            'The AI returned an empty response instead of proofreading JSON.'
        )
    try:
        return [json.loads(text)]
    except json.JSONDecodeError as original_error:
        # Some models occasionally return one valid response followed by a
        # duplicate JSON object or a short explanation. Decode every complete
        # top-level value independently so a valid first response is not lost.
        decoder = json.JSONDecoder()
        values = []
        position = 0
        while position < len(text):
            match = re.search(r'[\{\[]', text[position:])
            if not match:
                break
            start = position + match.start()
            try:
                value, end = decoder.raw_decode(text, start)
            except json.JSONDecodeError:
                position = start + 1
                continue
            values.append(value)
            position = end
        if values:
            return values
        raise ResponseFormatError(
            'Could not parse JSON in the AI response: %s' % original_error
        )


def _json_value(text):
    """Return the first complete JSON value (legacy parser helper)."""
    return _json_values(text)[0]


# Retained for compatibility with early plugin tests and integrations.
_json_object = _json_value


def _normalized_result(value):
    if not isinstance(value, dict) or 'revised' not in value:
        raise ResponseFormatError(
            'The JSON response is missing the required "revised" field.'
        )
    revised = str(value.get('revised', '')).strip()
    revised, removed_service_notes = SERVICE_NOTE_RE.subn('', revised)
    revised, removed_square_asides = SQUARE_MODEL_ASIDE_RE.subn('', revised)
    revised, removed_angle_asides = ANGLE_MODEL_ASIDE_RE.subn('', revised)
    revised = revised.strip()
    if not revised:
        raise ResponseFormatError('The AI returned an empty "revised" field.')
    severity = str(value.get('severity', 'minor')).strip().lower()
    if severity not in {'none', 'minor', 'major'}:
        severity = 'minor'
    alignment = str(value.get('alignment', 'uncertain')).strip().lower()
    if alignment not in {'ok', 'uncertain', 'mismatch'}:
        alignment = 'uncertain'
    notes = str(value.get('notes', '')).strip()
    if removed_service_notes:
        notes = '\n\n'.join(filter(None, (
            notes,
            'Plugin safeguard: removed a model-generated alignment note from '
            'the revised book text.',
        )))
    if removed_square_asides or removed_angle_asides:
        notes = '\n\n'.join(filter(None, (
            notes,
            'Plugin safeguard: removed model scratchpad/reasoning text from '
            'the revised book text.',
        )))
    return {
        'id': str(value.get('id', '')).strip(),
        'revised': revised,
        'notes': notes,
        'severity': severity,
        'alignment': alignment,
    }


def _results_from_value(value, expected_ids):
    if isinstance(value, dict) and isinstance(value.get('items'), list):
        raw_items = value['items']
    elif isinstance(value, list):
        raw_items = value
    elif len(expected_ids) == 1 and isinstance(value, dict):
        raw_items = [value]
    else:
        raise ResponseFormatError(
            'The JSON response is missing the required "items" array.'
        )

    results = [_normalized_result(item) for item in raw_items]
    if len(results) != len(expected_ids):
        raise ResponseFormatError(
            'The AI returned %d items for %d input pairs.' % (
                len(results), len(expected_ids)
            )
        )
    by_id = {result['id']: result for result in results if result['id']}
    if len(by_id) == len(expected_ids) and all(
        item_id in by_id for item_id in expected_ids
    ):
        ordered = [by_id[item_id] for item_id in expected_ids]
        ordered[0]['term_decisions'] = _term_decisions(value)
        return ordered
    if len(expected_ids) > 1 or any(result['id'] for result in results):
        raise ResponseFormatError(
            'The AI changed, duplicated, or omitted one or more pair ids.'
        )
    results[0]['term_decisions'] = _term_decisions(value)
    return results


def _json_results(text, expected_ids):
    values = _json_values(text)
    last_error = None

    # Prefer any single complete response. This safely handles a correct JSON
    # payload followed by repeated JSON or unrelated trailing text.
    for value in values:
        try:
            return _results_from_value(value, expected_ids)
        except ResponseFormatError as err:
            last_error = err

    # A response can also be split into several individually incomplete JSON
    # objects. Merge their item arrays, then apply the normal strict id checks.
    if len(values) > 1:
        combined_items = []
        combined_terms = []
        for value in values:
            if isinstance(value, dict) and isinstance(value.get('items'), list):
                combined_items.extend(value['items'])
                combined_terms.extend(value.get('term_decisions', []) or [])
            elif isinstance(value, list):
                combined_items.extend(value)
            elif isinstance(value, dict) and 'revised' in value:
                combined_items.append(value)
        if combined_items:
            try:
                return _results_from_value({
                    'items': combined_items,
                    'term_decisions': combined_terms,
                }, expected_ids)
            except ResponseFormatError as err:
                last_error = err

    raise last_error or ResponseFormatError(
        'The AI response did not contain a complete proofreading result.'
    )


def _partial_json_results(text, expected_ids, pairs):
    """Recover a mostly complete response by stable id without guessing order."""
    values = _json_values(text)
    expected = set(expected_ids)
    by_id = {}
    duplicate_ids = set()
    decisions = []
    for value in values:
        decisions.extend(_term_decisions(value))
        if isinstance(value, dict) and isinstance(value.get('items'), list):
            raw_items = value['items']
        elif isinstance(value, list):
            raw_items = value
        elif isinstance(value, dict) and 'revised' in value:
            raw_items = [value]
        else:
            continue
        for raw in raw_items:
            try:
                result = _normalized_result(raw)
            except ResponseFormatError:
                continue
            item_id = result['id']
            if item_id not in expected:
                continue
            if item_id in by_id:
                duplicate_ids.add(item_id)
                continue
            by_id[item_id] = result

    matched = len(by_id)
    required = max(1, (len(expected_ids) * 4 + 4) // 5)
    if matched < required:
        raise ResponseFormatError(
            'Only %d of %d input pair ids could be recovered safely.' % (
                matched, len(expected_ids)
            )
        )

    pair_by_id = {
        str(pair.get('id', '')): pair for pair in pairs
    }
    missing = [item_id for item_id in expected_ids if item_id not in by_id]
    ordered = []
    for item_id in expected_ids:
        result = by_id.get(item_id)
        if result is None:
            result = {
                'id': item_id,
                'revised': str(
                    pair_by_id.get(item_id, {}).get('translation', '')
                ).strip(),
                'notes': (
                    'Plugin recovery: the AI omitted or changed this pair id. '
                    'The current translation was retained as an editable '
                    'placeholder; no additional API request was sent.'
                ),
                'severity': 'none',
                'alignment': 'uncertain',
                '_plugin_missing': True,
            }
        ordered.append(result)

    summary = (
        'Plugin recovery: used %d of %d results that retained valid stable ids; '
        '%d missing result(s) kept the current translation. No retry was sent.'
        % (matched, len(expected_ids), len(missing))
    )
    if duplicate_ids:
        summary += ' Duplicate ids ignored: %s.' % ', '.join(
            sorted(duplicate_ids)
        )
    ordered[0]['notes'] = '\n\n'.join(filter(None, (
        ordered[0].get('notes', ''), summary,
    )))
    ordered[0]['term_decisions'] = decisions
    return ordered


def _term_decisions(value):
    decisions = []
    seen = set()
    if not isinstance(value, dict):
        return decisions
    for raw in value.get('term_decisions', []) or []:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get('source', '')).strip()
        target = str(raw.get('target', '')).strip()
        key = (source.casefold(), target.casefold())
        if source and target and key not in seen:
            seen.add(key)
            decisions.append({'source': source, 'target': target})
    return decisions


def _operation_results(text, expected_ids, pairs, cyrillic_target=False):
    """Apply exact AI-proposed substitutions without allowing prose rewrites."""
    values = _json_values(text)
    raw_items = []
    for value in values:
        if isinstance(value, dict) and isinstance(value.get('items'), list):
            raw_items.extend(value['items'])
        elif isinstance(value, list):
            raw_items.extend(value)
    by_id = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get('id', '')).strip()
        if item_id in expected_ids and item_id not in by_id:
            by_id[item_id] = item
    if not by_id:
        raise ResponseFormatError(
            'The AI response did not contain any valid edit-operation ids.'
        )

    pair_by_id = {str(pair.get('id', '')): pair for pair in pairs}
    results = []
    for item_id in expected_ids:
        pair = pair_by_id[item_id]
        translation = str(pair.get('translation', ''))
        revised = translation
        raw = by_id.get(item_id)
        notes = []
        valid_edits = 0
        rejected_edits = 0
        alignment = 'ok'
        if raw is None:
            notes.append(
                'Plugin recovery: the model omitted this id, so the current '
                'translation was retained without a retry.'
            )
        else:
            alignment = str(raw.get('alignment', 'ok')).strip().lower()
            if alignment not in {'ok', 'uncertain', 'mismatch'}:
                alignment = 'uncertain'
            model_note = str(raw.get('notes', '')).strip()
            if model_note:
                notes.append(model_note)
            edits = raw.get('edits', [])
            if not isinstance(edits, list):
                edits = []
                rejected_edits += 1
            if alignment != 'mismatch':
                for edit in edits:
                    if not isinstance(edit, dict):
                        rejected_edits += 1
                        continue
                    old = str(edit.get('old', ''))
                    new = str(edit.get('new', ''))
                    if (
                        not old or not new or old == new or
                        '\n' in old or '\n' in new or
                        len(old) > 300 or len(new) > 300 or
                        old not in revised
                    ):
                        rejected_edits += 1
                        continue
                    old_wrappers = ''.join(
                        char for char in old if char in STRUCTURAL_WRAPPERS
                    )
                    new_wrappers = ''.join(
                        char for char in new if char in STRUCTURAL_WRAPPERS
                    )
                    if old_wrappers != new_wrappers:
                        rejected_edits += 1
                        continue
                    if cyrillic_target and re.search(r'[A-Za-z]{2,}', new):
                        rejected_edits += 1
                        continue
                    revised = revised.replace(old, new, 1)
                    valid_edits += 1
        if valid_edits:
            notes.append(
                'Plugin applied %d exact localized replacement%s; all other '
                'text remained byte-for-byte unchanged.' % (
                    valid_edits, '' if valid_edits == 1 else 's'
                )
            )
        if rejected_edits:
            notes.append(
                'Plugin rejected %d unsafe or non-matching replacement%s.' % (
                    rejected_edits, '' if rejected_edits == 1 else 's'
                )
            )
        results.append({
            'id': item_id,
            'revised': revised,
            'notes': '\n\n'.join(notes),
            'severity': (
                'none' if not valid_edits else
                'minor' if valid_edits <= 2 else 'major'
            ),
            'alignment': alignment,
        })
    return results


def _content_blocked(value):
    lowered = str(value or '').lower()
    return any(marker in lowered for marker in CONTENT_BLOCK_MARKERS)


class ProofreaderClient:
    def __init__(self, settings, status_callback=None):
        self.settings = dict(settings)
        self.status_callback = status_callback or (lambda _message: None)

    @property
    def provider(self):
        return self.settings.get('provider', 'gemini')

    def validate(self, require_model=True):
        missing = []
        if not str(self.settings.get('endpoint', '')).strip():
            missing.append('Endpoint')
        if require_model and not str(self.settings.get('model', '')).strip():
            missing.append('Model')
        if self.provider != 'local' and not str(
            self.settings.get('api_key', '')
        ).strip():
            missing.append('API key')
        if not self.settings.get('auto_detect_source_language', False) and not str(
            self.settings.get('source_language', '')
        ).strip():
            missing.append('Original language')
        if not self.settings.get('auto_detect_target_language', False) and not str(
            self.settings.get('target_language', '')
        ).strip():
            missing.append('Translation language')
        if missing:
            raise APIError('Complete these settings: %s.' % ', '.join(missing))

    def proofread(self, original, translation):
        return self.proofread_many([{
            'id': 'p1',
            'original': original,
            'translation': translation,
        }])[0]

    def proofread_many(self, pairs, terminology=None):
        self.validate()
        pairs = [dict(pair) for pair in pairs]
        if not pairs:
            raise APIError('The proofreading request contains no paragraph pairs.')
        chunk_chars = max(1000, int(self.settings.get('chunk_chars', 8000)))
        source_length = sum(len(str(pair.get('original', ''))) for pair in pairs)
        target_length = sum(len(str(pair.get('translation', ''))) for pair in pairs)
        largest_source = max(len(str(pair.get('original', ''))) for pair in pairs)
        largest_target = max(len(str(pair.get('translation', ''))) for pair in pairs)
        if len(pairs) > 1 and (
            source_length > chunk_chars or target_length > chunk_chars
        ):
            raise APIError(
                'The chunk contains %d original and %d translation characters, '
                'while the per-book target is %d. Rebuild the chunks; the text '
                'was not truncated.' % (
                    source_length, target_length, chunk_chars
                )
            )

        mode = str(self.settings.get('review_mode', 'full')).strip().lower()
        if mode not in REVIEW_MODES:
            mode = 'full'
        if mode == 'polish':
            # Polish is deliberately isolated from every accumulated rule and
            # memory source. The bilingual pair is its entire book context.
            terminology = []
        prompt_template = self.settings.get('prompt') or ''
        try:
            source_language = self._language_instruction('source')
            target_language = self._language_instruction('target')
            if mode == 'full':
                system_prompt = prompt_template.format(
                    source_language=source_language,
                    target_language=target_language,
                    book_instructions=self._optional_prompt(
                        'BOOK INSTRUCTIONS', self.settings.get('book_prompt', '')
                    ),
                    auto_memory_instructions=self._optional_prompt(
                        'ADDITIONAL BOOK NOTES', self.settings.get('auto_prompt', '')
                    ),
                    terminology_instructions=self._terminology_prompt(terminology),
                )
            elif mode == 'polish':
                system_prompt = self._polish_prompt(
                    source_language, target_language
                )
            else:
                system_prompt = self._operation_prompt(
                    mode, source_language, target_language, terminology
                )
        except (KeyError, ValueError) as err:
            raise APIError('Prompt template error: %s' % err)

        request_pairs = [dict(pair) for pair in pairs]
        has_pair_glossary = False
        for pair in request_pairs:
            required = self._required_glossary(
                pair.get('original', ''), terminology
            )
            if required:
                # Keep the relevant mapping next to the paragraph as well as
                # in the system prompt. Small/Flash models are much less likely
                # to overlook a term buried in a chunk-wide instruction this
                # way, especially when a yellow row is reviewed again later.
                pair['required_glossary'] = required
                has_pair_glossary = True
        if has_pair_glossary:
            system_prompt = (
                system_prompt.rstrip() +
                '\n\nPAIR-LOCAL REQUIRED GLOSSARY:\n'
                'An input pair may contain required_glossary entries with '
                'source and canonical fields. They are active AUTO, USER, or '
                'user-confirmed terminology references. A canonical value is a '
                'dictionary/base form, never a literal search-and-replace value. '
                'Use it only when the matched source span denotes the same name, '
                'term, or sense in this context. Inflect the target naturally '
                'for its sentence, including proper names. '
                'Never preserve the source-language term or invent a competing '
                'translation.'
            )
        if mode in {'cleanup', 'full'} and self._target_is_ukrainian():
            known_targets = [
                term.get('canonical', '') for term in (terminology or [])
                if term.get('canonical')
            ]
            for pair in request_pairs:
                hints = scan_language_hints(
                    pair.get('translation', ''), known_targets
                )
                if hints:
                    pair['language_hints'] = hints
        payload_text = json.dumps(
            {'pairs': request_pairs},
            ensure_ascii=False,
        )
        expected_ids = [str(pair.get('id', '')) for pair in pairs]
        format_attempts = max(
            1, int(self.settings.get('response_attempts', 3))
        )
        for format_attempt in range(1, format_attempts + 1):
            response_text = self._request_text(system_prompt, payload_text)
            try:
                if mode in {'full', 'polish'}:
                    results = _json_results(response_text, expected_ids)
                else:
                    results = _operation_results(
                        response_text, expected_ids, pairs,
                        cyrillic_target=self._target_uses_cyrillic(),
                    )
            except ResponseFormatError as err:
                results = None
                if mode == 'full':
                    try:
                        results = _partial_json_results(
                            response_text, expected_ids, pairs
                        )
                    except ResponseFormatError:
                        results = None
                if results is not None:
                    self.status_callback(
                        'Recovered a mostly complete AI response by stable id; '
                        'no format retry was sent.'
                    )
                elif format_attempt >= format_attempts:
                    raise ResponseFormatError(
                        'The AI returned an invalid proofreading response after '
                        '%d attempts. Last error: %s' % (
                            format_attempts, err
                        )
                    )
                else:
                    self.status_callback(
                        'Invalid AI response. Retrying request %d of %d...' % (
                            format_attempt + 1, format_attempts
                        )
                    )
                    continue
            if mode == 'full':
                self._repair_suspicious_results(
                    results, pairs, terminology,
                    source_language, target_language,
                )
                # Inspect the raw model wording before the last-resort local
                # cleanup. Otherwise ``old wording [new wording]`` can lose
                # only its brackets and evade the focused repair pass.
                self._remove_new_glossary_brackets(
                    results, pairs, terminology
                )
            elif mode == 'polish':
                self._remove_new_glossary_brackets(results, pairs, [])
                self._flag_revised_language(results, pairs, [])
            else:
                self._flag_revised_language(results, pairs, terminology)
            break
        self._append_glossary_audit(results, pairs, terminology)
        if len(pairs) == 1 and (
            largest_source > chunk_chars or largest_target > chunk_chars
        ):
            results[0]['notes'] = (
                results[0]['notes'] + '\n\n'
                'This single paragraph exceeded the configured chunk target and '
                'was sent alone without truncation.'
            ).strip()
        return results

    @staticmethod
    def _optional_prompt(title, value):
        value = str(value or '').strip()
        if not value:
            return ''
        return '%s:\n%s' % (title, value)

    def _request_text(
        self, system_prompt, payload_text, use_search=False,
        response_schema=None,
    ):
        if self.provider == 'gemini':
            return self._gemini(
                system_prompt, payload_text,
                use_search=use_search,
                response_schema=response_schema,
            )
        return self._openai_compatible(system_prompt, payload_text)

    def suggest_terminology(
        self, candidates, book_metadata=None, use_search=False,
    ):
        """Return one contextual target-language proposal per candidate."""
        self.validate()
        if self.provider == 'local':
            raise APIError(
                'Automatic terminology research is disabled for local models.'
            )
        candidates = [dict(value) for value in candidates or ()]
        if not candidates:
            return []
        source_language = self._language_instruction('source')
        target_language = self._language_instruction('target')
        system_prompt = (
            'You are building a compact terminology glossary for a translated '
            'book. For every candidate, use its original-language spelling and '
            'the supplied bilingual context excerpts to choose exactly one '
            'canonical %s dictionary/base form. First classify the candidate as '
            'character, place, skill, title, organization, concept, ordinary, '
            'or unknown. Translate meaningful names of skills, titles, attacks, '
            'objects, and concepts; transliterate genuine personal/place names '
            'when semantic translation is inappropriate. For coined compounds '
            'and genre terminology, decompose the source meaning and create a '
            'natural, memorable target-language adaptation rather than a rigid '
            'word-for-word calque or phonetic copy. Treat wording found in the '
            'existing translation excerpts as noisy evidence: if it is awkward, '
            'nonsensical, overly literal, or merely transliterates a meaningful '
            'term, replace it with your independently reasoned proposal. Do not '
            'preserve an '
            'untranslated %s term in the target form. Infer character gender '
            'only from clear textual evidence; otherwise return unknown. Never '
            'invent certainty from the spelling of a name. Return one proposal, '
            'not alternatives. Existing glossary decisions are intentionally '
            'not supplied: independently assess the evidence. Output JSON only.'
            % (target_language, source_language)
        )
        if use_search and self.provider == 'gemini':
            system_prompt += (
                ' You may use Google Search to check publicly available '
                'authoritative information about the named book or series, but '
                'the supplied book passages remain the primary evidence. Search '
                'for the source term, its component words, and genre usage; do '
                'not let an existing machine-translated form anchor the answer.'
            )
        payload = {
            'book': dict(book_metadata or {}),
            'candidates': candidates,
        }
        schema = {
            'type': 'OBJECT',
            'properties': {
                'items': {
                    'type': 'ARRAY',
                    'items': {
                        'type': 'OBJECT',
                        'properties': {
                            'id': {'type': 'STRING'},
                            'canonical': {'type': 'STRING'},
                            'category': {'type': 'STRING'},
                            'gender': {'type': 'STRING'},
                            'confidence': {'type': 'NUMBER'},
                            'notes': {'type': 'STRING'},
                        },
                        'required': [
                            'id', 'canonical', 'category', 'gender',
                            'confidence', 'notes',
                        ],
                    },
                },
            },
            'required': ['items'],
        }
        response = self._request_text(
            system_prompt,
            json.dumps(payload, ensure_ascii=False),
            use_search=bool(use_search and self.provider == 'gemini'),
            response_schema=schema,
        )
        value = _json_value(response)
        raw_items = value.get('items', []) if isinstance(value, dict) else []
        expected = {
            str(candidate.get('id', '')): candidate for candidate in candidates
        }
        results = []
        seen = set()
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item_id = str(raw.get('id', '')).strip()
            canonical = str(raw.get('canonical', '')).strip()
            if item_id not in expected or item_id in seen or not canonical:
                continue
            seen.add(item_id)
            try:
                confidence = max(
                    0.0, min(1.0, float(raw.get('confidence', 0.0)))
                )
            except (TypeError, ValueError):
                confidence = 0.0
            results.append({
                'id': item_id,
                'source': str(expected[item_id].get('source', '')).strip(),
                'canonical': canonical,
                'category': str(raw.get('category', 'unknown')).strip(),
                'gender': str(raw.get('gender', 'unknown')).strip(),
                'confidence': confidence,
                'notes': str(raw.get('notes', '')).strip(),
            })
        if not results:
            raise ResponseFormatError(
                'The AI returned no usable terminology proposals.'
            )
        return results

    def _operation_prompt(self, mode, source_language, target_language, terms):
        cleanup = mode == 'cleanup'
        scope = (
            'Correct confirmed terminology, untranslated source words, '
            'interference from another language, mixed-language forms, obvious '
            'calques, and locally inappropriate word choices.' if cleanup else
            'Correct only occurrences of the confirmed glossary terms.'
        )

        extra = '\n\n'.join(filter(None, (
            self._optional_prompt(
                'BOOK INSTRUCTIONS', self.settings.get('book_prompt', '')
            ),
            self._optional_prompt(
                'ADDITIONAL BOOK NOTES', self.settings.get('auto_prompt', '')
            ),
            self._terminology_prompt(terms),
        )))
        return (
            'You are a cautious %s copy editor using an aligned %s source. '
            '%s Make only localized word or short-phrase substitutions. Never '
            'rewrite an unaffected sentence or return a revised paragraph.\n\n'
            'For each change, copy "old" exactly from the translation and put '
            'only its replacement in "new". Exclude surrounding brackets, '
            'quotation marks, punctuation, and formatting from both fields; '
            'the plugin preserves structural context. A glossary canonical is '
            'a lemma/reference, not text to paste verbatim: use it only for the '
            'same concept or named entity and inflect it for the sentence. Do '
            'not replace an ordinary word merely because it resembles a name. '
            'Treat language_hints as candidates, not commands.\n\n'
            '%s\n\nReturn only JSON with one item per input id:\n'
            '{{"items":[{{"id":"same id","edits":['
            '{{"old":"exact existing span","new":"replacement"}}],'
            '"notes":"brief","alignment":"ok|uncertain|mismatch"}}]}}\n'
            'Use an empty edits array when no safe change is needed. On a true '
            'narrative mismatch, return no edits and alignment "mismatch".' % (
                target_language, source_language, scope, extra
            )
        )

    @staticmethod
    def _polish_prompt(source_language, target_language):
        return (
            'You are a conservative %s copy editor performing a final surface '
            'polish. The aligned %s original is context only and may resolve '
            'an obviously wrong or untranslated word. Correct spelling, typos, '
            'punctuation, grammar, agreement, syntax, interference from another '
            'language, mixed-language forms, '
            'untranslated source words, accidental malformed or invented '
            'words, and locally awkward wording. Preserve meaning, facts, '
            'character identity, tone, paragraph boundaries, and the existing '
            'terminology unless it is plainly erroneous in this exact context. '
            'Do not globally rewrite style, embellish, summarize, restore whole '
            'missing scenes, add explanations, or expose analysis. Preserve '
            'quotation marks, brackets, emphasis, and other structural context '
            'when the aligned original/translation uses them. Return polished '
            'publishable book text only.\n\n'
            'The input contains a "pairs" array. Return only valid JSON with '
            'exactly one item per input id, in the same order:\n'
            '{{"items":[{{"id":"same input id","revised":"complete polished '
            'translation only","notes":"brief","severity":"none|minor|major",'
            '"alignment":"ok|uncertain|mismatch"}}]}}\n\n'
            'If no correction is needed, copy the translation unchanged. Use '
            '"mismatch" only when the two passages are genuinely from different '
            'narrative locations; then keep the translation unchanged.' % (
                target_language, source_language
            )
        )

    def _editing_scope_prompt(self):
        if self.settings.get('repair_major_mistranslations', True):
            return (
                'EDITING SCOPE — MAJOR REPAIR ENABLED:\n'
                '- Be conservative about structural boundaries, but not about '
                'the quality of a bad translation.\n'
                '- When a source pair and translation pair belong to the same '
                'narrative moment, set alignment to "ok" even if the translation '
                'seriously mistranslates, paraphrases, omits, or adds sentences.\n'
                '- Rewrite the complete translation as needed. Translate source '
                'sentences missing locally from that corresponding pair and mark '
                'a substantial correction as severity "major".\n'
                '- A low textual similarity alone is not a mismatch. Use the '
                'neighboring pairs, characters, actions, and dialogue to decide.\n'
                '- Use "mismatch" only for a genuinely different narrative '
                'location or an unsafe shifted boundary. In that case preserve '
                'the existing translation rather than importing text belonging '
                'to an adjacent pair.\n'
                '- The revised field must contain publishable translated book '
                'text only: never ids, alignment notes, warnings, explanations, '
                'or parenthetical service comments.\n'
                '- Return only your final edit. Never expose scratchpad text, '
                'self-corrections, or messages such as "Wait, let me fix..." '
                'inside square/angle brackets.\n'
                '- Never introduce untranslated source-language terms into the '
                'translation. Translate descriptive terms; transliterate only '
                'genuine proper names when semantic translation is unsuitable.'
            )
        return (
            'EDITING SCOPE — CONSERVATIVE MODE:\n'
            'Correct only material already represented in the translation. Do '
            'not restore omitted source sentences. Keep uncertain or mismatched '
            'pairs unchanged. The revised field must contain book text only, '
            'never ids, warnings, or service comments. Never introduce '
            'untranslated source-language terms into the translation. Never '
            'include scratchpad reasoning or self-correction messages.'
        )

    def _target_uses_cyrillic(self):
        language = str(self.settings.get('target_language', '')).casefold()
        return any(marker in language for marker in CYRILLIC_TARGET_MARKERS)

    def _target_is_ukrainian(self):
        language = str(self.settings.get('target_language', '')).casefold()
        return bool(re.search(r'(^|\W)(?:uk|ukr)(?:\W|$)', language)) or any(
            marker in language for marker in ('ukrain', 'укра')
        )

    @staticmethod
    def _source_phrases(text):
        phrases = set()
        for run in LATIN_RUN_RE.findall(str(text or '')):
            words = LATIN_TOKEN_RE.findall(run)
            for size in range(2, min(5, len(words)) + 1):
                for index in range(len(words) - size + 1):
                    phrase = ' '.join(words[index:index + size])
                    if len(phrase) >= 7:
                        phrases.add(phrase)
        return phrases

    @staticmethod
    def _contains_latin_phrase(text, phrase):
        words = LATIN_TOKEN_RE.findall(phrase)
        if not words:
            return False
        pattern = r'(?<![A-Za-z])' + r'\s+'.join(
            re.escape(word) for word in words
        ) + r'(?![A-Za-z])'
        return bool(re.search(pattern, str(text or ''), flags=re.I))

    def _flag_revised_language(self, results, pairs, terminology):
        if not self._target_uses_cyrillic():
            return
        term_sources = {
            str(term.get('source', '')).strip()
            for term in (terminology or [])
            if str(term.get('source', '')).strip()
        }
        for pair, result in zip(pairs, results):
            revised = str(result.get('revised', ''))
            translation = str(pair.get('translation', ''))
            candidates = self._source_phrases(pair.get('original', ''))
            candidates.update(term_sources)
            introduced = []
            for phrase in sorted(candidates, key=len, reverse=True):
                if any(
                    phrase.casefold() in longer.casefold()
                    for longer in introduced
                ):
                    continue
                if not re.search(r'[A-Za-z]{2,}', phrase):
                    continue
                if (
                    self._contains_latin_phrase(revised, phrase) and
                    not self._contains_latin_phrase(translation, phrase)
                ):
                    introduced.append(phrase[:120])
            if introduced:
                result['alignment'] = (
                    'mismatch' if result.get('alignment') == 'mismatch'
                    else 'uncertain'
                )
                result['notes'] = '\n\n'.join(filter(None, (
                    result.get('notes', ''),
                    'Plugin safeguard: the suggestion introduced untranslated '
                    'source-language text (%s). It is shown for manual review, '
                    'excluded from Accept all, and did not trigger a paid '
                    'retry.' % ', '.join(
                        '"%s"' % value for value in introduced[:8]
                    ),
                )))

    def _introduced_source_copy(self, original, translation, revised):
        """Find source-language wording newly copied into a Cyrillic revision."""
        candidates = self._source_phrases(original)
        candidates.update(
            token for token in LATIN_TOKEN_RE.findall(str(original or ''))
            if len(token) >= 3
        )
        introduced = []
        for phrase in sorted(candidates, key=lambda value: (-len(value), value)):
            if any(
                phrase.casefold() in longer.casefold()
                for longer in introduced
            ):
                continue
            if (
                self._contains_latin_phrase(revised, phrase) and
                not self._contains_latin_phrase(translation, phrase)
            ):
                introduced.append(phrase[:120])
        return introduced

    @staticmethod
    def _looks_incomplete(original, translation, revised):
        def compact_length(value):
            return len(re.sub(r'\s+', '', str(value or '')))

        original_length = compact_length(original)
        translation_length = compact_length(translation)
        revised_length = compact_length(revised)
        if revised_length < 40:
            return max(original_length, translation_length) >= 140

        shorter_than_both = (
            original_length >= 180 and translation_length >= 180 and
            revised_length < min(original_length, translation_length) * 0.62
        )
        original_sentences = len(SENTENCE_END_RE.findall(str(original or '')))
        translation_sentences = len(
            SENTENCE_END_RE.findall(str(translation or ''))
        )
        revised_sentences = len(SENTENCE_END_RE.findall(str(revised or '')))
        lost_sentences = (
            min(original_sentences, translation_sentences) >= 4 and
            revised_sentences <= min(
                original_sentences, translation_sentences
            ) - 2 and
            revised_length < translation_length * 0.78
        )
        stripped = str(revised or '').rstrip()
        abrupt_ending = bool(
            stripped and stripped[-1] in ',:;—-' and
            revised_length < translation_length * 0.90
        )
        return shorter_than_both or lost_sentences or abrupt_ending

    def _quality_issues(self, pair, result, terminology=None):
        original = str(pair.get('original', ''))
        translation = str(pair.get('translation', ''))
        revised = str(result.get('revised', ''))
        issues = []
        if self._target_uses_cyrillic():
            required = self._required_glossary(original, terminology)
            untranslated_required = [
                '%s → %s' % (term['source'], term['canonical'])
                for term in required
                if self._contains_source_term(
                    revised, term['source'],
                    case_sensitive=bool(term.get('case_sensitive_source')),
                )
            ]
            if untranslated_required:
                issues.append(
                    'required glossary source wording remains untranslated: %s'
                    % ', '.join(untranslated_required[:8])
                )
            introduced = self._introduced_source_copy(
                original, translation, revised
            )
            if introduced:
                issues.append(
                    'newly copied source-language wording: %s' % ', '.join(
                        '"%s"' % value for value in introduced[:8]
                    )
                )
        for label, pattern in (
            ('round parentheses', ROUND_GROUP_RE),
            ('square brackets', SQUARE_GROUP_RE),
        ):
            revised_count = len(pattern.findall(revised))
            expected_count = max(
                len(pattern.findall(original)),
                len(pattern.findall(translation)),
            )
            if revised_count > expected_count:
                issues.append(
                    'new editorial text in %s that has no aligned structural '
                    'counterpart' % label
                )
        if self._looks_incomplete(original, translation, revised):
            issues.append(
                'the proposed paragraph appears truncated or materially incomplete'
            )
        return issues

    @staticmethod
    def _append_quality_note(result, message):
        result['notes'] = '\n\n'.join(filter(None, (
            str(result.get('notes', '')).strip(), message,
        )))
        if result.get('alignment') != 'mismatch':
            result['alignment'] = 'uncertain'

    def _repair_suspicious_results(
        self, results, pairs, terminology, source_language, target_language
    ):
        suspicious = []
        for index, (pair, result) in enumerate(zip(pairs, results)):
            if result.get('_plugin_missing'):
                continue
            issues = self._quality_issues(pair, result, terminology)
            if issues:
                suspicious.append((index, pair, result, issues))
        if not suspicious:
            return

        if not self.settings.get('auto_repair_suspicious_responses', True):
            for _index, _pair, result, issues in suspicious:
                self._append_quality_note(
                    result,
                    'Plugin quality check: %s. The suggestion remains editable '
                    'and requires manual review.' % '; '.join(issues),
                )
            return

        self.status_callback(
            'Quality check found %d suspicious revision(s). Sending one focused '
            'repair request...' % len(suspicious)
        )
        interval = max(
            0.0, float(self.settings.get('request_interval', 0.5))
        )
        if interval:
            time.sleep(interval)
        repair_pairs = [{
            'id': str(pair.get('id', '')),
            'original': str(pair.get('original', '')),
            'current_translation': str(pair.get('translation', '')),
            'proposed_revision': str(result.get('revised', '')),
            'detected_problems': issues,
            'required_glossary': self._required_glossary(
                pair.get('original', ''), terminology
            ),
        } for _index, pair, result, issues in suspicious]
        repair_prompt = (
            'You are the final quality-control editor for a %s literary '
            'translation aligned with a %s source. Repair each proposed revision '
            'only where needed. Compare all three fields. Return a complete, '
            'publishable translation paragraph, never a fragment.\n\n'
            'Never copy source-language wording into the translation merely '
            'because a term is uncertain. Translate descriptive names and terms '
            'naturally; preserve an existing target-language rendering when it '
            'is safer. Do not show alternatives, corrections, explanations, or '
            'editorial comments in parentheses or brackets unless the aligned '
            'source itself contains the equivalent structure. Preserve meaning, '
            'paragraph function, dialogue, punctuation, and confirmed glossary '
            'forms.\n\n%s\n\nReturn only JSON with exactly one item per id: '
            '{"items":[{"id":"same id","revised":"complete final '
            'translation only","notes":"brief repair note","severity":'
            '"none|minor|major","alignment":"ok|uncertain|mismatch"}]}' % (
                target_language, source_language,
                self._terminology_prompt(terminology),
            )
        )
        payload = json.dumps({'pairs': repair_pairs}, ensure_ascii=False)
        expected_ids = [entry['id'] for entry in repair_pairs]
        try:
            repaired = _json_results(
                self._request_text(repair_prompt, payload), expected_ids
            )
        except Exception as err:
            for _index, _pair, result, issues in suspicious:
                self._append_quality_note(
                    result,
                    'Plugin quality check: %s. The single automatic repair '
                    'request failed (%s); the first AI suggestion remains '
                    'editable for manual review.' % ('; '.join(issues), err),
                )
            return

        repaired_by_id = {
            str(result.get('id', '')): result for result in repaired
        }
        for index, pair, initial, initial_issues in suspicious:
            repaired_result = repaired_by_id.get(str(pair.get('id', '')))
            if not repaired_result:
                self._append_quality_note(
                    initial,
                    'Plugin quality check: %s. The repair response omitted this '
                    'id, so the first suggestion remains editable.' % '; '.join(
                        initial_issues
                    ),
                )
                continue
            remaining = self._quality_issues(
                pair, repaired_result, terminology
            )
            if remaining:
                self._append_quality_note(
                    repaired_result,
                    'Automatic quality repair was attempted for: %s. Remaining '
                    'concern: %s. This repaired suggestion is shown for manual '
                    'review and is excluded from Accept all.' % (
                        '; '.join(initial_issues), '; '.join(remaining)
                    ),
                )
            else:
                repaired_result['notes'] = '\n\n'.join(filter(None, (
                    repaired_result.get('notes', ''),
                    'Plugin quality repair: one focused follow-up corrected a '
                    'suspicious first response (%s).' % '; '.join(initial_issues),
                )))
            results[index] = repaired_result

    @staticmethod
    def _active_terms(terms):
        active = []
        for term in terms or []:
            source = str(term.get('source', '')).strip()
            words = LATIN_TOKEN_RE.findall(source)
            status = str(term.get('status', '')).upper()
            enabled = status in {'USER', 'AUTO'} or (
                status == 'CONFIRMED' and
                bool(term.get('confirmed_by_user', False))
            )
            # A one-word lower-case entry is inherently polysemous and unsafe
            # as a global instruction (especially slang/profanity). Keep it in
            # the library for inspection, but do not send it to the model.
            if (
                source and term.get('canonical') and enabled and
                not (len(words) == 1 and source == source.lower())
            ):
                active.append(dict(term))
        return active

    @staticmethod
    def _contains_source_term(text, source, case_sensitive=False):
        source = str(source or '').strip()
        if not source:
            return False
        escaped = re.escape(source).replace(r'\ ', r'\s+')
        return bool(re.search(
            r'(?<![A-Za-z0-9])%s(?![A-Za-z0-9])' % escaped,
            str(text or ''), flags=0 if case_sensitive else re.I,
        ))

    @classmethod
    def _required_glossary(cls, original, terms):
        required = []
        for term in cls._active_terms(terms):
            source = str(term.get('source', '')).strip()
            case_sensitive = bool(
                term.get('proper_name') or
                any(character.isupper() for character in source)
            )
            if not cls._contains_source_term(
                original, source, case_sensitive=case_sensitive
            ):
                continue
            entry = {
                'source': source,
                'canonical': str(term.get('canonical', '')).strip(),
                'canonical_is_lemma': True,
                'case_sensitive_source': case_sensitive,
            }
            if term.get('proper_name'):
                entry['proper_name'] = True
            gender = str(term.get('gender', '')).strip()
            if gender:
                entry['gender_grammar'] = gender
            required.append(entry)
        required.sort(key=lambda value: (
            -len(value['source']), value['source'].casefold()
        ))
        return required

    @classmethod
    def _append_glossary_audit(cls, results, pairs, terminology):
        for pair, result in zip(pairs, results):
            required = cls._required_glossary(
                pair.get('original', ''), terminology
            )
            if not required:
                continue
            summary = '; '.join(
                '%s → %s' % (term['source'], term['canonical'])
                for term in required
            )
            result['notes'] = '\n\n'.join(filter(None, (
                str(result.get('notes', '')).strip(),
                'Plugin request glossary (sent with this pair): %s.' % summary,
            )))

    @staticmethod
    def _remove_new_glossary_brackets(results, pairs, terminology):
        """Undo model-added glossary brackets when the source has none."""
        mappings = [
            (str(term.get('source', '')).strip(),
             str(term.get('canonical', '')).strip())
            for term in (terminology or [])
            if term.get('source') and term.get('canonical')
        ]
        for pair, result in zip(pairs, results):
            original = str(pair.get('original', ''))
            prior = str(pair.get('translation', ''))
            revised = str(result.get('revised', ''))
            removed = []
            if '[' not in original and '[' not in prior and '[' in revised:
                revised, count = re.subn(
                    r'\[([^\[\]\n]{1,300})\]', r'\1', revised
                )
                if count:
                    removed.append('%d unexpected bracketed span(s)' % count)
            for source, canonical in mappings:
                source_pattern = re.compile(
                    r'(?<![A-Za-z])%s(?![A-Za-z])' % re.escape(source), re.I
                )
                if not source_pattern.search(original):
                    continue
                source_wrapped = re.compile(
                    r'\[\s*%s\s*\]' % re.escape(source), re.I
                )
                target_wrapped = re.compile(
                    r'\[\s*(%s)\s*\]' % re.escape(canonical), re.I
                )
                # Only strip brackets newly introduced by this response and
                # only when no equivalent source occurrence is bracketed.
                if (
                    not source_wrapped.search(original) and
                    not target_wrapped.search(prior) and
                    target_wrapped.search(revised)
                ):
                    revised, count = target_wrapped.subn(r'\1', revised)
                    if count:
                        removed.append(canonical)
            if removed:
                result['revised'] = revised
                result['notes'] = '\n\n'.join(filter(None, (
                    result.get('notes', ''),
                    'Plugin safeguard: removed model-added square brackets '
                    'because the aligned source context was not bracketed: '
                    '%s.' % ', '.join(
                        sorted(set(removed), key=str.casefold)
                    ),
                )))

    def _filter_term_decisions(self, results):
        if not results:
            return
        decisions = results[0].get('term_decisions', []) or []
        accepted = []
        rejected = []
        for decision in decisions:
            source = str(decision.get('source', '')).strip()
            target = str(decision.get('target', '')).strip()
            invalid_cyrillic_target = (
                self._target_uses_cyrillic() and
                bool(re.search(r'[A-Za-z]{2,}', target))
            )
            if (
                _looks_untranslated_term(source, target) or
                invalid_cyrillic_target
            ):
                rejected.append(source)
                continue
            accepted.append(decision)
        results[0]['term_decisions'] = accepted
        if rejected:
            results[0]['notes'] = '\n\n'.join(filter(None, (
                results[0].get('notes', ''),
                'Plugin safeguard: ignored untranslated terminology decisions '
                'for %s.' % ', '.join(sorted(set(rejected), key=str.casefold)),
            )))

    @staticmethod
    def _terminology_prompt(terms):
        terms = ProofreaderClient._active_terms(terms)
        if not terms:
            return 'CONFIRMED GLOSSARY: none for this request.'
        lines = [
            'CONFIRMED GLOSSARY FOR THIS REQUEST:',
            'Canonical forms are lemmas: use only for the same contextual sense '
            'or named entity, and inflect them naturally instead of pasting them.',
        ]
        for term in terms:
            source = str(term.get('source', '')).strip()
            canonical = str(term.get('canonical', '')).strip()
            gender = str(term.get('gender', '')).strip()
            details = []
            if term.get('proper_name'):
                details.append('proper name; keep this spelling')
            if gender:
                details.append('gender/grammar: %s' % gender)
            suffix = '; ' + '; '.join(details) if details else ''
            lines.append('- %s = %s%s' % (source, canonical, suffix))
        return '\n'.join(lines)

    def _language_instruction(self, role):
        auto_key = 'auto_detect_%s_language' % role
        language_key = '%s_language' % role
        if self.settings.get(auto_key, False):
            passage = 'original' if role == 'source' else 'translation'
            return 'detect automatically from the %s passage' % passage
        fallback = (
            'the source language' if role == 'source'
            else 'the translation language'
        )
        return str(self.settings.get(language_key, fallback)).strip() or fallback

    def list_models(self):
        self.validate(require_model=False)
        if self.provider == 'gemini':
            models = self._gemini_models()
        else:
            models = self._compatible_models()
        models = sorted({model for model in models if model}, key=str.lower)
        if not models:
            raise APIError('Connected successfully, but no models were found.')
        return models

    def _sleep_before_retry(self, attempt, retry_after=None):
        base = max(0.1, float(self.settings.get('retry_delay', 2.0)))
        delay = min(60.0, base * (2 ** max(0, attempt - 1)))
        if retry_after:
            try:
                delay = min(120.0, max(delay, float(retry_after)))
            except (TypeError, ValueError):
                pass
        attempts = max(1, int(self.settings.get('request_attempts', 10)))
        self.status_callback(
            'Temporary API error. Retry %d of %d in %.1f s…' % (
                attempt + 1, attempts, delay
            )
        )
        time.sleep(delay)

    def _request(self, request):
        timeout = max(5, int(self.settings.get('timeout', 90)))
        attempts = max(1, int(self.settings.get('request_attempts', 10)))
        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    body = response.read().decode('utf-8', errors='replace')
            except urllib.error.HTTPError as err:
                body = err.read().decode('utf-8', errors='replace')
                detail = self._error_detail(body)
                if _content_blocked(detail):
                    raise ContentBlockedError(
                        'The provider blocked this passage with a content filter: %s' % detail
                    )
                if err.code in RETRYABLE_HTTP_CODES and attempt < attempts:
                    self._sleep_before_retry(
                        attempt, err.headers.get('Retry-After') if err.headers else None
                    )
                    continue
                raise APIError('The API returned HTTP %s: %s' % (err.code, detail))
            except (urllib.error.URLError, TimeoutError, socket.timeout) as err:
                if attempt < attempts:
                    self._sleep_before_retry(attempt)
                    continue
                reason = getattr(err, 'reason', err)
                raise APIError(
                    'Could not connect to the API after %d attempts: %s' % (
                        attempts, reason
                    )
                )
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                raise APIError('The API returned non-JSON data: %s' % body[:500])
        raise APIError('Could not complete the API request.')

    @staticmethod
    def _error_detail(body):
        try:
            detail = json.loads(body)
            detail = detail.get('error', detail)
            if isinstance(detail, dict):
                detail = detail.get(
                    'message', json.dumps(detail, ensure_ascii=False)
                )
            return str(detail)
        except Exception:
            return body[:1000]

    def _gemini_endpoint(self):
        model = str(self.settings['model']).strip()
        if model.startswith('models/'):
            model = model.split('/', 1)[1]
        endpoint = str(self.settings['endpoint']).strip()
        quoted_model = urllib.parse.quote(model, safe='-_.')
        if '{model}' in endpoint:
            endpoint = endpoint.format(model=quoted_model)
        elif endpoint.rstrip('/').endswith('/models'):
            endpoint = endpoint.rstrip('/') + '/' + quoted_model + ':generateContent'
        return endpoint

    def _gemini_headers(self):
        return {
            'Content-Type': 'application/json',
            'x-goog-api-key': str(self.settings['api_key']).strip(),
        }

    def _gemini(
        self, system_prompt, payload_text, use_search=False,
        response_schema=None,
    ):
        model = str(self.settings['model']).strip().removeprefix('models/')
        generation_config = {'responseMimeType': 'application/json'}
        if response_schema:
            generation_config['responseSchema'] = response_schema
        if not model.startswith(('gemini-3.5-', 'gemini-3.6-')):
            generation_config['temperature'] = float(
                self.settings.get('temperature', 0.2)
            )
        body = {
            'systemInstruction': {'parts': [{'text': system_prompt}]},
            'contents': [{
                'role': 'user',
                'parts': [{'text': payload_text}],
            }],
            'generationConfig': generation_config,
        }
        if use_search:
            body['tools'] = [{'google_search': {}}]
        request = urllib.request.Request(
            self._gemini_endpoint(),
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers=self._gemini_headers(),
            method='POST',
        )
        data = self._request(request)
        prompt_feedback = data.get('promptFeedback', {})
        block_reason = prompt_feedback.get('blockReason')
        if block_reason:
            raise ContentBlockedError(
                'Gemini blocked this passage: %s' % block_reason
            )
        try:
            candidate = data['candidates'][0]
            finish_reason = str(candidate.get('finishReason', '')).upper()
            if finish_reason in {
                'SAFETY', 'PROHIBITED_CONTENT', 'BLOCKLIST', 'RECITATION'
            }:
                raise ContentBlockedError(
                    'Gemini stopped the response with a content filter: %s' % finish_reason
                )
            return ''.join(
                str(part.get('text', ''))
                for part in candidate['content']['parts']
                if isinstance(part, dict) and part.get('text')
            )
        except ContentBlockedError:
            raise
        except (KeyError, IndexError, TypeError):
            raise APIError(
                'Unexpected Gemini response structure: %s' % (
                    json.dumps(data, ensure_ascii=False)[:1000]
                )
            )

    def _gemini_models(self):
        endpoint = str(self.settings['endpoint']).strip()
        marker = '/models'
        index = endpoint.find(marker)
        if index < 0:
            raise APIError('The Gemini endpoint must contain a /models path.')
        url = endpoint[:index + len(marker)] + '?pageSize=1000'
        request = urllib.request.Request(
            url, headers=self._gemini_headers(), method='GET'
        )
        data = self._request(request)
        result = []
        for model in data.get('models', []):
            methods = model.get('supportedGenerationMethods', [])
            if 'generateContent' in methods:
                result.append(str(model.get('name', '')).removeprefix('models/'))
        return result

    def _auth_headers(self):
        headers = {'Content-Type': 'application/json'}
        api_key = str(self.settings.get('api_key', '')).strip()
        if api_key:
            headers['Authorization'] = 'Bearer %s' % api_key
        return headers

    def _openai_compatible(self, system_prompt, payload_text):
        body = {
            'model': str(self.settings['model']).strip(),
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': payload_text},
            ],
            'temperature': float(self.settings.get('temperature', 0.2)),
        }
        request = urllib.request.Request(
            str(self.settings['endpoint']).strip(),
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers=self._auth_headers(),
            method='POST',
        )
        data = self._request(request)
        try:
            choice = data['choices'][0]
            finish_reason = str(choice.get('finish_reason', '')).lower()
            if finish_reason in {'content_filter', 'safety'}:
                raise ContentBlockedError(
                    'The provider stopped the response with a content filter: %s' % finish_reason
                )
            content = choice['message']['content']
            if isinstance(content, list):
                return ''.join(
                    part.get('text', '') for part in content
                    if isinstance(part, dict)
                )
            return content
        except ContentBlockedError:
            raise
        except (KeyError, IndexError, TypeError):
            raise APIError(
                'Unexpected OpenAI-compatible response structure: %s' % (
                    json.dumps(data, ensure_ascii=False)[:1000]
                )
            )

    def _model_urls(self):
        endpoint = str(self.settings['endpoint']).strip()
        parsed = urllib.parse.urlsplit(endpoint)
        path = parsed.path.rstrip('/')
        for suffix in ('/chat/completions', '/responses', '/completions'):
            if path.endswith(suffix):
                path = path[:-len(suffix)] + '/models'
                break
        else:
            path += '/models'
        standard = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, path, '', '')
        )
        urls = [standard]
        if self.provider == 'local':
            base = urllib.parse.urlunsplit(
                (parsed.scheme, parsed.netloc, '', '', '')
            ).rstrip('/')
            if parsed.port == 11434:
                urls.append(base + '/api/tags')
            if parsed.port == 1234:
                urls.append(base + '/api/v1/models')
        return list(dict.fromkeys(urls))

    @staticmethod
    def _models_from_response(data):
        models = []
        for entry in data.get('data', []):
            if isinstance(entry, dict):
                models.append(str(entry.get('id') or entry.get('model') or ''))
        for entry in data.get('models', []):
            if isinstance(entry, dict):
                models.append(str(
                    entry.get('id') or entry.get('model') or
                    entry.get('name') or entry.get('key') or ''
                ))
        return [model for model in models if model]

    def _compatible_models(self):
        errors = []
        for url in self._model_urls():
            try:
                request = urllib.request.Request(
                    url, headers=self._auth_headers(), method='GET'
                )
                models = self._models_from_response(self._request(request))
                if models:
                    return models
            except APIError as err:
                errors.append('%s → %s' % (url, err))
        raise APIError(
            'Could not fetch the model list. Endpoints checked:\n%s' %
            '\n'.join(errors or self._model_urls())
        )
