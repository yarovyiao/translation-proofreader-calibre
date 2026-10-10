import copy
import json
import os
import time


BUNDLE_FORMAT = 'translation-proofreader-portable-bundle'
BUNDLE_VERSION = 1
MAX_BUNDLE_BYTES = 50 * 1024 * 1024


class BundleError(ValueError):
    pass


def build_bundle(profile_library, core_prompt):
    profiles = {}
    for name in profile_library.names():
        try:
            profiles[name] = profile_library.load(name)
        except ValueError as err:
            raise BundleError(str(err))
    return {
        'format': BUNDLE_FORMAT,
        'version': BUNDLE_VERSION,
        'exported_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'core_prompt': str(core_prompt or ''),
        'profiles': profiles,
        'security': {
            'api_keys_included': False,
            'ai_settings_included': False,
        },
    }


def write_bundle(path, profile_library, core_prompt):
    path = os.path.abspath(str(path or ''))
    if not path:
        raise BundleError('Choose an export file.')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + '.tmp'
    try:
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(
                build_bundle(profile_library, core_prompt), stream,
                ensure_ascii=False, indent=2, sort_keys=True,
            )
        os.replace(temporary, path)
    except OSError as err:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise BundleError('Could not write the portable bundle: %s' % err)
    return path


def read_bundle(path):
    path = os.path.abspath(str(path or ''))
    try:
        size = os.path.getsize(path)
    except OSError as err:
        raise BundleError('Could not open the portable bundle: %s' % err)
    if size > MAX_BUNDLE_BYTES:
        raise BundleError('The selected bundle is larger than 50 MB.')
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            data = json.load(stream)
    except (OSError, ValueError, TypeError) as err:
        raise BundleError('The selected file is not valid JSON: %s' % err)
    if not isinstance(data, dict) or data.get('format') != BUNDLE_FORMAT:
        raise BundleError('This is not a Translation Proofreader bundle.')
    if int(data.get('version', 0) or 0) != BUNDLE_VERSION:
        raise BundleError(
            'Unsupported portable bundle version: %s.' % data.get('version')
        )
    core_prompt = data.get('core_prompt', '')
    profiles = data.get('profiles', {})
    if not isinstance(core_prompt, str) or not isinstance(profiles, dict):
        raise BundleError('The portable bundle has an invalid structure.')
    if len(core_prompt) > 2_000_000 or len(profiles) > 10_000:
        raise BundleError('The portable bundle exceeds safe content limits.')
    cleaned_profiles = {}
    for raw_name, raw_payload in profiles.items():
        name = ' '.join(str(raw_name or '').split()).strip()[:160]
        if not name or not isinstance(raw_payload, dict):
            raise BundleError('The bundle contains an invalid profile entry.')
        terms = raw_payload.get('terms', {})
        if not isinstance(terms, dict):
            raise BundleError('Profile "%s" has an invalid terminology list.' % name)
        cleaned_profiles[name] = copy.deepcopy(raw_payload)
    return {
        'format': BUNDLE_FORMAT,
        'version': BUNDLE_VERSION,
        'core_prompt': core_prompt,
        'profiles': cleaned_profiles,
    }


def import_profiles(bundle, profile_library, replace_existing=False):
    profiles = bundle.get('profiles', {})
    existing_by_name = {
        name.casefold(): name for name in profile_library.names()
    }
    added = []
    merged = []
    replaced = []
    for name, payload in profiles.items():
        existing = existing_by_name.get(name.casefold())
        if existing:
            if replace_existing:
                profile_library.save_payload(existing, payload)
                replaced.append(existing)
            else:
                profile_library.merge_payload(existing, payload)
                merged.append(existing)
        else:
            profile_library.save_payload(name, payload)
            added.append(name)
            existing_by_name[name.casefold()] = name
    return {
        'added': added,
        'merged': merged,
        'replaced': replaced,
    }
