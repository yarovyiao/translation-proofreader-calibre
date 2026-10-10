import os
import time

from calibre.utils.config import JSONConfig


DEFAULT_PROMPT = """You are a literary translation editor.

Compare each {target_language} translation with its aligned {source_language}
source. Correct mistranslations, missing or unjustified text, untranslated
source words, interference from another language, mixed-language forms, awkward
calques, grammar, punctuation, and unnatural literary wording. You may rewrite a poor translation extensively while
preserving the author's meaning, tone, facts, and paragraph function.

Use the short active glossary below as a terminology reference, not as literal
replacement text. Apply an entry only when the aligned source uses the same
name, concept, or sense. Inflect names and terms naturally for the sentence and
capitalize them according to context. Obey supplied character gender notes. Do
not invent alternative names or terminology.

Preserve structural context as in the aligned source: brackets, quotation
marks, paragraph boundaries, and emphasis must appear only where the source has
their equivalent. A glossary entry itself never implies brackets or formatting.

Use neighboring pairs as context. A poor translation is still aligned when it
belongs to the same narrative moment. Use "mismatch" only for a different
narrative location or unsafe shifted boundary; then keep the existing
translation unchanged.

Any language_hints in an input pair are locally detected candidates. Verify
them against both texts; they are not mandatory corrections.

{book_instructions}

{auto_memory_instructions}

{terminology_instructions}

The input contains a "pairs" array. Return only valid JSON with exactly one
item per input id, in the same order:
{{"items":[{{"id":"same input id","revised":"complete corrected translation only","notes":"brief explanation","severity":"none|minor|major","alignment":"ok|uncertain|mismatch"}}]}}

If no change is needed, copy the translation unchanged. The revised field must
contain publishable translated book text only: no source copy, labels,
warnings, analysis, scratchpad, or service comments."""


DEFAULTS = {
    'provider': 'gemini',
    'api_key': '',
    'endpoint': 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
    'model': '',
    'source_language': '',
    'target_language': '',
    'auto_detect_source_language': True,
    'auto_detect_target_language': True,
    'review_mode': 'full',
    'prompt': DEFAULT_PROMPT,
    'temperature': 0.5,
    'timeout': 90,
    'chunk_chars': 8000,
    'request_attempts': 10,
    'response_attempts': 3,
    'auto_repair_suspicious_responses': True,
    'retry_delay': 2.0,
    'request_interval': 0.5,
    'auto_cleanup_merged_prefixes': True,
    'repair_major_mistranslations': True,
    # Interface preferences are deliberately global rather than part of an
    # API profile.  Changing providers must not unexpectedly change the UI or
    # expose an optional workflow the user had hidden.
    # Preserve the historical/public English UI until the user explicitly
    # selects another language (or Automatic) in settings.
    'interface_language': 'en',
    'experimental_polishing': False,
    'ai_profiles': {},
    'active_ai_profile': '',
}


AI_PROFILE_KEYS = (
    'provider', 'api_key', 'endpoint', 'model',
    'review_mode',
    'source_language', 'target_language',
    'auto_detect_source_language', 'auto_detect_target_language',
    'temperature', 'timeout', 'chunk_chars', 'request_attempts',
    'response_attempts', 'auto_repair_suspicious_responses',
    'retry_delay', 'request_interval',
    'auto_cleanup_merged_prefixes', 'repair_major_mistranslations',
)


prefs = JSONConfig('plugins/translation_proofreader')
prefs.defaults = DEFAULTS


def get_settings():
    # 0.11 replaces the old voting/UNRESOLVED prompt. Preserve genuinely
    # custom modern prompts, but migrate any prompt carrying the old JSON
    # contract once and keep a backup in the Calibre profile.
    if int(prefs.get('settings_schema', 0) or 0) < 2:
        old_prompt = str(prefs.get('prompt', '') or '')
        if '"term_decisions"' in old_prompt or (
            'UNRESOLVED/AUTO candidates' in old_prompt
        ):
            prefs['legacy_prompt_backup'] = old_prompt
            prefs['prompt'] = DEFAULT_PROMPT
        if int(prefs.get('chunk_chars', 6000) or 6000) == 6000:
            prefs['chunk_chars'] = 8000
        prefs['settings_schema'] = 2
        prefs.commit()
    settings = {
        key: prefs.get(key, value) for key, value in DEFAULTS.items()
    }
    prompt = settings.get('prompt') or ''
    for variable in (
        'book_instructions', 'auto_memory_instructions',
        'terminology_instructions',
    ):
        placeholder = '{%s}' % variable
        if placeholder not in settings['prompt']:
            settings['prompt'] = settings['prompt'].rstrip() + '\n\n' + placeholder
    return settings


def save_settings(values):
    for key in DEFAULTS:
        if key in values:
            prefs[key] = values[key]
    prefs.commit()


def get_ai_profiles():
    profiles = prefs.get('ai_profiles', {})
    if not isinstance(profiles, dict):
        return {}
    return {
        str(name): dict(value)
        for name, value in profiles.items()
        if str(name).strip() and isinstance(value, dict)
    }


def save_ai_profile(name, values):
    name = ' '.join(str(name or '').split()).strip()[:160]
    if not name:
        raise ValueError('Enter a settings profile name.')
    profiles = get_ai_profiles()
    existing = next(
        (value for value in profiles if value.casefold() == name.casefold()),
        None,
    )
    if existing and existing != name:
        del profiles[existing]
    profiles[name] = {
        key: values[key] for key in AI_PROFILE_KEYS if key in values
    }
    prefs['ai_profiles'] = profiles
    prefs['active_ai_profile'] = name
    prefs.commit()
    return name


def delete_ai_profile(name):
    profiles = get_ai_profiles()
    found = next(
        (value for value in profiles if value.casefold() == str(name).casefold()),
        None,
    )
    if not found:
        return False
    del profiles[found]
    prefs['ai_profiles'] = profiles
    if str(prefs.get('active_ai_profile', '')).casefold() == found.casefold():
        prefs['active_ai_profile'] = ''
    prefs.commit()
    return True


def get_recent_book_pairs(limit=5):
    """Return existing recent original/translation pairs, newest first."""
    raw = prefs.get('recent_book_pairs', [])
    if not isinstance(raw, list):
        return []
    result = []
    seen = set()
    for value in raw:
        if not isinstance(value, dict):
            continue
        original = os.path.abspath(str(value.get('original', '') or ''))
        translation = os.path.abspath(str(value.get('translation', '') or ''))
        if not original or not translation:
            continue
        key = (os.path.normcase(original), os.path.normcase(translation))
        if key in seen or not (
            os.path.isfile(original) and os.path.isfile(translation)
        ):
            continue
        seen.add(key)
        result.append({
            'original': original,
            'translation': translation,
            'label': str(value.get('label', '') or '').strip(),
            'last_opened': float(value.get('last_opened', 0.0) or 0.0),
        })
        if len(result) >= max(1, int(limit)):
            break
    return result


def remember_book_pair(
    original, translation, original_title='', translation_title=''
):
    original = os.path.abspath(str(original or ''))
    translation = os.path.abspath(str(translation or ''))
    if not original or not translation:
        return
    original_name = str(original_title or '').strip() or os.path.splitext(
        os.path.basename(original)
    )[0]
    translation_name = str(translation_title or '').strip() or os.path.splitext(
        os.path.basename(translation)
    )[0]
    label = (
        original_name if original_name.casefold() == translation_name.casefold()
        else '%s ↔ %s' % (original_name, translation_name)
    )
    current = prefs.get('recent_book_pairs', [])
    if not isinstance(current, list):
        current = []
    key = (os.path.normcase(original), os.path.normcase(translation))
    kept = []
    for value in current:
        if not isinstance(value, dict):
            continue
        other = (
            os.path.normcase(os.path.abspath(str(value.get('original', '') or ''))),
            os.path.normcase(os.path.abspath(str(value.get('translation', '') or ''))),
        )
        if other != key:
            kept.append(value)
    prefs['recent_book_pairs'] = [{
        'original': original,
        'translation': translation,
        'label': label[:240],
        'last_opened': time.time(),
    }] + kept[:4]
    prefs.commit()
