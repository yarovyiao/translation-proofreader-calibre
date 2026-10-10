import os
import time

from qt.core import QFileDialog, Qt, QMenu, QMessageBox, QTimer
from calibre.gui2.actions import InterfaceAction

from .config import get_recent_book_pairs, get_settings, save_settings
from .dialogs import ProofreaderDialog, SettingsDialog, TerminologyDialog
from .localization import tr
from .terminology import MemoryProfileLibrary, ProfileTerminology
from .transfer import BundleError, import_profiles, read_bundle, write_bundle


class TranslationProofreaderAction(InterfaceAction):
    name = 'Translation Proofreader'
    action_spec = (
        'TProofreader', 'images/icon.png',
        'Compare a translation with its original using AI', None,
    )

    TRANSLATION_TAGS = frozenset({
        'translated', 'translation',
    })

    def genesis(self):
        language = get_settings().get('interface_language', 'auto')
        t = lambda value: tr(value, language)
        icon = get_icons('images/icon.png', 'Translation Proofreader')
        self.qaction.setIcon(icon)
        menu = QMenu(self.gui)
        self.selected_pair_action = self.create_menu_action(
            menu,
            'translation-proofreader-selected-pair',
            t('Proofread two selected books'),
            icon=icon,
            description=(
                'Use two selected EPUB books as the original and translation'
            ),
            triggered=self.open_selected_pair,
        )
        self.reopen_last_action = self.create_menu_action(
            menu,
            'translation-proofreader-reopen-last',
            t('Reopen last book pair'),
            icon=icon,
            description='Open and align the most recently used pair of EPUBs',
            triggered=self.reopen_last_pair,
        )
        self.recent_menu = menu.addMenu(t('Recent book pairs'))
        menu.addSeparator()
        self.manual_picker_action = self.create_menu_action(
            menu,
            'translation-proofreader-manual-picker',
            t('Choose books manually…'),
            description='Open the editor and choose books from the library or disk',
            triggered=self.open_proofreader,
        )
        self.profile_menu = menu.addMenu(t('Series memory profiles'))
        menu.addSeparator()
        self.export_bundle_action = self.create_menu_action(
            menu,
            'translation-proofreader-export-portable-bundle',
            t('Export prompts and glossary…'),
            description=(
                'Export the core prompt and all saved series-memory profiles '
                'to one portable file'
            ),
            triggered=self.export_portable_bundle,
        )
        self.import_bundle_action = self.create_menu_action(
            menu,
            'translation-proofreader-import-portable-bundle',
            t('Import prompts and glossary…'),
            description=(
                'Restore a portable core-prompt and series-memory bundle'
            ),
            triggered=self.import_portable_bundle,
        )
        self.settings_action = self.create_menu_action(
            menu,
            'translation-proofreader-settings',
            t('AI settings…'),
            description='Configure the API, local model, and retry behavior',
            triggered=self.open_settings,
        )
        menu.aboutToShow.connect(self._update_menu_state)
        self.qaction.setMenu(menu)
        self.qaction.triggered.connect(self.open_proofreader)
        self._windows = []
        self._profiles = MemoryProfileLibrary()

    def _update_menu_state(self):
        self._apply_menu_language()
        self._update_selection_state()
        self._populate_recent_menu()
        self._populate_profile_menu()

    def _apply_menu_language(self):
        language = get_settings().get('interface_language', 'en')
        self.selected_pair_action.setText(tr(
            'Proofread two selected books', language
        ))
        self.reopen_last_action.setText(tr('Reopen last book pair', language))
        self.recent_menu.setTitle(tr('Recent book pairs', language))
        self.manual_picker_action.setText(tr(
            'Choose books manually…', language
        ))
        self.profile_menu.setTitle(tr('Series memory profiles', language))
        self.export_bundle_action.setText(tr(
            'Export prompts and glossary…', language
        ))
        self.import_bundle_action.setText(tr(
            'Import prompts and glossary…', language
        ))
        self.settings_action.setText(tr('AI settings…', language))

    def _populate_recent_menu(self):
        self.recent_menu.clear()
        recent = get_recent_book_pairs()
        self.reopen_last_action.setEnabled(bool(recent))
        if not recent:
            action = self.recent_menu.addAction('No recent book pairs')
            action.setEnabled(False)
            return
        for entry in recent:
            label = entry.get('label') or 'Previous book pair'
            action = self.recent_menu.addAction(label)
            action.setToolTip(
                '%s\n%s' % (entry['original'], entry['translation'])
            )
            action.triggered.connect(
                lambda _checked=False, value=dict(entry):
                self.open_recent_pair(value)
            )

    def _populate_profile_menu(self):
        self.profile_menu.clear()
        names = self._profiles.names()
        if not names:
            action = self.profile_menu.addAction('No saved profiles')
            action.setEnabled(False)
            return
        for name in names:
            action = self.profile_menu.addAction(name)
            action.setToolTip(
                'Open and edit this shared terminology profile without a book'
            )
            action.triggered.connect(
                lambda _checked=False, value=name:
                self.open_memory_profile(value)
            )

    @staticmethod
    def _book_tags(api, book_id):
        raw_tags = api.field_for('tags', book_id) or ()
        if isinstance(raw_tags, str):
            raw_tags = raw_tags.split(',')
        return tuple(
            str(tag).strip() for tag in raw_tags if str(tag).strip()
        )

    def _is_translation(self, tags):
        normalized = {tag.casefold().strip() for tag in tags}
        return any(
            tag in self.TRANSLATION_TAGS or tag.startswith('translated')
            for tag in normalized
        )

    def _library_entries(self):
        api = self.gui.current_db.new_api
        entries = []
        for book_id in sorted(api.all_book_ids()):
            try:
                formats = api.formats(book_id) or ()
                if 'EPUB' not in {str(fmt).upper() for fmt in formats}:
                    continue
                title = api.field_for('title', book_id) or 'Untitled'
                tags = self._book_tags(api, book_id)
                path = api.format_abspath(book_id, 'EPUB')
                if path:
                    tag_text = ', '.join(tags) if tags else 'no tags'
                    entries.append((
                        '[%s] %s  —  tags: %s' % (book_id, title, tag_text),
                        path,
                    ))
            except Exception:
                continue
        return entries

    def _selected_epub_records(self):
        api = self.gui.current_db.new_api
        model = self.gui.library_view.model()
        rows = self.gui.library_view.selectionModel().selectedRows()
        selected = []
        for row in sorted(rows, key=lambda value: value.row()):
            try:
                book_id = model.id(row)
                formats = api.formats(book_id) or ()
                if 'EPUB' in {str(fmt).upper() for fmt in formats}:
                    path = api.format_abspath(book_id, 'EPUB')
                    if path:
                        selected.append({
                            'path': path,
                            'tags': self._book_tags(api, book_id),
                        })
            except Exception:
                continue
        return selected

    def _selected_epubs(self):
        selected = self._selected_epub_records()
        if len(selected) == 1 and self._is_translation(selected[0]['tags']):
            return ['', selected[0]['path']]
        if len(selected) == 2:
            translation_flags = [
                self._is_translation(book['tags']) for book in selected
            ]
            if translation_flags.count(True) == 1:
                translation_index = translation_flags.index(True)
                original_index = 1 - translation_index
                return [
                    selected[original_index]['path'],
                    selected[translation_index]['path'],
                ]
        return [book['path'] for book in selected[:2]]

    def _update_selection_state(self):
        selected_rows = self.gui.library_view.selectionModel().selectedRows()
        selected = self._selected_epub_records()
        enabled = len(selected_rows) == 2 and len(selected) == 2
        self.selected_pair_action.setEnabled(enabled)
        if enabled:
            self.selected_pair_action.setToolTip(
                'Open two selected EPUB books; the translated tag identifies the translation'
            )
        else:
            self.selected_pair_action.setToolTip(
                'Select exactly two books in EPUB format'
            )

    def open_selected_pair(self):
        selected_rows = self.gui.library_view.selectionModel().selectedRows()
        selected = self._selected_epub_records()
        if len(selected_rows) != 2 or len(selected) != 2:
            return
        self._open_window(self._selected_epubs())

    def open_proofreader(self):
        self._open_window(self._selected_epubs())

    def reopen_last_pair(self):
        recent = get_recent_book_pairs()
        if recent:
            self.open_recent_pair(recent[0])

    def open_recent_pair(self, entry):
        self._open_window([
            entry.get('original', ''), entry.get('translation', '')
        ], auto_load=True)

    def open_memory_profile(self, name):
        try:
            terminology = ProfileTerminology(self._profiles, name)
        except Exception as err:
            QMessageBox.critical(
                self.gui, 'Could not open series memory profile', str(err)
            )
            return
        dialog = TerminologyDialog(terminology, None, self.gui)
        dialog.setWindowTitle('Series terminology — %s' % name)
        if dialog.exec():
            QMessageBox.information(
                self.gui, 'Series memory profile saved',
                'Saved profile "%s". A proofreader window that is already '
                'open keeps its current in-memory copy; click Apply profile '
                'there to reload these changes. Newly opened books will use '
                'the updated profile normally.' % name,
            )

    def export_portable_bundle(self):
        filename = 'translation-proofreader-backup-%s.tproofreader.json' % (
            time.strftime('%Y-%m-%d')
        )
        default_path = os.path.join(
            os.path.expanduser('~'), 'Documents', filename
        )
        path, _selected_filter = QFileDialog.getSaveFileName(
            self.gui, 'Export prompts and glossary', default_path,
            'Translation Proofreader bundle (*.tproofreader.json);;JSON (*.json)',
        )
        if not path:
            return
        if not path.lower().endswith(('.json', '.tproofreader')):
            path += '.tproofreader.json'
        try:
            saved = write_bundle(
                path, self._profiles, get_settings().get('prompt', '')
            )
        except BundleError as err:
            QMessageBox.critical(
                self.gui, 'Could not export prompts and glossary', str(err)
            )
            return
        QMessageBox.information(
            self.gui, 'Portable bundle exported',
            'Saved the core prompt and %d series-memory profile(s) to:\n%s\n\n'
            'Book instructions, book memory/notes, terminology, gender notes, '
            'and statuses inside those profiles are included. API keys and AI '
            'provider settings are not included. Private book memory must be '
            'saved as a series profile first.' % (
                len(self._profiles.names()), saved
            ),
        )

    def import_portable_bundle(self):
        path, _selected_filter = QFileDialog.getOpenFileName(
            self.gui, 'Import prompts and glossary', '',
            'Translation Proofreader bundle (*.tproofreader.json *.json)',
        )
        if not path:
            return
        try:
            bundle = read_bundle(path)
        except BundleError as err:
            QMessageBox.critical(
                self.gui, 'Could not import prompts and glossary', str(err)
            )
            return
        existing = {name.casefold() for name in self._profiles.names()}
        conflicts = [
            name for name in bundle['profiles']
            if name.casefold() in existing
        ]
        replace = False
        if conflicts:
            answer = QMessageBox.question(
                self.gui, 'Existing series-memory profiles',
                '%d imported profile name(s) already exist.\n\n'
                'Yes: replace those profiles with the imported copies.\n'
                'No: keep every existing entry immutable and add only terms '
                'missing from it.\nCancel: import nothing.' % len(conflicts),
                QMessageBox.StandardButton.Yes |
                QMessageBox.StandardButton.No |
                QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            replace = answer == QMessageBox.StandardButton.Yes
        current_prompt = get_settings().get('prompt', '')
        import_core = True
        if bundle['core_prompt'] != current_prompt:
            answer = QMessageBox.question(
                self.gui, 'Import core prompt',
                'Replace the current global Core prompt with the one from the '
                'portable bundle? Choosing No still imports the profiles.',
                QMessageBox.StandardButton.Yes |
                QMessageBox.StandardButton.No |
                QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            import_core = answer == QMessageBox.StandardButton.Yes
        try:
            result = import_profiles(
                bundle, self._profiles, replace_existing=replace
            )
            if import_core:
                save_settings({'prompt': bundle['core_prompt']})
        except (BundleError, ValueError, OSError) as err:
            QMessageBox.critical(
                self.gui, 'Could not import prompts and glossary', str(err)
            )
            return
        QMessageBox.information(
            self.gui, 'Portable bundle imported',
            'Profiles added: %d; safely merged: %d; replaced: %d.\n'
            'Core prompt: %s.\n\nAlready open proofreader windows keep '
            'their current in-memory copy; click Apply profile again to load '
            'the imported data.' % (
                len(result['added']), len(result['merged']),
                len(result['replaced']),
                'imported' if import_core else 'kept unchanged',
            ),
        )

    def _open_window(self, selected_paths, auto_load=False):
        window = ProofreaderDialog(
            self.gui,
            self._library_entries(),
            selected_paths,
            self.gui,
        )
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        window.destroyed.connect(lambda: self._remove_window(window))
        self._windows.append(window)
        window.show()
        window.raise_()
        if auto_load:
            QTimer.singleShot(0, window.load_books)

    def _remove_window(self, window):
        if window in self._windows:
            self._windows.remove(window)

    def open_settings(self):
        SettingsDialog(self.gui).exec()
