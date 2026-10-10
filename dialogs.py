import os
import re
import time
from difflib import SequenceMatcher

from calibre.utils.localization import canonicalize_lang, lang_as_iso639_1
from qt.core import (
    Qt, QObject, QThread, pyqtSignal, pyqtSlot, QApplication, QBrush, QColor,
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QTextCursor, QVBoxLayout, QWidget,
)

from .api import (
    APIError, ContentBlockedError, ProofreaderClient, ResponseFormatError,
)
from .config import (
    DEFAULTS, DEFAULT_PROMPT, delete_ai_profile, get_ai_profiles,
    get_settings, remember_book_pair, save_ai_profile, save_settings,
)
from .epub import EpubDocument, EpubError, align_documents, build_review_chunks
from .language_scan import ukrainian_dictionary_available
from .localization import LANGUAGES, localize_widget_tree, tr
from .terminology import (
    BookTerminology, MemoryProfileLibrary, _source_occurs,
)


OUTPUT_STAGE_LABELS = {
    'proofreaded': 'Proofreaded',
    'polished': 'Polished',
}


def _metadata_stage(title, tags=()):
    normalized_tags = {
        str(value or '').strip().casefold() for value in (tags or ())
    }
    if 'polished' in normalized_tags or re.search(
        r'\[Polished\]\s*$', str(title or ''), flags=re.I
    ):
        return 'polished'
    if 'proofreaded' in normalized_tags or re.search(
        r'\[Proofreaded\]\s*$', str(title or ''), flags=re.I
    ):
        return 'proofreaded'
    return ''


def _proofread_pass(title='', tags=()):
    """Return the highest completed proofreading pass encoded in metadata."""
    result = 0
    normalized_tags = [
        str(value or '').strip().casefold() for value in (tags or ())
    ]
    if _metadata_stage(title, tags):
        result = 1
    for value in normalized_tags:
        match = re.fullmatch(r'(\d+)pr', value)
        if match:
            result = max(result, int(match.group(1)))
    return result


def _target_language_code(settings, document=None):
    configured = str(settings.get('target_language', '') or '')
    detected = (
        list(getattr(document, 'languages', ()) or ())
        if document is not None else []
    )
    # Prefer the visible configured language whenever it is available. Many
    # translated EPUBs incorrectly retain the source language in dc:language;
    # trusting that stale value would label a Ukrainian output as EN_. Fresh
    # installations leave this field blank, so genuine auto-detection still
    # falls back to the EPUB metadata.
    candidates = [configured] + detected
    for candidate in candidates:
        canonical = canonicalize_lang(candidate)
        code = lang_as_iso639_1(canonical) if canonical else None
        if code:
            # The requested visible Ukrainian marker is UA rather than the ISO
            # 639-1 code UK; all other languages use their normal two-letter code.
            return ('UA' if code.casefold() == 'uk' else code.upper())
    return ''


def _output_title(title, stage='proofreaded', language_code=''):
    title = re.sub(r'\s+', ' ', str(title or '')).strip() or 'Proofread book'
    while re.search(r'\s*\[(?:Proofreaded|Polished)\]\s*$', title, flags=re.I):
        title = re.sub(
            r'\s*\[(?:Proofreaded|Polished)\]\s*$', '', title, flags=re.I
        ).strip()
    title = re.sub(r'^(?:[A-Z]{2}_)+', '', title, flags=re.I).strip()
    prefix = '%s_' % language_code.upper() if language_code else ''
    label = OUTPUT_STAGE_LABELS.get(stage, OUTPUT_STAGE_LABELS['proofreaded'])
    return '%s%s [%s]' % (prefix, title, label)


def _proofread_title(title):
    """Compatibility helper retained for integrations using the old function."""
    return _output_title(title, 'proofreaded')


def _output_filename(path, stage, language_code=''):
    directory = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r'_(?:proofread|proofreaded|polished)$', '', stem, flags=re.I)
    stem = re.sub(r'^(?:[A-Z]{2}_)+', '', stem, flags=re.I).strip()
    prefix = '%s_' % language_code.upper() if language_code else ''
    suffix = '_polished' if stage == 'polished' else '_proofread'
    return os.path.join(directory, prefix + stem + suffix + '.epub')


PROVIDER_ENDPOINTS = {
    'gemini': DEFAULTS['endpoint'],
    'deepseek': 'https://api.deepseek.com/chat/completions',
    'openai': 'https://api.openai.com/v1/chat/completions',
    'local': 'http://127.0.0.1:11434/v1/chat/completions',
}

EDIT_WORD_RE = re.compile(
    r"[^\W\d_]+(?:[’'ʼ-][^\W\d_]+)*", flags=re.UNICODE
)


def _manual_word_replacements(before, after):
    """Return compact word-level replacements made in a manual edit."""
    before_words = EDIT_WORD_RE.findall(str(before or ''))
    after_words = EDIT_WORD_RE.findall(str(after or ''))
    matcher = SequenceMatcher(
        None,
        [word.casefold() for word in before_words],
        [word.casefold() for word in after_words],
        autojunk=False,
    )
    replacements = []
    for operation, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if operation != 'replace':
            continue
        old_words = before_words[old_start:old_end]
        new_words = after_words[new_start:new_end]
        if not old_words or not new_words:
            continue
        if len(old_words) > 5 or len(new_words) > 5:
            continue
        replacements.append((' '.join(old_words), ' '.join(new_words)))
    return replacements


def _message(parent, icon, title, text, details=''):
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    if details:
        box.setDetailedText(details)
    box.exec()


def _dominant_script(document):
    """Return latin/cyrillic only for a very strong book-wide signal."""
    latin = 0
    cyrillic = 0
    for unit in document.units:
        for character in str(unit.text or ''):
            folded = character.casefold()
            if 'a' <= folded <= 'z':
                latin += 1
            elif '\u0400' <= character <= '\u052f':
                cyrillic += 1
    if latin >= 5000 and latin > cyrillic * 8:
        return 'latin'
    if cyrillic >= 5000 and cyrillic > latin * 8:
        return 'cyrillic'
    return 'mixed'


class NumberedNotesEdit(QPlainTextEdit):
    """Read-only notes pane that emits the clicked [paragraph] number."""
    paragraphClicked = pyqtSignal(int)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        cursor = self.cursorForPosition(event.position().toPoint())
        block = cursor.block()
        for _ in range(30):
            if not block.isValid():
                break
            match = re.match(r'^\s*\[(\d+)\]', block.text())
            if match:
                self.paragraphClicked.emit(int(match.group(1)) - 1)
                break
            block = block.previous()


class BookPicker(QWidget):
    def __init__(self, label, library_entries, initial_path='', parent=None):
        super().__init__(parent)
        self.label = label
        self.library_entries = list(library_entries)
        self.path_edit = QLineEdit(self)
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText('Choose an EPUB from the library or disk')
        library_button = QPushButton('Library…', self)
        file_button = QPushButton('File…', self)
        library_button.clicked.connect(self.choose_library)
        file_button.clicked.connect(self.choose_file)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.path_edit, 1)
        layout.addWidget(library_button)
        layout.addWidget(file_button)
        if initial_path:
            self.path_edit.setText(initial_path)

    @property
    def path(self):
        return self.path_edit.text().strip()

    def choose_library(self):
        if not self.library_entries:
            _message(
                self, QMessageBox.Icon.Information, 'No EPUB books',
                'No EPUB books were found in the current Calibre library.'
            )
            return
        labels = [entry[0] for entry in self.library_entries]
        chosen, ok = QInputDialog.getItem(
            self, self.label, 'Book:', labels, 0, False
        )
        if ok:
            self.path_edit.setText(
                self.library_entries[labels.index(chosen)][1]
            )

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, self.label, '', 'EPUB (*.epub)'
        )
        if path:
            self.path_edit.setText(path)


class TextPromptDialog(QDialog):
    def __init__(self, title, text='', description='', parent=None, reset_text=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(820, 620)
        self.editor = QPlainTextEdit(str(text or ''), self)
        self.editor.setMinimumHeight(440)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        if reset_text is not None:
            reset_button = QPushButton('Reset to default', self)
            reset_button.clicked.connect(
                lambda: self.editor.setPlainText(reset_text)
            )
            bottom.addWidget(reset_button)
        bottom.addStretch(1)
        bottom.addWidget(buttons)
        layout = QVBoxLayout(self)
        if description:
            label = QLabel(description, self)
            label.setWordWrap(True)
            layout.addWidget(label)
        layout.addWidget(self.editor, 1)
        layout.addLayout(bottom)

    @property
    def text(self):
        return self.editor.toPlainText()


class AutoPromptDialog(QDialog):
    def __init__(self, terminology, parent=None):
        super().__init__(parent)
        self.terminology = terminology
        self.setWindowTitle('Active book memory and notes')
        self.setMinimumSize(900, 700)
        explanation = QLabel(
            'The first field previews active AUTO/USER/CONFIRMED terminology and '
            'is read-only. Only terms present in a source chunk are sent. AI '
            'voting and alternatives are not used. The second field contains '
            'optional editable book-wide notes.',
            self,
        )
        explanation.setWordWrap(True)
        self.preview = QPlainTextEdit(terminology.memory_summary(), self)
        self.preview.setReadOnly(True)
        self.notes = QPlainTextEdit(terminology.auto_prompt, self)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(explanation)
        layout.addWidget(QLabel('Active terminology sent to the AI:', self))
        layout.addWidget(self.preview, 2)
        layout.addWidget(QLabel('Additional book notes:', self))
        layout.addWidget(self.notes, 1)
        layout.addWidget(buttons)
        localize_widget_tree(
            self, get_settings().get('interface_language', 'auto')
        )

    def accept(self):
        self.terminology.auto_prompt = self.notes.toPlainText()
        self.terminology.save()
        self.terminology.sync_active_profile_prompts()
        super().accept()


class TerminologyDialog(QDialog):
    NEW_ROLE = int(Qt.ItemDataRole.UserRole) + 10
    PROFILE_ROLE = int(Qt.ItemDataRole.UserRole) + 11
    STATUS_ROLE = int(Qt.ItemDataRole.UserRole) + 12
    COUNT_ROLE = int(Qt.ItemDataRole.UserRole) + 13
    PROPER_NAME_ROLE = int(Qt.ItemDataRole.UserRole) + 14

    def __init__(
        self, terminology, scan_callback, parent=None,
        ai_callback=None, deep_ai_callback=None,
    ):
        super().__init__(parent)
        self.terminology = terminology
        self.scan_callback = scan_callback
        self.ai_callback = ai_callback
        self.deep_ai_callback = deep_ai_callback
        self.setWindowTitle('Book terminology')
        self.setMinimumSize(1080, 720)

        self.minimum = QSpinBox(self)
        self.minimum.setRange(1, 100)
        self.minimum.setValue(terminology.minimum_occurrences)
        settings = QHBoxLayout()
        settings.addWidget(QLabel('Minimum occurrences:', self))
        settings.addWidget(self.minimum)
        settings.addStretch(1)

        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText(
            'Search source, canonical form, AI proposal, gender note, or status…'
        )
        self.search_edit.setClearButtonEnabled(True)
        self.search_count = QLabel('All terms', self)
        self.term_filter = QComboBox(self)
        self.term_filter.addItem('All terms', 'all')
        self.term_filter.addItem('New in this book', 'new')
        self.term_filter.addItem('Present in source EPUB', 'present')
        self.term_filter.addItem('From series profile', 'profile')
        self.term_filter.addItem('Unresolved — no proposal', 'unresolved')
        self.term_filter.addItem('AI suggestions', 'ai_suggestion')
        self.term_filter.addItem('Automatic (active)', 'auto')
        self.term_filter.addItem('User edits', 'user')
        self.term_filter.addItem('Confirmed', 'confirmed')
        self.term_filter.addItem('Locked: user + confirmed', 'locked')
        self.term_filter.addItem('Proper names', 'names')
        self.term_filter.addItem('Double translation', 'ai_diff')
        self.term_filter.setToolTip(
            'Present means detected in the EPUB selected as Original, not in '
            'the translated EPUB.'
        )
        previous_match = QPushButton('Previous', self)
        next_match = QPushButton('Next', self)
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel('Search:', self))
        search_layout.addWidget(self.search_edit, 1)
        search_layout.addWidget(QLabel('Show:', self))
        search_layout.addWidget(self.term_filter)
        search_layout.addWidget(self.search_count)
        search_layout.addWidget(previous_match)
        search_layout.addWidget(next_match)

        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels([
            'Source', 'Canonical target form', 'AI suggestion',
            'Gender / grammar note', 'Proper name', 'Occurrences', 'Status',
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.search_edit.textChanged.connect(self.filter_terms)
        self.term_filter.currentIndexChanged.connect(self.filter_terms)
        self.search_edit.returnPressed.connect(self.find_next_term)
        previous_match.clicked.connect(
            lambda: self.find_next_term(-1)
        )
        next_match.clicked.connect(lambda: self.find_next_term(1))

        scan_button = QPushButton('Scan book terms', self)
        scan_button.setToolTip(
            'Run the local no-cost scanner again using Minimum occurrences. '
            'Apply the correct series profile first so old profile terms are '
            'not mistaken for new book discoveries.'
        )
        scan_button.setEnabled(self.scan_callback is not None)
        if self.scan_callback is None:
            scan_button.setToolTip(
                'A shared profile has no source EPUB. Scan after applying it '
                'to a loaded book.'
            )
        apply_button = QPushButton('Apply edited mappings / notes', self)
        apply_button.setToolTip(
            'Save direct edits from Canonical target form, gender notes, and '
            'Proper name. A human canonical always outranks the AI suggestion.'
        )
        confirm_button = QPushButton('Confirm selected', self)
        confirm_button.setToolTip(
            'Lock the selected canonical as a human-confirmed decision. If the '
            'canonical is blank, promote the AI suggestion first.'
        )
        reset_button = QPushButton('Reset selected', self)
        reset_button.setToolTip(
            'Remove the human canonical/gender/name lock. A retained AI '
            'suggestion becomes the lower-priority AUTO fallback.'
        )
        delete_button = QPushButton('Delete selected', self)
        delete_button.setToolTip(
            'Permanently exclude selected rows from this book and, when '
            'synchronization is active, from the attached series profile. A '
            'later scan or stale book copy will not silently restore them.'
        )
        reviewed_button = QPushButton('Mark selected reviewed', self)
        reviewed_button.setToolTip(
            'Remove the amber “new in this book” marker without changing the '
            'translation, status, or whether the term is sent to the AI.'
        )
        clear_button = QPushButton('Clear AUTO mappings', self)
        clear_button.setToolTip(
            'Delete every non-human AUTO/AI proposal while keeping USER and '
            'explicitly confirmed mappings.'
        )
        self.ai_check_button = QPushButton('AI check new/selected…', self)
        self.ai_check_button.setEnabled(self.ai_callback is not None)
        self.ai_check_button.setToolTip(
            'Send selected rows—or all amber new rows when nothing is '
            'selected—with compact bilingual contexts. Local providers are '
            'not used. Existing human canonicals are not disclosed or replaced.'
        )
        self.ai_deep_button = QPushButton('Deep-check selected…', self)
        self.ai_deep_button.setEnabled(self.deep_ai_callback is not None)
        self.ai_deep_button.setToolTip(
            'Recheck selected rows with more book context. With Gemini this '
            'uses a freer semantic-adaptation prompt and also enables Google '
            'Search grounding, which may consume separate search quota or billing.'
        )
        use_ai_button = QPushButton('Use AI for selected')
        use_ai_button.setToolTip(
            'Copy the separate AI proposal into the human canonical column for '
            'selected rows. This is an explicit USER decision and can replace '
            'the old human mapping after confirmation.'
        )
        close_button = QPushButton('Close', self)
        scan_button.clicked.connect(self.scan)
        apply_button.clicked.connect(self.apply_edits)
        confirm_button.clicked.connect(self.confirm_selected)
        reset_button.clicked.connect(self.reset_selected)
        delete_button.clicked.connect(self.delete_selected)
        reviewed_button.clicked.connect(self.mark_selected_reviewed)
        clear_button.clicked.connect(self.clear_auto)
        self.ai_check_button.clicked.connect(
            lambda: self.request_ai_check(False)
        )
        self.ai_deep_button.clicked.connect(
            lambda: self.request_ai_check(True)
        )
        use_ai_button.clicked.connect(self.use_ai_selected)
        close_button.clicked.connect(self.accept)
        actions = QHBoxLayout()
        for button in (
            scan_button, apply_button, confirm_button, reset_button,
            delete_button, reviewed_button, clear_button,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(close_button)
        ai_actions = QHBoxLayout()
        ai_actions.addWidget(QLabel('AI terminology:', self))
        ai_actions.addWidget(self.ai_check_button)
        ai_actions.addWidget(self.ai_deep_button)
        ai_actions.addWidget(use_ai_button)
        ai_actions.addStretch(1)
        gender_help = QLabel(
            'Gender / grammar note is optional and is sent only when that '
            'character occurs in the current request. Example: "male; elderly '
            'man; use masculine forms". Mark Proper name to lock the confirmed '
            'spelling while still allowing normal grammatical inflections in '
            'prose. AUTO, USER, and user-confirmed CONFIRMED mappings are sent '
            'to the AI; explicit USER choices always outrank AUTO. '
            'Automatic voting and competing alternatives are disabled. Edits '
            'are saved by Apply, Scan, and Close. Terms first discovered in '
            'this book are highlighted in amber and can be filtered or marked '
            'reviewed without changing their terminology status.', self
        )
        gender_help.setWordWrap(True)
        self.save_status = QLabel(
            'Edits are saved by Apply, Scan, Close, and the title-bar X.',
            self,
        )
        self.save_status.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(settings)
        layout.addLayout(search_layout)
        layout.addWidget(gender_help)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.save_status)
        layout.addLayout(ai_actions)
        layout.addLayout(actions)
        self.populate()
        self.search_edit.setFocus()
        terminology_language = get_settings().get(
            'interface_language', 'auto'
        )
        localize_widget_tree(self, terminology_language)
        self.table.setHorizontalHeaderLabels([
            tr('Source', terminology_language),
            tr('Canonical target form', terminology_language),
            tr('AI suggestion', terminology_language),
            tr('Gender / grammar note', terminology_language),
            tr('Proper name', terminology_language),
            tr('Occurrences', terminology_language),
            tr('Status', terminology_language),
        ])

    def populate(self):
        self.table.setRowCount(0)
        for term in self.terminology.rows():
            row = self.table.rowCount()
            self.table.insertRow(row)
            source = QTableWidgetItem(str(term.get('source', '')))
            source.setFlags(source.flags() & ~Qt.ItemFlag.ItemIsEditable)
            source.setData(
                self.NEW_ROLE, bool(term.get('new_in_book', False))
            )
            source.setData(
                self.PROFILE_ROLE, bool(term.get('profile_term', False))
            )
            source.setData(
                self.STATUS_ROLE, str(term.get('status', 'UNRESOLVED')).upper()
            )
            source.setData(
                self.COUNT_ROLE, int(term.get('count', 0) or 0)
            )
            source.setData(
                self.PROPER_NAME_ROLE, bool(term.get('proper_name', False))
            )
            canonical = QTableWidgetItem(str(term.get('canonical', '')))
            canonical.setData(Qt.ItemDataRole.UserRole, str(term.get('canonical', '')))
            ai_value = str(term.get('ai_suggestion', ''))
            ai_details = []
            if term.get('ai_category'):
                ai_details.append(str(term.get('ai_category')))
            if term.get('ai_gender') and str(term.get('ai_gender')).lower() not in {
                'unknown', 'not_applicable', 'not applicable',
            }:
                ai_details.append(str(term.get('ai_gender')))
            if term.get('ai_confidence'):
                ai_details.append('%d%%' % round(
                    float(term.get('ai_confidence', 0.0)) * 100
                ))
            ai_suggestion = QTableWidgetItem(ai_value)
            ai_suggestion.setFlags(
                ai_suggestion.flags() & ~Qt.ItemFlag.ItemIsEditable
            )
            ai_suggestion.setToolTip('\n'.join(filter(None, (
                '; '.join(ai_details), str(term.get('ai_notes', '')),
            ))))
            gender = QTableWidgetItem(str(term.get('gender', '')))
            gender.setData(Qt.ItemDataRole.UserRole, str(term.get('gender', '')))
            proper_name = QTableWidgetItem(
                'Name' if term.get('proper_name', False) else ''
            )
            proper_name.setFlags(
                (proper_name.flags() | Qt.ItemFlag.ItemIsUserCheckable) &
                ~Qt.ItemFlag.ItemIsEditable
            )
            proper_name.setCheckState(
                Qt.CheckState.Checked if term.get('proper_name', False)
                else Qt.CheckState.Unchecked
            )
            proper_name.setData(
                Qt.ItemDataRole.UserRole, bool(term.get('proper_name', False))
            )
            values = [
                source, canonical, ai_suggestion, gender, proper_name,
                QTableWidgetItem(str(term.get('count', 0))),
                QTableWidgetItem(str(term.get('status', 'UNRESOLVED'))),
            ]
            for column, item in enumerate(values):
                if column not in (1, 3):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if term.get('new_in_book', False):
                    item.setBackground(QBrush(QColor(92, 66, 24)))
                    item.setToolTip(
                        'New in this book; it was not present in the applied '
                        'series profile.'
                    )
                self.table.setItem(row, column, item)
        for column in (4, 5, 6):
            self.table.resizeColumnToContents(column)
        self.filter_terms()

    def _term_search_matches(self):
        query = self.search_edit.text().strip().casefold()
        selected_filter = str(self.term_filter.currentData() or 'all')
        matches = []
        for row in range(self.table.rowCount()):
            source_item = self.table.item(row, 0)
            is_new = bool(source_item.data(self.NEW_ROLE))
            is_profile = bool(source_item.data(self.PROFILE_ROLE))
            status = str(source_item.data(self.STATUS_ROLE) or '').upper()
            count = int(source_item.data(self.COUNT_ROLE) or 0)
            is_name = bool(source_item.data(self.PROPER_NAME_ROLE))
            human_value = self.table.item(row, 1).text().strip()
            ai_value = self.table.item(row, 2).text().strip()
            filter_matches = {
                'all': True,
                'new': is_new,
                'present': count > 0,
                'profile': is_profile,
                'unresolved': not human_value and not ai_value,
                'ai_suggestion': bool(ai_value),
                'auto': status == 'AUTO',
                'user': status == 'USER',
                'confirmed': status == 'CONFIRMED',
                'locked': status in {'USER', 'CONFIRMED'},
                'names': is_name,
                'ai_diff': bool(
                    human_value and ai_value and
                    human_value.casefold() != ai_value.casefold()
                ),
            }.get(selected_filter, True)
            if not filter_matches:
                continue
            searchable = ' '.join(
                self.table.item(row, column).text()
                for column in range(self.table.columnCount())
                if self.table.item(row, column) is not None
            ).casefold()
            if not query or query in searchable:
                matches.append(row)
        return matches

    def filter_terms(self, *_args):
        matches = self._term_search_matches()
        visible = set(matches)
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, row not in visible)
        query = self.search_edit.text().strip()
        filtered = str(self.term_filter.currentData() or 'all') != 'all'
        self.search_count.setText(
            '%d match%s' % (len(matches), '' if len(matches) == 1 else 'es')
            if query or filtered else '%d terms' % self.table.rowCount()
        )
        if query and matches and self.table.currentRow() not in visible:
            self.table.setCurrentCell(matches[0], 0)
            self.table.scrollToItem(self.table.item(matches[0], 0))

    def find_next_term(self, direction=1):
        matches = self._term_search_matches()
        if not matches:
            return
        current = self.table.currentRow()
        if current in matches:
            position = matches.index(current)
            row = matches[(position + int(direction)) % len(matches)]
        else:
            row = matches[0 if int(direction) >= 0 else -1]
        self.table.setCurrentCell(row, 0)
        self.table.scrollToItem(self.table.item(row, 0))

    def _save_controls(self):
        self.terminology.minimum_occurrences = self.minimum.value()
        self.terminology.save()

    def _selected_sources(self):
        return [
            self.table.item(index.row(), 0).text()
            for index in self.table.selectionModel().selectedRows()
        ]

    def _commit_active_editor(self):
        """Push the last in-place cell edit into its QTableWidgetItem."""
        focus = QApplication.focusWidget()
        if focus is not None and (
            focus is self.table or self.table.isAncestorOf(focus)
        ):
            focus.clearFocus()
            QApplication.processEvents()

    def scan(self):
        if self.scan_callback is None:
            return
        self.apply_edits()
        self._save_controls()
        self.scan_callback()
        self.populate()

    def request_ai_check(self, deep=False):
        callback = self.deep_ai_callback if deep else self.ai_callback
        if callback is None:
            return
        sources = self._selected_sources()
        if not sources:
            sources = [
                self.table.item(row, 0).text()
                for row in range(self.table.rowCount())
                if bool(self.table.item(row, 0).data(self.NEW_ROLE))
            ]
        self.apply_edits()
        if not sources:
            _message(
                self, QMessageBox.Icon.Information,
                'No terminology selected',
                'Select one or more rows, or leave amber new rows available.'
            )
            return
        callback(sources)

    def use_ai_selected(self):
        sources = self._selected_sources()
        if not sources:
            _message(
                self, QMessageBox.Icon.Information,
                'No terminology selected', 'Select one or more rows first.'
            )
            return
        self.apply_edits()
        usable = []
        replacements = 0
        for source in sources:
            key = self.terminology._stored_term_key(source)
            term = self.terminology.terms.get(key, {}) if key else {}
            suggestion = str(term.get('ai_suggestion', '')).strip()
            if not suggestion:
                continue
            if term.get('canonical') and str(
                term.get('canonical')
            ).casefold() != suggestion.casefold():
                replacements += 1
            usable.append((source, suggestion, term))
        if not usable:
            _message(
                self, QMessageBox.Icon.Information,
                'No AI suggestions',
                'The selected rows do not contain an AI proposal.'
            )
            return
        answer = QMessageBox.question(
            self, 'Use AI terminology?',
            'Promote the AI proposal to a USER canonical for %d selected '
            'term(s)? Existing human mappings replaced: %d.' % (
                len(usable), replacements
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        changed = 0
        for source, suggestion, term in usable:
            if not self.terminology.set_user(source, suggestion):
                continue
            ai_gender = str(term.get('ai_gender', '')).strip()
            if (
                not term.get('gender') and ai_gender.casefold() not in {
                    '', 'unknown', 'not_applicable', 'not applicable',
                }
            ):
                self.terminology.set_gender(source, ai_gender)
            changed += 1
        self.terminology.save()
        self.populate()
        self.save_status.setText(
            'Promoted %d AI proposal(s) to explicit USER mappings.' % changed
        )

    def apply_edits(self):
        self._commit_active_editor()
        rejected = []
        for row in range(self.table.rowCount()):
            source = self.table.item(row, 0).text()
            item = self.table.item(row, 1)
            gender_item = self.table.item(row, 3)
            old = str(item.data(Qt.ItemDataRole.UserRole) or '')
            new = item.text().strip()
            old_gender = str(gender_item.data(Qt.ItemDataRole.UserRole) or '')
            new_gender = gender_item.text().strip()
            proper_name_item = self.table.item(row, 4)
            old_proper_name = bool(
                proper_name_item.data(Qt.ItemDataRole.UserRole)
            )
            new_proper_name = (
                proper_name_item.checkState() == Qt.CheckState.Checked
            )
            if new != old:
                if new:
                    if not self.terminology.set_user(source, new):
                        rejected.append(source)
                elif new != old:
                    self.terminology.reset(source)
            if new_gender != old_gender or (new != old and new_gender):
                self.terminology.set_gender(source, new_gender)
            if new_proper_name != old_proper_name:
                self.terminology.set_proper_name(source, new_proper_name)
        self.terminology.save()
        self.populate()
        profile_name = str(getattr(
            self.terminology, 'profile_name', ''
        ) or '')
        if profile_name:
            destination = 'shared profile "%s"' % profile_name
        elif (
            self.terminology.active_profile and
            self.terminology.sync_profile
        ):
            destination = (
                'this book and series profile "%s"; automatic scans add only '
                'missing keys, while explicit user edits update their key'
            ) % (
                self.terminology.active_profile
            )
        else:
            destination = 'this book\'s private memory'
        self.save_status.setText(
            'Saved to %s at %s.' % (
                destination, time.strftime('%H:%M:%S')
            )
        )
        if rejected:
            _message(
                self, QMessageBox.Icon.Warning,
                'Invalid canonical translation',
                'These mappings were not saved because the canonical target '
                'form still contains source-language text: %s. Use a semantic '
                'translation or target-script transcription.' % ', '.join(
                    rejected[:20]
                )
            )

    def confirm_selected(self):
        sources = self._selected_sources()
        self.apply_edits()
        for source in sources:
            self.terminology.confirm(source)
        self.populate()

    def reset_selected(self):
        sources = self._selected_sources()
        self.apply_edits()
        for source in sources:
            self.terminology.reset(source)
        self.populate()

    def delete_selected(self):
        sources = self._selected_sources()
        if not sources:
            return
        self.apply_edits()
        preview = ', '.join(sources[:5])
        if len(sources) > 5:
            preview += ', …'
        answer = QMessageBox.question(
            self, 'Delete terminology permanently?',
            'Delete and block %d selected term(s) from being rediscovered by '
            'later scans or stale book copies?\n\n%s\n\nA term can still be '
            'created again later by an explicit USER edit.' % (
                len(sources), preview
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.save_status.setText('Deletion cancelled; nothing changed.')
            return
        for source in sources:
            self.terminology.delete(source)
        if (
            sources and self.terminology.active_profile and
            self.terminology.sync_profile and
            not getattr(self.terminology, 'profile_name', '')
        ):
            MemoryProfileLibrary().delete_terms(
                self.terminology.active_profile, sources
            )
        self.populate()
        destination = (
            'the shared profile' if getattr(
                self.terminology, 'profile_name', ''
            ) else
            'this book and its synchronized series profile' if (
                self.terminology.active_profile and
                self.terminology.sync_profile
            ) else
            'this book memory'
        )
        self.save_status.setText(
            'Deleted and blocked %d term(s) in %s; no Save as is required.' % (
                len(sources), destination
            )
        )

    def mark_selected_reviewed(self):
        sources = self._selected_sources()
        self.apply_edits()
        self.terminology.mark_reviewed(sources)
        self.populate()

    def clear_auto(self):
        answer = QMessageBox.question(
            self, 'Clear AUTO mappings',
            'Clear AUTO and non-user-confirmed CONFIRMED mappings while keeping '
            'USER and explicitly confirmed mappings?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.terminology.clear_auto_memory()
            self.populate()

    def accept(self):
        self.apply_edits()
        self._save_controls()
        super().accept()

    def closeEvent(self, event):
        # Users naturally treat the window-manager X like the visible Close
        # button. Persist the active cell as well, instead of silently losing
        # the last glossary edit.
        self.apply_edits()
        self._save_controls()
        super().closeEvent(event)


class SettingsDialog(QDialog):
    def __init__(self, parent=None, terminology=None):
        super().__init__(parent)
        self.terminology = terminology
        self.setWindowTitle('AI Settings — Translation Proofreader')
        self.setMinimumSize(980, 720)
        values = get_settings()
        self.ai_profiles = get_ai_profiles()
        self._active_ai_profile = str(
            values.get('active_ai_profile', '') or ''
        )

        self.interface_language = QComboBox(self)
        for code, label in LANGUAGES:
            self.interface_language.addItem(label, code)
        language_index = self.interface_language.findData(
            values.get('interface_language', 'auto')
        )
        self.interface_language.setCurrentIndex(max(0, language_index))
        self.interface_language.setToolTip(
            'The interface changes after this settings window is saved and '
            'reopened. Missing translations fall back to English.'
        )

        self.experimental_polishing = QCheckBox(
            'Enable experimental polishing controls', self
        )
        self.experimental_polishing.setChecked(bool(
            values.get('experimental_polishing', False)
        ))
        self.experimental_polishing.setToolTip(
            'Optional isolated surface pass. It deliberately omits the core '
            'prompt, book instructions, memory, and glossary, so it may alter '
            'established terminology. Keep it disabled unless you intend to '
            'review every proposed change.'
        )

        self.ai_profile_combo = QComboBox(self)
        self.ai_profile_combo.setMinimumContentsLength(28)
        self.load_ai_profile_button = QPushButton('Load', self)
        self.save_ai_profile_button = QPushButton('Save as...', self)
        self.update_ai_profile_button = QPushButton('Update', self)
        self.delete_ai_profile_button = QPushButton('Delete', self)
        self.load_ai_profile_button.clicked.connect(self.load_ai_profile)
        self.save_ai_profile_button.clicked.connect(self.save_ai_profile_as)
        self.update_ai_profile_button.clicked.connect(self.update_ai_profile)
        self.delete_ai_profile_button.clicked.connect(self.delete_ai_profile)
        self.ai_profile_combo.currentIndexChanged.connect(
            self._update_ai_profile_buttons
        )
        ai_profile_row = QWidget(self)
        ai_profile_layout = QHBoxLayout(ai_profile_row)
        ai_profile_layout.setContentsMargins(0, 0, 0, 0)
        ai_profile_layout.addWidget(self.ai_profile_combo, 1)
        ai_profile_layout.addWidget(self.load_ai_profile_button)
        ai_profile_layout.addWidget(self.save_ai_profile_button)
        ai_profile_layout.addWidget(self.update_ai_profile_button)
        ai_profile_layout.addWidget(self.delete_ai_profile_button)
        self._refresh_ai_profiles(self._active_ai_profile)

        self.review_mode = QComboBox(self)
        self.review_mode.addItem(
            'Terminology replacement (strict)', 'terminology'
        )
        self.review_mode.addItem(
            'Terminology + language cleanup', 'cleanup'
        )
        self.review_mode.addItem(
            'Experimental full proofreading', 'full'
        )
        mode_index = self.review_mode.findData(
            values.get('review_mode', 'full')
        )
        self.review_mode.setCurrentIndex(max(0, mode_index))
        self.review_mode.setToolTip(
            'Strict modes apply only exact localized replacements. Full '
            'proofreading lets the AI rewrite paragraphs and therefore needs '
            'manual review.'
        )

        self.provider = QComboBox(self)
        self.provider.addItem('Gemini API', 'gemini')
        self.provider.addItem('DeepSeek API', 'deepseek')
        self.provider.addItem('Other OpenAI-compatible API', 'openai')
        self.provider.addItem(
            'Local model — Ollama / LM Studio / llama.cpp', 'local'
        )
        provider_index = self.provider.findData(values['provider'])
        self.provider.setCurrentIndex(max(0, provider_index))
        self.provider.setMinimumWidth(700)

        self.api_key = QLineEdit(values['api_key'], self)
        self.api_key.setMinimumWidth(700)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText('The key is stored locally in the Calibre profile')
        self.endpoint = QLineEdit(values['endpoint'], self)
        self.endpoint.setMinimumWidth(700)
        self.model = QComboBox(self)
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model.setMinimumContentsLength(32)
        if values['model']:
            self.model.addItem(values['model'])
            self.model.setCurrentText(values['model'])
        self._last_provider = values['provider']
        self.model.lineEdit().setPlaceholderText(
            'Fetch the list or enter the exact model name'
        )
        self.discover_button = QPushButton(
            'Connect and fetch models', self
        )
        self.discover_button.clicked.connect(self.discover_models)
        model_row = QWidget(self)
        model_layout = QHBoxLayout(model_row)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.addWidget(self.model, 1)
        model_layout.addWidget(self.discover_button)
        self.source_language = QLineEdit(values['source_language'], self)
        self.target_language = QLineEdit(values['target_language'], self)
        self.source_language.setPlaceholderText('Original language')
        self.target_language.setPlaceholderText('Translation language')
        self.auto_source_language = QCheckBox('Auto-detect', self)
        self.auto_target_language = QCheckBox('Auto-detect', self)
        self.auto_source_language.setChecked(bool(
            values['auto_detect_source_language']
        ))
        self.auto_target_language.setChecked(bool(
            values['auto_detect_target_language']
        ))
        language_row = QWidget(self)
        language_layout = QHBoxLayout(language_row)
        language_layout.setContentsMargins(0, 0, 0, 0)
        language_layout.addWidget(QLabel('Original:', self))
        language_layout.addWidget(self.source_language, 1)
        language_layout.addWidget(self.auto_source_language)
        language_layout.addSpacing(12)
        language_layout.addWidget(QLabel('Translation:', self))
        language_layout.addWidget(self.target_language, 1)
        language_layout.addWidget(self.auto_target_language)

        self.temperature = QDoubleSpinBox(self)
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(float(values['temperature']))
        self.temperature.setToolTip(
            '0.2–0.4: very conservative; 0.5–0.7: more flexible literary editing; '
            'above 0.8: greater rewriting and consistency risk'
        )
        self.timeout = QSpinBox(self)
        self.timeout.setRange(5, 600)
        self.timeout.setSuffix(' s')
        self.timeout.setValue(int(values['timeout']))
        self.chunk_chars = QSpinBox(self)
        self.chunk_chars.setRange(1000, 100000)
        self.chunk_chars.setSingleStep(500)
        self.chunk_chars.setValue(int(values['chunk_chars']))
        self.chunk_chars.setSuffix(' per book')
        self.chunk_chars.setToolTip(
            'Maximum characters from each book in one request. 8,000 gives '
            'Flash Lite models more narrative context while keeping JSON '
            'responses manageable.'
        )
        self.request_attempts = QSpinBox(self)
        self.request_attempts.setRange(1, 20)
        self.request_attempts.setValue(int(values['request_attempts']))
        self.response_attempts = QSpinBox(self)
        self.response_attempts.setRange(1, 10)
        self.response_attempts.setValue(int(values['response_attempts']))
        self.response_attempts.setToolTip(
            'Retries a chunk when the provider returns malformed, duplicated, '
            'or incomplete proofreading JSON.'
        )
        self.auto_repair_suspicious = QCheckBox(
            'Send one focused repair request for a suspicious AI revision', self
        )
        self.auto_repair_suspicious.setChecked(bool(
            values.get('auto_repair_suspicious_responses', True)
        ))
        self.auto_repair_suspicious.setToolTip(
            'In full proofreading mode, one additional request is sent only '
            'when the proposed text newly copies source-language wording, adds '
            'an editorial aside in brackets, or appears truncated. A remaining '
            'problem is kept as an editable yellow suggestion.'
        )
        self.retry_delay = QDoubleSpinBox(self)
        self.retry_delay.setRange(0.1, 60.0)
        self.retry_delay.setSingleStep(0.5)
        self.retry_delay.setSuffix(' s')
        self.retry_delay.setValue(float(values['retry_delay']))
        self.request_interval = QDoubleSpinBox(self)
        self.request_interval.setRange(0.0, 60.0)
        self.request_interval.setSingleStep(0.5)
        self.request_interval.setSuffix(' s')
        self.request_interval.setValue(float(values['request_interval']))
        self.auto_cleanup_merged = QCheckBox(
            'Automatically remove high-confidence merged duplicate prefixes',
            self,
        )
        self.auto_cleanup_merged.setChecked(bool(
            values.get('auto_cleanup_merged_prefixes', True)
        ))
        self.auto_cleanup_merged.setToolTip(
            'Uses local HTML-break structure and consecutive source/translation '
            'length patterns. No AI call is made and the input EPUB is never changed.'
        )
        self.repair_major_mistranslations = QCheckBox(
            'Rewrite severe mistranslations and restore locally missing source text',
            self,
        )
        self.repair_major_mistranslations.setChecked(bool(
            values.get('repair_major_mistranslations', True)
        ))
        self.repair_major_mistranslations.setToolTip(
            'When neighboring context proves that both paragraphs belong to the '
            'same narrative location, allow a full rewrite or a local translation '
            'of omitted source sentences. True shifted boundaries remain protected.'
        )
        self.repair_major_mistranslations.hide()
        self.language_scan_status = QLabel(self)
        self.language_scan_status.setWordWrap(True)
        self.language_scan_status.setToolTip(
            'Calibre custom dictionaries are used only as a local suspicion '
            'filter. Unknown words are never changed automatically.'
        )

        self._prompt_text = values['prompt']
        self.prompt_button = QPushButton('Open core proofreading prompt…', self)
        self.prompt_button.clicked.connect(self.edit_core_prompt)

        form = QFormLayout()
        form.addRow('Interface language:', self.interface_language)
        form.addRow('Experimental features:', self.experimental_polishing)
        form.addRow('Settings profile:', ai_profile_row)
        form.addRow('Review mode:', self.review_mode)
        form.addRow('Provider:', self.provider)
        form.addRow('API key:', self.api_key)
        form.addRow('Endpoint:', self.endpoint)
        form.addRow('Model:', model_row)
        form.addRow('Languages:', language_row)
        form.addRow('Temperature:', self.temperature)
        form.addRow('Timeout:', self.timeout)
        form.addRow('Characters per request:', self.chunk_chars)
        form.addRow('Request attempts:', self.request_attempts)
        form.addRow('Invalid response attempts:', self.response_attempts)
        form.addRow('Suggestion repair:', self.auto_repair_suspicious)
        form.addRow('Initial retry delay:', self.retry_delay)
        form.addRow('Interval between paragraphs:', self.request_interval)
        form.addRow('Content repair:', self.auto_cleanup_merged)
        form.addRow('Local language scan:', self.language_scan_status)

        prompt_group = QGroupBox('Core prompt', self)
        prompt_layout = QHBoxLayout(prompt_group)
        prompt_description = QLabel(
            'Compact full-proofreading instructions and JSON contract. Strict '
            'modes use a fixed operation-only prompt.', self
        )
        prompt_description.setWordWrap(True)
        prompt_layout.addWidget(prompt_description, 1)
        prompt_layout.addWidget(self.prompt_button)

        book_prompt_group = QGroupBox('Book instructions', self)
        book_prompt_layout = QHBoxLayout(book_prompt_group)
        book_prompt_description = QLabel(
            'Optional instructions and locked USER glossary entries for the '
            'currently loaded pair of books.', self
        )
        book_prompt_description.setWordWrap(True)
        self.book_prompt_settings_button = QPushButton(
            'Open book-specific instructions…', self
        )
        self.book_prompt_settings_button.clicked.connect(
            self.edit_book_prompt
        )
        book_prompt_layout.addWidget(book_prompt_description, 1)
        book_prompt_layout.addWidget(self.book_prompt_settings_button)

        auto_prompt_group = QGroupBox('Active book memory', self)
        auto_prompt_layout = QHBoxLayout(auto_prompt_group)
        auto_prompt_description = QLabel(
            'View active AUTO/USER/CONFIRMED terminology and optional book notes. '
            'AI voting and learned alternatives are disabled.', self
        )
        auto_prompt_description.setWordWrap(True)
        self.auto_prompt_settings_button = QPushButton(
            'Open active memory and notes…', self
        )
        self.auto_prompt_settings_button.clicked.connect(
            self.edit_auto_prompt
        )
        auto_prompt_layout.addWidget(auto_prompt_description, 1)
        auto_prompt_layout.addWidget(self.auto_prompt_settings_button)

        has_book_project = self.terminology is not None
        self.book_prompt_settings_button.setEnabled(has_book_project)
        self.auto_prompt_settings_button.setEnabled(has_book_project)
        if not has_book_project:
            unavailable = (
                'Load and align two books, then open AI settings from the '
                'proofreader window.'
            )
            self.book_prompt_settings_button.setToolTip(unavailable)
            self.auto_prompt_settings_button.setToolTip(unavailable)

        test_button = QPushButton('Test proofreading', self)
        test_button.clicked.connect(self.test_api)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.save_and_accept)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(test_button)
        bottom.addStretch(1)
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(prompt_group)
        layout.addWidget(book_prompt_group)
        layout.addWidget(auto_prompt_group)
        layout.addStretch(1)
        layout.addLayout(bottom)

        self.provider.currentIndexChanged.connect(self.provider_changed)
        self.review_mode.currentIndexChanged.connect(self.review_mode_changed)
        self.auto_source_language.toggled.connect(
            self.source_language.setDisabled
        )
        self.auto_target_language.toggled.connect(
            self.target_language.setDisabled
        )
        self.target_language.textChanged.connect(
            self._update_language_scan_status
        )
        self.auto_target_language.toggled.connect(
            self._update_language_scan_status
        )
        self.source_language.setDisabled(self.auto_source_language.isChecked())
        self.target_language.setDisabled(self.auto_target_language.isChecked())
        self._update_language_scan_status()
        self.provider_changed()
        self.review_mode_changed()
        localize_widget_tree(
            self, values.get('interface_language', 'auto')
        )

    def _update_language_scan_status(self, *_args):
        target = self.target_language.text().casefold()
        ukrainian = bool(
            re.search(r'(^|\W)(?:uk|ukr)(?:\W|$)', target)
        ) or any(marker in target for marker in ('ukrain', 'укра'))
        if ukrainian:
            detail = (
                'available' if ukrainian_dictionary_available() else
                'not installed; strong Russian patterns still work'
            )
            self.language_scan_status.setText(
                'Optional Ukrainian local detector: %s.' % detail
            )
        else:
            self.language_scan_status.setText(
                'No language-specific local dictionary is enabled for this '
                'target. The selected AI still performs the general language '
                'review.'
            )

    def values(self):
        return {
            'provider': self.provider.currentData(),
            'api_key': self.api_key.text().strip(),
            'endpoint': self.endpoint.text().strip(),
            'model': self.model.currentText().strip(),
            'review_mode': self.review_mode.currentData(),
            'source_language': self.source_language.text().strip(),
            'target_language': self.target_language.text().strip(),
            'auto_detect_source_language': self.auto_source_language.isChecked(),
            'auto_detect_target_language': self.auto_target_language.isChecked(),
            'prompt': self._prompt_text,
            'temperature': self.temperature.value(),
            'timeout': self.timeout.value(),
            'chunk_chars': self.chunk_chars.value(),
            'request_attempts': self.request_attempts.value(),
            'response_attempts': self.response_attempts.value(),
            'auto_repair_suspicious_responses': (
                self.auto_repair_suspicious.isChecked()
            ),
            'retry_delay': self.retry_delay.value(),
            'request_interval': self.request_interval.value(),
            'auto_cleanup_merged_prefixes': self.auto_cleanup_merged.isChecked(),
            'repair_major_mistranslations': (
                self.repair_major_mistranslations.isChecked()
            ),
            'interface_language': self.interface_language.currentData(),
            'experimental_polishing': self.experimental_polishing.isChecked(),
        }

    def _selected_ai_profile(self):
        return str(self.ai_profile_combo.currentData() or '')

    def _refresh_ai_profiles(self, selected=''):
        self.ai_profiles = get_ai_profiles()
        self.ai_profile_combo.blockSignals(True)
        try:
            self.ai_profile_combo.clear()
            self.ai_profile_combo.addItem('Current settings (not linked)', '')
            for name in sorted(self.ai_profiles, key=str.casefold):
                self.ai_profile_combo.addItem(name, name)
            if selected:
                index = self.ai_profile_combo.findData(selected)
                if index >= 0:
                    self.ai_profile_combo.setCurrentIndex(index)
        finally:
            self.ai_profile_combo.blockSignals(False)
        self._update_ai_profile_buttons()

    def _update_ai_profile_buttons(self, *_args):
        selected = bool(self._selected_ai_profile())
        self.load_ai_profile_button.setEnabled(selected)
        self.update_ai_profile_button.setEnabled(selected)
        self.delete_ai_profile_button.setEnabled(selected)

    def _apply_ai_profile_values(self, values):
        provider = str(values.get('provider', DEFAULTS['provider']))
        index = self.provider.findData(provider)
        self.provider.blockSignals(True)
        try:
            self.provider.setCurrentIndex(max(0, index))
        finally:
            self.provider.blockSignals(False)
        self._last_provider = provider
        self.api_key.setText(str(values.get('api_key', '')))
        self.endpoint.setText(str(
            values.get('endpoint', PROVIDER_ENDPOINTS.get(provider, ''))
        ))
        self.model.clear()
        model = str(values.get('model', ''))
        if model:
            self.model.addItem(model)
            self.model.setCurrentText(model)
        mode = str(values.get('review_mode', DEFAULTS['review_mode']))
        mode_index = self.review_mode.findData(mode)
        self.review_mode.setCurrentIndex(max(0, mode_index))
        self.source_language.setText(str(
            values.get('source_language', DEFAULTS['source_language'])
        ))
        self.target_language.setText(str(
            values.get('target_language', DEFAULTS['target_language'])
        ))
        self.auto_source_language.setChecked(bool(values.get(
            'auto_detect_source_language',
            DEFAULTS['auto_detect_source_language'],
        )))
        self.auto_target_language.setChecked(bool(values.get(
            'auto_detect_target_language',
            DEFAULTS['auto_detect_target_language'],
        )))
        self.temperature.setValue(float(
            values.get('temperature', DEFAULTS['temperature'])
        ))
        self.timeout.setValue(int(values.get('timeout', DEFAULTS['timeout'])))
        self.chunk_chars.setValue(int(
            values.get('chunk_chars', DEFAULTS['chunk_chars'])
        ))
        self.request_attempts.setValue(int(
            values.get('request_attempts', DEFAULTS['request_attempts'])
        ))
        self.response_attempts.setValue(int(
            values.get('response_attempts', DEFAULTS['response_attempts'])
        ))
        self.auto_repair_suspicious.setChecked(bool(values.get(
            'auto_repair_suspicious_responses',
            DEFAULTS['auto_repair_suspicious_responses'],
        )))
        self.retry_delay.setValue(float(
            values.get('retry_delay', DEFAULTS['retry_delay'])
        ))
        self.request_interval.setValue(float(
            values.get('request_interval', DEFAULTS['request_interval'])
        ))
        self.auto_cleanup_merged.setChecked(bool(values.get(
            'auto_cleanup_merged_prefixes',
            DEFAULTS['auto_cleanup_merged_prefixes'],
        )))
        self.repair_major_mistranslations.setChecked(bool(values.get(
            'repair_major_mistranslations',
            DEFAULTS['repair_major_mistranslations'],
        )))
        self.provider_changed()

    def review_mode_changed(self, *_args):
        mode = str(self.review_mode.currentData() or 'full')
        self.prompt_button.setEnabled(mode == 'full')
        self.auto_repair_suspicious.setEnabled(mode == 'full')
        if mode == 'full':
            self.prompt_button.setToolTip(
                'Edit the compact prompt used by Experimental full proofreading.'
            )
        else:
            self.prompt_button.setToolTip(
                'Strict modes use a fixed short operation-only prompt so the '
                'model cannot rewrite complete paragraphs.'
            )

    def load_ai_profile(self):
        name = self._selected_ai_profile()
        values = self.ai_profiles.get(name)
        if not name or not isinstance(values, dict):
            return
        self._apply_ai_profile_values(values)
        self._active_ai_profile = name

    def save_ai_profile_as(self):
        suggested = self.model.currentText().strip() or str(
            self.provider.currentText()
        )
        name, ok = QInputDialog.getText(
            self, 'Save AI settings profile', 'Profile name:', text=suggested
        )
        name = str(name or '').strip()
        if not ok or not name:
            return
        if any(name.casefold() == value.casefold() for value in self.ai_profiles):
            answer = QMessageBox.question(
                self, 'Replace settings profile',
                'Replace the existing settings profile "%s"?' % name,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            name = save_ai_profile(name, self.values())
        except Exception as err:
            _message(
                self, QMessageBox.Icon.Critical,
                'Could not save settings profile', str(err)
            )
            return
        self._active_ai_profile = name
        self._refresh_ai_profiles(name)

    def update_ai_profile(self):
        name = self._selected_ai_profile()
        if not name:
            return
        try:
            save_ai_profile(name, self.values())
        except Exception as err:
            _message(
                self, QMessageBox.Icon.Critical,
                'Could not update settings profile', str(err)
            )
            return
        self._active_ai_profile = name
        self._refresh_ai_profiles(name)

    def delete_ai_profile(self):
        name = self._selected_ai_profile()
        if not name:
            return
        answer = QMessageBox.question(
            self, 'Delete settings profile',
            'Permanently delete the settings profile "%s"?' % name,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        delete_ai_profile(name)
        if self._active_ai_profile.casefold() == name.casefold():
            self._active_ai_profile = ''
        self._refresh_ai_profiles()

    def edit_core_prompt(self):
        dialog = TextPromptDialog(
            'Core proofreading prompt', self._prompt_text,
            'Universal instructions for every book. Available variables: '
            '{source_language}, {target_language}, {book_instructions}, '
            '{auto_memory_instructions}, and {terminology_instructions}. '
            'Keep the JSON response contract intact.',
            self, reset_text=DEFAULT_PROMPT,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._prompt_text = dialog.text

    def edit_book_prompt(self):
        if not self.terminology:
            return
        dialog = TextPromptDialog(
            'Book-specific user instructions', self.terminology.manual_prompt,
            'Optional instructions and manual glossary for this book. Mapping '
            'lines such as "Loamwalker -> Землеходець" become locked USER '
            'mappings and cannot be overwritten by the AI.',
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.terminology.manual_prompt = dialog.text
            self.terminology.apply_manual_prompt_mappings()
            self.terminology.save()

    def edit_auto_prompt(self):
        if self.terminology:
            AutoPromptDialog(self.terminology, self).exec()

    def provider_changed(self):
        provider = self.provider.currentData()
        if provider != self._last_provider:
            self.model.clear()
            self._last_provider = provider
        current = self.endpoint.text().strip()
        known = set(PROVIDER_ENDPOINTS.values())
        if not current or current in known:
            self.endpoint.setText(PROVIDER_ENDPOINTS[provider])
        if provider == 'local':
            self.api_key.setPlaceholderText(
                'Optional; required only if the local server is protected'
            )
        else:
            self.api_key.setPlaceholderText(
                'The key is stored locally in the Calibre profile'
            )

    def discover_models(self):
        check_values = self.values()
        check_values['request_attempts'] = min(
            2, check_values['request_attempts']
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            models = ProofreaderClient(check_values).list_models()
        except Exception as err:
            _message(
                self, QMessageBox.Icon.Critical,
                'Could not fetch models', str(err)
            )
        else:
            current = self.model.currentText().strip()
            self.model.clear()
            self.model.addItems(models)
            if current and current in models:
                self.model.setCurrentText(current)
            _message(
                self, QMessageBox.Icon.Information, 'Connection successful',
                'Found %d models. Choose one from the list.' % len(models)
            )
        finally:
            QApplication.restoreOverrideCursor()

    def save_and_accept(self):
        try:
            ProofreaderClient(self.values()).validate()
        except APIError as err:
            _message(self, QMessageBox.Icon.Warning, 'Incomplete settings', str(err))
            return
        values = self.values()
        save_settings(values)
        if self._active_ai_profile:
            save_ai_profile(self._active_ai_profile, values)
        else:
            save_settings({'active_ai_profile': ''})
        self.accept()

    def test_api(self):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            test_values = self.values()
            test_values['auto_detect_source_language'] = True
            test_values['auto_detect_target_language'] = True
            result = ProofreaderClient(test_values).proofread(
                'The door was open.', 'La puerta estaba abierta.'
            )
        except Exception as err:
            _message(self, QMessageBox.Icon.Critical, 'API error', str(err))
        else:
            _message(
                self, QMessageBox.Icon.Information, 'API is working',
                'A valid response was received.',
                'Revision: %s\n\nNotes: %s' % (
                    result['revised'], result['notes']
                ),
            )
        finally:
            QApplication.restoreOverrideCursor()


class ProofreadWorker(QObject):
    result = pyqtSignal(int, object)
    progress = pyqtSignal(int, int)
    message = pyqtSignal(str)
    item_failed = pyqtSignal(int, str)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, settings, work_items):
        super().__init__()
        self.settings = settings
        self.work_items = work_items
        self.cancelled = False

    @pyqtSlot()
    def run(self):
        client = ProofreaderClient(self.settings, self.message.emit)
        total = len(self.work_items)
        for count, work_item in enumerate(self.work_items, 1):
            index, pairs = work_item[:2]
            terminology = work_item[2] if len(work_item) > 2 else []
            if self.cancelled:
                break
            try:
                result = client.proofread_many(pairs, terminology=terminology)
            except ContentBlockedError as err:
                self.item_failed.emit(index, str(err))
                self.progress.emit(count, total)
                continue
            except ResponseFormatError as err:
                self.item_failed.emit(index, str(err))
                self.progress.emit(count, total)
                continue
            except Exception as err:
                self.failed.emit(str(err))
                break
            else:
                self.result.emit(index, result)
                self.progress.emit(count, total)
            interval = max(
                0.0, float(self.settings.get('request_interval', 0.5))
            )
            slept = 0.0
            while interval > slept and not self.cancelled:
                step = min(0.1, interval - slept)
                time.sleep(step)
                slept += step
        self.finished.emit()

    def cancel(self):
        self.cancelled = True


class TerminologyWorker(QObject):
    result = pyqtSignal(object)
    progress = pyqtSignal(int, int)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, settings, batches, metadata, use_search=False):
        super().__init__()
        self.settings = dict(settings)
        self.batches = list(batches)
        self.metadata = dict(metadata or {})
        self.use_search = bool(use_search)

    @pyqtSlot()
    def run(self):
        client = ProofreaderClient(self.settings)
        total = len(self.batches)
        for number, batch in enumerate(self.batches, 1):
            try:
                values = client.suggest_terminology(
                    batch, self.metadata, use_search=self.use_search
                )
            except Exception as err:
                self.failed.emit(str(err))
                break
            self.result.emit(values)
            self.progress.emit(number, total)
            if number < total:
                interval = max(
                    0.0, float(self.settings.get('request_interval', 0.5))
                )
                if self.settings.get('provider') == 'gemini':
                    # Free Gemini Flash tiers commonly expose 15 RPM. Staying
                    # just below that avoids paying for bursts of 429 retries.
                    interval = max(interval, 4.1)
                time.sleep(interval)
        self.finished.emit()


class ProofreaderDialog(QDialog):
    def __init__(self, gui, library_entries, selected_paths=None, parent=None):
        super().__init__(parent or gui)
        self.gui = gui
        self.original_doc = None
        self.translation_doc = None
        self.terminology = None
        self.memory_profiles = MemoryProfileLibrary()
        self._updating_memory_controls = False
        self.items = []
        self.chunks = []
        self._total_original_chars = 0
        self._total_translation_chars = 0
        self.current_index = -1
        self._translation_drafts = {}
        self._suggestion_drafts = {}
        self._committed_translation_values = {}
        self._committed_suggestion_values = {}
        self._manual_dirty_indices = set()
        self._suggestion_dirty_indices = set()
        self._polish_ready = False
        self._input_stage = ''
        self._input_proofread_pass = 0
        self._last_applied_stage = ''
        self._active_review_mode = ''
        self._suggestion_modes = {}
        self._updating = False
        self._thread = None
        self._worker = None
        self._term_thread = None
        self._term_worker = None
        self._term_error = ''
        self._terminology_dialog = None
        self._selection_cache = {}
        self._last_target_selection = ''
        selected_paths = selected_paths or []
        interface_settings = get_settings()
        self.experimental_polishing = bool(
            interface_settings.get('experimental_polishing', False)
        )

        self.setWindowTitle('Translation Proofreader — bilingual review')
        self.setMinimumSize(1180, 720)
        self.resize(1450, 860)

        self.original_picker = BookPicker(
            'Original', library_entries,
            selected_paths[0] if selected_paths else '', self
        )
        self.translation_picker = BookPicker(
            'Translation', library_entries,
            selected_paths[1] if len(selected_paths) > 1 else '', self
        )
        load_button = QPushButton('Load and align', self)
        settings_button = QPushButton('AI settings…', self)
        load_button.setMinimumWidth(170)
        settings_button.setMinimumWidth(170)
        load_button.clicked.connect(self.load_books)
        settings_button.clicked.connect(self.open_settings)
        self.core_prompt_button = QPushButton('Core prompt…', self)
        self.book_prompt_button = QPushButton('Book instructions…', self)
        self.scan_terms_button = QPushButton('Scan book terms', self)
        self.terminology_button = QPushButton('Book terminology…', self)
        self.auto_prompt_button = QPushButton('Book memory and notes…', self)
        self.core_prompt_button.clicked.connect(self.edit_core_prompt)
        self.book_prompt_button.clicked.connect(self.edit_book_prompt)
        self.scan_terms_button.clicked.connect(self.scan_book_terms)
        self.terminology_button.clicked.connect(self.open_terminology)
        self.auto_prompt_button.clicked.connect(self.edit_auto_prompt)
        for button in (
            self.book_prompt_button, self.scan_terms_button,
            self.terminology_button, self.auto_prompt_button,
        ):
            button.setEnabled(False)

        books = QGridLayout()
        books.addWidget(QLabel('Original:'), 0, 0)
        books.addWidget(self.original_picker, 0, 1)
        books.addWidget(QLabel('Translation:'), 1, 0)
        books.addWidget(self.translation_picker, 1, 1)
        books.addWidget(load_button, 0, 2)
        books.addWidget(settings_button, 1, 2)
        prompt_actions = QHBoxLayout()
        prompt_actions.addWidget(self.core_prompt_button)
        prompt_actions.addWidget(self.book_prompt_button)
        prompt_actions.addWidget(self.scan_terms_button)
        prompt_actions.addWidget(self.terminology_button)
        prompt_actions.addWidget(self.auto_prompt_button)
        prompt_actions.addStretch(1)
        self.review_mode_label = QLabel('', self)
        prompt_actions.addWidget(self.review_mode_label)
        books.addLayout(prompt_actions, 2, 1, 1, 2)
        self._update_review_mode_label()

        self.memory_profile_combo = QComboBox(self)
        self.memory_profile_combo.setMinimumContentsLength(28)
        self.apply_memory_profile_button = QPushButton('Apply profile', self)
        self.save_memory_profile_button = QPushButton('Save as...', self)
        self.delete_memory_profile_button = QPushButton('Delete', self)
        self.clear_book_memory_button = QPushButton('Clear book memory', self)
        self.memory_profile_sync = QCheckBox(
            'Sync user edits and new terms', self
        )
        self.memory_profile_sync.setToolTip(
            'Automatic scans add only terms that do not exist in the shared '
            'profile. Explicit canonical, gender, proper-name, reset, and '
            'delete edits made by you are also saved to the selected profile. '
            'Older book copies and automatic counts cannot overwrite it.'
        )
        self.apply_memory_profile_button.clicked.connect(
            self.apply_memory_profile
        )
        self.save_memory_profile_button.clicked.connect(
            self.save_memory_profile_as
        )
        self.delete_memory_profile_button.clicked.connect(
            self.delete_memory_profile
        )
        self.clear_book_memory_button.clicked.connect(
            self.clear_book_memory
        )
        self.memory_profile_sync.toggled.connect(
            self.memory_profile_sync_changed
        )
        self.memory_profile_combo.currentIndexChanged.connect(
            self._update_memory_profile_controls
        )
        memory_actions = QHBoxLayout()
        memory_actions.addWidget(QLabel('Series memory:', self))
        memory_actions.addWidget(self.memory_profile_combo, 1)
        memory_actions.addWidget(self.apply_memory_profile_button)
        memory_actions.addWidget(self.save_memory_profile_button)
        memory_actions.addWidget(self.delete_memory_profile_button)
        memory_actions.addWidget(self.memory_profile_sync)
        memory_actions.addWidget(self.clear_book_memory_button)
        books.addLayout(memory_actions, 3, 1, 1, 2)
        self._refresh_memory_profiles()

        self.table = QTableWidget(0, 6, self)
        main_language = interface_settings.get('interface_language', 'auto')
        self.table.setHorizontalHeaderLabels([
            '#', tr('Original text', main_language),
            tr('Characters', main_language), tr('Status', main_language),
            tr('Severity', main_language), tr('Alignment', main_language),
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.setMinimumWidth(640)
        self.table.currentCellChanged.connect(self.change_row)

        self.original_edit = QPlainTextEdit(self)
        self.original_edit.setReadOnly(True)
        self.translation_edit = QPlainTextEdit(self)
        self.suggestion_edit = QPlainTextEdit(self)
        editor_hint = (
            'A blank line separates source paragraphs. Keep the same number of '
            'paragraphs when editing a chunk manually.'
        )
        self.translation_edit.setToolTip(editor_hint)
        self.suggestion_edit.setToolTip(editor_hint)
        self.notes_edit = NumberedNotesEdit(self)
        self.notes_edit.setReadOnly(True)
        self.notes_edit.setMaximumHeight(120)
        self.notes_edit.setToolTip(
            'Click a numbered note to reveal that paragraph in all three panes.'
        )
        self.notes_edit.paragraphClicked.connect(
            self.scroll_to_note_paragraph
        )
        self.translation_edit.textChanged.connect(self.capture_edits)
        self.suggestion_edit.textChanged.connect(self.capture_edits)
        self.original_edit.selectionChanged.connect(
            lambda: self._remember_editor_selection(
                'original', self.original_edit
            )
        )
        self.translation_edit.selectionChanged.connect(
            lambda: self._remember_editor_selection(
                'translation', self.translation_edit
            )
        )
        self.suggestion_edit.selectionChanged.connect(
            lambda: self._remember_editor_selection(
                'suggestion', self.suggestion_edit
            )
        )
        self._syncing_scroll = False
        for editor in (
            self.original_edit, self.translation_edit, self.suggestion_edit
        ):
            editor.verticalScrollBar().valueChanged.connect(
                lambda value, source=editor: self._sync_scroll(source, value)
            )

        editors = QSplitter(Qt.Orientation.Horizontal, self)
        editors.addWidget(self._panel('Original', self.original_edit))
        editors.addWidget(self._panel('Current translation', self.translation_edit))
        editors.addWidget(self._panel(
            'AI suggestion — Accept suggestion to use', self.suggestion_edit
        ))
        editors.setSizes([450, 450, 450])

        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(editors, 1)
        notes_header = QHBoxLayout()
        notes_header.addWidget(QLabel('AI notes:'))
        notes_header.addStretch(1)
        self.removed_text_button = QPushButton(
            'Inspect removed duplicate…', self
        )
        self.removed_text_button.setEnabled(False)
        self.removed_text_button.clicked.connect(self.inspect_removed_text)
        notes_header.addWidget(self.removed_text_button)
        right_layout.addLayout(notes_header)
        right_layout.addWidget(self.notes_edit)

        center = QSplitter(Qt.Orientation.Horizontal, self)
        center.addWidget(self.table)
        center.addWidget(right)
        center.setSizes([640, 810])

        self.analyze_button = QPushButton('Review selected', self)
        self.analyze_button.setToolTip(
            'Review the current chunk or all chunks selected with Ctrl/Shift'
        )
        self.batch_button = QPushButton('Review all', self)
        self.polish_selected_button = QPushButton('Polish selected', self)
        self.polish_selected_button.setToolTip(
            'Run the isolated temperature-0.1 surface polish only for the '
            'currently selected chunk rows. Use this to retry failed rows with '
            'another model without restarting the whole book.'
        )
        self.polish_button = QPushButton('Polish all', self)
        self.polish_button.setToolTip(
            'Final surface pass at temperature 0.1. Uses only the aligned '
            'original and current translation; Core prompt, book instructions, '
            'book memory, and glossary are excluded.'
        )
        self.accept_button = QPushButton('Accept suggestion', self)
        self.accept_button.setToolTip(
            'Copy the right-hand AI suggestion into Current translation and '
            'mark it for EPUB output. Editing the right pane alone never '
            'changes the saved book.'
        )
        self.apply_manual_button = QPushButton('Apply manual edit', self)
        self.apply_manual_button.setToolTip(
            'Commit your manual edit for EPUB output. If the right AI suggestion '
            'pane was edited, that version is used; otherwise the middle Current '
            'translation pane is used.'
        )
        self.add_term_button = QPushButton('Add term…', self)
        self.add_term_button.setToolTip(
            'Add the selected Original and Current translation text as a locked '
            'USER glossary mapping. If no source text is selected, choose an '
            'unresolved term occurring in this chunk.'
        )
        self.apply_all_manual_button = QPushButton('Apply all manual edits', self)
        self.apply_all_manual_button.setToolTip(
            'Approve every Current translation pane changed by hand; Accept '
            'all suggestions skips these chunks'
        )
        self.keep_button = QPushButton('Keep translation', self)
        self.approve_yellow_button = QPushButton('Approve yellow rows…', self)
        self.approve_yellow_button.setToolTip(
            'After confirmation, resolve every yellow (uncertain) row at once. '
            'The AI suggestion is used when present; otherwise Current '
            'translation is kept. Red rows and unapplied manual edits are skipped.'
        )
        self.accept_all_button = QPushButton('Accept all suggestions', self)
        self.save_button = QPushButton('Save new EPUB…', self)
        self.add_to_library = QCheckBox('Add to Calibre library', self)
        library_available = bool(
            self.gui is not None and getattr(self.gui, 'current_db', None)
        )
        self.add_to_library.setChecked(library_available)
        self.add_to_library.setEnabled(library_available)
        self.add_to_library.setToolTip(
            'Create a separate Calibre book with a language prefix, the actual '
            'last processing stage in its title, and cumulative proofreaded / '
            'polished tags.'
        )
        self.cancel_button = QPushButton('Cancel requests', self)
        self.cancel_button.setEnabled(False)

        self.analyze_button.clicked.connect(self.analyze_current)
        self.batch_button.clicked.connect(self.analyze_all)
        self.polish_selected_button.clicked.connect(self.polish_selected)
        self.polish_button.clicked.connect(self.polish_all)
        self.accept_button.clicked.connect(self.accept_suggestion)
        self.apply_manual_button.clicked.connect(self.apply_manual_edit)
        self.add_term_button.clicked.connect(self.add_selected_term)
        self.apply_all_manual_button.clicked.connect(self.apply_all_manual_edits)
        self.keep_button.clicked.connect(self.keep_translation)
        self.approve_yellow_button.clicked.connect(self.approve_yellow_rows)
        self.accept_all_button.clicked.connect(self.accept_all)
        self.save_button.clicked.connect(self.save_epub)
        self.cancel_button.clicked.connect(self.cancel_job)

        workflow_actions = QHBoxLayout()
        for button in (
            self.analyze_button, self.batch_button, self.cancel_button,
        ):
            workflow_actions.addWidget(button)
        workflow_actions.addStretch(1)

        self.polish_group = QGroupBox(
            'Experimental polish (optional)', self
        )
        self.polish_group.setToolTip(
            'Optional isolated surface pass. It does not receive the glossary '
            'or book instructions and can therefore change established terms. '
            'Review its proposals manually.'
        )
        polish_layout = QHBoxLayout(self.polish_group)
        polish_layout.setContentsMargins(7, 2, 7, 2)
        polish_warning = QLabel(
            'May change glossary terms; review manually.', self.polish_group
        )
        polish_warning.setToolTip(self.polish_group.toolTip())
        polish_layout.addWidget(polish_warning)
        polish_layout.addWidget(self.polish_selected_button)
        polish_layout.addWidget(self.polish_button)
        self.polish_group.setVisible(self.experimental_polishing)
        workflow_actions.addWidget(self.polish_group)

        result_actions = QHBoxLayout()
        for button in (
            self.accept_button, self.accept_all_button, self.keep_button,
        ):
            result_actions.addWidget(button)
        result_actions.addSpacing(14)
        for button in (
            self.apply_manual_button, self.apply_all_manual_button,
            self.add_term_button, self.approve_yellow_button,
        ):
            result_actions.addWidget(button)
        result_actions.addStretch(1)
        result_actions.addWidget(self.add_to_library)
        result_actions.addWidget(self.save_button)
        actions = QVBoxLayout()
        actions.addLayout(workflow_actions)
        actions.addLayout(result_actions)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status = QLabel('Choose two EPUB books.', self)
        footer = QHBoxLayout()
        footer.addWidget(self.status, 1)
        footer.addWidget(self.progress)

        layout = QVBoxLayout(self)
        layout.addLayout(books)
        layout.addWidget(center, 1)
        layout.addLayout(actions)
        layout.addLayout(footer)
        self._set_work_enabled(False)
        self.cancel_button.setEnabled(False)
        self._update_memory_profile_controls()
        localize_widget_tree(
            self, interface_settings.get('interface_language', 'auto')
        )

    def _panel(self, title, editor):
        group = QGroupBox(title, self)
        layout = QVBoxLayout(group)
        layout.addWidget(editor)
        return group

    def _sync_scroll(self, source_editor, value):
        if self._syncing_scroll:
            return
        source_bar = source_editor.verticalScrollBar()
        source_maximum = source_bar.maximum()
        ratio = value / source_maximum if source_maximum else 0.0
        self._syncing_scroll = True
        try:
            for editor in (
                self.original_edit, self.translation_edit, self.suggestion_edit
            ):
                if editor is source_editor:
                    continue
                target_bar = editor.verticalScrollBar()
                target_bar.setValue(round(ratio * target_bar.maximum()))
        finally:
            self._syncing_scroll = False

    def scroll_to_note_paragraph(self, paragraph_index):
        if not (0 <= self.current_index < len(self.chunks)):
            return
        count = len(self.chunks[self.current_index].items)
        if not (0 <= paragraph_index < count):
            return
        self._syncing_scroll = True
        try:
            for editor in (
                self.original_edit, self.translation_edit, self.suggestion_edit
            ):
                text = editor.toPlainText()
                separators = list(re.finditer(r'\n[ \t]*\n+', text))
                starts = [0] + [match.end() for match in separators]
                ends = [match.start() for match in separators] + [len(text)]
                if len(starts) != count:
                    continue
                cursor = editor.textCursor()
                cursor.setPosition(starts[paragraph_index])
                cursor.setPosition(
                    ends[paragraph_index], QTextCursor.MoveMode.KeepAnchor
                )
                editor.setTextCursor(cursor)
                editor.centerCursor()
        finally:
            self._syncing_scroll = False

    def open_settings(self):
        SettingsDialog(self, terminology=self.terminology).exec()
        settings = get_settings()
        self.experimental_polishing = bool(
            settings.get('experimental_polishing', False)
        )
        self.polish_group.setVisible(self.experimental_polishing)
        self._update_review_mode_label()
        language = settings.get('interface_language', 'auto')
        self.table.setHorizontalHeaderLabels([
            '#', tr('Original text', language), tr('Characters', language),
            tr('Status', language), tr('Severity', language),
            tr('Alignment', language),
        ])
        localize_widget_tree(self, language)
        self._update_manual_edit_count()

    def _update_review_mode_label(self):
        mode = str(get_settings().get('review_mode', 'full'))
        labels = {
            'terminology': 'Mode: strict terminology',
            'cleanup': 'Mode: terminology + cleanup',
            'full': 'Mode: experimental full proofreading',
        }
        label = labels.get(mode, labels['full'])
        self.review_mode_label.setProperty('tp_source_text', label)
        self.review_mode_label.setText(label)
        self.core_prompt_button.setEnabled(mode == 'full')

    def edit_core_prompt(self):
        values = get_settings()
        dialog = TextPromptDialog(
            'Core proofreading prompt', values['prompt'],
            'Universal instructions for every book. Book instructions and '
            'terminology memory are appended separately for each request.',
            self, reset_text=DEFAULT_PROMPT,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            save_settings({'prompt': dialog.text})

    def edit_book_prompt(self):
        if not self.terminology:
            return
        dialog = TextPromptDialog(
            'Book-specific user instructions', self.terminology.manual_prompt,
            'Optional instructions and manual glossary for this book. Mapping '
            'lines such as "Loamwalker -> Землеходець" become locked USER '
            'mappings and cannot be overwritten by the AI.',
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.terminology.manual_prompt = dialog.text
            self.terminology.apply_manual_prompt_mappings()
            self.terminology.save()
            self.terminology.sync_active_profile_prompts()

    def edit_auto_prompt(self):
        if self.terminology:
            AutoPromptDialog(self.terminology, self).exec()

    def open_terminology(self):
        if self.terminology:
            dialog = TerminologyDialog(
                self.terminology, self.scan_book_terms, self,
                ai_callback=lambda sources: self.analyze_terminology(
                    sources, deep=False, automatic=False
                ),
                deep_ai_callback=lambda sources: self.analyze_terminology(
                    sources, deep=True, automatic=False
                ),
            )
            self._terminology_dialog = dialog
            try:
                result = dialog.exec()
            finally:
                self._terminology_dialog = None
            if result == QDialog.DialogCode.Accepted:
                if (
                    self.terminology.active_profile and
                    self.terminology.sync_profile
                ):
                    self.status.setText(
                        'Terminology saved to this book and series profile '
                        '"%s". Automatic scans cannot overwrite old entries; '
                        'your explicit edits can.' %
                        self.terminology.active_profile
                    )
                else:
                    self.status.setText(
                        'Terminology saved to this book\'s private memory.'
                    )

    def _selected_memory_profile(self):
        return str(self.memory_profile_combo.currentData() or '')

    def _refresh_memory_profiles(self, selected=''):
        selected = str(selected or '')
        self._updating_memory_controls = True
        try:
            self.memory_profile_combo.clear()
            self.memory_profile_combo.addItem('No shared profile', '')
            for name in self.memory_profiles.names():
                self.memory_profile_combo.addItem(name, name)
            if selected:
                index = self.memory_profile_combo.findData(selected)
                if index >= 0:
                    self.memory_profile_combo.setCurrentIndex(index)
        finally:
            self._updating_memory_controls = False
        self._update_memory_profile_controls()

    def _update_memory_profile_controls(self, *_args):
        if self._updating_memory_controls:
            return
        selected = self._selected_memory_profile()
        loaded = self.terminology is not None
        active = self.terminology.active_profile if loaded else ''
        is_active = bool(selected and selected.casefold() == active.casefold())
        self.apply_memory_profile_button.setEnabled(loaded and bool(selected))
        self.save_memory_profile_button.setEnabled(loaded)
        self.delete_memory_profile_button.setEnabled(bool(selected))
        self.clear_book_memory_button.setEnabled(loaded)
        self._updating_memory_controls = True
        try:
            self.memory_profile_sync.setEnabled(loaded and is_active)
            self.memory_profile_sync.setChecked(
                bool(is_active and self.terminology.sync_profile)
            )
        finally:
            self._updating_memory_controls = False

    def apply_memory_profile(self):
        if not self.terminology:
            return
        name = self._selected_memory_profile()
        if not name:
            return
        book_series = str(self.original_doc.series or '').strip()
        if book_series and name.casefold() != book_series.casefold():
            answer = QMessageBox.question(
                self, 'Possible series-memory mismatch',
                'The EPUB identifies its series as "%s", but the selected '
                'memory profile is "%s". Applying it can send unrelated book '
                'instructions and terminology to the AI. Apply it anyway?' % (
                    book_series, name
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            payload = self.memory_profiles.load(name)
            self.terminology.apply_profile(name, payload, sync=True)
        except Exception as err:
            _message(
                self, QMessageBox.Icon.Critical,
                'Could not apply memory profile', str(err)
            )
            return
        self._update_memory_profile_controls()
        self.status.setText(
            'Applied series memory "%s". Existing profile terms are active; '
            'they are protected from this book. Click Scan book terms when '
            'you want to discover and highlight only new terms from this '
            'volume.' % name
        )

    def save_memory_profile_as(self):
        if not self.terminology:
            return
        suggested = (
            self.original_doc.series or self.original_doc.title or
            'Book series memory'
        )
        name, ok = QInputDialog.getText(
            self, 'Save series memory',
            'Profile name (normally the series title):',
            text=suggested,
        )
        name = str(name or '').strip()
        if not ok or not name:
            return
        if any(name.casefold() == value.casefold()
               for value in self.memory_profiles.names()):
            answer = QMessageBox.question(
                self, 'Replace memory profile',
                'Replace the existing profile "%s" with this book memory?' % name,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            self.memory_profiles.save_book(name, self.terminology)
            self.terminology.active_profile = name
            self.terminology.sync_profile = True
            self.terminology.save()
        except Exception as err:
            _message(
                self, QMessageBox.Icon.Critical,
                'Could not save memory profile', str(err)
            )
            return
        self._refresh_memory_profiles(name)
        self.status.setText('Saved series memory profile "%s".' % name)

    def delete_memory_profile(self):
        name = self._selected_memory_profile()
        if not name:
            return
        answer = QMessageBox.question(
            self, 'Delete memory profile',
            'Permanently delete the saved profile "%s"? Current book memory '
            'will be kept.' % name,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.memory_profiles.delete(name)
            if (self.terminology and
                    self.terminology.active_profile.casefold() == name.casefold()):
                self.terminology.detach_profile()
        except Exception as err:
            _message(
                self, QMessageBox.Icon.Critical,
                'Could not delete memory profile', str(err)
            )
            return
        self._refresh_memory_profiles()

    def memory_profile_sync_changed(self, checked):
        if self._updating_memory_controls or not self.terminology:
            return
        name = self._selected_memory_profile()
        if not name or name.casefold() != self.terminology.active_profile.casefold():
            return
        self.terminology.sync_profile = bool(checked)
        self.terminology.save()
        self.status.setText(
            'Series profile "%s" %s. Terminology Apply/Close saves %s.' % (
                name,
                'new-term accumulation enabled' if checked else
                'new-term accumulation disabled',
                'to the book; explicit edits and absent scanned keys also go '
                'to the profile' if checked else
                'only to this book',
            )
        )

    def clear_book_memory(self):
        if not self.terminology:
            return
        answer = QMessageBox.question(
            self, 'Clear book memory',
            'Clear this book\'s custom prompt, automatic notes, and complete '
            'terminology list? Saved series profiles will not be deleted.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.terminology.clear_book_memory()
        self._refresh_memory_profiles()
        self.status.setText(
            'Cleared this book memory. Saved series profiles were kept.'
        )

    def scan_book_terms(self, quiet=False):
        if not self.original_doc or not self.terminology:
            return 0
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            count = self.terminology.scan(
                unit.text for unit in self.original_doc.units
            )
        except Exception as err:
            if not quiet:
                _message(
                    self, QMessageBox.Icon.Critical,
                    'Terminology scan failed', str(err)
                )
            return 0
        finally:
            QApplication.restoreOverrideCursor()
        added = int(getattr(
            self.terminology, 'last_scan_added_count', 0
        ) or 0)
        profile_added = int(getattr(
            self.terminology, 'last_scan_profile_added_count', 0
        ) or 0)
        scan_summary = (
            'Local scan found %d candidates: %d genuinely new in this book; '
            '%d added to the selected series profile. No API request was used.'
            % (count, added, profile_added)
        )
        if count and not added:
            scan_summary += ' Every detected candidate already existed in memory.'
        self.status.setText(scan_summary)
        if not quiet:
            _message(
                self, QMessageBox.Icon.Information, 'Terminology scan complete',
                scan_summary
            )
        new_sources = list(getattr(
            self.terminology, 'last_scan_added_sources', ()
        ) or ())
        if new_sources:
            self.analyze_terminology(
                new_sources, deep=False, automatic=True
            )
        return count

    @staticmethod
    def _term_context_excerpt(text, term, maximum):
        text = re.sub(r'\s+', ' ', str(text or '')).strip()
        if len(text) <= maximum:
            return text
        index = text.casefold().find(str(term or '').casefold())
        if index < 0:
            return text[:maximum].rstrip() + '…'
        half = maximum // 2
        start = max(0, index - half)
        end = min(len(text), start + maximum)
        start = max(0, end - maximum)
        return (
            ('…' if start else '') + text[start:end].strip() +
            ('…' if end < len(text) else '')
        )

    def _terminology_candidates(self, sources, deep=False):
        candidates = []
        maximum_contexts = 8 if deep else 3
        maximum_chars = 1800 if deep else 900
        wanted = []
        seen = set()
        for source in sources or ():
            source = re.sub(r'\s+', ' ', str(source or '')).strip()
            key = source.casefold()
            if source and key not in seen:
                wanted.append(source)
                seen.add(key)
        for number, source in enumerate(wanted, 1):
            key = self.terminology._stored_term_key(source)
            term = self.terminology.terms.get(key, {}) if key else {}
            contexts = []
            for item in self.items:
                if not _source_occurs(item.original.text, source):
                    continue
                contexts.append({
                    'source': self._term_context_excerpt(
                        item.original.text, source, maximum_chars
                    ),
                    'translation': self._term_context_excerpt(
                        item.translation_text, source, maximum_chars
                    ),
                })
                if len(contexts) >= maximum_contexts:
                    break
            if not contexts:
                continue
            candidates.append({
                'id': 't%05d' % number,
                'source': source,
                'occurrences': int(term.get('count', 0) or 0),
                'contexts': contexts,
            })
        return candidates

    @staticmethod
    def _terminology_request_settings(settings, deep=False):
        """Use freer sampling only for terminology research requests."""
        values = dict(settings or {})
        try:
            configured_temperature = float(values.get('temperature', 0.2))
        except (TypeError, ValueError):
            configured_temperature = 0.2
        values['temperature'] = max(
            configured_temperature, 0.65 if deep else 0.40
        )
        return values

    def analyze_terminology(self, sources, deep=False, automatic=False):
        if not self.terminology or not self.original_doc:
            return False
        if self._term_thread is not None:
            if not automatic:
                _message(
                    self, QMessageBox.Icon.Information,
                    'Terminology analysis is running',
                    'Wait for the current terminology request to finish.'
                )
            return False
        settings = get_settings()
        if settings.get('provider') == 'local':
            message = (
                'Automatic terminology research is disabled for local models. '
                'Select Gemini, DeepSeek, or another cloud provider in AI settings.'
            )
            self.status.setText(message)
            if not automatic:
                _message(
                    self, QMessageBox.Icon.Information,
                    'Cloud terminology model required', message
                )
            return False
        try:
            ProofreaderClient(settings).validate()
        except Exception as err:
            self.status.setText(
                'Automatic terminology analysis skipped: %s' % err
            )
            if not automatic:
                _message(
                    self, QMessageBox.Icon.Warning,
                    'AI settings are incomplete', str(err)
                )
            return False
        requested = list(sources or ())
        if automatic:
            # Bracketed one-off tokens can bypass the local scan threshold.
            # Do not spend API requests on them automatically.
            requested = [
                source for source in requested
                if int(self.terminology.terms.get(
                    self.terminology._stored_term_key(source), {}
                ).get('count', 0) or 0) >= self.terminology.minimum_occurrences
            ]
        candidates = self._terminology_candidates(requested, deep=deep)
        if not candidates:
            if not automatic:
                _message(
                    self, QMessageBox.Icon.Information,
                    'No contextual candidates',
                    'No selected term was found in the aligned original text.'
                )
            return False
        batch_size = 12 if deep else 60
        batches = [
            candidates[index:index + batch_size]
            for index in range(0, len(candidates), batch_size)
        ]
        if not automatic:
            extra = (
                '\n\nGemini deep-check also enables Google Search grounding, '
                'which may consume separate search quota or billing. The '
                'terminology-only request uses a freer semantic-adaptation '
                'temperature; normal proofreading settings are unchanged.'
                if deep and settings.get('provider') == 'gemini' else ''
            )
            answer = QMessageBox.question(
                self,
                'Deep-check terminology' if deep else 'Check terminology with AI',
                'Send %d term(s) with book context in %d request(s)? Existing '
                'human canonicals will not be sent to the model or overwritten.%s'
                % (len(candidates), len(batches), extra),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        metadata = {
            'title': self.original_doc.title,
            'authors': list(self.original_doc.authors),
            'series': self.original_doc.series,
            'source_language': settings.get('source_language', ''),
            'target_language': settings.get('target_language', ''),
        }
        self._term_thread = QThread(self)
        self._term_error = ''
        terminology_settings = self._terminology_request_settings(
            settings, deep=deep
        )
        self._term_worker = TerminologyWorker(
            terminology_settings, batches, metadata,
            use_search=bool(deep and settings.get('provider') == 'gemini'),
        )
        self._term_worker.moveToThread(self._term_thread)
        self._term_thread.started.connect(self._term_worker.run)
        self._term_worker.result.connect(self._receive_terminology_results)
        self._term_worker.progress.connect(
            lambda done, total: self.status.setText(
                'Terminology AI analysis: request %d/%d completed.' %
                (done, total)
            )
        )
        self._term_worker.failed.connect(self._terminology_failed)
        self._term_worker.finished.connect(self._term_thread.quit)
        self._term_worker.finished.connect(self._term_worker.deleteLater)
        self._term_thread.finished.connect(self._terminology_finished)
        self._term_thread.finished.connect(self._term_thread.deleteLater)
        self.status.setText(
            'Started %s terminology analysis for %d term(s) in %d request(s).'
            % ('automatic' if automatic else 'manual', len(candidates), len(batches))
        )
        self._term_thread.start()
        return True

    def _receive_terminology_results(self, results):
        stored = 0
        for result in results or ():
            if self.terminology.set_ai_suggestion(
                result.get('source', ''), result.get('canonical', ''),
                gender=result.get('gender', ''),
                category=result.get('category', ''),
                confidence=result.get('confidence', 0.0),
                notes=result.get('notes', ''),
            ):
                stored += 1
        if stored:
            self.terminology.save()
            dialog = self._terminology_dialog
            if dialog is not None and dialog.isVisible():
                dialog.populate()

    def _terminology_failed(self, message):
        self._term_error = str(message)
        self.status.setText('Terminology AI analysis failed: %s' % message)
        _message(
            self, QMessageBox.Icon.Warning,
            'Terminology AI analysis failed', message
        )

    def _terminology_finished(self):
        self._term_worker = None
        self._term_thread = None
        if not self._term_error:
            self.status.setText(
                'Terminology AI analysis finished. AI proposals are stored in '
                'their own column; human canonicals still have priority.'
            )

    def load_books(self):
        original_path = self.original_picker.path
        translation_path = self.translation_picker.path
        if not original_path or not translation_path:
            _message(
                self, QMessageBox.Icon.Warning, 'Choose books',
                'Choose one original EPUB and one translated EPUB.'
            )
            return
        if os.path.normcase(original_path) == os.path.normcase(translation_path):
            _message(
                self, QMessageBox.Icon.Warning, 'Same file',
                'The original and translation must be different files.'
            )
            return
        settings = get_settings()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            original = EpubDocument(original_path)
            translation = EpubDocument(translation_path)
        except Exception as err:
            _message(self, QMessageBox.Icon.Critical, 'EPUB error', str(err))
            return
        finally:
            QApplication.restoreOverrideCursor()

        original_script = _dominant_script(original)
        translation_script = _dominant_script(translation)
        target_is_cyrillic = ProofreaderClient(
            settings
        )._target_uses_cyrillic()
        role_notice = ''
        if (
            target_is_cyrillic and original_script == 'cyrillic' and
            translation_script == 'latin'
        ):
            answer = QMessageBox.question(
                self, 'Books appear reversed',
                'The EPUB selected as Original is predominantly Cyrillic, '
                'while the Translation is predominantly Latin. Swap their '
                'roles automatically?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            original, translation = translation, original
            original_path, translation_path = translation_path, original_path
            self.original_picker.path_edit.setText(original_path)
            self.translation_picker.path_edit.setText(translation_path)
            role_notice = ' Reversed book roles were corrected automatically.'
        elif (
            target_is_cyrillic and original_script == 'cyrillic' and
            translation_script == 'cyrillic'
        ):
            answer = QMessageBox.question(
                self, 'Original EPUB may be a translation',
                'Both selected EPUB files are predominantly Cyrillic. The '
                'terminology scanner reads only the Original/source EPUB, so '
                'this usually means that two translated editions were '
                'selected and source names such as Zac will not be found. '
                'Continue anyway?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            items, warnings = align_documents(
                original, translation,
                auto_cleanup_merged=bool(
                    settings.get(
                        'auto_cleanup_merged_prefixes', True
                    )
                ),
            )
        except Exception as err:
            _message(self, QMessageBox.Icon.Critical, 'EPUB error', str(err))
            return
        finally:
            QApplication.restoreOverrideCursor()

        # Loading a new pair must sever every connection to the editors and
        # drafts from the previous pair.  In particular, setCurrentCell() below
        # emits currentCellChanged; if current_index still points at the old
        # chunks, change_row() would capture the old pane contents into the new
        # book before displaying row 1.
        self._updating = True
        try:
            self.current_index = -1
            self.original_edit.clear()
            self.translation_edit.clear()
            self.suggestion_edit.clear()
            self.notes_edit.clear()
        finally:
            self._updating = False
        self.original_doc = original
        self.translation_doc = translation
        translation_name = os.path.basename(translation.path).casefold()
        self._input_stage = _metadata_stage(
            translation.title, translation.tags
        )
        self._input_proofread_pass = _proofread_pass(
            translation.title, translation.tags
        )
        self._last_applied_stage = ''
        self._active_review_mode = ''
        self._suggestion_modes.clear()
        self._selection_cache.clear()
        self._last_target_selection = ''
        self._polish_ready = bool(
            self._input_stage in {'proofreaded', 'polished'} or
            '_proofread' in translation_name or '_polished' in translation_name
        )
        remember_book_pair(
            original_path, translation_path,
            original.title, translation.title,
        )
        language_guard = target_is_cyrillic
        self.terminology = BookTerminology(
            original_path, translation_path,
            forbid_latin_targets=language_guard,
        )
        existing_book_memory = bool(
            self.terminology.terms or self.terminology.manual_prompt or
            self.terminology.auto_prompt
        )
        self.terminology.apply_manual_prompt_mappings()
        self.terminology.save()
        self.items = items
        self._item_indices = {id(item): index for index, item in enumerate(items)}
        self.chunks = build_review_chunks(
            items, get_settings().get('chunk_chars', 8000)
        )
        self._total_original_chars = sum(
            chunk.original_chars for chunk in self.chunks
        )
        self._total_translation_chars = sum(
            chunk.translation_chars for chunk in self.chunks
        )
        self._translation_drafts.clear()
        self._suggestion_drafts.clear()
        self._manual_dirty_indices.clear()
        self._suggestion_dirty_indices.clear()
        self._committed_translation_values = {
            index: self._chunk_value(chunk, 'translation_text')
            for index, chunk in enumerate(self.chunks)
        }
        self._committed_suggestion_values = {
            index: self._chunk_value(chunk, 'suggestion')
            for index, chunk in enumerate(self.chunks)
        }
        self._update_manual_edit_count()
        self._populate_table()
        self._set_work_enabled(True)
        for button in (
            self.book_prompt_button, self.scan_terms_button,
            self.terminology_button, self.auto_prompt_button,
        ):
            button.setEnabled(True)
        selected_profile = self.terminology.active_profile
        if not selected_profile and self.original_doc.series:
            for name in self.memory_profiles.names():
                if name.casefold() == self.original_doc.series.casefold():
                    selected_profile = name
                    break
        self._refresh_memory_profiles(selected_profile)
        if self.chunks:
            self.table.setCurrentCell(0, 0)
            # QTableWidget may keep cell (0, 0) current while its rows are
            # replaced, in which case setCurrentCell emits no signal.  Display
            # the first new chunk explicitly instead of leaving blank editors.
            if self.current_index != 0:
                self.change_row(0, 0, -1, -1)
        memory_status = 'Started separate private memory for this book.'
        if self.terminology.active_profile:
            memory_status = 'Shared series profile "%s" is active%s.' % (
                self.terminology.active_profile,
                '; only new keys are accumulated automatically'
                if self.terminology.sync_profile else '',
            )
        elif existing_book_memory:
            memory_status = (
                'Loaded existing private memory for this book; no shared '
                'series profile is active.'
            )
        elif selected_profile:
            memory_status = (
                'Suggested series profile "%s" is selected but has not been '
                'applied.' % selected_profile
            )
        if not self.terminology.terms:
            memory_status += (
                ' No terminology scan was run automatically; apply a series '
                'profile first if needed, then click Scan book terms.'
            )
        repaired_count = sum(
            bool(item.removed_duplicate_text) for item in items
        )
        repair_status = (
            ' Automatically repaired %d merged duplicate block%s before the '
            'final alignment check.' % (
                repaired_count, '' if repaired_count == 1 else 's'
            ) if repaired_count else ''
        )
        self.status.setText(
            'Created %d chunks from %d paragraphs; about %d API requests for '
            'Review all.%s %s' % (
                len(self.chunks), len(items),
                sum(self._chunk_is_safe(chunk) for chunk in self.chunks),
                repair_status + role_notice,
                memory_status,
            )
        )
        if warnings:
            _message(
                self, QMessageBox.Icon.Warning, 'Incomplete alignment',
                'After automatic content repairs, %d unresolved alignment '
                'issue%s remain. Only these remaining issues are listed below.' % (
                    len(warnings), '' if len(warnings) == 1 else 's'
                ),
                '\n'.join(warnings[:200]),
            )

    def _populate_table(self):
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(self.chunks))
            for row, chunk in enumerate(self.chunks):
                self.table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
                self.table.setItem(row, 1, QTableWidgetItem(chunk.preview))
                self.table.setItem(row, 2, QTableWidgetItem(
                    '%d + %d' % (
                        chunk.original_chars, chunk.translation_chars
                    )
                ))
                self.table.setItem(row, 3, QTableWidgetItem('pending'))
                self.table.setItem(row, 4, QTableWidgetItem(''))
                self.table.setItem(row, 5, QTableWidgetItem(''))
                self._update_row(row)
        finally:
            self.table.setUpdatesEnabled(True)
        self.table.resizeColumnToContents(0)
        for column in (0, 2, 3, 4, 5):
            self.table.resizeColumnToContents(column)

    def _set_work_enabled(self, enabled):
        for button in (
            self.analyze_button, self.batch_button, self.accept_button,
            self.polish_selected_button, self.polish_button,
            self.apply_manual_button, self.add_term_button,
            self.apply_all_manual_button,
            self.keep_button,
            self.approve_yellow_button,
            self.accept_all_button, self.save_button,
        ):
            button.setEnabled(enabled)
        self.polish_button.setEnabled(bool(
            enabled and self._polish_ready and self.experimental_polishing
        ))
        self.polish_selected_button.setEnabled(
            bool(
                enabled and self._polish_ready
                and self.experimental_polishing
            )
        )

    def _update_manual_edit_count(self):
        count = len(
            self._manual_dirty_indices | self._suggestion_dirty_indices
        )
        base = 'Apply all manual edits'
        self.apply_all_manual_button.setProperty('tp_source_text', base)
        language = get_settings().get('interface_language', 'en')
        self.apply_all_manual_button.setText(
            tr(base, language) + (' (%d)' % count if count else '')
        )

    @staticmethod
    def _join_values(values):
        values = [str(value or '').strip() for value in values]
        if not any(values):
            return ''
        return '\n\n'.join(values)

    @staticmethod
    def _split_values(text, expected_count):
        text = str(text or '').strip()
        if expected_count == 1:
            # A blank one-item draft is an intentional deletion, chiefly for
            # unrelated/merged translator blocks that have no source pair.
            return [text]
        if not text:
            return None
        values = re.split(r'\n[ \t]*\n+', text)
        return values if len(values) == expected_count else None

    def _chunk_value(self, chunk, attribute):
        if attribute == 'original.text':
            return self._join_values(item.original.text for item in chunk.items)
        return self._join_values(
            getattr(item, attribute) for item in chunk.items
        )

    @staticmethod
    def _chunk_is_safe(chunk):
        return all(
            item.structural_alignment in {
                'exact', 'repaired-duplicate-prefix',
            } for item in chunk.items
        )

    @staticmethod
    def _chunk_state(chunk):
        states = {item.state for item in chunk.items}
        if 'blocked' in states:
            return 'blocked'
        if states == {'manual'}:
            return 'manual'
        if states == {'accepted'}:
            return 'accepted'
        if states == {'kept'}:
            return 'kept'
        if states <= {'accepted', 'kept'}:
            return 'completed'
        if 'analyzed' in states:
            return 'analyzed'
        return 'pending'

    @staticmethod
    def _chunk_severity(chunk):
        priorities = {'': 0, 'none': 1, 'minor': 2, 'major': 3}
        return max(
            (item.severity for item in chunk.items),
            key=lambda value: priorities.get(value, 0),
            default='',
        )

    @staticmethod
    def _chunk_alignment(chunk):
        if chunk.items and all(item.manual_override for item in chunk.items):
            return 'manual'
        priorities = {
            'exact': 0, 'ok': 1, 'uncertain': 2, 'positional': 3,
            'length-outlier': 4, 'mismatch': 5, 'duplicate': 6,
            'translation-overflow': 7,
            'repaired-duplicate-prefix': 1,
        }
        values = []
        for item in chunk.items:
            alignment = item.ai_alignment or item.structural_alignment
            if item.structural_alignment != 'exact' and alignment == 'ok':
                alignment = item.structural_alignment
            values.append(alignment)
        return max(
            values,
            key=lambda value: priorities.get(value, 0),
            default='exact',
        )

    def _chunk_pairs(self, chunk):
        return [{
            'id': 'p%06d' % (self._item_indices[id(item)] + 1),
            'original': item.original.text,
            'translation': item.translation_text,
        } for item in chunk.items]

    def _work_item(self, index, include_terminology=True):
        pairs = self._chunk_pairs(self.chunks[index])
        source_text = '\n\n'.join(pair['original'] for pair in pairs)
        terminology = (
            self.terminology.relevant_for_text(source_text)
            if self.terminology and include_terminology else []
        )
        return index, pairs, terminology

    def _apply_draft(self, index, attribute, drafts):
        chunk = self.chunks[index]
        text = drafts.get(index, '')
        values = self._split_values(text, len(chunk.items))
        if values is None:
            return False
        for item, value in zip(chunk.items, values):
            setattr(item, attribute, value.strip())
        return True

    def _commit_translation_drafts(self, indices=None):
        invalid = []
        selected = set(indices) if indices is not None else None
        for index in self._translation_drafts:
            if selected is not None and index not in selected:
                continue
            if not self._apply_draft(
                index, 'translation_text', self._translation_drafts
            ):
                invalid.append(index + 1)
        return invalid

    def _selected_chunk_indices(self):
        rows = self.table.selectionModel().selectedRows()
        indices = sorted({row.row() for row in rows})
        if not indices and 0 <= self.current_index < len(self.chunks):
            indices = [self.current_index]
        return indices

    def change_row(self, current_row, _current_col, _old_row, _old_col):
        if current_row < 0 or current_row >= len(self.chunks):
            return
        if current_row != self.current_index:
            self.capture_edits()
            self._selection_cache.clear()
            self._last_target_selection = ''
        self.current_index = current_row
        chunk = self.chunks[current_row]
        self._updating = True
        try:
            self.original_edit.setPlainText(self._chunk_value(chunk, 'original.text'))
            self.translation_edit.setPlainText(
                self._translation_drafts.get(
                    current_row, self._chunk_value(chunk, 'translation_text')
                )
            )
            self.suggestion_edit.setPlainText(
                self._suggestion_drafts.get(
                    current_row, self._chunk_value(chunk, 'suggestion')
                )
            )
            notes = [
                '[%d] %s' % (number, item.notes)
                for number, item in enumerate(chunk.items, 1) if item.notes
            ]
            self.notes_edit.setPlainText('\n\n'.join(notes))
            self.removed_text_button.setEnabled(any(
                item.removed_duplicate_text for item in chunk.items
            ))
        finally:
            self._updating = False
        self.status.setText(
            'Chunks: %d/%d · Characters: %d + %d / %d + %d · Paragraphs: %d' % (
                current_row + 1, len(self.chunks),
                chunk.original_chars, chunk.translation_chars,
                sum(value.original_chars for value in self.chunks),
                sum(value.translation_chars for value in self.chunks),
                len(chunk.items),
            )
        )

    def inspect_removed_text(self):
        if not (0 <= self.current_index < len(self.chunks)):
            return
        removed = [
            '[Block %d]\n%s' % (number, item.removed_duplicate_text)
            for number, item in enumerate(
                self.chunks[self.current_index].items, 1
            ) if item.removed_duplicate_text
        ]
        if not removed:
            return
        _message(
            self, QMessageBox.Icon.Information,
            'Automatically removed duplicate prefix',
            'The text below was removed only from the in-memory/output copy. '
            'The input EPUB is unchanged. Disable automatic cleanup in AI '
            'settings and reload the books to keep the original block.',
            '\n\n'.join(removed),
        )

    def capture_edits(self):
        if self._updating or not (0 <= self.current_index < len(self.chunks)):
            return
        self._translation_drafts[self.current_index] = (
            self.translation_edit.toPlainText().strip()
        )
        if self._translation_drafts[self.current_index] != (
            self._committed_translation_values.get(self.current_index, '')
        ):
            self._manual_dirty_indices.add(self.current_index)
        else:
            self._manual_dirty_indices.discard(self.current_index)
        self._suggestion_drafts[self.current_index] = (
            self.suggestion_edit.toPlainText().strip()
        )
        if self._suggestion_drafts[self.current_index] != (
            self._committed_suggestion_values.get(self.current_index, '')
        ):
            self._suggestion_dirty_indices.add(self.current_index)
        else:
            self._suggestion_dirty_indices.discard(self.current_index)
        self._update_manual_edit_count()
        self._apply_draft(
            self.current_index, 'translation_text', self._translation_drafts
        )
        if self._suggestion_drafts[self.current_index]:
            self._apply_draft(
                self.current_index, 'suggestion', self._suggestion_drafts
            )

    def analyze_current(self):
        indices = self._selected_chunk_indices()
        if not indices:
            return
        self.capture_edits()
        invalid = self._commit_translation_drafts(indices)
        if invalid:
            _message(
                self, QMessageBox.Icon.Warning, 'Paragraph boundaries changed',
                'Keep exactly one blank-line-separated translation paragraph for '
                'each original paragraph in these chunks: %s.' % ', '.join(
                    map(str, invalid[:20])
                )
            )
            return
        risky = [
            index for index in indices
            if not self._chunk_is_safe(self.chunks[index])
        ]
        if risky:
            answer = QMessageBox.question(
                self, 'Risky chunks selected',
                '%d of %d selected chunks may not have a correct counterpart in '
                'the original. Send all selected chunks to the AI anyway?' % (
                    len(risky), len(indices)
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._start_job([
            self._work_item(index)
            for index in indices
        ])

    def analyze_all(self):
        selected_profile = self._selected_memory_profile()
        active_profile = (
            self.terminology.active_profile if self.terminology else ''
        )
        if selected_profile and selected_profile.casefold() != active_profile.casefold():
            answer = QMessageBox.question(
                self, 'Series memory profile is not applied',
                'The series-memory profile "%s" is selected but has not been '
                'applied to this book. Review all would run without its '
                'terminology and book instructions. Continue anyway?' %
                selected_profile,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.capture_edits()
        invalid = self._commit_translation_drafts()
        if invalid:
            _message(
                self, QMessageBox.Icon.Warning, 'Paragraph boundaries changed',
                'These chunks no longer have the original number of blank-line-'
                'separated translation paragraphs: %s.' % ', '.join(
                    map(str, invalid[:20])
                )
            )
            return
        all_pending = [
            self._work_item(index)
            for index, chunk in enumerate(self.chunks)
            if self._chunk_state(chunk) == 'pending'
        ]
        pending = [
            entry for entry in all_pending
            if self._chunk_is_safe(self.chunks[entry[0]])
        ]
        skipped = len(all_pending) - len(pending)
        if not pending:
            _message(
                self, QMessageBox.Icon.Information, 'Review complete',
                'There are no safe unreviewed chunks. Risky chunks can be '
                'opened and reviewed individually.'
            )
            return
        answer = QMessageBox.question(
            self, 'Batch review',
            'Send %d safe chunks in %d API requests? Risky chunks skipped: '
            '%d. This may incur API charges.' % (
                len(pending), len(pending), skipped
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_job(pending)

    def polish_selected(self):
        if not self._polish_ready or not self.chunks:
            return
        indices = self._selected_chunk_indices()
        if not indices:
            return
        self.capture_edits()
        self._polish_indices(indices, selected=True)

    def polish_all(self):
        if not self._polish_ready or not self.chunks:
            return
        self.capture_edits()
        # Resume rather than restart after a quota/provider failure. Successful
        # polish results keep their mode marker; failed rows do not. An explicit
        # Polish selected remains available when the user really wants a rerun.
        indices = [
            index for index in range(len(self.chunks))
            if self._suggestion_modes.get(index) != 'polish'
        ]
        self._polish_indices(indices, selected=False)

    def _polish_indices(self, requested_indices, selected=False):
        requested = sorted(set(requested_indices))
        indices = [
            index for index in requested
            if self._chunk_is_safe(self.chunks[index]) and
            index not in self._manual_dirty_indices and
            index not in self._suggestion_dirty_indices
        ]
        # Risky rows and unapplied edits are deliberately skipped, so their
        # paragraph shape cannot block otherwise safe rows.
        invalid = self._commit_translation_drafts(indices)
        if invalid:
            _message(
                self, QMessageBox.Icon.Warning,
                'Cannot start final polish',
                'Keep the original number of blank-line-separated paragraphs '
                'in these chunks first: %s.' % ', '.join(
                    map(str, invalid[:20])
                )
            )
            return
        skipped = len(requested) - len(indices)
        if not indices:
            _message(
                self, QMessageBox.Icon.Information, 'Nothing to polish',
                'No structurally safe selected chunks are available.' if selected
                else 'Every safe chunk already has a polish result, or contains '
                'an unapplied edit. Select rows and use Polish selected to rerun '
                'them explicitly.'
            )
            return
        answer = QMessageBox.question(
            self,
            'Polish selected chunks' if selected else 'Resume surface polish',
            'Polish %d safe %s in %d API requests? Risky or unapplied chunks '
            'skipped: %d.\n\nThis pass forces temperature 0.1 and sends only '
            'each aligned original/translation pair. It ignores the Core '
            'prompt, Book instructions, Book memory, and glossary. Each chunk '
            'is independent; no whole-book context is lost by selecting only '
            'failed rows.' % (
                len(indices),
                'selected chunks' if selected else 'remaining chunks',
                len(indices), skipped,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_job([
                self._work_item(index, include_terminology=False)
                for index in indices
            ], review_mode='polish')

    def _start_job(self, work_items, review_mode=None):
        if self._thread is not None:
            return
        try:
            client = ProofreaderClient(get_settings())
            client.validate()
        except APIError as err:
            _message(
                self, QMessageBox.Icon.Warning, 'Configure AI', str(err)
            )
            self.open_settings()
            return
        self._set_work_enabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, len(work_items))
        self.progress.setValue(0)
        self.status.setText('Running API review…')

        self._thread = QThread(self)
        worker_settings = get_settings()
        if review_mode == 'polish':
            worker_settings['review_mode'] = 'polish'
            worker_settings['temperature'] = 0.1
            worker_settings['auto_repair_suspicious_responses'] = False
            worker_settings['book_prompt'] = ''
            worker_settings['auto_prompt'] = ''
        elif self.terminology:
            worker_settings['book_prompt'] = (
                self.terminology.narrative_instructions()
            )
            worker_settings['auto_prompt'] = self.terminology.auto_prompt
        self._active_review_mode = str(
            worker_settings.get('review_mode', 'full')
        ).strip().lower()
        self._worker = ProofreadWorker(worker_settings, work_items)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.result.connect(self._receive_result)
        self._worker.progress.connect(self._receive_progress)
        self._worker.message.connect(self.status.setText)
        self._worker.item_failed.connect(self._receive_item_failed)
        self._worker.failed.connect(self._job_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._job_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _receive_result(self, index, result):
        chunk = self.chunks[index]
        for item, item_result in zip(chunk.items, result):
            item.suggestion = item_result['revised']
            repair_note = (
                'A high-confidence merged duplicate prefix was removed locally '
                'before this request.'
                if item.removed_duplicate_text else ''
            )
            item.notes = '\n\n'.join(filter(None, (
                repair_note, item_result['notes'],
            )))
            item.severity = item_result['severity']
            item.ai_alignment = item_result.get('alignment', 'uncertain')
            item.state = 'analyzed'
            item.manual_override = False
        self._suggestion_drafts.pop(index, None)
        self._committed_suggestion_values[index] = self._chunk_value(
            chunk, 'suggestion'
        )
        self._suggestion_modes[index] = self._active_review_mode or 'full'
        self._suggestion_dirty_indices.discard(index)
        self._update_row(index)
        if index == self.current_index:
            self.change_row(index, 0, index, 0)

    def _receive_progress(self, current, total):
        self.progress.setMaximum(total)
        self.progress.setValue(current)
        self.status.setText('Reviewed %d of %d.' % (current, total))

    def _receive_item_failed(self, index, message):
        chunk = self.chunks[index]
        for item in chunk.items:
            item.state = 'blocked'
            item.ai_alignment = 'mismatch'
            item.notes = '\n\n'.join(filter(None, (
                'A high-confidence merged duplicate prefix was removed locally '
                'before this request.' if item.removed_duplicate_text else '',
                message,
            )))
            item.suggestion = ''
        self._suggestion_drafts.pop(index, None)
        self._committed_suggestion_values[index] = ''
        self._suggestion_modes.pop(index, None)
        self._suggestion_dirty_indices.discard(index)
        self._update_row(index)
        if index == self.current_index:
            self.change_row(index, 0, index, 0)

    def _job_failed(self, message):
        _message(self, QMessageBox.Icon.Critical, 'API error', message)

    def _job_finished(self):
        self._thread = None
        self._worker = None
        self._active_review_mode = ''
        self._set_work_enabled(bool(self.chunks))
        self.cancel_button.setEnabled(False)
        self.status.setText('Review completed or stopped.')

    def cancel_job(self):
        if self._worker is not None:
            self._worker.cancel()
            self.status.setText(
                'Cancelling… the current HTTP request will finish at its timeout.'
            )

    def _update_row(self, index):
        chunk = self.chunks[index]
        self.table.item(index, 2).setText(
            '%d + %d' % (chunk.original_chars, chunk.translation_chars)
        )
        state = self._chunk_state(chunk)
        states = {
            'pending': 'pending',
            'analyzed': 'suggestion ready',
            'accepted': 'accepted',
            'manual': 'manual edit applied',
            'kept': 'kept',
            'completed': 'completed',
            'blocked': 'blocked',
        }
        severities = {
            'none': 'none',
            'minor': 'minor',
            'major': 'major',
        }
        severity = self._chunk_severity(chunk)
        self.table.item(index, 3).setText(states.get(state, state))
        self.table.item(index, 4).setText(
            severities.get(severity, severity)
        )
        alignment = self._chunk_alignment(chunk)
        alignments = {
            'exact': 'structural',
            'positional': 'check',
            'length-outlier': 'unusual length',
            'duplicate': 'possible duplicate',
            'translation-overflow': 'extra / merged translation',
            'repaired-duplicate-prefix': 'duplicate prefix removed',
            'ok': 'confirmed',
            'uncertain': 'uncertain',
            'mismatch': 'mismatch',
            'manual': 'manual override',
        }
        self.table.item(index, 5).setText(
            alignments.get(alignment, alignment)
        )
        if (
            state == 'blocked' or alignment in (
                'mismatch', 'duplicate', 'translation-overflow'
            )
        ):
            brush = QBrush(QColor(255, 90, 90, 95))
        elif (
            alignment in ('positional', 'length-outlier', 'uncertain')
        ):
            brush = QBrush(QColor(255, 205, 64, 95))
        else:
            brush = QBrush()
        for column in range(self.table.columnCount()):
            self.table.item(index, column).setBackground(brush)

    def accept_suggestion(self):
        if not (0 <= self.current_index < len(self.chunks)):
            return
        self.capture_edits()
        chunk = self.chunks[self.current_index]
        if not self._apply_draft(
            self.current_index, 'suggestion', self._suggestion_drafts
        ):
            _message(
                self, QMessageBox.Icon.Warning, 'Paragraph boundaries changed',
                'Keep exactly one blank-line-separated suggestion for each '
                'original paragraph before accepting this chunk.'
            )
            return
        alignment = self._chunk_alignment(chunk)
        explicitly_resolving_risk = (
            not self._chunk_is_safe(chunk) or
            alignment in ('uncertain', 'mismatch')
        )
        if explicitly_resolving_risk:
            answer = QMessageBox.question(
                self, 'Uncertain alignment',
                'This pair may be mismatched. Accept the suggestion despite '
                'the duplication risk?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        for item in chunk.items:
            item.translation_text = item.suggestion
            item.state = 'accepted'
            item.manual_override = explicitly_resolving_risk
            if explicitly_resolving_risk:
                item.ai_alignment = 'manual'
        self._translation_drafts[self.current_index] = self._chunk_value(
            chunk, 'translation_text'
        )
        self._committed_translation_values[self.current_index] = (
            self._translation_drafts[self.current_index]
        )
        self._manual_dirty_indices.discard(self.current_index)
        self._committed_suggestion_values[self.current_index] = (
            self._chunk_value(chunk, 'suggestion')
        )
        self._suggestion_dirty_indices.discard(self.current_index)
        self._mark_applied_stage(self._suggestion_stage(self.current_index))
        self._update_manual_edit_count()
        self._update_row(self.current_index)
        self._updating = True
        self.translation_edit.setPlainText(
            self._translation_drafts[self.current_index]
        )
        self._updating = False

    @staticmethod
    def _selected_editor_text(editor):
        selected = editor.textCursor().selectedText().replace('\u2029', ' ')
        return re.sub(r'\s+', ' ', selected).strip()

    def _remember_editor_selection(self, name, editor):
        if self._updating or not (0 <= self.current_index < len(self.chunks)):
            return
        value = self._selected_editor_text(editor)
        if not value:
            return
        self._selection_cache[name] = (self.current_index, value)
        if name in {'translation', 'suggestion'}:
            self._last_target_selection = name

    def _cached_editor_selection(self, name, editor):
        live = self._selected_editor_text(editor)
        if live:
            self._selection_cache[name] = (self.current_index, live)
            return live
        row, value = self._selection_cache.get(name, (-1, ''))
        return value if row == self.current_index else ''

    def _store_user_term(self, source, suggested_target=''):
        if not self.terminology:
            return False
        source = re.sub(r'\s+', ' ', str(source or '')).strip()
        suggested_target = re.sub(
            r'\s+', ' ', str(suggested_target or '')
        ).strip()
        if not source:
            return False
        existing_key = self.terminology._stored_term_key(source)
        existing = (
            self.terminology.terms.get(existing_key, {})
            if existing_key else {}
        )
        old_target = str(existing.get('canonical', '')).strip()
        target, ok = QInputDialog.getText(
            self, 'Add USER glossary term',
            'Source: %s\n\nCanonical translation (use the dictionary/base '
            'form; the AI will inflect it in context):' % source,
            QLineEdit.EchoMode.Normal, suggested_target or old_target,
        )
        if not ok:
            self.status.setText('Glossary addition cancelled; nothing changed.')
            return False
        target = target.strip()
        replacing = bool(
            old_target and old_target.casefold() != target.casefold()
        )
        already_same = bool(
            old_target and old_target.casefold() == target.casefold()
        )
        if replacing:
            answer = QMessageBox.question(
                self, 'Replace existing glossary term?',
                'Replace the existing human mapping?\n\n%s\n%s → %s' % (
                    source, old_target, target
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.status.setText(
                    'Glossary replacement declined; the existing mapping was kept.'
                )
                return False
        if not self.terminology.set_user(source, target):
            _message(
                self, QMessageBox.Icon.Warning, 'Term was not added',
                'Enter a non-empty target-language canonical form. For a '
                'Cyrillic target, a source-language/Latin form is not accepted.'
            )
            return False
        self.terminology.save()
        self._update_memory_profile_controls()
        destination = (
            'the current book and series profile "%s"' %
            self.terminology.active_profile
            if self.terminology.active_profile and self.terminology.sync_profile
            else 'the current book memory only'
        )
        action = (
            'Replaced' if replacing else
            'Confirmed existing' if already_same else
            'Added'
        )
        message = (
            '%s USER glossary mapping: %s → %s. Saved to %s. Review this '
            'chunk again to apply it throughout the matching source contexts.'
            % (action, source, target, destination)
        )
        self.status.setText(message)
        _message(
            self, QMessageBox.Icon.Information,
            'Glossary mapping %s' % (
                'replaced' if replacing else
                'confirmed' if already_same else
                'added'
            ),
            message,
        )
        return True

    def add_selected_term(self):
        if not self.terminology or not (
            0 <= self.current_index < len(self.chunks)
        ):
            return
        self.capture_edits()
        source = self._cached_editor_selection(
            'original', self.original_edit
        )
        translation_target = self._cached_editor_selection(
            'translation', self.translation_edit
        )
        suggestion_target = self._cached_editor_selection(
            'suggestion', self.suggestion_edit
        )
        if self._last_target_selection == 'suggestion' and suggestion_target:
            target = suggestion_target
        elif self._last_target_selection == 'translation' and translation_target:
            target = translation_target
        else:
            target = suggestion_target or translation_target
        if not source:
            source_text = self._chunk_value(
                self.chunks[self.current_index], 'original.text'
            )
            unresolved = self.terminology.unresolved_for_text(source_text)
            labels = [str(term.get('source', '')) for term in unresolved]
            if not labels:
                _message(
                    self, QMessageBox.Icon.Information,
                    'No source term selected',
                    'Select the source-language term in the Original pane first. '
                    'No unresolved glossary candidate was found in this chunk.'
                )
                return
            source, ok = QInputDialog.getItem(
                self, 'Choose source term', 'Unresolved term in this chunk:',
                labels, 0, False,
            )
            if not ok:
                return
        self._store_user_term(source, target)

    def _offer_manual_glossary_terms(self, chunk, before_text, after_text):
        if not self.terminology or before_text == after_text:
            return 0
        before_values = self._split_values(before_text, len(chunk.items))
        after_values = self._split_values(after_text, len(chunk.items))
        if before_values is None or after_values is None:
            return 0
        proposals = []
        for item, before, after in zip(
            chunk.items, before_values, after_values
        ):
            replacements = _manual_word_replacements(before, after)
            if not replacements:
                continue
            unresolved = self.terminology.unresolved_for_text(
                item.original.text
            )
            direct = []
            for term in unresolved:
                alternatives = term.get('alternatives', {}) or {}
                alternative_keys = {
                    ' '.join(EDIT_WORD_RE.findall(value)).casefold()
                    for value in alternatives
                }
                for old, new in replacements:
                    old_key = ' '.join(
                        EDIT_WORD_RE.findall(old)
                    ).casefold()
                    if old_key and old_key in alternative_keys:
                        direct.append((term.get('source', ''), new))
            if direct:
                proposals.extend(direct)
            elif len(unresolved) == 1 and len(replacements) == 1:
                proposals.append((
                    unresolved[0].get('source', ''), replacements[0][1]
                ))
        added = 0
        seen = set()
        for source, target in proposals[:3]:
            key = str(source).casefold()
            if not source or key in seen:
                continue
            seen.add(key)
            if self._store_user_term(source, target):
                added += 1
        return added

    def _suggestion_stage(self, index):
        return (
            'polished' if self._suggestion_modes.get(index) == 'polish'
            else 'proofreaded'
        )

    def _mark_applied_stage(self, stage):
        if stage in OUTPUT_STAGE_LABELS:
            self._last_applied_stage = stage

    def _current_output_stage(self):
        return self._last_applied_stage or self._input_stage or 'proofreaded'

    def _current_output_tags(self):
        tags = ['proofreaded']
        existing = {
            str(value or '').strip().casefold()
            for value in getattr(self.translation_doc, 'tags', ()) or ()
        }
        if (
            self._current_output_stage() == 'polished'
            or 'polished' in existing
        ):
            tags.append('polished')
        completed_passes = max(
            self._input_proofread_pass,
            1 if 'proofreaded' in existing or 'polished' in existing else 0,
        )
        # A polish pass never increments proofreading history.  A newly
        # accepted normal review does.  The first review keeps the simple
        # "proofreaded" tag; later reviews add the compact 2pr/3pr marker.
        if self._last_applied_stage == 'proofreaded':
            completed_passes = max(1, completed_passes + 1)
        if completed_passes >= 2:
            tags.append('%dpr' % completed_passes)
        return tags

    def _commit_manual_chunk(self, index):
        """Commit the pane the user actually edited and protect the result."""
        chunk = self.chunks[index]
        use_suggestion = index in self._suggestion_dirty_indices
        if use_suggestion:
            if not self._apply_draft(
                index, 'suggestion', self._suggestion_drafts
            ):
                return False, use_suggestion
            for item in chunk.items:
                item.translation_text = item.suggestion
            self._translation_drafts[index] = self._chunk_value(
                chunk, 'translation_text'
            )
            self._committed_suggestion_values[index] = self._chunk_value(
                chunk, 'suggestion'
            )
            self._suggestion_dirty_indices.discard(index)
        elif not self._apply_draft(
            index, 'translation_text', self._translation_drafts
        ):
            return False, use_suggestion

        for item in chunk.items:
            item.state = 'manual'
            item.manual_override = True
            item.ai_alignment = 'manual'
            item.notes = 'Manual edit explicitly applied by the user.'
        self._committed_translation_values[index] = self._chunk_value(
            chunk, 'translation_text'
        )
        self._translation_drafts[index] = self._committed_translation_values[
            index
        ]
        self._manual_dirty_indices.discard(index)
        # A hand correction made while reviewing a polish result is still part
        # of the polish pass, regardless of whether the user edited the middle
        # current-translation pane or the right suggestion pane.
        self._mark_applied_stage(
            self._suggestion_stage(index)
            if index in self._suggestion_modes else 'proofreaded'
        )
        self._update_row(index)
        return True, use_suggestion

    def apply_manual_edit(self):
        if not (0 <= self.current_index < len(self.chunks)):
            return
        previous_text = self._committed_translation_values.get(
            self.current_index,
            self._chunk_value(
                self.chunks[self.current_index], 'translation_text'
            ),
        )
        self.capture_edits()
        applied, used_suggestion = self._commit_manual_chunk(
            self.current_index
        )
        if not applied:
            _message(
                self, QMessageBox.Icon.Warning, 'Paragraph boundaries changed',
                'Keep exactly one blank-line-separated %s paragraph for each '
                'original paragraph before applying the manual edit.' % (
                    'right-hand suggestion' if used_suggestion else 'translation'
                )
            )
            return
        chunk = self.chunks[self.current_index]
        self._update_manual_edit_count()
        self.change_row(self.current_index, 0, self.current_index, 0)
        learned = self._offer_manual_glossary_terms(
            chunk, previous_text,
            self._committed_translation_values[self.current_index],
        )
        if not learned:
            self.status.setText(
                'Manual edit from the %s pane applied to chunk %d; this text '
                'will be written to the EPUB. Select matching source/target text '
                'and use Add term… '
                'when the glossary mapping is not unambiguous.' % (
                    'right AI suggestion' if used_suggestion else 'middle Current translation',
                    self.current_index + 1,
                )
            )

    def apply_all_manual_edits(self):
        if not self.chunks:
            return
        self.capture_edits()
        indices = sorted(
            self._manual_dirty_indices | self._suggestion_dirty_indices
        )
        if not indices:
            self.status.setText('There are no unapplied manual edits.')
            return
        invalid = set()
        applied = 0
        for index in indices:
            committed, _used_suggestion = self._commit_manual_chunk(index)
            if not committed:
                invalid.add(index)
                continue
            applied += 1
        self._update_manual_edit_count()
        if 0 <= self.current_index < len(self.chunks):
            self.change_row(self.current_index, 0, self.current_index, 0)
        if invalid:
            _message(
                self, QMessageBox.Icon.Warning,
                'Some manual edits were not applied',
                'Keep the original number of blank-line-separated paragraphs '
                'in these chunks: %s.' % ', '.join(
                    str(value + 1) for value in sorted(invalid)[:20]
                )
            )
        self.status.setText(
            'Applied manual edits to %d chunk(s); unapplied invalid chunks: %d.'
            % (applied, len(invalid))
        )

    def keep_translation(self):
        if not (0 <= self.current_index < len(self.chunks)):
            return
        self.capture_edits()
        if not self._apply_draft(
            self.current_index, 'translation_text', self._translation_drafts
        ):
            _message(
                self, QMessageBox.Icon.Warning, 'Paragraph boundaries changed',
                'Keep the same number of blank-line-separated paragraphs before '
                'keeping this chunk.'
            )
            return
        for item in self.chunks[self.current_index].items:
            item.state = 'kept'
            item.manual_override = True
            approval_note = 'Current translation explicitly approved by the user.'
            if approval_note not in (item.notes or ''):
                item.notes = '\n\n'.join(part for part in (item.notes, approval_note) if part)
        self._committed_translation_values[self.current_index] = (
            self._chunk_value(
                self.chunks[self.current_index], 'translation_text'
            )
        )
        self._manual_dirty_indices.discard(self.current_index)
        # Keep translation is also an explicit rejection of any edited text in
        # the AI suggestion pane for this chunk.
        self._committed_suggestion_values[self.current_index] = (
            self._chunk_value(
                self.chunks[self.current_index], 'suggestion'
            )
        )
        self._suggestion_dirty_indices.discard(self.current_index)
        # Rejecting a proposal does not turn a merely proofread input into a
        # polished one. Preserve any stage already applied elsewhere; an
        # otherwise untouched input retains its incoming stage.
        if not self._last_applied_stage:
            self._mark_applied_stage(self._input_stage or 'proofreaded')
        self._update_manual_edit_count()
        self._update_row(self.current_index)
        self.change_row(self.current_index, 0, self.current_index, 0)
        self.status.setText(
            'Current translation approved for chunk %d; this text will be '
            'written to the EPUB.' % (self.current_index + 1)
        )

    def approve_yellow_rows(self):
        if not self.chunks:
            return
        self.capture_edits()
        yellow_alignments = {'positional', 'length-outlier', 'uncertain'}
        candidates = []
        protected = 0
        for index, chunk in enumerate(self.chunks):
            if self._chunk_alignment(chunk) not in yellow_alignments:
                continue
            if (
                index in self._manual_dirty_indices or
                index in self._suggestion_dirty_indices
            ):
                protected += 1
                continue
            candidates.append(index)
        if not candidates:
            _message(
                self, QMessageBox.Icon.Information, 'No yellow rows to approve',
                'There are no unresolved yellow rows without unapplied manual '
                'edits. Red rows are deliberately excluded.'
            )
            return
        answer = QMessageBox.question(
            self, 'Approve all yellow rows',
            'Approve %d yellow rows at once?\n\nWhen an AI suggestion is '
            'available, it will become the Current translation. Otherwise the '
            'existing Current translation will be kept. Red rows are excluded, '
            'and %d row(s) with unapplied manual edits will remain untouched.\n\n'
            'Use this only after sampling the yellow rows: their alignment was '
            'not fully confirmed automatically.' % (len(candidates), protected),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        suggestions_used = 0
        translations_kept = 0
        applied_stages = []
        for index in candidates:
            chunk = self.chunks[index]
            if all(item.suggestion for item in chunk.items):
                for item in chunk.items:
                    item.translation_text = item.suggestion
                    item.state = 'accepted'
                suggestions_used += 1
                applied_stages.append(self._suggestion_stage(index))
            else:
                for item in chunk.items:
                    item.state = 'kept'
                translations_kept += 1
                applied_stages.append(self._input_stage or 'proofreaded')
            for item in chunk.items:
                item.manual_override = True
                item.ai_alignment = 'manual'
                note = 'Yellow alignment explicitly approved by the user.'
                if note not in (item.notes or ''):
                    item.notes = '\n\n'.join(filter(None, (item.notes, note)))
            value = self._chunk_value(chunk, 'translation_text')
            self._translation_drafts[index] = value
            self._committed_translation_values[index] = value
            self._committed_suggestion_values[index] = self._chunk_value(
                chunk, 'suggestion'
            )
            self._manual_dirty_indices.discard(index)
            self._suggestion_dirty_indices.discard(index)
            self._update_row(index)
        self._mark_applied_stage(
            'polished' if 'polished' in applied_stages else 'proofreaded'
        )
        self._update_manual_edit_count()
        if 0 <= self.current_index < len(self.chunks):
            self.change_row(self.current_index, 0, self.current_index, 0)
        self.status.setText(
            'Approved %d yellow rows: %d AI suggestions accepted, %d current '
            'translations kept; protected manual rows: %d.' % (
                len(candidates), suggestions_used, translations_kept, protected
            )
        )

    def accept_all(self):
        self.capture_edits()
        count = 0
        skipped = 0
        skipped_manual = 0
        applied_stages = []
        for index, chunk in enumerate(self.chunks):
            if (
                index in self._manual_dirty_indices or
                index in self._suggestion_dirty_indices
            ):
                skipped_manual += 1
                continue
            safe_alignment = self._chunk_is_safe(chunk) and all(
                item.ai_alignment == 'ok' for item in chunk.items
            )
            has_suggestions = all(item.suggestion for item in chunk.items)
            if has_suggestions and safe_alignment:
                for item in chunk.items:
                    item.translation_text = item.suggestion
                    item.state = 'accepted'
                    item.manual_override = False
                self._translation_drafts[index] = self._chunk_value(
                    chunk, 'translation_text'
                )
                self._committed_translation_values[index] = (
                    self._translation_drafts[index]
                )
                self._committed_suggestion_values[index] = self._chunk_value(
                    chunk, 'suggestion'
                )
                self._suggestion_dirty_indices.discard(index)
                applied_stages.append(self._suggestion_stage(index))
                self._update_row(index)
                count += 1
            elif any(item.suggestion for item in chunk.items):
                skipped += 1
        if 0 <= self.current_index < len(self.chunks):
            self.change_row(self.current_index, 0, self.current_index, 0)
        if applied_stages:
            self._mark_applied_stage(
                'polished' if 'polished' in applied_stages else 'proofreaded'
            )
        self.status.setText(
            'Accepted chunks: %d; skipped due to uncertain alignment: %d; '
            'protected manual edits: %d.' % (
                count, skipped, skipped_manual
            )
        )

    def save_epub(self):
        if not self.translation_doc:
            return
        self.capture_edits()
        unaccepted_suggestions = [
            index + 1 for index in sorted(self._suggestion_dirty_indices)
        ]
        if unaccepted_suggestions:
            first = unaccepted_suggestions[0] - 1
            self.table.setCurrentCell(first, 0)
            _message(
                self, QMessageBox.Icon.Warning,
                'Edited suggestions have not been accepted',
                'The right-hand AI suggestion pane is only a proposal and is '
                'not written to the EPUB until you click Accept suggestion. '
                'Edited but unaccepted suggestions remain in chunks: %s.\n\n'
                'For each one, click Accept suggestion to use the right-hand '
                'text, or click Keep translation to discard it and keep the '
                'middle Current translation pane.' % ', '.join(
                    map(str, unaccepted_suggestions[:20])
                )
            )
            return
        invalid = self._commit_translation_drafts()
        if invalid:
            _message(
                self, QMessageBox.Icon.Warning, 'Cannot save manual edits',
                'Keep the original number of blank-line-separated paragraphs in '
                'these chunks before saving: %s.' % ', '.join(
                    map(str, invalid[:20])
                )
            )
            return
        risky = [
            index + 1 for index, chunk in enumerate(self.chunks)
            if self._chunk_state(chunk) == 'blocked' or
            self._chunk_alignment(chunk) in (
                'mismatch', 'duplicate', 'translation-overflow'
            )
        ]
        if risky:
            answer = QMessageBox.question(
                self, 'Unresolved risky chunks',
                '%d chunks are still blocked, mismatched, possible duplicates, '
                'or merged translation blocks. '
                'They will keep their current translation. Save anyway?\n\n'
                'First affected chunks: %s' % (
                    len(risky), ', '.join(map(str, risky[:20]))
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        source = self.translation_doc.path
        output_stage = self._current_output_stage()
        output_tags = self._current_output_tags()
        language_code = _target_language_code(
            get_settings(), self.translation_doc
        )
        default_path = _output_filename(
            source, output_stage, language_code
        )
        output, _ = QFileDialog.getSaveFileName(
            self, 'Save proofread EPUB', default_path, 'EPUB (*.epub)'
        )
        if not output:
            return
        if not output.lower().endswith('.epub'):
            output += '.epub'
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        output_title = _output_title(
            self.translation_doc.title, output_stage, language_code
        )
        prepare_polish = False
        save_error = None
        saved = ''
        library_note = ''
        try:
            self.translation_doc.apply_texts(self.items)
            self.translation_doc.set_output_metadata(
                output_title, tags=output_tags
            )
            saved = self.translation_doc.save_as(output)
        except Exception as err:
            save_error = err
        else:
            if self.add_to_library.isChecked():
                try:
                    book_id = self._add_output_to_library(
                        saved, output_title, output_tags
                    )
                except Exception as err:
                    library_note = (
                        '\n\nThe EPUB was saved correctly, but it could not be '
                        'added to the Calibre library: %s' % err
                    )
                else:
                    library_note = (
                        '\n\nAdded to the Calibre library as "%s" with the '
                        '%s tag%s (book id %s).' % (
                            output_title, ', '.join(output_tags),
                            's' if len(output_tags) != 1 else '', book_id,
                        )
                    )
        finally:
            QApplication.restoreOverrideCursor()
        if save_error is not None:
            _message(
                self, QMessageBox.Icon.Critical, 'Save error', str(save_error)
            )
            return
        if saved:
            _message(
                self, QMessageBox.Icon.Information, 'EPUB saved', saved
                + '\n\nZIP, package spine, modified XHTML, and output metadata '
                'validation passed.' + library_note
            )
            if (
                output_stage != 'polished'
                and self.experimental_polishing
            ):
                answer = QMessageBox.question(
                    self, 'Prepare final polish pass',
                    'Replace the current Translation input with this newly saved '
                    'EPUB? The original input file will not be overwritten. This '
                    'reloads the aligned pair and enables Polish all for a separate '
                    'surface-correction pass.',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                prepare_polish = answer == QMessageBox.StandardButton.Yes
        if prepare_polish:
            self.translation_picker.path_edit.setText(saved)
            self.load_books()
            self._polish_ready = True
            self._set_work_enabled(bool(self.chunks))
            self.status.setText(
                'The saved EPUB is now the current Translation. Polish all is '
                'ready and will use its isolated temperature-0.1 prompt.'
            )

    def _add_output_to_library(self, path, title, output_tags=None):
        if self.gui is None or getattr(self.gui, 'current_db', None) is None:
            raise RuntimeError('No active Calibre library is available.')
        from calibre.ebooks.metadata.meta import get_metadata

        with open(path, 'rb') as stream:
            metadata = get_metadata(
                stream, 'epub', force_read_metadata=True
            )
        metadata.title = title
        metadata.title_sort = title
        tags = [str(value).strip() for value in (metadata.tags or [])]
        for output_tag in output_tags or ('proofreaded',):
            if output_tag.casefold() not in {
                value.casefold() for value in tags
            }:
                tags.append(output_tag)
        metadata.tags = tags
        cover = None
        cover_data = getattr(metadata, 'cover_data', None)
        if cover_data and len(cover_data) > 1:
            cover = cover_data[1]
        database = self.gui.current_db
        api = database.new_api
        # Match Calibre's own Add books path and Ebook Translator: the legacy
        # database facade creates the GUI-visible record, while new_api safely
        # attaches the format. Calling new_api.create_book_entry directly can
        # create a record that the current library model never announces.
        book_id = database.create_book_entry(metadata, cover=cover)
        try:
            api.add_format(
                book_id, 'EPUB', path, replace=True, run_hooks=False
            )
        except Exception:
            api.remove_books({book_id}, permanent=True)
            raise
        model = self.gui.library_view.model()
        model.books_added(1)
        try:
            self.gui.library_view.scroll_to_book(book_id)
        except Exception:
            pass
        return book_id

    def closeEvent(self, event):
        if self._thread is not None or self._term_thread is not None:
            _message(
                self, QMessageBox.Icon.Warning, 'Requests are still running',
                'Wait for terminology analysis to finish, or click "Cancel '
                'requests" for a proofreading run before closing.'
            )
            event.ignore()
            return
        if self.terminology is not None:
            try:
                self.terminology.save()
            except OSError as err:
                _message(
                    self, QMessageBox.Icon.Critical,
                    'Could not save book memory', str(err)
                )
                event.ignore()
                return
        super().closeEvent(event)
