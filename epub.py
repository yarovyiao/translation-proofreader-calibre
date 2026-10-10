import copy
import math
import os
import posixpath
import re
import zipfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from statistics import median
from urllib.parse import unquote

from lxml import etree


BLOCK_TAGS = {
    'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote',
    'figcaption', 'td', 'th', 'dt', 'dd',
}

PRESERVE_EXACT_TAGS = {
    'pre', 'code', 'kbd', 'samp', 'script', 'style', 'svg', 'math',
}


def _local_name(element):
    tag = getattr(element, 'tag', '')
    if not isinstance(tag, str):
        return ''
    return tag.rsplit('}', 1)[-1].lower()


def _clean_text(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def _explicitly_hidden(element):
    hidden = element.get('hidden')
    if hidden is not None:
        return True
    if (element.get('aria-hidden') or '').strip().lower() == 'true':
        return True
    style = re.sub(r'\s+', '', (element.get('style') or '').lower())
    return 'display:none' in style or 'visibility:hidden' in style


def _hidden_in_tree(element):
    return any(
        _explicitly_hidden(node)
        for node in (element, *element.iterancestors())
    )


def _visible_text(element):
    parts = []

    def walk(node):
        if node is not element and _explicitly_hidden(node):
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(element)
    return ''.join(parts)


@dataclass
class TextUnit:
    chapter_index: int
    block_index: int
    chapter_name: str
    document_path: str
    text: str
    element: object


@dataclass
class ComparisonItem:
    original: TextUnit
    translation: TextUnit
    translation_text: str
    suggestion: str = ''
    notes: str = ''
    severity: str = ''
    state: str = 'pending'
    structural_alignment: str = 'exact'
    ai_alignment: str = ''
    manual_override: bool = False
    original_translation_text: str = ''
    removed_duplicate_text: str = ''
    repair_confidence: float = 0.0


@dataclass
class ReviewChunk:
    items: list

    @property
    def original_chars(self):
        return sum(len(item.original.text) for item in self.items)

    @property
    def translation_chars(self):
        return sum(len(item.translation_text) for item in self.items)

    @property
    def preview(self):
        text = self.items[0].original.text if self.items else ''
        return _clean_text(text)[:140]


class EpubError(Exception):
    pass


def build_review_chunks(items, chunk_chars=8000):
    """Group consecutive aligned paragraphs into API-sized review chunks."""
    limit = max(1000, int(chunk_chars))
    chunks = []
    current = []
    source_chars = 0
    target_chars = 0
    current_is_safe = None

    def flush():
        nonlocal current, source_chars, target_chars, current_is_safe
        if current:
            chunks.append(ReviewChunk(current))
        current = []
        source_chars = 0
        target_chars = 0
        current_is_safe = None

    for item in items:
        item_source_chars = len(item.original.text)
        item_target_chars = len(item.translation_text)
        item_is_safe = item.structural_alignment in {
            'exact', 'repaired-duplicate-prefix',
        }
        exceeds_limit = current and (
            source_chars + item_source_chars > limit or
            target_chars + item_target_chars > limit
        )
        changes_risk_class = (
            current and item_is_safe != current_is_safe
        )
        if exceeds_limit or changes_risk_class:
            flush()
        current.append(item)
        source_chars += item_source_chars
        target_chars += item_target_chars
        current_is_safe = item_is_safe
    flush()
    return chunks


class EpubDocument:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.title = os.path.splitext(os.path.basename(self.path))[0]
        self.series = ''
        self.authors = []
        self.tags = []
        self.languages = []
        self.archive = None
        self.infos = []
        self.raw = {}
        self.trees = {}
        self.spine_paths = []
        self.package_path = ''
        self.package_tree = None
        self.units_by_chapter = []
        self.units = []
        self.modified_paths = set()
        self._load()

    def _load(self):
        if not os.path.isfile(self.path):
            raise EpubError('File not found: %s' % self.path)
        if not zipfile.is_zipfile(self.path):
            raise EpubError('The file is not a valid EPUB/ZIP archive: %s' % self.path)
        with zipfile.ZipFile(self.path, 'r') as archive:
            self.infos = archive.infolist()
            self.raw = {info.filename: archive.read(info.filename) for info in self.infos}

        container = self.raw.get('META-INF/container.xml')
        if not container:
            raise EpubError('The EPUB does not contain META-INF/container.xml.')
        try:
            root = etree.fromstring(container)
            roots = root.xpath('//*[local-name()="rootfile"]/@full-path')
            opf_path = roots[0]
            opf = etree.fromstring(self.raw[opf_path])
        except Exception as err:
            raise EpubError('Could not read the EPUB structure: %s' % err)
        self.package_path = opf_path
        self.package_tree = opf
        self.trees[opf_path] = opf

        titles = opf.xpath(
            '//*[local-name()="metadata"]/*[local-name()="title"]/text()'
        )
        if titles and _clean_text(titles[0]):
            self.title = _clean_text(titles[0])
        self.authors = [
            _clean_text(value) for value in opf.xpath(
                '//*[local-name()="metadata"]/*[local-name()="creator"]/text()'
            ) if _clean_text(value)
        ]
        self.tags = [
            _clean_text(value) for value in opf.xpath(
                '//*[local-name()="metadata"]/*[local-name()="subject"]/text()'
            ) if _clean_text(value)
        ]
        self.languages = [
            _clean_text(value) for value in opf.xpath(
                '//*[local-name()="metadata"]/*[local-name()="language"]/text()'
            ) if _clean_text(value)
        ]
        calibre_series = opf.xpath(
            '//*[local-name()="metadata"]/*[local-name()="meta" and '
            '@name="calibre:series"]/@content'
        )
        epub_series = opf.xpath(
            '//*[local-name()="metadata"]/*[local-name()="meta" and '
            '@property="belongs-to-collection"]/text()'
        )
        series_values = calibre_series or epub_series
        if series_values:
            self.series = _clean_text(series_values[0])

        base = posixpath.dirname(opf_path)
        manifest = {}
        for item in opf.xpath('//*[local-name()="manifest"]/*[local-name()="item"]'):
            item_id = item.get('id')
            href = unquote((item.get('href') or '').split('#', 1)[0])
            media_type = item.get('media-type', '')
            if item_id and href:
                manifest[item_id] = (
                    posixpath.normpath(posixpath.join(base, href)), media_type
                )
        for ref in opf.xpath('//*[local-name()="spine"]/*[local-name()="itemref"]'):
            entry = manifest.get(ref.get('idref'))
            if entry and ('html' in entry[1] or entry[0].lower().endswith(('.xhtml', '.html', '.htm'))):
                self.spine_paths.append(entry[0])
        if not self.spine_paths:
            self.spine_paths = [
                path for path, media_type in manifest.values()
                if 'html' in media_type or path.lower().endswith(('.xhtml', '.html', '.htm'))
            ]
        if not self.spine_paths:
            raise EpubError('No XHTML/HTML chapters were found in the EPUB.')

        parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
        for chapter_index, path in enumerate(self.spine_paths):
            data = self.raw.get(path)
            if data is None:
                continue
            try:
                tree = etree.fromstring(data, parser=parser)
            except Exception:
                continue
            self.trees[path] = tree
            chapter_units = self._extract_units(tree, chapter_index, path)
            self.units_by_chapter.append(chapter_units)
            self.units.extend(chapter_units)

    def _extract_units(self, tree, chapter_index, path):
        units = []
        candidates = tree.xpath('//*[local-name()="body"]//*')
        for element in candidates:
            if _local_name(element) not in BLOCK_TAGS:
                continue
            if _hidden_in_tree(element):
                continue
            if any(_local_name(child) in BLOCK_TAGS for child in element.iterdescendants()):
                continue
            if any(
                _local_name(child) in PRESERVE_EXACT_TAGS
                for child in element.iterdescendants()
            ):
                continue
            text = _clean_text(_visible_text(element))
            if not text:
                continue
            units.append(TextUnit(
                chapter_index=chapter_index,
                block_index=len(units),
                chapter_name=posixpath.basename(path),
                document_path=path,
                text=text,
                element=element,
            ))
        return units

    def apply_texts(self, items):
        for item in items:
            if _clean_text(item.translation_text) == item.translation.text:
                continue
            if item.structural_alignment in {
                'translation-overflow', 'repaired-duplicate-prefix',
            }:
                _replace_corrupt_block_text(
                    item.translation.element, item.translation_text
                )
            else:
                _replace_text_preserving_inline_markup(
                    item.translation.element, item.translation_text
                )
            item.translation.text = _clean_text(item.translation_text)
            self.modified_paths.add(item.translation.document_path)

    def set_output_metadata(self, title, tag='proofreaded', tags=None):
        """Set distinct output metadata without disturbing the EPUB package."""
        title = _clean_text(title)
        output_tags = []
        for value in ([tag] if tags is None else tags):
            value = _clean_text(value)
            if value and value.casefold() not in {
                existing.casefold() for existing in output_tags
            }:
                output_tags.append(value)
        if self.package_tree is None or not self.package_path:
            raise EpubError('The EPUB package metadata is unavailable.')
        # Numbered proofreading tags represent one cumulative counter, not a
        # history list.  Replacing 2pr with 3pr avoids ambiguous Calibre rows
        # such as "2pr, 3pr" after another pass while retaining ordinary book
        # tags and the cumulative proofreaded/polished markers.
        numbered_pass = next((
            value for value in output_tags
            if re.fullmatch(r'\d+pr', value, flags=re.I)
        ), '')
        if numbered_pass:
            for node in list(self.package_tree.xpath(
                '//*[local-name()="metadata"]/*[local-name()="subject"]'
            )):
                if re.fullmatch(r'\d+pr', _clean_text(node.text), flags=re.I):
                    parent = node.getparent()
                    if parent is not None:
                        parent.remove(node)
            self.tags = [
                value for value in self.tags
                if not re.fullmatch(r'\d+pr', _clean_text(value), flags=re.I)
            ]
        titles = self.package_tree.xpath(
            '//*[local-name()="metadata"]/*[local-name()="title"]'
        )
        if title:
            if titles:
                titles[0].text = title
            else:
                metadata_nodes = self.package_tree.xpath(
                    '//*[local-name()="metadata"]'
                )
                if not metadata_nodes:
                    raise EpubError('The EPUB package has no metadata element.')
                etree.SubElement(
                    metadata_nodes[0],
                    '{http://purl.org/dc/elements/1.1/}title',
                ).text = title
            self.title = title
            for node in self.package_tree.xpath(
                '//*[local-name()="metadata"]/*[local-name()="meta" and '
                '@name="calibre:title_sort"]'
            ):
                node.set('content', title)
        for tag in output_tags:
            subjects = self.package_tree.xpath(
                '//*[local-name()="metadata"]/*[local-name()="subject"]'
            )
            if tag.casefold() not in {
                _clean_text(node.text).casefold() for node in subjects
            }:
                metadata_nodes = self.package_tree.xpath(
                    '//*[local-name()="metadata"]'
                )
                if metadata_nodes:
                    etree.SubElement(
                        metadata_nodes[0],
                        '{http://purl.org/dc/elements/1.1/}subject',
                    ).text = tag
            if tag.casefold() not in {value.casefold() for value in self.tags}:
                self.tags.append(tag)
        self.modified_paths.add(self.package_path)

    def save_as(self, output_path):
        output_path = os.path.abspath(output_path)
        if os.path.normcase(output_path) == os.path.normcase(self.path):
            raise EpubError('Save the result as a new file instead of overwriting the input.')
        changed = {}
        for path in self.modified_paths:
            tree = self.trees[path]
            original = self.raw.get(path, b'')
            has_decl = original.lstrip().startswith(b'<?xml')
            changed[path] = etree.tostring(
                tree,
                encoding='utf-8',
                xml_declaration=has_decl,
                doctype=tree.getroottree().docinfo.doctype or None,
            )

        info_map = {info.filename: info for info in self.infos}
        with zipfile.ZipFile(output_path, 'w') as output:
            mimetype = info_map.get('mimetype')
            if mimetype is not None:
                mime_info = copy.copy(mimetype)
                mime_info.compress_type = zipfile.ZIP_STORED
                output.writestr(mime_info, self.raw['mimetype'])
            for info in self.infos:
                if info.filename == 'mimetype':
                    continue
                data = changed.get(info.filename, self.raw[info.filename])
                output.writestr(copy.copy(info), data)
        validate_epub(
            output_path,
            expected_spine=self.spine_paths,
            strict_paths=self.modified_paths,
        )
        return output_path


def validate_epub(path, expected_spine=None, strict_paths=None):
    """Validate the container, package spine, and edited XHTML after writing."""
    try:
        with zipfile.ZipFile(path, 'r') as archive:
            bad_file = archive.testzip()
            if bad_file:
                raise EpubError('The saved EPUB has a corrupt ZIP entry: %s' % bad_file)
            infos = archive.infolist()
            names = {info.filename for info in infos}
            if not infos or infos[0].filename != 'mimetype':
                raise EpubError('The saved EPUB does not start with the mimetype entry.')
            if infos[0].compress_type != zipfile.ZIP_STORED:
                raise EpubError('The saved EPUB mimetype entry is compressed.')
            if archive.read('mimetype').strip() != b'application/epub+zip':
                raise EpubError('The saved EPUB has an invalid mimetype value.')
            container = etree.fromstring(archive.read('META-INF/container.xml'))
            roots = container.xpath('//*[local-name()="rootfile"]/@full-path')
            if not roots or roots[0] not in names:
                raise EpubError('The saved EPUB package document is missing.')
            opf_path = roots[0]
            opf = etree.fromstring(archive.read(opf_path))
            base = posixpath.dirname(opf_path)
            manifest = {}
            for item in opf.xpath('//*[local-name()="manifest"]/*[local-name()="item"]'):
                item_id = item.get('id')
                href = unquote((item.get('href') or '').split('#', 1)[0])
                if item_id and href:
                    manifest[item_id] = (
                        posixpath.normpath(posixpath.join(base, href)),
                        item.get('media-type', ''),
                    )
            spine = []
            for ref in opf.xpath('//*[local-name()="spine"]/*[local-name()="itemref"]'):
                manifest_entry = manifest.get(ref.get('idref'))
                path_in_book = manifest_entry[0] if manifest_entry else ''
                if not manifest_entry or path_in_book not in names:
                    raise EpubError(
                        'The saved EPUB spine references a missing manifest item.'
                    )
                spine.append(manifest_entry)
            spine_paths = [entry[0] for entry in spine]
            if len(spine_paths) != len(set(spine_paths)):
                raise EpubError('The saved EPUB contains duplicate spine entries.')
            html_spine = [
                path_in_book for path_in_book, media_type in spine
                if 'html' in media_type or
                path_in_book.lower().endswith(('.xhtml', '.html', '.htm'))
            ]
            if expected_spine is not None and list(expected_spine) != html_spine:
                raise EpubError('The saved EPUB spine order changed unexpectedly.')
            parser = etree.XMLParser(recover=False, resolve_entities=False, no_network=True)
            for chapter in set(strict_paths or ()):
                if chapter in names:
                    etree.fromstring(archive.read(chapter), parser=parser)
    except EpubError:
        raise
    except Exception as err:
        raise EpubError('The saved EPUB failed structural validation: %s' % err)
    return True


def _text_slots(element):
    slots = []

    def walk(node):
        if node is not element and _explicitly_hidden(node):
            return
        if node.text and node.text.strip():
            slots.append((node, 'text', node.text))
        for child in node:
            walk(child)
            if child.tail and child.tail.strip():
                slots.append((child, 'tail', child.tail))

    walk(element)
    return slots


def _with_whitespace(old_value, new_value):
    leading = re.match(r'^\s*', old_value or '').group(0)
    trailing = re.search(r'\s*$', old_value or '').group(0)
    return leading + new_value + trailing


def _replace_text_preserving_inline_markup(element, revised):
    revised = _clean_text(revised)
    slots = _text_slots(element)
    if not slots:
        element.text = revised
        return
    if not revised:
        # An explicit blank manual edit means "remove this visible block".
        # Keep the inline element structure/attributes, but clear every visible
        # text slot. The old early return silently restored corrupt text during
        # EPUB output.
        for owner, attr, old in slots:
            setattr(owner, attr, _with_whitespace(old, ''))
        return
    if len(slots) == 1:
        owner, attr, old = slots[0]
        setattr(owner, attr, _with_whitespace(old, revised))
        return

    words = revised.split()
    weights = [max(1, len(_clean_text(old).split())) for _, _, old in slots]
    total_weight = sum(weights)
    consumed = 0
    for index, (owner, attr, old) in enumerate(slots):
        if index == len(slots) - 1:
            count = len(words) - consumed
        else:
            target = round(len(words) * sum(weights[:index + 1]) / total_weight)
            count = max(0, target - consumed)
        piece = ' '.join(words[consumed:consumed + count])
        consumed += count
        setattr(owner, attr, _with_whitespace(old, piece))


def _replace_corrupt_block_text(element, revised):
    """Replace a locally confirmed merged/overflow block without retaining its breaks."""
    revised = _clean_text(revised)
    for child in list(element):
        element.remove(child)
    element.text = revised


def _break_fragments(element):
    pieces = [element.text or '']
    for child in element:
        if _local_name(child) == 'br':
            pieces.append('\n')
        else:
            pieces.append(_visible_text(child))
        if child.tail:
            pieces.append(child.tail)
    raw = ''.join(pieces)
    return [
        _clean_text(fragment)
        for fragment in re.split(r'\n\s*\n+', raw)
        if _clean_text(fragment)
    ]


def _merged_prefix_repair(item, source_chapter, target_chapter):
    fragments = _break_fragments(item.translation.element)
    if len(fragments) < 5:
        return None
    prefix = fragments[:-1]
    suffix = fragments[-1]
    if sum(map(len, prefix)) < 1000:
        return None
    block_index = item.original.block_index
    chapter_ratios = [
        len(target.text) / max(20, len(source.text))
        for source, target in zip(source_chapter, target_chapter)
        if 20 <= len(source.text) and 10 <= len(target.text) < 1500
    ]
    expected_ratio = median(chapter_ratios) if chapter_ratios else 1.0
    suffix_ratio = (
        len(suffix) / max(20, len(item.original.text)) /
        max(0.1, expected_ratio)
    )

    compared = min(len(fragments), block_index + 1, 20)
    if compared >= 5:
        source_window = source_chapter[
            block_index - compared + 1:block_index + 1
        ]
        target_window = fragments[-compared:]
        ratios = [
            len(target) / max(20, len(source.text))
            for target, source in zip(target_window, source_window)
        ]
        normalized = [ratio / max(0.1, expected_ratio) for ratio in ratios]
        plausible = sum(
            0.35 <= ratio <= 2.5 for ratio in normalized
        ) / compared
        tight = sum(0.55 <= ratio <= 1.8 for ratio in normalized) / compared
        typical = median(normalized)
        if (
            plausible >= 0.80 and tight >= 0.60 and
            0.60 <= typical <= 1.55 and 0.45 <= suffix_ratio <= 2.0
        ):
            confidence = min(
                0.99,
                0.45 * plausible + 0.35 * tight +
                0.20 * (1.0 - min(1.0, abs(1.0 - typical))),
            )
            return {
                'kept': suffix,
                'removed': '\n\n'.join(prefix),
                'fragments': len(fragments),
                'compared': compared,
                'confidence': confidence,
            }

    # A corrupt merged block near the start of a chapter has too few preceding
    # pairs for the backward check above. If both chapter sides still have the
    # same block count, validate the stable alignment using the following
    # ordinary pairs instead. This catches a giant pasted prefix followed by
    # the correct current translation without guessing across languages.
    forward_ratios = []
    if len(source_chapter) == len(target_chapter):
        end = min(len(source_chapter), block_index + 7)
        for index in range(block_index + 1, end):
            source_text = source_chapter[index].text
            target_text = target_chapter[index].text
            if 20 <= len(source_text) and 10 <= len(target_text) < 1500:
                forward_ratios.append(
                    len(target_text) / max(20, len(source_text)) /
                    max(0.1, expected_ratio)
                )
    forward_count = len(forward_ratios)
    if forward_count >= 5:
        plausible = sum(
            0.35 <= ratio <= 2.5 for ratio in forward_ratios
        ) / forward_count
        tight = sum(
            0.55 <= ratio <= 1.8 for ratio in forward_ratios
        ) / forward_count
        typical = median(forward_ratios)
        if (
            plausible >= 0.90 and tight >= 0.80 and
            0.70 <= typical <= 1.35 and 0.45 <= suffix_ratio <= 2.0
        ):
            confidence = min(
                0.98,
                0.45 * plausible + 0.35 * tight +
                0.20 * (1.0 - min(1.0, abs(1.0 - typical))),
            )
            return {
                'kept': suffix,
                'removed': '\n\n'.join(prefix),
                'fragments': len(fragments),
                'compared': forward_count,
                'confidence': confidence,
            }
    return None


def align_documents(original, translation, auto_cleanup_merged=True):
    items = []
    chapter_records = []
    chapter_count = max(
        len(original.units_by_chapter), len(translation.units_by_chapter)
    )
    warnings = []
    for chapter_index in range(chapter_count):
        source = (
            original.units_by_chapter[chapter_index]
            if chapter_index < len(original.units_by_chapter) else []
        )
        target = (
            translation.units_by_chapter[chapter_index]
            if chapter_index < len(translation.units_by_chapter) else []
        )
        if not source or not target:
            if source or target:
                warnings.append('Chapter %d has no counterpart.' % (chapter_index + 1))
            continue
        positional_alignment = len(source) != len(target)
        if positional_alignment:
            warnings.append(
                'Chapter %d: %d paragraphs in the original and %d in the '
                'translation; positional alignment was used.' % (
                    chapter_index + 1, len(source), len(target)
                )
            )
        chapter_items = []
        for target_index, target_unit in enumerate(target):
            if len(target) == 1:
                source_index = 0
            else:
                source_index = round(
                    target_index * (len(source) - 1) / (len(target) - 1)
                )
            source_unit = source[min(source_index, len(source) - 1)]
            item = ComparisonItem(
                original=source_unit,
                translation=target_unit,
                translation_text=target_unit.text,
                structural_alignment=(
                    'positional' if positional_alignment else 'exact'
                ),
            )
            items.append(item)
            chapter_items.append(item)
        chapter_records.append((chapter_index, source, target, chapter_items))
    if not items:
        raise EpubError('Could not align text paragraphs from the two EPUB files.')
    _mark_preflight_anomalies(
        chapter_records, warnings,
        auto_cleanup_merged=auto_cleanup_merged,
    )
    return items, warnings


def _unit_words(units):
    text = ' '.join(unit.text for unit in units).casefold()
    return re.findall(r"[\w’'-]+", text, flags=re.UNICODE)


def _translation_words(items):
    text = ' '.join(item.translation_text for item in items).casefold()
    return re.findall(r"[\w’'-]+", text, flags=re.UNICODE)


def _mark_items(items, alignment):
    priority = {
        'exact': 0,
        'positional': 1,
        'length-outlier': 2,
        'duplicate': 3,
        'repaired-duplicate-prefix': 4,
        'translation-overflow': 5,
    }
    for item in items:
        if priority.get(alignment, 0) > priority.get(
            item.structural_alignment, 0
        ):
            item.structural_alignment = alignment


def _mark_preflight_anomalies(
    chapter_records, warnings, auto_cleanup_merged=True
):
    """Detect likely corruption locally before any passage is sent to an API."""
    for chapter_index, _source, _target, chapter_items in chapter_records:
        for item in chapter_items:
            source_length = len(item.original.text)
            target_length = len(item.translation_text)
            translation_element = item.translation.element
            break_count = sum(
                _local_name(child) == 'br'
                for child in translation_element.iterdescendants()
            ) if translation_element is not None else 0
            extreme_ratio = target_length / max(20, source_length)
            if target_length >= 1500 and (
                extreme_ratio >= 6.0 or
                (break_count >= 10 and target_length >= source_length + 1000)
            ):
                repair = _merged_prefix_repair(item, _source, _target)
                if repair and auto_cleanup_merged:
                    item.original_translation_text = item.translation_text
                    item.removed_duplicate_text = repair['removed']
                    item.translation_text = repair['kept']
                    item.repair_confidence = repair['confidence']
                    item.structural_alignment = 'repaired-duplicate-prefix'
                    item.notes = (
                        'Removed a high-confidence merged duplicate prefix '
                        'locally: removed %d characters across %d leading '
                        'fragments and kept the final %d-character fragment '
                        '(confidence %.0f%%). The original EPUB remains '
                        'unchanged.' % (
                            len(repair['removed']),
                            repair['fragments'] - 1,
                            len(repair['kept']),
                            repair['confidence'] * 100,
                        )
                    )
                else:
                    item.structural_alignment = 'translation-overflow'
                    warnings.append(
                        'Chapter %d (%s), block %d: the translation has %d '
                        'characters and %d line breaks for a %d-character source '
                        'block. It is probably merged or unrelated text and has '
                        'no reliable source counterpart.' % (
                            chapter_index + 1,
                            item.translation.document_path,
                            item.translation.block_index + 1,
                            target_length,
                            break_count,
                            source_length,
                        )
                    )
    substantive = []
    ratios = []
    for chapter_index, source, target, chapter_items in chapter_records:
        source_words = _unit_words(source)
        # Automatic merged-prefix repair changes the effective translation
        # before chunking. Preflight must inspect that cleaned text rather than
        # the original malformed 9k-character HTML block, otherwise the whole
        # chapter remains falsely risky and a tiny repaired item becomes its
        # own API request.
        target_words = _translation_words(chapter_items)
        if len(source_words) < 250 or len(target_words) < 250:
            continue
        ratio = len(target_words) / max(1, len(source_words))
        substantive.append((
            chapter_index, source_words, target_words, chapter_items, ratio,
            target[0].document_path,
        ))
        ratios.append(ratio)

    if len(ratios) >= 5:
        typical = median(ratios)
        for (
            chapter_index, _source_words, _target_words, chapter_items,
            ratio, path,
        ) in substantive:
            relative = ratio / max(0.01, typical)
            if relative > 1.45 or relative < 0.55:
                _mark_items(chapter_items, 'length-outlier')
                warnings.append(
                    'Chapter %d (%s): unusual translation length — %.2f× '
                    'the typical ratio to the original.' % (
                        chapter_index + 1, path, relative
                    )
                )

    previous = None
    for record in substantive:
        (
            chapter_index, _source_words, target_words, chapter_items,
            _ratio, path,
        ) = record
        if previous is not None:
            previous_words = previous[2]
            match = SequenceMatcher(
                None, previous_words, target_words, autojunk=False
            ).find_longest_match()
            shorter = min(len(previous_words), len(target_words))
            if match.size >= 80 and match.size >= int(shorter * 0.04):
                _mark_items(chapter_items, 'duplicate')
                warnings.append(
                    'Chapter %d (%s): possible duplicate — %d consecutive words '
                    'also occur in the preceding substantive chapter.' % (
                        chapter_index + 1, path, match.size
                    )
                )
        previous = record
