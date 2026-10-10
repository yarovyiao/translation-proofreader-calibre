# Changelog

## 0.11.15

- Moved surface polishing into a clearly labelled **Experimental polish
  (optional)** group. It is disabled and hidden by default, must be enabled in
  AI settings, and no longer appears as a post-save recommendation unless the
  user opted in. The UI now warns that this isolated pass deliberately omits
  glossary/book instructions and can therefore change established terms.
- Reordered editor actions so **Accept suggestion** and **Accept all
  suggestions** are adjacent, as are the single/all manual-edit actions.
- Added cumulative proofreading-pass tags. A first normal pass retains the
  simple `proofreaded` tag; later normal passes add `2pr`, `3pr`, and so on.
  Polishing preserves the counter without incrementing it and adds only the
  cumulative `polished` marker. Old numbered tags are replaced rather than
  accumulated.
- Added an interface-language selector with English, Ukrainian, Polish,
  German, French, and Italian, plus automatic Calibre/system detection. Russian
  is intentionally not offered. The main editor, settings, terminology window,
  and principal plugin menu actions have initial translations; untranslated
  secondary messages safely fall back to English.

## 0.11.14

- Normalized terminology rows containing an AI proposal but no human canonical
  to `AUTO`, including older synchronized profiles that previously retained a
  stale `UNRESOLVED` status. **Unresolved — no proposal** now means exactly that:
  neither a USER/CONFIRMED canonical nor an AI suggestion exists. Added a
  separate **AI suggestions** filter.
- Added **Polish selected**. It uses the same isolated temperature-0.1 surface
  prompt as Polish all but sends only the selected chunk rows, so failed blocks
  can be retried with another model without restarting the book.
- Changed **Polish all** into a resumable action within the current editor
  session. Chunks that already returned a successful polish result are skipped;
  failed/unprocessed safe chunks continue. **Polish selected** can still rerun a
  previously polished row deliberately.
- Made terminology research less mechanically literal without changing normal
  proofreading. Normal AI term checks use at least temperature 0.40 and deep
  checks 0.65 where the provider accepts temperature. The prompt now asks the
  model to decompose coined compounds, create natural genre adaptations, and
  treat awkward existing machine translations as noisy rather than authoritative
  evidence. Gemini deep-check retains expanded context and Search grounding.
- Split the crowded editor controls into two rows so the new selective-polish
  action remains readable at ordinary window widths.

## 0.11.13

- Stopped bracketed System notifications from bypassing the minimum-occurrence
  threshold indiscriminately. Numeric statistics, percentages, ranked rewards,
  and long requirement/value messages are no longer glossary candidates.
  Concise title-like bracket terms remain eligible, and a useful prefix such as
  `Achievement Hunter` can be retained without the trailing requirement text.
- Added cleanup of legacy scanner noise during the next terminology scan.
  Non-human numeric/UI rows are removed automatically; explicit USER and
  user-confirmed mappings are never pruned.
- Made terminology deletion persistent across a whole series profile. Deleted
  keys now leave profile tombstones, so an old private book memory cannot add
  them back on the next open, apply, or scan. An explicit new USER mapping can
  intentionally restore a deleted key.
- **Delete selected** now asks for confirmation and reports that the deletion is
  already saved to book/profile memory; **Save as** is not required.

## 0.11.12

- Added a separate read-only **AI suggestion** terminology column. AI research
  never overwrites a human canonical; USER/CONFIRMED text remains authoritative,
  while the proposal is used as an AUTO fallback only when no human canonical
  exists. A **Double translation** filter exposes disagreements and **Use AI for
  selected** promotes a proposal only after explicit confirmation.
- Newly discovered amber terms are now checked automatically after a local book
  scan through supported cloud providers. Manual **AI check new/selected** and
  **Deep-check selected** actions provide compact or expanded bilingual context;
  Gemini deep checks can additionally use Google Search grounding. Existing
  glossary decisions and statuses are deliberately omitted from these requests
  so that the result is an independent second opinion.
- Preserved AI proposals independently in book and series-profile memory. A
  newer proposal can refresh only AI-owned fields and cannot replace an older
  USER/CONFIRMED canonical, grammar note, or proper-name decision.
- Fixed **Add term…** selection capture when the user selects the target text
  before returning to select its source counterpart. Existing mappings can now
  be replaced from the book after an explicit old-to-new confirmation, and the
  result states whether it was stored only in book memory or also in the active
  series profile.
- Added explanatory tooltips to every terminology action and a warning before
  **Review all** when a saved series profile is selected but has not been
  applied.

## 0.11.11

- Split output metadata into real processing stages. Normal editing produces a
  `[Proofreaded]` title and `proofreaded` tag; an applied Polish pass produces a
  `[Polished]` title while retaining both `proofreaded` and `polished` tags.
  Merely loading a proofread book no longer mislabels an untouched save as
  polished.
- Added a target-language prefix to embedded titles and suggested filenames.
  Ukrainian uses the requested `UA_`; other recognized languages use their
  two-letter language code. Existing prefixes and stage suffixes are replaced,
  not duplicated on later exports.
- Made terminology labels and validation messages target-language neutral, and
  replaced Ukrainian-only wording in the universal proofreading/polishing
  prompts with general language-interference instructions.
- Confined the optional Ukrainian Hunspell/Russian-pattern detector to Ukrainian
  targets. Other configured languages no longer receive Ukrainian-specific
  local hints; the settings window explains whether a target-specific local
  detector is active.
- EPUB language metadata is now read for prefix selection when target-language
  auto-detection is enabled. An explicitly configured target language takes
  priority, avoiding stale translator metadata such as an English code on a
  Ukrainian translation.

## 0.11.10

- Corrected **Apply manual edit** to follow the pane the user actually edited.
  A hand-edited right-hand AI suggestion is now copied into Current translation,
  marked as a manual override, and written to EPUB without requiring a second
  **Accept suggestion** click. When the right pane is unchanged, the button
  continues to commit an edit made directly in Current translation.
- Extended **Apply all manual edits** to include hand edits made in either the
  middle or right pane, while **Accept all suggestions** protects both kinds of
  unfinished manual work.
- Added **Approve yellow rows…** with an explicit confirmation. For each yellow
  row it accepts a complete AI suggestion when available or approves the current
  translation otherwise. Red rows and rows with unapplied manual changes are
  excluded, and approved rows are visibly resolved.

## 0.11.9

- Fixed edited text in the right-hand AI suggestion pane being silently absent
  from output unless it had first been accepted. The pane and button tooltips
  now explain the distinction, and EPUB saving stops with the exact affected
  chunk numbers until the edited proposal is accepted or rejected.
- Fixed stale editor contents being captured into a newly loaded alignment when
  the just-saved EPUB was reloaded in the same window. This was the cause of
  false paragraph-boundary errors that could prevent the final polish pass.
- Made **Polish all** validate only the structurally safe chunks it will actually
  send. Risky alignment rows and unapplied translation/suggestion drafts are
  skipped rather than blocking every safe chunk in the book.
- Added regressions for suggestion acceptance, same-window reload, the save
  safeguard, and the real volume-10 8,098-character merged block output path.

## 0.11.8

- Fixed Calibre-library output by creating the new record through Calibre's
  GUI-visible database facade before attaching the EPUB format. Output keeps a
  distinct `[Proofreaded]` title and `proofreaded` tag.
- Fixed empty manual/suggestion edits being silently ignored by both XHTML
  replacement paths. A user can now remove an unrelated merged block entirely;
  real-volume regression tests cover the volume-5 9,600-character overflow and
  the volume-8 promotional insertion.
- Added an after-save option to reload the new EPUB as the current Translation,
  plus an isolated **Polish all** pass. It forces temperature 0.1, sends the
  bilingual pair, and excludes the Core prompt, book prompts/memory, glossary,
  and automatic repair rules.
- Explicit user edits made in an attached Book terminology window now update
  the corresponding saved profile term by a newer revision timestamp. Automatic
  scans still add missing keys only and cannot overwrite established records.
- The terminology title-bar X now commits the active edit. Explicit Book
  instructions and Book memory changes also synchronize to the active profile,
  including clearing those fields.
- Terminology scan feedback now separates detected candidates, genuinely new
  book terms, and keys added to the profile, and explains when every candidate
  already existed.

## 0.11.7

- Shortened the toolbar action caption to **TProofreader** while preserving the
  full plugin identity and window names.
- Added **Export prompts and glossary** and **Import prompts and glossary** to
  the plugin menu. A validated portable JSON bundle carries the global Core
  prompt and every saved series profile, including Book instructions, Book
  memory/notes, terminology, statuses, proper-name locks, and grammar notes.
- Added safe import conflict handling. Existing profiles can retain every old
  key and accept only missing terms, or be explicitly replaced by the imported
  copy after confirmation. The global Core prompt has its own confirmation.
- Portable bundles explicitly exclude API keys, endpoints, model/provider
  settings, and EPUB content.

## 0.11.6

- Made established series terminology immutable during later-book workflows.
  Applying, rescanning, loading, or automatically synchronizing another volume
  can add an absent source key but cannot change an existing canonical form,
  status, grammar note, proper-name flag, or user decision.
- Fixed profile terms inheriting a stale per-book `new_in_book` flag. Imported
  profile rows now remain unhighlighted; only genuinely absent keys discovered
  in the current volume are amber.
- Removed destructive automatic cleanup of persisted canonical values. A
  suspicious legacy mapping stays visible for manual correction but is omitted
  from AI requests instead of being silently blanked.
- Normalized harmless apostrophe, dash, case, and whitespace variations when
  identifying source keys, preventing typography-only duplicates.
- Made glossary use context-sensitive. Capitalized entries and proper names are
  matched case-sensitively, one-word lower-case entries are not sent as global
  rules, and every request identifies the canonical as a lemma to inflect only
  for the same source sense or entity.
- Added **Add to Calibre library** to output. A saved EPUB receives a distinct
  `[Proofreaded]` embedded title and `proofreaded` tag, then can be added as a
  separate Calibre book while retaining the selected output file.

## 0.11.5

- Canonical `AUTO` mappings are active glossary fallbacks again and are sent
  beside matching source paragraphs just like USER and user-confirmed entries.
  Explicit USER revisions and locked names retain higher merge priority;
  `UNRESOLVED` terms remain inactive.
- Added a **Series memory profiles** plugin submenu. Saved terminology profiles
  can be searched, filtered, edited, and saved without first loading two books.
- Added **Reopen last book pair** and a five-entry **Recent book pairs** submenu.
  Reopening a recent pair loads and aligns it automatically.
- Removed automatic terminology scanning from book load and **Apply profile**.
  The user can now apply the series baseline first, click **Scan book terms**
  deliberately, and reliably see additions from the current volume in amber.
- Removed the redundant series-memory **Update** button. Terminology Apply,
  Scan, and Close save immediately to the book and, when synchronization is
  checked, merge into the selected shared profile. The dialog reports where
  the edit was saved.

## 0.11.4

- Made current terminology explicit in every proofreading payload. Active
  USER/user-confirmed mappings that occur in a paragraph are now repeated in
  that pair's `required_glossary`, in addition to the compact system-prompt
  glossary. This prevents small/Flash models from overlooking a term in a
  chunk-wide instruction when a yellow row is reviewed again.
- The focused repair payload receives the same pair-local glossary. A fixed
  operational instruction identifies these mappings as mandatory current user
  decisions that override model guesses.
- Fixed quality checking for a confirmed source term that was already English
  in the current translation. Leaving `Hatchetman`, for example, now triggers
  the focused repair for `Hatchetman → Сокирник`; it is no longer ignored merely
  because the bad English text predates the latest AI response.
- Added a request audit to AI notes: every reviewed paragraph lists the exact
  glossary mappings sent with that pair. This makes it possible to distinguish
  a missing/inactive memory entry from a model that disobeyed an included one.
- Terminology is resolved afresh when Review selected/Review all creates each
  work item. A canonical edit made before starting the new review is not cached
  from the preceding run.

## 0.11.3

- Added an optional focused quality-repair pass for Experimental full
  proofreading. Only suspicious returned paragraphs are sent once more: newly
  copied source-language wording, model-authored corrections/asides in brackets,
  or a materially truncated response. Ordinary successful paragraphs still use
  a single request.
- A failed or still-suspicious repair no longer discards the AI text or turns it
  into an unreviewed placeholder. The latest suggestion remains visible and
  editable, is marked `uncertain`, and is excluded only from **Accept all**.
- Moved square-bracket cleanup behind the quality check so output such as
  `old wording [suggested correction]` is repaired as a sentence instead of
  merely losing its brackets.
- Added **Add term…** beside the manual-edit controls. Select corresponding text
  in Original and Current translation (or AI suggestion) to save a canonical
  USER glossary mapping. Without a source selection, the plugin offers
  unresolved terms found in the current chunk.
- Applying a compact, unambiguous manual word correction now offers to save the
  matching unresolved source term as USER memory. The plugin asks for a
  dictionary/base form rather than mechanically replacing every inflected
  occurrence; re-reviewing the chunk lets the AI apply it in context.
- Synchronized the installable Calibre archive and GitHub source archive at
  version 0.11.3 and added package-content/install smoke checks.

## 0.11.2

- Fixed **Apply profile** replacing a stronger local USER/CONFIRMED mapping
  with a weaker shared UNRESOLVED entry. Explicit local edits and locked proper
  names now survive both profile application and rescanning.
- Corrected legacy recovery precedence. Older established terms are rebuilt
  before later automatic scans, while newer explicit USER revisions can still
  update them. A legacy canonical term whose **Proper name** checkbox was set
  is treated as explicitly confirmed because that checkbox was always a manual
  action.
- Split terminology filtering into exact UNRESOLVED, legacy AUTO, USER,
  CONFIRMED, combined locked, and proper-name filters. AUTO is no longer shown
  under UNRESOLVED.
- Renamed the occurrence filter to **Present in source EPUB** and documented
  that counts come from the Original/source file.
- Added strong Latin/Cyrillic role validation. Reversed source/translation
  books can be swapped automatically; selecting two Cyrillic translations
  produces a warning before alignment or terminology scanning.

## 0.11.1

- Fixed series memory being replaced by the most recently opened or scanned
  book. Automatic synchronization and **Update** now accumulate missing terms,
  preserve older USER/CONFIRMED decisions, and keep old UNRESOLVED candidates.
- Made local rescans additive: they update current-book occurrence counts but
  never silently delete an older term. Only **Delete selected** removes one.
- Explicit terminology-table edits receive a revision marker, so a deliberate
  newer correction can update the shared series entry without allowing a stale
  book project or automatic scan to overwrite it.
- Added persistent per-book highlighting for newly discovered terminology.
  New rows are sorted first and shown in amber.
- Added terminology filters for new entries, terms present in the current book,
  imported series terms, unresolved entries, confirmed entries, and proper
  names, plus **Mark selected reviewed**.
- Added a one-time v4 profile repair. On first use, 0.11.1 rebuilds a profile
  from every linked per-book memory, writes a `.v4-backup`, and upgrades the
  accumulated profile to v5.

## 0.11.0

- Replaced the single overloaded review workflow with three modes:
  **Terminology replacement**, **Terminology + language cleanup**, and
  **Experimental full proofreading**.
- Strict modes accept only exact `old` to `new` localized operations. The
  plugin applies them to the existing translation and leaves all unaffected
  text unchanged, while the three editor panes and manual editing remain fully
  available.
- Replaced the long full-proofreading prompt with a compact literary-editing
  prompt designed for small/Flash Lite models. Existing installations using
  the old `term_decisions` prompt are migrated once and its text is retained as
  a local backup.
- AI terminology voting, alternatives, UNRESOLVED instructions, and automatic
  canonical selection are disabled. Only explicit USER and user-confirmed
  CONFIRMED mappings occurring in the current chunk are sent as a short plain
  glossary; character gender notes accompany those mappings.
- Legacy CONFIRMED entries created by the former vote threshold are downgraded
  to inactive AUTO on migration. Confirm them once manually or edit their
  canonical form to activate them again.
- Removed the Alternatives and vote-count columns from Book terminology.
  Legacy values remain readable in saved profiles but are never sent to the AI
  or allowed to override a canonical mapping.
- Added local language hints for leftover Latin text and strong Russian
  patterns. If a custom Ukrainian Hunspell dictionary is installed in Calibre,
  it is used as an additional suspicion filter; hints never modify text by
  themselves.
- Full proofreading removes newly introduced square brackets when the aligned
  source and current translation contain none. Glossary formatting now uses no
  square-bracket status syntax, preventing the model from imitating it.
- Increased the default per-book chunk target from 6,000 to 8,000 characters
  to provide more narrative context with the shorter prompt.

## 0.10.8

- Fixed intermittent loss of the last edited terminology cell. Pending table
  edits are now committed and saved before Apply, Scan, Confirm, Reset, Delete,
  and Close; selecting a row alone no longer changes its mapping status.
- Manually removed terminology alternatives are remembered as blocked variants
  and cannot be silently reintroduced by later AI decisions. Manual
  alternatives and their block list survive local rescans and series-profile
  updates.
- Added a per-term **Proper name** checkbox. A checked name keeps its confirmed
  target-script spelling locked across later proofreading requests while normal
  grammatical inflection remains allowed in prose.
- Tightened terminology prompts: AI decisions must report a canonical
  dictionary/nominative form, never a nearby word or an incidental inflection.
- Made the local candidate scan more conservative about contractions and
  capitalized ordinary words seen only at sentence starts, reducing entries
  such as `Iv'd`, `Thank`, `Apart`, and `Middle` without an API request.

## 0.10.7

- Added instant Book terminology search across source terms, canonical forms,
  gender/grammar notes, status, alternatives, and counts. The table filters as
  the user types and includes Previous/Next navigation with a match counter.
- Successful merged-duplicate cleanup is no longer reported as an unresolved
  alignment warning. Repairs are summarized in the main status line, while the
  warning dialog is generated only from issues that remain after cleanup and
  the final length/duplicate preflight.
- Verified on the Defiance of the Fall 3 pair: seven merged blocks are repaired
  and the final unresolved-warning count is zero.

## 0.10.6

- Reworded UNRESOLVED terminology instructions as an explicit required action,
  not an arrow mapping to the literal word `unresolved`. The model must choose
  a Ukrainian form from context and may never preserve the English source just
  because no canonical form has been confirmed.
- Up to six clean prior Ukrainian alternatives and their evidence are now sent
  with a relevant term, giving the model useful choices without sending the
  entire glossary.
- AI notes are clickable: selecting a numbered note scrolls to and highlights
  the corresponding paragraph in the original, current translation, and AI
  suggestion panes.
- Added local removal of recognizable model scratchpad/self-correction inserts
  such as `[Wait, let me fix...]` and angle-bracket translation reasoning. The
  removal is recorded in AI notes and uses no retry.
- Added **Apply all manual edits** with a live unapplied-count badge. **Accept
  all suggestions** now protects and skips chunks whose Current translation
  was edited manually.
- The Alternatives column is editable. Users can add or remove semicolon-
  separated Ukrainian variants while existing vote evidence is retained for
  unchanged entries; invalid source-language forms are rejected.
- Cyrillic-target terminology cleanup now also rejects mojibake/non-Cyrillic
  alternatives that contain no valid target-script text.

## 0.10.5

- Mostly complete AI responses are now recovered by stable pair id when at
  least 80% of the requested ids are valid. Missing or changed ids retain the
  current translation as an editable placeholder while all safely mapped
  suggestions remain available; no paid format retry is sent.
- A suggestion that newly introduces an English source term into a Cyrillic
  translation is shown for manual review and marked `uncertain` instead of
  discarding the entire request. Accept all skips it, while manual editing or
  explicit acceptance remains possible without repeated identical API calls.
- Canonical Ukrainian terminology containing Latin text is rejected before it
  can become USER/AUTO/CONFIRMED. Existing mixed forms such as `лорде Atwood`
  are reset to UNRESOLVED on the next book load, and invalid alternatives are
  removed.
- The terminology editor reports rejected manual mappings and asks for a
  Ukrainian semantic translation or target-script transcription.

## 0.10.4

- The load status now distinguishes automatically persisted private book
  memory from an active shared series profile and from a merely suggested,
  unapplied profile.
- Applying a shared profile whose name differs from the EPUB series metadata
  requires confirmation and explains the risk of sending unrelated prompts or
  terminology to the AI.
- Added an editable **Gender / grammar note** column to Book terminology.
  Character notes are stored with private and shared memory, shown in the
  automatic-memory preview, and sent only in requests where that character
  occurs.
- Gender/grammar notes are mandatory prompt constraints for pronouns, titles,
  and inflection. They are intentionally user-authored rather than inferred
  automatically from a potentially incorrect translation.

## 0.10.3

- **Keep translation** now explicitly approves the text currently shown for
  the selected chunk. It clears a stale blocked/mismatch warning while keeping
  the original API error in the notes for audit and writes that approved text
  to the output EPUB.
- Extended conservative merged-prefix repair to malformed blocks near the
  beginning of a chapter, where too few preceding pairs exist for the original
  backward-only check. Equal chapter counts, a plausible retained suffix, and
  at least five normally aligned following pairs are required before cleanup.
- Regression-tested the complete Defiance of the Fall 3 EPUB pair: all seven
  merged-prefix corruptions are repaired, including the two remaining 8,000+
  character prefixes, without changing the input EPUB.

## 0.10.2

- Clarified that UNRESOLVED means a canonical target-language form still needs
  to be chosen, never that the English source should be preserved.
- Descriptive terms, attack types, skills, items, and titles must be translated
  semantically; phonetic transcription is reserved for genuine proper names.
- Automatic terminology decisions identical or nearly identical to their
  English source—including bracket-wrapped forms—are rejected locally and
  cannot gain AUTO or CONFIRMED status.
- Existing invalid non-USER memory entries are cleared back to UNRESOLVED the
  next time the book is loaded. Explicit USER mappings remain untouched.
- For Cyrillic target languages, newly introduced source-language phrases in
  revised book text invalidate the response and trigger the configured response
  retry. This prevents English terminology from reaching AI suggestions.

## 0.10.1

- Added Major repairs mode, enabled by default, which distinguishes a badly
  translated but correctly located paragraph from a genuinely shifted pair.
- When neighboring context establishes the same narrative location, the AI is
  now explicitly allowed to rewrite severe mistranslations and translate
  locally omitted source sentences, marking substantial changes as major.
- Genuine cross-chapter or shifted-boundary mismatches remain unchanged and
  excluded from Accept all to retain the duplicate-text safeguard.
- Added an output sanitizer for model-generated alignment annotations such as
  `(Note: p002069 input text does not match)`. Removed annotations are reported
  in AI notes and can never be accepted as book text.
- Major repairs is stored independently in every AI settings profile.

## 0.10.0

- Added persistent series-memory profiles containing the book instructions,
  automatic-memory notes, and complete terminology database while keeping the
  universal core prompt separate.
- Series profiles can be selected from the main editor, applied to a new book,
  updated automatically, saved under a new name, updated manually, or deleted.
- Applying a profile merges its established terminology with candidates from
  the current book, so later volumes retain canonical names while adding new
  terms.
- Added Clear book memory, which removes the current book prompt and glossary
  without deleting any saved series profile.
- EPUB title and series metadata are read to suggest a useful profile name and
  to preselect an existing matching series profile without applying it
  automatically.
- Added reusable AI settings profiles for provider, API key, endpoint, model,
  languages, temperature, chunk size, retries, and timing. The universal core
  prompt is deliberately excluded from these profiles.

## 0.9.2

- Accept a complete proofreading JSON response even when the model appends
  explanatory text or repeats a second JSON object after it.
- Reassemble responses that split one result across multiple complete JSON
  objects while retaining strict result-count and stable-id validation.
- Added a separate configurable retry count for malformed AI responses; the
  default is three attempts and does not consume network-retry attempts.
- After all format retries fail, mark only that chunk as blocked and continue
  reviewing the rest of the book instead of aborting the entire batch.

## 0.9.1

- Recompute chapter length ratios and duplicate checks from the cleaned
  in-memory translation rather than the original malformed merged block.
- Repaired paragraphs now participate in normal safe chunk packing and join a
  neighboring request whenever both book sides remain below the configured
  character limit.
- On the affected Defiance of the Fall 4 pair, this removes all five stale
  chapter-length warnings and reduces the default 6,000-character plan from
  234 to 219 API requests.

## 0.9.0

- Added automatic local cleanup of high-confidence merged duplicate prefixes.
- The detector splits malformed single-`<p>` blocks at repeated `<br>` pairs,
  maps their trailing sequence against consecutive source blocks using strict
  relative-length rules, and keeps only the final current translation.
- Cleaned text—not the duplicate prefix—is sent to the configured AI, without
  any additional API request.
- Added a Content repair setting to enable or disable automatic cleanup.
- Added `duplicate prefix removed` alignment status and an Inspect removed
  duplicate action for auditing the discarded text.
- Input EPUBs remain byte-for-byte unchanged; only a newly saved output EPUB
  receives the cleanup.

## 0.8.1

- Added local detection for giant merged translation elements, including the
  malformed single-`<p>`/many-`<br>` pattern found in translated EPUB output.
- Such rows are red and labeled `extra / merged translation`; batch review and
  bulk acceptance treat them as unsafe.
- Explicit manual repair of a detected merged block now removes its obsolete
  inline breaks instead of redistributing the replacement sentence across
  dozens of stale text slots.
- Added Book instructions and Automatic book memory groups to AI settings.
  They are active for the currently loaded book project and explain why they
  are unavailable when settings is opened globally.

## 0.8.0

- Added a local per-book terminology scanner and persistent `book_terms.json`.
- Added `USER`, `AUTO`, `CONFIRMED`, and unresolved terminology states with
  conflict vote counts and a configurable cross-chunk confirmation threshold.
- Added terminology decisions to normal proofreading responses without extra
  API calls; unknown terms are rejected locally and only relevant terms are
  sent with each chunk.
- Added the Book terminology editor, local rescan, manual locking, confirmation,
  reset, deletion, and Clear auto memory controls.
- Split prompts into universal core, per-book user, and automatic book-memory
  layers with separate editors.
- Added an explicit Apply manual edit action for blocked or mismatched chunks.
- Widened the API key and endpoint controls.
- Added post-save EPUB ZIP, mimetype, package spine, duplicate-spine, and
  modified-XHTML validation.

## 0.7.0

- Added proportional synchronized vertical scrolling across the original,
  current translation, and AI suggestion panes.
- Added Ctrl/Shift extended row selection and a Review selected action for
  processing several chosen chunks sequentially.
- Added temperature guidance to the settings tooltip and documentation.
- Changed the default temperature for new installations from 0.2 to 0.5;
  existing saved preferences remain unchanged.

## 0.6.0

- Replaced one-row-per-paragraph review with configurable multi-paragraph
  chunks targeting 6,000 characters per book side by default.
- One chunk now produces one API request containing stable paragraph IDs and a
  strictly validated result array.
- The chunk list shows the opening source text and original-plus-translation
  character counts for easier navigation.
- Manual chunk editing preserves the paragraph mapping through blank-line
  boundaries and blocks saving if their count changes.
- Batch confirmation now reports the actual expected API request count.

## 0.5.0

- Added separate original and translation language controls on a single row.
- Added an independent Auto-detect checkbox for each language. Detection is
  performed by the selected AI within the normal proofreading request.
- Migrates existing custom prompts by adding the new `{source_language}`
  instruction when needed.
- Translated all UI text, errors, warnings, tooltips, and documentation into
  English for public distribution.
- Documented how the translated EPUB is used as the output template and the
  limitations of inline-format preservation and navigation handling.
- Protected preformatted, code, and math blocks from whitespace-destroying
  edits, and excluded explicitly hidden elements from AI input.

## 0.4.0

- Added the supplied toolbar and context-menu icon.
- Library pickers now show Calibre tags beside every book title.
- Added a two-selected-books command that is disabled unless exactly two EPUB
  books are selected.
- A unique `translated` tag automatically identifies the translation even when
  both selected books have the same title.
- Selecting a single tagged translation pre-fills the translation field rather
  than the original field in the manual editor.

## 0.3.0

- Added a local duplicate/corruption preflight before API processing.
- Detects long exact overlaps with the previous substantive chapter.
- Detects chapter-length ratio outliers against the original book.
- Duplicate rows are red; length outliers are yellow.
- Batch analysis skips all structurally risky passages. Individual analysis
  requires explicit confirmation.

## 0.2.0

- Added DeepSeek and local OpenAI-compatible providers.
- Added model discovery for Gemini, DeepSeek, Ollama, LM Studio, and compatible
  `/models` endpoints.
- Added configurable retries, exponential backoff, and request intervals.
- Added non-retrying content-filter handling that continues batch processing.
- Added structural and AI alignment warnings with yellow/red row highlighting.
- Accept All now skips risky alignments.
- Clarified the combined character cap and translation-only EPUB output.

## 0.1.0

- Initial EPUB-first proofreader MVP.
- Gemini and OpenAI-compatible API clients.
- Side-by-side paragraph review, batch analysis, and EPUB export.
