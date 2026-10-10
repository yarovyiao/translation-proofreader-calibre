# Translation Proofreader for Calibre

Translation Proofreader is a Calibre interface-action plugin for comparing an
original EPUB with a translated EPUB and conservatively editing the translation
through an AI API.

## Version 0.11.15

- Calibre 7+ / Qt 6, tested against Calibre 9.12 on Windows.
- EPUB input and EPUB output.
- Books can be selected from the current Calibre library or from disk.
- Library choices show Calibre tags, so identically named originals and
  translations remain distinguishable.
- The plugin menu can open exactly two selected EPUB books directly. If exactly
  one has the `translated` tag, it is assigned to the translation side
  automatically; otherwise the library selection order is used.
- The plugin menu remembers the five most recently loaded original/translation
  file pairs. **Reopen last book pair** loads and aligns the newest pair in one
  step; the **Recent book pairs** submenu can reopen any of the five.
- Includes a dedicated toolbar/context-menu icon.
- Uses the compact toolbar caption **TProofreader** while retaining the full
  **Translation Proofreader** name in Calibre's plugin list and dialogs.
- Three-pane editor: original, current translation, and AI suggestion.
- The suggestion pane is now labelled as a proposal: edits made there must be
  accepted before output. Saving is blocked with the affected chunk numbers
  instead of silently discarding an edited but unaccepted suggestion.
- Proportional synchronized vertical scrolling across all three text panes.
- Ctrl/Shift multi-row selection for reviewing several chosen chunks in one
  operation. Each selected chunk remains one API request.
- Consecutive aligned paragraphs are grouped into readable review chunks. The
  default target is up to 8,000 original characters plus up to 8,000
  translation characters per API request.
- The chunk table shows a text preview and per-side character counts instead of
  one row per XHTML paragraph.
- Every paragraph inside a request has a stable ID. The AI must return exactly
  one result for each ID, so a chunk can be mapped safely back to the original
  translation XHTML blocks.
- Review one or several selected chunks, or send all pending chunks
  sequentially.
- An optional, explicitly experimental **Polish all** final pass forces
  temperature `0.1` and uses only
  the aligned original/current translation with a compact surface-edit prompt.
  It excludes the Core prompt, Book instructions, Book memory, and glossary,
  correcting spelling, grammar, punctuation, language interference,
  mixed-language forms, untranslated words, and malformed wording without broad
  literary rewriting.
  Structurally risky rows and unapplied drafts are skipped without preventing
  the remaining safe chunks from being polished. Polishing controls are hidden
  by default and must be enabled in AI settings. Because this isolated pass
  deliberately excludes the glossary, it can change established terms and its
  results should be reviewed manually.
- **Polish selected** applies that same isolated surface pass only to selected
  rows. Within one editor session, **Polish all** resumes failed/unprocessed
  chunks and skips rows that already returned a successful polish result;
  selecting a row explicitly can rerun it with another model.
- Repeated normal review passes are recorded with cumulative `2pr`, `3pr`, …
  Calibre tags. The first pass remains `proofreaded`; polish adds `polished`
  without incrementing the proofreading counter.
- Interface language can be set to English, Ukrainian, Polish, German, French,
  or Italian, or follow Calibre/system language automatically. Missing
  secondary translations fall back to English.
- Three review modes: exact terminology replacement, terminology plus local
  language cleanup, and experimental full literary proofreading.
- Strict modes accept only validated exact substitutions; full proofreading
  uses a compact prompt and locally removes newly invented square brackets
  when the aligned source context has none.
- Accept, reject, or manually edit every suggestion. **Apply manual edit** uses
  the right AI-suggestion pane when you changed it by hand; otherwise it applies
  the middle Current translation pane. The chosen text becomes EPUB output
  immediately, without an additional **Accept suggestion** click.
- **Approve yellow rows…** resolves every yellow row after one explicit
  confirmation. It accepts the AI proposal when one exists and otherwise keeps
  Current translation; red rows and unapplied manual edits remain untouched.
- Gemini API, DeepSeek API, other OpenAI-compatible cloud endpoints, and local
  OpenAI-compatible servers such as Ollama, LM Studio, and llama.cpp.
- Connection/model discovery button that fills an editable model dropdown.
- Wider API key and endpoint fields, matching the provider/model controls.
- Editable source/target languages with independent auto-detection. Core
  proofreading and polishing prompts are target-language neutral. Ukrainian
  Hunspell/Russian-pattern hints are an optional local detector enabled only
  when Ukrainian is the configured target; other languages use the general AI
  review without those hints.
- Compact full-proofreading prompt, editable per-book instructions, and active
  book memory/notes.
- Persistent series-memory profiles accumulate terminology across later
  volumes instead of letting the newest book replace older entries. Automatic
  apply/scan synchronization can add only absent keys. An explicit human edit
  in the attached Book terminology window carries a newer revision and updates
  that exact shared key; stale book copies and occurrence counts cannot do so.
- The editor can save, apply, accumulate absent keys, detach, or permanently
  delete a series profile. Clear book memory resets only the current book and
  never deletes a saved profile. A **Series memory profiles** submenu opens any
  saved profile for explicit editing even when no books are loaded.
- **Export prompts and glossary** writes the global Core prompt and every saved
  series profile to one portable `.tproofreader.json` file. Each profile carries
  Book instructions, Book memory and notes, terminology, statuses, proper-name
  locks, and gender/grammar notes. **Import prompts and glossary** restores the
  data on another computer with safe merge or explicit replacement for naming
  conflicts. API keys and provider settings are deliberately excluded.
- Profiles created by 0.11.0 are repaired once from their linked per-book
  memories when first loaded by 0.11.1; the original profile file is retained
  beside it with a `.v4-backup` suffix.
- Private book memory is saved automatically for each original EPUB and is
  restored when that book is reopened. It does not become shared series memory
  until **Save as** is used. The load status identifies which kind is active.
- Applying a profile whose name differs from the EPUB series metadata shows a
  confirmation warning before unrelated memory can be sent to the AI.
- EPUB series metadata is used as the suggested profile name. A matching saved
  profile is preselected but never applied without an explicit click.
- Reusable AI settings profiles preserve provider/model/API and tuning values
  for Gemini, DeepSeek, other compatible services, or local servers. The core
  proofreading prompt remains global and is not copied into these profiles.
- AI settings shows the compact full prompt, book instructions, and active
  book memory/notes. Per-book controls become
  active when settings is opened from a proofreader window after loading two
  books; otherwise their tooltips explain how to activate them.
- A local terminology scan finds recurring names, bracketed terms, likely
  LitRPG/System terminology, and conservative recurring two-to-four-word
  phrases. The scan uses no AI request.
- Minimum occurrences now applies conservatively to bracketed content as well.
  Numeric characteristics, percentages, ranked rewards, and long System
  requirement/value messages are rejected natively instead of becoming
  one-off glossary rows. Concise title-like bracket terms can still qualify.
- After that local scan, newly discovered amber rows are automatically sent to
  a supported cloud model for an independent contextual proposal. Existing
  canonicals and statuses are not disclosed to the model. Local providers are
  excluded from this terminology-research step.
- Terms first discovered in the current book are sorted first and highlighted
  in amber. The terminology editor can filter new, present, series-profile,
  unresolved, automatic, USER, CONFIRMED, locked, or proper-name entries
  and mark selected new entries reviewed without changing their mapping status.
- The separate read-only **AI suggestion** column never overwrites a human
  canonical. **Double translation** filters rows where both values exist and
  differ; **Use AI for selected** makes the proposal authoritative only after
  explicit confirmation. When the human canonical is blank, the AI proposal is
  the lower-priority AUTO fallback used during proofreading.
- Rows with an AI proposal and no human canonical are normalized to `AUTO`.
  **Unresolved — no proposal** therefore shows only untouched terms, while
  **AI suggestions** provides the complementary filter.
- **AI check new/selected** performs a normal contextual second opinion.
  **Deep-check selected** sends more bilingual excerpts and, for Gemini, enables
  Google Search grounding. Grounded searches may use a separate provider quota
  or incur charges.
- Terminology research deliberately samples more freely than proofreading:
  normal checks use at least temperature 0.40 and deep checks 0.65 when the
  provider supports it. The term prompt asks for semantic decomposition and a
  natural genre adaptation of compounds instead of copying an awkward literal
  form already present in the translation. Proofreading and final polish keep
  their own configured/forced temperatures.
- **Present in source EPUB** means that the source scanner found the
  term in the file selected as Original. A strong script check offers to swap
  reversed books and warns before continuing with two Cyrillic translations.
- Per-book terminology is persisted as `book_terms.json` in the Calibre
  profile. Canonical `AUTO`, explicit `USER`, and user-confirmed `CONFIRMED`
  mappings are sent to the AI, and only when the source term occurs in the current chunk.
  Each mapping is also attached directly to every matching paragraph as
  `required_glossary`, including a focused repair request. AI notes show
  **Plugin request glossary (sent with this pair)** so the exact terminology
  used for a repeated review can be verified without guessing.
- AI terminology voting and competing alternatives are disabled. A canonical
  `AUTO` entry is an active fallback, but cannot override a newer explicit USER
  decision. `UNRESOLVED` entries remain available for inspection and are never
  sent as instructions.
- Glossary canonicals are sent as dictionary/reference forms, not literal
  replacement strings. Capitalized terms and proper names match the aligned
  source case-sensitively; ambiguous one-word lower-case entries stay visible
  in memory but are not sent to the AI. The model is instructed to verify the
  same contextual sense and inflect the target form naturally.
- Existing invalid source or mixed-language canonical mappings are reset when
  the book is next loaded, including USER mappings. For a Cyrillic target,
  canonical fields cannot store Latin/source-language text such as
  `лорде Atwood`.
- In Experimental full proofreading, a local quality check detects newly copied
  source-language wording (including a single term), model-authored bracketed
  corrections, and materially truncated answers. The optional **Suggestion
  repair** setting sends only those suspicious paragraphs through one focused
  repair request. If the repair is still doubtful, its text remains visible and
  editable with an `uncertain` warning and is excluded from Accept all; it is
  never replaced with a blank or unchanged placeholder merely because English
  remains.
- If an active glossary source term remains in English, the same quality check
  applies even when that English wording was already present before the latest
  request.
- A terminology editor exposes canonical target-language forms, gender/grammar,
  a separate AI proposal, proper-name locks, occurrence counts, and status.
  Manual USER mappings are locked against AI replacement.
- Book terminology has an instant search/filter across every visible field,
  plus Previous/Next navigation and a live match count.
- Pending terminology cell edits are committed before Apply, Scan, and Close,
  so the final active edit is not silently lost. The title-bar X now saves it
  too; **Save as** is not required after ordinary edits to an attached profile.
- Deleting a terminology row is also saved immediately. A synchronized series
  profile keeps a deletion marker, preventing stale memories from older books
  from resurrecting that source term. A later explicit USER mapping is the only
  action that intentionally restores it.
- **Add term…** remembers the most recently selected target wording while you
  return to select the source wording. If that source already has a different
  canonical, the plugin shows the old and proposed values and asks explicitly
  before replacing it. The success message identifies whether the mapping was
  saved only for the current book or synchronized into its series profile.
- A **Proper name** checkbox locks the confirmed target-script spelling. The AI
  may still use normal grammatical inflections in prose but cannot invent a
  competing transliteration.
- Book terminology also stores an optional editable **Gender / grammar note**
  for characters, such as `male; elderly man; use masculine forms`. It follows
  the term into a saved series profile and is included only when that character
  occurs in the current request.
- Separate original and translation language settings on one row. Either
  language can be fixed or inferred by the configured AI during the same
  proofreading request.
- Configurable network attempts (10 by default), malformed-response attempts
  (3 by default), exponential backoff, timeout, and interval between chunks.
- Network failures, HTTP 429, and temporary 5xx errors are retried. Content
  filter blocks are not retried; the affected row is marked and batch work
  continues.
- Valid proofreading JSON is recovered when a model appends prose or repeats a
  second JSON object. Incomplete/split JSON responses are retried separately;
  after the final failure only that row is marked and the batch continues.
- If at least 80% of a multi-pair response retains valid stable ids, those
  results are recovered immediately. Missing ids use the current translation
  as an editable placeholder, so `27 items for 28 input pairs` does not discard
  27 useful suggestions or consume three identical retries.
- Structural alignment warnings and AI alignment classification. Risky rows are
  highlighted, and Accept All skips them.
- Numbered AI notes are clickable and select the corresponding paragraph in all
  three text panes for immediate comparison.
- Experimental full proofreading allows complete rewrites and local
  translation of omitted source sentences when neighboring context proves that
  both sides belong to the same narrative location. Low textual similarity
  alone is not treated as a mismatch.
- Genuine shifted boundaries remain protected. Model-generated service notes
  containing pair IDs and mismatch warnings are removed from the revised text
  locally and shown only in AI notes.
- **Apply manual edit** explicitly approves the current translation text and
  clears the red blocked/mismatch state for that chunk without inventing an AI
  suggestion.
- **Add term…** saves selected corresponding source and target wording as a
  locked USER glossary entry. Applying a small unambiguous manual correction
  can offer the same mapping automatically. The canonical form is confirmed by
  the user; no global textual replacement is attempted, so normal target-language
  inflection is left to a later review of that chunk.
- **Apply all manual edits** approves every changed Current translation pane at
  once and shows the number of unapplied edited chunks on the button. Accept
  all suggestions never overwrites these pending manual edits.
- **Keep translation** approves the text currently shown without accepting the
  AI suggestion. It also resolves a red content-filter/API or mismatch state;
  the original error remains visible in AI notes for audit.
- Recognizable model scratchpad inserts such as `[Wait, let me fix...]` and
  angle-bracket translation reasoning are removed locally before display, with
  an audit note and no additional API call.
- A local duplicate preflight detects long exact overlaps with the preceding
  substantive chapter and chapter-length outliers relative to the original.
  Risky passages are excluded from batch API requests.
- Giant translated blocks containing many line breaks but paired with a short
  source block are inspected locally as possible merged duplicate prefixes.
  When the trailing fragments match the consecutive source-block structure by
  conservative length ratios, the plugin keeps only the final current
  translation and labels the row `duplicate prefix removed`.
- The same cleanup can use at least five normally aligned following pairs for
  a malformed block near the start of a chapter, where too few preceding pairs
  exist. This still requires equal chapter block counts and plausible length
  ratios; uncertain text is never deleted automatically.
- Confirmed cleanup uses no AI call, sends only the retained translation to
  proofreading, and removes obsolete `<br>` elements from the output EPUB.
  **Inspect removed duplicate** shows the discarded text. Unconfirmed cases
  remain red as `extra / merged translation` and are skipped by Review All.
- Completed automatic repairs are summarized in the editor status rather than
  mislabeled as incomplete alignment. The warning dialog is produced only from
  structural, length, duplicate, or overflow issues still unresolved after the
  complete cleanup/preflight pass.
- Automatic merged-prefix cleanup is enabled by default and can be disabled in
  AI settings. The input EPUB is never modified; disabling the option and
  reloading restores its original in-memory text.
- Chapter-length and duplicate preflight metrics are recalculated from the
  cleaned in-memory translation before chunking. Repaired short paragraphs can
  therefore share a normal request with neighboring paragraphs instead of
  consuming a request by themselves, while both sides still remain within the
  configured character target.
- The translated EPUB is never overwritten; the plugin requires a new output
  file.
- Saved output receives a non-duplicating target-language prefix such as
  `UA_`, `PL_`, or `EN_`. Its title ends in the actual latest stage:
  `[Proofreaded]` after editing or `[Polished]` after an applied Polish pass.
  Metadata tags retain processing history: polished output contains both
  `proofreaded` and `polished`. **Add to Calibre library** is enabled by default
  when a library is available and creates a GUI-visible separate book entry
  through Calibre's normal database path while retaining the selected disk file.
- Saving asks whether to reload the new EPUB as the current Translation. If
  accepted, **Polish all** becomes available for the isolated final pass.
- An explicitly blank manual edit is written as a real removal. Corrupt merged
  or unrelated one-block content no longer survives output because the XHTML
  writer silently ignored an empty replacement.
- Inline XHTML markup is preserved, with revised words redistributed across
  the existing inline text nodes.
- Every saved file is checked for ZIP integrity, EPUB mimetype ordering,
  package/spine consistency, duplicate spine entries, and valid modified XHTML.

## Installation

1. Open Calibre Preferences.
2. Choose Plugins, then Load plugin from file.
3. Select `Translation Proofreader.zip` and confirm the warning.
4. Restart Calibre if requested.
5. Add **Translation Proofreader** to a toolbar through
   Preferences > Toolbars & menus, if it is not shown automatically.
   Its visible toolbar caption is **TProofreader** to save horizontal space.
6. To use the two-book shortcut on right click, also add **Translation
   Proofreader** to **The context menu for books in the calibre library** in the
   same Calibre settings page.

## Use

1. Open the plugin and click **AI settings**.
2. Select Gemini, DeepSeek, another cloud provider, or a local server. Enter the
   API key and endpoint, then use **Connect and fetch models**. Local servers
   do not need a key unless you configured authentication.
3. Select an original EPUB and its translated EPUB. Calibre tags are displayed
   in both library pickers. Alternatively, select exactly two EPUB books in the
   main library, open **Translation Proofreader**, and choose **Proofread two
   selected books**. That command is disabled for any other selection count.
4. Click **Load and align**.
5. If this volume belongs to an existing series, select and **Apply profile**.
   The plugin deliberately does not scan automatically: click **Scan book
   terms** afterward when you want to discover this volume's terms and see them
   highlighted in amber. Optionally add book instructions such as
   `Loamwalker -> Землеходець`.
6. Review the chunk alignment before batch processing. If the two books
   have different paragraph counts, the plugin uses position-based alignment,
   displays a warning, and highlights risky rows.
7. Analyze individual chunks or all pending chunks. Batch analysis can
   incur API charges.
8. Accept suggestions, use **Apply manual edit** where necessary, and save a
   new EPUB. Leave **Add to Calibre library** checked to create a separate
   language-prefixed library entry with the matching proofreading/polishing
   title status and cumulative tags.

## Portable backup and transfer

Open the plugin arrow menu and choose **Export prompts and glossary**. The one
portable file contains the Core prompt and all saved series profiles. **Save
as** already stores a book's Book instructions, Book memory and notes, and full
terminology inside its series profile; private book-only memory must therefore
be saved as a profile once before exporting. On another computer, install the
plugin and choose **Import prompts and glossary**. Existing profiles are kept
immutable by default, or can be explicitly replaced after confirmation.

The portable file never contains API keys, endpoints, provider/model settings,
or EPUB text.

## Privacy and limitations

- The selected passages are sent to the configured third-party API.
- The API key is stored locally in the Calibre profile using Calibre's JSON
  preferences. It is not encrypted by this plugin.
- DRM-protected books are not supported.
- The chunk-size setting is a per-book character target. At the default 8,000,
  one request contains up to roughly 8,000 original characters and 8,000
  translation characters, plus the prompt and JSON structure. It is not a
  token limit. A single indivisible paragraph that exceeds the target is sent
  alone without truncation.
- Paragraph alignment is structural and position-based. Books whose translation
  merges, splits, removes, or reorders many paragraphs need manual alignment
  review. A later version can add semantic alignment and checkpoint caching.
- The saved EPUB contains only the edited translation. Original passages and AI
  notes are never inserted into the book.
- The translated EPUB is the output template. Its package metadata, navigation
  files, stylesheet, images, fonts, chapter order, and block structure are
  copied unchanged. Only accepted text inside matched translation XHTML blocks
  is rewritten.
- Existing inline tags such as emphasis, strong text, and links are retained.
  When a revision changes the word count, words are redistributed across the
  existing inline text slots proportionally; the exact bold/italic boundary can
  therefore shift and should be spot-checked in heavily styled passages.
- Preformatted and code/math blocks are deliberately left unchanged so their
  whitespace and syntax cannot be damaged. Elements explicitly hidden with an
  HTML attribute, `aria-hidden`, or an inline `display:none`/`visibility:hidden`
  style are excluded from AI input and retained unchanged.
- Hidden or malformed source text is never imported from the original EPUB.
  External CSS selectors are not fully evaluated, and any broken table of
  contents already present in the translated EPUB is preserved as-is because
  version 0.9.1 validates but does not rebuild navigation.

## Temperature guidance

- `0.2–0.4`: highly conservative corrections.
- `0.5–0.7`: more flexible literary editing while the alignment safeguards and
  conservative prompt remain active.
- Above `0.8`: increasing risk of unnecessary rewriting and terminology drift.

The default for new installations is `0.5`. Existing saved settings are not
overwritten during an upgrade.
- The plugin does not currently add the output EPUB back to the Calibre library
  automatically.

## License and inspiration

GPL-3.0. The Calibre integration patterns and product direction were informed
by BookFere's GPL-3.0 Ebook Translator plugin; this is a separate implementation
specialized for bilingual proofreading.
