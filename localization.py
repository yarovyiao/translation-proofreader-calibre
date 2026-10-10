"""Small dependency-free UI localization layer.

Calibre plugins cannot assume that a system gettext catalogue is installed.
Keep the English source strings as stable keys and translate the most visible
controls here.  Missing strings intentionally fall back to English.
"""

import locale


LANGUAGES = (
    ('auto', 'Automatic (Calibre / system)'),
    ('en', 'English'),
    ('uk', 'Українська'),
    ('pl', 'Polski'),
    ('de', 'Deutsch'),
    ('fr', 'Français'),
    ('it', 'Italiano'),
)


# Order: Ukrainian, Polish, German, French, Italian.  English is the key.
_T = {
    'Translation Proofreader — bilingual review': ('Translation Proofreader — двомовна перевірка', 'Translation Proofreader — korekta dwujęzyczna', 'Translation Proofreader — zweisprachige Prüfung', 'Translation Proofreader — révision bilingue', 'Translation Proofreader — revisione bilingue'),
    'AI Settings — Translation Proofreader': ('Налаштування ШІ — Translation Proofreader', 'Ustawienia AI — Translation Proofreader', 'KI-Einstellungen — Translation Proofreader', 'Paramètres IA — Translation Proofreader', 'Impostazioni IA — Translation Proofreader'),
    'Original': ('Оригінал', 'Oryginał', 'Original', 'Original', 'Originale'),
    'Original:': ('Оригінал:', 'Oryginał:', 'Original:', 'Original :', 'Originale:'),
    'Translation': ('Переклад', 'Tłumaczenie', 'Übersetzung', 'Traduction', 'Traduzione'),
    'Translation:': ('Переклад:', 'Tłumaczenie:', 'Übersetzung:', 'Traduction :', 'Traduzione:'),
    'Choose an EPUB from the library or disk': ('Оберіть EPUB із бібліотеки або диска', 'Wybierz EPUB z biblioteki lub dysku', 'EPUB aus Bibliothek oder vom Datenträger wählen', 'Choisissez un EPUB dans la bibliothèque ou sur le disque', 'Scegli un EPUB dalla libreria o dal disco'),
    'Library…': ('Бібліотека…', 'Biblioteka…', 'Bibliothek…', 'Bibliothèque…', 'Libreria…'),
    'File…': ('Файл…', 'Plik…', 'Datei…', 'Fichier…', 'File…'),
    'No shared profile': ('Немає спільного профілю', 'Brak profilu wspólnego', 'Kein gemeinsames Profil', 'Aucun profil partagé', 'Nessun profilo condiviso'),
    'Load and align': ('Завантажити й вирівняти', 'Wczytaj i wyrównaj', 'Laden und ausrichten', 'Charger et aligner', 'Carica e allinea'),
    'AI settings…': ('Налаштування ШІ…', 'Ustawienia AI…', 'KI-Einstellungen…', 'Paramètres IA…', 'Impostazioni IA…'),
    'Core prompt…': ('Основний промпт…', 'Główny prompt…', 'Hauptprompt…', 'Prompt principal…', 'Prompt principale…'),
    'Book instructions…': ('Інструкції книги…', 'Instrukcje książki…', 'Buchanweisungen…', 'Instructions du livre…', 'Istruzioni del libro…'),
    'Scan book terms': ('Сканувати терміни', 'Skanuj terminy', 'Buchbegriffe scannen', 'Analyser les termes', 'Scansiona i termini'),
    'Book terminology…': ('Термінологія книги…', 'Terminologia książki…', 'Buchterminologie…', 'Terminologie du livre…', 'Terminologia del libro…'),
    'Book memory and notes…': ('Пам’ять і нотатки книги…', 'Pamięć i notatki książki…', 'Buchspeicher und Notizen…', 'Mémoire et notes du livre…', 'Memoria e note del libro…'),
    'Series memory:': ('Пам’ять серії:', 'Pamięć serii:', 'Serienspeicher:', 'Mémoire de la série :', 'Memoria della serie:'),
    'Apply profile': ('Застосувати профіль', 'Zastosuj profil', 'Profil anwenden', 'Appliquer le profil', 'Applica profilo'),
    'Save as...': ('Зберегти як…', 'Zapisz jako…', 'Speichern unter…', 'Enregistrer sous…', 'Salva come…'),
    'Delete': ('Видалити', 'Usuń', 'Löschen', 'Supprimer', 'Elimina'),
    'Clear book memory': ('Очистити пам’ять книги', 'Wyczyść pamięć książki', 'Buchspeicher leeren', 'Effacer la mémoire du livre', 'Cancella memoria libro'),
    'Sync user edits and new terms': ('Синхронізувати правки й нові терміни', 'Synchronizuj poprawki i nowe terminy', 'Änderungen und neue Begriffe synchronisieren', 'Synchroniser les corrections et nouveaux termes', 'Sincronizza modifiche e nuovi termini'),
    'Original text': ('Текст оригіналу', 'Tekst oryginału', 'Originaltext', 'Texte original', 'Testo originale'),
    'Characters': ('Символи', 'Znaki', 'Zeichen', 'Caractères', 'Caratteri'),
    'Status': ('Статус', 'Status', 'Status', 'État', 'Stato'),
    'Severity': ('Серйозність', 'Waga', 'Schweregrad', 'Gravité', 'Gravità'),
    'Alignment': ('Вирівнювання', 'Wyrównanie', 'Ausrichtung', 'Alignement', 'Allineamento'),
    'Current translation': ('Поточний переклад', 'Bieżące tłumaczenie', 'Aktuelle Übersetzung', 'Traduction actuelle', 'Traduzione attuale'),
    'AI suggestion — Accept suggestion to use': ('Пропозиція ШІ — прийміть, щоб застосувати', 'Sugestia AI — zaakceptuj, aby użyć', 'KI-Vorschlag — zum Verwenden annehmen', 'Suggestion IA — accepter pour utiliser', 'Suggerimento IA — accetta per usare'),
    'AI notes:': ('Нотатки ШІ:', 'Uwagi AI:', 'KI-Notizen:', 'Notes IA :', 'Note IA:'),
    'Inspect removed duplicate…': ('Переглянути видалений повтор…', 'Sprawdź usunięty duplikat…', 'Entferntes Duplikat prüfen…', 'Examiner le doublon supprimé…', 'Esamina duplicato rimosso…'),
    'Review selected': ('Перевірити вибране', 'Sprawdź wybrane', 'Auswahl prüfen', 'Réviser la sélection', 'Revisiona selezionati'),
    'Review all': ('Перевірити все', 'Sprawdź wszystko', 'Alles prüfen', 'Tout réviser', 'Revisiona tutto'),
    'Cancel requests': ('Скасувати запити', 'Anuluj żądania', 'Anfragen abbrechen', 'Annuler les requêtes', 'Annulla richieste'),
    'Experimental polish (optional)': ('Експериментальне полірування (необов’язково)', 'Eksperymentalne wygładzanie (opcjonalne)', 'Experimenteller Feinschliff (optional)', 'Polissage expérimental (facultatif)', 'Rifinitura sperimentale (facoltativa)'),
    'May change glossary terms; review manually.': ('Може змінювати терміни голосарію; перевіряйте вручну.', 'Może zmieniać terminy glosariusza; sprawdź ręcznie.', 'Kann Glossarbegriffe ändern; manuell prüfen.', 'Peut modifier les termes du glossaire ; vérifiez manuellement.', 'Può modificare i termini del glossario; controlla manualmente.'),
    'Polish selected': ('Полірувати вибране', 'Wygładź wybrane', 'Auswahl polieren', 'Polir la sélection', 'Rifinisci selezionati'),
    'Polish all': ('Полірувати все', 'Wygładź wszystko', 'Alles polieren', 'Tout polir', 'Rifinisci tutto'),
    'Accept suggestion': ('Прийняти пропозицію', 'Zaakceptuj sugestię', 'Vorschlag annehmen', 'Accepter la suggestion', 'Accetta suggerimento'),
    'Accept all suggestions': ('Прийняти всі пропозиції', 'Zaakceptuj wszystkie sugestie', 'Alle Vorschläge annehmen', 'Accepter toutes les suggestions', 'Accetta tutti i suggerimenti'),
    'Keep translation': ('Залишити переклад', 'Zachowaj tłumaczenie', 'Übersetzung behalten', 'Conserver la traduction', 'Mantieni traduzione'),
    'Apply manual edit': ('Застосувати ручну правку', 'Zastosuj ręczną poprawkę', 'Manuelle Änderung anwenden', 'Appliquer la correction manuelle', 'Applica modifica manuale'),
    'Apply all manual edits': ('Застосувати всі ручні правки', 'Zastosuj wszystkie ręczne poprawki', 'Alle manuellen Änderungen anwenden', 'Appliquer toutes les corrections manuelles', 'Applica tutte le modifiche manuali'),
    'Add term…': ('Додати термін…', 'Dodaj termin…', 'Begriff hinzufügen…', 'Ajouter un terme…', 'Aggiungi termine…'),
    'Approve yellow rows…': ('Підтвердити жовті рядки…', 'Zatwierdź żółte wiersze…', 'Gelbe Zeilen bestätigen…', 'Approuver les lignes jaunes…', 'Approva righe gialle…'),
    'Add to Calibre library': ('Додати до бібліотеки Calibre', 'Dodaj do biblioteki Calibre', 'Zur Calibre-Bibliothek hinzufügen', 'Ajouter à la bibliothèque Calibre', 'Aggiungi alla libreria Calibre'),
    'Save new EPUB…': ('Зберегти новий EPUB…', 'Zapisz nowy EPUB…', 'Neues EPUB speichern…', 'Enregistrer le nouvel EPUB…', 'Salva nuovo EPUB…'),
    'Choose two EPUB books.': ('Оберіть дві книги EPUB.', 'Wybierz dwie książki EPUB.', 'Zwei EPUB-Bücher auswählen.', 'Choisissez deux livres EPUB.', 'Scegli due libri EPUB.'),
    'Interface language:': ('Мова інтерфейсу:', 'Język interfejsu:', 'Oberflächensprache:', 'Langue de l’interface :', 'Lingua interfaccia:'),
    'Experimental features:': ('Експериментальні функції:', 'Funkcje eksperymentalne:', 'Experimentelle Funktionen:', 'Fonctions expérimentales :', 'Funzioni sperimentali:'),
    'Enable experimental polishing controls': ('Увімкнути експериментальне полірування', 'Włącz eksperymentalne wygładzanie', 'Experimentellen Feinschliff aktivieren', 'Activer le polissage expérimental', 'Abilita rifinitura sperimentale'),
    'Settings profile:': ('Профіль налаштувань:', 'Profil ustawień:', 'Einstellungsprofil:', 'Profil de paramètres :', 'Profilo impostazioni:'),
    'Review mode:': ('Режим перевірки:', 'Tryb korekty:', 'Prüfmodus:', 'Mode de révision :', 'Modalità revisione:'),
    'Provider:': ('Провайдер:', 'Dostawca:', 'Anbieter:', 'Fournisseur :', 'Provider:'),
    'API key:': ('Ключ API:', 'Klucz API:', 'API-Schlüssel:', 'Clé API :', 'Chiave API:'),
    'Endpoint:': ('Адреса API:', 'Adres API:', 'Endpunkt:', 'Point d’accès :', 'Endpoint:'),
    'Model:': ('Модель:', 'Model:', 'Modell:', 'Modèle :', 'Modello:'),
    'Languages:': ('Мови:', 'Języki:', 'Sprachen:', 'Langues :', 'Lingue:'),
    'Temperature:': ('Температура:', 'Temperatura:', 'Temperatur:', 'Température :', 'Temperatura:'),
    'Timeout:': ('Тайм-аут:', 'Limit czasu:', 'Zeitlimit:', 'Délai :', 'Timeout:'),
    'Characters per request:': ('Символів на запит:', 'Znaków na żądanie:', 'Zeichen pro Anfrage:', 'Caractères par requête :', 'Caratteri per richiesta:'),
    'Test proofreading': ('Перевірити редактуру', 'Testuj korektę', 'Korrektur testen', 'Tester la révision', 'Prova revisione'),
    'Load': ('Завантажити', 'Wczytaj', 'Laden', 'Charger', 'Carica'),
    'Update': ('Оновити', 'Aktualizuj', 'Aktualisieren', 'Mettre à jour', 'Aggiorna'),
    'Auto-detect': ('Визначити автоматично', 'Wykryj automatycznie', 'Automatisch erkennen', 'Détecter automatiquement', 'Rileva automaticamente'),
    'Request attempts:': ('Спроб запиту:', 'Próby żądania:', 'Anfrageversuche:', 'Tentatives de requête :', 'Tentativi richiesta:'),
    'Invalid response attempts:': ('Спроб після хибної відповіді:', 'Próby po błędnej odpowiedzi:', 'Versuche bei ungültiger Antwort:', 'Tentatives après réponse invalide :', 'Tentativi dopo risposta non valida:'),
    'Suggestion repair:': ('Виправлення пропозицій:', 'Naprawa sugestii:', 'Vorschlagsreparatur:', 'Réparation des suggestions :', 'Correzione suggerimenti:'),
    'Initial retry delay:': ('Початкова затримка повтору:', 'Początkowe opóźnienie ponowienia:', 'Anfängliche Wiederholungsverzögerung:', 'Délai initial avant nouvelle tentative :', 'Ritardo iniziale nuovo tentativo:'),
    'Interval between paragraphs:': ('Інтервал між запитами:', 'Odstęp między żądaniami:', 'Intervall zwischen Anfragen:', 'Intervalle entre les requêtes :', 'Intervallo tra richieste:'),
    'Content repair:': ('Виправлення вмісту:', 'Naprawa treści:', 'Inhaltsreparatur:', 'Réparation du contenu :', 'Correzione contenuto:'),
    'Local language scan:': ('Локальна мовна перевірка:', 'Lokalne sprawdzanie języka:', 'Lokale Sprachprüfung:', 'Analyse linguistique locale :', 'Controllo lingua locale:'),
    'Core prompt': ('Основний промпт', 'Główny prompt', 'Hauptprompt', 'Prompt principal', 'Prompt principale'),
    'Book instructions': ('Інструкції книги', 'Instrukcje książki', 'Buchanweisungen', 'Instructions du livre', 'Istruzioni del libro'),
    'Active book memory': ('Активна пам’ять книги', 'Aktywna pamięć książki', 'Aktiver Buchspeicher', 'Mémoire active du livre', 'Memoria libro attiva'),
    'Open core proofreading prompt…': ('Відкрити основний промпт редактури…', 'Otwórz główny prompt korekty…', 'Hauptprompt der Prüfung öffnen…', 'Ouvrir le prompt principal de révision…', 'Apri prompt principale di revisione…'),
    'Open book-specific instructions…': ('Відкрити інструкції цієї книги…', 'Otwórz instrukcje tej książki…', 'Buchspezifische Anweisungen öffnen…', 'Ouvrir les instructions de ce livre…', 'Apri istruzioni del libro…'),
    'Open active memory and notes…': ('Відкрити активну пам’ять і нотатки…', 'Otwórz aktywną pamięć i notatki…', 'Aktiven Speicher und Notizen öffnen…', 'Ouvrir la mémoire active et les notes…', 'Apri memoria attiva e note…'),
    'Terminology replacement (strict)': ('Заміна термінології (сувора)', 'Zamiana terminologii (ścisła)', 'Terminologieersetzung (streng)', 'Remplacement terminologique (strict)', 'Sostituzione terminologica (rigorosa)'),
    'Terminology + language cleanup': ('Термінологія + мовне очищення', 'Terminologia + czyszczenie języka', 'Terminologie + Sprachbereinigung', 'Terminologie + nettoyage linguistique', 'Terminologia + pulizia linguistica'),
    'Experimental full proofreading': ('Експериментальна повна редактура', 'Eksperymentalna pełna korekta', 'Experimentelle Vollprüfung', 'Révision complète expérimentale', 'Revisione completa sperimentale'),
    'Mode: strict terminology': ('Режим: сувора термінологія', 'Tryb: ścisła terminologia', 'Modus: strenge Terminologie', 'Mode : terminologie stricte', 'Modalità: terminologia rigorosa'),
    'Mode: terminology + cleanup': ('Режим: термінологія + очищення', 'Tryb: terminologia + czyszczenie', 'Modus: Terminologie + Bereinigung', 'Mode : terminologie + nettoyage', 'Modalità: terminologia + pulizia'),
    'Mode: experimental full proofreading': ('Режим: експериментальна повна редактура', 'Tryb: eksperymentalna pełna korekta', 'Modus: experimentelle Vollprüfung', 'Mode : révision complète expérimentale', 'Modalità: revisione completa sperimentale'),
    'Book terminology': ('Термінологія книги', 'Terminologia książki', 'Buchterminologie', 'Terminologie du livre', 'Terminologia del libro'),
    'Minimum occurrences:': ('Мінімум повторень:', 'Minimalna liczba wystąpień:', 'Mindestvorkommen:', 'Occurrences minimales :', 'Occorrenze minime:'),
    'Search:': ('Пошук:', 'Szukaj:', 'Suchen:', 'Rechercher :', 'Cerca:'),
    'Show:': ('Показати:', 'Pokaż:', 'Anzeigen:', 'Afficher :', 'Mostra:'),
    'Previous': ('Попередній', 'Poprzedni', 'Zurück', 'Précédent', 'Precedente'),
    'Next': ('Наступний', 'Następny', 'Weiter', 'Suivant', 'Successivo'),
    'Source': ('Оригінал', 'Źródło', 'Quelle', 'Source', 'Sorgente'),
    'Canonical target form': ('Канонічна цільова форма', 'Kanoniczna forma docelowa', 'Kanonische Zielform', 'Forme cible canonique', 'Forma canonica di destinazione'),
    'AI suggestion': ('Пропозиція ШІ', 'Sugestia AI', 'KI-Vorschlag', 'Suggestion IA', 'Suggerimento IA'),
    'Gender / grammar note': ('Стать / граматична нотатка', 'Płeć / uwaga gramatyczna', 'Geschlecht / Grammatiknotiz', 'Genre / note grammaticale', 'Genere / nota grammaticale'),
    'Proper name': ('Власна назва', 'Nazwa własna', 'Eigenname', 'Nom propre', 'Nome proprio'),
    'Occurrences': ('Повторення', 'Wystąpienia', 'Vorkommen', 'Occurrences', 'Occorrenze'),
    'Close': ('Закрити', 'Zamknij', 'Schließen', 'Fermer', 'Chiudi'),
    'Apply edited mappings / notes': ('Застосувати змінені відповідники / нотатки', 'Zastosuj zmienione mapowania / notatki', 'Bearbeitete Zuordnungen / Notizen anwenden', 'Appliquer les correspondances / notes modifiées', 'Applica corrispondenze / note modificate'),
    'Confirm selected': ('Підтвердити вибране', 'Potwierdź wybrane', 'Auswahl bestätigen', 'Confirmer la sélection', 'Conferma selezionati'),
    'Reset selected': ('Скинути вибране', 'Resetuj wybrane', 'Auswahl zurücksetzen', 'Réinitialiser la sélection', 'Reimposta selezionati'),
    'Delete selected': ('Видалити вибране', 'Usuń wybrane', 'Auswahl löschen', 'Supprimer la sélection', 'Elimina selezionati'),
    'Proofread two selected books': ('Редагувати дві вибрані книги', 'Skoryguj dwie wybrane książki', 'Zwei ausgewählte Bücher prüfen', 'Réviser deux livres sélectionnés', 'Revisiona due libri selezionati'),
    'Reopen last book pair': ('Відкрити останню пару книг', 'Otwórz ostatnią parę książek', 'Letztes Buchpaar erneut öffnen', 'Rouvrir la dernière paire de livres', 'Riapri ultima coppia di libri'),
    'Recent book pairs': ('Нещодавні пари книг', 'Ostatnie pary książek', 'Letzte Buchpaare', 'Paires de livres récentes', 'Coppie di libri recenti'),
    'Choose books manually…': ('Вибрати книги вручну…', 'Wybierz książki ręcznie…', 'Bücher manuell auswählen…', 'Choisir les livres manuellement…', 'Scegli libri manualmente…'),
    'Series memory profiles': ('Профілі пам’яті серії', 'Profile pamięci serii', 'Serienspeicherprofile', 'Profils de mémoire de série', 'Profili memoria serie'),
    'Export prompts and glossary…': ('Експортувати промпти й голосарій…', 'Eksportuj prompty i glosariusz…', 'Prompts und Glossar exportieren…', 'Exporter les prompts et le glossaire…', 'Esporta prompt e glossario…'),
    'Import prompts and glossary…': ('Імпортувати промпти й голосарій…', 'Importuj prompty i glosariusz…', 'Prompts und Glossar importieren…', 'Importer les prompts et le glossaire…', 'Importa prompt e glossario…'),
}

_INDEX = {'uk': 0, 'pl': 1, 'de': 2, 'fr': 3, 'it': 4}


def resolved_language(code='auto'):
    code = str(code or 'auto').lower().replace('_', '-').strip()
    if code != 'auto':
        return code if code in _INDEX or code == 'en' else 'en'
    try:
        from calibre.utils.localization import get_lang
        detected = str(get_lang() or '')
    except Exception:
        detected = locale.getdefaultlocale()[0] or ''
    prefix = detected.lower().replace('_', '-').split('-', 1)[0]
    return prefix if prefix in _INDEX else 'en'


def tr(text, language='auto'):
    language = resolved_language(language)
    if language == 'en':
        return text
    values = _T.get(str(text))
    return values[_INDEX[language]] if values else text


def localize_widget_tree(root, language='auto'):
    """Translate stable widget captions while preserving English source keys."""
    language = resolved_language(language)

    def translate_property(widget, getter, setter, property_name):
        source = widget.property(property_name)
        if source is None:
            source = getter()
            widget.setProperty(property_name, source)
        if source:
            setter(tr(str(source), language))

    translate_property(
        root, root.windowTitle, root.setWindowTitle, 'tp_source_window_title'
    )
    try:
        from qt.core import (
            QObject, QCheckBox, QComboBox, QGroupBox, QLabel, QLineEdit,
            QPushButton,
        )
        widgets = root.findChildren(QObject)
    except Exception:
        return
    for widget in widgets:
        if isinstance(widget, (QLabel, QPushButton, QCheckBox)):
            translate_property(widget, widget.text, widget.setText, 'tp_source_text')
        elif isinstance(widget, QGroupBox):
            translate_property(widget, widget.title, widget.setTitle, 'tp_source_title')
        elif isinstance(widget, QLineEdit):
            translate_property(
                widget, widget.placeholderText, widget.setPlaceholderText,
                'tp_source_placeholder',
            )
        elif isinstance(widget, QComboBox):
            source_items = widget.property('tp_source_items')
            if source_items is None:
                source_items = [widget.itemText(i) for i in range(widget.count())]
                widget.setProperty('tp_source_items', source_items)
            for index, source in enumerate(source_items):
                if index < widget.count():
                    widget.setItemText(index, tr(str(source), language))
