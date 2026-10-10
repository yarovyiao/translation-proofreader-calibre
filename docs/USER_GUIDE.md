# Translation Proofreader 0.11.15 — Installation and First Run

## What it does

Translation Proofreader is a Calibre plugin for comparing an original EPUB
with an existing translation. It presents the original, current translation,
and AI suggestion side by side. You can accept, reject, or manually edit each
suggestion before saving a new EPUB.

## Installation

1. Open **Calibre → Preferences → Plugins**.
2. Click **Load plugin from file**.
3. Select `Translation Proofreader.zip` and confirm the warning.
4. Restart Calibre if requested.
5. Add **Translation Proofreader** through **Preferences → Toolbars & menus**.
   The compact caption shown on the toolbar is **TProofreader**.
6. To use it from a book's right-click menu, also add it to **The context menu
   for books in the calibre library**.

## Selecting books

- Calibre tags are shown beside book titles in the library picker.
- If exactly one of two selected books has the `translated` tag, that book is
  assigned to the translation side automatically.
- **Proofread two selected books** is enabled only when exactly two selected
  books both contain an EPUB format.
- If neither book, or both books, have the tag, the visible library order is
  used: original first and translation second.
- **Reopen last book pair** loads and aligns the most recent pair immediately.
  **Recent book pairs** retains up to five existing file pairs; it remembers
  paths and saved memory, not unaccepted AI suggestions from an old window.

## API and language settings

1. Open the plugin menu and choose **AI settings**.
2. Select Gemini, DeepSeek, another OpenAI-compatible service, or a local
   Ollama, LM Studio, or llama.cpp server.
3. Enter the API key and endpoint. A local server usually does not need a key.
4. Click **Connect and fetch models**, then select an available model.
5. Under **Languages**, enter the original and translation languages or enable
   **Auto-detect** independently for either one.
6. Optionally click **Test proofreading**, then save the settings.

The **Interface language** setting offers English, Ukrainian, Polish, German,
French, and Italian, plus automatic Calibre/system detection. Reopen a plugin
window after changing this setting. **Experimental polishing** is disabled by
default; enable it only if you want the separate surface pass and intend to
review terminology changes manually.

Choose a **Review mode**:

- **Terminology replacement (strict)** changes only active glossary terms.
- **Terminology + language cleanup** also proposes exact localized corrections
  for leftover source-language text, interference from another language,
  mixed-language forms, and suspicious word choices.
- **Experimental full proofreading** lets the AI rewrite poor paragraphs,
  restore locally missing text, and perform literary editing. Its suggestions
  require manual review; manual editing remains available in every mode.

**Characters per request** controls chunking. The default 8,000 means up to
approximately 8,000 characters from the original and 8,000 from the existing
translation in one request. It does not mean one request per paragraph.

**Request attempts** controls retries for connection loss, rate limits, and
temporary server errors. **Invalid response attempts** separately controls
retries when the model returns incomplete or malformed proofreading JSON; the
default is three. If all such attempts fail, only that chunk is marked red and
the rest of a whole-book review continues. Saving preserves the current
translation for the failed chunk.

A mostly complete response is not retried when at least 80% of its stable pair
ids remain usable. Valid suggestions are displayed, while every missing id is
filled with its unchanged current translation and marked uncertain. Likewise,
a full-proofreading suggestion that introduces source-language wording, adds an
ungrounded correction in brackets, or appears truncated can use exactly one
focused **Suggestion repair** request. Only suspicious paragraphs are resent.
If that repair remains doubtful, its returned text stays visible and editable,
is marked uncertain, and is excluded from Accept all. Completely unparseable
or mostly unmappable responses still use the configured invalid-response
attempts.

Experimental full proofreading permits a full rewrite or local translation of
missing source sentences when surrounding pairs establish that both sides
belong to the same scene. Strict modes never replace a complete paragraph.

Automatic language detection is performed by the configured AI as part of the
normal proofreading request. It does not make a separate API request.

Common local endpoints:

- Ollama: `http://127.0.0.1:11434/v1/chat/completions`
- LM Studio: `http://127.0.0.1:1234/v1/chat/completions`
- llama.cpp server: `http://127.0.0.1:8080/v1/chat/completions`

The API key is stored locally in the Calibre profile and is not encrypted by
this plugin.

## Proofreading workflow

1. Choose the original EPUB and translated EPUB.
2. Click **Load and align**.
3. Inspect the generated chunk list. Each row shows the beginning of its source
   text and the original-plus-translation character count.
4. Inspect warnings and highlighted rows before batch processing.
5. Select one or more chunks and click **Review selected**, or use **Review all**
   for all safe pending chunks. Ctrl and Shift extend the row selection.
   Normally each chunk uses one API request; API usage may incur charges.
6. Accept suggestions or edit the translation manually. Blank lines preserve
   the paragraph boundaries within a chunk.
7. For a manual correction—especially a blocked or mismatched row—click
   **Apply manual edit**. This explicitly marks that text for output.
   To remember a corrected term, select its counterpart in Original and the
   desired wording in Current translation, then click **Add term…**. For a
   compact unambiguous edit, Apply manual edit can offer that USER mapping
   automatically. Confirm the canonical/base form, then review the chunk again
   if you want the AI to inflect the term in context.
8. If several Current translation panes were edited, use **Apply all manual
   edits**. Its counter shows how many edited chunks remain unapplied, and
   **Accept all suggestions** will not overwrite them.
9. Click a numbered entry in **AI notes** to select and reveal its corresponding
   paragraph in all three upper panes.
10. To retain and approve the translation currently shown without using the AI
   suggestion, click **Keep translation**. This resolves the red state even
   after a content-filter error while preserving the error text in AI notes.
11. Click **Save new EPUB**. The plugin validates the archive, package spine,
   modified XHTML, and output metadata before reporting success. With **Add to
   Calibre library** checked, it also creates a separate language-prefixed book
   with the actual `[Proofreaded]` or `[Polished]` status. A polished book keeps
   both `proofreaded` and `polished` tags; the selected output file remains on
   disk as well.
12. The save confirmation can reload that new EPUB as **Translation**. Click
    **Polish all** for an isolated temperature-0.1 surface pass that ignores all
    accumulated prompts/glossary and corrects only language-level defects.
    **Polish selected** sends only the chosen rows. If a provider limit stops a
    run, **Polish all** resumes rows without a successful polish result instead
    of restarting from the first chunk in the same editor session.

## Prompts and terminology memory

- **Core prompt** contains the universal proofreading and JSON rules.
- **Book instructions** contains optional instructions for the current book.
  A line such as `Loamwalker -> Землеходець` creates a locked `USER` mapping.
- **Scan book terms** examines the source EPUB locally. It uses no AI call and
  runs only when clicked, so you can apply a series profile first and then see
  newly discovered terms highlighted in amber. Once the local scan has found
  new amber terms, supported cloud providers automatically research only those
  new rows with their bilingual context. Local models are not used for this
  automatic terminology step.
- The minimum-occurrence threshold is not bypassed by arbitrary bracketed
  System text. Numeric statistics, percentage lines, reward/ranking messages,
  and long requirements are excluded. Rescanning also removes legacy rows of
  that shape unless you explicitly saved them as USER/CONFIRMED decisions.
- **Book terminology** shows source terms, canonical target-language forms,
  separate read-only AI suggestions, gender/grammar notes, proper-name locks,
  occurrence counts, and status. You can edit, confirm, reset, or delete
  entries. Hover an action button for its exact effect.
- **AI check new/selected** gives selected rows—or all new amber rows—an
  independent contextual check. The current canonical and status are not sent.
  **Deep-check selected** sends more context and enables Google Search grounding
  for Gemini; this may use separate search quota or billing. The **Double
  translation** filter shows disagreements without changing either value.
- AI terminology checks are intentionally freer than book proofreading. Normal
  checks use at least temperature 0.40 and deep checks 0.65 when supported, and
  the model is told to adapt coined compounds semantically rather than trust an
  awkward existing machine translation. This does not change ordinary review;
  final polish still forces temperature 0.1.
- AI-only rows use `AUTO`. **Unresolved — no proposal** shows only rows with no
  human canonical and no AI suggestion; use **AI suggestions** to inspect the
  model-populated rows separately.
- Human USER/CONFIRMED text always has priority. An AI suggestion is used only
  as an AUTO fallback when the human canonical is blank. **Use AI for selected**
  promotes it to a USER decision only after confirmation.
- The Search row filters all terminology columns immediately. Press Enter or
  use Previous/Next to move through matches; clearing the field restores the
  complete table.
- Alternatives and AI vote counts are intentionally absent. Apply, Scan,
  Close, and the title-bar X commit the active cell first, so the last edit is
  not lost while the cursor is still inside it.
- Check **Proper name** after confirming a character/place name to lock that
  spelling. Normal grammatical inflection remains allowed in prose, while a
  competing transliteration is not accepted.
- Rescanning updates counts and discovers new candidates while preserving USER
  mappings, grammar notes, and proper-name locks.
- **Delete selected** saves immediately and records the exclusion in the
  synchronized series profile, so old book memory cannot restore the row. You
  do not need **Save as** after deleting; explicitly adding the same source as a
  USER mapping later will intentionally restore it.
- **Add term…** can replace an existing canonical after showing the old and new
  values in a confirmation. Its success message says whether the edit was saved
  only to the book or synchronized to the applied series profile.
- Its editable **Gender / grammar note** column can store instructions such as
  `male; elderly man; use masculine forms`. The note is sent only for requests
  containing that character, and is preserved in series profiles.
- **Book memory and notes** previews the active glossary sent to the AI and
  provides a separate editable field for optional additional book notes.

The compact full prompt, book instructions, and active memory/notes are
shown in **AI settings**. Book-specific controls are enabled after two books
have been loaded
and AI settings is opened from that proofreader window. They are disabled in
the global Calibre plugin settings because no book project exists there.

The AI cannot add terminology decisions. Enter a canonical form manually to
create a locked USER mapping, or explicitly confirm a valid mapping. Canonical
`AUTO` mappings are active fallbacks; explicit USER and user-confirmed
CONFIRMED mappings have higher priority. Scanned `UNRESOLVED` candidates are
never sent. For a Cyrillic target, the canonical target field rejects
Latin/source-language mappings such as `лорде Atwood`; enter a semantic
translation or target-script transcription such as `лорд Атвуд`.

For every API request, matching AUTO/USER/user-confirmed terminology is sent
both in the compact instruction and beside the exact paragraph as
`required_glossary`.
The focused repair request receives the same data. After a review, AI notes
include **Plugin request glossary (sent with this pair)** with the mappings that
were actually transmitted. If no such line is shown, the term was
`UNRESOLVED`, had no canonical form, or its configured source form was not found
in that aligned original paragraph. Review an AUTO mapping and use **Confirm
selected** when you want to lock it as an explicit decision.

## Series memory profiles

After loading a book, use the **Series memory** row in the main editor:

- **Save as** creates a persistent profile. The EPUB series title is suggested
  when available; otherwise the book title is used.
- **Apply profile** imports its custom instructions, automatic notes, and full
  terminology database into the current book. It does not scan automatically;
  click **Scan book terms** afterward to discover and highlight additions from
  the new volume.
- **Sync user edits and new terms** lets automatic scanning add only absent
  source keys. Explicit canonical, gender, proper-name, reset, and deletion
  actions made by the user update that exact profile entry; stale book copies
  and automatic occurrence counts remain unable to rewrite it.
- **Clear book memory** removes the current book's custom prompt and glossary
  but does not touch saved profiles.
- **Delete** permanently removes the selected profile file while retaining the
  current book's already imported memory.

Profiles are individual JSON files under the Calibre configuration directory:
`plugins/translation-proofreader-memory-profiles`. They remain available until
you explicitly delete them. The plugin's **Series memory profiles** submenu can
open and edit any saved profile without loading a book. Press **Apply edited
mappings / notes** or **Close** to save. An already open proofreader window
keeps its in-memory copy until you click **Apply profile** again.

Private book memory is different: it is saved automatically under
`plugins/translation-proofreader-projects` using the original EPUB identity.
Reopening the same book restores its prompts and terminology even when the
Series memory selector still says **No shared profile**. Use **Save as** once to
create a reusable series profile. With **Sync user edits and new terms**
enabled, only terms absent from the shared profile are added automatically;
only a newer explicit human edit can update an existing key.

There is no separate terminology **Update** button. Apply/Scan/Close inside the
terminology dialog save the current book immediately; with **Sync user edits
and new terms** checked they also add absent keys and persist explicit user
changes to the shared profile.
The status line reports the save destination. The **Update** button inside AI
settings is unrelated and updates an AI provider/settings profile.

Loading another original EPUB creates or restores that book's separate private
memory, so the previous book's data is not silently reused. Applying a shared
profile remains explicit. If its name differs from the EPUB series metadata,
the plugin asks for confirmation before applying it.

## Portable prompts and glossary backup

Use the plugin arrow menu:

- **Export prompts and glossary** saves the global Core prompt and all series
  profiles into one `.tproofreader.json` file. Every profile includes Book
  instructions, Book memory and notes, canonical terminology, statuses,
  proper-name flags, and gender/grammar notes.
- **Import prompts and glossary** restores that file on another computer. If a
  profile name already exists, choose **No** for a protected add-missing-only
  merge or **Yes** to replace it explicitly with the imported copy.
- A private book project is portable only after **Save as** turns it into a
  series profile. Contrary to its short label, **Save as** includes both book
  prompt fields and the complete terminology database, not only the glossary.

The backup excludes API keys, endpoints, provider/model settings, and book
text. Configure the desired API separately on the destination computer.

## AI settings profiles

The **Settings profile** row in AI settings stores provider, API key, endpoint,
model, languages, temperature, chunk size, retry counts, and timing. Use
**Load**, **Save as**, **Update**, and **Delete** to compare Gemini, DeepSeek,
or local-model configurations. Saving the settings dialog also updates the
currently loaded profile. The universal core proofreading prompt is global and
is deliberately excluded from AI profiles.

The original, translation, and AI suggestion panes scroll together
proportionally. You can scroll any one of them to move the other two.

For literary editing, temperature `0.6–0.7` is a useful next step when `0.5`
feels too rigid. Values above `0.8` increase rewriting and terminology risk.

The plugin never overwrites the input file. The saved book contains only the
final translation, not the original passages or AI notes. Its embedded title
uses the configured/detected target-language prefix and ends in `[Proofreaded]`
or `[Polished]` according to the last applied operation. Metadata accumulates
both processing tags after polishing.

## Output EPUB and formatting

The translated EPUB—not the original—is used as the output template. The
plugin copies its metadata, navigation files, stylesheets, images, fonts,
chapter order, and HTML structure. It rewrites only accepted text in matched
translation blocks.

Inline elements such as `<em>`, `<strong>`, `<span>`, and links remain in the
document. Revised words are distributed across the existing inline text slots
according to their previous word proportions. This preserves the markup but
cannot always preserve the exact word that was bold or italic when the AI
substantially changes sentence length.

Preformatted, code, and math blocks are deliberately excluded from editing so
their whitespace and syntax remain intact. Elements explicitly hidden with
`hidden`, `aria-hidden`, or inline `display:none`/`visibility:hidden` are also
excluded from AI input and retained unchanged. The plugin does not fully
evaluate visibility rules stored in external stylesheets.

The plugin does not rebuild the table of contents or repair malformed EPUB
navigation. Hidden or broken content already present in the translated book is
copied unchanged unless it is part of a matched edited text block.

## Retries and safeguards

Network failures, HTTP 429, and temporary 5xx errors are retried with
exponential backoff. Content-filter blocks are not retried; the affected row is
marked and batch processing continues.

A local preflight detects likely duplicated chapter content and unusual
translation-to-original length ratios. Risky passages are skipped during
**Review all** and can only be sent individually after confirmation.

When one translated HTML element contains thousands of characters and many
`<br>` breaks while its source block is short, the plugin tests whether it is a
merged duplicate prefix. It compares the final break-separated fragments with
the consecutive source-block structure and surrounding length ratios; this is
local and uses no AI call. A high-confidence match keeps only the final current
translation and is labeled **duplicate prefix removed**. Use **Inspect removed
duplicate** beside AI notes to review what was discarded.

For a malformed block close to a chapter start, the same check can validate the
retained final fragment against at least five ordinary following source/
translation pairs. It additionally requires equal chapter block counts and
conservative length ratios. This avoids blindly removing an unexplained long
translation when the surrounding structure is not convincing.

If confidence is insufficient, the row remains red as **extra / merged
translation**, is skipped during Review All, and requires manual handling.
Automatic cleanup can be disabled under **Content repair** in AI settings. The
input EPUB is never edited, so disabling the option and reloading restores the
original in-memory block.

Successful duplicate-prefix repairs are shown in the main status line and do
not trigger an incomplete-alignment warning. After all local cleanup, chapter
length, and duplicate checks finish, the warning dialog lists only issues that
remain unresolved and still require attention.

After cleanup, chapter-risk metrics and request chunks are rebuilt from the
retained text. A short repaired paragraph is packed with adjacent safe
paragraphs whenever the original and translation totals both remain under the
configured character target; it does not automatically consume its own API
request.

Chunking targets the configured character count independently on each side. A
8,000-character setting therefore allows approximately 8,000 original plus
8,000 translation characters in one request. Prompt and JSON overhead are
additional, and the value is not a token limit.

## Current limitations

- EPUB input and output only; DRM-protected books are not supported.
- Structural, position-based paragraph alignment rather than full semantic
  alignment of merged, split, removed, or reordered paragraphs.
- No automatic insertion of the saved EPUB into the Calibre library.
- No checkpoint recovery for a closed batch session.
- No table-of-contents rebuilding or general EPUB repair.
